"""Workspace-scoped project audit metadata. Never reads comparison source files."""
import json
from contextlib import contextmanager
import sqlite3
import time
import uuid
from pathlib import Path

STATUSES=('Unreviewed','In review','Approved','Changes requested')

def database(root,owner=None):
    folder=Path(root)/'workspaces'/owner if owner else Path(root)
    folder.mkdir(parents=True,exist_ok=True)
    return folder/'projects.sqlite'

def job_snapshot(job):
    summary=job.get('summary') or {}
    return dict(id=job['id'],kind='table',created=job['created'],finished=job.get('finished'),state=job['state'],
        source_available=True,title=' ↔ '.join(job['files'][s]['name'] for s in ('left','right')),
        formats=[job['files'][s].get('format','csv') for s in ('left','right')],
        changed_cells=summary.get('changed_cells'),changed_rows=summary.get('changed_rows'),matched_keys=summary.get('matched_keys'),
        equal_rows=summary.get('equal_rows'),left_only=summary.get('left_only'),right_only=summary.get('right_only'),
        changed_columns=sum(v>0 for v in summary.get('changed_cells_by_column',{}).values()),
        compared_columns=len(summary.get('changed_cells_by_column',{})),error=job.get('error','')[-1000:])

class Store:
    def __init__(self,path):
        self.path=Path(path)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,description TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),snapshot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'Unreviewed',note TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS records_project ON records(project_id,updated DESC);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),at REAL NOT NULL,message TEXT NOT NULL,record_id TEXT);
        CREATE INDEX IF NOT EXISTS events_project ON events(project_id,at DESC);
        ''')
        try:
            with db: yield db
        finally: db.close()
    def sync(self,snapshot):
        with self.connect() as db:
            previous=db.execute('SELECT * FROM records WHERE id=?',(snapshot['id'],)).fetchone()
            if previous and json.loads(previous['snapshot'])==snapshot:return
            now=time.time()
            db.execute('INSERT INTO records(id,snapshot,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot,updated=excluded.updated',(snapshot['id'],json.dumps(snapshot),now))
            if previous and previous['project_id'] and json.loads(previous['snapshot']).get('state')!=snapshot['state']:
                self.event(db,previous['project_id'],'Comparison '+snapshot['state'],snapshot['id'])
    def inherit(self,source,target):
        with self.connect() as db:
            row=db.execute('SELECT project_id FROM records WHERE id=?',(source,)).fetchone()
        if row and row['project_id']:
            self.update_record(dict(comparison_id=target,project_id=row['project_id'],status='Unreviewed'))
    def mark_removed(self,identity):
        with self.connect() as db:
            row=db.execute('SELECT * FROM records WHERE id=?',(identity,)).fetchone()
            if not row:return
            snapshot=json.loads(row['snapshot']);snapshot['source_available']=False
            db.execute('UPDATE records SET snapshot=?,updated=? WHERE id=?',(json.dumps(snapshot),time.time(),identity))
            if row['project_id']:self.event(db,row['project_id'],'Source files removed; audit summary retained',identity)
    def event(self,db,project,message,record=None):
        db.execute('INSERT INTO events(project_id,at,message,record_id) VALUES(?,?,?,?)',(project,time.time(),message,record))
    def create(self,config):
        name=config.get('name','');description=config.get('description','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Use a project name of 1–100 characters')
        if not isinstance(description,str) or len(description)>2000:raise ValueError('Project description must be under 2,000 characters')
        identity=uuid.uuid4().hex
        try:
            with self.connect() as db:
                if db.execute('SELECT count(*) FROM projects').fetchone()[0]>=500:raise ValueError('Maximum 500 projects per workspace')
                db.execute('INSERT INTO projects VALUES(?,?,?,?)',(identity,name.strip(),description,time.time()))
                self.event(db,identity,'Project created')
        except sqlite3.IntegrityError as error:raise ValueError('A project with this name already exists') from error
        return identity
    def update_record(self,config):
        identity=config.get('comparison_id');project=config.get('project_id');status=config.get('status','Unreviewed');note=config.get('note','');reviewer=config.get('reviewer','')
        if status not in STATUSES:raise ValueError('Choose a valid audit status')
        if not isinstance(note,str) or len(note)>2000 or not isinstance(reviewer,str) or len(reviewer)>100:raise ValueError('Use a short reviewer name and a note under 2,000 characters')
        with self.connect() as db:
            current=db.execute('SELECT * FROM records WHERE id=?',(identity,)).fetchone()
            if not current:raise ValueError('Comparison is not available in this workspace')
            if project and not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise ValueError('Project not found in this workspace')
            if status=='Approved' and json.loads(current['snapshot'])['state']!='complete':raise ValueError('Complete the comparison before approving the audit')
            if current['project_id'] and current['project_id']!=project:self.event(db,current['project_id'],'Comparison detached',identity)
            if project:
                if current['project_id']!=project:self.event(db,project,'Comparison attached',identity)
                if (current['status'],current['note'],current['reviewer'])!=(status,note,reviewer):self.event(db,project,'Audit: '+status+(' · '+reviewer if reviewer else ''),identity)
            db.execute('UPDATE records SET project_id=?,status=?,note=?,reviewer=?,updated=? WHERE id=?',(project or None,status,note,reviewer,time.time(),identity))
    @staticmethod
    def record(row):
        return dict(json.loads(row['snapshot']),project_id=row['project_id'],audit_status=row['status'],audit_note=row['note'],reviewer=row['reviewer'],updated=row['updated'])
    def records(self,project=None,offset=0,identity=None):
        where=' WHERE project_id=?' if project else ' WHERE id=?' if identity else ''
        values=[project or identity] if (project or identity) else []
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM records'+where,values).fetchone()[0]
            rows=db.execute('SELECT * FROM records'+where+' ORDER BY updated DESC,id LIMIT 25 OFFSET ?',values+[offset]).fetchall()
        return dict(records=[self.record(r) for r in rows],total=total,offset=offset)
    def overview(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT p.*,count(r.id) AS comparisons,sum(CASE WHEN r.status="Approved" THEN 1 ELSE 0 END) AS approved,max(r.updated) AS updated FROM projects p LEFT JOIN records r ON r.project_id=p.id GROUP BY p.id ORDER BY coalesce(max(r.updated),p.created) DESC')]
    def detail(self,identity,offset=0):
        with self.connect() as db:
            project=db.execute('SELECT * FROM projects WHERE id=?',(identity,)).fetchone()
            if not project:raise ValueError('Project not found')
            rows=db.execute('SELECT * FROM records WHERE project_id=? ORDER BY updated DESC,id',(identity,)).fetchall()
            events=db.execute('SELECT * FROM events WHERE project_id=? ORDER BY at DESC,id DESC LIMIT 100',(identity,)).fetchall()
        records=[self.record(r) for r in rows];complete=[r for r in records if r['state']=='complete']
        latest=max(complete,key=lambda r:r.get('finished') or r['created'],default=None)
        return dict(project=dict(project),total=len(records),offset=offset,records=records[offset:offset+25],
            audit_counts={s:sum(r['audit_status']==s for r in records) for s in STATUSES},
            states={s:sum(r['state']==s for r in records) for s in {r['state'] for r in records}},
            latest=latest,trend=sorted(complete,key=lambda r:r.get('finished') or r['created'])[-12:],events=[dict(e) for e in events])
