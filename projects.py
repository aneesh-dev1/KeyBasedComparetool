"""Shared project audit metadata. Never reads comparison source files."""
import json
import re
from contextlib import contextmanager
import sqlite3
import time
import uuid
from pathlib import Path

STATUSES=('Unreviewed','In review','Approved','Changes requested')
MAX_COLUMNS=5000
ISSUES_SHOWN=200
DATE=re.compile(r'\d{4}-\d{2}-\d{2}')
# A parent project takes the status of its most pressing subproject; it's signed off when all of them are.
URGENCY=('running','fixing','ready','clean','empty','signed_off')
STATE_LABELS=dict(empty='No rounds yet',running='Running',fixing='Fixing',ready='Ready for all keys',clean='Ready to sign off',signed_off='Signed off')


def _keys_checked(record):
    sample=record.get('sample')
    if sample:return sample['size']
    return (record.get('matched_keys') or 0)+(record.get('left_only') or 0)


def progress(records,signoff=None):
    """A project's rounds and where it stands. Completed table comparisons are rounds, oldest first; each is
    compared with the round before it by mismatching column: fixed (mismatched before, not now), still failing,
    and new. The status says what the project needs next."""
    tables=[r for r in records if r.get('kind','table')=='table']
    running=sorted([r for r in tables if r['state'] in ('queued','running')],key=lambda r:r['created'])
    complete=sorted([r for r in tables if r['state']=='complete'],key=lambda r:(r.get('finished') or r['created'],r['id']))
    population=max([(r.get('sample') or {}).get('population_rows') or r.get('left_rows') or 0 for r in complete] or [0])
    rounds,previous,history=[],None,[]
    for number,record in enumerate(complete,1):
        failing=set(record.get('mismatch_columns') or {})
        sample=record.get('sample')
        checked=_keys_checked(record)
        how='carry' if sample and sample.get('carried') else 'random' if sample else 'all' if population and checked>=population else 'files'
        entry=dict(id=record['id'],n=number,title=record.get('title'),created=record['created'],finished=record.get('finished'),
                   keys_checked=checked,how=how,carried=(sample or {}).get('carried',0),seed=(sample or {}).get('seed'),
                   mismatching=len(failing),cells=record.get('changed_cells') or 0,keys_with_differences=record.get('changed_rows') or 0,
                   left_only=record.get('left_only') or 0,right_only=record.get('right_only') or 0,
                   audit_status=record.get('audit_status'),source_available=record.get('source_available',True),as_of=record.get('as_of'))
        entry['clean']=not failing and not entry['left_only'] and not entry['right_only']
        if previous is None:entry.update(still=0,new=len(failing),fixed=0)
        else:entry.update(still=len(failing&previous),new=len(failing-previous),fixed=len(previous-failing))
        rounds.append(entry);history.append((record,failing));previous=failing
    latest=rounds[-1] if rounds else None
    if running:status='running'
    elif not latest:status='empty'
    # Signed off only while the project's sign-off covers its latest round; a newer round reopens it.
    elif signoff and signoff['round_id']==latest['id']:status='signed_off'
    elif not latest['clean']:status='fixing'
    elif population and latest['keys_checked']<population:status='ready'
    else:status='clean'
    issues=dict(still=[],new=[],fixed=[])
    if history:
        record,failing=history[-1];before=history[-2][1] if len(history)>1 else set()
        counts=record.get('mismatch_columns') or {}
        def trail(name):return ['f' if name in seen else 'c' for _,seen in history]
        def since(name):
            first=len(history)
            while first>1 and name in history[first-2][1]:first-=1
            return first
        for name in sorted(failing,key=lambda name:-counts.get(name,0)):
            group='still' if name in before else 'new'
            issues[group].append(dict(column=name,cells=counts.get(name,0),since=since(name),trail=trail(name)))
        for name in sorted(before-failing):
            first=len(history)-1
            while first>1 and name in history[first-2][1]:first-=1
            issues['fixed'].append(dict(column=name,cells=(history[-2][0].get('mismatch_columns') or {}).get(name,0),since=first,trail=trail(name)))
        issues={group:dict(total=len(items),items=items[:ISSUES_SHOWN]) for group,items in issues.items()}
    return dict(status=status,status_label=STATE_LABELS[status],population=population,rounds=rounds,
                signoff=signoff if signoff and latest and signoff['round_id']==latest['id'] else None,
                running=[dict(id=r['id'],created=r['created'],state=r['state'],sample=r.get('sample_requested')) for r in running],
                issues=issues if history else None,
                checked=max([r['keys_checked'] for r in rounds] or [0]))


def database(root,owner=None):
    folder=Path(root)
    folder.mkdir(parents=True,exist_ok=True)
    return folder/'projects.sqlite'

def job_snapshot(job):
    summary=job.get('summary') or {}
    return dict(id=job['id'],owner=job.get('owner'),kind='table',created=job['created'],finished=job.get('finished'),state=job['state'],
        source_available=True,title=' ↔ '.join(job['files'][s]['name'] for s in ('left','right')),
        formats=[job['files'][s].get('format','csv') for s in ('left','right')],
        changed_cells=summary.get('changed_cells'),changed_rows=summary.get('changed_rows'),matched_keys=summary.get('matched_keys'),
        equal_rows=summary.get('equal_rows'),left_only=summary.get('left_only'),right_only=summary.get('right_only'),
        changed_columns=sum(v>0 for v in summary.get('changed_cells_by_column',{}).values()),
        compared_columns=len(summary.get('changed_cells_by_column',{})),error=job.get('error','')[-1000:],
        left_rows=summary.get('left_rows'),key_columns=job.get('keys'),
        # Project rounds: which columns mismatch, and the sample of keys the run compared (none: every key).
        mismatch_columns=dict(list({name:count for name,count in summary.get('changed_cells_by_column',{}).items() if count}.items())[:MAX_COLUMNS]),
        sample={k:v for k,v in (summary.get('sample') or {}).items() if k!='digest'} or None,
        sample_requested=job.get('sample'))

class Store:
    def __init__(self,path,viewer=None):
        self.path=Path(path);self.viewer=viewer
    def visibility(self,alias=''):
        prefix=alias+'.' if alias else ''
        return (f"(json_extract({prefix}snapshot,'$.state')='complete' OR json_extract({prefix}snapshot,'$.owner')=?)",[self.viewer]) if self.viewer is not None else ('1',[])
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE,description TEXT NOT NULL,created REAL NOT NULL,profile_id TEXT,parent_id TEXT);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),snapshot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'Unreviewed',note TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS records_project ON records(project_id,updated DESC);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),at REAL NOT NULL,message TEXT NOT NULL,record_id TEXT);
        CREATE INDEX IF NOT EXISTS events_project ON events(project_id,at DESC);
        CREATE TABLE IF NOT EXISTS signoffs(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),round_id TEXT NOT NULL,at REAL NOT NULL,reviewer TEXT NOT NULL,note TEXT NOT NULL,mode TEXT NOT NULL,reasons TEXT NOT NULL,report TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS signoffs_project ON signoffs(project_id,at DESC);
        ''')
        # Projects made from a template (a saved comparison profile) remember it, so each round starts from it.
        columns={row[1] for row in db.execute('PRAGMA table_info(projects)')}
        if 'profile_id' not in columns:db.execute('ALTER TABLE projects ADD COLUMN profile_id TEXT')
        # Subprojects: one level under a parent project, which only groups them.
        if 'parent_id' not in columns:db.execute('ALTER TABLE projects ADD COLUMN parent_id TEXT')
        # The date a user gives a comparison when attaching it (YYYY-MM-DD), shown instead of the run date.
        if 'as_of' not in {row[1] for row in db.execute('PRAGMA table_info(records)')}:db.execute('ALTER TABLE records ADD COLUMN as_of TEXT')
        # Names were unique across the app; now they're unique among top-level projects and within each parent,
        # so two projects can both have a "Cards" subproject. Older databases are rebuilt once without the constraint.
        if 'UNIQUE' in (db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()[0] or '').upper():
            db.commit()
            db.executescript('''PRAGMA foreign_keys=OFF;BEGIN;
            CREATE TABLE projects_rebuilt(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE,description TEXT NOT NULL,created REAL NOT NULL,profile_id TEXT,parent_id TEXT);
            INSERT INTO projects_rebuilt SELECT id,name,description,created,profile_id,parent_id FROM projects;
            DROP TABLE projects;ALTER TABLE projects_rebuilt RENAME TO projects;COMMIT;PRAGMA foreign_keys=ON;''')
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
    def latest_signoff(self,identity,db=None):
        def read(connection):
            row=connection.execute('SELECT * FROM signoffs WHERE project_id=? ORDER BY at DESC,id DESC LIMIT 1',(identity,)).fetchone()
            return dict(row,reasons=json.loads(row['reasons'])) if row else None
        if db is not None:return read(db)
        with self.connect() as connection:return read(connection)
    def sign_off(self,identity,round_id,reviewer,note,mode,reasons,report):
        with self.connect() as db:
            db.execute('INSERT INTO signoffs(project_id,round_id,at,reviewer,note,mode,reasons,report) VALUES(?,?,?,?,?,?,?,?)',
                       (identity,round_id,time.time(),reviewer,note,mode,json.dumps(reasons),str(report)))
            self.event(db,identity,'Project signed off'+(' with reasons for remaining mismatches' if mode=='exceptions' else ' at 100% match')+' · '+reviewer,round_id)
    def name_taken(self,name,parent=None):
        with self.connect() as db:
            if parent:return db.execute('SELECT 1 FROM projects WHERE parent_id=? AND name=?',(parent,name.strip())).fetchone() is not None
            return db.execute('SELECT 1 FROM projects WHERE parent_id IS NULL AND name=?',(name.strip(),)).fetchone() is not None
    def set_profile(self,identity,profile_id):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM projects WHERE id=?',(identity,)).fetchone():raise ValueError('Project not found')
            db.execute('UPDATE projects SET profile_id=? WHERE id=?',(profile_id or None,identity))
            self.event(db,identity,'Template saved for new rounds' if profile_id else 'Template removed')
    def signoffs(self,identity):
        with self.connect() as db:
            return [dict(row,reasons=json.loads(row['reasons'])) for row in db.execute('SELECT * FROM signoffs WHERE project_id=? ORDER BY at,id',(identity,))]
    def events_of(self,identity):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE project_id=? ORDER BY at,id',(identity,))]
    def restore(self,identity,events,signoffs):
        """An imported project's history, with its original times: activity, then sign-offs (report paths added later)."""
        with self.connect() as db:
            for item in events:db.execute('INSERT INTO events(project_id,at,message,record_id) VALUES(?,?,?,?)',(identity,item['at'],item['message'],item.get('record_id')))
            ids=[]
            for item in signoffs:
                cursor=db.execute('INSERT INTO signoffs(project_id,round_id,at,reviewer,note,mode,reasons,report) VALUES(?,?,?,?,?,?,?,?)',
                                  (identity,item['round_id'],item['at'],item['reviewer'],item['note'],item['mode'],json.dumps(item['reasons']),''))
                ids.append(cursor.lastrowid)
            return ids
    def adopt(self,record_id,identity,status,note,reviewer,as_of=None):
        """Attach an imported round with its audit status; the import restores the original activity itself."""
        if status not in STATUSES:status='Unreviewed'
        if not (isinstance(as_of,str) and DATE.fullmatch(as_of)):as_of=None
        with self.connect() as db:db.execute('UPDATE records SET project_id=?,status=?,note=?,reviewer=?,as_of=? WHERE id=?',(identity,status,note[:2000],reviewer[:100],as_of,record_id))
    def record_ids(self,identity):
        with self.connect() as db:return [row[0] for row in db.execute('SELECT id FROM records WHERE project_id=?',(identity,))]
    def remove(self,identity,keep_records=False):
        """Delete a project with its activity and sign-offs. Its comparison records go too, or with ``keep_records``
        they stay in history, no longer in a project. Also undoes a failed import."""
        with self.connect() as db:
            ids=[row[0] for row in db.execute('SELECT id FROM records WHERE project_id=?',(identity,))]
            for table in ('signoffs','events'):db.execute(f'DELETE FROM {table} WHERE project_id=?',(identity,))
            if keep_records:db.execute('UPDATE records SET project_id=NULL WHERE project_id=?',(identity,))
            else:db.execute('DELETE FROM records WHERE project_id=?',(identity,))
            db.execute('DELETE FROM projects WHERE id=?',(identity,))
        return ids
    def forget(self,record_ids):
        with self.connect() as db:db.executemany('DELETE FROM records WHERE id=?',[(i,) for i in record_ids])
    def set_signoff_report(self,signoff_id,report):
        with self.connect() as db:db.execute('UPDATE signoffs SET report=? WHERE id=?',(str(report),signoff_id))
    def accepts_comparisons(self,identity):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM projects WHERE id=?',(identity,)).fetchone()) and not db.execute('SELECT 1 FROM projects WHERE parent_id=?',(identity,)).fetchone()
    def children(self,identity):
        with self.connect() as db:return [row[0] for row in db.execute('SELECT id FROM projects WHERE parent_id=? ORDER BY created',(identity,))]
    def exists(self,identity):
        with self.connect() as db:return db.execute('SELECT 1 FROM projects WHERE id=?',(identity,)).fetchone() is not None
    def event(self,db,project,message,record=None):
        db.execute('INSERT INTO events(project_id,at,message,record_id) VALUES(?,?,?,?)',(project,time.time(),message,record))
    def create(self,config):
        name=config.get('name','');description=config.get('description','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Use a project name of 1–100 characters')
        if not isinstance(description,str) or len(description)>2000:raise ValueError('Project description must be under 2,000 characters')
        identity=uuid.uuid4().hex
        try:
            with self.connect() as db:
                if db.execute('SELECT count(*) FROM projects').fetchone()[0]>=500:raise ValueError('Maximum 500 projects')
                profile=config.get('profile_id') or None;parent=config.get('parent_id') or None
                if parent:
                    row=db.execute('SELECT parent_id FROM projects WHERE id=?',(parent,)).fetchone()
                    if not row:raise ValueError('The parent project no longer exists')
                    if row['parent_id']:raise ValueError('A subproject can’t have subprojects of its own')
                    if db.execute('SELECT 1 FROM records WHERE project_id=?',(parent,)).fetchone():
                        raise ValueError('This project already has comparisons, so it can’t hold subprojects. Create a new project to group them.')
                same=db.execute('SELECT 1 FROM projects WHERE parent_id=? AND name=?',(parent,name.strip())) if parent else db.execute('SELECT 1 FROM projects WHERE parent_id IS NULL AND name=?',(name.strip(),))
                if same.fetchone():raise ValueError('A subproject with this name already exists in this project' if parent else 'A project with this name already exists')
                db.execute('INSERT INTO projects(id,name,description,created,profile_id,parent_id) VALUES(?,?,?,?,?,?)',(identity,name.strip(),description,config.get('created') or time.time(),profile,parent))
                if not config.get('imported'):self.event(db,identity,'Project created'+(' from a template' if profile else ''))
                if parent and not config.get('imported'):self.event(db,parent,'Subproject added: '+name.strip())
        except sqlite3.IntegrityError as error:raise ValueError('A project with this name already exists') from error
        return identity
    def update_record(self,config):
        identity=config.get('comparison_id');project=config.get('project_id');status=config.get('status','Unreviewed');note=config.get('note','');reviewer=config.get('reviewer','')
        if status not in STATUSES:raise ValueError('Choose a valid audit status')
        if not isinstance(note,str) or len(note)>2000 or not isinstance(reviewer,str) or len(reviewer)>100:raise ValueError('Use a short reviewer name and a note under 2,000 characters')
        with self.connect() as db:
            current=db.execute('SELECT * FROM records WHERE id=?',(identity,)).fetchone()
            if not current:raise ValueError('Comparison is not available')
            if self.viewer is not None and json.loads(current['snapshot']).get('owner')!=self.viewer:raise ValueError('Create your own review to change this comparison’s project or audit status')
            if project and not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise ValueError('Project not found')
            if project and db.execute('SELECT 1 FROM projects WHERE parent_id=?',(project,)).fetchone():raise ValueError('This project groups subprojects. Attach the comparison to one of its subprojects.')
            as_of=config.get('as_of',current['as_of']) or None
            if as_of is not None and (not isinstance(as_of,str) or not DATE.fullmatch(as_of)):raise ValueError('Enter the date as YYYY-MM-DD')
            if project and as_of!=current['as_of']:self.event(db,project,'Date set: '+as_of if as_of else 'Date cleared',identity)
            if status=='Approved' and json.loads(current['snapshot'])['state']!='complete':raise ValueError('Complete the comparison before approving the audit')
            if current['project_id'] and current['project_id']!=project:self.event(db,current['project_id'],'Comparison detached',identity)
            if project:
                if current['project_id']!=project:self.event(db,project,'Comparison attached',identity)
                if (current['status'],current['note'],current['reviewer'])!=(status,note,reviewer):self.event(db,project,'Audit: '+status+(' · '+reviewer if reviewer else ''),identity)
            db.execute('UPDATE records SET project_id=?,status=?,note=?,reviewer=?,as_of=?,updated=? WHERE id=?',(project or None,status,note,reviewer,as_of,time.time(),identity))
    @staticmethod
    def record(row):
        snapshot=json.loads(row['snapshot']);snapshot.pop('owner',None)
        return dict(snapshot,project_id=row['project_id'],audit_status=row['status'],audit_note=row['note'],reviewer=row['reviewer'],updated=row['updated'],as_of=row['as_of'])
    def records(self,project=None,offset=0,identity=None):
        visible,values=self.visibility()
        where=' WHERE '+visible
        if project or identity:
            where+=' AND '+('project_id=?' if project else 'id=?');values.append(project or identity)
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM records'+where,values).fetchone()[0]
            rows=db.execute('SELECT * FROM records'+where+' ORDER BY updated DESC,id LIMIT 25 OFFSET ?',values+[offset]).fetchall()
        return dict(records=[self.record(r) for r in rows],total=total,offset=offset)
    def overview(self):
        visible,values=self.visibility('r')
        with self.connect() as db:
            projects=[dict(r) for r in db.execute('SELECT p.*,count(r.id) AS comparisons,sum(CASE WHEN r.status="Approved" THEN 1 ELSE 0 END) AS approved,max(r.updated) AS updated FROM projects p LEFT JOIN records r ON r.project_id=p.id AND '+visible+' GROUP BY p.id ORDER BY coalesce(max(r.updated),p.created) DESC',values)]
            plain,plain_values=self.visibility()
            grouped={}
            for row in db.execute('SELECT * FROM records WHERE project_id IS NOT NULL AND '+plain,plain_values):grouped.setdefault(row['project_id'],[]).append(self.record(row))
            signoffs={project['id']:self.latest_signoff(project['id'],db) for project in projects}
        for project in projects:
            state=progress(grouped.get(project['id'],[]),signoffs.get(project['id']))
            latest=state['rounds'][-1] if state['rounds'] else None
            # The dashboard row needs the status, coverage and the latest round; not every round's detail.
            project.update(status=state['status'],status_label=state['status_label'],population=state['population'],checked=state['checked'],
                           rounds=[dict(n=r['n'],keys_checked=r['keys_checked'],clean=r['clean'],mismatching=r['mismatching']) for r in state['rounds']],
                           latest_round=latest,feed=(latest or {}).get('title') or next((r.get('title') for r in grouped.get(project['id'],[])),None),
                           signoff=state['signoff'] and dict(mode=state['signoff']['mode'],reviewer=state['signoff']['reviewer'],at=state['signoff']['at']),
                           # When the latest round actually ran (or a running one started), not when a comparison
                           # was attached or its record last changed.
                           last_run=(state['running'][-1]['created'] if state['running'] else (latest.get('finished') or latest['created']) if latest else None))
        # A parent's row summarises its subprojects: how many are signed off, and the most pressing status.
        children={}
        for project in projects:
            if project.get('parent_id'):children.setdefault(project['parent_id'],[]).append(project)
        for project in projects:
            subs=children.get(project['id'])
            if not subs:continue
            done=sum(sub['status']=='signed_off' for sub in subs)
            status='signed_off' if done==len(subs) else min((sub['status'] for sub in subs if sub['status']!='signed_off'),key=URGENCY.index)
            runs=[sub['last_run'] for sub in subs if sub['last_run']]
            # The tag colour follows the most pressing subproject; the words say how far the group is.
            label='Signed off' if done==len(subs) else 'Not started' if all(sub['status']=='empty' for sub in subs) else 'In progress'
            project.update(status=status,status_label=label,last_run=max(runs) if runs else None,
                           subprojects=[dict(id=sub['id'],name=sub['name'],status=sub['status'],status_label=sub['status_label']) for sub in sorted(subs,key=lambda sub:sub['created'])],
                           signed_off=done)
        projects.sort(key=lambda project:project['last_run'] or project['created'],reverse=True)
        return projects
    def runs(self,identity):
        """A project's completed table comparisons the viewer can see, oldest first."""
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM projects WHERE id=?',(identity,)).fetchone():raise ValueError('Project not found')
            visible,values=self.visibility()
            rows=db.execute('SELECT * FROM records WHERE project_id=? AND '+visible,[identity]+values).fetchall()
        records=[self.record(r) for r in rows]
        return sorted([r for r in records if r['state']=='complete' and r.get('kind','table')=='table'],key=lambda r:(r.get('finished') or r['created'],r['id']))
    def detail(self,identity,offset=0):
        with self.connect() as db:
            project=db.execute('SELECT * FROM projects WHERE id=?',(identity,)).fetchone()
            if not project:raise ValueError('Project not found')
            visible,values=self.visibility()
            rows=db.execute('SELECT * FROM records WHERE project_id=? AND '+visible+' ORDER BY updated DESC,id',[identity]+values).fetchall()
            event_visible,event_values=self.visibility('r')
            events=db.execute('SELECT e.* FROM events e LEFT JOIN records r ON r.id=e.record_id WHERE e.project_id=? AND (e.record_id IS NULL OR ('+event_visible+')) ORDER BY e.at DESC,e.id DESC LIMIT 100',[identity]+event_values).fetchall()
            signoff=self.latest_signoff(identity,db)
        records=[self.record(r) for r in rows];complete=[r for r in records if r['state']=='complete']
        latest=max(complete,key=lambda r:r.get('finished') or r['created'],default=None)
        return dict(project=dict(project),total=len(records),offset=offset,records=records[offset:offset+25],
            audit_counts={s:sum(r['audit_status']==s for r in records) for s in STATUSES},
            states={s:sum(r['state']==s for r in records) for s in {r['state'] for r in records}},
            latest=latest,trend=sorted(complete,key=lambda r:r.get('finished') or r['created'])[-12:],events=[dict(e) for e in events],
            progress=progress(records,signoff),key_columns=(latest or {}).get('key_columns'),
            parent=self.parent_of(project),subprojects=self.subprojects(identity))
    def parent_of(self,project):
        if not project['parent_id']:return None
        with self.connect() as db:row=db.execute('SELECT id,name FROM projects WHERE id=?',(project['parent_id'],)).fetchone()
        return dict(row) if row else None
    def subprojects(self,identity):
        """A parent's subprojects, each as its dashboard row; None for a project without any."""
        if not self.children(identity):return None
        return [p for p in sorted(self.overview(),key=lambda p:p['created']) if p.get('parent_id')==identity]


def migrate_workspaces(root):
    """Merge previous workspace project metadata once, preserving associations."""
    root=Path(root);store=Store(database(root))
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS migrated_workspaces(path TEXT PRIMARY KEY)')
        for source in root.glob('workspaces/*/projects.sqlite'):
            if db.execute('SELECT 1 FROM migrated_workspaces WHERE path=?',(str(source),)).fetchone():continue
            old=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True);old.row_factory=sqlite3.Row
            try:
                for row in old.execute('SELECT * FROM projects'):
                    if db.execute('SELECT 1 FROM projects WHERE id=?',(row['id'],)).fetchone():continue
                    name=row['name'];suffix=0
                    while db.execute('SELECT 1 FROM projects WHERE name=?',(name,)).fetchone():
                        suffix+=1
                        label=' · '+source.parent.name[:8]+(' '+str(suffix) if suffix>1 else '')
                        name=row['name'][:100-len(label)]+label
                    db.execute('INSERT INTO projects(id,name,description,created) VALUES(?,?,?,?)',(row['id'],name,row['description'],row['created']))
                for row in old.execute('SELECT * FROM records'):
                    snapshot=json.loads(row['snapshot']);snapshot.setdefault('owner',source.parent.name)
                    db.execute('INSERT OR IGNORE INTO records(id,project_id,snapshot,status,note,reviewer,updated) VALUES(?,?,?,?,?,?,?)',(row['id'],row['project_id'],json.dumps(snapshot),row['status'],row['note'],row['reviewer'],row['updated']))
                for row in old.execute('SELECT project_id,at,message,record_id FROM events'):
                    db.execute('INSERT INTO events(project_id,at,message,record_id) VALUES(?,?,?,?)',tuple(row))
                db.execute('INSERT INTO migrated_workspaces VALUES(?)',(str(source),))
            finally:old.close()
