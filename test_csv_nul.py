"""NUL characters (mainframe low-values) are read exactly on every supported Python, including 3.9 and 3.10."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

import csv_nul  # noqa: F401
import partitioned
from compare import compare, parser

OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')
AWKWARD = ['a\x00b', '\x00', 'quoted, "\x00"\nline', '', '', '\x00', 'plain', '']


class CsvNulTests(unittest.TestCase):
    def test_round_trip(self):
        buffer = io.StringIO()
        csv.writer(buffer).writerows([AWKWARD, AWKWARD[::-1]])
        reader = csv.reader(io.StringIO(buffer.getvalue(), newline=''), strict=True)
        self.assertEqual(list(reader), [AWKWARD, AWKWARD[::-1]])
        self.assertEqual(reader.line_num, 4)
        self.assertEqual(list(csv.DictReader(io.StringIO('k,v\n1,x\x00y\n'))), [{'k': '1', 'v': 'x\x00y'}])
        self.assertEqual(list(csv.reader(['x\x00,y', 'p,q'], delimiter=',')), [['x\x00', 'y'], ['p', 'q']])

    def test_compare_with_nul_in_keys_and_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            header = ['id', 'note', 'flag'] + [f'w{i}' for i in range(40)]
            left = [[f'K{i:03d}' + ('\x00' if i % 7 == 0 else ''), AWKWARD[i % len(AWKWARD)], 'Y'] + ['x' * 12] * 40 for i in range(300)]
            right = [row[:] for row in left[10:]]
            for row in right[::5]:
                row[1] = row[1] + '\x00'
            for name, rows in (('left.csv', left), ('right.csv', right)):
                with open(root / name, 'w', encoding='utf-8', newline='') as stream:
                    csv.writer(stream).writerows([header] + rows)
            outputs = {}
            for engine, fingerprint in (('auto', 'auto'), ('sort', 'auto'), ('partitioned', 'off'), ('partitioned', 'on')):
                out = root / f'out-{engine}-{fingerprint}'
                argv = [str(root / 'left.csv'), str(root / 'right.csv'), '--keys', 'id', '--output', str(out),
                        '--temp-dir', str(root), '--memory-mb', '64', '--fingerprint', fingerprint]
                args = parser().parse_args(argv + ([] if engine == 'auto' else ['--engine', engine]))
                args.parallel_workers = 2
                summary = compare(args)
                self.assertEqual((summary['changed_cells'], summary['left_only']), (len(right[::5]), 10))
                outputs[(engine, fingerprint)] = {name: (out / f'{name}.csv').read_bytes() for name in OUTPUTS}
                with open(out / 'differences.csv', encoding='utf-8', newline='') as stream:
                    rows = list(csv.reader(stream))[1:]
                self.assertTrue(rows and all(row[3] == row[2] + '\x00' for row in rows))
            reference = outputs[('auto', 'auto')]
            for key, value in outputs.items():
                for name in ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only'):
                    self.assertEqual(value[name], reference[name], (key, name))


if __name__ == '__main__':
    unittest.main()
