import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import test_ui
from compare import compare, parser, validate_overrides
from server import Application


class OverrideTests(unittest.TestCase):
    def test_directional_pairs_and_original_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for side,rows in [('left',[['01','None','x'],['02','none','x'],['03','None','old'],['04','','x']]),('right',[['01','none','x'],['02','None','x'],['03','none','new'],['04','NULL','x']])]:
                with (root/f'{side}.csv').open('w',newline='') as f:
                    writer=csv.writer(f);writer.writerow(['id','status','other']);writer.writerows(rows)
            args=parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'out')])
            args.value_overrides=[{'column':'status','left':'None','right':'none'},{'column':'status','left':'','right':'NULL'}]
            with contextlib.redirect_stdout(io.StringIO()):result=compare(args)
            self.assertEqual(result['equal_rows'],2)
            self.assertEqual(result['changed_rows'],2)
            self.assertEqual(result['changed_cells'],2)
            self.assertEqual(result['override_equivalent_cells'],3)
            with (root/'out/differences.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual([(r['column'],r['left_value'],r['right_value']) for r in rows],[('status','none','None'),('other','old','new')])
    def test_validation_and_multiple_columns(self):
        rules=[{'column':'a','left':'None','right':'none'},{'column':'b','left':'N/A','right':''}]
        cleaned,lookup=validate_overrides(rules+rules,['id','a','b'],['id'],[])
        self.assertEqual(len(cleaned),2);self.assertIn(('N/A',''),lookup['b'])
        for rule in [{'column':'id','left':'a','right':'b'},{'column':'unknown','left':'a','right':'b'},{'column':'a','left':None,'right':'none'}]:
            with self.assertRaises(ValueError):validate_overrides([rule],['id','a'],['id'],[])
        with self.assertRaises(ValueError):validate_overrides([rules[0]],['id','a'],['id'],['a'])


class WorkflowAPITests(unittest.TestCase):
    setUpClass=classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass=classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request=test_ui.ServerTests.request
    wait=test_ui.ServerTests.wait

    def upload(self,left,right):
        job=self.request('/api/jobs',{'files':{'left':{'name':'workflow-left.csv','size':len(left)},'right':{'name':'workflow-right.csv','size':len(right)}}})
        path='/api/jobs/'+job['id']
        for side,content in [('left',left),('right',right)]:self.request(path+f'/files/{side}?offset=0',content,'PUT')
        self.request(path+'/finalize',{})
        return path
    def test_preview_limits_and_column_paging(self):
        data=('id,'+','.join(f'c{i}' for i in range(30))+'\n'+','.join(['001']+['None']*30)+'\n'+','.join(['002']+['none']*30)+'\nBAD,ROW\n').encode()
        path=self.upload(data,data)
        preview=self.request(path+'/source-preview?side=left&rows=2&column_offset=20')
        self.assertEqual(len(preview['rows']),2)
        self.assertEqual(preview['headers'][0],'c19');self.assertEqual(preview['total_columns'],31)
        from urllib.error import HTTPError
        for suffix in ['rows=0','rows=1001','rows=abc','rows=2&side=outside','rows=2&column_offset=500']:
            with self.assertRaises(HTTPError):self.request(path+'/source-preview?'+suffix)
    def test_history_draft_override_and_live_logs(self):
        path=self.upload(b'id,status\n01,None\n02,other\n',b'id,status\n01,none\n02,other\n')
        config={'keys':['id'],'memory_mb':64,'ignore_columns':[],'ignore_keys':'','value_overrides':[{'column':'status','left':'None','right':'none'}]}
        self.request(path+'/config',config)
        self.assertEqual(self.request(path)['draft'],config)
        history=self.request('/api/jobs?offset=0')
        self.assertIn(path.rsplit('/',1)[1],[item['id'] for item in history['jobs']])
        self.request(path+'/start',config)
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        self.assertEqual(result['summary']['changed_cells'],0)
        self.assertEqual(result['summary']['override_equivalent_cells'],1)
        log=self.request(path+'/logs?cursor=0')
        for phase in ['queued','Validating','Reading left','Reading right','Comparing keys','Writing reports','complete']:self.assertIn(phase,log['text'])
        self.assertEqual(self.request(path+f'/logs?cursor={log["cursor"]}')['text'],'')
        self.assertIn(b'Comparison complete',self.request(path+'/download/run.log'))
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):self.request(path+'/config',config)
        # Load the same on-disk store in a new application instance.
        reopened=Application(self.temp.name)
        try:self.assertEqual(reopened.load(result['id'])['summary']['equal_rows'],2)
        finally:reopened.pool.shutdown()

if __name__=='__main__':unittest.main()
