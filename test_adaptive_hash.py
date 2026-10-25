"""Independent exact-value oracle plus overflow, collision and cancellation regressions."""
import contextlib
import csv
import io
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from compare import compare, parser, encode, pack_values
from hash_join import _packed_source
from hash_spill import FRAME, loaded_buckets, records
from task_control import Cancelled


class AdaptiveHashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, side, rows):
        path = self.root / (side + '.csv')
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'value', 'other'])
            writer.writerows(rows)
        return path

    def read_report(self, out, name):
        with (out / (name + '.csv')).open(newline='', encoding='utf-8') as stream:
            reader = csv.reader(stream)
            next(reader)
            return Counter(map(tuple, reader))

    def test_overflow_continues_each_input_once_and_keeps_exact_duplicate_semantics(self):
        rng = random.Random(271)
        left = [[str(i), 'x' * 200 + str(i), 'left' if i % 3 else 'same'] for i in range(200)]
        right = [[str(i), 'x' * 200 + str(i + (i % 2)), 'same'] for i in range(20, 230)]
        rng.shuffle(left); rng.shuffle(right)
        # Duplicates before and after the resident budget is exhausted.
        left = [['z', 'first', 'same'], ['z', 'middle', 'same']] + left + [['z', 'last', 'same'], ['1', 'replacement', 'same']]
        right += [['z', 'right', 'same'], ['30', 'replacement', 'same']]
        self.write('left', left); self.write('right', right)
        for policy in ('first', 'last'):
            args = parser().parse_args([str(self.root/'left.csv'), str(self.root/'right.csv'), '--keys', 'id',
                '--output', str(self.root/policy), '--duplicate-policy', policy])
            args.memory_mb = .02  # force overflow while the left reader is still open
            args.temp_dir = str(self.root)
            args.diagnostics_file = self.root / (policy + '-diagnostics.json')
            opened = []
            def source(*values, **kwargs):
                opened.append(Path(values[0]).name)
                return _packed_source(*values, **kwargs)
            with patch('hash_join._packed_source', side_effect=source), \
                    patch('compare.sort_inputs', side_effect=AssertionError('No sorting')), \
                    patch('compare.write_run', side_effect=AssertionError('No sorted runs')), \
                    contextlib.redirect_stdout(io.StringIO()):
                actual = compare(args)
            self.assertEqual(opened, ['left.csv', 'right.csv'])
            selected = []
            for source_rows in (left, right):
                rows = {}
                for row in source_rows:
                    if policy == 'last' or row[0] not in rows:
                        rows[row[0]] = row
                selected.append(rows)
            a, b = selected
            expected = Counter()
            for key in a.keys() & b.keys():
                for i, name in enumerate(('id', 'value', 'other')):
                    if a[key][i] != b[key][i]:
                        expected[(encode([key]).decode(), name, a[key][i], b[key][i])] += 1
            out = Path(args.output)
            self.assertEqual(self.read_report(out, 'differences'), expected)
            self.assertEqual(self.read_report(out, 'left_only'), Counter((k,) for k in a.keys() - b.keys()))
            self.assertEqual(self.read_report(out, 'right_only'), Counter((k,) for k in b.keys() - a.keys()))
            self.assertEqual(actual['changed_cells'], sum(expected.values()))
            self.assertEqual(actual['left_duplicate_rows_skipped'], 3)
            self.assertEqual(actual['right_duplicate_rows_skipped'], 1)
            diagnostic = json.loads(args.diagnostics_file.read_text())
            self.assertEqual(diagnostic['key'], ['1'])
            self.assertEqual(diagnostic['count'], 2)
            self.assertFalse(list(self.root.glob('hash-overflow-*')))

    def test_all_initial_hashes_collide_but_full_keys_remain_distinct(self):
        from hash_spill import bucket
        paths = []
        for side in ('left', 'right'):
            path = self.root / side
            with path.open('wb') as stream:
                for i in range(160):
                    key, value = encode([str(i)]), pack_values([str(i), side])
                    stream.write(FRAME.pack(len(key), len(value), 1) + key + value)
            paths.append([(str(path), 0, path.stat().st_size)])
        # Simulate a pathological first hash level; the depth-specific next level must recover.
        def collisions(key, count, depth=0):
            return 0 if depth == 0 else bucket(key, count, depth)
        seen = set()
        with patch('hash_spill.bucket', side_effect=collisions):
            for a, b, samples in loaded_buckets(*paths, 'first', True, 6000, self.root):
                self.assertEqual(a.keys(), b.keys())
                self.assertFalse(seen.intersection(a))
                seen.update(a)
                a.clear(); b.clear()
        self.assertEqual(len(seen), 160)
        self.assertFalse(list(self.root.glob('hash-buckets-*')))

    def test_legacy_engine_option_uses_hashes_without_sort_helpers(self):
        self.write('left', [['b', 'old', ''], ['a', 'same', '']])
        self.write('right', [['a', 'same', ''], ['b', 'new', '']])
        args = parser().parse_args([str(self.root/'left.csv'), str(self.root/'right.csv'),
            '--keys', 'id', '--output', str(self.root/'out'), '--engine', 'sort', '--workers', '1'])
        with patch('compare.sort_inputs', side_effect=AssertionError('No sorting')), \
                patch('compare.write_run', side_effect=AssertionError('No sorted runs')), \
                contextlib.redirect_stdout(io.StringIO()):
            summary = compare(args)
        self.assertEqual(summary['execution_engine'], 'partitioned')
        self.assertEqual(summary['equal_rows'], 1)
        self.assertEqual(summary['changed_cells'], 1)

    def test_truncated_or_invalid_frames_are_rejected(self):
        for body in (b'bad', FRAME.pack(20, 4, 1) + b'short', FRAME.pack(1, 1, 0) + b'ab'):
            path = self.root / 'broken'
            path.write_bytes(body)
            with self.assertRaises(ValueError):
                list(records([(str(path), 0, len(body))]))

    def test_evidence_checksum_covers_encoded_buffered_output(self):
        import hashlib
        from hash_spill import evidence_stream
        path = self.root / 'evidence.csv'
        stream, sink = evidence_stream(path, True)
        with stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'value'])
            writer.writerows([[str(i), '雪,"quoted"\n' * 100] for i in range(1000)])
        data = path.read_bytes()
        self.assertEqual(sink.size, len(data))
        self.assertEqual(sink.digest.hexdigest(), hashlib.sha256(data).hexdigest())
        with path.open(newline='', encoding='utf-8') as stream:
            self.assertEqual(len(list(csv.reader(stream))), 1001)

    def test_spill_cancellation_and_oversized_row_are_explicit(self):
        path = self.root / 'rows'
        key, value = encode(['id']), b'x' * 10000
        path.write_bytes(FRAME.pack(len(key), len(value), 1) + key + value)
        segments = [(str(path), 0, path.stat().st_size)]
        cancel = self.root / 'cancel'; cancel.touch()
        with self.assertRaises(Cancelled):
            list(loaded_buckets(segments, [], 'first', False, 100, self.root, cancel))
        with self.assertRaisesRegex(ValueError, 'exceeds the available memory'):
            list(loaded_buckets(segments, [], 'first', False, 100, self.root))
        self.assertFalse(list(self.root.glob('hash-buckets-*')))


if __name__ == '__main__':
    unittest.main()
