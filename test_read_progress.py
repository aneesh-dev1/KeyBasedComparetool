"""The read phase reports how much of the input is read, as a rising percentage that ends at 100%, in every engine."""
import contextlib
import csv
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import partitioned
from compare import ReadProgress, compare, parser

LINE = re.compile(r'(Reading files|Reading and indexing files) \| (\d+)%')


class ReadProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('left.csv', 'right.csv'):
            with open(self.root / name, 'w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['id', 'a', 'b'])
                writer.writerows([[f'K{i:06d}', 'x' * 20, str(i)] for i in range(30000)])

    def percentages(self, engine, workers=2):
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id',
                                    '--output', str(self.root / f'out-{engine}-{workers}'), '--temp-dir', str(self.root),
                                    '--memory-mb', '64'] + ([] if engine == 'auto' else ['--engine', engine]))
        args.parallel_workers = workers
        log = io.StringIO()
        with patch.object(partitioned, 'RANGE_BYTES', 64 * 1024), contextlib.redirect_stdout(log):
            compare(args)
        return [int(match.group(2)) for match in LINE.finditer(log.getvalue())]

    def test_every_engine_reports_a_rising_percentage(self):
        for engine, workers in (('partitioned', 2), ('auto', 1), ('sort', 1)):
            with self.subTest(engine=engine):
                values = self.percentages(engine, workers)
                self.assertGreaterEqual(len(values), 3, values)
                self.assertEqual(values, sorted(values))
                self.assertEqual(values[-1], 100)

    def test_lines_are_throttled(self):
        lines = []
        with patch('compare.progress', lambda args, phase, **values: lines.append(values['percent'])):
            reading = ReadProgress(None, 1000)
            for done in range(0, 1000, 7):
                reading.update(done)
            reading.update(1000, final=True)
        self.assertEqual(lines, sorted(lines))
        self.assertLessEqual(len(lines), 22)
        self.assertEqual(lines[-1], 100)


if __name__ == '__main__':
    unittest.main()
