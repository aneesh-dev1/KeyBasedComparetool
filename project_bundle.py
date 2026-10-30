"""Moving a project between Data Compare installations as one .zip.

A bundle holds data only: the project, its template, every round's settings and result files (cell differences,
keys found in one file, duplicates, mismatch patterns, the sampled keys), audit statuses, activity and sign-offs.
No HTML: the importing app builds each round's report and the sign-off report from that data with its own
templates, so an imported project looks like one made there.

    manifest.json                       project, template, rounds (settings + audit), sign-offs, activity
    rounds/<n>/job.json                 the round's comparison settings and summary
    rounds/<n>/report/<file>            the round's result files
    subprojects/<i>/rounds/<n>/...      a parent project's subprojects, each like a project of its own

A parent project travels with all its subprojects (listed under ``subprojects`` in the manifest); a subproject is
exported only as part of its parent. A round whose comparison was deleted (only its audit summary is left) travels
as that summary in the manifest.
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
MEMBER = re.compile(r'manifest\.json|(?:subprojects/[1-9][0-9]{0,3}/)?rounds/[1-9][0-9]{0,5}/(job\.json|report/[A-Za-z0-9][A-Za-z0-9._-]{0,120})')
MAX_UNPACKED = 200 * 1024 ** 3
RECORD_ONLY = {'project_id', 'audit_status', 'audit_note', 'reviewer', 'updated', 'as_of'}


def _report_files(folder):
    """A round's result files: everything in report/ except built HTML."""
    folder = Path(folder)
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() not in ('.html', '.part')) if folder.is_dir() else []


def _template(profile):
    return profile and {k: profile[k] for k in ('name', 'source_headers', 'column_headers', 'config')}


def _export_part(app, store, identity, profiles, bundle, prefix):
    """One project's part of the manifest, with its rounds' files written under ``prefix``."""
    project = store.detail(identity)['project']
    rounds = store.runs(identity)
    index = {record['id']: n for n, record in enumerate(rounds, 1)}
    part = dict(project={k: project[k] for k in ('name', 'description', 'created')},
                template=_template(profiles.get(project.get('profile_id'))), rounds=[], signoffs=[], events=[])
    for n, record in enumerate(rounds, 1):
        audit = dict(n=n, title=record.get('title'), audit_status=record['audit_status'], audit_note=record['audit_note'],
                     reviewer=record['reviewer'], as_of=record.get('as_of'))
        if not (app.directory(record['id']) / 'job.json').exists():
            part['rounds'].append(dict(audit, summary={k: v for k, v in record.items() if k not in RECORD_ONLY}, files=[]))
            continue
        job = {k: v for k, v in app.load(record['id']).items() if k not in TRANSIENT}
        if isinstance(job.get('sample'), dict) and job['sample'].get('from'):
            job['sample'] = dict(job['sample'], **{'from': index.get(job['sample']['from'])})
        bundle.writestr(f'{prefix}rounds/{n}/job.json', json.dumps(job))
        files = _report_files(app.directory(record['id']) / 'report')
        for path in files:
            bundle.write(path, f'{prefix}rounds/{n}/report/{path.name}')
        part['rounds'].append(dict(audit, files=[path.name for path in files]))
    for item in store.signoffs(identity):
        if item['round_id'] in index:
            part['signoffs'].append(dict(round=index[item['round_id']], **{k: item[k] for k in ('at', 'reviewer', 'note', 'mode', 'reasons')}))
    for item in store.events_of(identity):
        part['events'].append(dict(at=item['at'], message=item['message'], round=index.get(item['record_id'])))
    return part


def export(app, store, identity, profiles, target):
    """Write project ``identity`` (with its subprojects, if it has any) to the zip ``target``. ``profiles`` maps
    template ids to the library's templates."""
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        manifest = dict(format=FORMAT, version=VERSION, exported=time.time(), **_export_part(app, store, identity, profiles, bundle, ''))
        children = store.children(identity)
        if children:
            manifest['subprojects'] = [_export_part(app, store, child, profiles, bundle, f'subprojects/{i}/') for i, child in enumerate(children, 1)]
        bundle.writestr('manifest.json', json.dumps(manifest))
    return manifest


def parts(manifest):
    """The bundle's projects with where their files are: the project itself, then each subproject."""
    yield manifest, ''
    for i, sub in enumerate(manifest.get('subprojects') or [], 1):
        yield sub, f'subprojects/{i}/'


def _check_part(bundle, names, part, prefix):
    project = part.get('project') if isinstance(part, dict) else None
    if not isinstance(project, dict) or not isinstance(project.get('name'), str) or not isinstance(part.get('rounds'), list):
        raise ValueError('The export’s manifest is incomplete, so it can’t be imported')
    where = '' if not prefix else f' of {project["name"]}'
    for round_ in part['rounds']:
        if not isinstance(round_, dict) or not isinstance(round_.get('n'), int):
            raise ValueError('The export’s list of rounds is damaged, so it can’t be imported')
        if isinstance(round_.get('summary'), dict):
            continue
        member = f"{prefix}rounds/{round_['n']}/job.json"
        if member not in names:
            raise ValueError(f"Round {round_['n']}{where} is missing from the export")
        try:
            job = json.loads(bundle.read(member))
            valid = all(isinstance(job['files'][side]['name'], str) for side in ('left', 'right')) and isinstance(job['keys'], list) \
                and isinstance(job.get('summary'), dict) and isinstance(job['created'], (int, float))
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise ValueError(f"Round {round_['n']}{where} in the export is damaged, so the project can’t be imported")


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
        if not isinstance(manifest.get('subprojects') or [], list):
            raise ValueError('The export’s list of subprojects is damaged, so it can’t be imported')
        for part, prefix in parts(manifest):
            _check_part(bundle, names, part, prefix)
        subs = [sub['project']['name'].strip().casefold() for sub in manifest.get('subprojects') or []]
        if len(set(subs)) != len(subs):
            raise ValueError('Two subprojects in the export have the same name, so it can’t be imported')
    return manifest


def preview(manifest, store):
    project, subs = manifest['project'], manifest.get('subprojects') or []
    all_parts = [part for part, _ in parts(manifest)]
    templates = list(dict.fromkeys(part['template']['name'] for part in all_parts if isinstance(part.get('template'), dict)))
    return dict(name=project['name'], description=project.get('description', ''), created=project.get('created'),
                exported=manifest.get('exported'), subprojects=[sub['project']['name'] for sub in subs],
                rounds=sum(len(part['rounds']) for part in all_parts), signoffs=sum(len(part.get('signoffs', [])) for part in all_parts),
                template=', '.join(templates) or None, exists=store.name_taken(project['name']))


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


def install(app, store, path, manifest, name, owner, profile_ids):
    """Create the project (and its subprojects) from the bundle: one finished comparison per round, with its result
    files, then audit statuses, activity and sign-offs at their original times. ``profile_ids`` holds each part's
    restored template id, in the order of ``parts``. Returns (project id, {project id: sign-off ids}, round ids)."""
    now, created, jobs, installed = time.time(), [], [], []
    try:
        for (part, prefix), profile_id in zip(parts(manifest), profile_ids):
            project = part['project']
            identity = store.create(dict(name=name if not prefix else project['name'], description=project.get('description', ''),
                                         created=project.get('created'), profile_id=profile_id, imported=True,
                                         parent_id=created[0] if prefix else None))
            created.append(identity)
            ids = {}
            try:
                _install_rounds(app, store, path, part, prefix, identity, owner, ids, now)
            finally:
                jobs.extend(ids.values())
            installed.append((identity, part, ids))
    except Exception:
        # Nothing of a failed import stays behind: not the projects, their records, or any round's folder.
        for identity in reversed(created):
            store.remove(identity)
        store.forget(jobs)
        for job_id in jobs:
            shutil.rmtree(app.directory(job_id), ignore_errors=True)
        raise
    signoffs = {}
    for identity, part, ids in installed:
        events = [dict(at=e['at'], message=e['message'], record_id=ids.get(e.get('round'))) for e in part.get('events', [])
                  if isinstance(e, dict) and isinstance(e.get('at'), (int, float)) and isinstance(e.get('message'), str)]
        if identity == created[0]:
            original = manifest['project']['name']
            events.append(dict(at=now, message=f'Imported from an export of “{original}”' + (f' as “{name}”' if name != original else ''), record_id=None))
        items = [dict(round_id=ids[s['round']], at=s['at'], reviewer=s['reviewer'], note=s.get('note', ''), mode=s['mode'], reasons=s.get('reasons') or {})
                 for s in part.get('signoffs', []) if isinstance(s, dict) and s.get('round') in ids]
        signoffs[identity] = store.restore(identity, events, items)
    return created[0], signoffs, jobs


def _install_rounds(app, store, path, part, prefix, identity, owner, ids, now):
    with zipfile.ZipFile(path) as bundle:
        for round_ in part['rounds']:
            n = round_['n']
            job_id = uuid.uuid4().hex
            ids[n] = job_id
            audit = (round_.get('audit_status') or 'Unreviewed', round_.get('audit_note') or '', round_.get('reviewer') or '', round_.get('as_of'))
            if isinstance(round_.get('summary'), dict):
                # Only the audit summary was left of this round; it comes back the same way.
                store.sync(dict(round_['summary'], id=job_id, owner=owner, source_available=False))
                store.adopt(job_id, identity, *audit)
                continue
            job = json.loads(bundle.read(f'{prefix}rounds/{n}/job.json'))
            folder = app.directory(job_id)
            (folder / 'report').mkdir(parents=True)
            files = f'{prefix}rounds/{n}/report/'
            for member in bundle.namelist():
                if member.startswith(files):
                    with bundle.open(member) as source, (folder / 'report' / member[len(files):]).open('wb') as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
            if isinstance(job.get('sample'), dict) and job['sample'].get('from'):
                job['sample'] = dict(job['sample'], **{'from': ids.get(job['sample']['from'])})
            for side in job.get('files', {}).values():
                side.update(uploaded=side.get('size', 0), complete=True)
            job.update(id=job_id, owner=owner, state='complete', exports={},
                       analysis=dict(state='not_started', message='Prepare analysis to include mismatch patterns.'),
                       imported=dict(at=now, project=part['project']['name'], round=n))
            app.save(job)
            store.adopt(job_id, identity, *audit)
