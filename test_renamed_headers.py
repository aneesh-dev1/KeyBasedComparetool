"""Renamed column headers (e.g. permId in one file, perm_id in the other) work for every format pair and engine.

The two files name every column differently. The header layout renames them, and the composite key, ignored
column, comparison rule and value override all use the new names.
"""
import csv
import importlib.util
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import partitioned
import test_ui
from compare import compare, parser
from test_excel_input import workbook

ARROW = importlib.util.find_spec('pyarrow') is not None
OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')
LEFT = (['permId', 'acct_no', 'amt', 'status', 'remark'],
        [['P1', 'A', '10', 'open', 'x'], ['P2', 'A', '20', 'open', 'y'], ['P3', 'B', '30', 'closed', 'z'], ['P5', 'C', '50', 'open', 'q']])
RIGHT = (['perm_id', 'account', 'amount', 'status', 'note'],
         [['P3', 'B', '30.2', 'open', 'zz'], ['P1', 'A', '10.4', 'OPEN', 'x'], ['P4', 'A', '40', 'open', 'w'], ['P5', 'C', '51', 'open', 'q']])
NAMES = ['perm_id', 'account', 'amount', 'status', 'note']
LAYOUT = {'left': NAMES, 'right': NAMES}
KEYS = ['perm_id', 'account']
RULES = [{'column': 'amount', 'tolerance': '0.5'}]
OVERRIDES = [{'column': 'status', 'left': 'open', 'right': 'OPEN'}]
# P1 matches through the tolerance and the override; P3 status and P5 amount differ; P2 and P4 are one-sided.
EXPECTED = dict(matched_keys=3, equal_rows=1, changed_cells=2, left_only=1, right_only=1)


def write(path, header, rows):
    suffix = path.suffix
    if suffix == '.parquet':
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.table({name: [row[i] for row in rows] for i, name in enumerate(header)}), path, row_group_size=2)
    elif suffix == '.xlsx':
        cell = lambda r, c, v: f'<c r="{"ABCDE"[c]}{r}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
        sheet = ''.join(f'<row r="{r}">' + ''.join(cell(r, c, v) for c, v in enumerate(row)) + '</row>'
                        for r, row in enumerate([header] + rows, 1))
        path.write_bytes(workbook(sheet))
    else:
        with open(path, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='|' if suffix == '.dat' else ',')
            writer.writerow(header)
            writer.writerows(rows)


def picked(summary):
    return {name: summary[name] for name in EXPECTED}


class RenamedHeaderEngineTests(unittest.TestCase):
    """Every ordered pair of delimited text and Parquet, through every engine, gives the same bytes."""

    def test_every_format_pair_and_engine(self):
        formats = ['.csv', '.dat'] + (['.parquet'] if ARROW else [])
        for left_suffix, right_suffix in itertools.product(formats, repeat=2):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                left, right = root / ('left' + left_suffix), root / ('right' + right_suffix)
                write(left, *LEFT)
                write(right, *RIGHT)
                reference = None
                for engine in ('auto', 'sort', 'partitioned'):
                    with self.subTest(left=left_suffix, right=right_suffix, engine=engine):
                        out = root / f'out-{engine}'
                        argv = [str(left), str(right), '--keys', *KEYS, '--output', str(out), '--temp-dir', str(root),
                                '--memory-mb', '64', '--ignore-columns', 'note', '--value-overrides', json.dumps(OVERRIDES)]
                        if engine != 'auto':
                            argv += ['--engine', engine]
                        args = parser().parse_args(argv)
                        args.column_headers, args.comparison_rules, args.parallel_workers = LAYOUT, RULES, 2
                        args.dialects = {side: {'delimiter': '|'} for side, path in (('left', left), ('right', right)) if path.suffix == '.dat'}
                        with patch.object(partitioned, 'RANGE_BYTES', 16):
                            summary = compare(args)
                        self.assertEqual(picked(summary), EXPECTED)
                        outputs = {name: (out / f'{name}.csv').read_bytes() for name in OUTPUTS}
                        if reference is None:
                            reference = outputs
                        self.assertEqual(outputs, reference)


class RenamedHeaderServerTests(unittest.TestCase):
    """The web flow: upload, choose worksheets, rename headers in Files & headers, then compare."""
    setUpClass = classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request = test_ui.ServerTests.request
    wait = test_ui.ServerTests.wait

    def run_pair(self, left_suffix, right_suffix):
        folder = Path(self.temp.name) / f'pair-{left_suffix[1:]}-{right_suffix[1:]}'
        folder.mkdir()
        data = {}
        for side, suffix, content in (('left', left_suffix, LEFT), ('right', right_suffix, RIGHT)):
            write(folder / (side + suffix), *content)
            data[side] = (side + suffix, (folder / (side + suffix)).read_bytes())
        job = self.request('/api/jobs', {'delimiter': 'auto', 'encoding': 'auto', 'header': 'auto',
                                         'files': {side: {'name': name, 'size': len(content)} for side, (name, content) in data.items()}})
        path = '/api/jobs/' + job['id']
        for side, (_, content) in data.items():
            self.request(path + f'/files/{side}?offset=0', content, 'PUT')
        job = self.request(path + '/finalize', {})
        if job['state'] == 'selecting_sheets':
            self.request(path + '/select-sheets', {side: 'Data' for side in ('left', 'right') if data[side][0].endswith('.xlsx')})
            job = self.wait(path, lambda j: j['state'] in ('ready', 'error'))
        self.assertEqual(job['state'], 'ready', job.get('error'))
        self.assertEqual(self.request(path + '/headers', {'column_headers': LAYOUT})['columns'], NAMES)
        self.request(path + '/start', {'keys': KEYS, 'memory_mb': 64, 'ignore_columns': ['note'],
                                       'comparison_rules': RULES, 'value_overrides': OVERRIDES})
        result = self.wait(path, lambda j: j['state'] in ('complete', 'error'))
        self.assertEqual(result['state'], 'complete', result.get('error'))
        self.assertEqual(picked(result['summary']), EXPECTED)

    def test_every_upload_pair(self):
        pairs = [('.csv', '.xlsx'), ('.xlsx', '.csv'), ('.xlsx', '.xlsx'), ('.dat', '.xlsx'), ('.csv', '.dat')]
        if ARROW:
            pairs += [('.parquet', '.parquet'), ('.parquet', '.xlsx'), ('.xlsx', '.parquet'), ('.dat', '.parquet'), ('.parquet', '.csv')]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.run_pair(left, right)


if __name__ == '__main__':
    unittest.main()
