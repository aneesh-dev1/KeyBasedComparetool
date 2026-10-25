"""Bounded, exact hash buckets shared by resident overflow and parallel comparison.

Hashes only route records. Dictionaries always check the complete encoded key.
Frames carry occurrence counts so spilling a coalesced duplicate group is O(1).
"""
import hashlib
import io
import struct
import sys
import tempfile
from pathlib import Path
from task_control import check_cancel

FRAME = struct.Struct('<QQQ')
MIB = 1024 ** 2


def bucket(key, count, depth=0):
    # A depth-specific digest also splits adversarial CRC collisions on overflow.
    return int.from_bytes(hashlib.blake2b(key, digest_size=8, person=depth.to_bytes(8, 'little')).digest(), 'little') % count


def records(segments, cancel_file=None):
    """Decode bounded blocks, avoiding three Python file reads per narrow row."""
    for path, offset, length in segments:
        with open(path, 'rb', buffering=256 * 1024) as stream:
            stream.seek(offset)
            remaining, data, position = length, b'', 0
            index = 0
            while remaining or position < len(data):
                if index % 4096 == 0:
                    check_cancel(cancel_file)
                index += 1
                if len(data) - position < FRAME.size:
                    block = stream.read(min(MIB, remaining))
                    if not block and remaining:
                        raise ValueError('Incomplete comparison spill data')
                    remaining -= len(block)
                    data, position = data[position:] + block, 0
                if len(data) - position < FRAME.size:
                    raise ValueError('Incomplete comparison spill frame')
                ks, ps, count = FRAME.unpack_from(data, position)
                size = FRAME.size + ks + ps
                if not count or size > len(data) - position + remaining:
                    raise ValueError('Invalid comparison spill frame')
                if size > len(data) - position:
                    wanted = min(remaining, max(MIB, size - (len(data) - position)))
                    block = stream.read(wanted)
                    if len(block) != wanted:
                        raise ValueError('Incomplete comparison spill data')
                    remaining -= len(block)
                    data, position = data[position:] + block, 0
                start = position + FRAME.size
                yield data[start:start + ks], data[start + ks:position + size], count
                position += size


class BudgetExceeded(Exception):
    pass


def load_pair(left, right, policy, capture, limit, cancel_file=None):
    maps, samples = {}, {}
    owned = 0
    overhead = 2 * sys.getsizeof(b'') + sys.getsizeof([None, None]) + sys.getsizeof(1)
    dict_bytes = 0
    sample_bytes = 0
    index = 0
    for side, segments in (('left', left), ('right', right)):
        rows = maps[side] = {}
        for key, payload, count in records(segments, cancel_file):
            if len(key) + len(payload) + overhead > limit:
                raise ValueError('A comparison key/row exceeds the available memory budget; increase available memory.')
            previous = rows.get(key)
            if previous is None:
                rows[key] = [payload, count]
                owned += len(key) + len(payload) + overhead
            else:
                if capture:
                    sample = samples.get(side)
                    if previous[1] == 1 and (sample is None or key < sample[0]):
                        samples[side] = (key, [previous[0], payload])
                    elif sample is not None and sample[0] == key and len(sample[1]) < 3:
                        sample[1].append(payload)
                if policy == 'last':
                    owned += len(payload) - len(previous[0])
                    previous[0] = payload
                previous[1] += count
            if index % 128 == 0:
                dict_bytes = sum(sys.getsizeof(m) for m in maps.values())
            if capture and previous is not None:
                sample_bytes = sum(sys.getsizeof(p) for _, values in samples.values() for p in values)
            index += 1
            if owned + dict_bytes + sample_bytes > limit:
                raise BudgetExceeded
    return maps['left'], maps['right'], samples


def loaded_buckets(left, right, policy, capture, limit, directory, cancel_file=None, depth=0):
    """Recursively split only overflowing buckets, without rereading source files.

    Source occurrence order is preserved within every child. Output order is
    bucket/insertion order, never sorted-key order. Scratch is removed on close.
    """
    try:
        result = load_pair(left, right, policy, capture, limit, cancel_file)
    except BudgetExceeded:
        result = None  # release failed maps before allocating spill buffers
    if result is not None:
        yield result
        return
    if depth >= 16:
        raise ValueError('A comparison key/row exceeds the available memory budget; increase available memory.')
    with tempfile.TemporaryDirectory(prefix='hash-buckets-', dir=directory) as work:
        children = {}
        for side, segments in (('left', left), ('right', right)):
            paths = [Path(work) / f'{side}-{i}.bin' for i in range(16)]
            streams = [open(path, 'wb', buffering=64 * 1024) for path in paths]
            try:
                for key, payload, count in records(segments, cancel_file):
                    target = streams[bucket(key, 16, depth)]
                    target.write(FRAME.pack(len(key), len(payload), count))
                    target.write(key)
                    target.write(payload)
            finally:
                for stream in streams:
                    stream.close()
            children[side] = [[(str(path), 0, path.stat().st_size)] for path in paths]
        for i in range(16):
            yield from loaded_buckets(children['left'][i], children['right'][i], policy, capture,
                                      limit, work, cancel_file, depth + 1)


class DiskJoin:
    """Continue a resident lookup that outgrew RAM without reopening either input."""
    def __init__(self, directory, limit):
        self.work = tempfile.TemporaryDirectory(prefix='hash-overflow-', dir=directory)
        self.limit = limit
        self.paths = {s: Path(self.work.name) / (s + '.bin') for s in ('left', 'right')}
        self.streams = {s: open(p, 'wb', buffering=256 * 1024) for s, p in self.paths.items()}
        self.samples = {}

    def add(self, side, key, payload, count=1):
        sample = self.samples.get(side)
        if count == 1 and sample and sample[0] == key and len(sample[1]) < 3:
            sample[1].append(payload)
        stream = self.streams[side]
        stream.write(FRAME.pack(len(key), len(payload), count))
        stream.write(key)
        stream.write(payload)

    def finish(self):
        for stream in self.streams.values():
            stream.close()

    def close(self):
        self.finish()
        self.work.cleanup()

    def iter_pairs(self, stats, audit, args):
        from hash_join import HashJoin
        self.finish()
        segments = {s: [(str(p), 0, p.stat().st_size)] for s, p in self.paths.items()}
        import copy
        local_args = copy.copy(args)
        local_args.diagnostics_file = None
        diagnostic_samples = {}
        for left, right, samples in loaded_buckets(segments['left'], segments['right'],
                args.duplicate_policy, bool(getattr(args, 'diagnostics_file', None)), self.limit,
                self.work.name, getattr(args, 'cancel_file', None)):
            # Samples captured before spilling contain the original occurrences,
            # whereas the spill frame holds the selected first/last occurrence.
            for side, rows in (('left', left), ('right', right)):
                original = self.samples.get(side)
                if original and original[0] in rows and (side not in samples or original[0] <= samples[side][0]):
                    samples[side] = original
            for side, rows in (('left', left), ('right', right)):
                sample = samples.get(side)
                if sample and (side not in diagnostic_samples or sample[0] < diagnostic_samples[side][0]):
                    diagnostic_samples[side] = (sample[0], list(sample[1]), rows[sample[0]][1])
            joined = HashJoin(left, right, samples, 0, self.limit)
            try:
                yield from joined.iter_pairs(stats, audit, local_args)
            finally:
                joined.close()
        diagnostics = getattr(args, 'diagnostics_file', None)
        if diagnostics and not Path(diagnostics).exists():
            import json
            from compare import unpack_values
            from file_io import atomic_write_text
            for side in ('left', 'right'):
                if side not in diagnostic_samples:
                    continue
                key, payloads, count = diagnostic_samples[side]
                atomic_write_text(diagnostics, json.dumps(dict(side=side, key=json.loads(key), count=count,
                    sample_columns=getattr(args, 'diagnostic_columns', [])[:20],
                    samples=[['[NULL]' if v is None else v[:500] for v in unpack_values(p)[:20]] for p in payloads],
                    note=f'Comparison continues using the {args.duplicate_policy} source occurrence per key. Extra rows are skipped. This is the first duplicate group; download the duplicate audit for all keys.')))
                break


class _DigestSink(io.RawIOBase):
    """Hash output at buffer granularity, avoiding a second write-time disk scan."""
    def __init__(self, path):
        super().__init__()
        self.stream = open(path, 'wb', buffering=0)
        self.digest = hashlib.sha256()
        self.size = 0

    def writable(self):
        return True

    def write(self, value):
        count = self.stream.write(value)
        self.digest.update(memoryview(value)[:count])
        self.size += count
        return count

    def close(self):
        if not self.closed:
            try:
                super().close()
            finally:
                self.stream.close()


def evidence_stream(path, integrity):
    if not integrity:
        return open(path, 'w', encoding='utf-8', newline='', buffering=256 * 1024), None
    sink = _DigestSink(path)
    return io.TextIOWrapper(io.BufferedWriter(sink, 256 * 1024), encoding='utf-8', newline=''), sink
