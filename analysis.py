"""On-demand, disk-backed indexes for exact key/column mismatch analysis."""
from contextlib import closing
import csv
import json
import sqlite3
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


def build_index(report, target, notify, cancelled=lambda:False):
    with closing(sqlite3.connect(target)) as db:
        db.set_progress_handler(lambda: 1 if cancelled() else 0,10000)
        db.executescript('PRAGMA cache_size=-8192; PRAGMA temp_store=FILE; CREATE TABLE cells(key_json TEXT, column_name TEXT, left_value TEXT, right_value TEXT); CREATE TABLE keys(key_json TEXT PRIMARY KEY,status TEXT,changed_cells INTEGER);')
        db.execute('CREATE TABLE patterns(column_name TEXT,pattern TEXT,left_hash TEXT,right_hash TEXT,left_value TEXT,right_value TEXT,left_length INTEGER,right_length INTEGER,occurrences INTEGER,PRIMARY KEY(column_name,pattern,left_hash,right_hash))')
        import hashlib
        with (report/'differences.csv').open(encoding='utf-8',newline='') as stream:
            for i,row in enumerate(csv.DictReader(stream),1):
                key=json.dumps(json.loads(row['key_json']),ensure_ascii=False,separators=(',',':'))
                db.execute('INSERT INTO cells VALUES (?,?,?,?)',(key,row['column'],row['left_value'],row['right_value']))
                left,right=row['left_value'],row['right_value']
                db.execute('INSERT INTO patterns VALUES (?,?,?,?,?,?,?,?,1) ON CONFLICT(column_name,pattern,left_hash,right_hash) DO UPDATE SET occurrences=occurrences+1',(row['column'],classify_pattern(left,right),hashlib.sha256(left.encode('utf-8')).hexdigest(),hashlib.sha256(right.encode('utf-8')).hexdigest(),left[:1000],right[:1000],len(left),len(right)))
                if i%10000==0:
                    db.commit();notify(f'Indexing mismatches: {i:,} cells')
        notify('Building key and column indexes…')
        db.executescript('CREATE INDEX cells_key ON cells(key_json,column_name); CREATE INDEX cells_column ON cells(column_name,key_json); INSERT INTO keys SELECT key_json,\'changed\',count(*) FROM cells GROUP BY key_json;')
        for kind in ('left_only','right_only'):
            with (report/(kind+'.csv')).open(encoding='utf-8',newline='') as stream:
                rows=csv.reader(stream);next(rows)
                for i,row in enumerate(rows,1):
                    key=json.dumps(row,ensure_ascii=False,separators=(',',':'))
                    db.execute('INSERT INTO keys VALUES (?,?,0)',(key,kind))
                    if i%10000==0:
                        db.commit();notify(f'Indexing {kind} keys: {i:,}')
        db.execute('CREATE INDEX patterns_rank ON patterns(occurrences DESC,column_name)')
        db.execute('CREATE INDEX patterns_column ON patterns(column_name,occurrences DESC)')
        db.commit()


def query_index(path, mode, offset=0, column='', key=None):
    if mode not in ('keys','cells','patterns'): raise ValueError('Choose key or cell analysis')
    offset=max(0,offset)
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro',uri=True)) as db:
        db.execute('PRAGMA cache_size=-8192')
        if mode=='patterns':
            clause=' WHERE column_name=?' if column else ''
            params=[column] if column else []
            total=db.execute('SELECT count(*) FROM patterns'+clause,params).fetchone()[0]
            rows=db.execute('SELECT column_name,pattern,left_value,right_value,occurrences,left_length,right_length FROM patterns'+clause+' ORDER BY occurrences DESC,column_name,pattern,left_hash,right_hash LIMIT 50 OFFSET ?',params+[offset]).fetchall()
            categories=db.execute('SELECT pattern,sum(occurrences) FROM patterns'+clause+' GROUP BY pattern ORDER BY sum(occurrences) DESC',params).fetchall()
            return dict(rows=rows,total=total,offset=offset,categories=categories)
        where=[];params=[]
        if key is not None:
            where.append('key_json=?');params.append(json.dumps(key,ensure_ascii=False,separators=(',',':')))
        if mode=='cells' and column:
            where.append('column_name=?');params.append(column)
        clause=' WHERE '+' AND '.join(where) if where else ''
        table='keys' if mode=='keys' else 'cells'
        count=db.execute('SELECT count(*) FROM '+table+clause,params).fetchone()[0]
        fields='key_json,status,changed_cells' if mode=='keys' else 'key_json,column_name,substr(left_value,1,1000),substr(right_value,1,1000),length(left_value),length(right_value)'
        rows=db.execute('SELECT '+fields+' FROM '+table+clause+' ORDER BY key_json'+(',column_name' if mode=='cells' else '')+' LIMIT 50 OFFSET ?',params+[offset]).fetchall()
        status=db.execute('SELECT status FROM keys WHERE key_json=?',(json.dumps(key,ensure_ascii=False,separators=(',',':')),)).fetchone() if key is not None else None
        if sum(sum(len(str(v).encode('utf-8')) for v in row) for row in rows)>2*1024*1024:
            raise ValueError('These keys exceed the analysis preview size limit. Use the full CSV export.')
        return dict(rows=rows,total=count,offset=offset,key_status=status[0] if status else None)
