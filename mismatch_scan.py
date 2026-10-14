"""Exact packed-row comparison with bounded, optional native sparse scanning.

Dense rows deliberately use Python's C string splitter: converting most cells
out of Arrow costs more than it saves.  The batch API samples each bounded
chunk and uses Arrow only when few of its cells differ.
"""
import json


MAX_BATCH_CELLS = 128_000
MAX_BATCH_BYTES = 8 * 1024 * 1024
SPARSE_DENSITY = 0.10


def _unpack(payload):
    marker = payload[:1]
    if marker == b'\x00':
        return payload[1:].decode('utf-8').split('\x1f')
    if marker == b'\x01':
        return json.loads(payload[1:])
    if marker == b'\x02':
        flags, values = payload[1:].split(b'\x1e', 1)
        return [None if flag == 49 else value
                for flag, value in zip(flags, values.decode('utf-8').split('\x1f'))]
    raise ValueError('Invalid temporary comparison payload')


def iter_differences(left_payload, right_payload):
    """Yield (column index, left value, right value) in source column order.

    Only one decoded row from each file is retained. Null is distinct from an
    empty string, and values are compared as exact text without normalization.
    """
    if left_payload == right_payload:
        return
    left, right = _unpack(left_payload), _unpack(right_payload)
    if len(left) != len(right):
        raise ValueError('Compared rows have different column counts')
    for index, (first, second) in enumerate(zip(left, right)):
        if first != second:
            yield index, first, second


def _native_differences(pairs, column_count, pa, pc):
    left = pc.split_pattern(pa.array([a[1][1:] for a, b in pairs], type=pa.binary()).cast(pa.string()), pattern='\x1f')
    right = pc.split_pattern(pa.array([b[1][1:] for a, b in pairs], type=pa.binary()).cast(pa.string()), pattern='\x1f')
    if (not pc.all(pc.equal(pc.list_value_length(left), column_count)).as_py()
            or not pc.all(pc.equal(pc.list_value_length(right), column_count)).as_py()):
        raise ValueError('Compared rows have different column counts')
    left, right = pc.list_flatten(left), pc.list_flatten(right)
    mask = pc.not_equal(left, right)
    # Matching cells remain inside Arrow. At most MAX_BATCH_CELLS cells can be
    # materialized, even if the sample underestimates the mismatch density.
    indices = pc.indices_nonzero(mask).to_pylist()
    first, second = pc.filter(left, mask).to_pylist(), pc.filter(right, mask).to_pylist()
    result = [[] for _ in pairs]
    for index, a, b in zip(indices, first, second):
        row, column = divmod(index, column_count)
        result[row].append((column, a, b))
    return result


def _scan_chunk(chunk, column_count, native):
    changed = [(index, a, b) for index, (a, b) in enumerate(chunk)
               if a is not None and b is not None and a[1] != b[1]]
    # Null and escaped payloads retain the exact JSON/null-bitmap decoder.
    compatible = (native is not None and column_count >= 16 and len(changed) >= 8
                  and all(a[1][:1] == b[1][:1] == b'\x00' for _, a, b in changed))
    sample = {}
    use_native = False
    if compatible:
        # Sampling is bounded to four rows. Spread samples across the chunk so
        # a file grouped by mismatch pattern does not always select one path.
        sampled = sorted({0, len(changed) // 3, 2 * len(changed) // 3, len(changed) - 1})
        for index in sampled:
            original, a, b = changed[index]
            sample[original] = list(iter_differences(a[1], b[1]))
        use_native = sum(map(len, sample.values())) <= column_count * len(sample) * SPARSE_DENSITY
    result = {}
    if use_native:
        remaining = [(index, a, b) for index, a, b in changed if index not in sample]
        rows = _native_differences([(a, b) for _, a, b in remaining], column_count, *native)
        result = {index: differences for (index, _, _), differences in zip(remaining, rows)}
    for index, (a, b) in enumerate(chunk):
        if a is None or b is None or a[1] == b[1]:
            differences = ()
        elif index in sample:
            differences = sample[index]
        elif use_native:
            differences = result[index]
        else:
            differences = iter_differences(a[1], b[1])
        yield a, b, differences


def differences_for_pairs(pairs, column_count, *, use_native=True,
                          max_cells=MAX_BATCH_CELLS, max_bytes=MAX_BATCH_BYTES):
    """Yield (left record, right record, differences) in input pair order.

    Each record is (key bytes, packed row), or None for a missing key. Missing
    keys yield no cell differences; the caller records the one-sided row.
    ``differences`` is an iterable of the triples from ``iter_differences``.
    Chunks are bounded by both packed bytes and cells. A single oversized row
    is compared directly rather than sent to Arrow. PyArrow is optional.
    """
    if column_count < 1 or max_cells < 1 or max_bytes < 1:
        raise ValueError('Column count and comparison batch limits must be positive')
    native = None
    if use_native:
        try:
            import pyarrow as pa
            import pyarrow.compute as pc
            native = pa, pc
        except ImportError:
            pass
    chunk, size = [], 0
    for a, b in pairs:
        packed_size = sum(len(record[1]) for record in (a, b) if record is not None)
        if chunk and (size + packed_size > max_bytes or (len(chunk) + 1) * column_count > max_cells):
            yield from _scan_chunk(chunk, column_count, native)
            chunk, size = [], 0
        if packed_size > max_bytes or column_count > max_cells:
            differences = () if a is None or b is None else iter_differences(a[1], b[1])
            yield a, b, differences
            continue
        chunk.append((a, b))
        size += packed_size
    if chunk:
        yield from _scan_chunk(chunk, column_count, native)
