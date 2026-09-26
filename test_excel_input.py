import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from urllib.error import HTTPError
from xml.sax.saxutils import escape
import test_workflow
from excel_input import convert, sheets


def workbook(rows, *, shared=False):
    """Small OOXML fixture; unselected sheet deliberately has invalid headers."""
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Wrong sheet" r:id="r1"/><sheet name="Data" r:id="r2"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/><Relationship Id="r2" Target="worksheets/sheet2.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet><sheetData><row r="1"><c r="A1"/></row></sheetData></worksheet>')
        z.writestr('xl/worksheets/sheet2.xml','<worksheet><sheetData>'+rows+'</sheetData></worksheet>')
        if shared:z.writestr('xl/sharedStrings.xml','<sst><si><t>id</t></si><si><r><t>va</t></r><r><t>lue</t></r><rPh><t>ignore</t></rPh></si><si><t>001</t></si></sst>')
    return output.getvalue()


def data_rows(value):
    return '<row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c><c r="B1" t="inlineStr"><is><t>value</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>001</t></is></c><c r="B2" t="inlineStr"><is><t>'+escape(value)+'</t></is></c></row>'


class ReaderTests(unittest.TestCase):
    def test_shared_strings_sparse_cells_and_cached_formula(self):
        content=workbook('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><f>1+1</f><v>2</v></c></row><row r="3"/><row r="4"><c r="A4" t="b"><v>1</v></c></row>',shared=True)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'in.xlsx').write_bytes(content)
            self.assertEqual([s['name'] for s in sheets(p/'in.xlsx')],['Wrong sheet','Data'])
            names,count=convert(p/'in.xlsx','Data',p/'out.csv')
            self.assertEqual(names,['id','value']);self.assertEqual(count,2)
            self.assertEqual(list(csv.reader(io.StringIO((p/'out.csv').read_text()))),[['id','value'],['001','2'],['TRUE','']])
    def test_missing_formula_and_invalid_sheet_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'in.xlsx').write_bytes(workbook(data_rows('ok').replace('<is><t>ok</t></is>','<f>1+1</f>').replace('r="B2" t="inlineStr"','r="B2"')))
            for name in ['Missing','Wrong sheet','Data']:
                with self.assertRaises(ValueError):convert(p/'in.xlsx',name,p/'out.csv')
                self.assertFalse((p/'out.csv').exists());self.assertFalse((p/'out.importing').exists())


class ExcelAPITests(unittest.TestCase):
    setUpClass=classmethod(test_workflow.WorkflowAPITests.setUpClass.__func__)
    tearDownClass=classmethod(test_workflow.WorkflowAPITests.tearDownClass.__func__)
    request=test_workflow.WorkflowAPITests.request
    wait=test_workflow.WorkflowAPITests.wait
    def prepare(self,left,right,right_name='right.xlsx'):
        job=self.request('/api/jobs',{'files':{'left':{'name':'left.xlsx','size':len(left)},'right':{'name':right_name,'size':len(right)}}})
        path='/api/jobs/'+job['id']
        for side,content in [('left',left),('right',right)]:self.request(path+f'/files/{side}?offset=0',content,'PUT')
        selected=self.request(path+'/finalize',{})
        self.assertEqual(selected['state'],'selecting_sheets')
        self.assertEqual(selected['files']['left']['sheets'][1]['name'],'Data')
        with self.assertRaises(HTTPError):self.request(path+'/start',{'keys':['id']})
        with self.assertRaises(HTTPError):self.request(path+'/select-sheets',{'left':'Missing','right':'Data'})
        self.request(path+'/select-sheets',{'left':'Data','right':'Data'})
        prepared=self.wait(path,lambda j:j['state'] in ('ready','error'))
        self.assertEqual(prepared['state'],'ready',prepared)
        self.assertEqual(prepared['columns'],['id','value'])
        return path
    def test_excel_flow_and_sources_in_reports(self):
        path=self.prepare(workbook(data_rows('None')),workbook(data_rows('none')))
        self.assertEqual(self.request(path+'/source-preview?side=left&rows=1')['rows'],[['001','None']])
        self.request(path+'/start',{'keys':['id'],'value_overrides':[{'column':'value','left':'None','right':'none'}]})
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        self.assertEqual(result['summary']['override_equivalent_cells'],1)
        self.assertEqual(result['summary']['sources'][0]['sheet'],'Data')
    def test_mixed_excel_csv(self):
        path=self.prepare(workbook(data_rows('old')),b'id,value\n001,new\n','right.csv')
        self.request(path+'/start',{'keys':['id']})
        result=self.wait(path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result);self.assertEqual(result['summary']['changed_cells'],1)
    def test_reject_legacy_excel(self):
        with self.assertRaises(HTTPError):self.request('/api/jobs',{'files':{'left':{'name':'old.xls','size':1},'right':{'name':'a.csv','size':1}}})

if __name__=='__main__':unittest.main()
