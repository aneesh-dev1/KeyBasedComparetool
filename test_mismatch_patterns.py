"""Mismatch patterns are counted while comparing, match differences.csv, and stay bounded in memory."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import mismatch_patterns
import partitioned
from compare import compare, parser


class TrackerTests(unittest.TestCase):
    def test_counts_first_keys_and_order(self):
        tracker = mismatch_patterns.Tracker()
        for i in range(8):
            tracker.add(json.dumps([f'K{i}']), 'status', 'open', 'void')
        tracker.add('["K9"]', 'status', 'open', 'closed')
        result = tracker.result()['columns']['status']
        self.assertEqual(result['patterns'][0], ['open', 'void', 8, [['K0'], ['K1'], ['K2'], ['K3'], ['K4']]])
        self.assertEqual(result['patterns'][1], ['open', 'closed', 1, [['K9']]])
        self.assertEqual((result['more_patterns'], result['more_cells'], result['partial']), (0, 0, False))

    def test_limits_keep_counts_adding_up(self):
        tracker = mismatch_patterns.Tracker(per_column=3)
        for i in range(10):
            tracker.add(f'["K{i}"]', 'amount', str(i), str(i + 1))
        tracker.add('["K0"]', 'amount', '0', '1')
        result = tracker.result()['columns']['amount']
        self.assertTrue(result['partial'])
        self.assertEqual(sum(pattern[2] for pattern in result['patterns']) + result['more_cells'], 11)
        self.assertEqual(result['patterns'][0][:3], ['0', '1', 2])

    def test_long_values_compare_in_full_and_show_shortened(self):
        tracker = mismatch_patterns.Tracker()
        first, second = 'x' * 2000 + 'a', 'x' * 2000 + 'b'
        tracker.add('["1"]', 'note', first, 'y')
        tracker.add('["2"]', 'note', second, 'y')
        patterns = tracker.result()['columns']['note']['patterns']
        self.assertEqual(len(patterns), 2)
        self.assertTrue(patterns[0][0].endswith('… [truncated]'))

    def test_merge_in_key_order(self):
        first, second = mismatch_patterns.Tracker(), mismatch_patterns.Tracker()
        for i in range(3):
            first.add(f'["A{i}"]', 'status', 'open', 'void')
        for i in range(4):
            second.add(f'["B{i}"]', 'status', 'open', 'void')
        merged = mismatch_patterns.Tracker()
        merged.merge(json.loads(json.dumps(first.state())))
        merged.merge(json.loads(json.dumps(second.state())))
        pattern = merged.result()['columns']['status']['patterns'][0]
        self.assertEqual(pattern[2], 7)
        self.assertEqual(pattern[3], [['A0'], ['A1'], ['A2'], ['B0'], ['B1']])

    def test_listed_and_keyed_limits(self):
        tracker = mismatch_patterns.Tracker()
        for i in range(mismatch_patterns.LISTED + 5):
            tracker.add(f'["K{i}"]', 'id_text', f'a{i:05d}', 'b')
        result = tracker.result()['columns']['id_text']
        self.assertEqual(len(result['patterns']), mismatch_patterns.LISTED)
        self.assertEqual((result['more_patterns'], result['more_cells']), (5, 5))
        self.assertEqual([len(pattern) for pattern in result['patterns'][mismatch_patterns.KEYED - 1:mismatch_patterns.KEYED + 1]], [4, 3])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name, change in (('left.csv', False), ('right.csv', True)):
            with open(self.root / name, 'w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'status', 'amount', 'note'])
                for i in range(6000):
                    status = ('void' if i % 3 == 0 else 'open') if change else 'open'
                    amount = str(i + (1 if change and i % 7 == 0 else 0))
                    writer.writerow([f'K{i:05d}', status, amount, 'same'])

    def run_engine(self, engine, workers):
        out = self.root / f'out-{engine}'
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id', '--output', str(out),
                                    '--temp-dir', str(self.root), '--memory-mb', '64', '--engine', engine])
        args.parallel_workers = workers
        with patch.object(partitioned, 'RANGE_BYTES', 32 * 1024), contextlib.redirect_stdout(io.StringIO()):
            compare(args)
        return out

    def expected(self, out):
        counts, first = Counter(), {}
        with (out / 'differences.csv').open(newline='') as stream:
            for key, column, left, right in list(csv.reader(stream))[1:]:
                counts[column, left, right] += 1
                first.setdefault((column, left, right), json.loads(key))
        return counts, first

    def test_every_engine_saves_patterns_matching_differences(self):
        for engine, workers in (('auto', 1), ('partitioned', 3)):
            with self.subTest(engine=engine):
                out = self.run_engine(engine, workers)
                saved = mismatch_patterns.load(out)
                self.assertIsNotNone(saved)
                counts, first = self.expected(out)
                listed = {(column, left, right): count for column, item in saved['columns'].items() for left, right, count, *_ in item['patterns']}
                self.assertEqual(listed, dict(counts))
                status = saved['columns']['status']['patterns'][0]
                self.assertEqual(status[:3], ['open', 'void', 2000])
                self.assertEqual(status[3][0], first['status', 'open', 'void'])
                self.assertEqual(status[3], [[f'K{i:05d}'] for i in range(0, 15, 3)])


if __name__ == '__main__':
    unittest.main()
