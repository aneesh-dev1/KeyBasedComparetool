"""Integration coverage for shared completed jobs and read-only server sources."""
import csv,hashlib,json,tempfile,threading,unittest,zipfile,io
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from server import make_server
from test_team_server import Client
from source_inputs import inspect_source,checked_path

class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.sources=self.root/'sources';self.sources.mkdir()
        self.server=make_server(self.root/'data',0,public_url='http://compare.internal',max_sort_mb=64);self.server.source_roots=[self.sources]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.a=Client(self.server);self.b=Client(self.server)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.server.app.pool.shutdown();self.temp.cleanup()
    def sources_job(self,left='id,v\n1,0\n2,old\n3,old\n',right='v,ID\n0.00,1\nnew,3\nnew,2\n'):
        for side,text in [('left',left),('right',right)]:(self.sources/(side+'.csv')).write_text(text)
        job=self.a.request('/api/server-sources',dict(files={s:str(self.sources/(s+'.csv')) for s in ('left','right')}))
        path='/api/jobs/'+job['id'];job=self.a.request(path+'/finalize',{})
        return path,job
    def complete(self,path,**config):
        self.a.request(path+'/start',dict(keys=['id'],**config));result=self.a.wait(path,lambda j:j['state'] in ('complete','error'));self.assertEqual(result['state'],'complete',result);return result
    def test_server_sources_avoid_copy_and_cleanup_keeps_originals(self):
        path,job=self.sources_job();before={p:p.read_bytes() for p in self.sources.iterdir()};result=self.complete(path)
        self.assertEqual(result['summary']['changed_cells'],3)
        directory=self.server.app.directory(job['id']);self.assertFalse((directory/'left.csv').exists())
        # The HTML report is written in the Write results stage, so it is ready the moment the job completes;
        # Excel is generated only on request.
        self.assertEqual(result['exports']['html']['state'],'complete');self.assertNotIn('excel',result['exports'])
        self.assertIn('Writing HTML report',(directory/'run.log').read_text())
        self.assertIn(b'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,',(directory/'comparison-report.html').read_bytes())
        self.a.request(path+'/export/excel',dict(max_rows=2,truncate=True))
        result=self.a.wait(path,lambda j:j['exports'].get('excel',{}).get('state') in ('complete','error'))
        self.assertEqual((result['exports']['excel']['state'],result['exports']['excel']['rows_per_column']),('complete',2))
        self.a.request(path,method='DELETE');self.assertFalse(directory.exists())
        self.assertEqual(before,{p:p.read_bytes() for p in self.sources.iterdir()})
    def test_source_roots_and_changed_files_are_rejected(self):
        outside=self.root/'outside.csv';outside.write_text('id,v\n1,a\n')
        for value in ['relative.csv',str(outside),str(self.sources)]:
            with self.assertRaises(ValueError):inspect_source(value,[self.sources])
        link=self.sources/'escape.csv';link.symlink_to(outside)
        with self.assertRaises(ValueError):inspect_source(str(link),[self.sources])
        path,job=self.sources_job();source=job['source_paths']['left'];(self.sources/'left.csv').write_text('id,v\n1,changed\n')
        with self.assertRaises(ValueError):checked_path(source)
        with self.assertRaises(HTTPError) as error:self.a.request(path+'/start',dict(keys=['id']))
        self.assertEqual(error.exception.code,400)
        self.assertIn('changed since selection',error.exception.read().decode())
        self.assertEqual(self.a.request(path)['state'],'ready')
        checked=self.a.request('/api/server-sources/check',dict(files={s:str(self.sources/(s+'.csv')) for s in ('left','right')}))
        self.assertTrue(checked['valid']);self.assertEqual(checked['files']['left']['name'],'left.csv')
        with self.assertRaises(HTTPError):self.a.request('/api/server-sources/check',dict(files=dict(left=str(outside),right=str(self.sources/'right.csv'))))
        self.server.source_roots=[]
        with self.assertRaises(HTTPError):self.a.request('/api/server-sources',dict(files={s:source['path'] for s in ['left','right']}))
    def test_shared_completed_history_with_private_active_controls(self):
        path,job=self.sources_job();self.assertEqual(self.b.request('/api/jobs')['jobs'],[])
        with self.assertRaises(HTTPError):self.b.request(path)
        project=self.a.request('/api/projects',{'name':'Team reconciliation'})['id']
        self.a.request('/api/projects/records',dict(comparison_id=job['id'],project_id=project))
        self.assertEqual(self.a.request('/api/projects/'+project)['total'],1)
        self.assertEqual(self.b.request('/api/projects/records')['total'],0)
        self.assertEqual(self.b.request('/api/projects')['projects'][0]['comparisons'],0)
        hidden=self.b.request('/api/projects/'+project)
        self.assertEqual(hidden['total'],0);self.assertFalse(any(e['record_id']==job['id'] for e in hidden['events']))
        result=self.complete(path)
        self.assertEqual(self.b.request('/api/jobs')['jobs'][0]['id'],job['id'])
        self.assertFalse(self.b.request(path)['can_manage'])
        # The HTML report is built in the background after the comparison completes.
        self.a.wait(path,lambda j:j['exports'].get('html',{}).get('state') in ('complete','error'))
        self.assertTrue(self.b.request(path+'/download/comparison-report.html').startswith(b'<!doctype html>'))
        with self.assertRaises(HTTPError) as error:self.b.request(path,method='DELETE')
        self.assertEqual(error.exception.code,403)
        self.assertEqual(self.b.request('/api/projects')['projects'][0]['id'],project)
        self.assertEqual(self.b.request('/api/projects/'+project)['total'],1)
        self.assertNotIn('owner',self.b.request(path))
        self.assertNotIn('owner',self.b.request('/api/projects/records')['records'][0])
    def test_exact_pattern_filter_and_cross_column_key_comments(self):
        path,_=self.sources_job(left='id,v,w\n1,old,a\n2,old,a\n3,other,a\n',right='id,v,w\n3,new,b\n2,new,b\n1,new,b\n');self.complete(path)
        self.a.request(path+'/analysis',{});self.a.wait(path,lambda j:j.get('analysis',{}).get('state')=='complete')
        patterns=self.b.request(path+'/analysis?mode=patterns&column=v');self.assertEqual(patterns['occurrences'],3)
        pair=patterns['rows'][0];self.assertEqual(pair[4],2)
        query=urlencode(dict(mode='cells',column='v',left_hash=pair[7],right_hash=pair[8]))
        details=self.b.request(path+'/analysis?'+query);self.assertEqual(details['total'],2)
        self.assertEqual({json.loads(r[0])[0] for r in details['rows']},{'1','2'})
        self.b.request(path+'/annotations',dict(column='',key=['1'],status='Expected',comment='Key-wide note'))
        for kind in ('excel','html'):
            self.b.request(path+'/export/'+kind,{});self.b.wait(path,lambda j:j['exports'][kind]['state']=='complete')
        with zipfile.ZipFile(io.BytesIO(self.a.request(path+'/download/mismatches.xlsx'))) as book:
            for sheet in ['sheet3.xml','sheet4.xml']:self.assertIn('Key-wide note',book.read('xl/worksheets/'+sheet).decode())
    def test_removing_rule_restores_accepted_evidence_without_sources(self):
        path,job=self.sources_job();result=self.complete(path,comparison_rules=[dict(column='v',tolerance='0')]);self.assertEqual(result['summary']['changed_cells'],2)
        for source in self.sources.iterdir():source.unlink()
        review=self.b.request(path+'/review-copy',dict(comparison_rules=[]));self.assertEqual(review['execution_mode'],'review')
        url='/api/jobs/'+review['id'];self.b.request(url+'/start',review['draft']);review=self.b.wait(url,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(review['state'],'complete',review);self.assertEqual(review['summary']['changed_cells'],3)
        self.assertEqual(self.a.request(path)['summary']['changed_cells'],2)
    def test_server_json_uses_same_json_flow(self):
        for side in ['left','right']:(self.sources/(side+'.json')).write_text('{"students":[{"id":"A1"}]}')
        result=self.a.request('/api/server-sources',dict(files={s:str(self.sources/(s+'.json')) for s in ['left','right']}))
        self.assertEqual(result['kind'],'json');self.assertEqual(result['documents']['left'],result['documents']['right'])

    def test_server_excel_mixed_csv_keeps_originals_and_copies_import_for_full_review(self):
        from test_excel_input import workbook,data_rows
        left=self.sources/'left.xlsx';right=self.sources/'right.csv'
        left.write_bytes(workbook(data_rows('café')));right.write_bytes('id;value\n001;CAFÉ\n'.encode('cp1252'))
        originals={p:p.read_bytes() for p in (left,right)}
        job=self.a.request('/api/server-sources',dict(files=dict(left=str(left),right=str(right)),delimiter=';',encoding='cp1252'))
        path='/api/jobs/'+job['id'];self.assertEqual(self.a.request(path+'/finalize',{})['state'],'selecting_sheets')
        self.a.request(path+'/select-sheets',dict(left='Data'))
        prepared=self.a.wait(path,lambda j:j['state'] in ('ready','error'));self.assertEqual(prepared['state'],'ready',prepared)
        self.assertEqual(prepared['columns'],['id','value'])
        self.assertEqual(self.a.request(path+'/source-preview?side=right&rows=1')['rows'],[['001','CAFÉ']])
        self.assertEqual(originals,{p:p.read_bytes() for p in (left,right)})
        result=self.complete(path);self.assertEqual(result['summary']['changed_cells'],1)
        # Excel/CSV imports are now stable local inputs, even if originals move.
        left.unlink();right.unlink()
        review=self.b.request(path+'/review-copy',dict(duplicate_policy='last'))
        self.assertEqual(review['execution_mode'],'full')
        target=self.server.app.directory(review['id'])
        self.assertTrue((target/'left.csv').exists());self.assertTrue((target/'right.csv').exists())
        review_path='/api/jobs/'+review['id'];self.b.request(review_path+'/start',review['draft'])
        completed=self.b.wait(review_path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(completed['state'],'complete',completed);self.assertEqual(completed['summary']['changed_cells'],1)

    def test_shared_review_retains_original_ignore_container_snapshot(self):
        path,_=self.sources_job()
        containers=self.a.request('/api/key-containers',dict(name='NO_HIT_KEYS',reason='No matching source record',values='3',key_width=1))['containers']
        result=self.complete(path,ignore_container_ids=[containers[0]['id']])
        self.assertEqual(result['summary']['changed_cells'],2)
        self.assertEqual(self.b.request('/api/key-containers')['containers'],[])
        review=self.b.request(path+'/review-copy',{})
        self.assertEqual(review['execution_mode'],'review')
        review_path='/api/jobs/'+review['id'];self.b.request(review_path+'/start',review['draft'])
        completed=self.b.wait(review_path,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(completed['state'],'complete',completed)
        self.assertEqual(completed['summary']['ignored_keys_count'],1)
        self.assertEqual(completed['summary']['ignore_key_containers'][0]['name'],'NO_HIT_KEYS')

class ProjectMigrationTests(unittest.TestCase):
    def test_same_names_and_workspace_prefixes_preserve_projects_records_and_events_once(self):
        from projects import Store,migrate_workspaces,database
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);existing=Store(database(root))
            central=existing.create(dict(name='Quarterly audit'))
            expected={central};owners=[]
            for suffix in ('11111111','22222222'):
                owner='aaaaaaaa'+suffix;owners.append(owner)
                target=root/'workspaces'/owner;target.mkdir(parents=True)
                old=Store(target/'projects.sqlite');project=old.create(dict(name='Quarterly audit'))
                expected.add(project)
                old.sync(dict(id=owner,state='complete',kind='json',created=1))
                old.update_record(dict(comparison_id=owner,project_id=project,status='Approved',note='Preserved note',reviewer='QA'))
            migrate_workspaces(root)
            first=existing.overview();self.assertEqual({p['id'] for p in first},expected)
            self.assertEqual(len({p['name'] for p in first}),3)
            records=existing.records()['records'];self.assertEqual(len(records),2)
            self.assertTrue(all(r['audit_note']=='Preserved note' and r['audit_status']=='Approved' for r in records))
            counts={p['id']:len(existing.detail(p['id'])['events']) for p in first}
            migrate_workspaces(root)
            self.assertEqual(counts,{p['id']:len(existing.detail(p['id'])['events']) for p in existing.overview()})
            with existing.connect() as db:
                self.assertEqual({json.loads(r[0])['owner'] for r in db.execute('SELECT snapshot FROM records')},set(owners))
