"""Re-evaluate retained comparison evidence without opening source files."""
import csv,json,sqlite3,time,shutil
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
from pathlib import Path
from contextlib import ExitStack,closing
from compare import validate_scope,validate_overrides,encode
from comparison_rules import validate_rules,equivalent

def exclusions(config,columns):
    keys=config['keys'];_,excluded=validate_scope(keys,columns,config.get('ignore_columns',[]),config.get('ignore_keys',''))
    audit=[]
    for item in config.get('ignore_key_containers',[]):
        _,values=validate_scope(keys,columns,[],item['values']);excluded.update(values)
        audit.append(dict(name=item['name'],reason=item['reason'],keys=[json.loads(k) for k in sorted(values)]))
    if config.get('ignore_keys','').strip():
        _,values=validate_scope(keys,columns,[],config['ignore_keys'])
        audit.append(dict(name='Manual keys',reason='Manually entered exclusion',keys=[json.loads(k) for k in sorted(values)]))
    return excluded,audit

def plan(job,baseline,config):
    summary=json.loads((baseline/'summary.json').read_text())
    reason=None
    if config['keys']!=summary['keys']:reason='Key columns changed.'
    elif config.get('duplicate_policy','first')!=summary.get('duplicate_policy','first'):reason='Duplicate-key selection changed.'
    elif not set(summary.get('ignored_columns',[]))<=set(config.get('ignore_columns',[])):reason='Previously unretained columns are being restored.'
    old={encode(k) for c in summary.get('ignore_key_containers',[]) for k in c['keys']}
    current,_=exclusions(config,job['columns'])
    if not old<=current:reason='Previously unretained keys are being restored.'
    if current!=old and not (baseline/'matched_keys.csv').exists():reason='This older comparison has no retained key inventory.'
    if not (baseline/'raw_differences.csv').exists():
        reason='This older comparison did not retain values accepted by its rules.'
    return dict(mode='full' if reason else 'review',reason=reason or 'Apply rules to retained differences; source files will not be read or sorted.')

def revise(baseline,output,config,columns,notify,live_file=None):
    started=time.monotonic();output.mkdir(exist_ok=True)
    s=json.loads((baseline/'summary.json').read_text());original=s['changed_cells']
    ignored=set(config.get('ignore_columns',[]));excluded,audit=exclusions(config,columns)
    _,overrides=validate_overrides(config.get('value_overrides',[]),columns,config['keys'],ignored)
    rules={r['column']:r for r in validate_rules(config.get('comparison_rules',[]),columns,config['keys'],ignored)}
    counts={c:0 for c in s['changed_cells_by_column'] if c not in ignored}
    raw=baseline/'raw_differences.csv';has_raw=raw.exists();raw=raw if has_raw else baseline/'differences.csv'
    # Rows to re-check: raw differences also hold the cells the original rules and overrides had accepted.
    expected=original+(s.get('override_equivalent_cells',0)+s.get('rule_equivalent_cells',0) if has_raw else 0)
    accepted=filtered=raw_count=0;s['override_equivalent_cells']=s['rule_equivalent_cells']=0
    dbpath=output/'review-counts.sqlite'
    # Live counts for the Analysis page while the review runs, the same snapshot a full comparison writes.
    live=None
    if live_file:
        from live import LiveSnapshot
        stats=dict(matched_keys=s.get('matched_keys',0),left_only=s.get('left_only',0),right_only=s.get('right_only',0),changed_cells=0,changed_rows=0,equal_rows=s.get('matched_keys',0),override_equivalent_cells=0,rule_equivalent_cells=0)
        live=LiveSnapshot(live_file,stats,counts)
        def tick(state='reviewing',force=False,reviewed=0):
            stats.update(override_equivalent_cells=s['override_equivalent_cells'],rule_equivalent_cells=s['rule_equivalent_cells'],equal_rows=max(0,stats['matched_keys']-stats['changed_rows']))
            live.tick(state,force=force,reviewed=reviewed,review_total=max(expected,reviewed))
        tick(force=True)
    try:
        with closing(sqlite3.connect(dbpath)) as db,ExitStack() as stack:
            db.executescript('PRAGMA temp_store=FILE; PRAGMA cache_size=-8192; CREATE TABLE changed(k TEXT PRIMARY KEY);')
            writers={}
            for name in ('differences','accepted_by_rules'):
                writers[name]=csv.writer(stack.enter_context((output/(name+'.csv')).open('w',encoding='utf-8',newline='')))
                writers[name].writerow(['key_json','column','left_value','right_value'])
            for i,row in enumerate(csv.DictReader(stack.enter_context(raw.open(encoding='utf-8',newline=''))),1):
                raw_count+=1
                if i%10000==0:db.commit();notify(f'Reviewed {i:,} retained differences')
                if row['column'] in ignored or encode(json.loads(row['key_json'])) in excluded:filtered+=1;continue
                a,b=row['left_value'],row['right_value']
                if has_raw:
                    a=None if row['left_null']=='1' else a;b=None if row['right_null']=='1' else b
                name=row['column'];category=None
                if (a,b) in overrides.get(name,()):category='override_equivalent_cells'
                elif name in rules and equivalent(a,b,rules[name]):category='rule_equivalent_cells'
                def display(v):return '[NULL]' if v is None else '[NULL] (text)' if v=='[NULL]' else v
                values=[row['key_json'],name,display(a) if has_raw else a,display(b) if has_raw else b]
                if category:
                    accepted+=1;s[category]+=1;writers['accepted_by_rules'].writerow(values)
                else:
                    counts[name]+=1;writers['differences'].writerow(values)
                    if db.execute('INSERT OR IGNORE INTO changed VALUES (?)',(row['key_json'],)).rowcount and live:stats['changed_rows']+=1
                    if live:stats['changed_cells']+=1;live.sample(values)
                if live and i&255==0:tick(reviewed=i)
            s['changed_rows']=db.execute('SELECT count(*) FROM changed').fetchone()[0]
            if live:tick(force=True,reviewed=raw_count)
            removed={'left':0,'right':0}
            if (baseline/'matched_keys.csv').exists():
                matched=0
                reader=csv.reader(stack.enter_context((baseline/'matched_keys.csv').open(encoding='utf-8',newline='')));next(reader)
                for i,row in enumerate(reader,1):
                    if i%10000==0:notify(f'Reviewing key scope: {i:,}')
                    if encode(json.loads(row[0])) in excluded:removed['left']+=1;removed['right']+=1
                    else:matched+=1
                s['matched_keys']=matched
            for kind,side in [('left_only','left'),('right_only','right')]:
                reader=csv.reader(stack.enter_context((baseline/(kind+'.csv')).open(encoding='utf-8',newline='')))
                writer=csv.writer(stack.enter_context((output/(kind+'.csv')).open('w',encoding='utf-8',newline='')));writer.writerow(next(reader));count=0
                for i,row in enumerate(reader,1):
                    if i%10000==0:notify(f'Reviewing {kind}: {i:,}')
                    if encode(row) in excluded:removed[side]+=1
                    else:writer.writerow(row);count+=1
                s[kind]=count
            for side in ('left','right'):
                s[side+'_excluded_rows']+=removed[side];s[side+'_compared_rows']-=removed[side]
            db.commit()
    finally:dbpath.unlink(missing_ok=True)
    s.update(changed_cells=sum(counts.values()),changed_cells_by_column=counts,equal_rows=s['matched_keys']-s['changed_rows'],comparison_rules=list(rules.values()),value_overrides=config.get('value_overrides',[]),ignored_columns=list(config.get('ignore_columns',[])),ignore_key_containers=audit,ignored_keys_count=len(excluded),elapsed_seconds=round(time.monotonic()-started,3),review_revision=dict(original_mismatches=original,raw_differences=raw_count,accepted_by_rules=accepted,excluded_from_scope=filtered,remaining_mismatches=sum(counts.values())))
    for name in ('duplicate_keys.csv','annotations.json'):
        if (baseline/name).exists():shutil.copy2(baseline/name,output/name)
    (output/'summary.json').write_text(json.dumps(s,indent=2),encoding='utf-8')
    if live:
        stats.update(matched_keys=s['matched_keys'],left_only=s['left_only'],right_only=s['right_only'],changed_rows=s['changed_rows'],changed_cells=s['changed_cells'])
        tick('writing',force=True,reviewed=raw_count)
    notify(f'Review complete: {accepted:,} accepted, {filtered:,} excluded, {s["changed_cells"]:,} remaining')
    return s
