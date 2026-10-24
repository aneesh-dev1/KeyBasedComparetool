"""Fixed-width files: layouts from positions or a copybook, exact slicing, and the upload-to-compare flow."""
import csv
import tempfile
import unittest
from pathlib import Path

import fixed_width
import test_ui

COPYBOOK = '''
      * Account extract
       01  ACCOUNT-RECORD.
           05  ACCT-ID        PIC X(6).
           05  FILLER         PIC X(2).
           05  BALANCE        PIC S9(5)V99.
           05  STATUS-CODE    PIC X(4).
'''


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def convert(self, content, fields, **settings):
        source, target = self.root / 'in.dat', self.root / 'out.csv'
        source.write_bytes(content)
        count = fixed_width.convert(source, fields, target, **settings)
        return count, list(csv.reader(target.read_text(encoding='utf-8').splitlines()))

    def test_positions(self):
        fields = fixed_width.parse_positions('# name,start,length\nid,1,3\nname,4,5\nflag,9,1\n')
        count, rows = self.convert(b'001Ann  Y\r\n002Bob  N\r\n', fields)
        self.assertEqual(count, 2)
        self.assertEqual(rows, [['id', 'name', 'flag'], ['001', 'Ann', 'Y'], ['002', 'Bob', 'N']])
        _, untrimmed = self.convert(b'001Ann  Y\n', fields, trim=False)
        self.assertEqual(untrimmed[1], ['001', 'Ann  ', 'Y'])

    def test_copybook_filler_and_implied_decimals(self):
        fields = fixed_width.parse_copybook(COPYBOOK)
        self.assertEqual([(f['name'], f['start'], f['length'], f['decimals']) for f in fields],
                         [('ACCT-ID', 1, 6, 0), ('BALANCE', 9, 7, 2), ('STATUS-CODE', 16, 4, 0)])
        _, rows = self.convert(b'A00001xx0012345OPEN\nA00002xx-000050HOLD\n', fields)
        self.assertEqual(rows[1:], [['A00001', '00123.45', 'OPEN'], ['A00002', '-0000.50', 'HOLD']])

    def test_rejections(self):
        for text in ['05 X PIC S9(5) COMP-3.', '05 X PIC X(3) OCCURS 5 TIMES.', '01 GROUP-ONLY.']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                fixed_width.parse_copybook(text)
        for text in ['id,1', 'id,0,3', 'id,1,3\nID,4,2']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                fixed_width.parse_positions(text)

    def test_record_length_ebcdic_and_short_records(self):
        fields = fixed_width.parse_positions('id,1,2\nv,3,2')
        _, rows = self.convert('01ab02cd'.encode('cp037'), fields, encoding='cp037', record_length=4)
        self.assertEqual(rows[1:], [['01', 'ab'], ['02', 'cd']])
        with self.assertRaisesRegex(ValueError, 'Record 2 is 3 characters'):
            self.convert(b'01ab\n02c\n', fields)


class FixedWidthServerTests(unittest.TestCase):
    setUpClass = classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request = test_ui.ServerTests.request
    wait = test_ui.ServerTests.wait

    def test_upload_layout_compare_and_relayout(self):
        left = b'001Ann  10.50\n002Bob  20.00\n003Cy   30.00\n'
        right = b'id,name,amount\n001,Ann,10.50\n002,Bob,21.00\n004,Dee,40.00\n'
        job = self.request('/api/jobs', {'delimiter': 'auto', 'encoding': 'auto', 'header': 'auto',
                                         'files': {'left': {'name': 'extract.dat', 'size': len(left)}, 'right': {'name': 'report.csv', 'size': len(right)}}})
        path = '/api/jobs/' + job['id']
        for side, content in (('left', left), ('right', right)):
            self.request(path + f'/files/{side}?offset=0', content, 'PUT')
        self.request(path + '/finalize', {})
        # A wrong layout first: the file must survive and the error is reported.
        self.request(path + '/fixed-width', {'side': 'left', 'format': 'positions', 'layout': 'id,1,3\nname,4,5\namount,9,9'})
        failed = self.wait(path, lambda j: j['state'] == 'ready')
        self.assertIn('Record 1', failed['fixed_width_error']['message'])
        self.request(path + '/fixed-width', {'side': 'left', 'format': 'positions', 'layout': 'id,1,3\nname,4,5\namount,9,5'})
        ready = self.wait(path, lambda j: j['state'] == 'ready' and j.get('dialects', {}).get('left', {}).get('fixed'))
        self.assertEqual(ready['columns'], ['id', 'name', 'amount'])
        self.assertEqual(ready['dialects']['left']['fixed']['records'], 3)
        preview = self.request(path + '/source-preview?side=left&rows=2')
        self.assertEqual(preview['rows'][0], ['001', 'Ann', '10.50'])
        # Changing the layout converts again from the original file.
        self.request(path + '/fixed-width', {'side': 'left', 'format': 'positions', 'layout': 'id,1,3\nname,4,5\namount,9,5\nwhole,9,2'})
        again = self.wait(path, lambda j: j['state'] == 'ready' and j.get('dialects', {}).get('left', {}).get('fixed', {}).get('fields') == 4)
        self.assertIn('whole', again['source_headers']['left'])
        self.request(path + '/fixed-width', {'side': 'left', 'format': 'positions', 'layout': 'id,1,3\nname,4,5\namount,9,5'})
        self.wait(path, lambda j: j['state'] == 'ready' and j.get('dialects', {}).get('left', {}).get('fixed', {}).get('fields') == 3)
        self.request(path + '/start', {'keys': ['id'], 'memory_mb': 64})
        result = self.wait(path, lambda j: j['state'] in ('complete', 'error'))
        self.assertEqual(result['state'], 'complete', result.get('error'))
        self.assertEqual((result['summary']['changed_cells'], result['summary']['left_only'], result['summary']['right_only']), (1, 1, 1))


if __name__ == '__main__':
    unittest.main()
