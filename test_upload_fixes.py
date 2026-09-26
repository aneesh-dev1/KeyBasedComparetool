import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from compare import normalize_headers, header, compare, parser, progress
from file_io import atomic_write_text
from server import Application
import test_workflow

class HeaderAndFileTests(unittest.TestCase):
    def test_collision_safe_headers(self):
        self.assertEqual(normalize_headers(['id','','name','name']),['id','column2','name','name_1'])
        self.assertEqual(normalize_headers(['','column1','a','a','a_1','  ']),['column1_1','column1','a','a_2','a_1','column6'])
        names=normalize_headers(['','a','a','a_1'])
        self.assertEqual(normalize_headers(names),names)
    def test_transient_permission_error_retries_unique_temp(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'job.json';target.write_text('old')
            import os
            replace=os.replace;attempts=[]
            def flaky(source,destination):
                attempts.append(Path(source).name)
                if len(attempts)<3:raise PermissionError('sharing violation')
                replace(source,destination)
            with patch('file_io.os.replace',side_effect=flaky),patch('file_io.time.sleep'):
                atomic_write_text(target,'new')
            self.assertEqual(target.read_text(),'new');self.assertEqual(len(attempts),3)
            self.assertNotEqual(attempts[0],'job.tmp');self.assertFalse(list(Path(d).glob('*.tmp')))
    def test_persistent_denial_preserves_job_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'job.json';target.write_text('old')
            with patch('file_io.os.replace',side_effect=PermissionError('locked')),patch('file_io.time.sleep'),self.assertRaisesRegex(PermissionError,'Existing data is preserved'):
                atomic_write_text(target,'new')
            self.assertEqual(target.read_text(),'old');self.assertFalse(list(Path(d).glob('*.tmp')))
    def test_progress_lock_does_not_abort_comparison(self):
        from types import SimpleNamespace
        args=SimpleNamespace(progress_file='unused')
        with patch('compare.atomic_write_text',side_effect=PermissionError('locked')),contextlib.redirect_stdout(io.StringIO()) as log:
            progress(args,'Comparing keys',rows=10000)
        self.assertIn('comparison continues',log.getvalue());self.assertIsNone(args.progress_file)

class UploadFixAPITests(unittest.TestCase):
    setUpClass=classmethod(test_workflow.WorkflowAPITests.setUpClass.__func__)
    tearDownClass=classmethod(test_workflow.WorkflowAPITests.tearDownClass.__func__)
    request=test_workflow.WorkflowAPITests.request
    wait=test_workflow.WorkflowAPITests.wait
    def test_upload_checkpoint_and_normalized_preview_comparison(self):
        left=b'id,,name,name\n001,x,old,a\n002,y,same,b\n'
        right=b'id,,name,name\n001,x,new,a\n002,z,same,b\n'
        job=self.request('/api/jobs',{'files':{'left':{'name':'a.csv','size':len(left)},'right':{'name':'b.csv','size':len(right)}}});path='/api/jobs/'+job['id']
        # Job metadata may be locked throughout upload: chunk checkpoints use file lengths.
        with patch.object(self.server.app,'save',side_effect=PermissionError('metadata locked')):
            self.request(path+'/files/left?offset=0',left[:10],'PUT')
            self.assertEqual(self.request(path)['files']['left']['uploaded'],10)
            reopened=Application(self.temp.name)
            try:self.assertEqual(reopened.load(job['id'])['files']['left']['uploaded'],10)
            finally:reopened.pool.shutdown()
            self.request(path+'/files/left?offset=10',left[10:],'PUT')
            self.request(path+'/files/right?offset=0',right,'PUT')
        ready=self.request(path+'/finalize',{})
        self.assertEqual(ready['columns'],['id','column2','name','name_1'])
        self.assertEqual(len(ready['header_changes']),4)
        self.assertEqual(self.request(path+'/source-preview?side=left&rows=1')['headers'],ready['columns'])
        self.request(path+'/start',{'keys':['id'],'ignore_columns':['name'],'sort_workers':2,'memory_mb':64})
        done=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(done['state'],'complete',done)
        self.assertEqual(done['summary']['changed_cells'],1)
        self.assertEqual(done['summary']['changed_cells_by_column']['column2'],1)
        self.assertEqual(len(done['summary']['header_changes']),4)
