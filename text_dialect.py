"""Detect the dialect of a delimited text file (.csv, .dat, .txt, .tsv, .psv).

Detection reads only the first megabyte. It chooses a starting point the user can review and change
before comparing; values are always compared as exact text whichever dialect is chosen.
"""
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
import io
import re

TEXT_SUFFIXES = {'.csv', '.dat', '.txt', '.tsv', '.psv'}
ENCODINGS = ('utf-8-sig', 'utf-16', 'cp1252')
# Common data-file delimiters, in preference order for ties: comma, pipe, tab, semicolon,
# Ctrl-A (Hive/mainframe extracts), unit separator, tilde, caret.
CANDIDATES = (',', '|', '\t', ';', '\x01', '\x1f', '~', '^')
NAMES = {',': 'Comma', '|': 'Pipe', '\t': 'Tab', ';': 'Semicolon', '\x01': 'Ctrl-A', '\x1f': 'Unit separator', '~': 'Tilde', '^': 'Caret'}
SAMPLE_BYTES = 1024 * 1024
_NUMBER = re.compile(r'^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$')
_DATE = re.compile(r'^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?$')


def valid_delimiter(value):
    return isinstance(value, str) and len(value) == 1 and value not in ('\r', '\n', '\0', '"')


def delimiter_name(value):
    return NAMES.get(value, f'Custom ({value})')


def detect_encoding(sample):
    if sample.startswith(b'\xff\xfe') or sample.startswith(b'\xfe\xff'):
        return 'utf-16'
    try:
        # A multi-byte character cut by the sample boundary is not an error.
        sample.decode('utf-8-sig') if len(sample) < SAMPLE_BYTES else sample[:-4].decode('utf-8-sig')
        return 'utf-8-sig'
    except UnicodeDecodeError:
        return 'cp1252'


def _rows(text, delimiter, limit=60):
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True):
            rows.append(row)
            if len(rows) >= limit:
                break
    except csv.Error:
        pass
    return rows


def detect_delimiter(text):
    """The candidate giving the most consistent field count (at least two fields) across sample rows."""
    best, best_score = ',', None
    complete = text if '\n' not in text else text[:text.rfind('\n') + 1]
    for order, delimiter in enumerate(CANDIDATES):
        rows = [row for row in _rows(complete, delimiter) if row]
        if len(rows) < 1:
            continue
        counts = [len(row) for row in rows]
        width = max(set(counts), key=counts.count)
        if width < 2:
            continue
        consistency = counts.count(width) / len(counts)
        score = (round(consistency, 2), min(width, 2), -order)
        if consistency >= 0.8 and (best_score is None or score > best_score):
            best, best_score = delimiter, score
    return best


def _kind(value):
    value = value.strip()
    if not value:
        return 'empty'
    if _NUMBER.match(value):
        return 'number'
    if _DATE.match(value):
        return 'date'
    return 'text'


def detect_header(text, delimiter):
    """True when the first record looks like column names rather than data."""
    rows = [row for row in _rows(text, delimiter, 41) if row]
    if not rows:
        return True
    first, body = rows[0], [row for row in rows[1:] if len(row) == len(rows[0])]
    if any(not name.strip() for name in first) or len({name.strip().casefold() for name in first}) != len(first):
        return False
    if not body:
        return True
    votes = 0
    for index, name in enumerate(first):
        kinds = {_kind(row[index]) for row in body} - {'empty'}
        if len(kinds) != 1:
            continue
        kind = kinds.pop()
        if kind in ('number', 'date'):
            votes += 1 if _kind(name) != kind else -1
    # Column names are text; a first row with no evidence either way is treated as a header.
    return votes >= 0 and not all(_kind(name) in ('number', 'date') for name in first)


def detect(path, delimiter='auto', encoding='auto', header='auto'):
    """Resolve 'auto' settings for one file. Returns dict(delimiter, encoding, header, detected)."""
    with open(path, 'rb') as stream:
        sample = stream.read(SAMPLE_BYTES)
    detected = []
    if encoding == 'auto':
        encoding = detect_encoding(sample)
        detected.append('encoding')
    text = sample.decode('utf-16' if encoding == 'utf-16' else encoding, 'replace')
    if encoding == 'utf-8-sig' and text.startswith('﻿'):
        text = text[1:]
    if delimiter == 'auto':
        delimiter = detect_delimiter(text)
        detected.append('delimiter')
    if header == 'auto':
        has_header = detect_header(text, delimiter)
        detected.append('header')
    else:
        has_header = header in (True, 'yes')
    return dict(delimiter=delimiter, encoding=encoding, header=has_header, detected=detected)
