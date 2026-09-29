#!/usr/bin/env python3
"""Local CSV comparison UI. Run: python3 server.py"""
import argparse
from http.cookies import SimpleCookie
import signal
from concurrent.futures import ThreadPoolExecutor
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

from task_control import check_cancel
from comparison_rules import validate_rules
from analysis import build_index, query_index
from json_compare import compare_json, discover_arrays
from excel_input import sheets as excel_sheets, convert as convert_excel
from file_io import atomic_write_text, replace_retry
from compare import execution_settings, common_headers, align_headers, read_header, normalize_headers, header, validate_scope, validate_overrides
from reports import export_excel, export_html, make_summary_html, REPORT_VERSION

BASE = Path(__file__).resolve().parent
CHUNK = 8 * 1024 * 1024


class Application:
    def __init__(self, root, max_jobs=1):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.file_locks = {}
        self.requests = {}
        self.cleanup_lock = threading.Lock()
        self.retention_days = 7
        self.pool = ThreadPoolExecutor(max_workers=max_jobs)  # Shared, bounded queue across sessions.
        self.json_lock = threading.Lock()
        self.tasks = {}
        self.max_jobs = max_jobs
        csv.field_size_limit(64 * 1024 * 1024)
        for path in self.root.glob('*/job.json'):
            if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                continue
            job = json.loads(path.read_text())
            changed = False
            if job['state'] in ('queued', 'running', 'preparing'):
                job.update(state='error', error='Server stopped before comparison completed. Resume this job to reuse validated sort batches.' if (path.parent/'checkpoints/identity.json').exists() else 'Server stopped before comparison completed. Start a new comparison.')
                changed = True
            if job.get('analysis', {}).get('state') in ('queued', 'running'):
                job['analysis'] = dict(state='error', message='Analysis preparation interrupted. Try again.')
                changed = True
            for export in job.get('exports', {}).values():
                if export['state'] in ('queued', 'running'):
                    export.update(state='error', error='Export interrupted. Generate it again.')
                    changed = True
            if changed:
                self.save(job)

    def submit(self, identity, kind, function, *args):
        with self.lock:
            flag=self.directory(identity)/('cancel-'+kind)
            flag.unlink(missing_ok=True)
            def run():
                try:
                    check_cancel(flag)
                    function(identity,*args)
                finally:
                    with self.lock:
                        if flag.exists(): self.mark_cancelled(identity,kind)
                        self.tasks.pop((identity,kind),None)
            future=self.pool.submit(run)
            self.tasks[(identity,kind)]={'future':future,'queued_at':time.time()}

    def mark_cancelled(self,identity,kind):
        job=self.load(identity)
        if kind=='comparison' and job['state']=='complete':return
        if kind in ('excel','html'):
            job['exports'][kind]=dict(state='cancelled',updated=time.time(),message='Export cancelled. Generate it again when ready.')
        elif kind=='analysis':job['analysis']=dict(state='cancelled',updated=time.time(),message='Analysis cancelled. Use Retry to prepare it again.')
        else:job.update(state='cancelled',error='Cancelled by user. Resume saved sort batches when ready.' if (self.directory(identity)/'checkpoints/identity.json').exists() else 'Cancelled by user. Start a new comparison.',finished=time.time())
        self.save(job)

    def cancel(self,identity,kind):
        with self.lock:
            entry=self.tasks.get((identity,kind))
            if not entry: raise ValueError('This task has already finished or is not queued')
            (self.directory(identity)/('cancel-'+kind)).touch()
            if entry['future'].cancel():
                self.mark_cancelled(identity,kind);self.tasks.pop((identity,kind),None)
            return dict(message='Cancellation requested. Active work stops at its next safe checkpoint.')

    def directory(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ValueError('Invalid job ID')
        return self.root / identity

    def load(self, identity):
        with self.lock:
            path = self.directory(identity) / 'job.json'
            if not path.is_file():
                raise ValueError('Comparison not found')
            job = json.loads(path.read_text())
            if job['state'] == 'uploading':
                for side in ('left', 'right'):
                    source = self.upload_path(job, side)
                    job['files'][side]['uploaded'] = source.stat().st_size if source.exists() else 0
            if job.get('analysis',{}).get('state')=='complete' and job['analysis'].get('version')!=2:
                job['analysis']=dict(state='not_started',message='Prepare analysis to include mismatch patterns.')
            for export in job.get('exports', {}).values():
                if export.get('state') == 'complete' and export.get('report_version') != REPORT_VERSION:
                    export.update(state='outdated', message='New report layout available. Generate this export again.')
            job['can_resume']=job['state'] in ('error','cancelled') and (self.directory(identity)/'checkpoints/identity.json').exists()
        return job

    def save(self, job):
        with self.lock:
            path = self.directory(job['id']) / 'job.json'
            atomic_write_text(path, json.dumps(job))

    def patch(self, identity, **values):
        with self.lock:
            job = self.load(identity)
            job.update(values)
            self.save(job)

    def create(self, config, owner=None):
        delimiter = config.get('delimiter', ',')
        encoding = config.get('encoding', 'utf-8-sig')
        if delimiter not in (',', '\t', ';', '|') or encoding not in ('utf-8-sig', 'utf-16', 'cp1252'):
            raise ValueError('Unsupported delimiter or encoding')
        files = config.get('files', {})
        for side in ('left', 'right'):
            if not isinstance(files.get(side), dict) or not isinstance(files[side].get('size'), int) or not 0 < files[side]['size'] <= 100 * 1024**3:
                raise ValueError('Select two nonempty CSV or Excel files (up to 100 GB each)')
            name = str(files[side].get('name', side))[:255]
            suffix = Path(name).suffix.lower()
            if suffix not in ('.csv', '.xlsx', '.xlsm'):
                raise ValueError('Choose CSV, .xlsx or .xlsm files. Save older .xls workbooks as .xlsx first.')
            files[side] = dict(name=name, size=files[side]['size'], uploaded=0, complete=False,
                               format='excel' if suffix in ('.xlsx', '.xlsm') else 'csv')
        identity = uuid.uuid4().hex
        self.directory(identity).mkdir()
        job = dict(id=identity, owner=owner, state='uploading', created=time.time(), files=files, delimiter=delimiter, encoding=encoding, exports={})
        required=sum(f['size'] for f in files.values())
        if shutil.disk_usage(self.root).free<required+128*1024**2:
            self.directory(identity).rmdir()
            raise ValueError('Insufficient server disk space for both uploaded files. Clean up old jobs first.')
        self.save(job)
        return job

    def busy(self, job):
        return job['state'] in ('queued', 'running', 'preparing') or job.get('analysis', {}).get('state') in ('queued','running') or any(
            export.get('state') in ('queued', 'running') for export in job.get('exports', {}).values())

    def remove_job(self, identity, allowed_requests=0):
        with self.cleanup_lock:
            self._remove_job(identity, allowed_requests)

    def _remove_job(self, identity, allowed_requests=0):
        with self.lock:
            job = self.load(identity)
            if self.busy(job) or self.requests.get(identity, 0) > allowed_requests:
                raise ValueError('Job is in use. Wait for uploads, downloads, comparison and exports to finish.')
            source = self.directory(identity)
            trash = self.root / ('.deleting-' + identity)
            source.rename(trash)
        # Rename atomically removes the job from history before reclaiming large files.
        shutil.rmtree(trash)

    def cleanup(self, now=None):
        with self.cleanup_lock:
            self._cleanup(now)

    def _cleanup(self, now=None):
        now = time.time() if now is None else now
        if self.retention_days:
            for path in list(self.root.glob('*/job.json')):
                if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                    continue
                try:
                    with self.lock:
                        job = self.load(path.parent.name)
                        age_from = max(job.get('finished', job['created']), path.stat().st_mtime)
                        if now - age_from < self.retention_days * 86400 or self.busy(job) or self.requests.get(job['id'], 0):
                            continue
                        # Hold the lock through rename so a new request cannot acquire this job.
                        trash = self.root / ('.deleting-' + job['id'])
                        self.directory(job['id']).rename(trash)
                    shutil.rmtree(trash)
                    print(f"Cleanup removed expired job {job['id']}", flush=True)
                except (ValueError, OSError) as error:
                    print(f'Cleanup deferred: {error}', flush=True)
        # Retry deletions interrupted by a restart or temporary file lock.
        for trash in self.root.glob('.deleting-*'):
            if re.fullmatch(r'.deleting-[a-f0-9]{32}', trash.name) and not trash.is_symlink():
                try:
                    shutil.rmtree(trash)
                except OSError as error:
                    print(f'Cleanup deferred: {error}', flush=True)

    def upload_path(self, job, side):
        return self.directory(job['id']) / (f'{side}.xlsx' if job['files'][side].get('format') == 'excel' else f'{side}.csv')

    def prepare_sheets(self, identity):
        job = self.load(identity)
        directory = self.directory(identity)
        def notify(message):
            check_cancel(directory/'cancel-import')
            self.patch(identity, preparation_message=message)
            with (directory / 'run.log').open('a') as log:
                log.write(f'{time.strftime("%H:%M:%S")}  {message}\n')
        try:
            columns = {}
            for side in ('left', 'right'):
                source = self.upload_path(job, side)
                if job['files'][side].get('format') == 'excel':
                    columns[side], _ = convert_excel(source, job['files'][side]['sheet'], directory / f'{side}.csv', notify)
                else:
                    notify(f'Preparing {side} CSV for mixed-format comparison')
                    temporary = directory / f'{side}.normalized'
                    with source.open(encoding=job['encoding'], newline='') as stream, temporary.open('w', encoding='utf-8', newline='') as output:
                        writer = csv.writer(output)
                        for index,row in enumerate(csv.reader(stream, delimiter=job['delimiter'], strict=True)):
                            if index%10000==0:check_cancel(directory/'cancel-import')
                            writer.writerow(row)
                    replace_retry(source, directory / f'{side}.source.csv')
                    replace_retry(temporary, source)
                    columns[side] = header(source, ',', 'utf-8')
            self.patch(identity, state='ready', source_headers=columns, column_headers=columns, headers_reviewed=False, columns=common_headers(align_headers(columns)[0]), delimiter=',', encoding='utf-8', preparation_message='Selected sheets are ready')
        except Exception as error:
            self.patch(identity, state='error', error=str(error))

    def run_comparison(self, identity):
        self.patch(identity, state='running', started=time.time())
        job = self.load(identity)
        directory = self.directory(identity)
        command = [sys.executable, str(BASE / 'worker.py'), str(directory)]
        try:
            with (directory / 'run.log').open('ab') as log:
                result = subprocess.run(command, stdout=log, stderr=log,
                                        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            if result.returncode not in (0, 1) or not (directory / 'report/summary.json').exists():
                with (directory / 'run.log').open('rb') as log:
                    log.seek(max(0, (directory / 'run.log').stat().st_size - 4000))
                    message = log.read().decode('utf-8', errors='replace')
                raise ValueError(message or 'Comparison worker stopped unexpectedly')
            check_cancel(directory/'cancel-comparison')
            summary = json.loads((directory / 'report/summary.json').read_text())
            summary['sources'] = [dict(side=side, file=job['files'][side]['name'], sheet=job['files'][side].get('sheet')) for side in ('left', 'right')]
            atomic_write_text(directory / 'report/summary.json',json.dumps(summary, indent=2))
            (directory / 'report/summary.html').write_text(make_summary_html(summary), encoding='utf-8')
            with (directory / 'run.log').open('a') as log:
                log.write(f'{time.strftime("%H:%M:%S")}  Comparison complete | changed cells={summary["changed_cells"]:,}\n')
            self.patch(identity, state='complete', summary=summary, finished=time.time())
            shutil.rmtree(directory/'checkpoints',ignore_errors=True)
        except Exception as error:
            self.patch(identity, state='error', error=str(error), finished=time.time())

    def run_analysis(self, identity):
        directory=self.directory(identity)
        temporary=directory/'analysis.part.sqlite'
        def notify(message):
            check_cancel(directory/'cancel-analysis')
            self.patch(identity, analysis=dict(state='running',message=message))
        try:
            temporary.unlink(missing_ok=True)
            notify('Preparing a disk-backed index for key and column analysis…')
            build_index(directory/'report',temporary,notify,lambda:(directory/'cancel-analysis').exists())
            replace_retry(temporary,directory/'analysis.sqlite')
            self.patch(identity,analysis=dict(state='complete',version=2,message='Analysis ready',updated=time.time()))
        except Exception as error:
            temporary.unlink(missing_ok=True)
            self.patch(identity,analysis=dict(state='error',message=str(error),updated=time.time()))

    def run_export(self, identity, kind):
        def status(state, **values):
            with self.lock:
                job = self.load(identity)
                job['exports'][kind] = dict(state=state, updated=time.time(), **values)
                self.save(job)
        directory = self.directory(identity)
        target = directory / ('mismatches.xlsx' if kind == 'excel' else 'comparison-report.html')
        temporary = target.with_suffix(target.suffix + '.part')
        last_update = [0.0]
        def notify(message):
            check_cancel(directory/('cancel-'+kind))
            if time.monotonic() - last_update[0] > 1:
                status('running', message=message)
                last_update[0] = time.monotonic()
        status('running', message='Preparing report')
        try:
            (export_excel if kind == 'excel' else export_html)(directory / 'report', temporary, notify)
            replace_retry(temporary, target)
            status('complete', size=target.stat().st_size, report_version=REPORT_VERSION)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            status('error', error=str(error))


class Handler(BaseHTTPRequestHandler):
    server_version = 'CSVCompare/1.0'

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        # Do not log download authentication tokens.
        pass

    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit=1024 * 1024):
        size = int(self.headers.get('Content-Length', 0))
        if size < 0 or size > limit:
            raise ValueError('Request too large')
        value = json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value

    def end_headers(self):
        if getattr(self, 'new_workspace', False):
            cookie = f'compare-workspace={self.workspace}; Path=/; HttpOnly; SameSite=Lax; Max-Age=15552000'
            if self.server.public_url and self.server.public_url.startswith('https://'):
                cookie += '; Secure'
            self.send_header('Set-Cookie', cookie)
            self.new_workspace = False
        super().end_headers()

    def container_path(self):
        if not self.server.shared:
            return self.app.root / 'key-containers.json'
        folder = self.app.root / 'workspaces' / self.workspace
        folder.mkdir(parents=True, exist_ok=True)
        return folder / 'key-containers.json'

    def check_request(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origins = {f'http://{host}' for host in hosts}
        if self.server.public_url:
            hosts.add(urlsplit(self.server.public_url).netloc)
            origins.add(self.server.public_url)
        if self.headers.get('Host') not in hosts:
            raise ValueError('Use the configured application URL')
        if self.headers.get('Origin') and self.headers['Origin'] not in origins:
            raise ValueError('Cross-origin request rejected')
        self.workspace = None
        self.new_workspace = False
        if self.server.shared:
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get('Cookie', ''))
            except Exception:
                pass
            value = cookies.get('compare-workspace')
            value = value.value if value else ''
            if not re.fullmatch(r'[a-f0-9]{32}', value):
                value = secrets.token_hex(16)
                self.new_workspace = True
            self.workspace = value
        url = urlsplit(self.path)
        if url.path.startswith('/api/'):
            token = self.headers.get('X-App-Token') or parse_qs(url.query).get('token', [''])[0]
            if not secrets.compare_digest(token, self.app.token):
                raise ValueError('Session expired. Reload this page.')
        return url.path, parse_qs(url.query)

    def dispatch(self):
        try:
            path, query = self.check_request()
            if self.command == 'GET' and path == '/health':
                return self.json_response(dict(service='key-based-compare', status='ready', pid=os.getpid()))
            if self.command == 'GET' and path in ('/', '/app.js', '/style.css', '/assets/transunion-logo.svg'):
                target = BASE / 'web' / ('index.html' if path == '/' else path[1:])
                data = target.read_bytes().replace(b'__APP_TOKEN__', self.app.token.encode()).replace(b'__WORKSPACE_MODE__', b'team' if self.server.shared else b'local').replace(b'__MAX_SORT_MB__', str(self.server.max_sort_mb).encode())
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target)[0] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ('/api/json-compare', '/api/json-arrays') and self.command == 'POST':
                config = self.body(64 * 1024 * 1024)
                if not self.app.json_lock.acquire(blocking=False):
                    return self.json_response({'error': 'Another JSON comparison is running. Try again shortly.'}, 429)
                try:
                    result = discover_arrays(config.get('left'), config.get('right')) if path == '/api/json-arrays' else compare_json(config.get('left'), config.get('right'), config.get('default_order', 'ordered'), config.get('rules', []), True)
                    return self.json_response(result)
                finally:
                    self.app.json_lock.release()
            if path == '/api/key-containers' and self.command in ('GET', 'POST'):
                with self.app.lock:
                    target = self.container_path()
                    items = json.loads(target.read_text()) if target.exists() else []
                    if self.command == 'POST':
                        data = self.body()
                        for field in ('name', 'reason', 'values'):
                            if not isinstance(data.get(field), str) or not data[field].strip():
                                raise ValueError('Container name, reason and key values are required')
                        if len(data['name']) > 100 or len(data['reason']) > 2000:
                            raise ValueError('Use up to 100 characters for the name and 2,000 for the reason')
                        if any(item['name'].casefold() == data['name'].strip().casefold() for item in items):
                            raise ValueError('A container with this name already exists')
                        width = int(data.get('key_width', 1))
                        if not 1 <= width <= 2000:
                            raise ValueError('Invalid number of key columns')
                        validate_scope([str(i) for i in range(width)], [], [], data['values'])
                        items.append(dict(id=uuid.uuid4().hex, name=data['name'].strip(), reason=data['reason'].strip(), values=data['values'], key_width=width))
                        atomic_write_text(target, json.dumps(items))
                    return self.json_response({'containers': items})
            if path == '/api/activity' and self.command == 'GET':
                activities=[]
                for target in self.app.root.glob('*/job.json'):
                    try:item=self.app.load(target.parent.name)
                    except (ValueError,OSError):continue
                    if self.server.shared and item.get('owner')!=self.workspace:continue
                    activities.append(dict(id=item['id'],kind='Comparison',state=item['state'],changed_at=item.get('finished',item['created'])))
                    for kind,value in item.get('exports',{}).items():activities.append(dict(id=item['id'],kind=kind+' export',state=value['state'],changed_at=value.get('updated',item['created'])))
                    if item.get('analysis'):activities.append(dict(id=item['id'],kind='Analysis',state=item['analysis']['state'],changed_at=item['analysis'].get('updated',item['created'])))
                return self.json_response(dict(activities=activities))
            if path == '/api/storage' and self.command == 'GET':
                jobs=[]
                for file in self.app.root.glob('*/job.json'):
                    if not re.fullmatch(r'[a-f0-9]{32}',file.parent.name):continue
                    try:item=self.app.load(file.parent.name)
                    except (ValueError,OSError):continue
                    if self.server.shared and item.get('owner')!=self.workspace:continue
                    sizes=dict(uploads=0,reports=0,analysis=0,temporary=0)
                    for folder,dirs,files in os.walk(file.parent,followlinks=False):
                        dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
                        for name in files:
                            target=Path(folder)/name
                            if target.is_symlink():continue
                            try:size=target.stat().st_size
                            except FileNotFoundError:continue
                            category='analysis' if name.startswith('analysis') else 'reports' if 'report' in target.relative_to(file.parent).parts or name in ('mismatches.xlsx','comparison-report.html') else 'uploads' if name in ('left.csv','right.csv','left.xlsx','right.xlsx','left.source.csv','right.source.csv') else 'temporary'
                            sizes[category]+=size
                    with self.app.lock:
                        tasks=[dict(kind=kind,state='running' if entry['future'].running() else 'queued',queued_at=entry['queued_at'],cancelling=(file.parent/('cancel-'+kind)).exists()) for (identity,kind),entry in self.app.tasks.items() if identity==item['id']]
                    jobs.append(dict(id=item['id'],created=item['created'],state=item['state'],files=item['files'],sizes=sizes,tasks=tasks,busy=self.app.busy(item)))
                disk=shutil.disk_usage(self.app.root)
                return self.json_response(dict(jobs=sorted(jobs,key=lambda item:item['created'],reverse=True),free=disk.free,total=disk.total,max_jobs=self.app.max_jobs,retention_days=self.app.retention_days))
            if path == '/api/preflight' and self.command == 'POST':
                config=self.body();size=config.get('bytes')
                if type(size) is not int or size<0 or size>200*1024**3:raise ValueError('Invalid combined upload size')
                free=shutil.disk_usage(self.app.root).free
                return self.json_response(dict(free=free,upload_bytes=size,suggested_free_bytes=size*3,can_upload=free>=size+128*1024**2,message='Plan for at least 3× combined input size for uploads, sorting and reports. This estimate is not a reservation or upper bound; many mismatches and analysis indexes need more.'))
            if path == '/api/profiles' and self.command in ('GET','POST','DELETE'):
                target=self.container_path().with_name('comparison-profiles.json')
                with self.app.lock:
                    profiles=json.loads(target.read_text()) if target.exists() else []
                    if self.command=='POST':
                        config=self.body();item=self.app.load(config.get('job_id',''))
                        if self.server.shared and item.get('owner')!=self.workspace:raise ValueError('Comparison not found')
                        if item['state'] not in ('ready','complete'):raise ValueError('Configure the comparison before saving a profile')
                        name=config.get('name','').strip()
                        if not name or len(name)>100:raise ValueError('Enter a profile name of 1–100 characters')
                        profile=dict(id=uuid.uuid4().hex,name=name,source_headers=item['source_headers'],column_headers=item['column_headers'],config=item.get('draft',{}) if item['state']=='ready' else {key:item.get(key) for key in ('keys','ignore_columns','ignore_keys','ignore_container_ids','value_overrides','comparison_rules','memory_mb','sort_workers','read_batch_size','compare_batch_size','duplicate_policy')})
                        profiles=[p for p in profiles if p['name'].casefold()!=name.casefold()]
                        if len(profiles)>=100:raise ValueError('Delete an unused profile first (maximum 100)')
                        profiles.append(profile)
                    elif self.command=='DELETE':
                        config=self.body();profiles=[p for p in profiles if p['id']!=config.get('id')]
                    if self.command!='GET':
                        encoded=json.dumps(profiles)
                        if len(encoded.encode('utf-8'))>5*1024**2:raise ValueError('Profile library exceeds 5 MiB; delete an unused profile first')
                        atomic_write_text(target,encoded)
                return self.json_response(dict(profiles=profiles))
            if self.command == 'POST' and path == '/api/jobs':
                return self.json_response(self.app.create(self.body(), self.workspace), 201)
            if self.command == 'GET' and path == '/api/jobs':
                offset = max(0, int(query.get('offset', ['0'])[0]))
                records = []
                for entry in self.app.root.glob('*/job.json'):
                    try:
                        item = self.app.load(entry.parent.name)
                    except (ValueError, OSError):
                        continue
                    if self.server.shared and item.get('owner') != self.workspace:
                        continue
                    summary = item.get('summary', {})
                    records.append(dict(id=item['id'], created=item['created'], state=item['state'],
                        files=item['files'], busy=self.app.busy(item), keys=item.get('keys', []), changed_cells=summary.get('changed_cells'),
                        changed_rows=summary.get('changed_rows'), elapsed_seconds=summary.get('elapsed_seconds')))
                records.sort(key=lambda item: item['created'], reverse=True)
                return self.json_response(dict(jobs=records[offset:offset+25], total=len(records), offset=offset, retention_days=self.app.retention_days))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(.*))?', path)
            if not match:
                return self.json_response({'error': 'Not found'}, 404)
            identity, action = match.groups()
            job = self.app.load(identity)
            if self.server.shared and job.get('owner') != self.workspace:
                return self.json_response({'error': 'Comparison not found in this browser workspace'}, 404)
            self.authorized_job = identity
            directory = self.app.directory(identity)
            if action == 'validate-rules' and self.command == 'POST':
                config=self.body()
                return self.json_response(dict(rules=validate_rules(config.get('comparison_rules',[]),job.get('columns',[]),config.get('keys',[]),config.get('ignore_columns',[]))))
            if action == 'cancel' and self.command == 'POST':
                return self.json_response(self.app.cancel(identity,self.body().get('kind')))
            if action == 'diagnostics' and self.command == 'GET':
                target=directory/'duplicate-keys.json'
                return self.json_response(json.loads(target.read_text()) if target.exists() else {})
            if action == 'apply-profile' and self.command == 'POST':
                config=self.body()
                with self.app.lock:
                    job=self.app.load(identity)
                    if job['state']!='ready':raise ValueError('Profiles can only be applied before comparison')
                    target=self.container_path().with_name('comparison-profiles.json')
                    profiles=json.loads(target.read_text()) if target.exists() else []
                    profile=next((p for p in profiles if p['id']==config.get('id')),None)
                    if not profile:raise ValueError('Profile not found')
                    layout={}
                    for side in ('left','right'):
                        aliases={old.casefold():new for old,new in zip(profile['source_headers'][side],profile['column_headers'][side])}
                        layout[side]=[aliases.get(name.casefold(),name) for name in job['source_headers'][side]]
                    aligned,changes=align_headers(job['source_headers'],layout);common=common_headers(aligned)
                    draft=profile['config'];keys=draft.get('keys') or []
                    if not keys or any(k not in common for k in keys):raise ValueError('Profile key columns are missing from these files; update headers or choose another profile')
                    ignored,_=validate_scope(keys,common,draft.get('ignore_columns') or [],draft.get('ignore_keys') or '')
                    validate_overrides(draft.get('value_overrides') or [],common,keys,ignored)
                    validate_rules(draft.get('comparison_rules') or [],common,keys,ignored)
                    target=self.container_path();containers=json.loads(target.read_text()) if target.exists() else []
                    if any(cid not in {c['id'] for c in containers} for cid in draft.get('ignore_container_ids') or []):raise ValueError('A profile ignore container is missing')
                    job.update(column_headers=layout,columns=common,draft=draft,headers_reviewed=True,header_layout_changes=changes)
                    self.app.save(job)
                return self.json_response(job)
            if action == 'annotations' and self.command in ('GET','POST'):
                if job['state']!='complete':raise ValueError('Complete a comparison before adding analysis notes')
                target=directory/'report/annotations.json'
                with self.app.lock:
                    notes=json.loads(target.read_text()) if target.exists() else []
                    if self.command=='POST':
                        current=self.app.load(identity)
                        if any(e.get('state') in ('queued','running') for e in current.get('exports',{}).values()):raise ValueError('Wait for the active export to finish before editing report notes')
                        config=self.body();column=config.get('column','');key=config.get('key');comment=config.get('comment','');status=config.get('status')
                        if column and column not in job['summary']['changed_cells_by_column']:raise ValueError('Choose a compared column')
                        if key is not None and (not isinstance(key,list) or len(key)!=len(job['keys']) or any(not isinstance(v,str) or len(v)>1000 for v in key)):raise ValueError('Invalid annotation key')
                        if not column and key is None:raise ValueError('Choose a column or an exact key for this note')
                        if status not in ('Expected','Needs investigation','Resolved') or not isinstance(comment,str) or len(comment)>2000:raise ValueError('Choose a classification and use at most 2,000 comment characters')
                        notes=[n for n in notes if (n['column'],n['key'])!=(column,key)]
                        if len(notes)>=10000:raise ValueError('Maximum 10,000 analysis notes per job')
                        notes.append(dict(column=column,key=key,status=status,comment=comment,updated=time.time()))
                        encoded=json.dumps(notes)
                        if len(encoded.encode('utf-8'))>2*1024**2:raise ValueError('Analysis notes exceed the 2 MiB limit')
                        atomic_write_text(target,encoded)
                        for export in current.get('exports',{}).values():
                            if export.get('state')=='complete':export.update(state='outdated',message='Analysis notes changed. Generate a fresh report.')
                        self.app.save(current)
                return self.json_response(dict(notes=notes))
            if self.command == 'DELETE' and action is None:
                self.app.remove_job(identity, allowed_requests=1)
                return self.json_response({'deleted': True})
            if self.command == 'GET' and action is None:
                if 'source_headers' not in job and job['state'] in ('ready', 'queued', 'running', 'complete'):
                    job['source_headers'] = {side: header(directory / f'{side}.csv', job['delimiter'], job['encoding']) for side in ('left', 'right')}
                    job.setdefault('column_headers', job['source_headers'])
                progress = directory / 'progress.json'
                if progress.exists():
                    job['progress'] = json.loads(progress.read_text())
                job['free_disk_bytes'] = shutil.disk_usage(directory).free
                return self.json_response(job)
            if self.command == 'GET' and action == 'logs':
                cursor = max(0, int(query.get('cursor', ['0'])[0]))
                path = directory / 'run.log'
                if not path.exists():
                    return self.json_response(dict(text='', cursor=0))
                with path.open('rb') as stream:
                    cursor = min(cursor, path.stat().st_size)
                    stream.seek(cursor)
                    content = stream.read(65536)
                    return self.json_response(dict(text=content.decode('utf-8', errors='replace'), cursor=stream.tell()))
            if self.command == 'GET' and action == 'source-preview':
                if job['state'] not in ('ready', 'queued', 'running', 'complete'):
                    raise ValueError('Finish uploading and preparing the selected sheets before previewing')
                side = query.get('side', ['left'])[0]
                limit = int(query.get('rows', ['10'])[0])
                start = int(query.get('column_offset', ['0'])[0])
                if side not in ('left', 'right') or not 1 <= limit <= 1000 or start < 0:
                    raise ValueError('Choose file 1 or file 2 and between 1 and 1,000 preview rows')
                rows, size = [], 0
                with (directory / f'{side}.csv').open(encoding=job['encoding'], newline='') as source:
                    reader = csv.reader(source, delimiter=job['delimiter'], strict=True)
                    original_names = normalize_headers(next(reader, None))
                    names = job.get('column_headers', {}).get(side, original_names)
                    if start >= len(names):
                        raise ValueError('Invalid column page')
                    for row in itertools.islice(reader, limit):
                        if len(row) != len(names):
                            raise ValueError('Invalid row width in preview')
                        values = [cell[:500] for cell in row[start:start+20]]
                        added = sum(len(cell.encode('utf-8')) for cell in values)
                        if size + added > 2 * 1024 * 1024:
                            break
                        size += added
                        rows.append(values)
                return self.json_response(dict(headers=names[start:start+20], rows=rows, requested_rows=limit,
                    total_columns=len(names), column_offset=start, limited=len(rows)<limit))
            if self.command == 'POST' and action == 'headers':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Column headers are locked after comparison starts')
                    source = job.get('source_headers') or {side: header(directory / f'{side}.csv', job['delimiter'], job['encoding']) for side in ('left', 'right')}
                    layout = config.get('column_headers')
                    if layout is None:
                        raise ValueError('Column headers are required')
                    aligned, changes = align_headers(source, layout)
                    # Preserve existing scope selections by their left-file position.
                    rename = dict(zip(job.get('column_headers',source)['left'], aligned['left']))
                    common = common_headers(aligned)
                    draft = job.get('draft', {})
                    for key in ('keys', 'ignore_columns'):
                        draft[key] = [rename.get(n,n) for n in (draft.get(key) or []) if rename.get(n,n) in common]
                    draft['value_overrides'] = [dict(rule, column=rename.get(rule['column'],rule['column'])) for rule in (draft.get('value_overrides') or []) if rename.get(rule['column'],rule['column']) in common]
                    draft['comparison_rules']=[dict(rule,column=rename.get(rule['column'],rule['column'])) for rule in (draft.get('comparison_rules') or []) if rename.get(rule['column'],rule['column']) in common]
                    job.update(source_headers=source, column_headers=layout, columns=common,
                               header_layout_changes=changes, headers_reviewed=True, draft=draft)
                    self.app.save(job)
                return self.json_response(job)
            if self.command == 'POST' and action == 'config':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Configuration is locked after comparison starts')
                    # Drafts are validated before execution; preserve partially filled forms.
                    job['draft'] = {key: config.get(key) for key in ('keys', 'ignore_columns', 'ignore_keys', 'memory_mb', 'comparison_rules', 'value_overrides', 'ignore_container_ids', 'sort_workers', 'read_batch_size', 'compare_batch_size', 'duplicate_policy') if key not in ('ignore_container_ids', 'sort_workers','comparison_rules','read_batch_size','compare_batch_size','duplicate_policy') or key in config}
                    self.app.save(job)
                return self.json_response({'saved': True})
            if self.command == 'PUT' and action in ('files/left', 'files/right'):
                side = action.split('/')[1]
                with self.app.lock:
                    lock = self.app.file_locks.setdefault((identity, side), threading.Lock())
                with lock:
                    job = self.app.load(identity)
                    if job['state'] != 'uploading' or job['files'][side]['complete']:
                        raise ValueError('This file is already finalized')
                    size = int(self.headers.get('Content-Length', 0))
                    offset = int(query.get('offset', ['-1'])[0])
                    target = self.app.upload_path(job, side)
                    current = target.stat().st_size if target.exists() else 0
                    if not 0 < size <= CHUNK or offset != current or current + size > job['files'][side]['size']:
                        raise ValueError(f'Invalid upload chunk. Expected offset {current}.')
                    if shutil.disk_usage(directory).free < size + 128 * 1024 * 1024:
                        raise ValueError('Server disk space is low. Free space before resuming this upload.')
                    self.connection.settimeout(120)
                    with target.open('ab') as stream:
                        remaining = size
                        try:
                            while remaining:
                                data = self.rfile.read(min(1024 * 1024, remaining))
                                if not data:
                                    raise ValueError('Upload disconnected; retry this chunk')
                                stream.write(data)
                                remaining -= len(data)
                        except Exception:
                            stream.truncate(current)
                            raise
                    # File length is the durable upload checkpoint. Avoid renaming
                    # job metadata for every chunk (a common Windows lock conflict).
                return self.json_response({'uploaded': current + size})
            if self.command == 'POST' and action == 'finalize':
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'uploading':
                        raise ValueError('Upload already finalized')
                    columns = {}
                    header_changes = []
                    for side in ('left', 'right'):
                        file = self.app.upload_path(job, side)
                        if not file.exists() or file.stat().st_size != job['files'][side]['size']:
                            raise ValueError('Both files must finish uploading')
                        if job['files'][side].get('format') == 'excel':
                            job['files'][side]['sheets'] = excel_sheets(file)
                        else:
                            columns[side], changes = read_header(file, job['delimiter'], job['encoding'])
                            header_changes.extend(dict(side=side, **change) for change in changes)
                    job['header_changes'] = header_changes
                    if any(item.get('format') == 'excel' for item in job['files'].values()):
                        for item in job['files'].values():
                            item['complete'] = True
                        job['state'] = 'selecting_sheets'
                        self.app.save(job)
                        return self.json_response(job)
                    for side in ('left', 'right'):
                        job['files'][side]['complete'] = True
                    job.update(state='ready', source_headers=columns, column_headers=columns, headers_reviewed=False, columns=common_headers(align_headers(columns)[0]))
                    self.app.save(job)
                return self.json_response(job)
            if self.command == 'POST' and action == 'select-sheets':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'selecting_sheets':
                        raise ValueError('Worksheet selection is locked after import begins')
                    for side in ('left', 'right'):
                        item = job['files'][side]
                        if item.get('format') == 'excel':
                            selected = config.get(side)
                            if selected not in [sheet['name'] for sheet in item['sheets']]:
                                raise ValueError(f'Select a worksheet for the {side} workbook')
                            item['sheet'] = selected
                    job.update(state='preparing', preparation_message='Queued for worksheet import')
                    self.app.save(job)
                    self.app.submit(identity,'import',self.app.prepare_sheets)
                return self.json_response(job, 202)
            if self.command == 'POST' and action == 'start':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Comparison is not ready to start')
                    source = job.get('source_headers') or {side: header(directory / f'{side}.csv', job['delimiter'], job['encoding']) for side in ('left', 'right')}
                    align_headers(source, job.get('column_headers'))
                    keys = config.get('keys', [])
                    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys) or any(k not in job['columns'] for k in keys):
                        raise ValueError('Select at least one valid, unique key column')
                    memory = int(config.get('memory_mb', 4096))
                    if memory not in (64, 128, 256, 512, 1024, 2048, 4096, 8192):
                        raise ValueError('Invalid memory setting')
                    if memory > self.server.max_sort_mb:
                        raise ValueError(f'The server limits each comparison to {self.server.max_sort_mb} MB sort memory')
                    sort_workers = config.get('sort_workers', 2)
                    if type(sort_workers) is not int or sort_workers not in (1, 2):
                        raise ValueError('Choose one or two sort workers')
                    ignored_columns, _ = validate_scope(keys, job['columns'], config.get('ignore_columns', []), config.get('ignore_keys', ''))
                    ids = config.get('ignore_container_ids', [])
                    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                        raise ValueError('Select valid ignore key containers')
                    library = self.container_path()
                    available = {item['id']: item for item in json.loads(library.read_text())} if library.exists() else {}
                    if any(i not in available for i in ids):
                        raise ValueError('An ignore key container no longer exists')
                    containers = [available[i] for i in dict.fromkeys(ids)]
                    for item in containers:
                        if item['key_width'] != len(keys):
                            raise ValueError(f"Container {item['name']} expects {item['key_width']} key columns")
                        validate_scope(keys, job['columns'], [], item['values'])
                    overrides, _ = validate_overrides(config.get('value_overrides', []), job['columns'], keys, ignored_columns)
                    rules=validate_rules(config.get('comparison_rules',[]),job['columns'],keys,ignored_columns)
                    job.update(**execution_settings(config),comparison_rules=rules,state='queued', keys=keys, memory_mb=memory, sort_workers=sort_workers,
                               ignore_columns=ignored_columns, ignore_keys=config.get('ignore_keys', ''), value_overrides=overrides,
                               ignore_container_ids=ids, ignore_key_containers=containers)
                    self.app.save(job)
                    with (directory / 'run.log').open('a') as log:
                        log.write(f'{time.strftime("%H:%M:%S")}  Comparison queued\n')
                    self.app.submit(identity,'comparison',self.app.run_comparison)
                return self.json_response(job, 202)
            if self.command == 'POST' and action in ('export/excel', 'export/html'):
                kind = action.split('/')[1]
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'complete':
                        raise ValueError('Wait for comparison to complete')
                    if job['exports'].get(kind, {}).get('state') not in ('queued', 'running', 'complete'):
                        job['exports'][kind] = {'state': 'queued'}
                        self.app.save(job)
                        self.app.submit(identity,kind,self.app.run_export,kind)
                return self.json_response(job, 202)
            if action == 'resume' and self.command == 'POST':
                with self.app.lock:
                    job=self.app.load(identity)
                    if not job.get('can_resume') or self.app.busy(job):
                        raise ValueError('This job has no resumable comparison, or is already active')
                    job.update(state='queued',error='',finished=None)
                    self.app.save(job)
                    self.app.submit(identity,'comparison',self.app.run_comparison)
                return self.json_response(job,202)
            if action == 'analysis' and self.command in ('GET','POST'):
                if job['state'] != 'complete':
                    raise ValueError('Complete the comparison before analysis')
                if self.command == 'POST':
                    with self.app.lock:
                        job=self.app.load(identity)
                        if job.get('analysis',{}).get('state') not in ('queued','running','complete'):
                            job['analysis']=dict(state='queued',message='Waiting for a server worker…')
                            self.app.save(job)
                            self.app.submit(identity,'analysis',self.app.run_analysis)
                    return self.json_response(job['analysis'],202)
                state=job.get('analysis',{'state':'not_started'})
                if state['state'] != 'complete': return self.json_response(state)
                key=json.loads(query['key'][0]) if 'key' in query else None
                if key is not None and (not isinstance(key,list) or len(key)!=len(job['keys']) or any(not isinstance(v,str) for v in key)):
                    raise ValueError('Supply an exact text key with one value per key column')
                result=query_index(directory/'analysis.sqlite',query.get('mode',['keys'])[0],int(query.get('offset',['0'])[0]),query.get('column',[''])[0],key)
                return self.json_response(dict(state='complete',**result))
            if self.command == 'GET' and action == 'preview':
                if job['state'] != 'complete':
                    raise ValueError('Results are not ready')
                category = query.get('category', ['differences'])[0]
                if category not in ('differences', 'left_only', 'right_only'):
                    raise ValueError('Unknown category')
                with (directory / 'report' / f'{category}.csv').open(encoding='utf-8', newline='') as stream:
                    reader = csv.reader(stream)
                    headers = next(reader)
                    if category == 'differences':
                        headers = [f'Key: {key}' for key in job['keys']] + ['Column', 'Left value', 'Right value']
                    # Bound response size for unusually long values or composite keys.
                    rows, cells = [], 0
                    for row in itertools.islice(reader, 100):
                        if category == 'differences':
                            row = json.loads(row[0]) + row[1:]
                        rows.append([v[:1000] for v in row])
                        cells += sum(len(v) for v in rows[-1])
                        if cells > 250000:
                            break
                return self.json_response(dict(headers=headers, rows=rows))
            if self.command == 'GET' and action and action.startswith('download/'):
                name = action.split('/')[1]
                if job['state'] != 'complete' and name != 'run.log':
                    raise ValueError('Results are not ready')
                kind = {'mismatches.xlsx': 'excel', 'comparison-report.html': 'html'}.get(name)
                if name == 'run.log' and (directory / name).exists():
                    target = directory / name
                elif kind and job['exports'].get(kind, {}).get('state') == 'complete':
                    target = directory / name
                elif name in ('duplicate_keys.csv', 'differences.csv', 'left_only.csv', 'right_only.csv', 'summary.html', 'summary.json'):
                    target = directory / 'report' / name
                else:
                    raise ValueError('Report not available yet')
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(target.stat().st_size))
                self.send_header('Content-Disposition', f'attachment; filename="{name}"')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                with target.open('rb') as stream:
                    shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
                return
            return self.json_response({'error': 'Not found'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, TypeError, KeyError, OSError, csv.Error) as error:
            self.json_response({'error': str(error)}, 400)
        except Exception as error:
            self.json_response({'error': 'Unexpected server error: ' + str(error)}, 500)

    def guarded_dispatch(self):
        self.authorized_job = None
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/.*)?', urlsplit(self.path).path)
        identity = match.group(1) if match else None
        if identity:
            with self.app.lock:
                self.app.requests[identity] = self.app.requests.get(identity, 0) + 1
        try:
            self.dispatch()
        finally:
            if identity:
                with self.app.lock:
                    self.app.requests[identity] -= 1
                    if not self.app.requests[identity]:
                        del self.app.requests[identity]
                    # Recent upload/configuration activity protects abandoned drafts from expiry.
                    path = self.app.directory(identity) / 'job.json'
                    if getattr(self, 'authorized_job', None) == identity and self.command in ('POST', 'PUT') and path.exists():
                        os.utime(path, None)

    do_GET = guarded_dispatch
    do_POST = guarded_dispatch
    do_PUT = guarded_dispatch
    do_DELETE = guarded_dispatch


def make_server(root, port=8765, host='127.0.0.1', public_url=None, max_jobs=1, max_sort_mb=8192):
    if not 1 <= max_jobs <= 5:
        raise ValueError('Concurrent comparisons must be between 1 and 5')
    if max_sort_mb not in (64,128,256,512,1024,2048,4096,8192):
        raise ValueError('Invalid server sort memory limit')
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Public URL must be an http(s) origin, without a path or credentials')
        public_url = public_url.rstrip('/')
    if host not in ('127.0.0.1', 'localhost') and not public_url:
        raise ValueError('Network binding requires --public-url, for example http://server-name:8765')
    server = ThreadingHTTPServer((host, port), Handler)
    server.shared = public_url is not None
    server.public_url = public_url
    server.max_sort_mb = max_sort_mb
    server.app = Application(root, max_jobs)
    return server


def lock_data_directory(root):
    """One server owns a data folder; independent workers do not take this lock."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / '.server.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('Another server is already using this data directory')
    return handle


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--public-url', help='Team URL, such as http://compare.internal:8765; enables separate browser workspaces without login')
    parser.add_argument('--max-jobs', type=int, default=1, help='Maximum simultaneous comparisons/imports/exports; other jobs queue')
    parser.add_argument('--max-sort-mb', type=int, default=4096, help='Maximum sort budget for each comparison')
    parser.add_argument('--data-dir', default=str(BASE / 'data'), help='Upload, scratch and report storage; use a fast SSD')
    parser.add_argument('--retention-days', type=int, default=7, help='Delete inactive jobs after this many days; 0 disables automatic cleanup')
    parser.add_argument('--open', action='store_true', help='Open the UI in your default browser')
    args = parser.parse_args()
    if args.retention_days < 0:
        parser.error('--retention-days must be zero or positive')
    data_lock = lock_data_directory(args.data_dir)
    server = make_server(args.data_dir, args.port, args.host, args.public_url, args.max_jobs, args.max_sort_mb)
    server.app.retention_days = args.retention_days
    cleanup_stop = threading.Event()
    def cleanup_loop():
        while not cleanup_stop.is_set():
            server.app.cleanup()
            cleanup_stop.wait(3600)
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    url = args.public_url or f'http://127.0.0.1:{server.server_address[1]}'
    print(f'CSV Compare is ready at {url}', flush=True)
    print(f'Data directory: {server.app.root}', flush=True)
    print(f'Concurrent heavy jobs: {args.max_jobs}; per-job sort budget limit: {args.max_sort_mb} MB', flush=True)
    if server.shared:
        print('Team mode: no login; separate browser workspaces. Keep access on a trusted internal network.', flush=True)
    if args.open:
        webbrowser.open(url)
    stopping = threading.Event()
    def stop(signum, frame):
        if not stopping.is_set():
            stopping.set()
            print('Stopping new requests; finishing queued and active work before exit…', flush=True)
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        cleanup_stop.set()
        cleanup_thread.join()
        server.server_close()
        server.app.pool.shutdown(wait=True)
        data_lock.close()
