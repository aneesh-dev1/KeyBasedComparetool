"""Mismatch patterns counted while comparing, so reports never re-read differences.csv.

A pattern is one pair of differing values in one column (File 1 ``open`` against File 2 ``void``). The engine
hands every mismatching cell it writes to a ``Tracker``, which counts each pattern and keeps the first five keys
that have it. Each engine worker has its own tracker; their results are merged in key order.

Memory is bounded. A tracker follows at most ``PER_COLUMN`` distinct patterns per column and ``TOTAL`` in all;
cells of patterns it cannot follow are still counted per column (``other``) and the column is marked ``partial``,
because a pattern first seen after the limit can then be counted low. Columns with fewer distinct patterns than
the limit, which covers codes, statuses and flags, are always exact. differences.csv always holds every cell.
"""
import csv
import hashlib
import json
from pathlib import Path

VERSION = 1
FILE = 'mismatch-patterns.json'
PER_COLUMN = 5000      # distinct patterns followed per column, per tracker
TOTAL = 200000         # distinct patterns followed per tracker, all columns together
MERGED_PER_COLUMN = 20000
KEYS = 5               # first keys kept per pattern
LISTED = 1000          # patterns per column kept in the saved result (most common first)
KEYED = 50             # of those, the most common keep their sample keys
SHOWN = 1000           # characters of a value kept for display


def _id(value):
    """Short values identify themselves; long ones by length and digest, so memory stays small. The leading NUL
    keeps a digest from ever equalling a real short value."""
    return value if len(value) <= 256 else f"\x00{len(value)}:{hashlib.blake2b(value.encode('utf-8', 'surrogatepass'), digest_size=16).hexdigest()}"


def _shown(value):
    return value if len(value) <= SHOWN else value[:SHOWN] + ' … [truncated]'


class Tracker:
    def __init__(self, per_column=PER_COLUMN, total=TOTAL):
        self.per_column, self.total = per_column, total
        self.columns, self.other, self.partial, self.size = {}, {}, set(), 0

    def add(self, key_text, column, left, right):
        """Count one mismatching cell (values as written to differences.csv)."""
        table = self.columns.get(column)
        if table is None:
            table = self.columns[column] = {}
        ident = (left if len(left) <= 256 else _id(left), right if len(right) <= 256 else _id(right))
        entry = table.get(ident)
        if entry is not None:
            entry[2] += 1
            if len(entry[3]) < KEYS:
                entry[3].append(key_text)
            return
        if len(table) >= self.per_column or self.size >= self.total:
            self.other[column] = self.other.get(column, 0) + 1
            self.partial.add(column)
            return
        table[ident] = [_shown(left), _shown(right), 1, [key_text]]
        self.size += 1

    def merge(self, data):
        """Add another tracker's ``state()``; call in key order so the first keys stay first."""
        for column, item in data['columns'].items():
            table = self.columns.setdefault(column, {})
            for ident_left, ident_right, left, right, count, keys in item['patterns']:
                ident = (ident_left, ident_right)
                entry = table.get(ident)
                if entry is not None:
                    entry[2] += count
                    entry[3].extend(keys[:KEYS - len(entry[3])])
                elif len(table) < MERGED_PER_COLUMN:
                    table[ident] = [left, right, count, list(keys[:KEYS])]
                else:
                    self.other[column] = self.other.get(column, 0) + count
                    self.partial.add(column)
            if item['other']:
                self.other[column] = self.other.get(column, 0) + item['other']
            if item['partial']:
                self.partial.add(column)

    def state(self):
        """Everything followed, for merging (worker results)."""
        return dict(version=VERSION, columns={column: dict(
            patterns=[[ident[0], ident[1], *entry] for ident, entry in table.items()],
            other=self.other.get(column, 0), partial=column in self.partial) for column, table in self.columns.items()})

    def result(self):
        """The saved form: per column the most common patterns, with keys for the first ``KEYED``."""
        columns = {}
        for column in dict.fromkeys([*self.columns, *self.other]):
            ranked = sorted(self.columns.get(column, {}).values(), key=lambda entry: (-entry[2], entry[0], entry[1]))
            listed, rest = ranked[:LISTED], ranked[LISTED:]
            columns[column] = dict(
                patterns=[[left, right, count, [json.loads(key) for key in keys]] if rank < KEYED else [left, right, count]
                          for rank, (left, right, count, keys) in enumerate(listed)],
                more_patterns=len(rest), more_cells=sum(entry[2] for entry in rest) + self.other.get(column, 0),
                partial=column in self.partial)
        return dict(version=VERSION, columns=columns)


def save(tracker, folder):
    target = Path(folder) / FILE
    temporary = target.with_suffix('.part')
    temporary.write_text(json.dumps(tracker.result(), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(target)
    return target


def load(folder):
    try:
        data = json.loads((Path(folder) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if data.get('version') == VERSION else None


def from_differences(report, notify=lambda message: None):
    """For runs made before patterns were counted during the comparison: one pass over differences.csv."""
    csv.field_size_limit(64 * 1024 * 1024)
    tracker = Tracker(per_column=MERGED_PER_COLUMN, total=TOTAL * 4)
    with (Path(report) / 'differences.csv').open(encoding='utf-8', newline='') as stream:
        reader = csv.reader(stream)
        next(reader, None)
        add = tracker.add
        for index, row in enumerate(reader, 1):
            add(row[0], row[1], row[2], row[3])
            if index % 1000000 == 0:
                notify(f'Grouping mismatch patterns: {index:,} cells')
    return tracker
