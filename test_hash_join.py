import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from compare import compare, encode, parser, unpack_values
from hash_join import try_build
from task_control import Cancelled


class HashJoinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.events = []
        self.stats = {'untouched': 'yes'}

    def write(self, side, names, rows):
        path = self.root / (side + '.csv')
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(names)
            writer.writerows(rows)
        return path

    def args(self, **kwargs):
        values = dict(left=self.root/'left.csv', right=self.root/'right.csv', keys=['id'],
                      memory_mb=8, delimiter=',', encoding='utf-8', read_batch_size=100000,
                      excluded_keys=set(), allow_empty_keys=False, duplicate_policy='first',
                      diagnostics_file=self.root/'duplicates.json', diagnostic_columns=['id', 'v'])
        values.update(kwargs)
        return SimpleNamespace(**values)

    def build(self, args, left=None, right=None, canonical=None):
        return try_build(args, left or ['id', 'v'], right or ['id', 'v'],
                         canonical or ['id', 'v'], self.root, self.stats,
                         lambda args, message, **values: self.events.append((message, values)))

    def test_shuffled_missing_keys_and_first_last_duplicates(self):
        self.write('left', ['id', 'v'], [['z','first-z'], ['a','first-a'], ['z','last-z'], ['a','middle-a'], ['a','last-a'], ['left','only']])
        self.write('right', ['v', 'id'], [['first-r','a'], ['only','right'], ['last-r','a'], ['same','z']])
        for policy in ('first', 'last'):
            with self.subTest(policy=policy):
                self.stats = {}
                diagnostics = self.root / (policy + '.json')
                args = self.args(duplicate_policy=policy, diagnostics_file=diagnostics)
                result = self.build(args, right=['v', 'id'])
                audit = io.StringIO()
                pairs = list(result.iter_pairs(self.stats, csv.writer(audit), args))
                self.assertEqual([json.loads((a or b)[0])[0] for a,b in pairs], ['a','left','right','z'])
                self.assertEqual(unpack_values(pairs[0][0][1]), ['a', 'first-a' if policy=='first' else 'last-a'])
                self.assertEqual(unpack_values(pairs[0][1][1]), ['a', 'first-r' if policy=='first' else 'last-r'])
                self.assertIsNone(pairs[1][1])
                self.assertIsNone(pairs[2][0])
                self.assertEqual(self.stats['left_rows'], 6)
                self.assertEqual(self.stats['right_rows'], 4)
                self.assertEqual(self.stats['left_duplicate_keys'], 2)
                self.assertEqual(self.stats['left_duplicate_rows_skipped'], 3)
                self.assertEqual(list(csv.reader(io.StringIO(audit.getvalue()))), [
                    ['left','["a"]','3','2',policy], ['left','["z"]','2','1',policy], ['right','["a"]','2','1',policy]])
                sample = json.loads(diagnostics.read_text())
                self.assertEqual(sample['key'], ['a'])
                self.assertEqual(sample['samples'], [['a','first-a'],['a','middle-a'],['a','last-a']])

    def test_exact_quotes_projection_composite_keys_and_exclusions(self):
        names = ['id','scope','v','unused']
        rows = [['001','a','quoted,\r\nvalue','x'], ['002','a','embedded\x1f雪','y'], ['003','b',' 0.00 ','z'], ['','omit','ignored','q']]
        self.write('left', names, rows)
        self.write('right', names, list(reversed(rows)))
        for reference in (False, True):
            args = self.args(keys=['id','scope'], excluded_keys={encode(['003','b']), encode(['','omit'])}, reference_csv_reader=reference)
            result = self.build(args, names, names, ['scope','id','v'])
            pairs = list(result.iter_pairs(self.stats, None, args))
            self.assertEqual(len(pairs), 2)
            self.assertTrue(all(a==b for a,b in pairs))
            self.assertEqual(unpack_values(pairs[0][0][1]), ['a','001','quoted,\r\nvalue'])
            self.assertEqual(self.stats['left_excluded_rows'], 2)
            self.assertEqual(self.stats['right_excluded_rows'], 2)

    def test_budget_fallback_preserves_stats_and_creates_no_reports(self):
        rows = [[str(i), 'x'*6000] for i in range(10)]
        self.write('left', ['id','v'], rows)
        self.write('right', ['id','v'], rows)
        before = dict(self.stats)
        result = self.build(self.args(memory_mb=0.01))
        self.assertIsNone(result)
        self.assertEqual(self.stats, before)
        self.assertTrue(any('falling back' in message for message,_ in self.events))
        self.assertEqual({p.name for p in self.root.iterdir()}, {'left.csv','right.csv'})

    def test_existing_sort_checkpoint_is_reused_without_opening_inputs(self):
        (self.root/'left.json').write_text('{}')
        self.assertIsNone(self.build(self.args()))
        self.assertEqual(self.stats, {'untouched':'yes'})

    def test_close_releases_rows_and_diagnostic_samples(self):
        self.write('left', ['id','v'], [['a','first'], ['a','last']])
        self.write('right', ['id','v'], [['a','value']])
        result = self.build(self.args())
        self.assertGreater(result.retained_bytes, 0)
        self.assertTrue(result.samples)
        result.close()
        result.close()
        self.assertEqual(result.left, {})
        self.assertEqual(result.right, {})
        self.assertEqual(result.samples, {})
        self.assertEqual(result.retained_bytes, 0)

    def test_orphan_sorter_before_first_manifest_blocks_hash_restart(self):
        from checkpoints import lock
        with lock(self.root/'lock-left'):
            with self.assertRaisesRegex(ValueError, 'still owns'):
                self.build(self.args(checkpoint_dir=self.root))

    def test_right_file_budget_fallback_does_not_keep_left_counts(self):
        self.write('left', ['id','v'], [['a','small']])
        self.write('right', ['id','v'], [['a','x'*6000], ['b','x'*6000]])
        before = dict(self.stats)
        self.assertIsNone(self.build(self.args(memory_mb=0.01)))
        self.assertEqual(self.stats, before)
        self.assertTrue(any(message == 'Indexing left file complete' for message,_ in self.events))

    def test_invalid_data_is_not_converted_into_fallback(self):
        self.write('left', ['id','v'], [['','invalid']])
        self.write('right', ['id','v'], [['a','value']])
        with self.assertRaisesRegex(ValueError, 'empty key component'):
            self.build(self.args())
        self.assertEqual(self.stats, {'untouched':'yes'})

    def test_cancellation_checked_for_excluded_rows(self):
        self.write('left', ['id','v'], [['a','ignored']]*1001)
        self.write('right', ['id','v'], [])
        with patch('hash_join.check_cancel', side_effect=[None, Cancelled('cancelled')]):
            with self.assertRaises(Cancelled):
                self.build(self.args(excluded_keys={encode(['a'])}))
        self.assertEqual(self.stats, {'untouched':'yes'})

    def test_parquet_native_and_reference_payloads_are_exact(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.table({'id':['a','b','c'], 'ignored':[1,2,3], 'v':[None,'','雪\x1f']})
        left, right = self.root/'left.parquet', self.root/'right.parquet'
        pq.write_table(table, left)
        pq.write_table(table.take([2,0,1]), right)
        for reference in (False, True):
            args = self.args(left=left, right=right, reference_parquet_reader=reference)
            result = self.build(args, ['id','ignored','v'], ['id','ignored','v'])
            pairs = list(result.iter_pairs(self.stats, None, args))
            self.assertEqual([unpack_values(a[1]) for a,b in pairs], [['a',None],['b',''],['c','雪\x1f']])
            self.assertTrue(all(a==b for a,b in pairs))

    def test_hash_and_disk_results_match_with_rules_duplicates_and_unequal_rows(self):
        names = ['id','part','value','amount','status','unused']
        self.write('left', names, [
            ['2','é','left first','0.00','None','ignore'],
            ['1','a','left only','1','None','ignore'],
            ['2','é','left last','-11.00','None','ignore'],
            ['3','b',' A ','4','None','ignore'],
            ['4','c','skip','0','skip','ignore'],
        ])
        self.write('right', list(reversed(names)), [list(reversed(row)) for row in [
            ['3','b','a','4','none','ignore'],
            ['5','z','right only','1','none','ignore'],
            ['2','é','right first','0','none','ignore'],
            ['2','é','right last','-11','none','ignore'],
            ['4','c','skip changed','9','skip changed','ignore'],
        ]])
        for policy in ('first','last'):
            summaries = []
            results = []
            for algorithm in ('auto','sort'):
                output = self.root/(policy+'-'+algorithm)
                args = parser().parse_args([str(self.root/'left.csv'), str(self.root/'right.csv'),
                    '--keys','id','part','--output',str(output),'--engine',algorithm,
                    '--duplicate-policy',policy,'--ignore-columns','unused',
                    '--ignore-keys','[["4","c"]]','--sort-workers','1'])
                args.value_overrides = [dict(column='status', left='None', right='none')]
                args.comparison_rules = [dict(column='amount', tolerance='0'),
                                         dict(column='value', trim=True, ignore_case=True)]
                with patch('compare.progress'):
                    summary = compare(args)
                self.assertEqual(summary['execution_engine'], 'hash' if algorithm=='auto' else 'sort')
                for field in ('execution_engine','comparison_engine','elapsed_seconds'):
                    summary.pop(field)
                summaries.append(summary)
                parsed = {}
                for path in output.glob('*.csv'):
                    with path.open(newline='') as stream:
                        records = list(csv.reader(stream))
                    parsed[path.name] = (records[0], sorted(records[1:]))
                results.append(parsed)
            self.assertEqual(summaries[0], summaries[1])
            self.assertEqual(results[0], results[1])
            self.assertEqual(summaries[0]['changed_cells'], 1)
            self.assertEqual(summaries[0]['left_only'], 1)
            self.assertEqual(summaries[0]['right_only'], 1)


if __name__ == '__main__':
    unittest.main()
