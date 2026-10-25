"""Parallel exact hash comparison with bounded recursive spilling.

Source ranges are parsed in parallel and routed by a stable key hash. Each
bucket preserves source occurrence order for duplicate policies. Full keys
and packed values determine equality; no input rows or keys are sorted.
CSV boundaries are validated in source order, merging quoted multiline ranges
when necessary. Checkpoints retain completed reads and comparisons.
"""
import base64
import hashlib
import csv
import io
import json
import multiprocessing
import os
import re
import shutil
import struct
import time
import zlib
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace

from task_control import check_cancel

from hash_spill import FRAME, loaded_buckets
MIB = 1024 ** 2
# '\n' and '"' are single, unambiguous bytes in these encodings, so byte ranges can be split safely.
SPLITTABLE_ENCODINGS = {'utf-8', 'utf-8-sig', 'cp1252'}
AUTO_MIN_BYTES = 64 * MIB
RANGE_BYTES = 64 * MIB
SPILL_BYTES = 48 * MIB
# Early estimate: keys whose CRC-32 falls in one bucket of ESTIMATE_BUCKETS are sampled. The choice depends only
# on the key, so both files sample the same keys. Each range keeps at most ESTIMATE_PER_RANGE rows.
ESTIMATE_BUCKETS = 64
ESTIMATE_PER_RANGE = 400
ESTIMATE_BYTES = 64 * MIB
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


def _init_worker(max_field_mb):
    csv.field_size_limit(max_field_mb * MIB)


def _partition_range(task):
    """Phase 2: pack one byte or row-group range and spill its rows grouped by key hash."""
    side, path = task['side'], task['path']
    partitions, excluded = task['partitions'], task['excluded']
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
    buffers = [[] for _ in range(partitions)]
    pending = 0
    spills = []
    checksums = {}
    sequence = 0
    spill_dir = Path(task['spill_dir'])

    def flush():
        nonlocal sequence, pending
        target = spill_dir / f"{side}-{task['index']:05d}-{sequence:04d}.spill"
        sequence += 1
        segments = []
        offset = 0
        digest = hashlib.sha256() if task.get('checkpointed') else None
        with open(target, 'wb', buffering=256 * 1024) as stream:
            for part, items in enumerate(buffers):
                if not items:
                    continue
                data = b''.join(items)
                stream.write(data)
                if digest is not None:
                    digest.update(data)
                segments.append((part, offset, len(data)))
                offset += len(data)
                items.clear()
        pending = 0
        if digest is not None:
            checksums[str(target)] = digest.hexdigest()
        return str(target), segments

    count = excluded_count = 0
    pack = FRAME.pack
    sample, sample_bytes = [], 0
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
            key_hash = zlib.crc32(key)
            buffers[key_hash % partitions].append(pack(len(key), len(payload), 1) + key + payload)
            if len(sample) < ESTIMATE_PER_RANGE and key_hash % ESTIMATE_BUCKETS == 0 and sample_bytes < 4 * MIB:
                sample.append((key, payload))
                sample_bytes += len(key) + len(payload)
            pending += len(key) + len(payload) + FRAME.size + 40
            if pending >= min(SPILL_BYTES, task['memory_mb'] * MIB // 8):
                spills.append(flush())
    spills.append(flush())
    return dict(side=side, index=task['index'], rows=count, excluded=excluded_count, spills=spills, checksums=checksums, estimate_sample=sample)


class _SampleCollector:
    """Keeps a few changed cells from one key range for the live view; snapshots are written by the parent."""

    def __init__(self, limit=25):
        self.rows, self.limit = [], limit

    def sample(self, row):
        if len(self.rows) < self.limit:
            self.rows.append((row[0], row[1], row[2][:300], row[3][:300]))

    def tick(self, *args, **kwargs):
        pass


class _NullWriter:
    def writerow(self, row):
        pass

    def writerows(self, rows):
        pass


class _Estimator:
    """Compares sampled keys present in both files with the real comparison loop, writing nothing."""

    def __init__(self, column_count, policies, names):
        self.column_count, self.policies = column_count, policies
        self.seen = dict(left={}, right={})
        self.bytes = dict(left=0, right=0)
        self.stats = dict(matched_keys=0, equal_rows=0, changed_rows=0, changed_cells=0, left_only=0, right_only=0,
                          override_equivalent_cells=0, rule_equivalent_cells=0)
        self.columns = {name: 0 for name in names}
        self.writers = {name: _NullWriter() for name in ('differences', 'raw_differences', 'matched_keys', 'left_only', 'right_only')}

    def feed(self, result):
        from compare import compare_pair_stream
        side = result.get('side')
        if side not in self.seen or not result.get('estimate_sample'):
            return
        mine, other, pairs = self.seen[side], self.seen['right' if side == 'left' else 'left'], []
        for key, payload in result['estimate_sample']:
            if key in mine or self.bytes[side] >= ESTIMATE_BYTES:
                continue
            mine[key] = payload
            self.bytes[side] += len(key) + len(payload)
            if key in other:
                pairs.append(((key, payload), (key, other[key])) if side == 'left' else ((key, other[key]), (key, payload)))
        if pairs:
            compare_pair_stream(pairs, self.column_count, self.policies, self.writers, self.stats, self.columns)

    def snapshot(self):
        compared = self.stats['matched_keys']
        return dict(sampled_keys=compared, changed_rows=self.stats['changed_rows'], equal_rows=self.stats['equal_rows'],
                    changed_cells=self.stats['changed_cells'],
                    changed_cells_by_column={name: count for name, count in self.columns.items() if count}) if compared else None


def _watch_reads(futures, args, estimator, live):
    """While both files are read, feed finished ranges from either file into the estimate."""
    pending, done_count, total = set(futures), 0, len(futures)
    while pending:
        check_cancel(getattr(args, 'cancel_file', None))
        done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
        for future in done:
            done_count += 1
            if future.exception() is None:
                estimator.feed(future.result())
        live.tick('reading', ranges_read=done_count, ranges_total=total, estimate=estimator.snapshot(), force=not pending)


def _join_partition(task):
    """Phase 3: compare one key range of both sides with the shared comparison loop."""
    from compare import compare_pair_stream
    part, policy = task['part'], task['policy']
    check_cancel(task['cancel_file'])
    out = Path(task['out'])
    stats = dict(matched_keys=0, equal_rows=0, changed_rows=0, changed_cells=0, left_only=0, right_only=0,
                 override_equivalent_cells=0, rule_equivalent_cells=0, left_duplicate_keys=0,
                 left_duplicate_rows_skipped=0, right_duplicate_keys=0, right_duplicate_rows_skipped=0)
    columns = {name: 0 for name in task['columns']}
    collector = _SampleCollector()
    names = ('differences', 'left_only', 'right_only', 'matched_keys', 'raw_differences', 'dup_left', 'dup_right')
    from hash_spill import evidence_stream
    opened = {name: evidence_stream(out / f'{name}-{part:05d}.csv', task.get('integrity', False)) for name in names}
    streams = {name: value[0] for name, value in opened.items()}
    try:
        writers = {name: csv.writer(stream) for name, stream in streams.items()}
        samples = {}
        limit = task.get('memory_bytes', 64 * MIB) * 3 // 5
        for left, right, bucket_samples in loaded_buckets(task['left'], task['right'], policy,
                task['capture'], limit, out, task['cancel_file']):
            for side, rows in (('left', left), ('right', right)):
                for key, entry in rows.items():
                    count = entry[1]
                    if count > 1:
                        stats[side + '_duplicate_keys'] += 1
                        stats[side + '_duplicate_rows_skipped'] += count - 1
                        writers['dup_' + side].writerow([side, key.decode('utf-8'), count, count - 1, policy])
                sample = bucket_samples.get(side)
                if sample and (side not in samples or sample[0] < samples[side][0]):
                    samples[side] = (sample[0], sample[1], rows[sample[0]][1])

            def pairs():
                for key, a in left.items():
                    b = right.get(key)
                    yield (key, a[0]), ((key, b[0]) if b is not None else None)
                for key, b in right.items():
                    if key not in left:
                        yield None, (key, b[0])

            compare_pair_stream(pairs(), task['column_count'], task['policies'], writers, stats, columns,
                                cancel_file=task['cancel_file'], live=collector)
            left.clear()
            right.clear()
    finally:
        for stream in streams.values():
            stream.close()
    return dict(part=part, stats=stats, columns=columns, samples=samples, live_samples=collector.rows,
                output_checksums={name: (sink.size, sink.digest.hexdigest()) for name, (_, sink) in opened.items() if sink is not None})


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
                    key_columns=[names.index(name) for name in args.keys],
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


WORK_DIR = 'partitioned'


def _encode(value):
    if isinstance(value, bytes):
        return {'__bytes__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {name: _encode(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        return base64.b64decode(value['__bytes__']) if set(value) == {'__bytes__'} else {name: _decode(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


class _Journal:
    """Progress of a checkpointed run: the plan, then one line per finished read range and joined key range.

    A resumed run with the same plan reuses every recorded range whose files are still on disk, so an
    interrupted large comparison continues where it stopped. Sources and settings are validated by the
    checkpoint identity before this is read.
    """

    def __init__(self, work, fingerprint, cancel_file=None):
        self.work, self.fingerprint = work, fingerprint
        self.cancel_file = cancel_file
        self.plan, self.reads, self.sides, self.joins = None, {}, {}, {}
        self.lock = threading.Lock()
        self.verified = set()
        manifest = work / 'manifest.json'
        try:
            saved = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = None
        if saved and saved.get('fingerprint') == fingerprint:
            self._verify_metadata(saved)
            self.plan = _decode(saved['plan'])
            try:
                lines = (work / 'progress.jsonl').read_text(encoding='utf-8').splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    break  # the last line of an interrupted write
                self._verify_metadata(entry)
                entry = _decode(entry)
                if entry['kind'] == 'read' and all(Path(path).exists() for path, _ in entry['result']['spills']):
                    self._verify_spills(entry['result'])
                    self.reads[entry['id']] = entry['result']
                elif entry['kind'] == 'side' and all(Path(path).exists() for result in entry['result']['ranges'] for path, _ in result['spills']):
                    # Every range of one file, accepted in file order (including ranges re-read across quoted line breaks).
                    for result in entry['result']['ranges']:
                        self._verify_spills(result)
                    self.sides[entry['id']] = entry['result']['ranges']
                elif entry['kind'] == 'join':
                    self.joins[entry['result']['part']] = entry['result']
        else:
            shutil.rmtree(work, ignore_errors=True)
        (work / 'spill').mkdir(parents=True, exist_ok=True)
        (work / 'parts').mkdir(exist_ok=True)

    @staticmethod
    def _signed(value):
        return dict(value, checksum=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())

    @staticmethod
    def _verify_metadata(value):
        checksum = value.pop('checksum', None)
        if checksum != hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest():
            raise ValueError('Comparison checkpoint metadata is damaged. Start a new comparison.')

    def _verify_spills(self, result):
        for name, expected in result.get('checksums', {}).items():
            if name in self.verified:
                continue
            from checkpoints import digest
            actual = digest(name, SimpleNamespace(cancel_file=self.cancel_file))
            if actual != expected:
                raise ValueError('Saved hash bucket is missing or damaged. Start a new comparison.')
            self.verified.add(name)

    @property
    def resumed(self):
        return bool(self.reads or self.sides or self.joins)

    def start(self, plan):
        self.plan = plan
        from file_io import atomic_write_text
        atomic_write_text(self.work / 'manifest.json', json.dumps(self._signed(dict(fingerprint=self.fingerprint, plan=_encode(plan)))))
        (self.work / 'progress.jsonl').write_text('', encoding='utf-8')

    def record(self, kind, identity, result):
        # The estimate sample only feeds the live estimate; it is not needed to resume.
        if kind == 'side':
            result = dict(ranges=[{name: value for name, value in item.items() if name != 'estimate_sample'} for item in result])
        else:
            result = {name: value for name, value in result.items() if name != 'estimate_sample'}
        line = json.dumps(self._signed(_encode(dict(kind=kind, id=identity, result=result)))) + '\n'
        with self.lock, open(self.work / 'progress.jsonl', 'a', encoding='utf-8') as stream:
            stream.write(line)

    def joined_files_exist(self, part):
        from checkpoints import digest
        names = ('differences', 'left_only', 'right_only', 'matched_keys', 'raw_differences', 'dup_left', 'dup_right')
        checksums = self.joins.get(part, {}).get('output_checksums', {})
        for name in names:
            path = self.work / 'parts' / f'{name}-{part:05d}.csv'
            expected = checksums.get(name)
            if not expected or not path.exists() or path.stat().st_size != expected[0]:
                return False
            if digest(path, SimpleNamespace(cancel_file=self.cancel_file)) != expected[1]:
                return False  # recompute this bucket from validated source spills
        return True


def _read_id(task):
    return f"{task['side']}:{task['index']}:{task['start'] if task['kind'] == 'text' else 0}"


def _done(result):
    future = Future()
    future.set_result(result)
    return future


def run(args, left_names, right_names, canonical, temp, stats, columns, out, policies, progress):
    """Compare both sources with the partitioned engine and write every output file into ``out``."""
    from compare import unpack_values
    from file_io import atomic_write_text
    started = time.monotonic()
    workers = min(worker_count(args), max(1, int(getattr(args, 'memory_mb', 1024) or 1024) // 64))
    # A checkpointed run keeps its work folder until it succeeds, so an interrupted run can resume.
    checkpointed = bool(getattr(args, 'checkpoint_dir', None))
    work = Path(temp) / (WORK_DIR if checkpointed else f'partitioned-{os.getpid()}')
    journal = None
    if checkpointed:
        journal = _Journal(work, dict(version=2, range_bytes=RANGE_BYTES, canonical=list(canonical), keys=list(args.keys)), getattr(args, 'cancel_file', None))
    else:
        shutil.rmtree(work, ignore_errors=True)
        (work / 'spill').mkdir(parents=True)
        (work / 'parts').mkdir()
    succeeded = False
    try:
        budget = max(1, float(getattr(args, 'memory_mb', 0) or 1024)) * MIB
        if journal is not None and journal.plan is not None:
            # The saved plan is reused as is, so recorded ranges line up even if workers or memory changed.
            sources, partitions = journal.plan['sources'], journal.plan['partitions']
            total_bytes = sum(source['size'] for source in sources)
            progress(args, 'Resuming parallel comparison', stage='index', engine='partitioned', workers=workers,
                     key_ranges=partitions, reused_reads=len(journal.reads), reused_key_ranges=len(journal.joins))
        else:
            sources = [_describe(args, 'left', args.left, left_names, canonical),
                       _describe(args, 'right', args.right, right_names, canonical)]
            total_bytes = sum(source['size'] for source in sources)
            # Plan hash buckets from file size; oversized or skewed buckets split again on demand.
            partitions = max(workers * 2, min(4096, -(-int(total_bytes * 2.5) // int(max(64 * MIB, budget / workers)))))
            progress(args, 'Planning parallel hash comparison', stage='index', engine='partitioned', workers=workers,
                     key_ranges=partitions, input_mb=round(total_bytes / MIB))
            if journal is not None:
                journal.start(dict(sources=sources, partitions=partitions))
        common = dict(partitions=partitions, checkpointed=checkpointed, excluded=args.excluded_keys, allow_empty=args.allow_empty_keys,
                      cancel_file=getattr(args, 'cancel_file', None), spill_dir=str(work / 'spill'),
                      batch_rows=getattr(args, 'read_batch_size', 100000),
                      memory_mb=max(1, int(budget / MIB / workers)), native_batch_bytes=getattr(args, 'native_batch_bytes', None))
        context = multiprocessing.get_context('spawn')
        totals = dict(left=0, right=0)
        with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_init_worker,
                                 initargs=(getattr(args, 'max_field_mb', 64),)) as pool:
            phase = time.monotonic()
            # Both files are read at the same time so no worker idles at the end of one file.
            # Both files are read side by side: ranges are queued alternately, in proportion to each file's size,
            # so the early estimate sees matching keys from the start.
            plans = {source['side']: _range_tasks(source, common) for source in sources}
            order = sorted(((index / len(tasks), side, index) for side, tasks in plans.items() for index in range(len(tasks))))
            futures_by_side = {side: [None] * len(tasks) for side, tasks in plans.items()}
            finished = journal.sides if journal is not None else {}
            for _, side, index in order:
                task = plans[side][index]
                cached = journal.reads.get(_read_id(task)) if journal is not None else None
                if side in finished:
                    cached = dict(side=side, rows=0, spills=[])  # already read; only keeps the live estimate's bookkeeping simple
                if cached is not None:
                    futures_by_side[side][index] = _done(cached)
                    continue
                future = pool.submit(_partition_range, task)
                if journal is not None:
                    future.add_done_callback(lambda done, task=task: done.exception() is None and journal.record('read', _read_id(task), done.result()))
                futures_by_side[side][index] = future
            submitted = {side: (plans[side], futures_by_side[side]) for side in plans}
            # Live view: an early estimate from sampled keys while the files are read, then exact counts per key range.
            live = None
            if getattr(args, 'live_file', None):
                from live import LiveSnapshot
                live_stats, live_columns = {}, {}
                live = LiveSnapshot(args.live_file, live_stats, live_columns)
                estimator = _Estimator(len(canonical), policies, [name for name in canonical if name not in args.keys])
                _watch_reads([future for _, futures in submitted.values() for future in futures], args, estimator, live)
            read = {}
            for source in sources:
                side = source['side']
                if side in finished:
                    read[side] = finished[side]
                    continue
                read[side] = _partition_side(pool, source, common, args, progress, totals, submitted[side])
                if journal is not None:
                    journal.record('side', side, read[side])
            reused_all = len(finished) == 2
            for side in ('left', 'right'):
                stats[side + '_rows'] = sum(result['rows'] for result in read[side])
                stats[side + '_excluded_rows'] = sum(result['excluded'] for result in read[side])
                progress(args, f'Indexing {side} file complete', stage='index', engine='partitioned',
                         rows=stats[side + '_rows'], excluded=stats[side + '_excluded_rows'], status='completed')
            progress(args, 'Read and partition complete', stage='index', engine='partitioned', status='completed',
                     elapsed_seconds=round(time.monotonic() - phase, 3))
            segments = {side: [[] for _ in range(partitions)] for side in ('left', 'right')}
            for side in ('left', 'right'):
                for result in read[side]:
                    for path, parts in result['spills']:
                        for part, offset, length in parts:
                            segments[side][part].append((path, offset, length))
            capture = bool(getattr(args, 'diagnostics_file', None)) and not Path(args.diagnostics_file).exists()
            names = [name for name in canonical if name not in args.keys]
            progress(args, 'Comparing keys', stage='compare', rows=0)
            phase = time.monotonic()
            futures = []
            for part in range(partitions):
                # A joined key range is reused only when its inputs were read from the same recorded ranges.
                cached = journal.joins.get(part) if journal is not None and reused_all else None
                if cached is not None and journal.joined_files_exist(part):
                    futures.append(_done(cached))
                    continue
                future = pool.submit(_join_partition, dict(part=part, left=segments['left'][part], right=segments['right'][part],
                                                           policy=args.duplicate_policy, capture=capture, out=str(work / 'parts'),
                                                           columns=names, column_count=len(canonical), policies=policies,
                                                           cancel_file=common['cancel_file'], memory_bytes=int(budget / workers), integrity=checkpointed))
                if journal is not None:
                    future.add_done_callback(lambda done: done.exception() is None and journal.record('join', done.result()['part'], done.result()))
                futures.append(future)
            joined = [0]

            estimate = estimator.snapshot() if live is not None else None

            def compared(result):
                joined[0] += 1
                if live is not None and result is not None:
                    for name, value in result['stats'].items():
                        live_stats[name] = live_stats.get(name, 0) + value
                    for name, value in result['columns'].items():
                        live_columns[name] = live_columns.get(name, 0) + value
                    for row in result.get('live_samples', ()):
                        live.sample(row)
                    live.tick(key_ranges_done=joined[0], key_ranges=len(futures), estimate=estimate, force=joined[0] == len(futures))
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
                sample = min((result['samples'][side] for result in results if side in result['samples']), key=lambda item: item[0], default=None)
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
        succeeded = True
    finally:
        if succeeded or journal is None:
            shutil.rmtree(work, ignore_errors=True)


def _assemble(out, parts, count, keys):
    """Concatenate partial outputs in stable bucket order with unchanged report headers."""
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
