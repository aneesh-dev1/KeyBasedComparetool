import io
import json
import zipfile
from urllib.error import HTTPError
from test_workflow import WorkflowAPITests

class ContainerTests(WorkflowAPITests):
    def test_container_snapshot_union_and_exports(self):
        a=self.request('/api/key-containers',dict(name='NO_HIT_KEYS',reason='No hit <source>',values='001, 002',key_width=1))['containers'][-1]
        b=self.request('/api/key-containers',dict(name='DUPLICATE_KEYS',reason='Duplicate',values='002, 999',key_width=1))['containers'][-1]
        self.assertIn(a,self.request('/api/key-containers')['containers'])
        with self.assertRaises(HTTPError):self.request('/api/key-containers',dict(name='no_hit_keys',reason='x',values='1'))
        path=self.upload(b'id,Ignored key containers\n001,a\n002,b\n003,c\n',b'id,Ignored key containers\n001,x\n002,y\n003,z\n')
        self.request(path+'/start',dict(keys=['id'],ignore_container_ids=[a['id'],b['id']]))
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        s=result['summary'];self.assertEqual(s['ignored_keys_count'],3);self.assertEqual(s['left_excluded_rows'],2);self.assertEqual(s['changed_cells'],1)
        self.assertEqual(s['ignore_key_containers'][0]['reason'],'No hit <source>')
        for kind in ('excel','html'):
            self.request(path+'/export/'+kind,{})
            self.wait(path,lambda j:j['exports'][kind]['state'] in ('complete','error'))
        with zipfile.ZipFile(io.BytesIO(self.request(path+'/download/mismatches.xlsx'))) as z:
            wb=z.read('xl/workbook.xml').decode();self.assertIn('name="Ignored key containers"',wb);self.assertIn('name="Ignored key containers (2)"',wb)
            audit=z.read('xl/worksheets/sheet3.xml').decode();self.assertIn('NO_HIT_KEYS',audit);self.assertIn('001',audit);self.assertIn('No hit &lt;source&gt;',audit)
        self.assertIn('No hit &lt;source&gt;',self.request(path+'/download/comparison-report.html').decode())
    def test_composite_container_width(self):
        item=self.request('/api/key-containers',dict(name='COMPOSITE',reason='Composite exclusion',values='[["001","A"]]',key_width=2))['containers'][-1]
        path=self.upload(b'id,part,v\n001,A,x\n001,B,x\n',b'id,part,v\n001,A,y\n001,B,y\n')
        with self.assertRaises(HTTPError):self.request(path+'/start',dict(keys=['id'],ignore_container_ids=[item['id']]))
        self.request(path+'/start',dict(keys=['id','part'],ignore_container_ids=[item['id']]))
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['summary']['changed_cells'],1)
