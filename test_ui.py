import csv, io, json, tempfile, threading, time, unittest, zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
from compare import compare, parser
from reports import export_excel, export_html, sheet_names, write_xlsx
from server import make_server
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def report(self,names,left,right,keys=('id',)):
        for filename,rows in [('left.csv',left),('right.csv',right)]:
            with (self.root/filename).open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(list(keys)+names);w.writerows(rows)
        compare(parser().parse_args([str(self.root/'left.csv'),str(self.root/'right.csv'),'--keys',*keys,'--output',str(self.root/'report')]))
        return self.root/'report'
    def test_ten_changed_columns_exactly_ten_sheets(self):
        columns=[f'column_{i}' for i in range(10)]
        report=self.report(columns,[['001','A']+['old']*10,['002','B']+['same']*10],[['001','A']+['new']*10,['002','B']+['same']*10],keys=('id','part'))
        target=self.root/'result.xlsx';export_excel(report,target)
        with zipfile.ZipFile(target) as book:
            root=ET.fromstring(book.read('xl/workbook.xml'))
            self.assertEqual([s.attrib['name'] for s in root.findall('s:sheets/s:sheet',NS)],columns)
            for i,column in enumerate(columns,1):
                root=ET.fromstring(book.read(f'xl/worksheets/sheet{i}.xml'))
                rows=[[c.find('s:is/s:t',NS).text or '' for c in row] for row in root.findall('s:sheetData/s:row',NS)]
                self.assertEqual(rows,[['Column',column],['Key: id','Key: part','Left value','Right value'],['001','A','old','new']])
    def test_only_own_column(self):
        report=self.report(['a','b'],[['01','x','x'],['02','x','same'],['03','same','x']],[['01','y','y'],['02','y','same'],['03','same','y']])
        target=self.root/'result.xlsx';export_excel(report,target)
        with zipfile.ZipFile(target) as book:
            for index,keys in [(1,['01','02']),(2,['01','03'])]:
                rows=ET.fromstring(book.read(f'xl/worksheets/sheet{index}.xml')).findall('s:sheetData/s:row',NS)[2:]
                self.assertEqual([r[0].find('s:is/s:t',NS).text for r in rows],keys)
    def test_text_safety_and_html_pagination(self):
        report=self.report(['value'],[['001','=1+1'],['002','<script>alert(1)</script>']],[['001','01'],['002','&']])
        target=self.root/'result.xlsx';export_excel(report,target)
        with zipfile.ZipFile(target) as book:
            root=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
            self.assertFalse(root.findall('.//s:f',NS));self.assertIn('=1+1',[e.text for e in root.findall('.//s:t',NS)])
        target=self.root/'html.zip';export_html(report,target,page_size=1)
        with zipfile.ZipFile(target) as book:
            text=book.read('differences_00002.html').decode();self.assertNotIn('<script>',text);self.assertIn('&lt;script&gt;',text)
    def test_sheet_names(self):
        names=sheet_names(['a/b','a?b','a'*40,'a'*39+'b',"'hello'",'History','NAME','name'])
        self.assertEqual(names[:2],['a_b','a_b (2)']);self.assertEqual(len(names),len(set(s.casefold() for s in names)));self.assertTrue(all(len(s)<=31 for s in names))
    def test_multiple_column_buckets(self):
        names=[f'field_{i}' for i in range(130)]
        report=self.report(names,[['01']+['a']*130],[['01']+['b']*130])
        target=self.root/'wide.xlsx';export_excel(report,target)
        with zipfile.ZipFile(target) as book:
            root=ET.fromstring(book.read('xl/workbook.xml'))
            self.assertEqual(len(root.findall('s:sheets/s:sheet',NS)),130)
            last=ET.fromstring(book.read('xl/worksheets/sheet130.xml'))
            values=[v.text for v in last.findall('s:sheetData/s:row',NS)[2].findall('.//s:t',NS)]
            self.assertEqual(values,['01','a','b'])
    def test_no_changes(self):
        report=self.report(['v'],[['001','x']],[['001','x']]);target=self.root/'result.xlsx';export_excel(report,target)
        with zipfile.ZipFile(target) as book:self.assertIn('No mismatches',book.read('xl/workbook.xml').decode())
    def test_long_value_not_truncated(self):
        with self.assertRaisesRegex(ValueError,'32,767'):write_xlsx(self.root/'long.xlsx',[('test',iter([['header'],['a'*32768]]))])

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=make_server(cls.temp.name,0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_address[1]}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.server.app.pool.shutdown();cls.temp.cleanup()
    def request(self,path,body=None,method=None,auth=True):
        if isinstance(body,dict):body=json.dumps(body).encode()
        with urlopen(Request(self.base+path,data=body,headers={'X-App-Token':self.server.app.token} if auth else {},method=method),timeout=20) as r:
            value=r.read();return json.loads(value) if r.headers.get_content_type()=='application/json' else value
    def wait(self,path,predicate):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            result=self.request(path)
            if predicate(result):return result
            time.sleep(.05)
        self.fail('Worker timed out')
    def test_full_flow(self):
        left=b'id,price,status\n001,10,old\n002,20,same\n003,30,left\n';right=b'id,price,status\n001,11,new\n002,20,same\n004,40,right\n'
        job=self.request('/api/jobs',{'files':{'left':{'name':'a.csv','size':len(left)},'right':{'name':'b.csv','size':len(right)}}});path='/api/jobs/'+job['id']
        for side,data in [('left',left),('right',right)]:
            self.request(path+f'/files/{side}?offset=0',data[:10],'PUT')
            with self.assertRaises(HTTPError):self.request(path+f'/files/{side}?offset=0',data[:10],'PUT')
            self.request(path+f'/files/{side}?offset=10',data[10:],'PUT')
        self.assertEqual(self.request(path+'/finalize',{})['state'],'ready');self.request(path+'/start',{'keys':['id'],'memory_mb':64})
        result=self.wait(path,lambda j:j['state'] in ('complete','error'));self.assertEqual(result['state'],'complete',result);self.assertEqual(result['summary']['changed_cells'],2)
        self.assertEqual(len(self.request(path+'/preview?category=differences')['rows']),2)
        for kind,filename in [('excel','mismatches.xlsx'),('html','html.zip')]:
            self.request(path+'/export/'+kind,{});result=self.wait(path,lambda j:j['exports'][kind]['state'] in ('complete','error'));self.assertEqual(result['exports'][kind]['state'],'complete',result)
            with zipfile.ZipFile(io.BytesIO(self.request(path+'/download/'+filename))) as archive:
                self.assertIsNone(archive.testzip())
                if kind=='excel':self.assertEqual([s.attrib['name'] for s in ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS)],['price','status'])
        self.assertIn(b'003',self.request(path+'/download/left_only.csv'));self.assertIn(b'004',self.request(path+'/download/right_only.csv'))
    def test_auth_required(self):
        with self.assertRaises(HTTPError):self.request('/api/jobs',{},auth=False)

if __name__=='__main__':unittest.main()
