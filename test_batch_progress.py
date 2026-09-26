import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from compare import compare, parser

class BatchProgressTests(unittest.TestCase):
    def test_partial_final_batch_and_sort_merge_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for side in ('left','right'):
                (root/f'{side}.csv').write_text('id,v\n'+''.join(f'{i},value\n' for i in range(10001)))
            args=parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'report')])
            args.memory_mb=0.1;args.fan_in=2;args.progress_file=root/'progress.json'
            log=io.StringIO()
            with contextlib.redirect_stdout(log):result=compare(args)
            text=log.getvalue()
            for side in ('left','right'):
                self.assertIn(f'{side} sort batch started',text)
                self.assertIn(f'{side} sort batch complete',text)
                self.assertIn(f'{side} merge batch started',text)
                self.assertIn(f'{side} merge batch complete',text)
            self.assertEqual(text.count('Comparison batch started'),2)
            self.assertEqual(text.count('Comparison batch complete'),2)
            self.assertIn('batch=2 | batch_keys=1 | rows=10,001',text)
            self.assertEqual(result['equal_rows'],10001)
            self.assertEqual(json.loads(args.progress_file.read_text())['stage'],'reports')
