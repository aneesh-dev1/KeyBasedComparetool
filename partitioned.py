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
from invalid_rows import invalid, blank_key, combine as combine_invalid, FIELDS as SKIP_FIELDS, COUNTERS as SKIP_COUNTERS
import base64
import bisect
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
import hashlib
import io
import itertools
import json
import multiprocessing
import os
import re
import shutil
import struct
import time
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
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
    from runtime_resources import cpu_capacity
    return max(1, min(explicit or 16, cpu_capacity()))


def _wide_text(args):
    """True when both inputs are wide UTF-8 delimited text, so the fingerprint lookup will be used."""
    from compare import dialect, is_parquet
    if getattr(args, 'fingerprint', 'auto') == 'off':
        return False
    for side, path in (('left', args.left), ('right', args.right)):
        if is_parquet(path):
            return False
        delimiter, encoding, _ = dialect(args, side)
        if encoding not in SPLITTABLE_ENCODINGS or len(delimiter) != 1 or not delimiter.isascii() or delimiter in '"\r\n':
            return False
        with open(path, 'rb') as stream:
            sample = stream.read(4 * MIB)
        if sample.count(b'\n') < 2 or len(sample) / sample.count(b'\n') < FINGERPRINT_MIN_ROW_BYTES:
            return False
    return True


def should_use(args):
    engine = getattr(args, 'comparison_engine', 'auto')
    if engine == 'partitioned':
        return True
    if engine != 'auto':
        return False
    total = sum(os.path.getsize(path) for path in (args.left, args.right))
    if total < AUTO_MIN_BYTES:
        return False
    wide = _wide_text(args)
    # With fingerprints even one worker beats the disk sort; otherwise the parallel read needs two or more.
    if worker_count(args) < 2 and not wide:
        return False
    # Spill files hold one packed copy of the compared columns, or only fingerprints; keep a margin for outputs.
    free = shutil.disk_usage(args.temp_dir or Path(args.left).parent).free
    return free > total * (0.1 if wide else 1.2) + 512 * MIB


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


def sample_parquet_keys(path, key_positions, count):
    """Sample keys by column position: comparison names may be renamed and differ from the file's own."""
    from parquet_input import open_file
    keys = []
    with open_file(path) as file:
        key_names = [file.schema_arrow.names[position] for position in key_positions]
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


# Bytes read so far by each running range, one slot per planned range, shared with the parent process so the
# read percentage moves while ranges are still running (several large ranges often finish together).
_PROGRESS = None


def _init_worker(max_field_mb, progress=None):
    global _PROGRESS
    csv.field_size_limit(max_field_mb * MIB)
    _PROGRESS = progress
    # Each process is already a CPU worker: native thread pools must not multiply
    # the broker's allocation by the host core count.
    try:
        import pyarrow as pa
        pa.set_cpu_count(1)
        pa.set_io_thread_count(1)
    except ImportError:
        pass


def _report(task, done):
    if _PROGRESS is not None and task.get('slot') is not None:
        _PROGRESS[task['slot']] = int(done)


def _write_spill(target, buffers):
    """Write buffered frames grouped by key range; returns (path, [(key range, offset, length)])."""
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
    return str(target), segments


# Fingerprint mode: the hash lookup for wide delimited-text files. Copying every row into spill files and
# reading it back was most of the read phase (and on Windows, antivirus scanning and OneDrive syncing of those
# writes made it far slower). Instead each row becomes a 30-byte reference: a 128-bit fingerprint of the row
# plus its byte position in the source, whatever the encoding (UTF-8 or Windows-1252) or quoting. Key ranges
# are joined by looking each key up in the other file's references. Rows whose fingerprints match are equal;
# only rows that differ are read again from the source and compared value by value, so differences stay exact.
#
# Reference kinds: U unquoted line, Q every field quoted, M some fields quoted (no "" escapes, no line breaks),
# P anything else, parsed by the strict CSV parser (escaped quotes, line breaks inside values).
REF = b'\x7f'
REF_TAIL = struct.Struct('<QI')  # record offset and length in the source file
SAME = b'\x00'  # one shared placeholder for both rows of an equal pair; identical payloads are never decoded
# Measured crossover: below about 500 bytes a row is cheaper to copy than to fingerprint and read again.
FINGERPRINT_MIN_ROW_BYTES = 512
UTF8 = ('utf-8', 'utf-8-sig')
_DIGESTS = {'sha256': lambda data: hashlib.sha256(data).digest()[:16],
            'blake2b': lambda data: hashlib.blake2b(data, digest_size=16).digest()}
_CHOSEN_DIGEST = []


def choose_digest():
    """The faster 128-bit fingerprint on this CPU: SHA-256 where it has hardware support, otherwise BLAKE2b."""
    if not _CHOSEN_DIGEST:
        data = os.urandom(2 * MIB)
        timings = {}
        for name, function in _DIGESTS.items():
            started = time.perf_counter()
            for _ in range(4):
                function(data)
            timings[name] = time.perf_counter() - started
        _CHOSEN_DIGEST.append(min(timings, key=timings.get))
    return _CHOSEN_DIGEST[0]


class _NotSimple(Exception):
    """This range needs the full text parser (for example a lone carriage return or an undecodable byte)."""


def _layout(source, digest='sha256'):
    order, dialect = source['order'], source.get('dialect') or {}
    return dict(path=source['path'], delimiter=dialect['delimiter'], width=len(source['names']), order=order,
                full=order == list(range(len(source['names']))), encoding=dialect.get('encoding', 'utf-8'), digest=digest)


def _quotes_wrap_fields(parts, separator):
    """True when every quote pair of a line split on '"' wraps a whole field: it opens at a field start and the
    closing quote is followed by the delimiter or the end of the line. Then the strict CSV parser reads exactly
    the text between the quotes."""
    last = len(parts) - 1
    if parts[0] and not parts[0].endswith(separator):
        return False
    for i in range(2, last, 2):
        if not (parts[i].startswith(separator) and parts[i].endswith(separator)):
            return False
    return not parts[last] or parts[last].startswith(separator)


def _split_quoted(parts, separator):
    """Fields of a line whose quotes all wrap whole fields (see _quotes_wrap_fields), as bytes."""
    cells, current, last = [], None, len(parts) - 1
    for i, part in enumerate(parts):
        if i % 2:
            current = part
            continue
        pieces = part.split(separator)
        if i:
            cells.append(current)
            pieces = pieces[1:]
            if not part:
                continue
        if i < last:
            cells.extend(pieces[:-1])
        else:
            cells.extend(pieces)
    return cells


def _cells(record, kind, layout):
    separator = layout['delimiter'].encode('ascii')
    if kind == b'U':
        return record.split(separator)
    if kind == b'Q':
        return record[1:-1].split(b'"' + separator + b'"')
    return _split_quoted(record.split(b'"'), separator)


def _ref_payload(record, kind, layout):
    """The packed row of a U, Q or M record; byte-identical to csv_input.PackedRows for the same line."""
    full, encoding = layout['full'], layout['encoding']
    if kind == b'U' and full:
        if encoding in UTF8:
            return b'\x00' + record.replace(layout['delimiter'].encode('ascii'), b'\x1f')
        return b'\x00' + record.decode(encoding).replace(layout['delimiter'], '\x1f').encode('utf-8')
    cells = _cells(record, kind, layout)
    chosen = cells if full else [cells[i] for i in layout['order']]
    if encoding in UTF8:
        return b'\x00' + b'\x1f'.join(chosen)
    return b'\x00' + '\x1f'.join(cell.decode(encoding) for cell in chosen).encode('utf-8')


def _parse_record(record, layout):
    """Packed row of a P record: the strict CSV parser over the record's own lines, as the text reader reads it."""
    from compare import pack_values
    text = record.decode(layout['encoding']) + '\n'
    row = next(csv.reader(io.StringIO(text, newline=''), delimiter=layout['delimiter'], strict=True))
    return pack_values(row if layout['full'] else [row[i] for i in layout['order']])


def _fingerprint(record, kind, layout, mode):
    # "raw": both files share delimiter, encoding and column order, so equal raw lines of the same kind are equal
    # rows. P records always use the packed row, so they also match across files and modes.
    digest = _DIGESTS[layout['digest']]
    if kind == b'P':
        return digest(_parse_record(record, layout))
    return digest(record if mode == 'raw' else _ref_payload(record, kind, layout))


def fingerprint_choice(args, sources):
    """('raw' or 'canonical', None) when both files are wide delimited text in a byte-splittable encoding;
    otherwise (None, the reason), and full rows are kept in the spill."""
    if getattr(args, 'fingerprint', 'auto') == 'off':
        return None, 'turned off'
    for source in sources:
        dialect = source.get('dialect') or {}
        delimiter = dialect.get('delimiter', '')
        if source['kind'] != 'text':
            return None, 'one file is Parquet'
        if source['ranges'] is None or dialect.get('encoding') not in SPLITTABLE_ENCODINGS:
            return None, f"{Path(source['path']).name} is {dialect.get('encoding')}, which cannot be split by bytes"
        if len(delimiter) != 1 or not delimiter.isascii() or delimiter in '"\r\n':
            return None, 'the delimiter is not a single ASCII character'
    if getattr(args, 'fingerprint', 'auto') != 'on':
        # Narrow rows are cheap to copy; the fingerprint only pays off when rows are wide.
        for source in sources:
            with open(source['path'], 'rb') as stream:
                stream.seek(source['start'])
                sample = stream.read(4 * MIB)
            if sample.count(b'\n') and len(sample) / sample.count(b'\n') < FINGERPRINT_MIN_ROW_BYTES:
                return None, f"rows of {Path(source['path']).name} average under {FINGERPRINT_MIN_ROW_BYTES} bytes"
    layouts = [_layout(source) for source in sources]
    family = {('utf-8' if layout['encoding'] in UTF8 else layout['encoding']) for layout in layouts}
    same = all(layout['full'] for layout in layouts) and layouts[0]['delimiter'] == layouts[1]['delimiter'] and len(family) == 1
    return ('raw' if same else 'canonical'), None


def fingerprint_mode(args, sources):
    return fingerprint_choice(args, sources)[0]


def _byte_lines(path, start, end):
    """(offset, line without its newline, had a newline) for the lines in [start, end)."""
    with open(path, 'rb') as stream:
        stream.seek(start)
        remaining, offset, tail = end - start, start, b''
        while remaining > 0:
            chunk = stream.read(min(8 * MIB, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            lines = (tail + chunk if tail else chunk).split(b'\n')
            tail = lines.pop()
            for line in lines:
                yield offset, line, True
                offset += len(line) + 1
        if tail:
            yield offset, tail, False


def _read_fingerprints(task):
    """Phase 2 in fingerprint mode: one reference per row, spilled by key range. Mirrors csv_input.PackedRows."""
    from compare import pack_values
    side, path = task['side'], task['path']
    bounds, excluded = task['bounds'], task['excluded']
    allow_empty, cancel_file, mode = task['allow_empty'], task['cancel_file'], task['fingerprint']
    layout = _layout(task, task.get('digest') or 'sha256')
    delimiter, width, full, order, encoding = layout['delimiter'], layout['width'], layout['full'], layout['order'], layout['encoding']
    utf8 = encoding in UTF8
    digest = _DIGESTS[layout['digest']]
    separator = delimiter.encode('ascii')
    quoted_separator = b'"' + separator + b'"'
    direct = delimiter not in ('"', '\r', '\n')
    separator_safe = delimiter == '\x1f'
    limit = csv.field_size_limit()
    key_positions = task['key_positions']
    largest_key, first_key = max(key_positions), min(key_positions)
    from_end = width - first_key < largest_key + 1
    if task['start'] == 0 and encoding == 'utf-8-sig':
        raise _NotSimple  # a byte-order mark belongs to the text reader
    buffers = [[] for _ in range(len(bounds) + 1)]
    spills, pending, sequence = [], 0, 0
    spill_dir = Path(task['spill_dir'])
    count = excluded_count = 0
    bisect_right, pack = bisect.bisect_right, FRAME.pack
    lines = _byte_lines(path, task['start'], task['end'])
    span_end = [0]

    def text(line, newline):
        try:
            return line.decode(encoding) + ('\n' if newline else '')
        except UnicodeDecodeError:
            raise _NotSimple from None

    def continuation():
        for extra_offset, extra, newline in lines:
            if b'\r' in (extra[:-1] if extra.endswith(b'\r') else extra):
                raise _NotSimple
            span_end[0] = extra_offset + len(extra)
            yield text(extra, newline)

    for offset, line, newline in lines:
        count += 1
        if count & 127 == 0:
            _report(task, offset - task['start'])
            if count & 4095 == 0:
                check_cancel(cancel_file)
        record = line[:-1] if line.endswith(b'\r') else line
        # Text mode ends a line at a lone carriage return; leave such files to the text reader.
        if b'\r' in record:
            raise _NotSimple
        if not line.isascii():
            text(line, False)  # the text reader would fail on a byte this encoding cannot decode
        kind = None
        fits = len(line) < limit and (separator_safe or b'\x1f' not in record)
        if direct and fits and record and b'"' not in record and record.count(separator) == width - 1:
            kind = b'U'
            if full:
                if from_end:
                    suffix = record.rsplit(separator, width - first_key)[1:]
                    parts = [suffix[i - first_key] for i in key_positions]
                else:
                    prefix = record.split(separator, largest_key + 1)
                    parts = [prefix[i] for i in key_positions]
            else:
                cells = record.split(separator)
                parts = [cells[i] for i in key_positions]
        elif direct and fits and record.startswith(b'"') and len(record) > 1 and record.endswith(b'"') and record.count(b'"') == 2 * width:
            cells = record[1:-1].split(quoted_separator)
            if len(cells) == width:
                kind = b'Q'
                parts = [cells[i] for i in key_positions]
        if kind is None and direct and fits and b'"' in record and b'""' not in record:
            # Some fields quoted: count the delimiters outside the quotes instead of splitting every value.
            quoted = record.split(b'"')
            if (len(quoted) % 2 and _quotes_wrap_fields(quoted, separator)
                    and record.count(separator) - sum(part.count(separator) for part in quoted[1::2]) == width - 1):
                kind = b'M'
                if quoted[0].count(separator) > largest_key:
                    prefix = quoted[0].split(separator, largest_key + 1)
                    parts = [prefix[i] for i in key_positions]
                else:
                    cells = _split_quoted(quoted, separator)
                    parts = [cells[i] for i in key_positions]
        if kind is not None:
            key_values = tuple(part.decode(encoding) for part in parts) if not utf8 else tuple(part.decode('utf-8') for part in parts)
        else:
            # Escaped quotes or line breaks inside values: the strict CSV parser, exactly as the text reader.
            span_end[0] = offset + len(line)
            row = next(csv.reader(itertools.chain((text(line, newline),), continuation()), delimiter=delimiter, strict=True))
            if len(row) != width:
                raise _NotSimple  # Re-read this range with the audited CSV parser.
            key_values = tuple(row[i] for i in key_positions)
            payload = pack_values(row if full else [row[i] for i in order])
        key = encode_key(key_values)
        if key in excluded:
            excluded_count += 1
            continue
        if not allow_empty and any(value == '' for value in key_values):
            raise _NotSimple  # The audited text reader records blank keys as skipped records.
        if kind is not None:
            stored = REF + kind + digest(record if mode == 'raw' else _ref_payload(record, kind, layout)) + REF_TAIL.pack(offset, len(record))
        else:
            # The record's whole span (all its lines) is referenced; only its fingerprint is kept.
            stored = REF + b'P' + digest(payload) + REF_TAIL.pack(offset, span_end[0] - offset)
        buffers[bisect_right(bounds, key)].append(pack(len(key), len(stored)) + key + stored)
        pending += len(key) + len(stored) + 16
        if pending >= SPILL_BYTES:
            spills.append(_write_spill(spill_dir / f"{side}-{task['index']:05d}-{sequence:04d}.spill", buffers))
            sequence += 1
            pending = 0
    spills.append(_write_spill(spill_dir / f"{side}-{task['index']:05d}-{sequence:04d}.spill", buffers))
    return dict(side=side, index=task['index'], rows=count, excluded=excluded_count, spills=spills)


def _fetch(layout, mode, refs):
    """Read referenced rows again from the source, in file order, and check each against its fingerprint."""
    rows = {}
    with open(layout['path'], 'rb') as stream:
        for ref in sorted(set(refs), key=lambda ref: REF_TAIL.unpack_from(ref, 18)[0]):
            offset, length = REF_TAIL.unpack_from(ref, 18)
            stream.seek(offset)
            record = stream.read(length)
            kind = ref[1:2]
            payload = _parse_record(record, layout) if kind == b'P' and len(record) == length else None
            check = _DIGESTS[layout['digest']](payload) if payload is not None else (
                _fingerprint(record, kind, layout, mode) if len(record) == length else None)
            if check != ref[2:18]:
                raise ValueError(f"{layout['path']} changed during the comparison. Start a new comparison.")
            rows[ref] = payload if payload is not None else _ref_payload(record, kind, layout)
    return rows


def _resolve(left, right, samples, task):
    """Equal fingerprints become one shared placeholder; rows that differ get their packed values back."""
    layouts, mode = task.get('sources'), task.get('fingerprint')
    if not layouts:
        return
    wanted = {'left': [], 'right': []}
    for key, entry in left.items():
        other = right.get(key)
        if other is None:
            continue
        a, b = entry[0], other[0]
        if invalid(a) or invalid(b):
            continue
        if a[:1] == REF and b[:1] == REF and a[2:18] == b[2:18] and (mode == 'canonical' or a[1:2] == b[1:2]):
            entry[0] = other[0] = SAME
            continue
        if a[:1] == REF:
            wanted['left'].append(entry)
        if b[:1] == REF:
            wanted['right'].append(other)
    for side in ('left', 'right'):
        sample = samples.get(side)
        refs = [entry[0] for entry in wanted[side]] + [payload for payload in (sample[1] if sample else ()) if payload[:1] == REF]
        if not refs:
            continue
        rows = _fetch(layouts[side], mode, refs)
        for entry in wanted[side]:
            entry[0] = rows[entry[0]]
        if sample:
            sample[1][:] = [rows.get(payload, payload) for payload in sample[1]]


def _partition_range(task):
    """Phase 2: pack one byte or row-group range and spill its rows grouped by key range."""
    if task.get('fingerprint') and task['kind'] == 'text' and not task['whole']:
        try:
            return _read_fingerprints(task)
        except _NotSimple:
            pass  # spill files from the attempt are overwritten or left unused; the text reader takes over
    side, path = task['side'], task['path']
    bounds, excluded = task['bounds'], task['excluded']
    allow_empty, cancel_file = task['allow_empty'], task['cancel_file']
    if task['kind'] == 'parquet':
        from parquet_input import PackedRows
        reader = PackedRows(path, task['projection'], task['key_positions'], 0, task['batch_rows'],
                            task['memory_mb'], task['native_batch_bytes'], row_groups=task['groups'])
    else:
        from csv_input import PackedRows
        options = SimpleNamespace(dialects={side: task['dialect']}, io_buffer_bytes=4 * MIB, range_start=None if task['whole'] else task['start'])
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
        pending = 0
        return _write_spill(target, buffers)

    count = excluded_count = consumed = 0
    text = task['kind'] == 'text'
    bisect_right = bisect.bisect_right
    pack = FRAME.pack
    with reader as rows:
        for key_values, payload in rows:
            count += 1
            if text:
                consumed += len(payload) + 1
                if count & 127 == 0:
                    _report(task, consumed)
            if count & 4095 == 0:
                check_cancel(cancel_file)
            key = encode_key(key_values)
            if key in excluded:
                excluded_count += 1
                continue
            if not allow_empty and any(value is None or value == '' for value in key_values):
                location = f'record {count}' if task['whole'] else f"byte range {task['start']}, record {count} within range"
                key_values, payload = blank_key(location, path, [task['names'][i] for i in task['key_positions']], key_values)
                key = encode_key(key_values)
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
            if capture and not invalid(previous[0]) and not invalid(payload):
                if occurrences == 2 and (sample is None or key < sample[0]):
                    sample = (key, [previous[0], payload])
                elif sample is not None and sample[0] == key and len(sample[1]) < 3:
                    sample[1].append(payload)
            bad = combine_invalid(previous[0], payload, occurrences)
            if bad is not None:
                previous[0] = bad
            elif policy == 'last':
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
    _resolve(left, right, dict(left=left_sample, right=right_sample), task)
    out = Path(task['out'])
    stats = dict(matched_keys=0, equal_rows=0, changed_rows=0, changed_cells=0, left_only=0, right_only=0,
                 override_equivalent_cells=0, rule_equivalent_cells=0, left_duplicate_keys=0,
                 left_duplicate_rows_skipped=0, right_duplicate_keys=0, right_duplicate_rows_skipped=0)
    stats.update({name: 0 for name in SKIP_COUNTERS})
    columns = {name: 0 for name in task['columns']}
    from mismatch_patterns import Tracker
    tracker = Tracker()
    names = ('skipped_keys', 'differences', 'left_only', 'right_only', 'matched_keys', 'raw_differences', 'dup_left', 'dup_right')
    streams = {name: open(out / f'{name}-{part:05d}.csv', 'w', encoding='utf-8', newline='', buffering=4 * MIB) for name in names}
    try:
        writers = {name: csv.writer(stream) for name, stream in streams.items()}
        for side, rows in (('left', left), ('right', right)):
            for key in sorted(key for key, entry in rows.items() if entry[1] > 1 and not invalid(entry[0])):
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
                            cancel_file=task['cancel_file'], patterns=tracker)
    finally:
        for stream in streams.values():
            stream.close()
    # This key range's mismatch patterns; merged in key order when the ranges are assembled.
    patterns_file = out / f'patterns-{part:05d}.json'
    patterns_file.with_suffix('.part').write_text(json.dumps(tracker.state(), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    patterns_file.with_suffix('.part').replace(patterns_file)
    samples = {name: (sample[0], sample[1], rows[sample[0]][1]) for name, sample, rows in (('left', left_sample, left), ('right', right_sample, right)) if sample and not invalid(rows[sample[0]][0])}
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


def _wait_all(pool, futures, args, on_done, on_tick=None):
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
        if on_tick is not None:
            on_tick()
    return outcome


def _submit_side(pool, source, common):
    tasks = _range_tasks(source, common)
    return tasks, [pool.submit(_partition_range, task) for task in tasks]


def _partition_side(pool, source, common, args, progress, totals, submitted, on_read=None, on_tick=None):
    """Wait for one side's ranges and accept them in file order.

    A range is accepted only when it starts where the previous accepted range ended. A range that
    fails with a CSV error ended inside a quoted line break, so it is merged with the next range and
    re-read; record-numbered errors are genuine and reported with their global record number.
    """
    side = source['side']

    def reported(result):
        if on_read is not None:
            on_read(result)
        totals[side] += result['rows']
        progress(args, f'Indexing {side} file', stage='index', engine='partitioned', rows=totals[side], status='ongoing')

    tasks, futures = submitted
    outcome = _wait_all(pool, futures, args, reported, on_tick)
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
            task = dict(tasks[index], index=rerun, start=cursor, end=tasks[last]['end'], slot=None)
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

    def __init__(self, work, fingerprint):
        self.work, self.fingerprint = work, fingerprint
        self.plan, self.reads, self.sides, self.joins = None, {}, {}, {}
        self.lock = threading.Lock()
        manifest = work / 'manifest.json'
        try:
            saved = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = None
        if saved and saved.get('fingerprint') == fingerprint:
            self.plan = _decode(saved['plan'])
            try:
                lines = (work / 'progress.jsonl').read_text(encoding='utf-8').splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    entry = _decode(json.loads(line))
                except ValueError:
                    break  # the last line of an interrupted write
                if entry['kind'] == 'read' and all(Path(path).exists() for path, _ in entry['result']['spills']):
                    self.reads[entry['id']] = entry['result']
                elif entry['kind'] == 'side' and all(Path(path).exists() for result in entry['result']['ranges'] for path, _ in result['spills']):
                    # Every range of one file, accepted in file order (including ranges re-read across quoted line breaks).
                    self.sides[entry['id']] = entry['result']['ranges']
                elif entry['kind'] == 'join':
                    self.joins[entry['result']['part']] = entry['result']
        else:
            shutil.rmtree(work, ignore_errors=True)
        (work / 'spill').mkdir(parents=True, exist_ok=True)
        (work / 'parts').mkdir(exist_ok=True)

    @property
    def resumed(self):
        return bool(self.reads or self.sides or self.joins)

    def start(self, plan):
        self.plan = plan
        from file_io import atomic_write_text
        atomic_write_text(self.work / 'manifest.json', json.dumps(dict(fingerprint=self.fingerprint, plan=_encode(plan))))
        (self.work / 'progress.jsonl').write_text('', encoding='utf-8')

    def record(self, kind, identity, result):
        if kind == 'side':
            result = dict(ranges=result)
        line = json.dumps(_encode(dict(kind=kind, id=identity, result=result))) + '\n'
        with self.lock, open(self.work / 'progress.jsonl', 'a', encoding='utf-8') as stream:
            stream.write(line)

    def joined_files_exist(self, part):
        return all((self.work / 'parts' / f'{name}-{part:05d}.csv').exists()
                   for name in ('differences', 'left_only', 'right_only', 'matched_keys', 'raw_differences', 'dup_left', 'dup_right')) \
            and (self.work / 'parts' / f'patterns-{part:05d}.json').exists()


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
    workers = worker_count(args)
    # A checkpointed run keeps its work folder until it succeeds, so an interrupted run can resume.
    checkpointed = bool(getattr(args, 'checkpoint_dir', None))
    work = Path(temp) / (WORK_DIR if checkpointed else f'partitioned-{os.getpid()}')
    journal = None
    if checkpointed:
        journal = _Journal(work, dict(version=2, range_bytes=RANGE_BYTES, canonical=list(canonical), keys=list(args.keys)))
    else:
        shutil.rmtree(work, ignore_errors=True)
        (work / 'spill').mkdir(parents=True)
        (work / 'parts').mkdir()
    succeeded = False
    try:
        budget = max(64, float(getattr(args, 'memory_mb', 0) or 1024)) * MIB
        if journal is not None and journal.plan is not None:
            # The saved plan is reused as is, so recorded ranges line up even if workers or memory changed.
            sources, bounds = journal.plan['sources'], journal.plan['bounds']
            mode, reason = journal.plan.get('fingerprint'), None
            digest = journal.plan.get('digest', 'sha256')
            total_bytes = sum(source['size'] for source in sources)
            progress(args, 'Resuming parallel comparison', stage='index', engine='partitioned', workers=workers,
                     key_ranges=len(bounds) + 1, reused_reads=len(journal.reads), reused_key_ranges=len(journal.joins))
        else:
            sources = [_describe(args, 'left', args.left, left_names, canonical),
                       _describe(args, 'right', args.right, right_names, canonical)]
            total_bytes = sum(source['size'] for source in sources)
            # A key range of both sides must fit one worker's share of memory (packed rows plus dict overhead).
            partitions = max(workers * 2, min(4096, -(-int(total_bytes * 2.5) // int(max(64 * MIB, budget / workers)))))
            sample_count = min(20000, partitions * 64)
            progress(args, 'Planning parallel comparison', stage='index', engine='partitioned', workers=workers,
                     key_ranges=partitions, input_mb=round(total_bytes / MIB))
            keys = []
            for source in sources:
                if source['kind'] == 'parquet':
                    keys += sample_parquet_keys(source['path'], source['key_columns'], sample_count // 2)
                elif source['start'] is not None:
                    keys += sample_text_keys(source['path'], len(source['names']), source['key_positions'],
                                             source['dialect']['delimiter'], source['dialect']['encoding'],
                                             source['start'], sample_count // 2)
            bounds = choose_bounds(keys, partitions)
            mode, reason = fingerprint_choice(args, sources)
            digest = choose_digest() if mode else 'sha256'
            if journal is not None:
                journal.start(dict(sources=sources, bounds=bounds, fingerprint=mode, digest=digest))
        if mode:
            progress(args, 'Fingerprint lookup: equal rows are matched by key and fingerprint; only rows that differ are read again',
                     stage='index', engine='partitioned', fingerprint=mode, hash=digest)
        elif reason:
            progress(args, f'Fingerprint lookup not used ({reason}); full rows are copied to scratch files',
                     stage='index', engine='partitioned')
        common = dict(fingerprint=mode, digest=digest, bounds=bounds, excluded=args.excluded_keys, allow_empty=args.allow_empty_keys,
                      cancel_file=getattr(args, 'cancel_file', None), spill_dir=str(work / 'spill'),
                      batch_rows=getattr(args, 'read_batch_size', 100000),
                      memory_mb=max(16, int(budget / MIB / workers)), native_batch_bytes=getattr(args, 'native_batch_bytes', None))
        context = multiprocessing.get_context('spawn')
        totals = dict(left=0, right=0)
        plans = {source['side']: _range_tasks(source, common) for source in sources}
        slots = [task for tasks in plans.values() for task in tasks]
        for slot, task in enumerate(slots):
            task['slot'] = slot
        shared = context.Array('q', max(1, len(slots)), lock=False)
        with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_init_worker,
                                 initargs=(getattr(args, 'max_field_mb', 64), shared)) as pool:
            phase = time.monotonic()
            # Both files are read side by side: ranges are queued alternately, in proportion to each file's size.
            order = sorted(((index / len(tasks), side, index) for side, tasks in plans.items() for index in range(len(tasks))))
            futures_by_side = {side: [None] * len(tasks) for side, tasks in plans.items()}
            finished = journal.sides if journal is not None else {}
            for _, side, index in order:
                task = plans[side][index]
                cached = journal.reads.get(_read_id(task)) if journal is not None else None
                if side in finished:
                    cached = dict(side=side, rows=0, spills=[])  # already read before an interruption
                if cached is not None:
                    futures_by_side[side][index] = _done(cached)
                    continue
                future = pool.submit(_partition_range, task)
                if journal is not None:
                    future.add_done_callback(lambda done, task=task: done.exception() is None and journal.record('read', _read_id(task), done.result()))
                futures_by_side[side][index] = future
            submitted = {side: (plans[side], futures_by_side[side]) for side in plans}
            # Read progress for the console and pipeline: each finished range counts with its share of the input
            # (bytes for text; Parquet slices share their file's size equally).
            weights = {}
            for source in sources:
                tasks = plans[source['side']]
                for task in tasks:
                    whole = task['kind'] != 'text' or task['whole']
                    weights[(source['side'], task['index'])] = source['size'] / len(tasks) if whole else task['end'] - task['start']
            from compare import ReadProgress
            reading = ReadProgress(args, sum(weights.values()), 'Reading and indexing files', stage='index', engine='partitioned')
            counted, read_bytes = set(), [0]

            def read_done(result):
                side = result.get('side')
                keys = [key for key in weights if key[0] == side] if side in finished else [(side, result.get('index'))]
                for key in keys:
                    if key in weights and key not in counted:
                        counted.add(key)
                        read_bytes[0] += weights[key]
                reading.update(read_bytes[0])

            def read_tick():
                # Finished ranges plus what the running ones have read so far.
                partial = sum(min(shared[task['slot']], weights[key]) for side, tasks in plans.items() for task in tasks
                              for key in [(side, task['index'])] if key not in counted)
                reading.update(read_bytes[0] + partial)

            for side in finished:
                read_done(dict(side=side))  # files read before an interruption count from the start
            read = {}
            for source in sources:
                side = source['side']
                if side in finished:
                    read[side] = finished[side]
                    continue
                read[side] = _partition_side(pool, source, common, args, progress, totals, submitted[side], read_done, read_tick)
                if journal is not None:
                    journal.record('side', side, read[side])
            reading.update(read_bytes[0], final=True)
            reused_all = len(finished) == 2
            for side in ('left', 'right'):
                stats[side + '_rows'] = sum(result['rows'] for result in read[side])
                stats[side + '_excluded_rows'] = sum(result['excluded'] for result in read[side])
                progress(args, f'Indexing {side} file complete', stage='index', engine='partitioned',
                         rows=stats[side + '_rows'], excluded=stats[side + '_excluded_rows'], status='completed')
            seconds = time.monotonic() - phase
            progress(args, 'Read and partition complete', stage='index', engine='partitioned', status='completed',
                     elapsed_seconds=round(seconds, 3), mb_per_second=round(total_bytes / MIB / max(seconds, 1e-3)))
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
            futures = []
            for part in range(len(bounds) + 1):
                # A joined key range is reused only when its inputs were read from the same recorded ranges.
                cached = journal.joins.get(part) if journal is not None and reused_all else None
                if cached is not None and journal.joined_files_exist(part):
                    futures.append(_done(cached))
                    continue
                future = pool.submit(_join_partition, dict(part=part, left=segments['left'][part], right=segments['right'][part],
                                                           policy=args.duplicate_policy, capture=capture, out=str(work / 'parts'),
                                                           columns=names, column_count=len(canonical), policies=policies,
                                                           cancel_file=common['cancel_file'], fingerprint=mode,
                                                           sources={source['side']: _layout(source, digest) for source in sources} if mode else None))
                if journal is not None:
                    future.add_done_callback(lambda done: done.exception() is None and journal.record('join', done.result()['part'], done.result()))
                futures.append(future)
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
        from mismatch_patterns import Tracker, save as save_patterns
        patterns = Tracker()
        for part in range(len(results)):
            patterns.merge(json.loads((work / 'parts' / f'patterns-{part:05d}.json').read_text(encoding='utf-8')))
        save_patterns(patterns, out)
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
        succeeded = True
    finally:
        if succeeded or journal is None:
            shutil.rmtree(work, ignore_errors=True)


def _assemble(out, parts, count, keys):
    """Concatenate partial outputs in key-range order under the same headers as the other engines."""
    layout = [('skipped_keys', SKIP_FIELDS, ['skipped_keys']), ('differences', ['key_json', 'column', 'left_value', 'right_value'], ['differences']),
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
