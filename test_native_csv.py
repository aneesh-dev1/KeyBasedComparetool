"""Exact CSV semantics and restart boundaries for the packed row reader."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import compare as engine
from compare import compare, parser, unpack_values
from csv_input import PackedRows


class NativeCSVTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_csv(self, name, names, rows, encoding='utf-8-sig', delimiter=','):
        path = self.root / name
        with path.open('w', encoding=encoding, newline='') as stream:
            writer = csv.writer(stream, delimiter=delimiter)
            writer.writerow(names)
            writer.writerows(rows)
        return path

    def assert_reader_matches_csv(self, path, names, order, key_positions,
                                  encoding='utf-8-sig', delimiter=','):
        args = SimpleNamespace(encoding=encoding, delimiter=delimiter)
        with path.open(encoding=encoding, newline='') as stream:
            reader = csv.reader(stream, delimiter=delimiter, strict=True)
            next(reader)
            expected = [(tuple(row[i] for i in key_positions),
                         [row[i] for i in order]) for row in reader]
        with PackedRows(path, names, order, key_positions, args) as source:
            actual = [(key, unpack_values(payload)) for key, payload in source]
        self.assertEqual(actual, expected)

    def test_wide_plain_records_keep_exact_text_and_composite_keys(self):
        names = ['id'] + [f'value{i}' for i in range(1779)]
        rows = [
            ['0001'] + ['0.00', '-11.00', ' None ', '', '雪'] * 355 + ['a', 'b', 'c', 'tail'],
            ['0002'] + ['0', '-11', 'none', ' ', 'é'] * 355 + ['a', 'b', 'c', ''],
        ]
        path = self.write_csv('wide.csv', names, rows)
        self.assert_reader_matches_csv(path, names, list(range(len(names))), [0, 1779])

    def test_wide_tail_keys_keep_whitespace_zeroes_and_empty_fields(self):
        names = [f'value{i}' for i in range(1780)]
        rows = [
            ['plain'] * 1776 + ['left', ' 0001', '0002 ', '0003'],
            ['plain'] * 1776 + ['left', '001', '', ''],
            ['plain'] * 1776 + ['left', '', ' ', ' 0.00 '],
            ['plain'] * 1776 + ['', '', '', ''],
        ]
        path = self.write_csv('tail-keys.csv', names, rows)
        for key_positions in ([1779], [1778, 1779], [1779, 1778], [1777, 1779]):
            with self.subTest(key_positions=key_positions):
                self.assert_reader_matches_csv(path, names, list(range(len(names))), key_positions)

    def test_comparison_with_allowed_empty_tail_keys_matches_reference(self):
        names = [f'value{i}' for i in range(62)] + ['part', 'id']
        rows = [
            ['plain'] * 62 + [' 0001', '0001'],
            ['plain'] * 62 + [' 0002', '001'],
            ['plain'] * 62 + ['', ''],
            ['plain'] * 62 + [' ', ' '],
        ]
        left = self.write_csv('tail-left.csv', names, rows)
        right_rows = [row[:] for row in reversed(rows)]
        right_rows[1][0] = 'changed on the empty key'
        right = self.write_csv('tail-right.csv', names, right_rows)
        for keys in (['id'], ['part', 'id'], ['id', 'part']):
            outputs = []
            for reference in (True, False):
                output = self.root / f'tail-{"-".join(keys)}-{reference}'
                args = parser().parse_args([str(left), str(right), '--keys', *keys,
                    '--output', str(output), '--allow-empty-keys', '--sort-workers', '1'])
                args.reference_csv_reader = reference
                with contextlib.redirect_stdout(io.StringIO()):
                    result = compare(args)
                self.assertEqual(result['matched_keys'], 4)
                self.assertEqual(result['changed_cells'], 1)
                outputs.append(output)
            self.assert_same_reports(*outputs)

    def test_mixed_quotes_multiline_separators_bom_and_utf16(self):
        names = ['id', 'part', 'value', 'empty']
        rows = [
            ['001', 'A', 'plain', ''],
            ['002', 'B', 'quoted,comma', ''],
            ['003', 'line\r\n雪', '"quoted"', ''],
            ['004', 'C', 'embedded\x1fseparator\x1e', ''],
            ['005', 'D', ' 0.00 ', ''],
            ['006', 'E', '', ''],
        ]
        for encoding in ('utf-8-sig', 'utf-16'):
            for delimiter in (',', ';', '\t'):
                with self.subTest(encoding=encoding, delimiter=delimiter):
                    path = self.write_csv('mixed.csv', names, rows, encoding, delimiter)
                    for order in ([0, 1, 2, 3], [2, 0, 1], [0]):
                        self.assert_reader_matches_csv(path, names, order, [0, 1], encoding, delimiter)

    def test_line_endings_and_final_record_without_newline(self):
        for ending in ('\n', '\r', '\r\n'):
            with self.subTest(ending=repr(ending)):
                path = self.root / 'endings.csv'
                path.write_bytes(('id,v,empty' + ending + '1,first,' + ending + '2,last,').encode())
                self.assert_reader_matches_csv(path, ['id', 'v', 'empty'], [0, 1, 2], [0])
        path = self.root / 'single.csv'
        path.write_text('id\nfirst\n""\nlast', encoding='utf-8')
        self.assert_reader_matches_csv(path, ['id'], [0], [0])

    def test_invalid_record_and_field_limits_are_not_bypassed(self):
        args = SimpleNamespace(encoding='utf-8-sig', delimiter=',')
        for record in ('\n', '1,extra,unexpected\n', '1\n'):
            with self.subTest(record=repr(record)):
                path = self.root / 'invalid.csv'
                path.write_text('id,v\n' + record, encoding='utf-8')
                with PackedRows(path, ['id', 'v'], [0, 1], [0], args) as source:
                    with self.assertRaisesRegex(ValueError, 'expected 2 fields'):
                        list(source)
        for record in ('1,"unterminated\n', '1,"quoted"junk\n'):
            with self.subTest(record=repr(record)):
                path.write_text('id,v\n' + record, encoding='utf-8')
                with PackedRows(path, ['id', 'v'], [0, 1], [0], args) as source:
                    with self.assertRaises(csv.Error):
                        list(source)
        old_limit = csv.field_size_limit(16)
        try:
            for field in ('x' * 17, '"' + 'x' * 17 + '"'):
                path.write_text('id,v\n1,' + field + '\n', encoding='utf-8')
                with PackedRows(path, ['id', 'v'], [0, 1], [0], args) as source:
                    with self.assertRaisesRegex(csv.Error, 'field larger than field limit'):
                        list(source)
            # A record can exceed the field limit while every individual field is valid.
            path.write_text('id,v\n' + 'i' * 12 + ',' + 'v' * 12 + '\n', encoding='utf-8')
            self.assert_reader_matches_csv(path, ['id', 'v'], [0, 1], [0])
        finally:
            csv.field_size_limit(old_limit)

    def assert_same_reports(self, first, second):
        for filename in ('differences.csv', 'raw_differences.csv', 'matched_keys.csv',
                         'left_only.csv', 'right_only.csv', 'duplicate_keys.csv'):
            self.assertEqual((first / filename).read_bytes(),
                             (second / filename).read_bytes(), filename)

    def test_comparison_matches_reference_for_projection_exclusions_and_duplicates(self):
        for encoding in ('utf-8-sig', 'utf-16'):
            for reordered in (False, True):
                for policy in ('first', 'last'):
                    with self.subTest(encoding=encoding, reordered=reordered, policy=policy):
                        names = ['id', 'part', 'v', 'ignored']
                        left_rows = [['a', '01', '0.00', 'x'], ['b', '02', 'two\nlines', 'x'],
                                     ['c', '03', 'left only', 'x'], ['d', '04', 'first', 'x'],
                                     ['d', '04', 'last', 'x'], ['é', '05', 'a\x1fb', 'x']]
                        right_rows = [['d', '04', 'last', 'y'], ['é', '05', 'a\x1fb', 'y'],
                                      ['b', '02', 'two\nlines', 'y'], ['a', '01', '0', 'y'],
                                      ['e', '06', 'right only', 'y']]
                        left = self.write_csv('left.csv', names, left_rows, encoding)
                        order = [3, 2, 1, 0] if reordered else list(range(4))
                        right = self.write_csv('right.csv', [names[i] for i in order],
                                               [[row[i] for i in order] for row in right_rows], encoding)
                        label = f'{encoding}-{reordered}-{policy}'
                        outputs = []
                        for reference in (True, False):
                            output = self.root / f'{label}-{reference}'
                            options = [str(left), str(right), '--keys', 'id', 'part',
                                '--output', str(output), '--encoding', encoding, '--sort-workers', '1',
                                '--duplicate-policy', policy, '--read-batch-size', '2', '--fan-in', '2']
                            if reordered:
                                options.extend(['--ignore-columns', 'ignored'])
                            args = parser().parse_args(options)
                            args.reference_csv_reader = reference
                            with contextlib.redirect_stdout(io.StringIO()):
                                result = compare(args)
                            self.assertEqual(result['matched_keys'], 4)
                            self.assertGreater(result['changed_cells'], 0)
                            outputs.append(output)
                        self.assert_same_reports(*outputs)

    def test_checkpoint_resume_crosses_plain_and_multiline_record_boundaries(self):
        names = ['id', 'v']
        rows = [['a', 'plain'], ['b', 'still plain'], ['c', 'multi\nline'],
                ['d', 'Unicode 雪'], ['e', 'tail']]
        left = self.write_csv('resume-left.csv', names, rows, 'utf-16')
        right = self.write_csv('resume-right.csv', names,
                               [[key, value + (' changed' if key == 'c' else '')] for key, value in reversed(rows)],
                               'utf-16')
        args = parser().parse_args([str(left), str(right), '--keys', 'id', '--output',
                                   str(self.root / 'resumed'), '--encoding', 'utf-16',
                                   '--read-batch-size', '2', '--checkpoint-dir', str(self.root / 'saved'), '--engine', 'sort'])
        original_progress = engine.progress

        def interrupt_after_plain_batch(args, message, **values):
            original_progress(args, message, **values)
            if message == 'Read and partition complete':
                raise RuntimeError('simulated interruption')

        with contextlib.redirect_stdout(io.StringIO()):
            with patch('compare.progress', side_effect=interrupt_after_plain_batch):
                with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                    compare(args)
            self.assertTrue((self.root / 'saved/partitioned/manifest.json').exists())
            result = compare(args)
            reference_args = parser().parse_args([str(left), str(right), '--keys', 'id', '--output',
                                                 str(self.root / 'reference'), '--encoding', 'utf-16', '--engine', 'sort'])
            reference_args.reference_csv_reader = True
            compare(reference_args)
        self.assertEqual(result['matched_keys'], 5)
        self.assertEqual(result['changed_cells'], 1)
        self.assert_same_reports(self.root / 'resumed', self.root / 'reference')


if __name__ == '__main__':
    unittest.main()
