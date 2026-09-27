import csv
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from reports import export_excel, export_html, make_summary_html, column_stats, numeric_difference
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.summary=dict(keys=['id'],matched_keys=2,changed_cells=2,left_only=1,right_only=0,changed_cells_by_column={'TOC':1,"a'b":1,'equal':0},sources=[dict(side='left',file='before.csv'),dict(side='right',file='after.csv')])
        (self.root/'summary.json').write_text(json.dumps(self.summary))
        for name,rows in {'differences':[['key_json','column','left_value','right_value'],['["001"]','TOC','4994','4993'],['["002"]',"a'b",'<script>x</script>','=1+1']], 'left_only':[['key_json'],['["003"]']], 'right_only':[['key_json']]}.items():
            with (self.root/(name+'.csv')).open('w',newline='') as f: csv.writer(f).writerows(rows)
    def test_workbook_links_types_and_reserved_names(self):
        target=self.root/'report.xlsx';export_excel(self.root,target)
        with zipfile.ZipFile(target) as z:
            sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS)
            names=[s.attrib['name'] for s in sheets]
            self.assertEqual(names,['File Summary','TOC','TOC (2)',"a'b"])
            toc=ET.fromstring(z.read('xl/worksheets/sheet2.xml'))
            links=toc.findall('s:hyperlinks/s:hyperlink',NS)
            self.assertEqual([l.attrib['location'] for l in links],["'TOC (2)'!A1","'a''b'!A1"])
            detail=ET.fromstring(z.read('xl/worksheets/sheet3.xml'))
            self.assertEqual(detail.find('s:hyperlinks/s:hyperlink',NS).attrib['location'],"'TOC'!A1")
            values=[v.text for v in detail.findall('.//s:t',NS)]
            for value in ['001','4994','4993','diffAB','1','text']: self.assertIn(value,values)
            self.assertFalse(detail.findall('.//s:f',NS))
            self.assertEqual(detail.find('s:autoFilter',NS).attrib['ref'],'A1:H2')
            for name in z.namelist():
                if name.endswith('.xml'): ET.fromstring(z.read(name))
    def test_dashboard_links_escape_and_all_column_pages(self):
        target=self.root/'report.zip';export_html(self.root,target,page_size=1)
        with zipfile.ZipFile(target) as z:
            index=z.read('index.html').decode()
            for value in ['columnSearch','Attributes with 100% match','66.6667%','50.0000%','before.csv','&lt;script&gt;']: self.assertIn(value,index)
            self.assertNotIn('<script>x</script>',index)
            self.assertEqual(index.count('</main>'),1)
            self.assertEqual(index.count('</body>'),1)
            for name in ['column_00001_00001.html','column_00002_00001.html','differences_00002.html','left_only_00001.html']: self.assertIn(name,z.namelist())
    def test_no_matched_rows_not_reported_as_full_match(self):
        self.summary.update(matched_keys=0,changed_cells=0,changed_cells_by_column={'value':0})
        text=make_summary_html(self.summary)
        self.assertIn('No matched rows; match rates cannot be calculated.',text)
        self.assertIsNone(column_stats(self.summary)[0][3])
    def test_numeric_delta_is_diagnostic_only(self):
        self.assertEqual(numeric_difference('4994','4993'),'1')
        self.assertEqual(numeric_difference('001','1'),'0')
        self.assertEqual(numeric_difference('0.3','0.2'),'0.1')
        for a in ['None','Infinity','NaN','1e99999999']: self.assertEqual(numeric_difference(a,'1'),'')
