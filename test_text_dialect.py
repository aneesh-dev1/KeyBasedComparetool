"""Delimited text (.dat/.txt/.tsv) detection and the per-file dialect workflow."""
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

import test_ui
import text_dialect


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def detect(self, content, name='sample.dat', **settings):
        path = Path(self.temporary.name) / name
        path.write_bytes(content)
        return text_dialect.detect(path, **settings)

    def test_common_extract_formats(self):
        cases = [
            (b'id|name|amount\n1|Ann|10.50\n2|Bob|7\n', '|', True),
            (b'1|Ann|10.50\n2|Bob|7\n3|Cy|1\n', '|', False),
            (b'id\tname\n1\tAnn\n2\tBob\n', '\t', True),
            (b'id;name;note\n1;Ann;"a; b"\n2;Bob;x\n', ';', True),
            (b'1\x01Ann\x012024-01-02\n2\x01Bob\x012024-02-03\n', '\x01', False),
            (b'id,name\n1,"Smith, J"\n2,Lee\n', ',', True),
            (b'"id"|"name"\n"1"|"a,b,c"\n"2"|"d,e"\n', '|', True),
        ]
        for content, delimiter, header in cases:
            with self.subTest(content=content[:20]):
                found = self.detect(content)
                self.assertEqual((found['delimiter'], found['header']), (delimiter, header))
                self.assertEqual(found['detected'], ['encoding', 'delimiter', 'header'])

    def test_encoding_and_explicit_settings(self):
        self.assertEqual(self.detect('id|name\n1|Caf\xe9\n'.encode('cp1252'))['encoding'], 'cp1252')
        self.assertEqual(self.detect('id|name\n1|Caf\xe9\n'.encode('utf-16'))['encoding'], 'utf-16')
        self.assertEqual(self.detect('﻿id|name\n1|x\n'.encode('utf-8'))['delimiter'], '|')
        found = self.detect(b'a|b\n1|2\n', delimiter=',', encoding='utf-8-sig', header='no')
        self.assertEqual((found['delimiter'], found['header'], found['detected']), (',', False, []))

    def test_numeric_first_row_is_data_and_imperfect_headers_are_preserved(self):
        self.assertFalse(self.detect(b'100,200\n300,400\n')['header'])
        self.assertTrue(self.detect(b'a,a\nx,y\n')['header'])
        self.assertTrue(self.detect(b'name,city\nAnn,Paris\n')['header'])

    def test_blank_headers_keep_named_columns_and_first_data_row(self):
        from compare import read_header
        cases = [
            (b'id,,amount\n1,Ann,10\n2,Bob,20\n', True, ['id', 'column2', 'amount']),
            (b',name,amount\n1,Ann,10\n2,Bob,20\n', True, ['column1', 'name', 'amount']),
            (b'id,name,\n1,Ann,10\n2,Bob,20\n', True, ['id', 'name', 'column3']),
            (b'1,,10\n2,Bob,20\n3,Cy,30\n', False, ['column1', 'column2', 'column3']),
        ]
        for content, expected, names in cases:
            with self.subTest(content=content):
                dialect = self.detect(content)
                self.assertEqual(dialect['header'], expected)
                path = Path(self.temporary.name) / 'sample.dat'
                self.assertEqual(read_header(path, dialect['delimiter'], dialect['encoding'], dialect['header'])[0], names)


    def test_csv_blank_header_comparison_keeps_all_data_rows(self):
        from compare import compare, parser
        root = Path(self.temporary.name)
        left, right = root / 'left.csv', root / 'right.csv'
        left.write_text('id,,amount\n001,Ann,10\n002,Bob,20\n')
        right.write_text('id,,amount\n002,Bob,21\n001,Ann,10\n')
        args = parser().parse_args([str(left), str(right), '--keys', 'id', '--output', str(root / 'out')])
        args.dialects = {side: text_dialect.detect(path) for side, path in [('left', left), ('right', right)]}
        result = compare(args)
        self.assertEqual(result['matched_keys'], 2)
        self.assertEqual(result['changed_cells'], 1)
        self.assertEqual(result['changed_cells_by_column'], {'column2': 0, 'amount': 1})



class DialectWorkflowTests(unittest.TestCase):
    setUpClass = classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request = test_ui.ServerTests.request
    wait = test_ui.ServerTests.wait

    def upload(self, files, **settings):
        job = self.request('/api/jobs', dict(files={side: dict(name=name, size=len(data)) for side, (name, data) in files.items()}, **settings))
        path = '/api/jobs/' + job['id']
        for side, (_, data) in files.items():
            self.request(path + f'/files/{side}?offset=0', data, 'PUT')
        return path, self.request(path + '/finalize', {})

    def test_headerless_dat_against_csv_with_auto_detection(self):
        left = b'001|10.00|old\n002|20|same\n003|30|left\n'
        right = b'001,10.0,new\n002,20,same\n004,40,right\n'
        path, job = self.upload({'left': ('extract.dat', left), 'right': ('extract.csv', right)}, delimiter='auto', encoding='auto', header='auto')
        self.assertEqual(job['state'], 'ready', job)
        self.assertEqual(job['dialects']['left']['delimiter'], '|')
        self.assertEqual(job['dialects']['right']['delimiter'], ',')
        self.assertEqual(job['columns'], ['column1', 'column2', 'column3'])
        preview = self.request(path + '/source-preview?side=left&rows=3')
        self.assertEqual(preview['rows'][0], ['001', '10.00', 'old'])
        self.request(path + '/start', {'keys': ['column1'], 'memory_mb': 64})
        result = self.wait(path, lambda j: j['state'] in ('complete', 'error'))
        self.assertEqual(result['state'], 'complete', result)
        # Values compare as exact text: 10.00 and 10.0 differ.
        self.assertEqual(result['summary']['changed_cells'], 2)
        self.assertEqual((result['summary']['left_only'], result['summary']['right_only']), (1, 1))

    def test_short_csv_rows_finish_and_have_downloadable_audit(self):
        left = b'id,,amount\n1,Ann\n2,Bob,20\n'
        right = b'id,,amount\n1,Ann,10\n2,Bob,20\n'
        path, job = self.upload({'left': ('left.csv', left), 'right': ('right.csv', right)},
                               delimiter='auto', encoding='auto', header='auto')
        self.assertEqual(job['columns'], ['id', 'column2', 'amount'])
        self.request(path + '/start', {'keys': ['id'], 'memory_mb': 64})
        result = self.wait(path, lambda j: j['state'] in ('complete', 'error'))
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['summary']['matched_keys'], 1)
        self.assertEqual(result['summary']['skipped_keys'], 1)
        self.assertIn(b'1 fields missing', self.request(path + '/download/skipped_keys.csv'))
        self.assertIn(b'Completed with skipped records', self.request(path + '/download/comparison-report.html'))

    def test_changing_a_file_dialect_rereads_its_columns(self):
        left = b'id;name\n1;Ann\n2;Bob\n'
        right = b'id;name\n1;Ann\n2;Bo\n'
        path, job = self.upload({'left': ('a.txt', left), 'right': ('b.txt', right)}, delimiter=',', encoding='utf-8-sig', header='yes')
        self.assertEqual(job['columns'], ['id;name'])
        self.request(path + '/config', {'keys': ['id;name']})
        for side in ('left', 'right'):
            job = self.request(path + '/dialect', dict(side=side, delimiter=';', encoding='utf-8-sig', header=True))
        self.assertEqual(job['columns'], ['id', 'name'])
        self.assertEqual(job['draft']['keys'], [])
        self.assertFalse(job['headers_reviewed'])
        with self.assertRaises(HTTPError):
            self.request(path + '/dialect', dict(side='left', delimiter='"', encoding='utf-8-sig', header=True))
        self.request(path + '/start', {'keys': ['id'], 'memory_mb': 64})
        result = self.wait(path, lambda j: j['state'] in ('complete', 'error'))
        self.assertEqual(result['summary']['changed_cells'], 1, result)
        with self.assertRaises(HTTPError):
            self.request(path + '/dialect', dict(side='left', delimiter=',', encoding='utf-8-sig', header=True))


if __name__ == '__main__':
    unittest.main()
