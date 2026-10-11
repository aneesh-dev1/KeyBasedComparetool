import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import pyarrow as pa
import pyarrow.parquet as pq
from compare import compare,read_header,pack_values,unpack_values
from parquet_input import Rows
from projects import Store
from test_team_features import FeatureTests

class ParquetTests(unittest.TestCase):
    def test_native_projection_nulls_rules_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            pq.write_table(pa.table({'id':['1','2','3'],'amount':['0','-11',None],'ignored':['x']*3}),root/'left.parquet',row_group_size=1)
            pq.write_table(pa.table({'id':['3','1','2'],'amount':['','0.00','-11.00'],'ignored':['y']*3}),root/'right.parquet',row_group_size=2)
            args=SimpleNamespace(left=root/'left.parquet',right=root/'right.parquet',keys=['id'],output=root/'out',memory_mb=1,temp_dir=root,delimiter=',',encoding='utf-8',fan_in=2,sort_workers=1,max_field_mb=64,allow_empty_keys=False,ignore_columns=['ignored'],ignore_keys='',value_overrides=[],comparison_rules=[{'column':'amount','tolerance':'0'}],read_batch_size=1,compare_batch_size=2,duplicate_policy='first',checkpoint_dir=root/'checkpoints')
            result=compare(args)
            self.assertEqual(result['changed_cells'],1);self.assertEqual(result['rule_equivalent_cells'],2)
            self.assertEqual(result['input_formats']['left'],'parquet')
            with Rows(root/'left.parquet',[0],position=1,batch_rows=1) as rows:self.assertEqual(list(rows),[['2'],['3']])
            self.assertEqual(compare(args)['changed_cells'],1)
            self.assertFalse((root/'left.csv').exists())
            self.assertEqual(unpack_values(pack_values([None,'','null'])),[None,'','null'])

    def test_interrupted_parquet_sort_resumes(self):
        from unittest.mock import patch
        import compare as engine
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for side in ('left','right'):pq.write_table(pa.table({'id':['1','2','3'],'v':['x','y','z']}),root/(side+'.parquet'),row_group_size=2)
            args=engine.parser().parse_args([str(root/'left.parquet'),str(root/'right.parquet'),'--keys','id','--output',str(root/'out'),'--memory-mb','1','--sort-workers','1','--read-batch-size','1'])
            args.checkpoint_dir=root/'checkpoints'
            original=engine.progress
            def interrupt(args,phase,**values):
                original(args,phase,**values)
                if phase=='left sort batch complete' and values['batch']==1:raise RuntimeError('Simulated interruption')
            with patch.object(engine,'progress',interrupt):
                with self.assertRaises(RuntimeError):engine.compare(args)
            self.assertEqual(engine.compare(args)['equal_rows'],3)

    def test_wide_projection_and_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'wide.parquet'
            pq.write_table(pa.table({'c'+str(i):[str(i)]*10 for i in range(2000)}),path)
            self.assertEqual(len(read_header(path,',','utf-8')[0]),2000)
            with Rows(path,[1,1999],batch_rows=2) as rows:
                self.assertEqual(len(list(rows)),10)
            with Rows(path,[1999]) as rows:self.assertEqual(next(iter(rows)),['1999'])

class ProjectAndParquetServerTests(FeatureTests):
    # Only this class's focused additions run here; inherited feature tests remain in their original suite.
    def test_parquet_api_and_project_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.parquet';pq.write_table(pa.table({'id':['1','2'],'value':['a','b']}),path);data=path.read_bytes()
        job=self.client.request('/api/jobs',{'files':{s:{'name':s+'.parquet','size':len(data)} for s in ('left','right')}});url='/api/jobs/'+job['id']
        for side in ('left','right'):self.client.request(url+'/files/'+side+'?offset=0',data,'PUT')
        ready=self.client.request(url+'/finalize',{});self.assertEqual(ready['columns'],['id','value'])
        preview=self.client.request(url+'/source-preview?side=left&rows=1');self.assertEqual(preview['rows'],[['1','a']])
        result=self.complete(url);self.assertEqual(result['summary']['changed_cells'],0)
        project=self.client.request('/api/projects',{'name':'Quarterly audit','description':'Test'})['id']
        self.client.request('/api/projects/records',dict(comparison_id=job['id'],project_id=project,status='Approved',note='Reviewed',reviewer='QA'))
        detail=self.client.request('/api/projects/'+project)
        self.assertEqual(detail['audit_counts']['Approved'],1);self.assertEqual(detail['latest']['id'],job['id'])
        self.assertEqual(self.other.request('/api/projects')['projects'],[])
        with self.assertRaises(HTTPError):self.other.request('/api/projects/records',dict(comparison_id=job['id'],project_id=project,status='Approved'))
        copied=self.client.request(url+'/review-copy',dict(keys=['id'],ignore_columns=[],comparison_rules=[],value_overrides=[]))
        self.assertEqual(self.complete('/api/jobs/'+copied['id'])['summary']['changed_cells'],0)
        detail=self.client.request('/api/projects/'+project)
        self.assertEqual(detail['total'],2);self.assertEqual(detail['audit_counts']['Unreviewed'],1)
        self.client.request(url,method='DELETE')
        retained=self.client.request('/api/projects/records?id='+job['id'])['records'][0]
        self.assertFalse(retained['source_available']);self.assertEqual(retained['audit_status'],'Approved')

    def test_json_audit_snapshot_attachment(self):
        result=self.client.request('/api/json-compare',dict(left='{"a":1}',right='{"a":2}',source_names=['first.json','second.json']))
        project=self.client.request('/api/projects',{'name':'JSON audit'})['id']
        self.client.request('/api/projects/records',dict(comparison_id=result['audit_id'],project_id=project,status='In review'))
        detail=self.client.request('/api/projects/'+project)
        self.assertEqual(detail['latest']['changed_cells'],1);self.assertEqual(detail['latest']['kind'],'json')
        self.assertEqual(detail['audit_counts']['In review'],1)

# Avoid duplicating the inherited feature tests in this module's discovery.
for name in dir(FeatureTests):
    if name.startswith('test_') and name not in ProjectAndParquetServerTests.__dict__:
        setattr(ProjectAndParquetServerTests,name,None)
del FeatureTests
