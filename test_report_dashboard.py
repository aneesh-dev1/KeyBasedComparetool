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
    def test_single_report_all_columns_and_streamed_samples(self):
        from unittest.mock import patch
        target=self.root/'report.html'
        with patch('reports.partition_columns',side_effect=AssertionError('Should not partition all mismatches')):
            export_html(self.root,target)
        text=target.read_text()
        for value in ['columnSearch','Attributes with 100% match','66.6667%','50.0000%','before.csv','equal',"a&#x27;b",'Column match distribution','data:image/svg+xml;base64,']:
            self.assertIn(value,text)
        self.assertEqual(text.count('</main>'),1)
        self.assertEqual(text.count('</body>'),1)
        self.assertNotIn('.zip',text)
        self.assertNotIn('src="http',text)
        self.assertNotIn('column_00001',text)
        self.assertIn('1 sample keys of 1 mismatches',text)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;',text)
        self.assertNotIn('<script>x</script>',text)
        self.assertIn('id="sample-0"',text)
    def test_exact_bands_and_escape(self):
        from reports import match_bands
        self.summary.update(matched_keys=1000000,changed_cells_by_column={'perfect':0,'rounded':1,'99':10000,'below99':10001,'95':50000,'below95':50001})
        self.assertEqual([b[1] for b in match_bands(self.summary)],[1,2,2,1])
        self.summary['changed_cells_by_column']['<script>column</script>']=1
        text=make_summary_html(self.summary)
        self.assertIn('&lt;script&gt;column&lt;/script&gt;',text)
        self.assertNotIn('<script>column</script>',text)
        self.summary['matched_keys']=0
        self.assertEqual(sum(b[1] for b in match_bands(self.summary)),0)
    def test_no_matched_rows_not_reported_as_full_match(self):
        self.summary.update(matched_keys=0,changed_cells=0,changed_cells_by_column={'value':0})
        text=make_summary_html(self.summary)
        self.assertIn('No matched rows; match rates cannot be calculated.',text)
        self.assertIsNone(column_stats(self.summary)[0][3])
    def test_exclusion_highlights(self):
        self.summary['ignore_key_containers']=[dict(name='NO_HIT_KEYS',reason='No hit <reason>',keys=[['007']])]
        (self.root/'summary.json').write_text(json.dumps(self.summary))
        text=make_summary_html(self.summary)
        self.assertIn('Ignored key containers — excluded from comparison',text)
        self.assertIn('No hit &lt;reason&gt;',text)
        target=self.root/'highlight.xlsx';export_excel(self.root,target)
        with zipfile.ZipFile(target) as z:
            audit=ET.fromstring(z.read('xl/worksheets/sheet3.xml'))
            self.assertTrue(all(c.attrib['s']=='5' for c in audit.findall('.//s:c',NS)))
            summary=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            self.assertIn("'Ignored key containers'!A1",[link.attrib['location'] for link in summary.findall('s:hyperlinks/s:hyperlink',NS)])

    def test_numeric_delta_is_diagnostic_only(self):
        self.assertEqual(numeric_difference('4994','4993'),'1')
        self.assertEqual(numeric_difference('001','1'),'0')
        self.assertEqual(numeric_difference('0.3','0.2'),'0.1')
        for a in ['None','Infinity','NaN','1e99999999']: self.assertEqual(numeric_difference(a,'1'),'')

class SampleTests(unittest.TestCase):
    def test_twenty_per_column_composite_keys_and_truncation(self):
        from reports import mismatch_samples
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with (root/'differences.csv').open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['key_json','column','left_value','right_value'])
                for i in range(25):writer.writerow([json.dumps([f'{i:03}', 'part,one']),'many','x'*1200,'new'])
                for i in range(3):writer.writerow([json.dumps([str(i),'part,two']),'few','old','new'])
            result=mismatch_samples(root,dict(changed_cells_by_column={'many':25,'few':3,'equal':0}))
            self.assertEqual(len(result['many']),20);self.assertEqual(len(result['few']),3)
            self.assertEqual(result['many'][0][:2],['000','part,one'])
            self.assertEqual(result['many'][-1][0],'019')
            self.assertTrue(result['many'][0][2].endswith('[truncated]'))
            self.assertNotIn('equal',result)
    def test_stops_at_quota_and_empty_scope_does_not_read(self):
        from reports import mismatch_samples
        from unittest.mock import patch
        import io
        data='key_json,column,left_value,right_value\n'+''.join(f'"[""{i}""]",v,old,new\n' for i in range(20))+'invalid,row\n'
        with patch.object(Path,'open',return_value=io.StringIO(data)):
            self.assertEqual(len(mismatch_samples(Path('.'),dict(changed_cells_by_column={'v':100}))['v']),20)
        with patch.object(Path,'open',side_effect=AssertionError('No read expected')):
            self.assertEqual(mismatch_samples(Path('.'),dict(changed_cells_by_column={'equal':0})),{})
    def test_missing_sample_records_fail_explicitly(self):
        from reports import mismatch_samples
        from unittest.mock import patch
        import io
        with patch.object(Path,'open',return_value=io.StringIO('key_json,column,left_value,right_value\n')):
            with self.assertRaisesRegex(ValueError,'incomplete'):
                mismatch_samples(Path('.'),dict(changed_cells_by_column={'v':1}))
