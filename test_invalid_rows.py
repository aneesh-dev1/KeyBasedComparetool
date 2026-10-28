import csv
import json
import tempfile
import unittest
from pathlib import Path
from compare import compare, parser

class InvalidRowsTests(unittest.TestCase):
    def run_case(self, engine, left, right, **options):
        root = Path(self.temp.name) / (engine + str(len(list(Path(self.temp.name).iterdir()))))
        root.mkdir()
        for side, content in [('left', left), ('right', right)]:
            (root / (side + '.csv')).write_text(content)
        args = parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id',
            '--output',str(root/'out'),'--engine',engine,'--memory-mb','64','--workers','2'])
        for name,value in options.items():setattr(args,name,value)
        result = compare(args)
        with (root/'out'/'skipped_keys.csv').open() as f:audit=list(csv.DictReader(f))
        return result,audit,root

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_skip_key_on_both_sides_and_preserve_empty_cells(self):
        for engine in ['auto','sort','partitioned']:
            with self.subTest(engine=engine):
                result,audit,_ = self.run_case(engine,'id,a,b\n1,x\n2,,z\n3,a,b\n','id,a,b\n3,a,c\n1,x,y\n2,,z\n')
                self.assertEqual((result['matched_keys'],result['changed_cells'],result['equal_rows']), (2,1,1))
                self.assertEqual((result['left_only'],result['right_only'],result['skipped_keys']), (0,0,1))
                self.assertEqual((result['left_compared_rows'],result['right_compared_rows']),(2,2))
                self.assertEqual(audit[0]['reason'],'1 fields missing')

    def test_invalid_duplicate_wins_over_first_and_last(self):
        for engine in ['auto','sort','partitioned']:
            for policy in ['first','last']:
                with self.subTest(engine=engine,policy=policy):
                    result,audit,_=self.run_case(engine,'id,a,b\n1,a,b\n1,x\n1,a,b\n2,a,b\n',
                        'id,a,b\n1,a,b\n2,a,b\n',duplicate_policy=policy)
                    self.assertEqual(result['skipped_keys'],1)
                    self.assertEqual(result['left_compared_rows'],1)
                    self.assertEqual(result['matched_keys'],1)
                    self.assertEqual(len(audit),1)

    def test_missing_key_extra_fields_and_multiline(self):
        for engine in ['auto','sort','partitioned']:
            with self.subTest(engine=engine):
                result,audit,_=self.run_case(engine,'id,a,b\n\n1,"two\nlines"\n2,a,b,extra\n3,a,b\n',
                    'id,a,b\n3,a,b\n2,a,b\n1,a,b\n',fingerprint='on')
                self.assertEqual(result['unidentified_records'],1)
                self.assertEqual(result['skipped_keys'],2)
                self.assertEqual(result['matched_keys'],1)
                self.assertEqual(result['malformed_records'],3)
                self.assertEqual(len(audit),3)

    def test_unclosed_quotes_remain_errors(self):
        for engine in ['auto','sort','partitioned']:
            with self.subTest(engine=engine):
                with self.assertRaises((csv.Error,ValueError)):
                    self.run_case(engine,'id,a,b\n1,"unfinished\n','id,a,b\n1,a,b\n')

    def test_reports_include_audit_and_avoid_false_clean_verdict(self):
        import zipfile
        from reports import export_html, export_excel, report_summary, make_summary_html
        result,audit,root=self.run_case('auto','id,a,b\n1,x\n2,a,b\n','id,a,b\n1,x,y\n2,a,b\n')
        html_path,xlsx_path=root/'report.html',root/'report.xlsx'
        export_html(root/'out',html_path)
        export_excel(root/'out',xlsx_path)
        text=html_path.read_text()
        self.assertIn('Completed with skipped records',text)
        self.assertIn('1 fields missing',text)
        self.assertNotIn('No differences within selected scope',text)
        self.assertEqual(len(report_summary(root/'out')['skipped_records_preview']),1)
        with zipfile.ZipFile(xlsx_path) as book:
            self.assertIn('Skipped keys',book.read('xl/workbook.xml').decode())
            self.assertIn('1 fields missing',book.read('xl/worksheets/sheet3.xml').decode())

    def test_resume_preserves_audit_without_double_counting(self):
        from unittest.mock import patch
        import compare as engine_module
        for engine in ['sort','partitioned']:
            root=Path(self.temp.name)/('resume-'+engine);root.mkdir()
            for side,data in [('left','id,a,b\n1,x\n2,a,b\n'),('right','id,a,b\n1,x,y\n2,a,b\n')]:
                (root/(side+'.csv')).write_text(data)
            args=parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'out'),'--engine',engine,'--workers','2','--memory-mb','64','--checkpoint-dir',str(root/'checkpoints')])
            original=engine_module.progress
            def stop(args, phase, **values):
                original(args, phase, **values)
                if phase == 'Comparing keys':
                    raise RuntimeError('interrupt')
            with patch('compare.progress',side_effect=stop):
                with self.assertRaises(RuntimeError):compare(args)
            result=compare(args)
            self.assertEqual(result['skipped_keys'],1)
            with (root/'out'/'skipped_keys.csv').open() as f:self.assertEqual(len(list(csv.DictReader(f))),1)

    def test_review_revision_retains_audit(self):
        from review_revision import revise
        result,audit,root=self.run_case('auto','id,a,b\n1,x\n2,a,b\n','id,a,b\n1,x,y\n2,a,c\n')
        config=dict(keys=['id'], comparison_rules=[], value_overrides=[], ignore_columns=[], ignore_keys='')
        revised=revise(root/'out',root/'revision',config,['id','a','b'],lambda message:None)
        self.assertEqual(revised['skipped_keys'],1)
        self.assertEqual((root/'revision'/'skipped_keys.csv').read_bytes(),(root/'out'/'skipped_keys.csv').read_bytes())

    def test_1800_columns(self):
        names=['id']+['c'+str(n) for n in range(1799)]
        valid=['k']+['x']*1799
        result,audit,_=self.run_case('auto',','.join(names)+'\n'+','.join(valid[:1626])+'\n',
            ','.join(names)+'\n'+','.join(valid)+'\n')
        self.assertEqual(result['skipped_keys'],1)
        self.assertEqual(audit[0]['expected_fields'],'1800')
        self.assertEqual(audit[0]['actual_fields'],'1626')
        self.assertEqual(result['matched_keys'],0)

if __name__=='__main__':unittest.main()
