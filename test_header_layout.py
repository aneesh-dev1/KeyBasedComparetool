import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from compare import common_headers, align_headers, normalize_headers
import test_ui
from test_excel_input import workbook, data_rows
from json_compare import compare_json

class LayoutTests(unittest.TestCase):
    def test_casefold_reordering_and_alias(self):
        source={'left':['ID','AT01S'], 'right':['AT02S','id']}
        self.assertEqual(common_headers(align_headers(source)[0]),['ID'])
        aligned,audit=align_headers(source,{'left':['ID','AT01S'], 'right':['at01s','id']})
        self.assertEqual(aligned,{'left':['ID','AT01S'], 'right':['AT01S','ID']})
        self.assertEqual(len(audit),2)
    def test_ambiguous_aliases_rejected(self):
        source={'left':['a','b'],'right':['a','b']}
        for bad in [['a','A'],['a',''],['a'],['a',None]]:
            with self.assertRaises(ValueError):align_headers(source,dict(left=bad,right=source['right']))
        self.assertEqual(normalize_headers(['A','a','A_1','']),['A','a_2','A_1','column4'])

class LayoutAPITests(unittest.TestCase):
    setUpClass=classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass=classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request=test_ui.ServerTests.request
    wait=test_ui.ServerTests.wait
    def upload(self,left,right,ext='csv'):
        job=self.request('/api/jobs',{'files':{side:{'name':side+'.'+ext,'size':len(content)} for side,content in [('left',left),('right',right)]}})
        path='/api/jobs/'+job['id']
        for side,content in [('left',left),('right',right)]:self.request(path+f'/files/{side}?offset=0',content,'PUT')
        self.request(path+'/finalize',{})
        return path
    def test_rename_both_directions_case_scope_preview_worker_and_history(self):
        for layout in [dict(left=['ID','AT01S','skip'],right=['skip','at01s','id']),dict(left=['ID','AT02S','skip'],right=['skip','AT02S','id'])]:
            with self.subTest(layout=layout):
                left=b'ID,AT01S,skip\n001,None,x\n002,OLD,x\n003,old,x\n'
                right=b'skip,AT02S,id\ny,none,001\ny,old,002\ny,new,003\n'
                path=self.upload(left,right)
                self.assertFalse(self.request(path)['headers_reviewed'])
                self.assertEqual(self.request(path)['columns'],['ID','skip'])
                self.request(path+'/config',{'keys':['ID'],'ignore_columns':['skip'],'value_overrides':[dict(column='AT01S',left='None',right='none')]})
                ready=self.request(path+'/headers',{'column_headers':layout})
                col=layout['left'][1]
                self.assertEqual(ready['draft']['value_overrides'][0]['column'],col)
                self.assertEqual(self.request(path+'/source-preview?side=right')['headers'],layout['right'])
                self.assertEqual((self.server.app.directory(ready['id'])/'left.csv').read_bytes(),left)
                self.request(path+'/start',dict(keys=['ID'],memory_mb=64,sort_workers=2,ignore_columns=['skip'],ignore_keys='003',value_overrides=ready['draft']['value_overrides']))
                result=self.wait(path,lambda j:j['state'] in ('complete','error'))
                self.assertEqual(result['state'],'complete',result)
                self.assertEqual(result['summary']['changed_cells'],1) # Values remain case-sensitive.
                self.assertEqual(result['summary']['override_equivalent_cells'],1)
                self.assertEqual(result['summary']['left_excluded_rows'],1)
                self.assertEqual(self.request(path+'/preview')['rows'][0][1],col)
                self.assertTrue(result['summary']['header_layout_changes'])
                with self.assertRaises(HTTPError):self.request(path+'/headers',{'column_headers':layout})
    def test_excel_differing_headers_can_be_aligned(self):
        path=self.upload(workbook(data_rows('old').replace('<t>value</t>','<t>AT01S</t>')),workbook(data_rows('new').replace('<t>value</t>','<t>AT02S</t>')),'xlsx')
        self.request(path+'/select-sheets',dict(left='Data',right='Data'))
        ready=self.wait(path,lambda j:j['state'] in ('ready','error'))
        self.assertEqual(ready['state'],'ready',ready)
        self.request(path+'/headers',{'column_headers':dict(left=['id','AT01S'],right=['ID','at01s'])})
        self.request(path+'/start',dict(keys=['id'],memory_mb=64,sort_workers=1))
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        self.assertEqual(result['summary']['changed_cells_by_column'],{'AT01S':1})
    def test_wide_layout_reads_headers_without_parsing_rows(self):
        names=['id']+[f'col{i}' for i in range(1,2000)]
        left=(','.join(names)+'\ninvalid row width\n').encode()
        right_names=list(reversed([n.upper() for n in names]))
        right=(','.join(right_names)+'\nalso invalid\n').encode()
        path=self.upload(left,right)
        ready=self.request(path+'/headers',{'column_headers':dict(left=names,right=right_names)})
        self.assertEqual(len(ready['columns']),2000)
        self.assertTrue(ready['headers_reviewed'])

    def test_case_only_requires_no_rename_and_rejects_collisions(self):
        path=self.upload(b'ID,AT01S\n001,a\n',b'at01s,id\na,001\n')
        with self.assertRaises(HTTPError):self.request(path+'/headers',{'column_headers':{'left':['ID','id'],'right':['at01s','id']}})
        self.request(path+'/headers',{'column_headers':self.request(path)['column_headers']})
        self.request(path+'/start',dict(keys=['ID'],memory_mb=64,sort_workers=1))
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        self.assertEqual(result['summary']['equal_rows'],1)

class JsonViewTests(unittest.TestCase):
    def test_keyed_alignment_original_paths_and_line_numbers(self):
        a={'users':[{'id':'a','v':1},{'id':'b','v':2}]}
        b={'users':[{'id':'b','v':3},{'id':'a','v':1}]}
        args=(json.dumps(a),json.dumps(b),'ordered',[dict(path='$.users',mode='keyed',field='id')])
        result=compare_json(*args,include_view=True)
        self.assertEqual(result['differences'],compare_json(*args)['differences'])
        changed=[r for r in result['view'] if r['kind']=='changed']
        self.assertEqual(len(changed),1)
        self.assertEqual(changed[0]['left']['path'],'$.users[1].v')
        self.assertEqual(changed[0]['right']['path'],'$.users[0].v')
        self.assertNotEqual(changed[0]['left']['line'],changed[0]['right']['line'])
        self.assertEqual(changed[0]['difference'],0)
    def test_unordered_duplicates_null_type_and_exact_large_numbers(self):
        for left,right,mode in [('[[1,2],1,1]','[1,[2,1]]','unordered'),('{"a":null,"b":{}}','{"c":true,"b":[]}','ordered'),('9007199254740992','9007199254740993','ordered'),('1.0','1','ordered')]:
            result=compare_json(left,right,mode,include_view=True)
            self.assertEqual(result['differences'],compare_json(left,right,mode)['differences'])
            self.assertTrue(result['view'])
            indexes={r['difference'] for r in result['view'] if r['difference'] is not None}
            self.assertEqual(indexes,set(range(len(result['differences']))))
        result=compare_json('null','{}',include_view=True)
        self.assertEqual(result['view'][0]['left']['text'],'null')
    def test_large_view_contains_late_changes(self):
        left=list(range(2200));right=left.copy();right[-1]=-1
        result=compare_json(json.dumps(left),json.dumps(right),include_view=True)
        self.assertEqual(len(result['view']),2202)
        self.assertEqual(result['view'][-2]['difference'],0)

if __name__=='__main__':unittest.main()
