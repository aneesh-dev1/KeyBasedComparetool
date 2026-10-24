"""Live snapshots report the same counts as the final summary and never change the report files."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live
import partitioned
from compare import compare, parser

OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')


class LiveSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        with open(self.root / 'left.csv', 'w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'a', 'b'])
            writer.writerows([[f'K{i:05d}', str(i), 'x' if i % 7 else 'y'] for i in range(3000)])
        with open(self.root / 'right.csv', 'w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'a', 'b'])
            writer.writerows([[f'K{i:05d}', str(i + (i % 5 == 0)), 'x'] for i in range(200, 3200)])

    def run_engine(self, engine, with_live):
        name = f'{engine}-{"live" if with_live else "plain"}'
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id',
                                    '--output', str(self.root / name), '--temp-dir', str(self.root), '--memory-mb', '64']
                                   + ([] if engine == 'auto' else ['--engine', engine]))
        args.parallel_workers = 2
        if with_live:
            args.live_file = self.root / f'{name}.json'
        with patch.object(partitioned, 'RANGE_BYTES', 4096):
            summary = compare(args)
        return self.root / name, summary, getattr(args, 'live_file', None)

    def test_counts_match_and_outputs_are_unchanged(self):
        for engine in ('auto', 'sort', 'partitioned'):
            with self.subTest(engine=engine):
                plain, _, _ = self.run_engine(engine, False)
                out, summary, path = self.run_engine(engine, True)
                snapshot = live.read(path)
                self.assertIsNotNone(snapshot)
                for name in live.COUNTS:
                    self.assertEqual(snapshot[name], summary[name], name)
                self.assertEqual(snapshot['changed_cells_by_column'], {k: v for k, v in summary['changed_cells_by_column'].items() if v})
                differences = {tuple(row) for row in csv.reader((out / 'differences.csv').read_text(encoding='utf-8').splitlines())}
                self.assertTrue(snapshot['samples'])
                for sample in snapshot['samples']:
                    self.assertIn(tuple(sample), differences)
                for name in OUTPUTS:
                    self.assertEqual((out / f'{name}.csv').read_bytes(), (plain / f'{name}.csv').read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
