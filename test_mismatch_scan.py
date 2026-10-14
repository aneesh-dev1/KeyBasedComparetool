import random
import unittest
from unittest.mock import patch

from compare import pack_values
from mismatch_scan import differences_for_pairs, iter_differences


class MismatchScanTests(unittest.TestCase):
    def test_exact_strings_nulls_and_embedded_delimiters(self):
        left = ['001', '', None, 'a\x1fb', 'line\ntext', 'Ü', '0', '-11', 'None']
        right = ['1', None, '', 'a\x1fb', 'line\r\ntext', 'ü', '0.00', '-11.00', 'none']
        expected = [(i, a, b) for i, (a, b) in enumerate(zip(left, right)) if a != b]
        self.assertEqual(list(iter_differences(pack_values(left), pack_values(right))), expected)
        self.assertEqual(list(iter_differences(pack_values(left), pack_values(left))), [])

    def test_null_bitmap_and_json_have_identical_meaning(self):
        values = ['a', None, '', 'Ü']
        bitmap = b'\x020100\x1ea\x1f\x1f\x1f' + 'Ü'.encode()
        self.assertEqual(list(iter_differences(bitmap, pack_values(values))), [])
        self.assertEqual(list(iter_differences(bitmap, pack_values(['b', '', '', 'Ü']))), [(0, 'a', 'b'), (1, None, '')])

    def test_sparse_dense_and_missing_rows_preserve_order(self):
        rng = random.Random(45)
        pairs, expected = [], []
        for row in range(180):
            left = [f'{row}-{column}' for column in range(100)]
            density = .01 if row < 60 else .6 if row < 120 else 0
            right = [value + 'x' if rng.random() < density else value for value in left]
            a, b = (str(row).encode(), pack_values(left)), (str(row).encode(), pack_values(right))
            if row == 10:
                a = None
            if row == 99:
                b = None
            pairs.append((a, b))
            expected.append((a, b, [] if a is None or b is None else [(i, x, y) for i, (x, y) in enumerate(zip(left, right)) if x != y]))
        for native in (True, False):
            actual = [(a, b, list(diff)) for a, b, diff in differences_for_pairs(iter(pairs), 100, use_native=native, max_cells=3000)]
            self.assertEqual(actual, expected)

    def test_dense_chunk_does_not_materialize_native_cells(self):
        pairs = [((str(row).encode(), pack_values(['a'] * 100)), (str(row).encode(), pack_values(['b'] * 60 + ['a'] * 40))) for row in range(40)]
        with patch('mismatch_scan._native_differences', side_effect=AssertionError('dense native materialization')):
            self.assertEqual(sum(len(list(diff)) for _, _, diff in differences_for_pairs(pairs, 100)), 2400)

    def test_sparse_native_chunk_materializes_only_differences(self):
        try:
            import pyarrow
        except ImportError:
            self.skipTest('PyArrow is optional')
        from mismatch_scan import _native_differences
        pairs = [((str(row).encode(), pack_values(['a'] * 100)), (str(row).encode(), pack_values(['b'] + ['a'] * 99))) for row in range(40)]
        with patch('mismatch_scan._native_differences', wraps=_native_differences) as native:
            rows = [(a, b, list(diff)) for a, b, diff in differences_for_pairs(pairs, 100)]
        self.assertTrue(native.called)
        self.assertTrue(all(diff == [(0, 'a', 'b')] for _, _, diff in rows))

    def test_sparse_sample_with_dense_remaining_rows_loses_no_differences(self):
        # Density sampling may miss a dense section. The native path must still
        # emit every difference while obeying its cell/byte chunk limits.
        sampled = {0, 13, 26, 39}
        pairs, expected = [], []
        for row in range(40):
            left = [''] + ['\u00dc', '0001', 'line\ntext', 'nul\0text'] * 25
            right = left.copy()
            for column in range(1 if row in sampled else 70):
                right[column] += 'changed'
            key = str(row).encode()
            pairs.append(((key, pack_values(left)), (key, pack_values(right))))
            expected.append([(column, a, b) for column, (a, b) in enumerate(zip(left, right)) if a != b])
        actual = [list(diffs) for _, _, diffs in differences_for_pairs(pairs, 101)]
        self.assertEqual(actual, expected)

    def test_streaming_consumption_reads_only_one_bounded_chunk_ahead(self):
        consumed = []
        def source():
            for row in range(100):
                consumed.append(row)
                key = str(row).encode()
                yield (key, pack_values(['a'] * 100)), (key, pack_values(['b'] * 100))
        stream = differences_for_pairs(source(), 100, max_cells=1000)
        first = next(stream)
        # Ten rows in this chunk plus one triggering the flush; the remaining
        # source stays unconsumed while the caller writes or displays results.
        self.assertEqual(len(consumed), 11)
        self.assertEqual(len(list(first[2])), 100)
        self.assertEqual(sum(len(list(diffs)) for _, _, diffs in stream), 9900)

    def test_byte_and_cell_limits_and_oversized_row(self):
        from mismatch_scan import _scan_chunk
        pairs = [((str(row).encode(), pack_values(['a'] * 100)), (str(row).encode(), pack_values(['b'] * 100))) for row in range(50)]
        recorded = []
        def checked(chunk, column_count, native):
            recorded.append(chunk)
            self.assertLessEqual(len(chunk) * column_count, 1000)
            self.assertLessEqual(sum(len(record[1]) for pair in chunk for record in pair), 2000)
            yield from _scan_chunk(chunk, column_count, native)
        with patch('mismatch_scan._scan_chunk', side_effect=checked):
            self.assertEqual(sum(len(list(diff)) for _, _, diff in differences_for_pairs(pairs, 100, max_cells=1000, max_bytes=2000)), 5000)
        self.assertGreater(len(recorded), 1)
        with patch('mismatch_scan._scan_chunk', side_effect=AssertionError('oversized batch')):
            self.assertEqual(sum(len(list(diff)) for _, _, diff in differences_for_pairs(pairs[:1], 100, max_cells=10)), 100)

    def test_invalid_limits_and_row_shape(self):
        with self.assertRaises(ValueError):
            list(differences_for_pairs([], 0))
        with self.assertRaises(ValueError):
            list(iter_differences(pack_values(['one']), pack_values(['one', 'two'])))


if __name__ == '__main__':
    unittest.main()
