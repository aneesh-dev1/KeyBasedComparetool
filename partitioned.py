"""Parallel, range-partitioned exact comparison for large inputs (the "partitioned" engine).

1. Plan: split each delimited-text file into byte ranges that end at a newline, and each Parquet file
   into row-group ranges. Sample keys and choose sorted key-range boundaries.
2. Partition (parallel): each worker parses one range with the same packers as the other engines and
   writes (key, packed row) frames into a spill file, grouped by key range.
3. Join (parallel): each worker loads one key range from both sides in source order (so first/last
   duplicate rules hold), sorts its keys and runs the shared comparison loop into partial outputs.
4. Assemble: partial outputs are concatenated in key-range order. Every output file therefore has the
   same global key order, and the same bytes, as the in-memory and disk-sort engines.

Exactness of byte ranges: a range end is only a candidate (the first newline after an even split). A
range is accepted only when its strict CSV parse ends exactly at the range end, and ranges are checked
in file order starting from the header, so every accepted boundary is a proven record boundary. A
range that ends inside a quoted line break fails its strict parse, so it is merged with the next range
and re-read from the last proven boundary. Genuine errors are reported with the same global record
number as the sequential engines.
"""
import bisect
import csv
import io
import json
import multiprocessing
import os
import re
import shutil
import struct
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace

from task_control import check_cancel

FRAME = struct.Struct('<QQ')
MIB = 1024 ** 2
# '\n' and '"' are single, unambiguous bytes in these encodings, so byte ranges can be split safely.
SPLITTABLE_ENCODINGS = {'utf-8', 'utf-8-sig', 'cp1252'}
AUTO_MIN_BYTES = 64 * MIB
RANGE_BYTES = 64 * MIB
SPILL_BYTES = 48 * MIB
_NEEDS_ESCAPE = re.compile(r'[\x00-\x1f\\"]').search


def encode_key(values):
    """Byte-identical to compare.encode(values), without the JSON encoder for plain text keys."""
    if all(type(value) is str and _NEEDS_ESCAPE(value) is None for value in values):
        return ('["' + '","'.join(values) + '"]').encode('utf-8')
    from compare import encode
    return encode(list(values))


def worker_count(args):
    explicit = int(getattr(args, 'parallel_workers', 0) or 0)
    return max(1, explicit or min(16, os.cpu_count() or 1))


def should_use(args):
    engine = getattr(args, 'comparison_engine', 'auto')
    if engine == 'partitioned':
        return True
    if engine != 'auto' or worker_count(args) < 2:
        return False
    total = sum(os.path.getsize(path) for path in (args.left, args.right))
    if total < AUTO_MIN_BYTES:
        return False
    # Spill files hold one packed copy of the compared columns; keep a margin for outputs.
    free = shutil.disk_usage(args.temp_dir or Path(args.left).parent).free
    return free > total * 1.2 + 512 * MIB


class _Range(io.RawIOBase):
    """Raw reader limited to [start, end) of a file."""
    def __init__(self, path, start, end):
        self._file = open(path, 'rb', buffering=0)
        self._file.seek(start)
        self._left = end - start

    def readable(self):
        return True

    def readinto(self, buffer):
        if self._left <= 0:
            return 0
        view = memoryview(buffer)[:min(len(buffer), self._left)]
        count = self._file.readinto(view)
        self._left -= count
        return count

    def close(self):
        if not self.closed:
            self._file.close()
        super().close()


def open_range(path, start, end, encoding):
    # Only the start of a file can carry a byte-order mark.
    if start and encoding == 'utf-8-sig':
        encoding = 'utf-8'
    raw = io.BufferedReader(_Range(path, start, end), buffer_size=4 * MIB)
    return io.TextIOWrapper(raw, encoding=encoding, newline='')


def data_start(path, delimiter, encoding, has_header):
    """Byte offset of the first data record, or None when the file cannot be split by bytes."""
    if encoding not in SPLITTABLE_ENCODINGS:
        return None
    if not has_header:
        return 0
    consumed = 0
    with open(path, 'rb') as raw:
        def lines():
            nonlocal consumed
            for index, line in enumerate(raw):
                # A bare carriage return ends a text-mode line; byte offsets would disagree.
                if b'\r' in line[:-2] or (line.endswith(b'\r') and not line.endswith(b'\r\n')):
                    raise _Unsplittable
                consumed += len(line)
                yield line.decode(encoding if index == 0 else ('utf-8' if encoding == 'utf-8-sig' else encoding))
        try:
            next(csv.reader(lines(), delimiter=delimiter, strict=True))
        except (_Unsplittable, StopIteration, csv.Error, UnicodeDecodeError):
            return None
    return consumed


class _Unsplittable(Exception):
    pass


def text_ranges(path, start, target_bytes=None):
    target_bytes = target_bytes or RANGE_BYTES
    size = os.path.getsize(path)
    count = max(1, -(-(size - start) // target_bytes)) if size > start else 1
    bounds = [start]
    with open(path, 'rb') as stream:
        for index in range(1, count):
            stream.seek(start + (size - start) * index // count)
            stream.readline()
            position = stream.tell()
            if position >= size:
                break
            if position > bounds[-1]:
                bounds.append(position)
    bounds.append(max(size, start))
    return list(zip(bounds, bounds[1:]))


def _split_record(text, delimiter, width):
    if '"' not in text:
        cells = text.split(delimiter)
        return cells if len(cells) == width else None
    try:
        row = next(csv.reader([text], delimiter=delimiter, strict=True))
    except (csv.Error, StopIteration):
        return None
    return row if len(row) == width else None


def sample_text_keys(path, width, key_positions, delimiter, encoding, start, count):
    """Keys read at evenly spaced offsets. They only balance key ranges; correctness never depends on them."""
    size = os.path.getsize(path)
    keys = []
    if size <= start:
        return keys
    decoding = 'utf-8' if encoding == 'utf-8-sig' else encoding
    with open(path, 'rb') as stream:
        for index in range(count):
            stream.seek(start + (size - start) * index // count)
            if index:
                stream.readline()
            line = stream.readline()
            if not line:
                continue
            cells = _split_record(line.decode(decoding, 'replace').rstrip('\r\n'), delimiter, width)
            if cells is not None:
                keys.append(encode_key(tuple(cells[i] for i in key_positions)))
    return keys


def sample_parquet_keys(path, key_names, count):
    from parquet_input import open_file
    keys = []
    with open_file(path) as file:
        groups = file.num_row_groups
        chosen = sorted({groups * index // max(1, min(groups, 32)) for index in range(min(groups, 32))})
        for group in chosen:
            table = file.read_row_group(group, columns=key_names)
            columns = [table.column(name).to_pylist() for name in key_names]
            step = max(1, table.num_rows // max(1, count // max(1, len(chosen))))
            for row in range(0, table.num_rows, step):
                keys.append(encode_key(tuple(None if column[row] is None else str(column[row]) for column in columns)))
    return keys


def choose_bounds(keys, partitions):
    keys = sorted(keys)
    if not keys or partitions <= 1:
        return []
    bounds = []
    for index in range(1, partitions):
        candidate = keys[len(keys) * index // partitions]
        if not bounds or candidate > bounds[-1]:
            bounds.append(candidate)
    return bounds


def _init_worker(max_field_mb):
    csv.field_size_limit(max_field_mb * MIB)


def _partition_range(task):
    """Phase 2: pack one byte or row-group range and spill its rows grouped by key range."""
    side, path = task['side'], task['path']
    bounds, excluded = task['bounds'], task['excluded']
    allow_empty, cancel_file = task['allow_empty'], task['cancel_file']
    if task['kind'] == 'parquet':
        from parquet_input import PackedRows
        reader = PackedRows(path, task['projection'], task['key_positions'], 0, task['batch_rows'],
                            task['memory_mb'], task['native_batch_bytes'], row_groups=task['groups'])
    else:
        from csv_input import PackedRows
        options = SimpleNamespace(dialects={side: task['dialect']}, io_buffer_bytes=4 * MIB)
        opener = None if task['whole'] else (lambda: open_range(path, task['start'], task['end'], task['dialect']['encoding']))
        reader = PackedRows(path, task['names'], task['order'], task['key_positions'], options, side=side, opener=opener)
    buffers = [[] for _ in range(len(bounds) + 1)]
    pending = 0
    spills = []
    sequence = 0
    spill_dir = Path(task['spill_dir'])

    def flush():
        nonlocal sequence, pending
        target = spill_dir / f"{side}-{task['index']:05d}-{sequence:04d}.spill"
        sequence += 1
        segments = []
        offset = 0
        with open(target, 'wb', buffering=8 * MIB) as stream:
            for part, items in enumerate(buffers):
                if not items:
                    continue
                data = b''.join(items)
                stream.write(data)
                segments.append((part, offset, len(data)))
                offset += len(data)
                items.clear()
        pending = 0
        return str(target), segments

    count = excluded_count = 0
    bisect_right = bisect.bisect_right
    pack = FRAME.pack
    with reader as rows:
        for key_values, payload in rows:
            count += 1
            if count & 4095 == 0:
                check_cancel(cancel_file)
            key = encode_key(key_values)
            if key in excluded:
                excluded_count += 1
                continue
            if not allow_empty and any(value is None or value == '' for value in key_values):
                raise ValueError(f'{path}: record {count}: empty key component')
            buffers[bisect_right(bounds, key)].append(pack(len(key), len(payload)) + key + payload)
            pending += len(key) + len(payload) + 16
            if pending >= SPILL_BYTES:
                spills.append(flush())
    spills.append(flush())
    return dict(side=side, index=task['index'], rows=count, excluded=excluded_count, spills=spills)


def _load_partition(segments, policy, capture):
    rows = {}
    sample = None
    unpack = FRAME.unpack_from
    for path, offset, length in segments:
        with open(path, 'rb') as stream:
            stream.seek(offset)
            data = stream.read(length)
        position, end = 0, len(data)
        while position < end:
            key_size, payload_size = unpack(data, position)
            position += 16
            key = data[position:position + key_size]
            position += key_size
            payload = data[position:position + payload_size]
            position += payload_size
            previous = rows.get(key)
            if previous is None:
                rows[key] = [payload, 1]
                continue
            occurrences = previous[1] + 1
            # Same diagnostic sample as the other engines: the smallest duplicated key, first three rows.
            if capture:
                if occurrences == 2 and (sample is None or key < sample[0]):
                    sample = (key, [previous[0], payload])
                elif sample is not None and sample[0] == key and len(sample[1]) < 3:
                    sample[1].append(payload)
            if policy == 'last':
                previous[0] = payload
            previous[1] = occurrences
    return rows, sample


def _join_partition(task):
    """Phase 3: compare one key range of both sides with the shared comparison loop."""
    from compare import compare_pair_stream
    part, policy = task['part'], task['policy']
    left, left_sample = _load_partition(task['left'], policy, task['capture'])
    right, right_sample = _load_partition(task['right'], policy, task['capture'])
    check_cancel(task['cancel_file'])
    out = Path(task['out'])
    stats = dict(matched_keys=0, equal_rows=0, changed_rows=0, changed_cells=0, left_only=0, right_only=0,
                 override_equivalent_cells=0, rule_equivalent_cells=0, left_duplicate_keys=0,
                 left_duplicate_rows_skipped=0, right_duplicate_keys=0, right_duplicate_rows_skipped=0)
    columns = {name: 0 for name in task['columns']}
    names = ('differences', 'left_only', 'right_only', 'matched_keys', 'raw_differences', 'dup_left', 'dup_right')
    streams = {name: open(out / f'{name}-{part:05d}.csv', 'w', encoding='utf-8', newline='', buffering=4 * MIB) for name in names}
    try:
        writers = {name: csv.writer(stream) for name, stream in streams.items()}
        for side, rows in (('left', left), ('right', right)):
            for key in sorted(key for key, entry in rows.items() if entry[1] > 1):
                count = rows[key][1]
                stats[side + '_duplicate_keys'] += 1
                stats[side + '_duplicate_rows_skipped'] += count - 1
                writers['dup_' + side].writerow([side, key.decode('utf-8'), count, count - 1, policy])
        keys = sorted(left.keys() | right.keys())

        def pairs():
            for key in keys:
                a, b = left.get(key), right.get(key)
                yield ((key, a[0]) if a is not None else None, (key, b[0]) if b is not None else None)

        compare_pair_stream(pairs(), task['column_count'], task['policies'], writers, stats, columns,
                            cancel_file=task['cancel_file'])
    finally:
        for stream in streams.values():
            stream.close()
    samples = {name: (sample[0], sample[1], rows[sample[0]][1]) for name, sample, rows in (('left', left_sample, left), ('right', right_sample, right)) if sample}
    return dict(part=part, stats=stats, columns=columns, samples=samples)


def _describe(args, side, path, names, canonical):
    from compare import dialect, is_parquet
    if is_parquet(path):
        from parquet_input import open_file
        with open_file(path) as file:
            sizes = [file.metadata.row_group(g).num_rows for g in range(file.num_row_groups)]
        projection = [names.index(name) for name in canonical]
        workers = worker_count(args)
        slices = max(1, min(len(sizes), workers * 3))
        groups = [list(range(len(sizes) * i // slices, len(sizes) * (i + 1) // slices)) for i in range(slices)]
        groups = [group for group in groups if group]
        offsets, total = [], 0
        for group in groups:
            offsets.append(total)
            total += sum(sizes[g] for g in group)
        return dict(side=side, path=str(path), kind='parquet', projection=projection, groups=groups,
                    row_offsets=offsets, key_positions=[canonical.index(name) for name in args.keys],
                    size=os.path.getsize(path))
    delimiter, encoding, has_header = dialect(args, side)
    start = data_start(path, delimiter, encoding, has_header)
    ranges = text_ranges(path, start) if start is not None else None
    return dict(side=side, path=str(path), kind='text', names=names, order=[names.index(name) for name in canonical],
                key_positions=[names.index(name) for name in args.keys], start=start, ranges=ranges,
                dialect=dict(delimiter=delimiter, encoding=encoding, header=has_header), size=os.path.getsize(path))


def _range_tasks(source, common, whole=False):
    if source['kind'] == 'parquet':
        return [dict(common, side=source['side'], index=index, kind='parquet', path=source['path'],
                     projection=source['projection'], key_positions=source['key_positions'], groups=group)
                for index, group in enumerate(source['groups'])]
    base = dict(common, side=source['side'], kind='text', path=source['path'], names=source['names'],
                order=source['order'], key_positions=source['key_positions'], dialect=source['dialect'])
    if whole or source['ranges'] is None:
        return [dict(base, index=0, whole=True, start=0, end=0)]
    return [dict(base, index=index, whole=False, start=start, end=end) for index, (start, end) in enumerate(source['ranges'])]


def _wait_all(pool, futures, args, on_done):
    """Wait for futures, checking cancellation; returns {future: (result, error)}."""
    outcome = {}
    pending = set(futures)
    while pending:
        check_cancel(getattr(args, 'cancel_file', None))
        done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
        for future in done:
            error = future.exception()
            outcome[future] = (None if error else future.result(), error)
            if error is None:
                on_done(future.result())
    return outcome


def _submit_side(pool, source, common):
    tasks = _range_tasks(source, common)
    return tasks, [pool.submit(_partition_range, task) for task in tasks]


def _partition_side(pool, source, common, args, progress, totals, submitted):
    """Wait for one side's ranges and accept them in file order.

    A range is accepted only when it starts where the previous accepted range ended. A range that
    fails with a CSV error ended inside a quoted line break, so it is merged with the next range and
    re-read; record-numbered errors are genuine and reported with their global record number.
    """
    side = source['side']

    def reported(result):
        totals[side] += result['rows']
        progress(args, f'Indexing {side} file', stage='index', engine='partitioned', rows=totals[side], status='ongoing')

    tasks, futures = submitted
    outcome = _wait_all(pool, futures, args, reported)
    results = [outcome[future] for future in futures]
    if source['kind'] == 'parquet' or len(tasks) == 1:
        for index, (result, error) in enumerate(results):
            if error is not None:
                _raise_numbered(error, source, [r for r, _ in results[:index]], index)
        return [result for result, _ in results]
    accepted, cursor, index, rerun = [], tasks[0]['start'], 0, len(tasks)
    while index < len(tasks):
        result, error = results[index]
        if error is None and tasks[index]['start'] == cursor:
            accepted.append(result)
            cursor = tasks[index]['end']
            index += 1
            continue
        if error is not None and tasks[index]['start'] == cursor and not isinstance(error, csv.Error):
            _raise_numbered(error, source, accepted, None)
        # Re-read from the last proven boundary through the next range end until the parse ends cleanly.
        last = index + 1 if error is not None else index
        while True:
            last = min(last, len(tasks) - 1)
            task = dict(tasks[index], index=rerun, start=cursor, end=tasks[last]['end'])
            rerun += 1
            progress(args, f'Re-reading part of {side} file (quoted line break at a range edge)', stage='index', engine='partitioned')
            result, error = _wait_all(pool, [pool.submit(_partition_range, task)], args, lambda _: None).popitem()[1]
            if error is None:
                accepted.append(result)
                totals[side] = sum(item['rows'] for item in accepted)
                cursor, index = task['end'], last + 1
                break
            if isinstance(error, csv.Error) and last < len(tasks) - 1:
                last += 1
                continue
            _raise_numbered(error, source, accepted, None)
    return accepted


def _raise_numbered(error, source, accepted, failed_index):
    """Errors from a range whose start is proven are the file's first genuine error; renumber them globally."""
    numbered = re.compile(re.escape(source['path']) + r': record (\d+):')
    if isinstance(error, ValueError) and numbered.match(str(error)):
        if source['kind'] == 'parquet' and failed_index is not None:
            before = source['row_offsets'][failed_index]
        else:
            before = sum(item['rows'] for item in accepted)
        message = numbered.sub(lambda match: f"{source['path']}: record {int(match.group(1)) + before}:", str(error), count=1)
        raise ValueError(message) from error
    raise error


def run(args, left_names, right_names, canonical, temp, stats, columns, out, policies, progress):
    """Compare both sources with the partitioned engine and write every output file into ``out``."""
    from compare import unpack_values
    from file_io import atomic_write_text
    started = time.monotonic()
    workers = worker_count(args)
    work = Path(temp) / f'partitioned-{os.getpid()}'
    shutil.rmtree(work, ignore_errors=True)
    (work / 'spill').mkdir(parents=True)
    (work / 'parts').mkdir()
    try:
        sources = [_describe(args, 'left', args.left, left_names, canonical),
                   _describe(args, 'right', args.right, right_names, canonical)]
        total_bytes = sum(source['size'] for source in sources)
        budget = max(64, float(getattr(args, 'memory_mb', 0) or 1024)) * MIB
        # A key range of both sides must fit one worker's share of memory (packed rows plus dict overhead).
        partitions = max(workers * 2, min(4096, -(-int(total_bytes * 2.5) // int(max(64 * MIB, budget / workers)))))
        sample_count = min(20000, partitions * 64)
        progress(args, 'Planning parallel comparison', stage='index', engine='partitioned', workers=workers,
                 key_ranges=partitions, input_mb=round(total_bytes / MIB))
        keys = []
        for source in sources:
            if source['kind'] == 'parquet':
                keys += sample_parquet_keys(source['path'], args.keys, sample_count // 2)
            elif source['start'] is not None:
                keys += sample_text_keys(source['path'], len(source['names']), source['key_positions'],
                                         source['dialect']['delimiter'], source['dialect']['encoding'],
                                         source['start'], sample_count // 2)
        bounds = choose_bounds(keys, partitions)
        common = dict(bounds=bounds, excluded=args.excluded_keys, allow_empty=args.allow_empty_keys,
                      cancel_file=getattr(args, 'cancel_file', None), spill_dir=str(work / 'spill'),
                      batch_rows=getattr(args, 'read_batch_size', 100000),
                      memory_mb=max(16, int(budget / MIB / workers)), native_batch_bytes=getattr(args, 'native_batch_bytes', None))
        context = multiprocessing.get_context('spawn')
        totals = dict(left=0, right=0)
        with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_init_worker,
                                 initargs=(getattr(args, 'max_field_mb', 64),)) as pool:
            phase = time.monotonic()
            # Both files are read at the same time so no worker idles at the end of one file.
            submitted = {source['side']: _submit_side(pool, source, common) for source in sources}
            read = {source['side']: _partition_side(pool, source, common, args, progress, totals, submitted[source['side']]) for source in sources}
            for side in ('left', 'right'):
                stats[side + '_rows'] = sum(result['rows'] for result in read[side])
                stats[side + '_excluded_rows'] = sum(result['excluded'] for result in read[side])
                progress(args, f'Indexing {side} file complete', stage='index', engine='partitioned',
                         rows=stats[side + '_rows'], excluded=stats[side + '_excluded_rows'], status='completed')
            progress(args, 'Read and partition complete', stage='index', engine='partitioned', status='completed',
                     elapsed_seconds=round(time.monotonic() - phase, 3))
            segments = {side: [[] for _ in range(len(bounds) + 1)] for side in ('left', 'right')}
            for side in ('left', 'right'):
                for result in read[side]:
                    for path, parts in result['spills']:
                        for part, offset, length in parts:
                            segments[side][part].append((path, offset, length))
            capture = bool(getattr(args, 'diagnostics_file', None)) and not Path(args.diagnostics_file).exists()
            names = [name for name in canonical if name not in args.keys]
            progress(args, 'Comparing keys', stage='compare', rows=0)
            phase = time.monotonic()
            futures = [pool.submit(_join_partition, dict(part=part, left=segments['left'][part], right=segments['right'][part],
                                                         policy=args.duplicate_policy, capture=capture, out=str(work / 'parts'),
                                                         columns=names, column_count=len(canonical), policies=policies,
                                                         cancel_file=common['cancel_file']))
                       for part in range(len(bounds) + 1)]
            joined = [0]

            def compared(result):
                joined[0] += 1
                # At most about 20 progress lines, however many key ranges there are.
                if joined[0] * 20 // len(futures) != (joined[0] - 1) * 20 // len(futures):
                    progress(args, 'Comparison progress', stage='compare', key_ranges_done=joined[0],
                             key_ranges=len(futures), status='ongoing')
            outcome = _wait_all(pool, futures, args, compared)
            results = []
            for future in futures:
                result, error = outcome[future]
                if error is not None:
                    raise error
                results.append(result)
        for result in results:
            for name, value in result['stats'].items():
                stats[name] += value
            for name, value in result['columns'].items():
                columns[name] += value
        _assemble(out, work / 'parts', len(results), args.keys)
        if capture:
            for side in ('left', 'right'):
                sample = next((result['samples'][side] for result in results if side in result['samples']), None)
                if sample is None:
                    continue
                key, payloads, count = sample
                atomic_write_text(args.diagnostics_file, json.dumps(dict(
                    side=side, key=json.loads(key), count=count,
                    sample_columns=getattr(args, 'diagnostic_columns', [])[:20],
                    samples=[['[NULL]' if value is None else value[:500] for value in unpack_values(payload)[:20]] for payload in payloads],
                    note=f'Comparison continues using the {args.duplicate_policy} source occurrence per key. Extra rows are skipped. This is the first duplicate group; download the duplicate audit for all keys.')))
                break
        progress(args, 'Comparing keys', stage='compare', rows=stats['matched_keys'] + stats['left_only'] + stats['right_only'],
                 status='completed', elapsed_seconds=round(time.monotonic() - phase, 3))
        progress(args, 'Partitioned comparison complete', stage='compare', workers=workers, key_ranges=len(results),
                 elapsed_seconds=round(time.monotonic() - started, 3))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _assemble(out, parts, count, keys):
    """Concatenate partial outputs in key-range order under the same headers as the other engines."""
    layout = [('differences', ['key_json', 'column', 'left_value', 'right_value'], ['differences']),
              ('left_only', keys, ['left_only']), ('right_only', keys, ['right_only']),
              ('matched_keys', ['key_json'], ['matched_keys']),
              ('raw_differences', ['key_json', 'column', 'left_value', 'right_value', 'left_null', 'right_null'], ['raw_differences']),
              ('duplicate_keys', ['side', 'key_json', 'occurrences', 'skipped_rows', 'kept_occurrence'], ['dup_left', 'dup_right'])]
    for name, header, sources in layout:
        target = out / f'{name}.csv'
        with open(target, 'w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerow(header)
        with open(target, 'ab') as stream:
            for source in sources:
                for part in range(count):
                    with open(parts / f'{source}-{part:05d}.csv', 'rb') as piece:
                        shutil.copyfileobj(piece, stream, 8 * MIB)
