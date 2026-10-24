"""An interrupted parallel comparison resumes from the key ranges that already finished, with identical outputs."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import partitioned
from compare import compare, parser

OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')


class CountingPool(partitioned.ProcessPoolExecutor):
    submitted = []

    def submit(self, function, *args, **kwargs):
        CountingPool.submitted.append(function.__name__)
        return super().submit(function, *args, **kwargs)


class PartitionedResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name, offset in (('left.csv', 0), ('right.csv', 1)):
            with open(self.root / name, 'w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'a', 'b'])
                writer.writerows([[f'K{i:05d}', str(i + (offset and i % 9 == 0)), 'x,y' if i % 4 else 'line\nbreak']
                                  for i in range(offset * 50, 4000 + offset * 50)])

    def args(self, name, checkpoints=None):
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id',
                                    '--output', str(self.root / name), '--temp-dir', str(self.root),
                                    '--memory-mb', '64', '--engine', 'partitioned'])
        args.parallel_workers = 3
        args.cancel_file = self.root / 'cancel'
        if checkpoints:
            args.checkpoint_dir = self.root / checkpoints
        return args

    def run_compare(self, args, stop_after=None):
        original = partitioned._wait_all

        def interrupted(pool, futures, run_args, on_done):
            def counted(result):
                on_done(result)
                counted.calls += 1
                if counted.calls == stop_after:
                    Path(run_args.cancel_file).write_text('cancel')
            counted.calls = 0
            return original(pool, futures, run_args, counted if on_done.__name__ == 'compared' else on_done)

        CountingPool.submitted = []
        with patch.object(partitioned, 'RANGE_BYTES', 8192), patch.object(partitioned, 'ProcessPoolExecutor', CountingPool), \
                patch.object(partitioned, '_wait_all', interrupted if stop_after else original):
            return compare(args)

    def test_resume_reuses_finished_key_ranges(self):
        plain = self.run_compare(self.args('plain'))
        args = self.args('resumed', 'checkpoints')
        with self.assertRaises(Exception):
            self.run_compare(args, stop_after=3)
        work = self.root / 'checkpoints' / 'partitioned'
        self.assertTrue((work / 'manifest.json').exists(), 'the work folder is kept after an interruption')
        first = list(CountingPool.submitted)
        (self.root / 'cancel').unlink()
        summary = self.run_compare(self.args('resumed', 'checkpoints'))
        # Every read range and at least the joined ranges recorded before the stop are reused.
        self.assertNotIn('_partition_range', CountingPool.submitted)
        self.assertLess(CountingPool.submitted.count('_join_partition'), first.count('_join_partition'))
        self.assertFalse(work.exists(), 'the work folder is removed once the comparison succeeds')
        for name in ('matched_keys', 'changed_cells', 'left_only', 'right_only', 'left_rows', 'right_rows'):
            self.assertEqual(summary[name], plain[name], name)
        for name in OUTPUTS:
            self.assertEqual((self.root / 'resumed' / f'{name}.csv').read_bytes(), (self.root / 'plain' / f'{name}.csv').read_bytes(), name)

    def test_interrupted_reads_resume(self):
        args = self.args('reads', 'checkpoints')

        def stop(pool, futures, run_args, on_done):
            Path(run_args.cancel_file).write_text('cancel')
            return original(pool, futures, run_args, on_done)

        original = partitioned._wait_all
        CountingPool.submitted = []
        with patch.object(partitioned, 'RANGE_BYTES', 8192), patch.object(partitioned, 'ProcessPoolExecutor', CountingPool), \
                patch.object(partitioned, '_wait_all', stop), self.assertRaises(Exception):
            compare(args)
        (self.root / 'cancel').unlink()
        plain = self.run_compare(self.args('plain'))
        summary = self.run_compare(self.args('reads', 'checkpoints'))
        self.assertEqual(summary['changed_cells'], plain['changed_cells'])
        for name in OUTPUTS:
            self.assertEqual((self.root / 'reads' / f'{name}.csv').read_bytes(), (self.root / 'plain' / f'{name}.csv').read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
