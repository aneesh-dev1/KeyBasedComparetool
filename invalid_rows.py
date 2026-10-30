"""Transport malformed-record evidence through the same key join as valid rows.

The reserved payload never reaches the value scanner. Keeping evidence with the
key makes exclusions symmetric, including duplicates, and checkpoint-safe.
"""
import json
import io
import struct

MARK = b'\xfe'
FIELDS = ['side', 'key_json', 'location', 'expected_fields', 'actual_fields', 'reason', 'action']
COUNTERS = ('skipped_keys', 'unidentified_records', 'malformed_records',
            'left_invalid_rows', 'right_invalid_rows', 'left_skipped_rows', 'right_skipped_rows')


def invalid(payload):
    return payload[:1] == MARK


def _pack(evidence):
    return MARK + struct.pack('>Q', 1) + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'


def row_count(payload):
    return struct.unpack_from('>Q', payload, 1)[0]


def evidence_rows(payload):
    stream = io.BytesIO(payload)
    stream.seek(9)
    for line in stream:
        yield json.loads(line)


def record(row, width, positions, location, source):
    identifiable = all(i < len(row) and row[i] not in ('', None) for i in positions)
    key = tuple(row[i] for i in positions) if identifiable else (
        {'invalid_source': str(source), 'location': location},)
    delta = width - len(row)
    evidence = dict(key=list(key) if identifiable else None, location=location, expected=width,
                    actual=len(row), reason=f'{abs(delta)} fields ' + ('missing' if delta > 0 else 'extra'))
    return key, _pack(evidence)


def blank_key(location, source, names=(), values=()):
    """A well-formed record with a blank key value. Its counterpart cannot be identified, so it is audited as
    "key unavailable" and kept out of the join, like an unidentifiable malformed record."""
    blank = [name for name, value in zip(names, values) if value in ('', None)]
    evidence = dict(key=None, location=location, expected='', actual='',
                    reason='Blank key value' + (f" in {', '.join(blank)}" if blank else ''))
    return ({'invalid_source': str(source), 'location': location},), _pack(evidence)


def combine(previous, payload, occurrences):
    """Malformed occurrences take precedence over either duplicate-selection policy."""
    if not (invalid(previous) or invalid(payload)):
        return None
    # Grow the audit buffer in place, rather than decoding and reserializing every
    # preceding malformed occurrence of a highly duplicated key (quadratic work).
    merged = previous if isinstance(previous, bytearray) else bytearray(previous) if invalid(previous) else bytearray(MARK + b'\0' * 8)
    if invalid(payload):
        merged.extend(payload[9:])
    struct.pack_into('>Q', merged, 1, occurrences)
    return merged


def filter_pairs(pairs, writers, stats, cancel_file=None):
    from task_control import check_cancel
    for index, (a, b) in enumerate(pairs):
        if index % 1024 == 0:
            check_cancel(cancel_file)
        if not ((a and invalid(a[1])) or (b and invalid(b[1]))):
            yield a, b
            continue
        identified = False
        for side, item in (('left', a), ('right', b)):
            if item is None:
                continue
            malformed = invalid(item[1])
            stats[side + '_skipped_rows'] = stats.get(side + '_skipped_rows', 0) + (row_count(item[1]) if malformed else 1)
            for entry in evidence_rows(item[1]) if malformed else ():
                key = entry['key']
                identified |= key is not None
                stats['malformed_records'] = stats.get('malformed_records', 0) + 1
                stats[side + '_invalid_rows'] = stats.get(side + '_invalid_rows', 0) + 1
                if key is None:
                    stats['unidentified_records'] = stats.get('unidentified_records', 0) + 1
                writers['skipped_keys'].writerow([
                    side, json.dumps(key, ensure_ascii=False) if key is not None else '',
                    entry['location'], entry['expected'], entry['actual'], entry['reason'],
                    'Key excluded from both files' if key is not None else 'Record excluded; key unavailable; counterpart cannot be identified'])
        stats['skipped_keys'] = stats.get('skipped_keys', 0) + int(identified)
