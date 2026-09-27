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
        result=self.run_comparison(left, right)
        self.assertEqual(result['changed_cells_by_column'], {})
        self.assertEqual(result['unmatched_columns'], {'left':['v'],'right':['other']})

    def test_more_columns_in_left_file(self):
        left=self.write('a.csv',['id','left_extra','Value','another_extra'],[['001','x','old','y']])
        right=self.write('b.csv',['value','ID'],[['new','001']])
        result=self.run_comparison(left,right)
        self.assertEqual(result['changed_cells_by_column'],{'Value':1})
        self.assertEqual(result['unmatched_columns'],{'left':['left_extra','another_extra'],'right':[]})
        self.assertEqual(result['matched_keys'],1)

    def test_more_columns_in_right_file(self):
        left=self.write('a.csv',['id','v'],[['001','same']])
        right=self.write('b.csv',['extra1','V','ID','extra2'],[['x','same','001','y']])
        result=self.run_comparison(left,right)
        self.assertEqual(result['changed_cells_by_column'],{'v':0})
        self.assertEqual(result['unmatched_columns'],{'left':[],'right':['extra1','extra2']})
        self.assertEqual(result['equal_rows'],1)

    def test_missing_key_in_one_file_is_rejected(self):
        left=self.write('a.csv',['id','v'],[['001','a']])
        right=self.write('b.csv',['different_key','v'],[['001','a']])
        with self.assertRaisesRegex(ValueError,'Keys must be distinct existing column names'):
            self.run_comparison(left,right)

    def test_no_common_columns_is_rejected(self):
        left=self.write('a.csv',['id','v'],[['001','a']])
        right=self.write('b.csv',['other_id','other_value','extra'],[['001','a','x']])
        with self.assertRaisesRegex(ValueError,'Keys must be distinct existing column names'):
            self.run_comparison(left,right)

    def test_only_key_is_common(self):
        left=self.write('a.csv',['id','left_only'],[['001','a'],['002','b']])
        right=self.write('b.csv',['right_only','ID','extra'],[['x','001','z'],['y','003','z']])
        result=self.run_comparison(left,right)
        self.assertEqual(result['changed_cells_by_column'],{})
        self.assertEqual([result[k] for k in ('matched_keys','left_only','right_only')],[1,1,1])

    def test_renaming_unequal_header_counts(self):
        left=self.write('a.csv',['id','AT01S','unused'],[['001','a','x']])
        right=self.write('b.csv',['AT02S','ID'],[['b','001']])
        args=parser().parse_args([str(left),str(right),'--keys','id','--output',str(self.root/'report')])
        args.column_headers={'left':['id','AT01S','unused'],'right':['AT01S','ID']}
        result=compare(args)
        self.assertEqual(result['changed_cells_by_column'],{'AT01S':1})
        self.assertEqual(result['unmatched_columns']['left'],['unused'])

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
