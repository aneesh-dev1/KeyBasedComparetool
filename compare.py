#!/usr/bin/env python3
"""Disk-backed, exact CSV comparison. Python 3.10+, standard library only."""
import argparse
import contextlib
import csv
import heapq
import io
import json
import os
import multiprocessing
from operator import itemgetter
import queue
import struct
from pathlib import Path
import sys
import tempfile
import time
from file_io import atomic_write_text
from task_control import check_cancel
from comparison_rules import validate_rules, equivalent
from parquet_input import is_parquet


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


_PROGRESS_QUEUE = None


def progress(args, phase, **values):
    check_cancel(getattr(args,'cancel_file',None))
    if _PROGRESS_QUEUE is not None:
        _PROGRESS_QUEUE.put(('progress', phase, values))
        return
    stage = ('left' if 'left' in phase.lower() else 'right' if 'right' in phase.lower()
             else 'compare' if phase == 'Comparing keys' else 'reports' if phase == 'Writing reports'
             else 'validate')
    stage = values.pop('stage', stage)
    print(f'{time.strftime("%H:%M:%S")}  {phase}' + ''.join(f' | {key}={value:,}' if isinstance(value, int) else f' | {key}={value}' for key, value in values.items()), flush=True)
    if getattr(args, 'progress_file', None):
        target = Path(args.progress_file)
        try:
            atomic_write_text(target, json.dumps(dict(phase=phase, stage=stage, **values)))
        except PermissionError as error:
            print(f'Progress snapshot disabled: {error}; comparison continues, see console log.', flush=True)
            args.progress_file = None


def normalize_headers(names):
    if not names:
        raise ValueError('CSV must contain a header row')
    reserved = {name.casefold() for name in names if name.strip()}
    used = set()
    result = []
    for number, name in enumerate(names, 1):
        base = name if name.strip() else f'column{number}'
        candidate = base
        suffix = 1
        # Keep explicitly named columns intact, including existing _1 suffixes.
        while candidate.casefold() in used or (candidate.casefold() in reserved and candidate != name):
            candidate = f'{base}_{suffix}'
            suffix += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def align_headers(source, layout=None):
    """Resolve positional aliases using headers only; never scan or rewrite data."""
    layout = source if layout is None else layout
    if not isinstance(layout, dict) or set(layout) != {'left', 'right'}:
        raise ValueError('Provide column header lists for both files')
    for side in ('left', 'right'):
        names = layout[side]
        if not isinstance(names, list) or len(names) != len(source[side]):
            raise ValueError(f'{side}: header count must match the uploaded file')
        if any(not isinstance(n, str) or not n.strip() for n in names):
            raise ValueError(f'{side}: comparison headers cannot be empty')
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError(f'{side}: comparison headers must be unique, ignoring case')
    canonical = {n.casefold(): n for n in layout['left']}
    right = {n.casefold(): n for n in layout['right']}
    aligned = dict(left=list(layout['left']), right=[canonical.get(n.casefold(), n) for n in layout['right']])
    audit = [dict(side=side, column=i, original=old, normalized=new)
             for side in ('left', 'right')
             for i,(old,new) in enumerate(zip(source[side], aligned[side]),1) if old != new]
    return aligned, audit


def common_headers(aligned):
    right = {name.casefold() for name in aligned['right']}
    return [name for name in aligned['left'] if name.casefold() in right]


def read_header(path, delimiter, encoding):
    if is_parquet(path):
        from parquet_input import metadata
        original=metadata(path)['columns'];normalized=normalize_headers(original)
        return normalized,[dict(column=i,original=a,normalized=b) for i,(a,b) in enumerate(zip(original,normalized),1) if a!=b]
    with open(path, encoding=encoding, newline='') as stream:
        original = next(csv.reader(stream, delimiter=delimiter, strict=True), None)
    normalized = normalize_headers(original)
    changes = [dict(column=i, original=before, normalized=after)
               for i,(before,after) in enumerate(zip(original,normalized),1) if before != after]
    return normalized, changes


def header(path, delimiter, encoding):
    return read_header(path, delimiter, encoding)[0]


def validate_scope(keys, columns, ignored_columns=None, ignored_keys_text=''):
    ignored_columns = [] if ignored_columns is None else ignored_columns
    if not isinstance(ignored_columns, list) or not all(isinstance(c, str) for c in ignored_columns):
        raise ValueError('Ignored columns must be a list of column names')
    if len(set(ignored_columns)) != len(ignored_columns) or any(c not in columns for c in ignored_columns):
        raise ValueError('Ignored columns must be distinct existing columns')
    if set(keys) & set(ignored_columns):
        raise ValueError('A key column cannot also be an ignored column')
    if not isinstance(ignored_keys_text, str):
        raise ValueError('Ignored keys must be text')
    if not ignored_keys_text.strip():
        return ignored_columns, set()
    if len(keys) == 1:
        rows = list(csv.reader(io.StringIO(ignored_keys_text), skipinitialspace=True, strict=True))
        if len(rows) != 1 or not rows[0] or any(value == '' for value in rows[0]):
            raise ValueError('Enter comma-separated key values without empty entries. Quote values containing commas.')
        tuples = [[value] for value in rows[0]]
    else:
        try:
            tuples = json.loads(ignored_keys_text)
        except json.JSONDecodeError as error:
            raise ValueError('For composite keys, enter JSON tuples such as [["001","A"],["002","B"]]') from error
        if not isinstance(tuples, list) or any(not isinstance(row, list) or len(row) != len(keys) or
                                               not all(isinstance(value, str) and value != '' for value in row) for row in tuples):
            raise ValueError('Each ignored key tuple must contain one nonempty string per selected key, in selection order')
    return ignored_columns, {encode(row) for row in tuples}


def validate_overrides(rules, columns, keys, ignored_columns):
    if not isinstance(rules, list) or len(rules) > 5000:
        raise ValueError('Value overrides must be a list with at most 5,000 rules')
    lookup = {}
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict) or any(not isinstance(rule.get(field), str) for field in ('column', 'left', 'right')):
            raise ValueError('Every override needs a column, file 1 value and file 2 value, all as text')
        column = rule['column']
        if column not in columns or column in keys or column in ignored_columns:
            raise ValueError(f'Override column {column!r} must be a compared non-key column')
        pair = (rule['left'], rule['right'])
        bucket = lookup.setdefault(column, set())
        if pair not in bucket:
            bucket.add(pair)
            cleaned.append(dict(column=column, left=pair[0], right=pair[1]))
    return cleaned, lookup


# Binary runs avoid JSON quoting overhead on every one of the billions of cells.
# Keys retain their existing JSON representation and sorting semantics.
FRAME = struct.Struct('<QQ')
SEPARATOR = '\x1f'


def pack_values(values):
    if any(v is None for v in values): return b'\x01'+encode(values)
    joined = SEPARATOR.join(values)
    if joined.count(SEPARATOR) == len(values)-1:
        return b'\x00' + joined.encode('utf-8')
    # An embedded separator takes the unambiguous exact-text fallback.
    return b'\x01' + encode(values)


def unpack_values(payload):
    if payload[:1] == b'\x00':
        return payload[1:].decode('utf-8').split(SEPARATOR)
    if payload[:1] == b'\x01':
        return json.loads(payload[1:])
    raise ValueError('Invalid temporary comparison payload')


def records(stream):
    while True:
        framing = stream.read(FRAME.size)
        if not framing:
            return
        if len(framing) != FRAME.size:
            raise ValueError('Truncated temporary comparison run')
        key_size, payload_size = FRAME.unpack(framing)
        key, payload = stream.read(key_size), stream.read(payload_size)
        if len(key) != key_size or len(payload) != payload_size:
            raise ValueError('Truncated temporary comparison record')
        yield key, payload


def merge(paths):
    with contextlib.ExitStack() as stack:
        readers = [records(stack.enter_context(open(p, 'rb', buffering=1024*1024))) for p in paths]
        yield from heapq.merge(*readers, key=itemgetter(0))


def write_run(path, rows, args=None):
    with open(path, 'wb', buffering=1024*1024) as stream:
        for index,(key, payload) in enumerate(rows):
            if index%10000==0: check_cancel(getattr(args,'cancel_file',None))
            stream.write(FRAME.pack(len(key), len(payload)))
            stream.write(key)
            stream.write(payload)
        if getattr(args,'checkpoint_dir',None): stream.flush();os.fsync(stream.fileno())


def sort_csv(path, names, canonical, keys, temp, prefix, args):
    if getattr(args,'checkpoint_dir',None):
        from checkpoints import lock
        # A spawned sorter can outlive a forcibly stopped parent process.
        with lock(Path(temp)/('lock-'+prefix)):
            return _sort_csv(path,names,canonical,keys,temp,prefix,args)
    return _sort_csv(path,names,canonical,keys,temp,prefix,args)


@contextlib.contextmanager
def input_rows(path,args,position=0,indices=None):
    if is_parquet(path):
        from parquet_input import Rows
        with Rows(path,indices,position,getattr(args,'read_batch_size',100000),args.memory_mb) as source:
            yield source,iter(source)
    else:
        with open(path,encoding=args.encoding,newline='',buffering=1024*1024) as source:
            if position: source.seek(position)
            reader=csv.reader(iter(source.readline,''),delimiter=args.delimiter,strict=True)
            if not position: next(reader)
            yield source,reader


def _sort_csv(path, names, canonical, keys, temp, prefix, args):
    progress(args, f'Reading {prefix} file', rows=0)
    projection=[names.index(name) for name in canonical] if is_parquet(path) else None
    if projection is not None: names=canonical
    order = [names.index(name) for name in canonical]
    key_positions = [names.index(name) for name in keys]
    same_order = names == canonical
    project = itemgetter(*order) if len(order) > 1 else None
    paths, chunk = [], []
    size = count = serial = excluded = 0
    budget = args.memory_mb * 1024 * 1024
    checkpoint = None
    position = 0
    reading_complete = False
    if getattr(args, 'checkpoint_dir', None):
        from checkpoints import SortCheckpoint
        progress(args, f'Validating {prefix} saved sort batches')
        checkpoint = SortCheckpoint(temp, prefix, args)
        if checkpoint.state:
            saved = checkpoint.state
            paths = [temp/item['name'] for item in saved['runs']]
            count, excluded, serial, position, reading_complete = (saved[k] for k in ('count','excluded','serial','position','complete'))
            progress(args, f'Resuming {prefix} from saved sort batches', rows=count, batches=len(paths))

    def flush():
        nonlocal chunk, size, serial
        batch_started = time.monotonic()
        progress(args, f'{prefix} sort batch sorting', batch=serial+1, batch_rows=len(chunk), rows=count, status='ongoing')
        chunk.sort(key=itemgetter(0))
        target = temp / f'{prefix}-{serial}.run'
        serial += 1
        write_run(target, chunk, args)
        paths.append(target)
        if checkpoint: checkpoint.save(paths,count,excluded,serial,position,False)
        progress(args, f'{prefix} sort batch complete', batch=serial, batch_rows=len(chunk), rows=count, status='completed', elapsed_seconds=round(time.monotonic()-batch_started, 3))
        chunk, size = [], 0

    if not reading_complete:
        with input_rows(path,args,position,projection) as (stream,reader):
            for row in reader:
                count += 1
                if len(row) != len(names):
                    raise ValueError(f'{path}: record {count}: expected {len(names)} fields, got {len(row)}')
                key = [row[i] for i in key_positions]
                if count % 10000 == 0:
                    progress(args, f'Reading {prefix} file', rows=count, excluded=excluded, batch=serial+1, batch_rows=len(chunk), status='ongoing')
                encoded_key = encode(key)
                if encoded_key in args.excluded_keys:
                    excluded += 1
                    continue
                if not args.allow_empty_keys and any(value is None or value == '' for value in key):
                    raise ValueError(f'{path}: record {count}: empty key component')
                if not chunk:
                    progress(args, f'{prefix} sort batch started', batch=serial+1, rows=count-1, status='ongoing')
                values = row if same_order else project(row) if project is not None else [row[order[0]]]
                item = (encoded_key, pack_values(values))
                # Includes byte objects, tuple, list pointer and sorting headroom.
                size += len(item[0]) + len(item[1]) + 192
                chunk.append(item)
                if size >= budget or len(chunk) >= getattr(args, 'read_batch_size', 100000):
                    if checkpoint: position=stream.tell()
                    flush()
            if checkpoint: position=stream.tell()
        if chunk or not paths:
            flush()
        if checkpoint: checkpoint.save(paths,count,excluded,serial,position,True)
    # Bound open file count and merge-buffer memory even for tiny chunk budgets.
    merge_pass = 0
    while len(paths) > args.fan_in:
        merge_pass += 1
        progress(args, f'Merging {prefix} sorted runs', runs=len(paths))
        replacement = []
        for start in range(0, len(paths), args.fan_in):
            group = paths[start:start + args.fan_in]
            target = temp / f'{prefix}-{serial}.run'
            serial += 1
            batch = start // args.fan_in + 1
            total_batches = (len(paths)+args.fan_in-1)//args.fan_in
            progress(args, f'{prefix} merge batch started', merge_pass=merge_pass, batch=batch, total_batches=total_batches, input_runs=len(group), status='ongoing')
            with contextlib.closing(merge(group)) as rows:
                write_run(target, rows, args)
            if not checkpoint:
                for source in group: source.unlink()
            replacement.append(target)
            progress(args, f'{prefix} merge batch complete', merge_pass=merge_pass, batch=batch, total_batches=total_batches, status='completed')
        if checkpoint:
            checkpoint.save(replacement,count,excluded,serial,position,True)
            for source in paths: source.unlink()
        paths = replacement
    progress(args, f'Read and sort {prefix} complete', rows=count, excluded=excluded)
    return paths, count, excluded


def _sort_task(events, call_args):
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = events
    prefix = call_args[-2]
    try:
        csv.field_size_limit(call_args[-1].max_field_mb * 1024 * 1024)
        result = sort_csv(*call_args)
        events.put(('done', prefix, result))
    except Exception as error:
        events.put(('error', prefix, f'{type(error).__name__}: {error}'))
    finally:
        _PROGRESS_QUEUE = None


def sort_inputs(args, left_names, right_names, canonical, temp):
    workers = getattr(args, 'sort_workers', 1)
    if workers == 1:
        return [sort_csv(source, names, canonical, args.keys, temp, side, args)
                for side, source, names in [('left',args.left,left_names), ('right',args.right,right_names)]]
    # Explicit spawn works on Windows; no fork-only state or inherited open files.
    from types import SimpleNamespace
    worker_args = SimpleNamespace(**vars(args))
    worker_args.memory_mb = args.memory_mb / 2
    worker_args.progress_file = None
    ctx = multiprocessing.get_context('spawn')
    events = ctx.Queue()
    processes = []
    results = {}
    progress(args, 'Read & sort both files', stage='sort', sort_workers=2, total_memory_mb=args.memory_mb)
    try:
        for side, source, names in [('left',args.left,left_names), ('right',args.right,right_names)]:
            call_args = (source,names,canonical,args.keys,temp,side,worker_args)
            process = ctx.Process(target=_sort_task, args=(events,call_args), name=f'csv-sort-{side}')
            process.start()
            processes.append((side,process))
        while len(results) < 2:
            check_cancel(getattr(args,'cancel_file',None))
            try:
                kind, name, value = events.get(timeout=0.2)
            except queue.Empty:
                for side, process in processes:
                    if side not in results and process.exitcode is not None:
                        raise ValueError(f'{side} sorting worker stopped unexpectedly (exit {process.exitcode})')
                continue
            if kind == 'progress':
                progress(args, name, **dict(value, stage='sort'))
            elif kind == 'error':
                raise ValueError(f'{name} sorting failed: {value}')
            else:
                results[name] = value
        for _, process in processes:
            process.join()
            if process.exitcode:
                raise ValueError(f'Sorting worker exited with code {process.exitcode}')
        return [results['left'],results['right']]
    finally:
        for _, process in processes:
            if process.is_alive():
                process.terminate()
            process.join()
        events.close()
        events.join_thread()


def execution_settings(config):
    settings = {}
    for name, default in [('read_batch_size', 100000), ('compare_batch_size', 10000)]:
        value = config.get(name, default)
        if type(value) is not int or not 1 <= value <= 1000000:
            raise ValueError(f'{name} must be an integer between 1 and 1,000,000')
        settings[name] = value
    policy = config.get('duplicate_policy', 'first')
    if policy not in ('first', 'last'):
        raise ValueError('Duplicate handling must be first or last')
    return dict(settings, duplicate_policy=policy)


def unique(rows, label, diagnostics=None, args=None, stats=None, audit=None):
    """Keep one source occurrence per key; stream all duplicate groups to the audit."""
    import itertools
    capture_sample = bool(diagnostics and not Path(diagnostics).exists())
    policy = getattr(args, 'duplicate_policy', 'first')
    for key, group in itertools.groupby(rows, key=lambda row: row[0]):
        first = selected = next(group)
        count = 1
        samples = []
        for item in group:
            count += 1
            if count % 10000 == 0: check_cancel(getattr(args, 'cancel_file', None))
            if capture_sample:
                if count == 2: samples.append(unpack_values(first[1]))
                if len(samples) < 3: samples.append(unpack_values(item[1]))
            if policy == 'last': selected = item
        if count > 1:
            if stats is not None:
                stats[label + '_duplicate_keys'] += 1
                stats[label + '_duplicate_rows_skipped'] += count - 1
            if audit: audit.writerow([label, key.decode('utf-8'), count, count - 1, policy])
            if capture_sample and not Path(diagnostics).exists():
                atomic_write_text(diagnostics, json.dumps(dict(side=label, key=json.loads(key), count=count,
                    sample_columns=getattr(args, 'diagnostic_columns', [])[:20], samples=[['[NULL]' if v is None else v[:500] for v in row[:20]] for row in samples],
                    note=f'Comparison continues using the {policy} source occurrence per key. Extra rows are skipped. This is the first duplicate group; download the duplicate audit for all keys.')))
            capture_sample = False
        yield selected


def compare(args):
    if getattr(args,'checkpoint_dir',None):
        from checkpoints import lock
        with lock(args.checkpoint_dir): return _compare(args)
    return _compare(args)


def _compare(args):
    started = time.monotonic()
    settings = execution_settings(vars(args))
    for name, value in settings.items(): setattr(args, name, value)
    progress(args, 'Validating configuration')
    # CSV cells can exceed Python's small default field limit.
    csv.field_size_limit(args.max_field_mb * 1024 * 1024)
    left_names, left_header_changes = read_header(args.left, args.delimiter, args.encoding)
    right_names, right_header_changes = read_header(args.right, args.delimiter, args.encoding)
    aligned, layout_changes = align_headers(dict(left=left_names, right=right_names), getattr(args, 'column_headers', None))
    left_names, right_names = aligned['left'], aligned['right']
    common = common_headers(aligned)
    unmatched_columns = {side: [name for name in aligned[side] if name not in common] for side in ('left', 'right')}
    if len(set(args.keys)) != len(args.keys) or any(k not in common for k in args.keys):
        raise ValueError('Keys must be distinct existing column names')
    ignored_columns, args.excluded_keys = validate_scope(args.keys, common,
        getattr(args, 'ignore_columns', []), getattr(args, 'ignore_keys', ''))
    exclusion_audit = []
    for container in getattr(args, 'ignore_key_containers', []):
        _, excluded = validate_scope(args.keys, common, [], container['values'])
        args.excluded_keys.update(excluded)
        exclusion_audit.append(dict(name=container['name'], reason=container['reason'],
                                    keys=[json.loads(key.decode('utf-8')) for key in sorted(excluded)]))
    legacy = getattr(args, 'ignore_keys', '')
    if legacy.strip():
        _, excluded = validate_scope(args.keys, common, [], legacy)
        exclusion_audit.append(dict(name='Manual keys', reason='Manually entered exclusion',
                                    keys=[json.loads(key.decode('utf-8')) for key in sorted(excluded)]))
    ignored_set = set(ignored_columns)
    canonical = [name for name in common if name not in ignored_set]
    overrides, override_lookup = validate_overrides(getattr(args, 'value_overrides', []), common, args.keys, ignored_columns)
    rules=validate_rules(getattr(args,'comparison_rules',[]),common,args.keys,ignored_columns)
    rule_lookup={rule['column']:rule for rule in rules}
    args.diagnostic_columns=canonical
    checkpoint_dir=getattr(args,'checkpoint_dir',None)
    if checkpoint_dir:
        from checkpoints import validate
        configuration=dict(keys=args.keys,headers=aligned,canonical=canonical,excluded=sorted(k.decode('utf-8') for k in args.excluded_keys),overrides=overrides,rules=rules,duplicate_policy=args.duplicate_policy,delimiter=args.delimiter,encoding=args.encoding,allow_empty_keys=args.allow_empty_keys)
        validate(checkpoint_dir,args,configuration,lambda message:progress(args,message))
    # Exclusive creation prevents accidental replacement of previous reports.
    out = Path(args.output)
    if out.exists() and checkpoint_dir:
        if (out/'summary.json').exists():
            return json.loads((out/'summary.json').read_text())
        if not (out/'INCOMPLETE').exists(): raise ValueError('Existing report is not an interrupted comparison')
        # Rebuild final comparison output from validated sorted runs; never append partial results.
    out.mkdir(parents=True, exist_ok=bool(checkpoint_dir))
    (out / 'INCOMPLETE').write_text('Reports are incomplete until summary.json is present.\n')
    stats = dict(left_rows=0, right_rows=0, matched_keys=0, equal_rows=0,
                 changed_rows=0, changed_cells=0, left_only=0, right_only=0, override_equivalent_cells=0, rule_equivalent_cells=0)
    stats.update({side + suffix: 0 for side in ('left', 'right') for suffix in ('_duplicate_keys', '_duplicate_rows_skipped')})
    columns = {name: 0 for name in canonical if name not in args.keys}
    with (contextlib.nullcontext(str(checkpoint_dir)) if checkpoint_dir else tempfile.TemporaryDirectory(prefix='csv-compare-', dir=args.temp_dir)) as work:
        temp = Path(work)
        left_result, right_result = sort_inputs(args, left_names, right_names, canonical, temp)
        left_paths, stats['left_rows'], stats['left_excluded_rows'] = left_result
        right_paths, stats['right_rows'], stats['right_excluded_rows'] = right_result
        if checkpoint_dir:
            from checkpoints import verify_sources
            verify_sources(checkpoint_dir)
        progress(args, 'Comparing keys', rows=0)
        with contextlib.ExitStack() as stack:
            writers = {}
            for name, fields in [('differences', ['key_json', 'column', 'left_value', 'right_value']),
                                 ('left_only', args.keys), ('right_only', args.keys),
                                 ('matched_keys', ['key_json']),
                                 ('raw_differences', ['key_json','column','left_value','right_value','left_null','right_null']),
                                 ('duplicate_keys', ['side', 'key_json', 'occurrences', 'skipped_rows', 'kept_occurrence'])]:
                stream = stack.enter_context(open(out / f'{name}.csv', 'w', encoding='utf-8', newline=''))
                writers[name] = csv.writer(stream)
                writers[name].writerow(fields)
            left_merge = stack.enter_context(contextlib.closing(merge(left_paths)))
            right_merge = stack.enter_context(contextlib.closing(merge(right_paths)))
            left = unique(left_merge, 'left',getattr(args,'diagnostics_file',None),args,stats,writers['duplicate_keys'])
            right = unique(right_merge, 'right',getattr(args,'diagnostics_file',None),args,stats,writers['duplicate_keys'])
            a, b = next(left, None), next(right, None)
            processed = 0
            batch_size = args.compare_batch_size
            batch_start = time.monotonic()
            while a is not None or b is not None:
                if processed % batch_size == 0:
                    batch_start = time.monotonic()
                    progress(args, 'Comparison batch started', stage='compare', batch=processed//batch_size+1, rows=processed, max_keys=batch_size, status='ongoing')
                processed += 1
                if b is None or (a is not None and a[0] < b[0]):
                    writers['left_only'].writerow(json.loads(a[0]))
                    stats['left_only'] += 1
                    a = next(left, None)
                elif a is None or b[0] < a[0]:
                    writers['right_only'].writerow(json.loads(b[0]))
                    stats['right_only'] += 1
                    b = next(right, None)
                else:
                    stats['matched_keys'] += 1
                    writers['matched_keys'].writerow([a[0].decode('utf-8')])
                    if a[1] == b[1]:
                        stats['equal_rows'] += 1
                    else:
                        changed = False
                        for name, av, bv in zip(canonical, unpack_values(a[1]), unpack_values(b[1])):
                            if av != bv:
                                writers['raw_differences'].writerow([a[0].decode('utf-8'),name,av,bv,int(av is None),int(bv is None)])
                                if (av, bv) in override_lookup.get(name, ()):
                                    stats['override_equivalent_cells'] += 1
                                    continue
                                if name in rule_lookup and equivalent(av,bv,rule_lookup[name]):
                                    stats['rule_equivalent_cells']+=1
                                    continue
                                changed = True
                                columns[name] += 1
                                stats['changed_cells'] += 1
                                writers['differences'].writerow([a[0].decode('utf-8'), name, '[NULL]' if av is None else '[NULL] (text)' if av=='[NULL]' else av, '[NULL]' if bv is None else '[NULL] (text)' if bv=='[NULL]' else bv])
                        stats['changed_rows' if changed else 'equal_rows'] += 1
                    a, b = next(left, None), next(right, None)
                if processed % batch_size == 0 or (a is None and b is None):
                    progress(args, 'Comparison batch complete', stage='compare', batch=(processed-1)//batch_size+1, batch_keys=(processed-1)%batch_size+1, rows=processed, changed_cells=stats['changed_cells'], status='completed', elapsed_seconds=round(time.monotonic()-batch_start, 3))
            progress(args, 'Comparing keys', stage='compare', rows=processed, completed_batches=(processed+batch_size-1)//batch_size, status='completed')
    if stats['left_duplicate_keys'] or stats['right_duplicate_keys']:
        progress(args, 'WARNING: duplicate keys found; extra rows skipped', policy=args.duplicate_policy, left_skipped=stats['left_duplicate_rows_skipped'], right_skipped=stats['right_duplicate_rows_skipped'])
    progress(args, 'Writing reports')
    stats.update(settings)
    stats.update(input_formats={side:('parquet' if is_parquet(path) else 'csv') for side,path in [('left',args.left),('right',args.right)]}, comparison_rules=rules, unmatched_columns=unmatched_columns, keys=args.keys, comparison='exact text with configured rules and/or overrides' if rules or overrides else 'exact text', changed_cells_by_column=columns,
                 value_overrides=overrides, ignore_key_containers=exclusion_audit,
                 column_headers=aligned, header_layout_changes=layout_changes,
                 header_changes=[dict(side=side, **change) for side, changes in (("left", left_header_changes), ("right", right_header_changes)) for change in changes],
                 ignored_columns=ignored_columns, ignored_keys_count=len(args.excluded_keys),
                 left_compared_rows=stats['left_rows']-stats['left_excluded_rows']-stats['left_duplicate_rows_skipped'],
                 right_compared_rows=stats['right_rows']-stats['right_excluded_rows']-stats['right_duplicate_rows_skipped'],
                 elapsed_seconds=round(time.monotonic() - started, 3))
    if checkpoint_dir: verify_sources(checkpoint_dir)
    atomic_write_text(out / 'summary.json', json.dumps(stats, indent=2))
    (out / 'INCOMPLETE').unlink()
    return stats


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('left')
    p.add_argument('right')
    p.add_argument('--keys', nargs='+', required=True)
    p.add_argument('--output', required=True, help='New report directory; must not exist')
    p.add_argument('--memory-mb', type=int, default=256, help='Sort chunk budget, not total process RSS')
    p.add_argument('--sort-workers', type=int, choices=(1,2), default=1, help='Parallel file-sorting processes; memory-mb is split between them')
    p.add_argument('--read-batch-size', type=int, default=100000)
    p.add_argument('--compare-batch-size', type=int, default=10000)
    p.add_argument('--duplicate-policy', choices=('first', 'last'), default='first')
    p.add_argument('--checkpoint-dir', help='Persistent validated sort batches for restart recovery')
    p.add_argument('--temp-dir', help='Scratch directory, preferably on a fast local SSD')
    p.add_argument('--fan-in', type=int, default=16, help='Maximum sorted streams per merge')
    p.add_argument('--delimiter', default=',')
    p.add_argument('--encoding', default='utf-8-sig')
    p.add_argument('--max-field-mb', type=int, default=64)
    p.add_argument('--allow-empty-keys', action='store_true')
    p.add_argument('--ignore-columns', nargs='+', default=[], help='Column names excluded from value comparison')
    p.add_argument('--ignore-keys', default='', help='Comma-separated values for a single key, or JSON tuples for composite keys')
    p.add_argument('--value-overrides', type=json.loads, default=[], help='JSON rules: [{"column":"status","left":"None","right":"none"}]')
    p.add_argument('--progress-file', help=argparse.SUPPRESS)
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.memory_mb < 1 or args.max_field_mb < 1 or not 2 <= args.fan_in <= 64:
        p.error('Memory/field limits must be positive; fan-in must be between 2 and 64')
    if len(args.delimiter) != 1:
        p.error('Delimiter must be one character')
    try:
        stats = compare(args)
    except (ValueError, OSError, csv.Error, UnicodeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in stats.items() if k != 'changed_cells_by_column'}, indent=2))
    return 1 if stats['changed_cells'] or stats['left_only'] or stats['right_only'] else 0


if __name__ == '__main__':
    sys.exit(main())
