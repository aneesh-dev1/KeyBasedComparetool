import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from compare import compare, parser
import compare as engine
from checkpoints import lock


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for side in ('left','right'):
            with (self.root/(side+'.csv')).open('w',newline='',encoding='utf-8') as f:
                writer=csv.writer(f);writer.writerow(['id','part','v'])
                for i in range(13):writer.writerow([str(i),'é\npart',('old' if side=='left' else 'new')+str(i)])
        self.args=parser().parse_args([str(self.root/'left.csv'),str(self.root/'right.csv'),'--keys','id','part','--output',str(self.root/'report'),'--read-batch-size','2','--compare-batch-size','3','--fan-in','2','--checkpoint-dir',str(self.root/'saved')])
    def interrupt(self,phase):
        original=engine.progress
        def stop(args,message,**values):
            original(args,message,**values)
            if message==phase:raise RuntimeError('simulated interruption')
        with patch('compare.progress',side_effect=stop):
            with self.assertRaisesRegex(RuntimeError,'simulated'):compare(self.args)
    def finish(self):
        result=compare(self.args);self.assertEqual(result['matched_keys'],13);self.assertEqual(result['changed_cells'],13)
        with (self.root/'report/differences.csv').open() as stream:self.assertEqual(len(list(csv.DictReader(stream))),13)
    def test_resume_partial_sort_at_multiline_unicode_record_boundary(self):
        self.interrupt('left sort batch complete')
        saved=json.loads((self.root/'saved/left.json').read_text());self.assertEqual(saved['count'],2)
        self.finish()
    def test_resume_interrupted_merge(self):
        self.interrupt('left merge batch complete');self.finish()
    def test_resume_comparison_reuses_sort_and_replaces_partial_results(self):
        self.interrupt('Comparison batch complete')
        with patch('compare.write_run',side_effect=AssertionError('Should reuse completed sorting')):self.finish()
    def test_changed_source_same_size_same_mtime_rejected(self):
        self.interrupt('left sort batch complete')
        path=self.root/'right.csv';stat=path.stat();path.write_bytes(path.read_bytes().replace(b'new',b'NEW'))
        import os
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'Source files or comparison settings changed'):compare(self.args)
    def test_corrupted_batch_rejected(self):
        self.interrupt('left sort batch complete')
        state=json.loads((self.root/'saved/left.json').read_text());path=self.root/'saved'/state['runs'][0]['name'];data=path.read_bytes();path.write_bytes(data[:-1]+bytes([data[-1]^1]))
        with self.assertRaisesRegex(ValueError,'missing or damaged'):compare(self.args)
    def test_exclusive_worker_and_configuration_validation(self):
        with lock(self.root/'saved'):
            with self.assertRaisesRegex(ValueError,'still owns'):compare(self.args)
        self.interrupt('left sort batch complete');self.args.duplicate_policy='last'
        with self.assertRaisesRegex(ValueError,'settings changed'):compare(self.args)

    def test_parallel_sort_resume_and_orphan_sorter_lock(self):
        self.interrupt('left sort batch complete')
        self.args.sort_workers=2
        self.finish()
    def test_orphan_sorter_lock_prevents_checkpoint_reuse(self):
        self.interrupt('left sort batch complete')
        with lock(self.root/'saved/lock-left'):
            with self.assertRaisesRegex(ValueError,'still owns'):compare(self.args)

    def test_checkpoint_metadata_corruption_rejected(self):
        self.interrupt('left sort batch complete')
        target=self.root/'saved/left.json';state=json.loads(target.read_text());state['position']+=1;target.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'metadata is damaged'):compare(self.args)


class ResumeAPITests(unittest.TestCase):
    def test_restart_resume_owner_isolation_and_checkpoint_cleanup(self):
        import threading
        from server import make_server, Application
        from test_team_server import Client
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as folder:
            server=make_server(folder,0,public_url='http://compare.internal',max_sort_mb=64)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            try:
                client=Client(server);other=Client(server)
                data=b'id,v\n1,a\n2,b\n3,c\n'
                job=client.request('/api/jobs',dict(files={side:dict(name=side+'.csv',size=len(data)) for side in ('left','right')}));path='/api/jobs/'+job['id']
                for side in ('left','right'):client.request(path+'/files/'+side+'?offset=0',data,'PUT')
                client.request(path+'/finalize',{})
                directory=server.app.directory(job['id']);job=server.app.load(job['id']);job.update(state='running',keys=['id'],memory_mb=64,sort_workers=1,read_batch_size=2);server.app.save(job)
                args=parser().parse_args([str(directory/'left.csv'),str(directory/'right.csv'),'--keys','id','--output',str(directory/'report'),'--read-batch-size','2','--checkpoint-dir',str(directory/'checkpoints')])
                original=engine.progress
                def stop(args,message,**values):
                    original(args,message,**values)
                    if message=='left sort batch complete':raise RuntimeError('interruption')
                with patch('compare.progress',side_effect=stop):
                    with self.assertRaises(RuntimeError):compare(args)
                server.app.pool.shutdown();server.app=Application(folder)
                client.token=other.token=server.app.token
                recovered=client.request(path);self.assertEqual(recovered['state'],'error');self.assertTrue(recovered['can_resume'])
                with self.assertRaises(HTTPError):other.request(path+'/resume',{})
                client.request(path+'/resume',{})
                finished=client.wait(path,lambda j:j['state'] in ('complete','error'))
                self.assertEqual(finished['state'],'complete',finished);self.assertEqual(finished['summary']['equal_rows'],3)
                server.app.pool.shutdown()
                self.assertFalse((directory/'checkpoints').exists())
                with self.assertRaises(HTTPError):client.request(path+'/resume',{})
            finally:
                server.shutdown();thread.join();server.server_close();server.app.pool.shutdown()
