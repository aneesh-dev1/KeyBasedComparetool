#!/usr/bin/env python3
"""Local CSV comparison UI. Run: python3 server.py"""
import argparse
from http.cookies import SimpleCookie
import signal
from concurrent.futures import ThreadPoolExecutor
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
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
import zlib

from workspace_settings import PERFORMANCE, validate_settings, resolved_rules
from task_control import check_cancel, Cancelled
from comparison_rules import validate_rules
from projects import Store as ProjectStore, database as project_database, job_snapshot
from analysis import build_index, query_index, INDEX_VERSION
from json_compare import compare_json, discover_arrays
from excel_input import sheets as excel_sheets, convert as convert_excel
from file_io import atomic_write_text, replace_retry
from compare import execution_settings, common_headers, align_headers, read_header, normalize_headers, header, validate_scope, validate_overrides
from reports import export_excel, export_html, make_summary_html, REPORT_VERSION
# Reports that include comments and rebuild themselves when comments change, after this quiet period.
AUTO_REFRESH = ('html', 'excel')
NOTES_SETTLE_SECONDS = 3
import run_diff
import text_dialect


UPLOAD_FILES = {f'{side}{suffix}' for side in ('left', 'right') for suffix in ('.csv', '.xlsx', '.parquet', '.source.csv', '.fixed-source')}


def job_sizes(directory):
    """Bytes a job uses on disk, by kind: uploads, reports, analysis (rebuildable) and temporary files."""
    sizes = dict(uploads=0, reports=0, analysis=0, temporary=0)
    directory = Path(directory)
    for folder, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(folder) / name).is_symlink()]
        for name in files:
            target = Path(folder) / name
            if target.is_symlink():
                continue
            try:
                size = target.stat().st_size
            except FileNotFoundError:
                continue
            if name.startswith('analysis'):
                kind = 'analysis'
            elif 'report' in target.relative_to(directory).parts or name in ('mismatches.xlsx', 'comparison-report.html'):
                kind = 'reports'
            elif name in UPLOAD_FILES:
                kind = 'uploads'
            else:
                kind = 'temporary'
            sizes[kind] += size
    return sizes


def side_dialect(job, side):
    """(delimiter, encoding, has_header) for one file. Older jobs share one delimiter and encoding."""
    chosen = (job.get('dialects') or {}).get(side) or {}
    return (chosen.get('delimiter', job.get('delimiter', ',')), chosen.get('encoding', job.get('encoding', 'utf-8-sig')),
            chosen.get('header', True))


def resolve_dialect(job, side, path):
    """Apply the job's text settings to one file, detecting anything left on 'auto'."""
    return text_dialect.detect(path, job.get('delimiter', ','), job.get('encoding', 'utf-8-sig'), job.get('header', 'yes'))

BASE = Path(__file__).resolve().parent
CHUNK = 8 * 1024 * 1024
# Fonts are served verbatim; every other static file may carry page placeholders.
FONT_FILES = ('/assets/fonts/inter-latin-wght.woff2', '/assets/fonts/jetbrains-mono-latin-wght.woff2')
STATIC_FILES = ('/', '/app.js', '/upload-transport.js', '/style.css', '/enterprise.js', '/enterprise.css',
                '/tokens.css', '/components.css', '/shell.css', '/boot.js',
                '/assets/transunion-logo.svg', '/assets/transunion-loader.svg', '/assets/tu-favicon.svg',
                '/assets/lucide-sprite.svg') + FONT_FILES


class UploadOffsetConflict(ValueError):
    def __init__(self, offset):
        self.offset = offset
        super().__init__(f'Invalid upload chunk. Expected offset {offset}.')


def resume_strategy(directory,job):
    checkpoints=Path(directory)/'checkpoints'
    if not (checkpoints/'identity.json').exists():return None
    if (checkpoints/'partitioned'/'manifest.json').exists():return 'partitioned'
    if job.get('comparison_engine','sort')=='sort' or any((checkpoints/(side+'.json')).exists() for side in ('left','right')):
        return 'sort'
    return 'hash'


def resume_message(directory,job):
    strategy=resume_strategy(directory,job)
    if strategy=='sort':return 'Resume this job to reuse validated sort batches.'
    if strategy=='partitioned':return 'Resume this job to continue from the key ranges that already finished; sources will be validated again.'
    if strategy=='hash':return 'Resume this job to rebuild its in-memory key lookup; sources will be validated again.'
    return 'Start a new comparison.'


class Application:
    def __init__(self, root, max_jobs=1):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        from projects import migrate_workspaces
        migrate_workspaces(self.root)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.file_locks = {}
        self.upload_offsets = {}
        self.requests = {}
        self.cleanup_lock = threading.Lock()
        self.retention_days = 7
        self.analysis_retention_days = 3
        # Comparisons share a bounded lane; reports, analysis and imports have their own so they never wait
        # behind a long comparison. CPUs and memory are split between running comparisons by allocate().
        self.pool = ThreadPoolExecutor(max_workers=max_jobs)
        self.light_pool = ThreadPoolExecutor(max_workers=2)
        self.allocations = {}
        self.resource_ready = threading.Condition(self.lock)
        self.json_lock = threading.Lock()
        self.tasks = {}
        self.max_jobs = max_jobs
        # When each job's comments last changed; reports that include comments rebuild once they settle.
        self.notes_at = {}
        csv.field_size_limit(64 * 1024 * 1024)
        for path in self.root.glob('*/job.json'):
            if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                continue
            job = json.loads(path.read_text())
            self.cleanup_upload_journal_temps(job['id'])
            if job['state'] == 'uploading':
                for side in ('left', 'right'):
                    if side not in job.get('source_paths', {}):
                        self.recover_upload(job, side)
            changed = False
            if job['state'] in ('queued', 'running', 'preparing'):
                job.update(state='error', error='Server stopped before comparison completed. '+resume_message(path.parent,job))
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
            else:
                ProjectStore(project_database(self.root)).sync(job_snapshot(job))

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
            future=(self.pool if kind=='comparison' else self.light_pool).submit(run)
            self.tasks[(identity,kind)]={'future':future,'queued_at':time.time()}

    def allocate(self, identity, weight):
        """Reserve non-overlapping CPU and memory shares; None means stay queued.

        Leave one CPU for requests/reports on multi-core hosts. A running job keeps
        its allocation; another job waits instead of oversubscribing the same cores.
        """
        from runtime_resources import cpu_capacity
        with self.lock:
            capacity = max(1, cpu_capacity() - 1)
            free = capacity - sum(item['cpus'] for item in self.allocations.values())
            if free <= 0:
                return None
            given = min(16, free)
            allocation = dict(weight=weight, cpus=given, memory_share=given / capacity,
                              concurrent=len(self.allocations) + 1)
            self.allocations[identity] = allocation
            return allocation

    def release(self, identity):
        with self.resource_ready:
            self.allocations.pop(identity, None)
            self.resource_ready.notify_all()

    def mark_cancelled(self,identity,kind):
        job=self.load(identity)
        if kind=='comparison' and job['state']=='complete':return
        if kind in ('excel','html','ai'):
            job['exports'][kind]=dict(state='cancelled',updated=time.time(),message='Export cancelled. Generate it again when ready.')
        elif kind=='analysis':job['analysis']=dict(state='cancelled',updated=time.time(),message='Analysis cancelled. Use Retry to prepare it again.')
        else:job.update(state='cancelled',error='Cancelled by user. '+resume_message(self.directory(identity),job),finished=time.time())
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
                # A file read from a server path is complete from the start; only uploads report progress.
                for side in [side for side in ('left', 'right') if side not in job.get('source_paths', {})]:
                    source = self.upload_path(job, side)
                    job['files'][side]['uploaded'] = self.upload_offsets.get(
                        (identity, side), source.stat().st_size if source.exists() else 0)
            for side in ('left', 'right'):
                job['files'][side]['in_flight'] = job['state'] == 'uploading' and (identity, side) in self.upload_offsets
            job['upload_compression'] = ['gzip']
            if job.get('analysis',{}).get('state')=='complete' and job['analysis'].get('version')!=INDEX_VERSION:
                job['analysis']=dict(state='not_started',message='Prepare analysis to include mismatch patterns.')
            for export in job.get('exports', {}).values():
                if export.get('state') == 'complete' and export.get('report_version') != REPORT_VERSION:
                    export.update(state='outdated', message='New report layout available. Generate this export again.')
            job['resume_strategy']=resume_strategy(self.directory(identity),job)
            job['can_resume']=job['state'] in ('error','cancelled') and job['resume_strategy'] is not None
        return job

    def sign_off_project(self, store, identity, config):
        """Sign off a project on its latest round. At 100% match that's all; otherwise every column still
        mismatching (and keys found in only one file) needs a reason. Builds the final project report with every
        round's HTML report attached, and records the sign-off."""
        detail = store.detail(identity)
        state = detail['progress']
        if state['running']:
            raise ValueError('Wait for the running round to finish before signing off')
        if not state['rounds']:
            raise ValueError('Run at least one round before signing off')
        reviewer, note = config.get('reviewer', ''), config.get('note', '')
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 100:
            raise ValueError('Enter your name (up to 100 characters) to sign off')
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('Keep the note under 2,000 characters')
        latest = state['rounds'][-1]
        record = next((r for r in detail['records'] if r['id'] == latest['id']), None) or store.records(identity=latest['id'])['records'][0]
        given = config.get('reasons') or {}
        if not isinstance(given, dict):
            raise ValueError('Give the reasons as a list per column')
        known = {item['column']: item for group in ('still', 'new') for item in (state['issues'] or {}).get(group, {}).get('items', [])}
        columns, missing = [], []
        # One shared reason covers every column without a reason of its own (rounds can have hundreds).
        others = given.get('others', '') if isinstance(given.get('others', ''), str) else ''
        for column, cells in (record.get('mismatch_columns') or {}).items():
            reason = (given.get('columns') or {}).get(column, '')
            if (not isinstance(reason, str) or not reason.strip()) and others.strip():
                reason = others
            if not isinstance(reason, str) or not reason.strip():
                missing.append(column)
            elif len(reason) > 2000:
                raise ValueError(f'Keep the reason for {column} under 2,000 characters')
            columns.append(dict(column=column, cells=cells, since=f"round {known[column]['since']}" if column in known else '—', reason=(reason or '').strip()))
        columns.sort(key=lambda item: -item['cells'])
        keys_reason = given.get('keys', '') if isinstance(given.get('keys', ''), str) else ''
        if (latest['left_only'] or latest['right_only']) and not keys_reason.strip():
            missing.append('keys found in only one file')
        if missing:
            raise ValueError('Give a reason for ' + ', '.join(missing[:5]) + (f' and {len(missing) - 5:,} more' if len(missing) > 5 else ''))
        mode = 'clean' if latest['clean'] else 'exceptions'
        reasons = dict(columns=columns, keys=keys_reason.strip()) if mode == 'exceptions' else dict(columns=[], keys='')
        signoff = dict(at=time.time(), reviewer=reviewer.strip(), note=note.strip(), mode=mode, reasons=reasons, round_id=latest['id'])
        target = self.signoff_report(store, identity, signoff)
        store.sign_off(identity, latest['id'], signoff['reviewer'], signoff['note'], mode, reasons, target)
        return store.detail(identity)

    def signoff_report(self, store, identity, signoff):
        """Build a sign-off's HTML report from the project's data, with every round's report inside it."""
        from reports import project_signoff_html
        detail = store.detail(identity)
        state = detail['progress']
        record = store.records(identity=signoff['round_id'])['records'][0]
        attachments = []
        for round_ in state['rounds']:
            target = self.directory(round_['id']) / 'comparison-report.html'
            job_file = self.directory(round_['id']) / 'job.json'
            # Rebuild a round's report when it is missing or was built by an older report version.
            stale = job_file.exists() and self.load(round_['id']).get('exports', {}).get('html', {}).get('report_version') != REPORT_VERSION
            if (not target.exists() or stale) and job_file.exists():
                try:
                    self._run_export(round_['id'], 'html')
                except Exception:
                    pass
            attachments.append(dict(id=round_['id'], content=target.read_bytes() if target.exists() else None))
        folder = self.root / 'project-reports' / identity
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"signoff-{int(signoff['at'])}.html"
        atomic_write_text(target, project_signoff_html(detail['project'], state, record, signoff, attachments))
        return target

    def round_sample(self, sample, keys):
        """A project round's sample: its size, a seed so it can be drawn again, and for carry-forward the earlier
        round whose keys it re-checks. That round must have been sampled on the same key columns."""
        if not isinstance(sample, dict):
            raise ValueError('Choose a valid sample')
        size, mode, seed = sample.get('size'), sample.get('mode', 'random'), sample.get('seed')
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 50_000_000:
            raise ValueError('Choose a sample of 1 to 50,000,000 keys')
        if mode not in ('random', 'carry'):
            raise ValueError('Choose random keys, or the previous round’s keys plus more')
        if seed is None:
            seed = secrets.randbelow(1_000_000)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 10 ** 9:
            raise ValueError('Use a whole-number seed below 1,000,000,000')
        result = dict(size=size, mode=mode, seed=seed)
        if mode == 'carry':
            source = str(sample.get('from', ''))
            if not re.fullmatch(r'[a-f0-9]{32}', source) or not (self.directory(source) / 'job.json').exists():
                raise ValueError('Choose the earlier round to carry keys from')
            if self.load(source).get('keys') != keys:
                raise ValueError('The earlier round matched on different key columns, so its keys can’t be carried forward')
            from sampling import load_keys
            carried = load_keys(self.directory(source) / 'report')
            if carried is None:
                raise ValueError('The earlier round compared every key in its files, so there’s no sample to carry forward. Choose random keys instead.')
            if len(carried) > size:
                raise ValueError(f'The earlier round compared {len(carried):,} keys. Choose a sample of at least that many.')
            result['from'] = source
        return result

    def save(self, job):
        with self.lock:
            path = self.directory(job['id']) / 'job.json'
            atomic_write_text(path, json.dumps(job))
            ProjectStore(project_database(self.root,job.get('owner'))).sync(job_snapshot(job))

    def patch(self, identity, **values):
        with self.lock:
            job = self.load(identity)
            job.update(values)
            self.save(job)

    def create(self, config, owner=None, source_paths=None):
        delimiter = config.get('delimiter', ',')
        encoding = config.get('encoding', 'utf-8-sig')
        has_header = config.get('header', 'yes')
        # 'auto' settings are resolved per file once its bytes are on the server (see finalize).
        if not (delimiter == 'auto' or text_dialect.valid_delimiter(delimiter)) or encoding not in ('auto', *text_dialect.ENCODINGS):
            raise ValueError('Unsupported delimiter or encoding')
        if has_header not in ('auto', 'yes', 'no'):
            raise ValueError('First-row setting must be auto, yes or no')
        files = config.get('files', {})
        for side in ('left', 'right'):
            if not isinstance(files.get(side), dict) or not isinstance(files[side].get('size'), int) or not 0 < files[side]['size'] <= 100 * 1024**3:
                raise ValueError('Select two nonempty CSV, Excel or Parquet files (up to 100 GB each)')
            name = str(files[side].get('name', side))[:255]
            suffix = Path(name).suffix.lower()
            if suffix not in (*text_dialect.TEXT_SUFFIXES, '.xlsx', '.xlsm', '.parquet'):
                raise ValueError('Choose CSV, DAT/TXT/TSV, Parquet, .xlsx or .xlsm files. Save older .xls workbooks as .xlsx first.')
            files[side] = dict(name=name, size=files[side]['size'], uploaded=0, complete=False,
                               format='excel' if suffix in ('.xlsx', '.xlsm') else 'parquet' if suffix=='.parquet' else 'csv')
        if any(f['format']=='parquet' for f in files.values()):
            from parquet_input import arrow
            arrow()
        identity = uuid.uuid4().hex
        self.directory(identity).mkdir()
        job = dict(id=identity, owner=owner, state='uploading', created=time.time(), files=files, delimiter=delimiter, encoding=encoding, header=has_header, comparison_engine='auto', exports={}, upload_compression=['gzip'])
        if source_paths:job['source_paths']=source_paths
        # Only uploaded files are copied to the server; a server path is read where it is.
        required=sum(f['size'] for side,f in files.items() if side not in (source_paths or {}))
        if shutil.disk_usage(self.root).free<required+128*1024**2:
            self.directory(identity).rmdir()
            raise ValueError('Insufficient server disk space for the uploaded files. Clean up old jobs first.')
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
            ProjectStore(project_database(self.root,job.get('owner'))).mark_removed(identity)
        # Rename atomically removes the job from history before reclaiming large files.
        shutil.rmtree(trash)

    def compare_runs(self, store, project, query):
        """Two completed runs of one project side by side: counts, columns, settings and, when the runs match on the
        same keys and both still have their results, the cells that are new, resolved or changed."""
        runs = store.runs(project)
        if len(runs) < 2:
            raise ValueError('This project needs two completed runs to compare')
        records = {run['id']: run for run in runs}
        base, target = query.get('base', [''])[0], query.get('target', [''])[0]
        if not base and not target:
            base, target = runs[-2]['id'], runs[-1]['id']
        if base not in records or target not in records:
            raise ValueError('Choose two completed runs of this project')
        if base == target:
            raise ValueError('Choose two different runs to compare')
        jobs = {identity: self.load(identity) if (self.directory(identity) / 'job.json').exists() else None for identity in (base, target)}
        # A deleted run keeps its audit summary in the project, so its totals can still be compared.
        a, b = (jobs[identity] or dict(summary=records[identity]) for identity in (base, target))
        counts, columns = run_diff.summary_changes(a, b)
        result = dict(base=base, target=target, counts=counts, columns=columns,
                      settings=run_diff.setting_changes(a, b) if all(jobs.values()) else [],
                      runs=[dict(id=run['id'], title=run['title'], finished=run.get('finished') or run['created'],
                                 changed_cells=run.get('changed_cells'), audit_status=run['audit_status']) for run in runs])
        reports = [self.directory(identity) / 'report' for identity in (base, target)]
        if not all(jobs.values()) or not all((report / 'differences.csv').exists() for report in reports):
            result['cells_unavailable'] = 'The detailed results of one of these runs were deleted, so only their totals can be compared.'
        elif a.get('keys') != b.get('keys'):
            result['cells_unavailable'] = 'These runs match rows on different key columns, so their cells cannot be paired.'
        else:
            path = run_diff.build(reports[0], reports[1], self.directory(target) / 'run-diffs' / (base + '.sqlite'))
            offset = max(0, int(query.get('offset', ['0'])[0]))
            result['cells'] = run_diff.cells(path, query.get('status', ['new'])[0], query.get('column', [''])[0], offset)
        return result

    def clear_analysis(self, identity, message):
        """Delete a job's rebuildable analysis index. Returns the bytes freed; busy jobs are left alone."""
        with self.lock:
            job = self.load(identity)
            if self.busy(job):
                raise ValueError('Wait for the running task on this job to finish')
            path = self.directory(identity) / 'analysis.sqlite'
            freed = path.stat().st_size if path.exists() else 0
            path.unlink(missing_ok=True)
            if job.get('analysis'):
                job['analysis'] = dict(state='not_started', message=message, cleared=time.time())
                self.save(job)
        return freed

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
                        ProjectStore(project_database(self.root,job.get('owner'))).mark_removed(job['id'])
                    shutil.rmtree(trash)
                    print(f"Cleanup removed expired job {job['id']}", flush=True)
                except (ValueError, OSError) as error:
                    print(f'Cleanup deferred: {error}', flush=True)
        if self.analysis_retention_days:
            for path in list(self.root.glob('*/analysis.sqlite')):
                if re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                    try:
                        if now - path.stat().st_mtime >= self.analysis_retention_days * 86400:
                            self.clear_analysis(path.parent.name, 'Analysis data was cleared to save space after '
                                                f'{self.analysis_retention_days} days; it rebuilds when you open the mismatch drawer.')
                    except (ValueError, OSError) as error:
                        print(f'Analysis cleanup deferred: {error}', flush=True)
        # Retry deletions interrupted by a restart or temporary file lock.
        for trash in self.root.glob('.deleting-*'):
            if re.fullmatch(r'.deleting-[a-f0-9]{32}', trash.name) and not trash.is_symlink():
                try:
                    shutil.rmtree(trash)
                except OSError as error:
                    print(f'Cleanup deferred: {error}', flush=True)

    def upload_path(self, job, side):
        return self.directory(job['id']) / (f'{side}.xlsx' if job['files'][side].get('format') == 'excel' else f'{side}.parquet' if job['files'][side].get('format')=='parquet' else f'{side}.csv')

    def upload_journal(self, identity, side):
        return self.directory(identity) / f'.upload-{side}.pending'

    def cleanup_upload_journal_temps(self, identity):
        # Only startup calls this: an incomplete publication never authorized an append.
        for temporary in self.directory(identity).glob('.upload-*.pending.*.tmp'):
            if (re.fullmatch(r'\.upload-(left|right)\.pending\.[a-f0-9]{32}\.tmp', temporary.name)
                    and temporary.is_file() and not temporary.is_symlink()):
                try:
                    temporary.unlink()
                except OSError:
                    pass  # An unused checkpoint temporary must not block server startup.

    def publish_upload_checkpoint(self, identity, side, offset):
        marker = self.upload_journal(identity, side)
        temporary = marker.with_name(f'{marker.name}.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('x') as checkpoint:
                checkpoint.write(json.dumps({'offset': offset}))
                checkpoint.flush()
                os.fsync(checkpoint.fileno())
            # Publish a complete journal before any source append. A process stop while
            # writing the temporary cannot expose partial JSON as a recovery checkpoint.
            replace_retry(temporary, marker)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def recover_upload(self, job, side):
        """Roll back an interrupted chunk; completed chunks need no metadata rewrite."""
        marker = self.upload_journal(job['id'], side)
        if not marker.exists():
            return
        checkpoint = json.loads(marker.read_text())
        offset = checkpoint.get('offset')
        if type(offset) is not int or not 0 <= offset <= job['files'][side]['size']:
            raise ValueError('Invalid upload checkpoint; the source file was preserved')
        target = self.upload_path(job, side)
        if target.exists():
            if target.stat().st_size < offset:
                raise ValueError('Upload is shorter than its checkpoint; the source file was preserved')
            with target.open('r+b') as stream:
                stream.truncate(offset)
                stream.flush()
                os.fsync(stream.fileno())
        elif offset:
            raise ValueError('The upload checkpoint source is missing')
        marker.unlink()
        self.upload_offsets.pop((job['id'], side), None)

    def receive_upload(self, identity, side, offset, wire_size, original_size, encoding, reader):
        """Commit one bounded raw or gzip chunk, reporting only original file offsets."""
        with self.lock:
            file_lock = self.file_locks.setdefault((identity, side), threading.Lock())
        with file_lock:
            with self.lock:
                job = self.load(identity)
                if job['state'] != 'uploading' or job['files'][side]['complete']:
                    raise ValueError('This file is already finalized')
                if side in job.get('source_paths', {}):
                    raise ValueError('This file is read from a server path, so it is not uploaded')
                self.recover_upload(job, side)
                target = self.upload_path(job, side)
                current = target.stat().st_size if target.exists() else 0
                if offset != current:
                    raise UploadOffsetConflict(current)
                if (not 0 < wire_size <= CHUNK or not 0 < original_size <= CHUNK
                        or current + original_size > job['files'][side]['size']):
                    raise ValueError(f'Invalid upload chunk. Expected offset {current}.')
                if encoding not in ('identity', 'gzip') or encoding == 'identity' and wire_size != original_size:
                    raise ValueError('Unsupported upload encoding or chunk size')
                if shutil.disk_usage(target.parent).free < original_size + 128 * 1024 * 1024:
                    raise ValueError('Server disk space is low. Free space before resuming this upload.')
                marker = self.upload_journal(identity, side)
                self.publish_upload_checkpoint(identity, side, current)
                self.upload_offsets[(identity, side)] = current
            try:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
                remaining, written = wire_size, 0
                with target.open('ab') as stream:
                    while remaining:
                        data = reader.read(min(1024 * 1024, remaining))
                        if not data:
                            raise ValueError('Upload disconnected; retry this chunk')
                        remaining -= len(data)
                        while data:
                            if decoder:
                                try:
                                    block = decoder.decompress(data, min(1024 * 1024, original_size - written + 1))
                                except zlib.error as error:
                                    raise ValueError('Invalid compressed upload chunk') from error
                                if decoder.unused_data:
                                    raise ValueError('Compressed upload chunk has trailing data')
                                data = decoder.unconsumed_tail
                            else:
                                block, data = data, b''
                            written += len(block)
                            if written > original_size:
                                raise ValueError('Upload chunk exceeds its declared original size')
                            stream.write(block)
                    if written != original_size or decoder is not None and not decoder.eof:
                        raise ValueError('Incomplete upload chunk; retry this chunk')
                    stream.flush()
                    os.fsync(stream.fileno())
                with self.lock:
                    marker.unlink()
                    self.upload_offsets.pop((identity, side), None)
                return current + written
            except BaseException:
                with self.lock:
                    self.recover_upload(job, side)
                raise

    def input_path(self,job,side):
        from source_inputs import checked_path
        source=job.get('source_paths',{}).get(side)
        return checked_path(source) if source else self.upload_path(job,side)

    def comparison_path(self,job,side):
        if job.get('source_paths',{}).get(side) and job['files'][side].get('format')!='excel':return self.input_path(job,side)
        return self.directory(job['id']) / (f'{side}.parquet' if job['files'][side].get('format')=='parquet' else f'{side}.csv')

    def convert_fixed_width(self, identity, side):
        """Convert one fixed-width file to the standard CSV form. The original is kept so the layout can be changed."""
        import fixed_width
        from source_inputs import checked_path
        job = self.load(identity)
        directory = self.directory(identity)
        spec = job['fixed_width_pending'][side]
        name = job['files'][side]['name']
        def progress(count):
            check_cancel(directory/'cancel-import')
            self.patch(identity, preparation_message=f'Converting {name}: {count:,} records')
        try:
            source = job['files'][side].get('fixed_source')
            if source:
                original = Path(source)
            elif side in job.get('source_paths', {}):
                original = checked_path(job['source_paths'][side])
            else:
                original = directory / f'{side}.csv'
            fields = fixed_width.parse_layout(spec['layout'], spec['format'])
            temporary = directory / f'{side}.converting'
            records = fixed_width.convert(original, fields, temporary, spec['encoding'], spec['record_length'], spec['trim'], progress)
            check_cancel(directory/'cancel-import')
            with self.lock:
                job = self.load(identity)
                if not job['files'][side].get('fixed_source'):
                    if side in job.get('source_paths', {}):
                        job['files'][side]['fixed_source'] = str(original)
                        job['source_paths'].pop(side)
                    else:
                        kept = directory / f'{side}.fixed-source'
                        replace_retry(original, kept)
                        job['files'][side]['fixed_source'] = str(kept)
                replace_retry(temporary, directory / f'{side}.csv')
                names, changes = read_header(directory / f'{side}.csv', ',', 'utf-8', True)
                job.setdefault('dialects', {})[side] = dict(delimiter=',', encoding='utf-8', header=True, detected=[],
                                                            fixed=dict(format=spec['format'], fields=len(fields), records=records, encoding=spec['encoding'],
                                                                       record_length=spec['record_length'], trim=spec['trim'], layout=spec['layout']))
                source = dict(job.get('source_headers') or {})
                source[side] = names
                for other in ('left', 'right'):
                    source.setdefault(other, header(self.comparison_path(job, other), *side_dialect(job, other)))
                layout = dict(job.get('column_headers') or source, **{side: names})
                aligned, layout_changes = align_headers(source, layout)
                common = common_headers(aligned)
                draft = job.get('draft', {})
                for key in ('keys', 'ignore_columns'):
                    draft[key] = [column for column in (draft.get(key) or []) if column in common]
                for key in ('value_overrides', 'comparison_rules'):
                    draft[key] = [rule for rule in (draft.get(key) or []) if rule.get('column') in common]
                job['header_changes'] = [item for item in job.get('header_changes', []) if item.get('side') != side] + [dict(side=side, **change) for change in changes]
                job.update(state='ready', source_headers=source, column_headers=layout, columns=common, header_layout_changes=layout_changes,
                           headers_reviewed=False, draft=draft, preparation_message=f'{name}: {records:,} fixed-width records converted')
                job.pop('fixed_width_error', None)
                self.save(job)
        except Exception as error:
            (directory / f'{side}.converting').unlink(missing_ok=True)
            self.patch(identity, state='ready', fixed_width_error=dict(side=side, message=str(error)), preparation_message='')

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
                source = self.input_path(job, side)
                if job['files'][side].get('format') == 'excel':
                    columns[side], _ = convert_excel(source, job['files'][side]['sheet'], directory / f'{side}.csv', notify)
                elif job['files'][side].get('format')=='parquet':
                    columns[side]=header(source,',','utf-8')
                else:
                    # Each file keeps its own dialect, so text is never re-encoded to match a worksheet. Server
                    # files are copied byte for byte so later full reviews do not depend on the original path.
                    if side in job.get('source_paths', {}):
                        notify(f'Copying {side} file for mixed-format comparison')
                        temporary = directory / f'{side}.copying'
                        shutil.copyfile(source, temporary)
                        replace_retry(temporary, directory / f'{side}.csv')
                        job['source_paths'].pop(side)
                    columns[side] = header(self.comparison_path(job, side), *side_dialect(job, side))
            dialects = dict(job.get('dialects') or {})
            dialects.update({side: dict(delimiter=',', encoding='utf-8', header=True, detected=[]) for side in ('left', 'right') if job['files'][side].get('format') == 'excel'})
            self.patch(identity, source_paths=job.get('source_paths', {}), state='ready', source_headers=columns, column_headers=columns, headers_reviewed=False, columns=common_headers(align_headers(columns)[0]), dialects=dialects, preparation_message='Selected sheets are ready')
        except Exception as error:
            self.patch(identity, state='error', error=str(error))

    def run_review(self,identity):
        from review_revision import revise
        directory=self.directory(identity)
        self.patch(identity,state='running',started=time.time())
        job=self.load(identity)
        def notify(message):
            check_cancel(directory/'cancel-comparison')
            atomic_write_text(directory/'progress.json',json.dumps(dict(stage='review',phase=message)))
            with (directory/'run.log').open('a') as log:log.write(f'{time.strftime("%H:%M:%S")}  {message}\n')
        try:
            notify('Applying rules to retained evidence; no source reads or sorting')
            summary=revise(directory/'baseline',directory/'report',job,job['columns'],notify)
            (directory/'report/summary.html').write_text(make_summary_html(summary),encoding='utf-8')
            self.write_html_report(identity)
            check_cancel(directory/'cancel-comparison')
            self.patch(identity,state='complete',summary=summary,finished=time.time())
        except Exception as error:self.patch(identity,state='error',error=str(error),finished=time.time())

    def run_comparison(self, identity):
        if self.load(identity).get('execution_mode')=='review':return self.run_review(identity)
        job = self.load(identity)
        weight = max(1, sum(int(item.get('size') or 0) for item in job['files'].values()))
        with self.resource_ready:
            allocation = None
            while allocation is None:
                check_cancel(self.directory(identity)/'cancel-comparison')
                allocation = self.allocate(identity, weight)
                if allocation is None:
                    self.resource_ready.wait(timeout=0.25)
        try:
            self._run_comparison(identity, allocation)
        finally:
            self.release(identity)

    def _run_comparison(self, identity, allocation):
        self.patch(identity, state='running', started=time.time(), memory_cap_mb=getattr(self, 'memory_cap_mb', 0), memory_concurrency=1, allocation=allocation)
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
            summary['sources'] = [dict(side=side, file=job['files'][side]['name'], sheet=job['files'][side].get('sheet'), format=job['files'][side].get('format','csv')) for side in ('left', 'right')]
            summary['source_types']={side:dict(zip(job.get('source_headers',{}).get(side,[]),job['files'][side].get('parquet',{}).get('types',[]))) if job['files'][side].get('format')=='parquet' else {'values':'stored Excel values converted to text' if job['files'][side].get('format')=='excel' else 'text'} for side in ('left','right')}
            atomic_write_text(directory / 'report/summary.json',json.dumps(summary, indent=2))
            (directory / 'report/summary.html').write_text(make_summary_html(summary), encoding='utf-8')
            inherited_notes=directory/'inherited-annotations.json'
            if inherited_notes.exists():shutil.copy2(inherited_notes,directory/'report/annotations.json')
            # The HTML report is part of Write results, so a completed comparison opens with its report ready.
            # Excel is generated on request from the Analysis page with the options the user chooses.
            self.write_html_report(identity)
            check_cancel(directory/'cancel-comparison')
            with (directory / 'run.log').open('a') as log:
                log.write(f'{time.strftime("%H:%M:%S")}  Comparison complete | changed cells={summary["changed_cells"]:,}\n')
            self.patch(identity, state='complete', summary=summary, finished=time.time())
            shutil.rmtree(directory/'checkpoints',ignore_errors=True)
        except Exception as error:
            self.patch(identity, state='error', error=str(error), finished=time.time())

    def run_analysis(self, identity):
        directory=self.directory(identity)
        temporary=directory/'analysis.part.sqlite'
        total=self.load(identity).get('summary',{}).get('changed_cells')
        last=[0.0,None]
        def notify(message,done=None,cells=None):
            check_cancel(directory/'cancel-analysis')
            # Progress is saved at most twice a second; phase changes are saved at once.
            if done is not None and message.startswith('Indexing mismatches') and time.monotonic()-last[0]<0.5 and last[1]=='indexing':return
            last[:]=[time.monotonic(),'indexing' if message.startswith('Indexing mismatches') else message]
            self.patch(identity, analysis=dict(state='running',message=message,done=done,total=cells))
        try:
            temporary.unlink(missing_ok=True)
            notify('Preparing a disk-backed index for key and column analysis…',0,total)
            build_index(directory/'report',temporary,lambda message:notify(message),lambda:(directory/'cancel-analysis').exists(),total,progress=notify)
            replace_retry(temporary,directory/'analysis.sqlite')
            self.patch(identity,analysis=dict(state='complete',version=INDEX_VERSION,message='Analysis ready',updated=time.time()))
        except Exception as error:
            temporary.unlink(missing_ok=True)
            self.patch(identity,analysis=dict(state='error',message=str(error),updated=time.time()))

    def supporting_values(self,identity,sample_keys,columns,notify):
        from compare import input_rows,encode
        from types import SimpleNamespace
        job=self.load(identity)
        wanted={encode(json.loads(k)):k for k in sample_keys}
        fields=job['keys']+columns;width=len(job['keys'])
        args=SimpleNamespace(dialects={side:dict(zip(('delimiter','encoding','header'),side_dialect(job,side))) for side in ('left','right')},memory_mb=64,read_batch_size=10000)
        for side in ('left','right'):
            names=job['column_headers'][side];indices=[names.index(c) for c in fields]
            path=self.comparison_path(job,side);found={}
            with input_rows(path,args,indices=indices,side=side) as (_,rows):
                for i,row in enumerate(rows,1):
                    if i%10000==0:notify(f'Reading {side} supporting fields: {i:,} rows')
                    values=row if path.suffix=='.parquet' else [row[index] for index in indices]
                    key=encode(values[:width])
                    if key in wanted and (key not in found or job.get('duplicate_policy')=='last'):
                        found[key]=[(v[:300] if v is not None else '',int(v is None),len(v) if v is not None else 0) for v in values[width:]]
                    if len(found)==len(wanted) and job.get('duplicate_policy','first')=='first':break
            for key,values in found.items():
                for column,(value,null,length) in zip(columns,values):yield [side,wanted[key],column,value,null,length]

    def write_html_report(self, identity):
        """Build the HTML report inside the Write results stage. A report that fails is shown on its export
        and can be generated again; only cancelling stops the comparison."""
        directory = self.directory(identity)
        check_cancel(directory/'cancel-comparison')
        atomic_write_text(directory/'progress.json', json.dumps(dict(stage='reports', phase='Writing HTML report')))
        with (directory/'run.log').open('a') as log:
            log.write(f'{time.strftime("%H:%M:%S")}  Writing HTML report\n')
        self.run_export(identity, 'html', pipeline=True)

    def refresh_export(self, identity, kind):
        """Rebuild a report after comments change, once they have settled, so a burst of comments rebuilds once."""
        flag = self.directory(identity) / ('cancel-' + kind)
        while time.time() - self.notes_at.get(identity, 0) < NOTES_SETTLE_SECONDS:
            check_cancel(flag)
            time.sleep(0.5)
        self.run_export(identity, kind)

    def run_export(self, identity, kind, pipeline=False):
        # The HTML report and Excel workbook include comments: comments saved while one is being built start
        # another build straight after, so the finished report always has them.
        started = time.time()
        self._run_export(identity, kind, pipeline)
        if pipeline or kind not in AUTO_REFRESH or self.notes_at.get(identity, 0) <= started:
            return
        if self.load(identity)['exports'].get(kind, {}).get('state') == 'queued':
            self.refresh_export(identity, kind)

    def _run_export(self, identity, kind, pipeline=False):
        def status(state, **values):
            with self.lock:
                job = self.load(identity)
                # Notes saved while this report was being built are not in it.
                previous = job['exports'].get(kind, {})
                if previous.get('state') == 'running' and previous.get('notes_changed'):
                    if state == 'complete':
                        state, values = ('queued', dict(message='Updating with the new comments…')) if kind in AUTO_REFRESH and not pipeline \
                            else ('outdated', dict(values, message='Analysis notes changed. Generate a fresh report.'))
                    elif state == 'running':
                        values = dict(values, notes_changed=True)
                job['exports'][kind] = dict(state=state, updated=time.time(), **values)
                self.save(job)
        directory = self.directory(identity)
        target = directory / ('ai-analysis.zip' if kind=='ai' else 'mismatches.xlsx' if kind == 'excel' else 'comparison-report.html')
        temporary = target.with_suffix(target.suffix + '.part')
        last_update = [0.0]
        def notify(message):
            check_cancel(directory/('cancel-'+kind))
            if pipeline:check_cancel(directory/'cancel-comparison')
            if time.monotonic() - last_update[0] > 1:
                status('running', message=message)
                if pipeline:
                    atomic_write_text(directory/'progress.json',json.dumps(dict(stage='reports',phase=f'Writing {kind.upper()} report: {message}')))
                    with (directory/'run.log').open('a') as log:log.write(f'{time.strftime("%H:%M:%S")}  {kind.upper()} report | {message}\n')
                last_update[0] = time.monotonic()
        status('running', message='Preparing report')
        details=None
        try:
            notify('Preparing report')
            if kind=='ai':
                from ai_package import export_package
                options=self.load(identity).get('ai_options',{})
                export_package(directory/'report',temporary,notify,supporting_reader=lambda keys,columns:self.supporting_values(identity,keys,columns,notify),**options)
            elif kind=='excel':
                options=self.load(identity).get('excel_options',{})
                details=export_excel(directory/'report',temporary,notify,max_rows=options.get('max_rows'),truncate=options.get('truncate',True))
            else:export_html(directory/'report',temporary,notify)
            if pipeline:check_cancel(directory/'cancel-comparison')
            replace_retry(temporary, target)
            if pipeline:
                with (directory/'run.log').open('a') as log:log.write(f'{time.strftime("%H:%M:%S")}  {kind.upper()} report complete | {target.stat().st_size:,} bytes\n')
            status('complete', size=target.stat().st_size, report_version=REPORT_VERSION, **(details if kind=='excel' and details else {}))
        except Exception as error:
            temporary.unlink(missing_ok=True)
            status('error', error=str(error))
            # A report that cannot be built never fails a finished comparison: its export shows the error and
            # can be retried from Export. Cancelling still stops the run.
            if pipeline and isinstance(error, Cancelled):raise
            if pipeline:
                with (directory/'run.log').open('a') as log:log.write(f'{time.strftime("%H:%M:%S")}  WARNING: {kind.upper()} report not created | {error}\n')


class Handler(BaseHTTPRequestHandler):
    server_version = 'CSVCompare/1.0'

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        # Do not log download authentication tokens.
        pass

    def json_response(self, value, status=200):
        # The owner is the private workspace cookie, never shared report metadata.
        if isinstance(value,dict) and 'files' in value and 'owner' in value:
            value={k:v for k,v in value.items() if k!='owner'}
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

    def profiles_path(self):
        return self.container_path().with_name('comparison-profiles.json')

    def profile_from_job(self, job_id, name):
        """A template (saved comparison profile) from a ready or finished comparison's settings."""
        item=self.app.load(job_id)
        if self.server.shared and item.get('owner')!=self.workspace:raise ValueError('Comparison not found')
        if item['state'] not in ('ready','complete'):raise ValueError('Configure the comparison before saving a profile')
        name=name.strip() if isinstance(name,str) else ''
        if not name or len(name)>100:raise ValueError('Enter a profile name of 1–100 characters')
        profile=dict(id=uuid.uuid4().hex,name=name,source_headers=item['source_headers'],column_headers=item['column_headers'],config=item.get('draft',{}) if item['state']=='ready' else {key:item.get(key) for key in ('keys','ignore_columns','ignore_keys','ignore_container_ids','value_overrides','comparison_rules','memory_mb','comparison_engine','sort_workers','read_batch_size','compare_batch_size','duplicate_policy')})
        profile['config']={k:v for k,v in profile['config'].items() if k not in PERFORMANCE}
        settings_path=self.container_path().with_name('settings.json')
        defaults=json.loads(settings_path.read_text()) if settings_path.exists() else {}
        cfg=profile['config']
        profile['config']['comparison_rules']=resolved_rules(defaults,cfg,item['columns'],cfg.get('keys') or [],cfg.get('ignore_columns') or [])
        return profile

    def project_import_folder(self):
        folder=self.app.root/'project-imports'/re.sub(r'[^A-Za-z0-9_-]','',str(self.workspace or 'local'))[:64]
        folder.mkdir(parents=True,exist_ok=True)
        # Uploads nobody finished importing are dropped after a day.
        for old in folder.glob('*.zip'):
            if time.time()-old.stat().st_mtime>86400:old.unlink(missing_ok=True)
        return folder

    def project_routes(self, store, path, query):
        """Export, import and templates for projects. Returns True when it answered the request."""
        import project_bundle
        with self.app.lock:
            profiles=json.loads(self.profiles_path().read_text()) if self.profiles_path().exists() else []
        def save_profiles(items):
            encoded=json.dumps(items)
            if len(encoded.encode('utf-8'))>5*1024**2:raise ValueError('Template library exceeds 5 MiB; delete an unused template first')
            atomic_write_text(self.profiles_path(),encoded)
        match=re.fullmatch(r'/api/projects/([a-f0-9]{32})/(export|template)',path)
        if match and match.group(2)=='export' and self.command=='GET':
            identity=match.group(1);project=store.detail(identity)['project']
            template=next((p for p in profiles if p['id']==project.get('profile_id')),None)
            folder=self.project_import_folder();target=folder/f'export-{uuid.uuid4().hex}.part'
            try:
                project_bundle.export(self.app,store,identity,template,target)
                name=re.sub(r'[^A-Za-z0-9._-]+','-',project['name']).strip('-')[:60] or 'project'
                self.send_response(200)
                self.send_header('Content-Type','application/zip')
                self.send_header('Content-Length',str(target.stat().st_size))
                self.send_header('Content-Disposition',f'attachment; filename="{name}-project.zip"')
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                with target.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,1024*1024)
            finally:target.unlink(missing_ok=True)
            return True
        if match and match.group(2)=='template' and self.command in ('POST','DELETE'):
            identity=match.group(1)
            if self.command=='DELETE':
                store.set_profile(identity,None);self.json_response(dict(saved=True));return True
            config=self.body()
            if config.get('profile_id'):
                if not any(p['id']==config['profile_id'] for p in profiles):raise ValueError('That template no longer exists')
                store.set_profile(identity,config['profile_id']);self.json_response(dict(saved=True));return True
            # Save the latest round's settings as a new template and use it for this project's next rounds.
            rounds=store.runs(identity)
            if not rounds:raise ValueError('Run a round first, then save its settings as a template')
            profile=self.profile_from_job(rounds[-1]['id'],config.get('name',''))
            with self.app.lock:
                profiles=json.loads(self.profiles_path().read_text()) if self.profiles_path().exists() else []
                if any(p['name'].casefold()==profile['name'].casefold() for p in profiles):raise ValueError(f"A template named “{profile['name']}” already exists. Choose another name.")
                if len(profiles)>=100:raise ValueError('Delete an unused template first (maximum 100)')
                save_profiles(profiles+[profile])
            store.set_profile(identity,profile['id'])
            self.json_response(dict(saved=True,profile=dict(id=profile['id'],name=profile['name'])));return True
        if path=='/api/projects/import' and self.command=='POST':
            # Step 1: receive the .zip and show what's in it, before anything is created.
            size=int(self.headers.get('Content-Length',0))
            if size<=0:raise ValueError('Choose the .zip made by Export project')
            if size>50*1024**3:raise ValueError('The export is larger than 50 GB')
            upload=uuid.uuid4().hex;target=self.project_import_folder()/f'{upload}.zip'
            self.connection.settimeout(120)
            try:
                with target.open('wb') as out:
                    left=size
                    while left:
                        chunk=self.rfile.read(min(left,1024*1024))
                        if not chunk:raise ValueError('The upload stopped before the whole file arrived. Try again.')
                        out.write(chunk);left-=len(chunk)
                manifest=project_bundle.read(target)
            except Exception:
                target.unlink(missing_ok=True);raise
            self.json_response(dict(project_bundle.preview(manifest,store),upload_id=upload));return True
        match=re.fullmatch(r'/api/projects/import/([a-f0-9]{32})',path)
        if match and self.command in ('POST','DELETE'):
            target=self.project_import_folder()/f'{match.group(1)}.zip'
            if self.command=='DELETE':
                target.unlink(missing_ok=True);self.json_response(dict(discarded=True));return True
            if not target.exists():raise ValueError('That upload has expired. Choose the file again.')
            config=self.body();manifest=project_bundle.read(target)
            name=config.get('name',manifest['project']['name'])
            name=name.strip() if isinstance(name,str) else ''
            if not 1<=len(name)<=100:raise ValueError('Use a project name of 1–100 characters')
            if store.name_taken(name):
                self.json_response(dict(error=f'A project named “{name}” already exists. Choose another name to import this one.',exists=True),409);return True
            with self.app.lock:
                profiles=json.loads(self.profiles_path().read_text()) if self.profiles_path().exists() else []
                count=len(profiles);profile_id=project_bundle.restore_template(manifest.get('template'),profiles)
                if len(profiles)!=count:save_profiles(profiles)
            identity=None
            try:
                identity,signoffs,ids=project_bundle.install(self.app,store,target,manifest,name,self.workspace if self.server.shared else None,profile_id)
                # Build every round's report and the sign-off report here, from the imported data.
                for job_id in ids.values():self.app._run_export(job_id,'html')
                if signoffs:
                    # The latest sign-off is the one whose report can be downloaded.
                    latest=store.signoffs(identity)[-1]
                    store.set_signoff_report(latest['id'],self.app.signoff_report(store,identity,latest))
            except Exception:
                if identity:
                    for job_id in store.remove(identity):shutil.rmtree(self.app.directory(job_id),ignore_errors=True)
                raise
            target.unlink(missing_ok=True)
            self.json_response(dict(id=identity),201);return True
        return False

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
            if self.command == 'GET' and path in STATIC_FILES:
                target = BASE / 'web' / ('index.html' if path == '/' else path[1:])
                data = target.read_bytes()
                if path in FONT_FILES:
                    content_type = 'font/woff2'
                else:
                    data = data.replace(b'__APP_TOKEN__', self.app.token.encode()).replace(b'__WORKSPACE_MODE__', b'team' if self.server.shared else b'local').replace(b'__MAX_SORT_MB__', str(self.server.max_sort_mb).encode())
                    content_type = mimetypes.guess_type(target)[0] + '; charset=utf-8'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
            if path=='/api/source-options' and self.command=='GET':
                return self.json_response(dict(enabled=bool(self.server.source_roots),roots=[str(p) for p in self.server.source_roots]))
            if path=='/api/server-sources/check' and self.command=='POST':
                from source_inputs import inspect_source
                # Checks whichever paths are given, so each file can be checked as soon as its path is typed.
                config=self.body();given={side:value for side,value in (config.get('files') or {}).items() if side in ('left','right')}
                if not given:raise ValueError('Enter a server path to check')
                sources={side:inspect_source(value,self.server.source_roots) for side,value in given.items()}
                suffixes=[Path(v['path']).suffix.lower() for v in sources.values()]
                if len(suffixes)==2 and '.json' in suffixes and suffixes!=['.json','.json']:raise ValueError('Choose JSON files on both sides')
                return self.json_response(dict(valid=True,files={side:dict(name=Path(value['path']).name,size=value['size']) for side,value in sources.items()}))
            if path=='/api/server-sources' and self.command=='POST':
                from source_inputs import inspect_source
                config=self.body();sources={side:inspect_source(config.get('files',{}).get(side),self.server.source_roots) for side in ('left','right')}
                suffixes=[Path(v['path']).suffix.lower() for v in sources.values()]
                if '.json' in suffixes:
                    if suffixes!=['.json','.json']:raise ValueError('Choose JSON files on both sides')
                    if any(v['size']>5*1024**2 for v in sources.values()):raise ValueError('JSON inputs must be at most 5 MiB each')
                    return self.json_response(dict(kind='json',documents={side:Path(v['path']).read_text(encoding='utf-8-sig') for side,v in sources.items()},names={side:Path(v['path']).name for side,v in sources.items()}))
                config['files']={side:dict(name=Path(v['path']).name,size=v['size']) for side,v in sources.items()}
                item=self.app.create(config,self.workspace,sources)
                for f in item['files'].values():f['uploaded']=f['size']
                self.app.save(item)
                return self.json_response(item,201)
            if path.startswith('/api/projects'):
                store=ProjectStore(project_database(self.app.root,self.workspace),self.workspace if self.server.shared else None)
                if path=='/api/projects' and self.command=='GET':
                    # Index old jobs lazily once; only small job metadata is read.
                    marker=store.path.with_suffix('.indexed')
                    with self.app.lock:
                        if not marker.exists():
                            for entry in self.app.root.glob('*/job.json'):
                                try:
                                    item=self.app.load(entry.parent.name)
                                    store.sync(job_snapshot(item))
                                except (ValueError,OSError):continue
                            marker.touch()
                    return self.json_response(dict(projects=store.overview()))
                if path=='/api/projects' and self.command=='POST':
                    config=self.body()
                    if config.get('profile_id'):
                        profiles=json.loads(self.profiles_path().read_text()) if self.profiles_path().exists() else []
                        if not any(p['id']==config['profile_id'] for p in profiles):raise ValueError('That template no longer exists')
                    config.pop('imported',None);config.pop('created',None)
                    return self.json_response(dict(id=store.create(config)),201)
                if self.project_routes(store,path,query):return
                if path=='/api/projects/records':
                    if self.command=='GET':return self.json_response(store.records(offset=max(0,int(query.get('offset',['0'])[0])),identity=query.get('id',[None])[0]))
                    if self.command=='POST':
                        config=self.body();record_id=config.get('comparison_id','')
                        if re.fullmatch(r'[a-f0-9]{32}',str(record_id)) and (self.app.directory(record_id)/'job.json').exists():
                            item=self.app.load(record_id)
                            if self.server.shared and item.get('owner')!=self.workspace:raise ValueError('Create your own review to change this comparison’s project or audit status')
                        store.update_record(config);return self.json_response(dict(saved=True))
                match_compare=re.fullmatch(r'/api/projects/([a-f0-9]{32})/compare',path)
                if match_compare and self.command=='GET':return self.json_response(self.app.compare_runs(store,match_compare.group(1),query))
                match_signoff=re.fullmatch(r'/api/projects/([a-f0-9]{32})/signoff(/report)?',path)
                if match_signoff and self.command=='POST' and not match_signoff.group(2):
                    return self.json_response(self.app.sign_off_project(store,match_signoff.group(1),self.body()))
                if match_signoff and self.command=='GET' and match_signoff.group(2):
                    signoff=store.latest_signoff(match_signoff.group(1))
                    target=Path(signoff['report']) if signoff else None
                    if not target or not target.exists() or self.app.root.resolve() not in target.resolve().parents:raise ValueError('This project has no sign-off report yet')
                    name=re.sub(r'[^A-Za-z0-9._-]+','-',store.detail(match_signoff.group(1))['project']['name']).strip('-')[:60]+'-signoff-report.html'
                    self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(target.stat().st_size))
                    self.send_header('Content-Disposition',f'attachment; filename="{name}"')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    with target.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,1024*1024)
                    return
                match_project=re.fullmatch(r'/api/projects/([a-f0-9]{32})',path)
                if match_project and self.command=='GET':
                    detail=store.detail(match_project.group(1),max(0,int(query.get('offset',['0'])[0])))
                    profiles=json.loads(self.profiles_path().read_text()) if self.profiles_path().exists() else []
                    template=next((p for p in profiles if p['id']==detail['project'].get('profile_id')),None)
                    detail['template']=template and dict(id=template['id'],name=template['name'])
                    return self.json_response(detail)
                return self.json_response({'error':'Not found'},404)
            if path in ('/api/json-compare', '/api/json-arrays') and self.command == 'POST':
                config = self.body(64 * 1024 * 1024)
                if not self.app.json_lock.acquire(blocking=False):
                    return self.json_response({'error': 'Another JSON comparison is running. Try again shortly.'}, 429)
                try:
                    result = discover_arrays(config.get('left'), config.get('right')) if path == '/api/json-arrays' else compare_json(config.get('left'), config.get('right'), config.get('default_order', 'ordered'), config.get('rules', []), True)
                    if path=='/api/json-compare':
                        identity=uuid.uuid4().hex
                        names=config.get('source_names',[])
                        title=' ↔ '.join(str(n)[:255] for n in names[:2]) if isinstance(names,list) and len(names)==2 else 'JSON comparison'
                        snapshot=dict(id=identity,owner=self.workspace,kind='json',created=time.time(),finished=time.time(),state='complete',title=title,formats=['json','json'],changed_cells=sum(result['counts'].values()),json_counts=result['counts'])
                        ProjectStore(project_database(self.app.root,self.workspace)).sync(snapshot)
                        result['audit_id']=identity
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
                    sizes=job_sizes(file.parent)
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
            if path == '/api/settings' and self.command in ('GET','POST'):
                target=self.container_path().with_name('settings.json')
                with self.app.lock:
                    if self.command=='POST':
                        settings=validate_settings(self.body(),self.server.max_sort_mb)
                        atomic_write_text(target,json.dumps(settings))
                    settings=json.loads(target.read_text()) if target.exists() else validate_settings({},self.server.max_sort_mb)
                    settings.setdefault('comparison_engine','auto')
                return self.json_response(settings)
            if path == '/api/profiles' and self.command in ('GET','POST','DELETE'):
                target=self.container_path().with_name('comparison-profiles.json')
                with self.app.lock:
                    profiles=json.loads(target.read_text()) if target.exists() else []
                    if self.command=='POST':
                        config=self.body();profile=self.profile_from_job(config.get('job_id',''),config.get('name',''))
                        profiles=[p for p in profiles if p['name'].casefold()!=profile['name'].casefold()]
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
                # Each file is either uploaded ({name, size}) or read from an allowed server path ({path}).
                config=self.body();sources={}
                for side,item in (config.get('files') or {}).items():
                    if side in ('left','right') and isinstance(item,dict) and 'path' in item:
                        from source_inputs import inspect_source
                        sources[side]=inspect_source(item['path'],self.server.source_roots)
                        if Path(sources[side]['path']).suffix.lower()=='.json':raise ValueError('JSON files are compared from the same source on both sides. Choose two server paths or upload both files.')
                        config['files'][side]=dict(name=Path(sources[side]['path']).name,size=sources[side]['size'])
                item=self.app.create(config,self.workspace,sources or None)
                if sources:
                    for side in sources:item['files'][side]['uploaded']=item['files'][side]['size']
                    self.app.save(item)
                return self.json_response(item,201)
            if self.command == 'GET' and path == '/api/jobs':
                offset = max(0, int(query.get('offset', ['0'])[0]))
                records = []
                for entry in self.app.root.glob('*/job.json'):
                    try:
                        item = self.app.load(entry.parent.name)
                    except (ValueError, OSError):
                        continue
                    if self.server.shared and item.get('owner')!=self.workspace and item['state']!='complete':continue
                    summary = item.get('summary', {})
                    records.append(dict(id=item['id'], created=item['created'], state=item['state'],
                        files=item['files'], busy=self.app.busy(item), can_manage=not self.server.shared or item.get('owner')==self.workspace, keys=item.get('keys', []), changed_cells=summary.get('changed_cells'),
                        changed_rows=summary.get('changed_rows'), elapsed_seconds=summary.get('elapsed_seconds'), sizes=job_sizes(entry.parent)))
                records.sort(key=lambda item: item['created'], reverse=True)
                return self.json_response(dict(jobs=records[offset:offset+25], total=len(records), offset=offset, retention_days=self.app.retention_days,
                                               analysis_retention_days=self.app.analysis_retention_days))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(.*))?', path)
            if not match:
                return self.json_response({'error': 'Not found'}, 404)
            identity, action = match.groups()
            job = self.app.load(identity)
            if self.server.shared and job.get('owner') != self.workspace:
                if job['state']!='complete':return self.json_response({'error':'Comparison is private while it is being prepared or processed'},404)
                shared_actions={'review-plan','review-copy','validate-rules','annotations','analysis','export/excel','export/html','export/ai','ai-findings'}
                if self.command not in ('GET','HEAD') and action not in shared_actions:
                    return self.json_response({'error':'Only the creating browser can edit sources, cancel or delete this job. Use Recompare to create your own review.'},403)
            job['can_manage']=not self.server.shared or job.get('owner')==self.workspace
            self.authorized_job = identity
            directory = self.app.directory(identity)
            if action in ('review-plan','review-copy') and self.command == 'POST':
                config=self.body()
                with self.app.lock:
                    job=self.app.load(identity)
                    if job['state']!='complete':raise ValueError('Complete a comparison before reviewing overrides')
                    draft={k:config.get(k,job.get(k)) for k in ('keys','ignore_columns','ignore_keys','ignore_container_ids','comparison_rules','duplicate_policy')}
                    keys=draft['keys']
                    if not isinstance(keys,list) or not keys or not all(isinstance(k,str) and k in job['columns'] for k in keys) or len(set(keys))!=len(keys):raise ValueError('Select at least one valid, unique key column')
                    ignored,_=validate_scope(keys,job['columns'],draft.get('ignore_columns') or [],draft.get('ignore_keys') or '')
                    draft['comparison_rules']=validate_rules(draft.get('comparison_rules') or [],job['columns'],keys,ignored)
                    rules,_=validate_overrides(config.get('value_overrides',job.get('value_overrides',[])),job['columns'],keys,ignored)
                    draft['value_overrides']=rules
                    ids=draft.get('ignore_container_ids') or []
                    library=self.container_path()
                    available={c['id']:c for c in json.loads(library.read_text())} if library.exists() else {}
                    for c in job.get('ignore_key_containers',[]):available.setdefault(c['id'],c)
                    if not isinstance(ids,list) or any(i not in available for i in ids):raise ValueError('An ignore container no longer exists')
                    draft['ignore_key_containers']=[available[i] for i in dict.fromkeys(ids)]
                    if draft.get('duplicate_policy') not in ('first','last'):raise ValueError('Choose first or last duplicate occurrence')
                    from review_revision import plan
                    baseline=directory/'baseline' if (directory/'baseline').exists() else directory/'report'
                    decision=plan(job,baseline,draft)
                    if action=='review-plan':return self.json_response(decision)
                    if config.get('execution_mode') and config['execution_mode']!=decision['mode']:raise ValueError('Review settings changed. Check the execution plan again.')
                    new_id=uuid.uuid4().hex
                    target=self.app.directory(new_id)
                    target.mkdir()
                    try:
                        # Finalized inputs are immutable. Linking keeps review runs from copying gigabytes.
                        for side in ('left','right'):
                            if side not in job.get('source_paths',{}) or job['files'][side].get('format')=='excel':
                                source=self.app.comparison_path(job,side)
                                if source.exists():os.link(source,target/source.name)
                                elif decision['mode']=='full':raise ValueError('The original source is unavailable for a fresh comparison')
                        fields=('source_paths','files','delimiter','encoding','header','dialects','source_headers','column_headers','columns','header_changes','header_layout_changes')
                        copy={k:job[k] for k in fields if k in job}
                        draft['value_overrides']=rules
                        copy.update(owner=self.workspace,id=new_id,state='ready',created=time.time(),exports={},headers_reviewed=True,draft=draft,review_of=identity)
                        copy['execution_mode']=decision['mode']
                        # Snapshot review notes independently of execution mode and scope.
                        # The full worker recreates report artifacts, so restore this snapshot afterwards.
                        notes_source=directory/'report/annotations.json'
                        if notes_source.exists():shutil.copy2(notes_source,target/'inherited-annotations.json')
                        if decision['mode']=='review':
                            retained=target/'baseline';retained.mkdir()
                            for name in ('summary.json','differences.csv','raw_differences.csv','matched_keys.csv','left_only.csv','right_only.csv','duplicate_keys.csv','skipped_keys.csv'):
                                if (baseline/name).exists():os.link(baseline/name,retained/name)
                            if (directory/'report/annotations.json').exists():shutil.copy2(directory/'report/annotations.json',retained/'annotations.json')
                        self.app.save(copy)
                        ProjectStore(project_database(self.app.root,self.workspace)).inherit(identity,new_id)
                    except Exception:
                        shutil.rmtree(target)
                        raise
                return self.json_response(copy,201)
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
                    draft={k:v for k,v in profile['config'].items() if k not in PERFORMANCE};keys=draft.get('keys') or []
                    if not keys or any(k not in common for k in keys):raise ValueError('Profile key columns are missing from these files; update headers or choose another profile')
                    ignored,_=validate_scope(keys,common,draft.get('ignore_columns') or [],draft.get('ignore_keys') or '')
                    validate_overrides(draft.get('value_overrides') or [],common,keys,ignored)
                    validate_rules(draft.get('comparison_rules') or [],common,keys,ignored)
                    target=self.container_path();containers=json.loads(target.read_text()) if target.exists() else []
                    if any(cid not in {c['id'] for c in containers} for cid in draft.get('ignore_container_ids') or []):raise ValueError('A profile ignore container is missing')
                    job.update(column_headers=layout,columns=common,draft=draft,headers_reviewed=True,header_layout_changes=changes)
                    self.app.save(job)
                return self.json_response(job)
            if action=='ai-findings' and self.command in ('GET','POST'):
                if job['state']!='complete':raise ValueError('Complete the comparison first')
                target=directory/'report/ai-findings.json'
                if self.command=='POST':
                    data=self.body(4*1024*1024);findings=data.get('findings')
                    if not isinstance(findings,list) or len(findings)>2000:raise ValueError('Import a JSON array of up to 2,000 findings')
                    cleaned=[]
                    for f in findings:
                        if not isinstance(f,dict) or f.get('column') not in job['summary']['changed_cells_by_column']:raise ValueError('Each finding must name a compared column')
                        row={k:f.get(k,'') for k in ('column','pattern','reason','evidence','confidence','recommended_action')}
                        if any(not isinstance(v,str) or len(v)>2000 for v in row.values()) or not row['reason']:raise ValueError('Findings need a reason and text fields of at most 2,000 characters')
                        if row['confidence'] not in ('low','medium','high',''):raise ValueError('Confidence must be low, medium or high')
                        row['review_status']='AI suggestion — unreviewed';cleaned.append(row)
                    atomic_write_text(target,json.dumps(cleaned,ensure_ascii=False))
                return self.json_response(dict(findings=json.loads(target.read_text()) if target.exists() else []))
            if action == 'annotations' and self.command in ('GET','POST'):
                if job['state']!='complete':raise ValueError('Complete a comparison before adding analysis notes')
                target=directory/'report/annotations.json'
                with self.app.lock:
                    notes=json.loads(target.read_text()) if target.exists() else []
                    if self.command=='POST':
                        current=self.app.load(identity)
                        config=self.body();column=config.get('column','');key=config.get('key');comment=config.get('comment','');status=config.get('status')
                        if column and column not in job['columns']:raise ValueError('Choose a valid column')
                        if key is not None and (not isinstance(key,list) or len(key)!=len(job['keys']) or any(not isinstance(v,str) or len(v)>1000 for v in key)):raise ValueError('Invalid annotation key')
                        if not column and key is None:raise ValueError('Choose a column or an exact key for this note')
                        if status not in ('Expected','Needs investigation','Resolved') or not isinstance(comment,str) or len(comment)>2000:raise ValueError('Choose a classification and use at most 2,000 comment characters')
                        notes=[n for n in notes if (n['column'],n['key'])!=(column,key)]
                        if len(notes)>=10000:raise ValueError('Maximum 10,000 analysis notes per job')
                        notes.append(dict(column=column,key=key,status=status,comment=comment,updated=time.time()))
                        encoded=json.dumps(notes)
                        if len(encoded.encode('utf-8'))>2*1024**2:raise ValueError('Analysis notes exceed the 2 MiB limit')
                        atomic_write_text(target,encoded)
                        self.app.notes_at[identity]=time.time();refresh=[]
                        for kind,export in current.get('exports',{}).items():
                            # The HTML report and Excel workbook rebuild themselves; the AI package is made on request.
                            if kind in AUTO_REFRESH and export.get('state') in ('complete','outdated'):
                                current['exports'][kind]=dict(state='queued',updated=time.time(),message='Updating with the new comments…');refresh.append(kind)
                            elif export.get('state')=='complete':export.update(state='outdated',message='Analysis notes changed. Generate a fresh report.')
                            elif export.get('state') in ('queued','running'):export['notes_changed']=True
                        self.app.save(current)
                        for kind in refresh:
                            if (identity,kind) not in self.app.tasks:self.app.submit(identity,kind,self.app.refresh_export,kind)
                return self.json_response(dict(notes=notes))
            if self.command == 'DELETE' and action is None:
                self.app.remove_job(identity, allowed_requests=1)
                return self.json_response({'deleted': True})
            if self.command == 'GET' and action is None:
                if 'source_headers' not in job and job['state'] in ('ready', 'queued', 'running', 'complete'):
                    job['source_headers'] = {side: header(self.app.comparison_path(job,side), *side_dialect(job,side)) for side in ('left', 'right')}
                    job.setdefault('column_headers', job['source_headers'])
                progress = directory / 'progress.json'
                if progress.exists():
                    job['progress'] = json.loads(progress.read_text())
                runtime = directory / 'runtime-settings.json'
                if runtime.exists():
                    job['runtime_settings'] = json.loads(runtime.read_text())
                    job.update({key: value for key, value in job['runtime_settings'].items() if key in PERFORMANCE})
                job['free_disk_bytes'] = shutil.disk_usage(directory).free
                return self.json_response(job)
            if self.command == 'GET' and action == 'upload-status':
                return self.json_response(dict(id=identity, state=job['state'], upload_compression=['gzip'],
                    files={side: {key: job['files'][side].get(key, False if key in ('complete', 'in_flight') else 0)
                                  for key in ('uploaded', 'size', 'complete', 'in_flight')}
                           for side in ('left', 'right')}))
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
                source_path=self.app.comparison_path(job,side)
                names=job.get('column_headers',{}).get(side) or header(source_path,*side_dialect(job,side))
                if start >= len(names): raise ValueError('Invalid column page')
                from compare import input_rows
                from types import SimpleNamespace
                from parquet_input import is_parquet
                parquet=is_parquet(source_path)
                options=SimpleNamespace(dialects={side:dict(zip(('delimiter','encoding','header'),side_dialect(job,side)))},read_batch_size=limit,memory_mb=16)
                with input_rows(source_path,options,indices=list(range(start,min(start+20,len(names)))),side=side) as (_,reader):
                    for row in itertools.islice(reader,limit):
                        if not parquet and len(row)!=len(names): raise ValueError('Invalid row width in preview')
                        values=[('[NULL]' if cell is None else cell[:500]) for cell in (row if parquet else row[start:start+20])]
                        added=sum(len(cell.encode('utf-8')) for cell in values)
                        if size+added>2*1024*1024:break
                        size+=added;rows.append(values)
                return self.json_response(dict(headers=names[start:start+20], rows=rows, requested_rows=limit,
                    total_columns=len(names), column_offset=start, limited=len(rows)<limit))
            if self.command == 'POST' and action == 'fixed-width':
                import fixed_width
                config = self.body()
                side = config.get('side')
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('File settings are locked after comparison starts')
                    if side not in ('left', 'right') or job['files'][side].get('format', 'csv') != 'csv':
                        raise ValueError('Fixed-width layouts apply to text files only')
                    kind = config.get('format') if config.get('format') in ('positions', 'copybook') else 'positions'
                    fixed_width.parse_layout(config.get('layout'), kind)
                    encoding = config.get('encoding', 'utf-8')
                    length = config.get('record_length', 0) or 0
                    if encoding not in fixed_width.ENCODINGS:
                        raise ValueError('Choose UTF-8, Windows-1252, Latin-1 or EBCDIC (cp037)')
                    if not isinstance(length, int) or isinstance(length, bool) or not 0 <= length <= 1_000_000:
                        raise ValueError('Record length must be a whole number of characters, or 0 for one record per line')
                    job.setdefault('fixed_width_pending', {})[side] = dict(format=kind, layout=config['layout'], encoding=encoding,
                                                                          record_length=length, trim=bool(config.get('trim', True)))
                    job.pop('fixed_width_error', None)
                    job.update(state='preparing', preparation_message=f"Converting {job['files'][side]['name']}")
                    self.app.save(job)
                    self.app.submit(identity, 'import', self.app.convert_fixed_width, side)
                return self.json_response(job, 202)
            if self.command == 'POST' and action == 'dialect':
                config = self.body()
                side = config.get('side')
                delimiter, encoding, has_header = config.get('delimiter'), config.get('encoding'), config.get('header')
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('File settings are locked after comparison starts')
                    if side not in ('left', 'right') or job['files'][side].get('format', 'csv') != 'csv':
                        raise ValueError('Delimiter settings apply to delimited text files only')
                    if not text_dialect.valid_delimiter(delimiter) or encoding not in text_dialect.ENCODINGS or not isinstance(has_header, bool):
                        raise ValueError('Unsupported delimiter, encoding or first-row setting')
                    names, changes = read_header(self.app.comparison_path(job, side), delimiter, encoding, has_header)
                    job.setdefault('dialects', {})[side] = dict(delimiter=delimiter, encoding=encoding, header=has_header, detected=[])
                    source = dict(job.get('source_headers') or {})
                    source[side] = names
                    for other in ('left', 'right'):
                        source.setdefault(other, header(self.app.comparison_path(job, other), *side_dialect(job, other)))
                    layout = dict(job.get('column_headers') or source, **{side: names})
                    aligned, layout_changes = align_headers(source, layout)
                    common = common_headers(aligned)
                    draft = job.get('draft', {})
                    # Selections that no longer name a shared column are dropped rather than silently remapped.
                    for key in ('keys', 'ignore_columns'):
                        draft[key] = [name for name in (draft.get(key) or []) if name in common]
                    for key in ('value_overrides', 'comparison_rules'):
                        draft[key] = [rule for rule in (draft.get(key) or []) if rule.get('column') in common]
                    job['header_changes'] = [item for item in job.get('header_changes', []) if item.get('side') != side] + [dict(side=side, **change) for change in changes]
                    job.update(source_headers=source, column_headers=layout, columns=common, header_layout_changes=layout_changes, headers_reviewed=False, draft=draft)
                    self.app.save(job)
                return self.json_response(job)
            if self.command == 'POST' and action == 'headers':
                config = self.body()
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'ready':
                        raise ValueError('Column headers are locked after comparison starts')
                    source = job.get('source_headers') or {side: header(self.app.comparison_path(job,side), *side_dialect(job,side)) for side in ('left', 'right')}
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
                    job['draft'] = {key: config.get(key) for key in ('keys', 'ignore_columns', 'ignore_keys', 'memory_mb', 'comparison_engine', 'comparison_rules', 'value_overrides', 'ignore_container_ids', 'sort_workers', 'read_batch_size', 'compare_batch_size', 'duplicate_policy') if key not in ('comparison_engine', 'ignore_container_ids', 'sort_workers','comparison_rules','read_batch_size','compare_batch_size','duplicate_policy') or key in config}
                    self.app.save(job)
                return self.json_response({'saved': True})
            if self.command == 'PUT' and action in ('files/left', 'files/right'):
                side = action.split('/')[1]
                size = int(self.headers.get('Content-Length', 0))
                offset = int(query.get('offset', ['-1'])[0])
                encoding = self.headers.get('Content-Encoding', 'identity').strip().lower()
                if encoding == 'gzip' and 'X-Upload-Size' not in self.headers:
                    raise ValueError('Compressed chunks require their original upload size')
                original_size = int(self.headers.get('X-Upload-Size', size))
                self.connection.settimeout(120)
                uploaded = self.app.receive_upload(identity, side, offset, size, original_size, encoding, self.rfile)
                return self.json_response({'uploaded': uploaded})
            if self.command == 'POST' and action == 'finalize':
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'uploading':
                        raise ValueError('Upload already finalized')
                    if any((identity, side) in self.app.upload_offsets or self.app.upload_journal(identity, side).exists()
                           for side in ('left', 'right')):
                        raise ValueError('Wait for active upload chunks to finish before finalizing')
                    columns = {}
                    header_changes = []
                    for side in ('left', 'right'):
                        file = self.app.input_path(job, side)
                        if not file.exists() or file.stat().st_size != job['files'][side]['size']:
                            raise ValueError('Both files must finish uploading')
                        if job['files'][side].get('format') == 'excel':
                            job['files'][side]['sheets'] = excel_sheets(file)
                        else:
                            if job['files'][side].get('format') != 'parquet':
                                # Delimiter, encoding and header row are detected per file when left on auto.
                                job.setdefault('dialects', {})[side] = resolve_dialect(job, side, file)
                            columns[side], changes = read_header(file, *side_dialect(job, side))
                            if job['files'][side].get('format')=='parquet':
                                from parquet_input import metadata
                                job['files'][side]['parquet']=metadata(file)
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
                    # External CSV/Parquet remain read-only references. Check their
                    # selection fingerprint before queueing; review-only jobs use
                    # retained evidence, and Excel already has a stable local import.
                    if job.get('execution_mode')!='review':
                        for side in job.get('source_paths',{}):
                            if job['files'][side].get('format')!='excel':self.app.input_path(job,side)
                    source = job.get('source_headers') or {side: header(self.app.comparison_path(job,side), *side_dialect(job,side)) for side in ('left', 'right')}
                    align_headers(source, job.get('column_headers'))
                    keys = config.get('keys', [])
                    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys) or any(k not in job['columns'] for k in keys):
                        raise ValueError('Select at least one valid, unique key column')
                    settings_path=self.container_path().with_name('settings.json')
                    defaults=json.loads(settings_path.read_text()) if settings_path.exists() else {}
                    # Queue placeholders only. The worker sizes itself when it actually starts,
                    # so saved tuning or a stale client cannot override current resource limits.
                    config.update(comparison_engine='auto', read_batch_size=100000, compare_batch_size=10000)
                    ignored_columns, _ = validate_scope(keys, job['columns'], config.get('ignore_columns', []), config.get('ignore_keys', ''))
                    ids = config.get('ignore_container_ids', [])
                    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                        raise ValueError('Select valid ignore key containers')
                    library = self.container_path()
                    available = {item['id']: item for item in json.loads(library.read_text())} if library.exists() else {}
                    if job.get('review_of'):
                        # Shared reviews retain the original container snapshot even
                        # when the reviewer's private library does not contain it.
                        for item in job.get('draft',{}).get('ignore_key_containers',[]):available.setdefault(item['id'],item)
                    if any(i not in available for i in ids):
                        raise ValueError('An ignore key container no longer exists')
                    containers = [available[i] for i in dict.fromkeys(ids)]
                    for item in containers:
                        if item['key_width'] != len(keys):
                            raise ValueError(f"Container {item['name']} expects {item['key_width']} key columns")
                        validate_scope(keys, job['columns'], [], item['values'])
                    overrides, _ = validate_overrides(config.get('value_overrides', []), job['columns'], keys, ignored_columns)
                    rules=resolved_rules(defaults,config,job['columns'],keys,ignored_columns)
                    # Project rounds: an optional sample of File 1's keys, and the project the run belongs to.
                    sample=config.get('sample')
                    if sample is not None and job.get('execution_mode')=='review':
                        raise ValueError('Recompare uses the original comparison’s keys; start a new round to change the sample')
                    if sample is not None:job['sample']=self.app.round_sample(sample,keys)
                    elif job.get('execution_mode')!='review':job.pop('sample',None)
                    project=config.get('project_id')
                    project_store=ProjectStore(project_database(self.app.root,self.workspace),self.workspace if self.server.shared else None)
                    if project is not None and (not re.fullmatch(r'[a-f0-9]{32}',str(project)) or not project_store.exists(project)):raise ValueError('That project no longer exists')
                    job.update(**execution_settings(config),comparison_rules=rules,state='queued', keys=keys, memory_cap_mb=self.server.max_sort_mb, sort_workers=1,
                               ignore_columns=ignored_columns, ignore_keys=config.get('ignore_keys', ''), value_overrides=overrides,
                               ignore_container_ids=ids, ignore_key_containers=containers)
                    if job.get('execution_mode')=='review':
                        from review_revision import plan
                        decision=plan(job,directory/'baseline',job)
                        if decision['mode']!='review':raise ValueError('A full comparison is required: '+decision['reason'])
                    self.app.save(job)
                    if project:
                        project_store.update_record(dict(comparison_id=identity,project_id=project))
                    with (directory / 'run.log').open('a') as log:
                        log.write(f'{time.strftime("%H:%M:%S")}  Comparison queued\n')
                    self.app.submit(identity,'comparison',self.app.run_comparison)
                return self.json_response(job, 202)
            if self.command == 'POST' and action in ('export/excel', 'export/html','export/ai'):
                kind = action.split('/')[1]
                options=self.body() if kind in ('ai','excel') else {}
                with self.app.lock:
                    job = self.app.load(identity)
                    if job['state'] != 'complete':
                        raise ValueError('Wait for comparison to complete')
                    if kind=='ai':
                        options={k:options.get(k,v) for k,v in [('token_budget',32000),('examples',3),('supporting_columns',[]),('selected_columns',None)]}
                        chosen=options['selected_columns']
                        if chosen is not None and (not isinstance(chosen,list) or not chosen or len(chosen)>2000 or any(not isinstance(c,str) or c not in job['summary']['changed_cells_by_column'] for c in chosen) or len(set(chosen))!=len(chosen)):raise ValueError('Select at least one valid compared column')
                        if not isinstance(options['supporting_columns'],list) or len(options['supporting_columns'])>5 or len(set(options['supporting_columns']))!=len(options['supporting_columns']) or any(c not in job['columns'] or c in job['keys'] for c in options['supporting_columns']):raise ValueError('Choose up to five distinct supporting value columns')
                        if options['token_budget'] not in (8000,32000,128000) or options['examples'] not in (1,3,5):raise ValueError('Invalid AI package options')
                        if options!=job.get('ai_options'):
                            if job['exports'].get(kind,{}).get('state') in ('queued','running'):raise ValueError('Wait for the current AI package to finish')
                            job['exports'].pop(kind,None)
                        job['ai_options']=options
                    if kind=='excel':
                        # Rows per column sheet (0 = as many as Excel allows) and whether long values are cut.
                        rows=options.get('max_rows',0)
                        if not isinstance(rows,int) or isinstance(rows,bool) or not 0<=rows<=1048574:raise ValueError('Rows per column must be between 1 and 1,048,574')
                        options=dict(max_rows=rows or None,truncate=bool(options.get('truncate',True)))
                        if options!=job.get('excel_options',dict(max_rows=None,truncate=True)) and job['exports'].get(kind,{}).get('state')!='running':
                            job['exports'].pop(kind,None)
                        job['excel_options']=options
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
                    atomic_write_text(directory/'progress.json',json.dumps(dict(stage='validate',phase='Queued for source validation')))
                    self.app.save(job)
                    self.app.submit(identity,'comparison',self.app.run_comparison)
                return self.json_response(job,202)
            if action == 'analysis' and self.command == 'DELETE':
                freed = self.app.clear_analysis(identity, 'Analysis data was cleared to save space; it rebuilds when you open the mismatch drawer.')
                return self.json_response(dict(freed=freed))
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
                result=query_index(directory/'analysis.sqlite',query.get('mode',['keys'])[0],int(query.get('offset',['0'])[0]),query.get('column',[''])[0],key,query.get('left_hash',[None])[0],query.get('right_hash',[None])[0])
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
                kind = {'mismatches.xlsx': 'excel', 'comparison-report.html': 'html','ai-analysis.zip':'ai'}.get(name)
                if name == 'run.log' and (directory / name).exists():
                    target = directory / name
                elif kind and job['exports'].get(kind, {}).get('state') == 'complete':
                    target = directory / name
                elif name in ('skipped_keys.csv', 'duplicate_keys.csv', 'differences.csv', 'left_only.csv', 'right_only.csv', 'summary.html', 'summary.json'):
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
        except UploadOffsetConflict as error:
            self.json_response({'error': str(error), 'uploaded': error.offset}, 409)
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


def make_server(root, port=8765, host='127.0.0.1', public_url=None, max_jobs=1, max_sort_mb=0):
    if not 1 <= max_jobs <= 8:
        raise ValueError('Concurrent comparisons must be between 1 and 8')
    if type(max_sort_mb) is not int or max_sort_mb < 0:
        raise ValueError('Invalid server comparison memory limit')
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Public URL must be an http(s) origin, without a path or credentials')
        public_url = public_url.rstrip('/')
    if host not in ('127.0.0.1', 'localhost') and not public_url:
        raise ValueError('Network binding requires --public-url, for example http://server-name:8765')
    server = ThreadingHTTPServer((host, port), Handler)
    server.shared = public_url is not None
    server.source_roots=[]
    server.public_url = public_url
    server.max_sort_mb = max_sort_mb
    server.app = Application(root, max_jobs)
    server.app.memory_cap_mb = max_sort_mb
    return server


def source_roots_file(path):
    """Folders listed in source-roots.txt, one per line, for server-path input on any platform (the Windows
    launcher has no command line to edit). Blank lines and # comments are ignored; a missing folder is reported
    and skipped so the server still starts."""
    roots=[]
    try:
        lines=Path(path).read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return roots
    for line in lines:
        value=line.strip()
        if not value or value.startswith('#'):
            continue
        folder=Path(os.path.expandvars(os.path.expanduser(value)))
        if folder.is_dir():
            roots.append(folder.resolve())
        else:
            print(f'{Path(path).name}: {value} is not a folder this server can read; skipped.', flush=True)
    return roots


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
    parser.add_argument('--max-jobs', type=int, default=max(2, min(4, (os.cpu_count() or 2) // 2)), help='Maximum simultaneous comparisons; CPUs and memory are shared between them by input size. Reports and analysis run in their own lane')
    parser.add_argument('--max-sort-mb', '--max-memory-mb', dest='max_sort_mb', type=int, default=0, help='Optional administrator memory ceiling in MB; 0 sizes automatically from available RAM')
    parser.add_argument('--source-root', action='append', default=[], help='Allow read-only server files below this directory; repeat for multiple roots')
    parser.add_argument('--data-dir', default=str(BASE / 'data'), help='Upload, scratch and report storage; use a fast SSD')
    parser.add_argument('--retention-days', type=int, default=7, help='Delete inactive jobs after this many days; 0 disables automatic cleanup')
    parser.add_argument('--analysis-retention-days', type=int, default=3, help='Delete the rebuildable mismatch-analysis index after this many days unused; 0 keeps it')
    parser.add_argument('--open', action='store_true', help='Open the UI in your default browser')
    args = parser.parse_args()
    if args.retention_days < 0:
        parser.error('--retention-days must be zero or positive')
    data_lock = lock_data_directory(args.data_dir)
    server = make_server(args.data_dir, args.port, args.host, args.public_url, args.max_jobs, args.max_sort_mb)
    server.source_roots=[Path(value).resolve(strict=True) for value in args.source_root]
    if any(not p.is_dir() for p in server.source_roots):parser.error('--source-root must be a directory')
    server.source_roots+=source_roots_file(BASE/'source-roots.txt')
    if server.source_roots:
        print('Server files can be read from: '+'; '.join(str(root) for root in server.source_roots), flush=True)
    server.app.retention_days = args.retention_days
    server.app.analysis_retention_days = max(0, args.analysis_retention_days)
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
    print(f"Concurrent heavy jobs: {args.max_jobs}; comparison memory: {str(args.max_sort_mb)+' MB administrator ceiling' if args.max_sort_mb else 'automatic, no fixed ceiling'}", flush=True)
    if server.shared:
        print('Team mode: no login; shared history and projects, independent uploads. Keep access on a trusted internal network.', flush=True)
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
