import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from compare import compare, parser
from live_preview import LivePreview, MAX_SNAPSHOT_BYTES, read_preview
from server import make_server
from test_team_server import Client


class LivePreviewTests(unittest.TestCase):
    def test_bounded_control_unicode_null_and_long_values(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'live-preview.json'; live=LivePreview(target)
            for index in range(1000):
                live.add(str(index)+'\x01'*1000,'\x02'*1000,None,'雪\x03'*1000)
            live.publish({'changed_cells':1000000},{'\x04'*1000:1000000},20,force=True)
            self.assertLessEqual(target.stat().st_size,MAX_SNAPSHOT_BYTES)
            data=read_preview(directory,'running')
            self.assertTrue(data['provisional']);self.assertTrue(data['sampled'])
            self.assertLessEqual(len(data['samples']),60)
            self.assertEqual(data['counts']['changed_cells'],1000000)
            self.assertIsNone(data['samples'][-1]['left'])
            self.assertTrue(data['samples'][-1]['right'].endswith('…'))

    def test_partial_snapshots_before_summary_and_exact_final_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for side,value in [('left','a'),('right','b')]:
                (root/f'{side}.csv').write_text('id,v,accepted\n'+''.join(f'{i},{value},{value}\n' for i in range(200)))
            args=parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'out'),'--compare-batch-size','64'])
            args.live_preview_file=root/'live-preview.json'
            args.value_overrides=[dict(column='accepted',left='a',right='b')]
            snapshots=[]
            def observe(*unused,**values):
                if args.live_preview_file.exists():
                    data=read_preview(root,'running')
                    if data.get('processed_keys'):snapshots.append((data,(root/'out/summary.json').exists()))
            with patch('compare.progress',side_effect=observe),patch('live_preview.time.monotonic',side_effect=range(0,1000,2)),contextlib.redirect_stdout(io.StringIO()):
                result=compare(args)
            partial=[(data,done) for data,done in snapshots if 0<data['processed_keys']<200]
            self.assertTrue(partial)
            self.assertTrue(all(not done for data,done in partial))
            final=read_preview(root,'complete')
            self.assertEqual(final['counts']['changed_cells'],result['changed_cells'])
            self.assertEqual(result['changed_cells'],200)
            self.assertFalse(final['provisional'])
            self.assertTrue(all(row['column']=='v' for row in final['samples']))

    def test_preview_file_error_does_not_fail_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            live=LivePreview(Path(directory)/'live-preview.json')
            with patch('live_preview.atomic_write_text',side_effect=PermissionError('locked')),contextlib.redirect_stdout(io.StringIO()):
                live.publish({}, {},0,force=True)
            self.assertIsNone(live.path)
            live.publish({}, {},0,force=True)
            self.assertEqual(read_preview(directory,'error')['job_state'],'error')


class LivePreviewServerTests(unittest.TestCase):
    def test_live_preview_is_owner_scoped_and_available_during_run(self):
        with tempfile.TemporaryDirectory() as directory:
            server=make_server(directory,0,max_sort_mb=64,public_url="http://compare.internal")
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                first,second=Client(server),Client(server)
                job=first.request('/api/jobs',{'files':{side:dict(name=side+'.csv',size=9) for side in ('left','right')}})
                server.app.patch(job['id'],state='running')
                live=LivePreview(server.app.directory(job['id'])/'live-preview.json')
                live.add('["001"]','v','a','b');live.publish({'changed_cells':1},{'v':1},1,force=True)
                path='/api/jobs/'+job['id']+'/live-preview'
                result=first.request(path)
                self.assertEqual(result['counts']['changed_cells'],1)
                self.assertEqual(result['job_state'],'running')
                self.assertFalse((server.app.directory(job['id'])/'report/summary.json').exists())
                with self.assertRaises(HTTPError) as caught:second.request(path)
                self.assertEqual(caught.exception.code,404)
                self.assertIn(b'renderLiveMismatches',first.request('/live-preview.js'))
            finally:
                server.shutdown();server.server_close();server.app.pool.shutdown()
