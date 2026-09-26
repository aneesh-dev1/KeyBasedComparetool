import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from compare import compare, parser, header, validate_scope
from reports import export_excel, export_html
from test_ui import ServerTests


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def run_case(self, names, left, right, extra=(), keys=('id',)):
        for side,rows in [('left',left),('right',right)]:
            with (self.root/f'{side}.csv').open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(names);writer.writerows(rows)
        args=parser().parse_args([str(self.root/'left.csv'),str(self.root/'right.csv'),'--keys',*keys,'--output',str(self.root/'result'),*extra])
        return compare(args)
    def test_ignored_columns_and_rows(self):
        result=self.run_case(['id','value','timestamp'],[['001','a','old'],['002','b','old'],['003','c','old']], [['001','a','new'],['002','changed','new'],['004','d','new']], ['--ignore-columns','timestamp','--ignore-keys','002, 003'])
        self.assertEqual(result['equal_rows'],1);self.assertEqual(result['changed_cells'],0)
        self.assertEqual(result['left_only'],0);self.assertEqual(result['right_only'],1)
        self.assertEqual(result['left_excluded_rows'],2);self.assertEqual(result['right_excluded_rows'],1)
        self.assertEqual(result['left_rows'],3);self.assertEqual(result['left_compared_rows'],1)
        self.assertEqual(result['changed_cells_by_column'],{'value':0})
    def test_ignored_duplicates_excluded_before_duplicate_check(self):
        result=self.run_case(['id','v'],[['01','a'],['01','b']], [['01','c']], ['--ignore-keys','01'])
        self.assertEqual(result['left_excluded_rows'],2);self.assertEqual(result['matched_keys'],0)
    def test_composite_keys_match_entire_tuple(self):
        result=self.run_case(['id','part','v'],[['01','A','old'],['01','B','old']], [['01','A','new'],['01','B','new']], ['--ignore-keys','[["01","A"]]'],keys=('id','part'))
        self.assertEqual(result['changed_cells'],1);self.assertEqual(result['left_excluded_rows'],1)
    def test_comma_quoted_and_leading_zero_keys(self):
        result=self.run_case(['id','v'],[['a,b','x'],['001','x'],['1','x']], [['a,b','y'],['001','y'],['1','y']], ['--ignore-keys','"a,b", 001'])
        self.assertEqual(result['changed_cells'],1);self.assertEqual(result['left_excluded_rows'],2)
    def test_all_value_columns_ignored(self):
        result=self.run_case(['id','v'],[['1','old']],[['1','new']],['--ignore-columns','v'])
        self.assertEqual(result['equal_rows'],1);self.assertEqual(result['changed_rows'],0)
    def test_invalid_filters(self):
        for columns,keys_text in [(['id'],''),(['missing'],''),(['v','v'],''),([], '01,,02')]:
            with self.subTest(columns=columns,keys=keys_text),self.assertRaises(ValueError):
                validate_scope(['id'],['id','v'],columns,keys_text)
        for value in ['01,A', '[[1,"A"]]', '[["01"]]', '{}']:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_scope(['id','part'],['id','part'],[],value)
    def test_header_reads_no_data_rows(self):
        class HeaderOnly(io.StringIO):
            def __next__(self):
                if self.tell()>0:raise AssertionError('Read beyond header')
                return super().__next__()
        names=[f'c{i}' for i in range(2000)]
        with patch('builtins.open',return_value=HeaderOnly(','.join(names)+'\n')):
            self.assertEqual(header('unused',',','utf-8'),names)
    def test_exports_obey_filters(self):
        self.run_case(['id','keep','skip'],[['1','old','old'],['2','old','old']],[['1','new','new'],['2','new','new']],['--ignore-columns','skip','--ignore-keys','2'])
        export_excel(self.root/'result',self.root/'result.xlsx')
        with zipfile.ZipFile(self.root/'result.xlsx') as book:
            self.assertIn('name="keep"',book.read('xl/workbook.xml').decode())
            self.assertNotIn('name="skip"',book.read('xl/workbook.xml').decode())
        export_html(self.root/'result',self.root/'report.zip')
        with zipfile.ZipFile(self.root/'report.zip') as book:
            self.assertIn('Ignored columns',book.read('index.html').decode())
            self.assertNotIn('<td>skip</td>',book.read('differences_00001.html').decode())


class ScopeAPITests(ServerTests):
    def test_scope_through_api(self):
        left=b'id,keep,skip\n001,a,x\n002,a,x\n003,a,x\n';right=b'id,keep,skip\n001,a,y\n002,b,y\n003,b,y\n'
        job=self.request('/api/jobs',{'files':{'left':{'name':'left.csv','size':len(left)},'right':{'name':'right.csv','size':len(right)}}});path='/api/jobs/'+job['id']
        for side,data in [('left',left),('right',right)]:self.request(path+f'/files/{side}?offset=0',data,'PUT')
        self.request(path+'/finalize',{})
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):self.request(path+'/start',{'keys':['id'],'ignore_columns':['id']})
        self.request(path+'/start',{'keys':['id'],'ignore_columns':['skip'],'ignore_keys':'002,003','memory_mb':64})
        job=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(job['state'],'complete',job)
        self.assertEqual(job['summary']['equal_rows'],1);self.assertEqual(job['summary']['changed_cells'],0)
        self.assertEqual(job['summary']['left_excluded_rows'],2)

if __name__=='__main__':unittest.main()
