import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from server import make_server
from task_control import check_cancel,Cancelled
from comparison_rules import validate_rules,equivalent
from test_team_server import Client

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=make_server(self.temp.name,0,public_url='http://compare.internal',max_sort_mb=64)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.client=Client(self.server);self.other=Client(self.server)
    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.server.app.pool.shutdown();self.temp.cleanup()
    def upload(self,left=b'id,v\n001,old\n',right=b'id,v\n001,new\n'):
        job=self.client.request('/api/jobs',dict(files={s:dict(name=s+'.csv',size=len(data)) for s,data in [('left',left),('right',right)]}));path='/api/jobs/'+job['id']
        for side,data in [('left',left),('right',right)]:self.client.request(path+'/files/'+side+'?offset=0',data,'PUT')
        self.client.request(path+'/finalize',{})
        return path
    def complete(self,path,**config):
        self.client.request(path+'/start',dict(keys=['id'],memory_mb=64,sort_workers=1,**config))
        result=self.client.wait(path,lambda j:j['state'] in ('complete','error','cancelled'))
        self.assertEqual(result['state'],'complete',result);return result
    def test_profile_storage_and_isolation(self):
        path=self.upload();job=self.client.request(path)
        self.client.request(path+'/headers',dict(column_headers=job['column_headers']))
        self.client.request(path+'/config',dict(keys=['id'],memory_mb=64,ignore_columns=[],ignore_keys='',value_overrides=[],read_batch_size=7,compare_batch_size=3,duplicate_policy='last',comparison_rules=[dict(column='v',trim=True)]))
        profile=self.client.request('/api/profiles',dict(job_id=job['id'],name='Team profile'))['profiles'][0]
        self.assertEqual(self.other.request('/api/profiles')['profiles'],[])
        target=self.upload(b'v,id\nold,001\n',b'id,v\n001,new\n')
        applied=self.client.request(target+'/apply-profile',dict(id=profile['id']))
        self.assertEqual(applied['draft']['comparison_rules'][0]['trim'],True)
        self.assertEqual(applied['columns'],['v','id'])
        self.assertEqual(applied['draft']['read_batch_size'],7)
        self.assertEqual(applied['draft']['compare_batch_size'],3)
        self.assertEqual(applied['draft']['duplicate_policy'],'last')
        with self.assertRaises(HTTPError):self.other.request(target+'/apply-profile',dict(id=profile['id']))
        storage=self.client.request('/api/storage');self.assertEqual(len(storage['jobs']),2)
        self.assertGreater(storage['jobs'][0]['sizes']['uploads'],0)
        self.assertEqual(self.other.request('/api/storage')['jobs'],[])
        self.assertTrue(self.client.request('/api/preflight',dict(bytes=100))['can_upload'])
        self.client.request('/api/profiles',dict(id=profile['id']),method='DELETE')
        self.assertEqual(self.client.request('/api/profiles')['profiles'],[])
    def test_notes_export_and_activity(self):
        path=self.upload();job=self.complete(path)
        self.client.request(path+'/annotations',dict(column='v',key=None,status='Expected',comment='Known <difference>'))
        for kind in ('excel','html'):
            self.client.request(path+'/export/'+kind,{})
            ready=self.client.wait(path,lambda j:j['exports'][kind]['state'] in ('complete','error'))
            self.assertEqual(ready['exports'][kind]['state'],'complete',ready)
        with zipfile.ZipFile(io.BytesIO(self.client.request(path+'/download/mismatches.xlsx'))) as book:
            self.assertIn('Analysis notes',book.read('xl/workbook.xml').decode())
            self.assertIn('Expected: Known &lt;difference&gt;',book.read('xl/worksheets/sheet2.xml').decode())
        with zipfile.ZipFile(io.BytesIO(self.client.request(path+'/download/html.zip'))) as book:
            self.assertIn('Known &lt;difference&gt;',book.read('index.html').decode())
        self.client.request(path+'/annotations',dict(column='v',key=None,status='Resolved',comment='Reviewed'))
        self.assertEqual(self.client.request(path)['exports']['excel']['state'],'outdated')
        self.assertTrue(self.client.request('/api/activity')['activities'])
        self.assertEqual(self.other.request('/api/activity')['activities'],[])
        with self.assertRaises(HTTPError):self.other.request(path+'/annotations')
    def test_comparison_rules_and_duplicate_diagnostics(self):
        path=self.upload(b'id,amount,date,text\n001,1.00,2026-09-28, Hello \n',b'id,amount,date,text\n001,1.04,28/09/2026,hello\n')
        rules=[dict(column='amount',tolerance='0.05'),dict(column='date',left_date_format='%Y-%m-%d',right_date_format='%d/%m/%Y'),dict(column='text',trim=True,ignore_case=True)]
        job=self.complete(path,comparison_rules=rules)
        self.assertEqual(job['summary']['changed_cells'],0);self.assertEqual(job['summary']['rule_equivalent_cells'],3)
        path=self.upload(b'id,v\n001,a\n001,b\n001,c\n',b'id,v\n001,a\n')
        self.client.request(path+'/start',dict(keys=['id'],memory_mb=64,sort_workers=1))
        job=self.client.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(job['state'],'complete')
        self.assertEqual(job['summary']['left_duplicate_rows_skipped'],2)
        diag=self.client.request(path+'/diagnostics');self.assertEqual(diag['count'],3);self.assertEqual(diag['key'],['001']);self.assertEqual(len(diag['samples']),3)
    def test_duplicate_exports_and_batch_settings(self):
        path=self.upload(b'id,v\n001,a\n001,b\n',b'id,v\n001,b\n')
        for config in (dict(read_batch_size=0),dict(compare_batch_size=True),dict(duplicate_policy='all')):
            with self.assertRaises(HTTPError):
                self.client.request(path+'/start',dict(keys=['id'],memory_mb=64,**config))
        job=self.complete(path,read_batch_size=1,compare_batch_size=1,duplicate_policy='last')
        self.assertEqual(job['summary']['changed_cells'],0)
        self.assertEqual(job['summary']['read_batch_size'],1)
        audit=self.client.request(path+'/download/duplicate_keys.csv')
        self.assertIn(b'kept_occurrence',audit)
        for kind in ('excel','html'):
            self.client.request(path+'/export/'+kind,{})
            ready=self.client.wait(path,lambda j:j['exports'][kind]['state'] in ('complete','error'))
            self.assertEqual(ready['exports'][kind]['state'],'complete',ready)
        with zipfile.ZipFile(io.BytesIO(self.client.request(path+'/download/mismatches.xlsx'))) as book:
            self.assertIn('Duplicate keys',book.read('xl/workbook.xml').decode())
            self.assertIn('WARNING: duplicate keys',book.read('xl/worksheets/sheet1.xml').decode())
        with zipfile.ZipFile(io.BytesIO(self.client.request(path+'/download/html.zip'))) as book:
            self.assertEqual(book.read('duplicate_keys.csv'),audit)
            self.assertIn('Warning: duplicate keys',book.read('index.html').decode())
    def test_cancel_queued_and_running_tasks(self):
        path=self.upload();job=self.client.request(path);started=threading.Event()
        def wait_for_cancel(identity):
            started.set()
            flag=self.server.app.directory(identity)/'cancel-comparison'
            until=time.monotonic()+10
            while time.monotonic()<until:
                check_cancel(flag);time.sleep(.01)
            raise AssertionError('Cancellation checkpoint not reached')
        self.server.app.run_comparison=wait_for_cancel
        self.client.request(path+'/start',dict(keys=['id'],memory_mb=64))
        self.assertTrue(started.wait(2))
        queued=self.upload();self.client.request(queued+'/start',dict(keys=['id'],memory_mb=64))
        self.client.request(queued+'/cancel',dict(kind='comparison'))
        self.assertEqual(self.client.request(queued)['state'],'cancelled')
        with self.assertRaises(HTTPError):self.other.request(path+'/cancel',dict(kind='comparison'))
        self.client.request(path+'/cancel',dict(kind='comparison'))
        self.client.wait(path,lambda j:j['state']=='cancelled')
    def test_cancel_auxiliary_tasks_and_release_queue(self):
        path=self.upload();job=self.complete(path);identity=job['id']
        for kind in ('excel','html','analysis','import'):
            started=threading.Event()
            def task(current):
                started.set()
                for _ in range(500):
                    check_cancel(self.server.app.directory(current)/('cancel-'+kind))
                    time.sleep(.005)
            self.server.app.submit(identity,kind,task)
            self.assertTrue(started.wait(2))
            self.client.request(path+'/cancel',dict(kind=kind))
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and (identity,kind) in self.server.app.tasks:time.sleep(.01)
            self.assertNotIn((identity,kind),self.server.app.tasks)
            current=self.client.request(path)
            state=current['exports'][kind]['state'] if kind in ('excel','html') else current['analysis']['state'] if kind=='analysis' else current['state']
            self.assertEqual(state,'cancelled')
        progressed=threading.Event()
        self.server.app.submit(identity,'probe',lambda _:progressed.set())
        self.assertTrue(progressed.wait(2))

    def test_comparison_cancel_checkpoint_and_disk_guard(self):
        from compare import compare,parser
        from unittest.mock import patch
        import shutil
        path=self.upload();job=self.client.request(path);root=self.server.app.directory(job['id'])
        flag=root/'cancel-check';flag.touch()
        args=parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'cancelled-report')])
        args.cancel_file=flag
        with self.assertRaises(Cancelled):compare(args)
        with patch('server.shutil.disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
            self.assertFalse(self.client.request('/api/preflight',dict(bytes=100))['can_upload'])
            with self.assertRaises(HTTPError):self.client.request('/api/jobs',dict(files={side:dict(name=side+'.csv',size=10) for side in ('left','right')}))

    def test_rule_validation_and_exact_default(self):
        self.assertFalse(equivalent('None','none',{}))
        self.assertFalse(equivalent('bad','other',dict(tolerance='1')))
        self.assertFalse(equivalent('NaN','1',dict(tolerance='1')))
        self.assertTrue(equivalent('0.3','0.2',dict(tolerance='0.1')))
        self.assertFalse(equivalent('0.3000000001','0.2',dict(tolerance='0.1')))
        with self.assertRaises(ValueError):validate_rules([dict(column='id',trim=True)],['id','v'],['id'],[])
        with self.assertRaises(ValueError):validate_rules([dict(column='v',tolerance='-1')],['id','v'],['id'],[])
