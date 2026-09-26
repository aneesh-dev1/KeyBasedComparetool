#!/usr/bin/env python3
"""Local CSV comparison UI. Run: python3 server.py"""
import argparse
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

from json_compare import compare_json
from excel_input import sheets as excel_sheets, convert as convert_excel
from file_io import atomic_write_text, replace_retry
from compare import read_header, normalize_headers, header, validate_scope, validate_overrides
from reports import export_excel, export_html, make_summary_html

BASE = Path(__file__).resolve().parent
CHUNK = 8 * 1024 * 1024


class Application:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.file_locks = {}
        self.pool = ThreadPoolExecutor(max_workers=1)  # Bound expensive work globally.
        csv.field_size_limit(64 * 1024 * 1024)
        for path in self.root.glob('*/job.json'):
            job = json.loads(path.read_text())
            changed = False
            if job['state'] in ('queued', 'running', 'preparing'):
                job.update(state='error', error='Server stopped before comparison completed. Start a new comparison.')
                changed = True
            for export in job.get('exports', {}).values():
                if export['state'] in ('queued', 'running'):
                    export.update(state='error', error='Export interrupted. Generate it again.')
                    changed = True
            if changed:
                self.save(job)

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

    def create(self, config):
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
        job = dict(id=identity, state='uploading', created=time.time(), files=files, delimiter=delimiter, encoding=encoding, exports={})
        self.save(job)
        return job

    def upload_path(self, job, side):
        return self.directory(job['id']) / (f'{side}.xlsx' if job['files'][side].get('format') == 'excel' else f'{side}.csv')

    def prepare_sheets(self, identity):
        job = self.load(identity)
        directory = self.directory(identity)
        def notify(message):
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
                        for row in csv.reader(stream, delimiter=job['delimiter'], strict=True):
                            writer.writerow(row)
                    replace_retry(source, directory / f'{side}.source.csv')
                    replace_retry(temporary, source)
                    columns[side] = header(source, ',', 'utf-8')
            if set(columns['left']) != set(columns['right']):
                raise ValueError('Selected sheets/files must have the same set of column headers')
            self.patch(identity, state='ready', columns=columns['left'], delimiter=',', encoding='utf-8', preparation_message='Selected sheets are ready')
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
            summary = json.loads((directory / 'report/summary.json').read_text())
            summary['sources'] = [dict(side=side, file=job['files'][side]['name'], sheet=job['files'][side].get('sheet')) for side in ('left', 'right')]
            (directory / 'report/summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
            (directory / 'report/summary.html').write_text(make_summary_html(summary), encoding='utf-8')
            with (directory / 'run.log').open('a') as log:
                log.write(f'{time.strftime("%H:%M:%S")}  Comparison complete | changed cells={summary["changed_cells"]:,}\n')
            self.patch(identity, state='complete', summary=summary, finished=time.time())
        except Exception as error:
            self.patch(identity, state='error', error=str(error), finished=time.time())

    def run_export(self, identity, kind):
        def status(state, **values):
            with self.lock:
                job = self.load(identity)
                job['exports'][kind] = dict(state=state, **values)
                self.save(job)
        directory = self.directory(identity)
        target = directory / ('mismatches.xlsx' if kind == 'excel' else 'html.zip')
        temporary = target.with_suffix(target.suffix + '.part')
        last_update = [0.0]
        def notify(message):
            if time.monotonic() - last_update[0] > 1:
                status('running', message=message)
                last_update[0] = time.monotonic()
        status('running', message='Preparing report')
        try:
            (export_excel if kind == 'excel' else export_html)(directory / 'report', temporary, notify)
            replace_retry(temporary, target)
            status('complete', size=target.stat().st_size)
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

    def check_request(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            raise ValueError('Use the local application address')
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}:
            raise ValueError('Cross-origin request rejected')
        url = urlsplit(self.path)
        if url.path.startswith('/api/'):
            token = self.headers.get('X-App-Token') or parse_qs(url.query).get('token', [''])[0]
            if not secrets.compare_digest(token, self.app.token):
                raise ValueError('Session expired. Reload this page.')
        return url.path, parse_qs(url.query)

    def dispatch(self):
        try:
            path, query = self.check_request()
            if self.command == 'GET' and path in ('/', '/app.js', '/style.css', '/assets/transunion-logo.svg'):
                target = BASE / 'web' / ('index.html' if path == '/' else path[1:])
                data = target.read_bytes().replace(b'__APP_TOKEN__', self.app.token.encode())
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target)[0] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == '/api/json-compare' and self.command == 'POST':
                config = self.body(64 * 1024 * 1024)
                result = self.app.pool.submit(compare_json, config.get('left'), config.get('right'), config.get('default_order', 'ordered'), config.get('rules', [])).result()
                return self.json_response(result)
            if path == '/api/key-containers' and self.command in ('GET', 'POST'):
                with self.app.lock:
                    target = self.app.root / 'key-containers.json'
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
            if self.command == 'POST' and path == '/api/jobs':
                return self.json_response(self.app.create(self.body()), 201)
            if self.command == 'GET' and path == '/api/jobs':
                offset = max(0, int(query.get('offset', ['0'])[0]))
                records = []
                for entry in self.app.root.glob('*/job.json'):
                    try:
                        item = self.app.load(entry.parent.name)
                    except (ValueError, OSError):
                        continue
                    summary = item.get('summary', {})
                    records.append(dict(id=item['id'], created=item['created'], state=item['state'],
                        files=item['files'], keys=item.get('keys', []), changed_cells=summary.get('changed_cells'),
                        changed_rows=summary.get('changed_rows'), elapsed_seconds=summary.get('elapsed_seconds')))
                records.sort(key=lambda item: item['created'], reverse=True)
                return self.json_response(dict(jobs=records[offset:offset+25], total=len(records), offset=offset))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(.*))?', path)
            if not match:
                return self.json_response({'error': 'Not found'}, 404)
            identity, action = match.groups()
            job = self.app.load(identity)
            directory = self.app.directory(identity)
            if self.command == 'GET' and action is None:
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
                    names = normalize_headers(next(reader, None))
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
            if self.command == 'POST' and action == 'config':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Configuration is locked after comparison starts')
                    # Drafts are validated before execution; preserve partially filled forms.
                    job['draft'] = {key: config.get(key) for key in ('keys', 'ignore_columns', 'ignore_keys', 'memory_mb', 'value_overrides', 'ignore_container_ids', 'sort_workers') if key not in ('ignore_container_ids', 'sort_workers') or key in config}
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
                    if set(columns['left']) != set(columns['right']):
                        raise ValueError('Column names differ between files. Both files must have the same set of columns.')
                    for side in ('left', 'right'):
                        job['files'][side]['complete'] = True
                    job.update(state='ready', columns=columns['left'])
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
                    self.app.pool.submit(self.app.prepare_sheets, identity)
                return self.json_response(job, 202)
            if self.command == 'POST' and action == 'start':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Comparison is not ready to start')
                    keys = config.get('keys', [])
                    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys) or any(k not in job['columns'] for k in keys):
                        raise ValueError('Select at least one valid, unique key column')
                    memory = int(config.get('memory_mb', 4096))
                    if memory not in (64, 128, 256, 512, 1024, 2048, 4096, 8192):
                        raise ValueError('Invalid memory setting')
                    sort_workers = config.get('sort_workers', 2)
                    if type(sort_workers) is not int or sort_workers not in (1, 2):
                        raise ValueError('Choose one or two sort workers')
                    ignored_columns, _ = validate_scope(keys, job['columns'], config.get('ignore_columns', []), config.get('ignore_keys', ''))
                    ids = config.get('ignore_container_ids', [])
                    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                        raise ValueError('Select valid ignore key containers')
                    library = self.app.root / 'key-containers.json'
                    available = {item['id']: item for item in json.loads(library.read_text())} if library.exists() else {}
                    if any(i not in available for i in ids):
                        raise ValueError('An ignore key container no longer exists')
                    containers = [available[i] for i in dict.fromkeys(ids)]
                    for item in containers:
                        if item['key_width'] != len(keys):
                            raise ValueError(f"Container {item['name']} expects {item['key_width']} key columns")
                        validate_scope(keys, job['columns'], [], item['values'])
                    overrides, _ = validate_overrides(config.get('value_overrides', []), job['columns'], keys, ignored_columns)
                    job.update(state='queued', keys=keys, memory_mb=memory, sort_workers=sort_workers,
                               ignore_columns=ignored_columns, ignore_keys=config.get('ignore_keys', ''), value_overrides=overrides,
                               ignore_container_ids=ids, ignore_key_containers=containers)
                    self.app.save(job)
                    with (directory / 'run.log').open('a') as log:
                        log.write(f'{time.strftime("%H:%M:%S")}  Comparison queued\n')
                    self.app.pool.submit(self.app.run_comparison, identity)
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
                        self.app.pool.submit(self.app.run_export, identity, kind)
                return self.json_response(job, 202)
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
                kind = {'mismatches.xlsx': 'excel', 'html.zip': 'html'}.get(name)
                if name == 'run.log' and (directory / name).exists():
                    target = directory / name
                elif kind and job['exports'].get(kind, {}).get('state') == 'complete':
                    target = directory / name
                elif name in ('differences.csv', 'left_only.csv', 'right_only.csv', 'summary.html', 'summary.json'):
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

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch


def make_server(root, port=8765):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app = Application(root)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=str(BASE / 'data'), help='Upload, scratch and report storage; use a fast SSD')
    parser.add_argument('--open', action='store_true', help='Open the UI in your default browser')
    args = parser.parse_args()
    server = make_server(args.data_dir, args.port)
    print(f'CSV Compare is ready at http://127.0.0.1:{server.server_address[1]}', flush=True)
    print(f'Data directory: {server.app.root}', flush=True)
    if args.open:
        webbrowser.open(f'http://127.0.0.1:{server.server_address[1]}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nFinishing active work before shutdown…', flush=True)
    finally:
        server.server_close()
        server.app.pool.shutdown(wait=True)
