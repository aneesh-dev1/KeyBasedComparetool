"""Fingerprint lookup in the partitioned engine: the same bytes as copying full rows, for every text layout."""
import collections
import csv
import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import partitioned
from compare import compare, parser

OUTPUTS = ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only', 'duplicate_keys')


def write(path, header, rows, delimiter=',', quoting=csv.QUOTE_MINIMAL, newline='\n', encoding='utf-8'):
    with open(path, 'w', encoding=encoding, newline='') as stream:
        writer = csv.writer(stream, delimiter=delimiter, quoting=quoting, lineterminator=newline)
        writer.writerow(header)
        writer.writerows(rows)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def dataset(self, rows=500, seed=5, awkward=True):
        generator = random.Random(seed)
        header = ['id'] + [f'c{i}' for i in range(12)]
        left = []
        for i in range(rows):
            row = [f'K{i % (rows - 30):05d}'] + [f'value {i}-{j}' for j in range(12)]
            if awkward and i % 17 == 0:
                row[3] = generator.choice(['has, comma', 'two\nlines', 'quote "inside"', 'trailing\r\nbreak', '', 'ünïcode ✓'])
            left.append(row)
        right = [row[:] for row in left[20:]] + [[f'R{i:04d}'] + ['x'] * 12 for i in range(15)]
        for row in right:
            if generator.random() < .25:
                row[1 + generator.randrange(12)] = 'changed'
        generator.shuffle(right)
        return header, left, right

    def run_engine(self, name, engine='partitioned', fingerprint='on', extra=(), left='left.csv', right='right.csv', **settings):
        out = self.root / f'out-{name}-{engine}-{fingerprint}'
        argv = [str(self.root / left), str(self.root / right), '--keys', 'id', '--output', str(out),
                '--temp-dir', str(self.root), '--memory-mb', '64', *extra]
        args = parser().parse_args(argv + ([] if engine == 'auto' else ['--engine', engine]))
        args.parallel_workers, args.fingerprint = 3, fingerprint
        args.diagnostics_file = self.root / f'diag-{name}-{engine}-{fingerprint}.json'
        for key, value in settings.items():
            setattr(args, key, value)
        modes = []
        original = partitioned.fingerprint_choice

        def recorded(*arguments):
            choice = original(*arguments)
            modes.append(choice[0])
            return choice
        with patch.object(partitioned, 'RANGE_BYTES', 2048), patch.object(partitioned, 'fingerprint_choice', recorded):
            summary = compare(args)
        return out, summary, modes

    def assert_same(self, name, expected_mode, **options):
        # The in-memory engine is the reference, as in test_partitioned (the disk sort orders the duplicate audit differently).
        reference, reference_summary, _ = self.run_engine(name, engine='auto', fingerprint='off', **options)
        copied, _, _ = self.run_engine(name, fingerprint='off', **options)
        looked_up, summary, modes = self.run_engine(name, **options)
        self.assertEqual(modes, [expected_mode])
        for output in OUTPUTS:
            self.assertEqual((looked_up / f'{output}.csv').read_bytes(), (reference / f'{output}.csv').read_bytes(), output)
            self.assertEqual((looked_up / f'{output}.csv').read_bytes(), (copied / f'{output}.csv').read_bytes(), output)
        for field in ('matched_keys', 'equal_rows', 'changed_rows', 'changed_cells', 'left_only', 'right_only', 'left_rows', 'right_rows'):
            self.assertEqual(summary[field], reference_summary[field], field)
        diagnostics = [self.root / f'diag-{name}-{engine}-{mode}.json' for engine, mode in (('auto', 'off'), ('partitioned', 'on'))]
        if diagnostics[0].exists():
            self.assertEqual(json.loads(diagnostics[1].read_text())['samples'], json.loads(diagnostics[0].read_text())['samples'])
        return summary

    def test_same_layout_uses_raw_fingerprints(self):
        header, left, right = self.dataset()
        write(self.root / 'left.csv', header, left)
        write(self.root / 'right.csv', header, right)
        summary = self.assert_same('raw', 'raw')
        self.assertGreater(summary['changed_cells'], 0)
        self.assertGreater(summary['left_duplicate_keys'], 0)

    def test_last_occurrence_and_excluded_keys(self):
        header, left, right = self.dataset(seed=9)
        write(self.root / 'left.csv', header, left)
        write(self.root / 'right.csv', header, right)
        self.assert_same('last', 'raw', extra=['--duplicate-policy', 'last'], excluded_keys={b'["K00007"]', b'["K00100"]'})

    def test_different_layouts_use_canonical_fingerprints(self):
        header, left, right = self.dataset(seed=13)
        write(self.root / 'left.csv', header, left)
        # The other file has its columns in another order, a different delimiter and every field quoted.
        order = [0] + list(range(12, 0, -1))
        write(self.root / 'right.dat', [header[i] for i in order], [[row[i] for i in order] for row in right],
              delimiter='|', quoting=csv.QUOTE_ALL, newline='\r\n')
        self.assert_same('canonical', 'canonical', right='right.dat', dialects={'right': {'delimiter': '|'}})

    def test_ignored_column_and_mixed_quoting(self):
        header, left, right = self.dataset(seed=21, awkward=False)
        write(self.root / 'left.csv', header, left, quoting=csv.QUOTE_ALL)
        write(self.root / 'right.csv', header, right)
        self.assert_same('ignored', 'canonical', extra=['--ignore-columns', 'c4'])
        self.assert_same('quoted', 'raw')

    def test_lone_carriage_return_falls_back_to_the_text_reader(self):
        header, left, right = self.dataset(seed=3, awkward=False)
        write(self.root / 'left.csv', header, left)
        write(self.root / 'right.csv', header, right)
        with open(self.root / 'right.csv', 'ab') as stream:
            stream.write(b'R9999,a\rb,1,2,3,4,5,6,7,8,9,10,11\n')
        results = []
        for fingerprint in ('off', 'on'):
            with self.subTest(fingerprint=fingerprint):
                _, result, _ = self.run_engine('cr', fingerprint=fingerprint)
                results.append(result)
        for key in ('malformed_records', 'skipped_keys', 'matched_keys', 'changed_cells'):
            self.assertEqual(results[0][key], results[1][key], key)
        self.assertEqual(results[0]['malformed_records'], 2)

    def test_changed_source_is_detected_when_rows_are_read_again(self):
        path = self.root / 'one.csv'
        path.write_bytes(b'id,a,b\nK1,x,y\nK2,x,z\n')
        source = dict(path=str(path), names=['id', 'a', 'b'], order=[0, 1, 2], dialect=dict(delimiter=',', encoding='utf-8'))
        layout = partitioned._layout(source)
        record = b'K2,x,z'
        ref = partitioned.REF + b'U' + partitioned._fingerprint(record, b'U', layout, 'raw') + partitioned.REF_TAIL.pack(14, len(record))
        self.assertEqual(partitioned._fetch(layout, 'raw', [ref])[ref], b'\x00K2\x1fx\x1fz')
        path.write_bytes(b'id,a,b\nK1,x,y\nK2,x,Z\n')
        with self.assertRaisesRegex(ValueError, 'changed during the comparison'):
            partitioned._fetch(layout, 'raw', [ref])

    def test_automatic_choice(self):
        def sources(width, delimiters=(',', ','), orders=None):
            result = []
            for index, side in enumerate(('left', 'right')):
                path = self.root / f'{side}-{width}.csv'
                path.write_text('id,v\n' + ''.join(f'K{i},{"x" * width}\n' for i in range(200)))
                result.append(dict(kind='text', path=str(path), names=['id', 'v'], order=(orders or [[0, 1], [0, 1]])[index],
                                   ranges=[(5, path.stat().st_size)], start=5, dialect=dict(delimiter=delimiters[index], encoding='utf-8')))
            return result
        args = SimpleNamespace()
        self.assertIsNone(partitioned.fingerprint_mode(args, sources(40)))
        self.assertEqual(partitioned.fingerprint_mode(args, sources(900)), 'raw')
        self.assertEqual(partitioned.fingerprint_mode(args, sources(900, delimiters=(',', '|'))), 'canonical')
        self.assertEqual(partitioned.fingerprint_mode(args, sources(900, orders=[[0, 1], [1, 0]])), 'canonical')
        self.assertIsNone(partitioned.fingerprint_mode(SimpleNamespace(fingerprint='off'), sources(900)))
        self.assertEqual(partitioned.fingerprint_mode(SimpleNamespace(fingerprint='on'), sources(40)), 'raw')
        parquet = sources(900)
        parquet[1] = dict(kind='parquet', path='x.parquet')
        self.assertIsNone(partitioned.fingerprint_mode(args, parquet))
        # Windows-1252 is split by bytes like UTF-8; raw fingerprints need the same encoding on both sides.
        cp1252 = sources(900)
        cp1252[0]['dialect']['encoding'] = 'cp1252'
        self.assertEqual(partitioned.fingerprint_mode(args, cp1252), 'canonical')
        cp1252[1]['dialect']['encoding'] = 'cp1252'
        self.assertEqual(partitioned.fingerprint_mode(args, cp1252), 'raw')
        utf16 = sources(900)
        utf16[0]['dialect']['encoding'] = 'utf-16'
        self.assertEqual(partitioned.fingerprint_choice(args, utf16)[0], None)
        self.assertIn('utf-16', partitioned.fingerprint_choice(args, utf16)[1])

    def awkward_dataset(self, seed):
        """Accented text, quoted values with commas, a quoted key and escaped quotes, in many rows."""
        header, left, right = self.dataset(seed=seed, awkward=False)
        for rows in (left, right):
            for i, row in enumerate(rows):
                if i % 3 == 0:
                    row[2] = f'José, Müller {i}'          # quoted, Windows-1252 friendly
                if i % 11 == 0:
                    row[5] = 'say "hi"'                    # escaped quotes: parsed by the CSV parser
                if i % 29 == 0 and row[0].startswith('K'):
                    row[0] = row[0] + ', branch'           # a quoted key
        return header, left, right

    def test_windows_1252_files(self):
        header, left, right = self.awkward_dataset(31)
        write(self.root / 'left.csv', header, left, encoding='cp1252')
        write(self.root / 'right.csv', header, right, encoding='cp1252')
        dialects = {side: {'encoding': 'cp1252'} for side in ('left', 'right')}
        self.assert_same('cp1252', 'raw', dialects=dialects)
        # Against a UTF-8 copy the raw bytes differ, so fingerprints of the packed values are compared.
        write(self.root / 'right-utf8.csv', header, right)
        self.assert_same('mixed', 'canonical', right='right-utf8.csv', dialects={'left': {'encoding': 'cp1252'}})

    def test_undecodable_windows_1252_byte_gives_the_same_error(self):
        header, left, right = self.dataset(seed=4, awkward=False)
        write(self.root / 'left.csv', header, left, encoding='cp1252')
        write(self.root / 'right.csv', header, right, encoding='cp1252')
        with open(self.root / 'right.csv', 'ab') as stream:
            stream.write(b'R9998,bad \x81 byte,1,2,3,4,5,6,7,8,9,10,11\n')
        dialects = {side: {'encoding': 'cp1252'} for side in ('left', 'right')}
        errors = []
        for fingerprint in ('off', 'on'):
            with self.assertRaises(ValueError) as caught:
                self.run_engine('bad-byte', fingerprint=fingerprint, dialects=dialects)
            errors.append(type(caught.exception))
        self.assertEqual(errors[0], errors[1])

    def test_quoted_rows_are_references_not_copies(self):
        header, left, right = self.awkward_dataset(37)
        write(self.root / 'left.csv', header, left)
        write(self.root / 'right.csv', header, right)
        self.assert_same('quoted-refs', 'raw')
        # Read one file in this process and look at what reached the spill: references only, never full rows.
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id', '--output', str(self.root / 'x')])
        source = partitioned._describe(args, 'left', args.left, header, header)
        spill = self.root / 'inspect'
        spill.mkdir()
        task = dict(partitioned._range_tasks(source, dict(bounds=[], excluded=set(), allow_empty=False, cancel_file=None,
                                                           spill_dir=str(spill), batch_rows=1, memory_mb=1, native_batch_bytes=None,
                                                           fingerprint='raw', digest='sha256'))[0])
        result = partitioned._read_fingerprints(task)
        kinds = collections.Counter()
        for path, segments in result['spills']:
            for _, offset, length in segments:
                rows, _ = partitioned._load_partition([(path, offset, length)], 'first', False)
                kinds.update(entry[0][:2] for entry in rows.values())
        self.assertTrue(all(kind[:1] == partitioned.REF for kind in kinds), kinds)
        self.assertTrue({b'\x7fU', b'\x7fM', b'\x7fP'} <= set(kinds), kinds)


if __name__ == '__main__':
    unittest.main()
