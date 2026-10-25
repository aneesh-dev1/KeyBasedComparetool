"""What changed between two runs of the same project. Reads only the two runs' report files, never their sources.

Column level comes straight from the two summaries. Cell level compares the runs' differences.csv files by
(key, column): a mismatch in the later run that the earlier run did not have is new, one that disappeared is
resolved, and one in both is either unchanged or has different values. The result is kept in a small SQLite file
inside the later run's folder, so it is built once and removed with that run.
"""
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

VERSION = 1
STATUSES = ('new', 'resolved', 'changed', 'same')
PAGE = 50
SHOWN = 300  # characters of a value kept for display; equality always uses the full value
COUNTS = ('changed_cells', 'changed_rows', 'matched_keys', 'equal_rows', 'left_only', 'right_only')
_locks = {}
_locks_guard = threading.Lock()


def summary_changes(base, target):
    """Run-level counts and each column's mismatches in both runs, from the job summaries alone."""
    a, b = base.get('summary') or {}, target.get('summary') or {}
    counts = {name: dict(base=a.get(name), target=b.get(name)) for name in COUNTS}
    left, right = a.get('changed_cells_by_column', {}), b.get('changed_cells_by_column', {})
    columns = [dict(column=name, base=left.get(name), target=right.get(name)) for name in dict.fromkeys([*left, *right])]
    return counts, columns


def setting_changes(base, target):
    """Settings that make the two runs less comparable, in plain words. Different file names are expected."""
    changes = []
    def listed(name):
        return [str(item) for item in base.get(name) or []], [str(item) for item in target.get(name) or []]
    if base.get('keys') != target.get('keys'):
        changes.append(f"Key columns changed from {', '.join(base.get('keys') or []) or 'none'} to {', '.join(target.get('keys') or []) or 'none'}.")
    before, after = listed('ignore_columns')
    added, removed = [c for c in after if c not in before], [c for c in before if c not in after]
    if added or removed:
        parts = ([f"{len(added)} more ignored ({', '.join(added[:3])}{'…' if len(added) > 3 else ''})"] if added else []) + \
                ([f"{len(removed)} no longer ignored ({', '.join(removed[:3])}{'…' if len(removed) > 3 else ''})"] if removed else [])
        changes.append('Ignored columns changed: ' + '; '.join(parts) + '.')
    if (base.get('ignore_keys') or '').strip() != (target.get('ignore_keys') or '').strip() or sorted(base.get('ignore_container_ids') or []) != sorted(target.get('ignore_container_ids') or []):
        changes.append('Excluded keys changed.')
    if (base.get('comparison_rules') or []) != (target.get('comparison_rules') or []):
        changes.append(f"Comparison rules changed ({len(base.get('comparison_rules') or [])} → {len(target.get('comparison_rules') or [])} columns with rules).")
    if (base.get('value_overrides') or []) != (target.get('value_overrides') or []):
        changes.append(f"Value overrides changed ({len(base.get('value_overrides') or [])} → {len(target.get('value_overrides') or [])}).")
    if (base.get('duplicate_policy') or 'first') != (target.get('duplicate_policy') or 'first'):
        changes.append('Duplicate keys now keep the ' + (target.get('duplicate_policy') or 'first') + ' row.')
    return changes


def _lock(path):
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


def _load(db, table, report):
    csv.field_size_limit(64 * 1024 * 1024)
    db.execute(f'CREATE TEMP TABLE {table}(k TEXT NOT NULL, c TEXT NOT NULL, l TEXT, r TEXT, PRIMARY KEY(k, c)) WITHOUT ROWID')
    with (report / 'differences.csv').open(encoding='utf-8', newline='') as stream:
        reader = csv.reader(stream)
        next(reader, None)
        batch = []
        for row in reader:
            batch.append(row[:4])
            if len(batch) == 10000:
                db.executemany(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)', batch)
                batch.clear()
        db.executemany(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)', batch)


def build(base_report, target_report, path):
    """Write the cell-level comparison to ``path`` (atomically), unless a current one is already there."""
    path = Path(path)
    with _lock(path):
        if current(path):
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.part')
        temporary.unlink(missing_ok=True)
        try:
            with closing(sqlite3.connect(temporary)) as db:
                db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE; PRAGMA cache_size=-16384;')
                _load(db, 'a', Path(base_report))
                _load(db, 'b', Path(target_report))
                db.executescript(f'''
                    CREATE TABLE cells(k TEXT, c TEXT, status TEXT, al TEXT, ar TEXT, bl TEXT, br TEXT);
                    INSERT INTO cells SELECT b.k, b.c,
                        CASE WHEN a.k IS NULL THEN 'new' WHEN a.l IS b.l AND a.r IS b.r THEN 'same' ELSE 'changed' END,
                        substr(a.l,1,{SHOWN}), substr(a.r,1,{SHOWN}), substr(b.l,1,{SHOWN}), substr(b.r,1,{SHOWN})
                        FROM temp.b LEFT JOIN temp.a ON a.k=b.k AND a.c=b.c;
                    INSERT INTO cells SELECT a.k, a.c, 'resolved', substr(a.l,1,{SHOWN}), substr(a.r,1,{SHOWN}), NULL, NULL
                        FROM temp.a WHERE NOT EXISTS (SELECT 1 FROM temp.b WHERE b.k=a.k AND b.c=a.c);
                    CREATE INDEX cells_by_key ON cells(status, k, c);
                    CREATE INDEX cells_by_column ON cells(status, c, k);
                    CREATE TABLE column_counts AS SELECT c, status, count(*) AS n FROM cells GROUP BY c, status;
                    CREATE TABLE key_counts AS SELECT
                        (SELECT count(*) FROM (SELECT DISTINCT k FROM temp.b) WHERE k NOT IN (SELECT k FROM temp.a)) AS new_keys,
                        (SELECT count(*) FROM (SELECT DISTINCT k FROM temp.a) WHERE k NOT IN (SELECT k FROM temp.b)) AS resolved_keys;
                    CREATE TABLE meta(version INTEGER);
                    INSERT INTO meta VALUES({VERSION});
                ''')
                db.commit()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def current(path):
    path = Path(path)
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            return db.execute('SELECT version FROM meta').fetchone()[0] == VERSION
    except sqlite3.Error:
        return False


def cells(path, status='new', column='', offset=0):
    """Totals by status and column, plus one page of cells with the given status (and column, when given)."""
    if status not in STATUSES:
        raise ValueError('Choose new, resolved, changed or same')
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        by_column = {}
        for name, kind, count in db.execute('SELECT c, status, n FROM column_counts'):
            by_column.setdefault(name, dict.fromkeys(STATUSES, 0))[kind] = count
        totals = {kind: sum(item[kind] for item in by_column.values()) for kind in STATUSES}
        new_keys, resolved_keys = db.execute('SELECT new_keys, resolved_keys FROM key_counts').fetchone()
        where, values = 'status=?', [status]
        if column:
            where, values = where + ' AND c=?', values + [column]
        order = 'c, k' if column else 'k, c'
        total = by_column.get(column, {}).get(status, 0) if column else totals[status]
        rows = db.execute(f'SELECT k, c, al, ar, bl, br FROM cells WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?', values + [PAGE, offset]).fetchall()
    page = [dict(key=json.loads(k), column=c, base=None if status == 'new' else [al, ar], target=None if status == 'resolved' else [bl, br]) for k, c, al, ar, bl, br in rows]
    return dict(totals=totals, by_column=by_column, new_keys=new_keys, resolved_keys=resolved_keys,
                status=status, column=column, offset=offset, total=total, page_size=PAGE, cells=page)
