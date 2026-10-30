"""Moving a project between Data Compare installations as one .zip.

A bundle holds data only: the project, its template, every round's settings and result files (cell differences,
keys found in one file, duplicates, mismatch patterns, the sampled keys), audit statuses, activity and sign-offs.
No HTML: the importing app builds each round's report and the sign-off report from that data with its own
templates, so an imported project looks like one made there.

    manifest.json                 project, template, rounds (settings + audit), sign-offs, activity
    rounds/<n>/job.json           the round's comparison settings and summary
    rounds/<n>/report/<file>      the round's result files

A round whose comparison was deleted (only its audit summary is left) travels as that summary in the manifest.
"""
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

FORMAT, VERSION = 'datacompare-project', 1
# Machine-specific or rebuilt on import: workspace owner, server paths, report builds, the analysis index.
TRANSIENT = {'owner', 'source_paths', 'exports', 'analysis', 'upload_compression', 'resume_strategy', 'can_resume',
             'allocation', 'memory_concurrency', 'ai_options', 'excel_options'}
MEMBER = re.compile(r'manifest\.json|rounds/[1-9][0-9]{0,5}/(job\.json|report/[A-Za-z0-9][A-Za-z0-9._-]{0,120})')
MAX_UNPACKED = 200 * 1024 ** 3
RECORD_ONLY = {'project_id', 'audit_status', 'audit_note', 'reviewer', 'updated'}


def _report_files(folder):
    """A round's result files: everything in report/ except built HTML."""
    folder = Path(folder)
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() not in ('.html', '.part')) if folder.is_dir() else []


def export(app, store, identity, template, target):
    """Write project ``identity`` to the zip ``target``."""
    detail = store.detail(identity)
    rounds = store.runs(identity)
    index = {record['id']: n for n, record in enumerate(rounds, 1)}
    manifest = dict(format=FORMAT, version=VERSION, exported=time.time(),
                    project={k: detail['project'][k] for k in ('name', 'description', 'created')},
                    template=template and {k: template[k] for k in ('name', 'source_headers', 'column_headers', 'config')},
                    rounds=[], signoffs=[], events=[])
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        for n, record in enumerate(rounds, 1):
            audit = dict(n=n, title=record.get('title'), audit_status=record['audit_status'], audit_note=record['audit_note'], reviewer=record['reviewer'])
            if not (app.directory(record['id']) / 'job.json').exists():
                manifest['rounds'].append(dict(audit, summary={k: v for k, v in record.items() if k not in RECORD_ONLY}, files=[]))
                continue
            job = {k: v for k, v in app.load(record['id']).items() if k not in TRANSIENT}
            if isinstance(job.get('sample'), dict) and job['sample'].get('from'):
                job['sample'] = dict(job['sample'], **{'from': index.get(job['sample']['from'])})
            bundle.writestr(f'rounds/{n}/job.json', json.dumps(job))
            files = _report_files(app.directory(record['id']) / 'report')
            for path in files:
                bundle.write(path, f'rounds/{n}/report/{path.name}')
            manifest['rounds'].append(dict(audit, files=[path.name for path in files]))
        for item in store.signoffs(identity):
            if item['round_id'] in index:
                manifest['signoffs'].append(dict(round=index[item['round_id']], **{k: item[k] for k in ('at', 'reviewer', 'note', 'mode', 'reasons')}))
        for item in store.events_of(identity):
            manifest['events'].append(dict(at=item['at'], message=item['message'], round=index.get(item['record_id'])))
        bundle.writestr('manifest.json', json.dumps(manifest))
    return manifest


def read(path):
    """The manifest of a bundle, after checking every member is one a bundle may hold."""
    try:
        bundle = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError('This file isn’t a project export. Choose the .zip made by Export project.') from error
    with bundle:
        names = bundle.namelist()
        if 'manifest.json' not in names:
            raise ValueError('This .zip has no project in it. Choose the .zip made by Export project.')
        for info in bundle.infolist():
            if not MEMBER.fullmatch(info.filename) or info.is_dir():
                raise ValueError(f'The export contains an unexpected file ({info.filename[:80]}), so it can’t be imported')
        if sum(info.file_size for info in bundle.infolist()) > MAX_UNPACKED:
            raise ValueError('The export unpacks to more than 200 GB, so it can’t be imported')
        try:
            manifest = json.loads(bundle.read('manifest.json'))
        except ValueError as error:
            raise ValueError('The export’s manifest is damaged, so it can’t be imported') from error
        if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
            raise ValueError('This .zip isn’t a Data Compare project export')
        if manifest.get('version') != VERSION:
            raise ValueError('This export was made by a newer version of Data Compare. Update the app to import it.')
        project = manifest.get('project') or {}
        if not isinstance(project.get('name'), str) or not isinstance(manifest.get('rounds'), list):
            raise ValueError('The export’s manifest is incomplete, so it can’t be imported')
        for round_ in manifest['rounds']:
            if not isinstance(round_, dict) or not isinstance(round_.get('n'), int):
                raise ValueError('The export’s list of rounds is damaged, so it can’t be imported')
            if isinstance(round_.get('summary'), dict):
                continue
            if f"rounds/{round_['n']}/job.json" not in names:
                raise ValueError(f"Round {round_['n']} is missing from the export")
            try:
                job = json.loads(bundle.read(f"rounds/{round_['n']}/job.json"))
                valid = all(isinstance(job['files'][side]['name'], str) for side in ('left', 'right')) and isinstance(job['keys'], list) \
                    and isinstance(job.get('summary'), dict) and isinstance(job['created'], (int, float))
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                raise ValueError(f"Round {round_['n']} in the export is damaged, so the project can’t be imported")
    return manifest


def preview(manifest, store):
    project = manifest['project']
    return dict(name=project['name'], description=project.get('description', ''), created=project.get('created'),
                exported=manifest.get('exported'), rounds=len(manifest['rounds']), signoffs=len(manifest.get('signoffs', [])),
                template=(manifest.get('template') or {}).get('name'), exists=store.name_taken(project['name']))


def restore_template(template, profiles):
    """Add the bundle's template to the library ``profiles`` (a list, changed in place); returns its id. The same
    template already there is reused; a different one with the same name is added under a new name."""
    if not isinstance(template, dict) or not isinstance(template.get('config'), dict):
        return None
    same = next((p for p in profiles if p['name'].casefold() == str(template['name']).casefold()), None)
    if same and same.get('config') == template['config'] and same.get('column_headers') == template.get('column_headers'):
        return same['id']
    name, n = str(template['name'])[:90], 1
    while any(p['name'].casefold() == name.casefold() for p in profiles):
        n += 1
        name = f"{str(template['name'])[:86]} ({n})"
    if len(profiles) >= 100:
        raise ValueError('The template library is full (100). Delete an unused template, then import again.')
    profile = dict(id=uuid.uuid4().hex, name=name, source_headers=template.get('source_headers'),
                   column_headers=template.get('column_headers'), config=template['config'])
    profiles.append(profile)
    return profile['id']


def install(app, store, path, manifest, name, owner, profile_id):
    """Create the project from the bundle: one finished comparison per round, with its result files, then audit
    statuses, activity and sign-offs at their original times. Returns (project id, sign-off ids, round ids)."""
    project = manifest['project']
    identity = store.create(dict(name=name, description=project.get('description', ''), created=project.get('created'),
                                 profile_id=profile_id, imported=True))
    ids, now = {}, time.time()
    try:
        _install_rounds(app, store, path, manifest, identity, owner, ids, now)
    except Exception:
        # Nothing of a failed import stays behind: not the project, its records, or any round's folder.
        store.remove(identity)
        store.forget(ids.values())
        for job_id in ids.values():
            shutil.rmtree(app.directory(job_id), ignore_errors=True)
        raise
    events = [dict(at=e['at'], message=e['message'], record_id=ids.get(e.get('round'))) for e in manifest.get('events', [])
              if isinstance(e, dict) and isinstance(e.get('at'), (int, float)) and isinstance(e.get('message'), str)]
    events.append(dict(at=now, message=f"Imported from an export of “{project['name']}”" + (f' as “{name}”' if name != project['name'] else ''), record_id=None))
    signoffs = [dict(round_id=ids[s['round']], at=s['at'], reviewer=s['reviewer'], note=s.get('note', ''), mode=s['mode'], reasons=s.get('reasons') or {})
                for s in manifest.get('signoffs', []) if isinstance(s, dict) and s.get('round') in ids]
    return identity, store.restore(identity, events, signoffs), ids


def _install_rounds(app, store, path, manifest, identity, owner, ids, now):
    project = manifest['project']
    with zipfile.ZipFile(path) as bundle:
        for round_ in manifest['rounds']:
            n = round_['n']
            job_id = uuid.uuid4().hex
            ids[n] = job_id
            audit = (round_.get('audit_status') or 'Unreviewed', round_.get('audit_note') or '', round_.get('reviewer') or '')
            if isinstance(round_.get('summary'), dict):
                # Only the audit summary was left of this round; it comes back the same way.
                store.sync(dict(round_['summary'], id=job_id, owner=owner, source_available=False))
                store.adopt(job_id, identity, *audit)
                continue
            job = json.loads(bundle.read(f'rounds/{n}/job.json'))
            folder = app.directory(job_id)
            (folder / 'report').mkdir(parents=True)
            prefix = f'rounds/{n}/report/'
            for member in bundle.namelist():
                if member.startswith(prefix):
                    with bundle.open(member) as source, (folder / 'report' / member[len(prefix):]).open('wb') as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
            if isinstance(job.get('sample'), dict) and job['sample'].get('from'):
                job['sample'] = dict(job['sample'], **{'from': ids.get(job['sample']['from'])})
            for side in job.get('files', {}).values():
                side.update(uploaded=side.get('size', 0), complete=True)
            job.update(id=job_id, owner=owner, state='complete', exports={},
                       analysis=dict(state='not_started', message='Prepare analysis to include mismatch patterns.'),
                       imported=dict(at=now, project=project['name'], round=n))
            app.save(job)
            store.adopt(job_id, identity, *audit)
