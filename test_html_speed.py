"""The indexed HTML path preserves the legacy drawer data and escaping."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mismatch_patterns as patterns
from report_patterns import pattern_drawer_chunks

class IndexedReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.tracker=patterns.Tracker()
        self.column='value</script>"&'
        self.tracker.add('["001"]',self.column,'</script><script>alert(1)</script>','雪 & <new>')
        self.summary={'changed_cells_by_column':{self.column:1,'missing':2},'analysis_notes':[{'comment':'<unsafe>'}]}

    def test_indexed_and_legacy_drawers_are_identical(self):
        patterns.save(self.tracker,self.root)
        indexed=''.join(pattern_drawer_chunks(self.root,self.summary,lambda message:None))
        with patch('mismatch_patterns.report_index',return_value=None):
            legacy=''.join(pattern_drawer_chunks(self.root,self.summary,lambda message:None))
        self.assertEqual(indexed,legacy)
        self.assertNotIn('<script>alert(1)</script>',indexed)
        self.assertIn('\\u003c/script\\u003e',indexed)
        self.assertEqual(patterns.load(self.root),self.tracker.result())

    def test_index_does_not_load_all_pattern_objects(self):
        patterns.save(self.tracker,self.root)
        with patch('mismatch_patterns.load',side_effect=AssertionError('Full parse is unnecessary')):
            self.assertTrue(list(pattern_drawer_chunks(self.root,self.summary,lambda message:None)))

    def test_stale_or_corrupt_index_falls_back(self):
        patterns.save(self.tracker,self.root)
        target=self.root/patterns.FILE
        target.write_text(json.dumps(self.tracker.result()))
        self.assertIsNone(patterns.report_index(self.root))
        self.assertTrue(list(pattern_drawer_chunks(self.root,self.summary,lambda message:None)))
        (self.root/(patterns.FILE+'.index')).write_text('not json')
        self.assertIsNone(patterns.report_index(self.root))

    def test_empty_pattern_file(self):
        patterns.save(patterns.Tracker(),self.root)
        self.assertEqual(patterns.load(self.root),dict(version=patterns.VERSION,columns={}))
        self.assertIsNotNone(patterns.report_index(self.root))

if __name__=='__main__':unittest.main()
