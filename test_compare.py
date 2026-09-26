import csv
import json
from pathlib import Path
import random
import tempfile
import unittest
from compare import compare, parser


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, header, rows):
        path = self.root / name
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def run_comparison(self, left, right, keys=('id',)):
        args = parser().parse_args([str(left), str(right), '--keys', *keys,
                                   '--output', str(self.root / 'report'), '--memory-mb', '1', '--fan-in', '2'])
        return compare(args)

    def test_composite_reordered_and_exact(self):
        left = self.write('a.csv', ['id', 'part', 'value'],
                          [['a', 'x', '01'], ['a', 'y', '雪\n\t,"'], ['b', 'x', ''], ['only-a', 'x', 'v']])
        right = self.write('b.csv', ['value', 'part', 'id'],
                           [['雪\n\t,"', 'y', 'a'], ['1', 'x', 'a'], ['', 'x', 'b'], ['v', 'x', 'only-b']])
        result = self.run_comparison(left, right, ('id', 'part'))
        self.assertEqual([result[k] for k in ('equal_rows', 'changed_rows', 'changed_cells', 'left_only', 'right_only')], [2, 1, 1, 1, 1])
        with (self.root / 'report/differences.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [dict(key_json='["a","x"]', column='value', left_value='01', right_value='1')])

    def test_duplicate_rejected_even_when_missing(self):
        left = self.write('a.csv', ['id', 'v'], [['a', 'x'], ['a', 'y']])
        right = self.write('b.csv', ['id', 'v'], [])
        with self.assertRaisesRegex(ValueError, 'duplicate key'):
            self.run_comparison(left, right)
        self.assertFalse((self.root / 'report/summary.json').exists())

    def test_empty_files_with_headers(self):
        left = self.write('a.csv', ['id'], [])
        right = self.write('b.csv', ['id'], [])
        self.assertEqual(self.run_comparison(left, right)['matched_keys'], 0)

    def test_bad_row(self):
        left = self.write('a.csv', ['id', 'v'], [['1']])
        right = self.write('b.csv', ['id', 'v'], [])
        with self.assertRaisesRegex(ValueError, 'expected 2 fields'):
            self.run_comparison(left, right)

    def test_empty_key(self):
        left = self.write('a.csv', ['id'], [['']])
        right = self.write('b.csv', ['id'], [])
        with self.assertRaisesRegex(ValueError, 'empty key'):
            self.run_comparison(left, right)

    def test_schema_mismatch(self):
        left = self.write('a.csv', ['id', 'v'], [])
        right = self.write('b.csv', ['id', 'other'], [])
        with self.assertRaisesRegex(ValueError, 'Schema mismatch'):
            self.run_comparison(left, right)

    def test_wide_multiple_merge_passes(self):
        names = ['id'] + [f'c{i}' for i in range(1999)]
        rows = [[str(i)] + ['abcdef'] * 1999 for i in range(400)]
        left = self.write('a.csv', names, rows)
        rows[15][1000] = 'changed'
        rows[16][1999] = 'trailing space '
        random.Random(7).shuffle(rows)
        right = self.write('b.csv', names, rows)
        result = self.run_comparison(left, right)
        self.assertEqual(result['equal_rows'], 398)
        self.assertEqual(result['changed_cells'], 2)
        self.assertEqual(result['changed_cells_by_column']['c999'], 1)


if __name__ == '__main__':
    unittest.main()
