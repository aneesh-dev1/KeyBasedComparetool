"""Typed comparison rules: values stay exact text; a type only decides when two texts are equal."""
import unittest

from comparison_rules import equivalent, validate_rules


def rule(**settings):
    return validate_rules([dict(column='v', **settings)], ['id', 'v'], ['id'], [])[0]


class TypedRuleTests(unittest.TestCase):
    def test_number(self):
        r = rule(type='number')
        for a, b in [('1', '1.0'), ('1.00', '1e0'), ('-0', '0'), (' 5', '5.000')]:
            self.assertTrue(equivalent(a, b, r), (a, b))
        for a, b in [('1', '1.0001'), ('1,000', '1000'), ('abc', 'abc '), ('NaN', 'nan'), ('1', '')]:
            self.assertFalse(equivalent(a, b, r), (a, b))

    def test_decimal_scale(self):
        r = rule(type='decimal', scale=2)
        self.assertTrue(equivalent('12.345', '12.35', r))
        self.assertTrue(equivalent('12.3', '12.30000', r))
        self.assertFalse(equivalent('12.344', '12.35', r))
        self.assertTrue(equivalent('2.5', '3', rule(type='decimal', scale=0)))

    def test_date(self):
        r = rule(type='date')
        self.assertTrue(equivalent('2024-01-05', '20240105', r))
        self.assertTrue(equivalent('2024-01-05T13:00:00', '2024-01-05', r))
        self.assertFalse(equivalent('2024-01-05', '2024-01-06', r))
        self.assertFalse(equivalent('05/01/2024', '2024-01-05', r))

    def test_timestamp_with_time_zones(self):
        r = rule(type='timestamp')
        self.assertTrue(equivalent('2024-01-05T10:00:00+05:30', '2024-01-05 04:30:00Z', r))
        self.assertTrue(equivalent('2024-01-05 04:30:00', '2024-01-05T04:30:00+00:00', r))
        self.assertFalse(equivalent('2024-01-05T04:30:00.000001Z', '2024-01-05T04:30:00Z', r))
        local = rule(type='timestamp', timezone='Asia/Kolkata')
        self.assertTrue(equivalent('2024-01-05 10:00:00', '2024-01-05T04:30:00Z', local))

    def test_trim_and_case_still_apply_first_and_nulls_stay_exact(self):
        self.assertTrue(equivalent(' 1 ', '1.0', rule(type='number', trim=True)))
        self.assertFalse(equivalent(None, '1', rule(type='number')))
        self.assertTrue(equivalent(None, None, rule(type='number')))

    def test_validation(self):
        for settings in [dict(type='money'), dict(type='decimal'), dict(type='decimal', scale=19), dict(type='number', tolerance='0.1'),
                         dict(type='timestamp', timezone='Mars/Base'), dict(type='date', left_date_format='%Y', right_date_format='%Y')]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                rule(**settings)


class TypedParquetEngineTests(unittest.TestCase):
    """Parquet int64 vs double, timestamps vs ISO text and dates: mismatches as text, equal with typed rules."""

    @unittest.skipUnless(__import__('importlib').util.find_spec('pyarrow'), 'pyarrow is not installed')
    def test_types_across_engines(self):
        import datetime, tempfile
        from pathlib import Path
        from unittest.mock import patch
        import pyarrow as pa, pyarrow.parquet as pq
        import partitioned
        from compare import compare, parser
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            moment = datetime.datetime(2024, 1, 5, 4, 30, tzinfo=datetime.timezone.utc)
            pq.write_table(pa.table({'id': ['a', 'b'], 'amount': pa.array([1, 25], pa.int64()),
                                     'at': pa.array([moment, moment], pa.timestamp('us', tz='UTC')), 'day': ['2024-01-05', '20240106']}), root / 'left.parquet')
            pq.write_table(pa.table({'id': ['a', 'b'], 'amount': pa.array([1.0, 25.0], pa.float64()),
                                     'at': ['2024-01-05T10:00:00+05:30', '2024-01-05 04:30:00Z'], 'day': ['2024-01-05T00:00:00', '2024-01-06']}), root / 'right.parquet')
            rules = [dict(column='amount', type='number'), dict(column='at', type='timestamp'), dict(column='day', type='date')]
            for engine in ('auto', 'sort', 'partitioned'):
                for typed in (False, True):
                    with self.subTest(engine=engine, typed=typed):
                        args = parser().parse_args([str(root / 'left.parquet'), str(root / 'right.parquet'), '--keys', 'id', '--output', str(root / f'{engine}-{typed}'),
                                                    '--temp-dir', str(root), '--memory-mb', '64'] + ([] if engine == 'auto' else ['--engine', engine]))
                        args.comparison_rules, args.parallel_workers = (rules if typed else []), 2
                        with patch.object(partitioned, 'RANGE_BYTES', 64):
                            summary = compare(args)
                        self.assertEqual(summary['changed_cells'], 0 if typed else 6)
                        if typed:
                            self.assertEqual(summary['rule_equivalent_cells'], 6)


if __name__ == '__main__':
    unittest.main()
