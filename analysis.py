"""On-demand, disk-backed indexes for exact key/column mismatch analysis."""
from contextlib import closing
import csv
import json
import sqlite3
import re
import hashlib
import itertools
from pathlib import Path


def classify_pattern(left,right):
    from decimal import Decimal, DecimalException
    if left=='':return 'Blank → value'
    if right=='':return 'Value → blank'
    if left.strip()==right.strip():return 'Whitespace only'
    if left.casefold()==right.casefold():return 'Case only'
    if left.strip().casefold()==right.strip().casefold():return 'Case and whitespace'
    if len(left)<=1000 and len(right)<=1000:
        try:
            a,b=Decimal(left),Decimal(right)
            if a.is_finite() and b.is_finite() and a==b:return 'Numeric formatting'
        except DecimalException:pass
    return 'Other value change'


# Index format: 4 stores 16-byte value hashes, pattern rows grouped after loading, and precomputed totals.
INDEX_VERSION = 4
BATCH = 20000


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).digest()[:16]


def _canonical_keys(rows):
    """Comparison engines write compact JSON keys already; normalize only if a sample shows otherwise."""
    rows = iter(rows)
    head = list(itertools.islice(rows, 1000))
    compact = all(row[0] == json.dumps(json.loads(row[0]), ensure_ascii=False, separators=(',', ':')) for row in head)
    for row in itertools.chain(head, rows):
        yield row if compact else [json.dumps(json.loads(row[0]), ensure_ascii=False, separators=(',', ':'))] + row[1:]


def build_index(report, target, notify, cancelled=lambda:False, total_cells=None, progress=None):
    """Build the drawer's disk index. The file is temporary until complete, so durability pragmas are off.

    ``notify(message)`` reports each phase; the optional ``progress(message, done, total)`` also receives counts.
    """
    report_progress = progress or (lambda message, done, total: notify(message))
    with closing(sqlite3.connect(target)) as db:
        db.set_progress_handler(lambda: 1 if cancelled() else 0,100000)
        db.create_function('classify',2,classify_pattern,deterministic=True)
        db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144; PRAGMA temp_store=FILE; PRAGMA locking_mode=EXCLUSIVE;'
                         'CREATE TABLE cells(key_json TEXT, column_name TEXT, left_value TEXT, right_value TEXT, left_hash BLOB, right_hash BLOB);'
                         'CREATE TABLE keys(key_json TEXT PRIMARY KEY,status TEXT,changed_cells INTEGER);'
                         'CREATE TABLE patterns(column_name TEXT,pattern TEXT,left_hash BLOB,right_hash BLOB,left_value TEXT,right_value TEXT,left_length INTEGER,right_length INTEGER,occurrences INTEGER);'
                         'CREATE TABLE totals(name TEXT PRIMARY KEY,value INTEGER);')
        with (report/'differences.csv').open(encoding='utf-8',newline='') as stream:
            rows=csv.reader(stream);next(rows,None);rows=_canonical_keys(rows)
            done=0
            while True:
                batch=[(row[0],row[1],row[2],row[3],_digest(row[2]),_digest(row[3])) for row in itertools.islice(rows,BATCH)]
                if not batch:break
                db.executemany('INSERT INTO cells VALUES (?,?,?,?,?,?)',batch);done+=len(batch)
                report_progress(f'Indexing mismatches: {done:,} of {total_cells:,} cells' if total_cells else f'Indexing mismatches: {done:,} cells',done,total_cells)
        report_progress('Building key and column indexes…',done,total_cells)
        # differences.csv is already in key order, so these indexes build from presorted input.
        db.executescript('CREATE INDEX cells_key ON cells(key_json,column_name);'
                         'CREATE INDEX cells_column ON cells(column_name,key_json);'
                         'CREATE INDEX cells_pattern ON cells(column_name,left_hash,right_hash,key_json);')
        report_progress('Grouping value-change patterns…',done,total_cells)
        # Every cell in a (column, left hash, right hash) group has the same values, so one is classified per group.
        db.executescript('INSERT INTO patterns SELECT column_name,classify(left_value,right_value),left_hash,right_hash,substr(left_value,1,1000),substr(right_value,1,1000),'
                         'length(left_value),length(right_value),count(*) FROM cells GROUP BY column_name,left_hash,right_hash;'
                         "INSERT INTO keys SELECT key_json,'changed',count(*) FROM cells GROUP BY key_json;")
        for kind in ('left_only','right_only'):
            with (report/(kind+'.csv')).open(encoding='utf-8',newline='') as stream:
                rows=csv.reader(stream);next(rows,None)
                while True:
                    batch=[(json.dumps(row,ensure_ascii=False,separators=(',',':')),kind) for row in itertools.islice(rows,BATCH)]
                    if not batch:break
                    db.executemany('INSERT INTO keys VALUES (?,?,0)',batch)
        report_progress('Finishing indexes…',done,total_cells)
        db.executescript('CREATE INDEX patterns_rank ON patterns(occurrences DESC,column_name,pattern,left_hash,right_hash);'
                         'CREATE INDEX patterns_column ON patterns(column_name,occurrences DESC,pattern,left_hash,right_hash);'
                         "INSERT INTO totals SELECT 'cells',count(*) FROM cells;"
                         "INSERT INTO totals SELECT 'keys',count(*) FROM keys;"
                         "INSERT INTO totals SELECT 'patterns',count(*) FROM patterns;"
                         "INSERT INTO totals SELECT 'cells:'||column_name,count(*) FROM cells GROUP BY column_name;"
                         "INSERT INTO totals SELECT 'patterns:'||column_name,count(*) FROM patterns GROUP BY column_name;"
                         'CREATE TABLE categories AS SELECT column_name,pattern,sum(occurrences) AS occurrences FROM patterns GROUP BY column_name,pattern;')
        db.commit()


def query_index(path, mode, offset=0, column='', key=None, left_hash=None, right_hash=None):
    if mode not in ('keys','cells','patterns'): raise ValueError('Choose key or cell analysis')
    offset=max(0,offset)
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro',uri=True)) as db:
        db.execute('PRAGMA cache_size=-32768')
        def total(name):
            row=db.execute('SELECT value FROM totals WHERE name=?',(name,)).fetchone()
            return row[0] if row else 0
        if mode=='patterns':
            clause=' WHERE column_name=?' if column else ''
            params=[column] if column else []
            rows=db.execute('SELECT column_name,pattern,left_value,right_value,occurrences,left_length,right_length,lower(hex(left_hash)),lower(hex(right_hash)) FROM patterns'+clause+' ORDER BY occurrences DESC,column_name,pattern,left_hash,right_hash LIMIT 50 OFFSET ?',params+[offset]).fetchall()
            categories=db.execute('SELECT pattern,sum(occurrences) FROM categories'+clause+' GROUP BY pattern ORDER BY sum(occurrences) DESC,pattern',params).fetchall()
            return dict(rows=rows,total=total('patterns:'+column if column else 'patterns'),offset=offset,categories=categories,occurrences=sum(r[1] for r in categories))
        where=[];params=[]
        if key is not None:
            where.append('key_json=?');params.append(json.dumps(key,ensure_ascii=False,separators=(',',':')))
        if mode=='cells' and column:
            where.append('column_name=?');params.append(column)
        if left_hash is not None or right_hash is not None:
            if mode!='cells' or not column or not all(isinstance(h,str) and re.fullmatch(r'[0-9a-f]{32}',h) for h in (left_hash,right_hash)):raise ValueError('Select an exact value-change pattern within a column')
            where.extend(['left_hash=?','right_hash=?']);params.extend([bytes.fromhex(left_hash),bytes.fromhex(right_hash)])
        clause=' WHERE '+' AND '.join(where) if where else ''
        table='keys' if mode=='keys' else 'cells'
        # Unfiltered and single-column totals were counted once when the index was built.
        if key is None and left_hash is None:
            count=total(table if mode=='keys' or not column else 'cells:'+column)
        else:
            count=db.execute('SELECT count(*) FROM '+table+clause,params).fetchone()[0]
        fields='key_json,status,changed_cells' if mode=='keys' else 'key_json,column_name,substr(left_value,1,1000),substr(right_value,1,1000),length(left_value),length(right_value)'
        rows=db.execute('SELECT '+fields+' FROM '+table+clause+' ORDER BY key_json'+(',column_name' if mode=='cells' else '')+' LIMIT 50 OFFSET ?',params+[offset]).fetchall()
        status=db.execute('SELECT status FROM keys WHERE key_json=?',(json.dumps(key,ensure_ascii=False,separators=(',',':')),)).fetchone() if key is not None else None
        if sum(sum(len(str(v).encode('utf-8')) for v in row) for row in rows)>2*1024*1024:
            raise ValueError('These keys exceed the analysis preview size limit. Use the full CSV export.')
        return dict(rows=rows,total=count,offset=offset,key_status=status[0] if status else None)
