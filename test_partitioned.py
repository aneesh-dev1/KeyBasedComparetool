"""The partitioned engine must produce byte-identical outputs to the in-memory and disk-sort engines."""
import csv
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import partitioned
from compare import compare, parser

OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')
VOLATILE = {'elapsed_seconds', 'execution_engine', 'comparison_engine'}


def write(path, header, rows, delimiter=','):
    with open(path, 'w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        if header:
            writer.writerow(header)
        writer.writerows(rows)


class PartitionedEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def dataset(self, rows=600, seed=11):
        random = __import__('random').Random(seed)
        header = ['id', 'name', 'note', 'amount', 'flag', 'updated']
        left = []
        for i in range(rows):
            note = random.choice(['plain', 'has, comma', 'two\nlines', 'quote "inside"', '', 'trailing\r\nbreak'])
            left.append([f'K{i % (rows - 40):05d}', f'name {i}', note, f'{random.randint(0, 9999) / 100:.2f}', random.choice(['Y', 'N', 'y']), f'2024-0{1 + i % 9}-1{i % 9}'])
        right = [row[:] for row in left[25:]] + [[f'R{i:04d}', 'extra', '', '1.00', 'N', '2024-01-01'] for i in range(30)]
        for row in right:
            if random.random() < .2:
                row[3] = f'{float(row[3]) + 0.001:.3f}'
            if random.random() < .1:
                row[4] = row[4].lower()
            if random.random() < .05:
                row[1] = row[1] + ' changed'
            row[5] = '2025-01-01'
        random.shuffle(right)
        return header, left, right

    def run_engine(self, engine, name, extra=(), left='left.csv', right='right.csv', **settings):
        out = self.root / f'out-{name}-{engine}'
        argv = [str(self.root / left), str(self.root / right), '--keys', 'id', '--output', str(out),
                '--temp-dir', str(self.root), '--memory-mb', '64', *extra]
        if engine != 'auto':
            argv += ['--engine', engine]
        args = parser().parse_args(argv)
        args.parallel_workers = 3
        args.diagnostics_file = self.root / f'diag-{name}-{engine}.json'
        for key, value in settings.items():
            setattr(args, key, value)
        with patch.object(partitioned, 'RANGE_BYTES', 300):
            summary = compare(args)
        return out, summary, args.diagnostics_file

    def assert_same(self, name, extra=(), **settings):
        reference, expected, expected_diag = self.run_engine('auto', name, extra, **settings)
        result, actual, actual_diag = self.run_engine('partitioned', name, extra, **settings)
        self.assertEqual(actual['execution_engine'], 'partitioned')
        for output in OUTPUTS:
            self.assertEqual((result / f'{output}.csv').read_bytes(), (reference / f'{output}.csv').read_bytes(), output)
        self.assertEqual({k: v for k, v in actual.items() if k not in VOLATILE},
                         {k: v for k, v in expected.items() if k not in VOLATILE})
        self.assertEqual(actual_diag.exists(), expected_diag.exists())
        if expected_diag.exists():
            self.assertEqual(json.loads(actual_diag.read_text()), json.loads(expected_diag.read_text()))
        return actual

    def test_quoted_multiline_duplicates_and_reordered_columns(self):
        header, left, right = self.dataset()
        write(self.root / 'left.csv', header, left)
        order = [0, 5, 3, 1, 4, 2]
        write(self.root / 'right.csv', [header[i] for i in order], [[row[i] for i in order] for row in right])
        for policy in ('first', 'last'):
            with self.subTest(policy=policy):
                summary = self.assert_same(policy, ['--duplicate-policy', policy])
                self.assertGreater(summary['left_duplicate_keys'], 0)
                self.assertGreater(summary['changed_cells'], 0)

    def test_ignored_columns_excluded_keys_rules_and_overrides(self):
        header, left, right = self.dataset(seed=3)
        write(self.root / 'left.csv', header, left)
        write(self.root / 'right.csv', header, right)
        rules = [{'column': 'flag', 'ignore_case': True}, {'column': 'amount', 'tolerance': '0.01'}]
        overrides = [{'column': 'name', 'left': 'name 7', 'right': 'name 7 changed'}]
        self.assert_same('scope', ['--ignore-columns', 'updated', '--ignore-keys', 'K00003, K00010',
                                   '--value-overrides', json.dumps(overrides)], comparison_rules=rules)

    def test_pipe_delimited_right_file_without_header(self):
        header, left, right = self.dataset(seed=5)
        write(self.root / 'left.dat', None, left, delimiter='|')
        write(self.root / 'right.dat', None, right, delimiter='|')
        out, summary, _ = self.run_engine('partitioned', 'dat', left='left.dat', right='right.dat',
                                          dialects={'left': dict(delimiter='|', header=False), 'right': dict(delimiter='|', header=False)},
                                          keys=['column1'])
        reference, expected, _ = self.run_engine('auto', 'dat', left='left.dat', right='right.dat',
                                                 dialects={'left': dict(delimiter='|', header=False), 'right': dict(delimiter='|', header=False)},
                                                 keys=['column1'])
        for output in OUTPUTS:
            self.assertEqual((out / f'{output}.csv').read_bytes(), (reference / f'{output}.csv').read_bytes(), output)
        self.assertEqual(summary['left_rows'], len(left))

    def test_blank_keys_are_skipped_and_audited_by_every_engine(self):
        rows = [[f'K{i:05d}', 'x'] for i in range(400)]
        rows[333][0] = ''
        write(self.root / 'left.csv', ['id', 'value'], rows)
        write(self.root / 'right.csv', ['id', 'value'], rows[:10] + [['', 'y']])
        results = {}
        for engine in ('auto', 'partitioned'):
            out, summary, _ = self.run_engine(engine, 'blank-' + engine)
            with (out / 'skipped_keys.csv').open(newline='') as stream:
                audit = list(csv.DictReader(stream))
            results[engine] = summary, out, audit
        for engine, (summary, out, audit) in results.items():
            with self.subTest(engine=engine):
                # Blank keys never match each other: each is its own unavailable key, on its own side.
                self.assertEqual((summary['unidentified_records'], summary['malformed_records'], summary['matched_keys'], summary['left_only']), (2, 2, 10, 389))
                self.assertEqual(sorted((row['side'], row['reason']) for row in audit), [('left', 'Blank key value in id'), ('right', 'Blank key value in id')])
        for output in OUTPUTS:
            self.assertEqual((results['auto'][1] / f'{output}.csv').read_bytes(), (results['partitioned'][1] / f'{output}.csv').read_bytes(), output)

    def test_quoted_fast_path_matches_the_csv_module(self):
        from csv_input import PackedRows
        from types import SimpleNamespace
        samples = ['"a","b,c","d"', '"","",""', '"a""b","c","d"', '"a",b,"c"', '"x","y"', '"one","two","three"']
        lines = '\n'.join(['h1,h2,h3'] + samples) + '\n'
        (self.root / 'q.csv').write_text(lines, encoding='utf-8')
        options = SimpleNamespace(delimiter=',', encoding='utf-8')
        from invalid_rows import invalid
        with PackedRows(self.root / 'q.csv', ['h1', 'h2', 'h3'], [0, 1, 2], [0], options) as rows:
            packed = list(rows)
        self.assertTrue(invalid(packed[4][1]))
        expected = list(csv.reader(samples))
        self.assertEqual([key[0] for key, _ in packed], [row[0] for row in expected])



if __name__ == '__main__':
    unittest.main()
