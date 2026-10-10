"""Persistent workspace defaults; jobs retain their execution-time snapshot."""
from compare import execution_settings
from comparison_rules import validate_rules

PERFORMANCE=('memory_mb','sort_workers','read_batch_size','compare_batch_size')

def validate_settings(config,limit):
    memory=config.get('memory_mb',min(4096,limit));workers=config.get('sort_workers',2)
    if type(memory) is not int or memory not in (64,128,256,512,1024,2048,4096,8192) or memory>limit:raise ValueError(f'Choose a sort budget up to {limit} MB')
    if type(workers) is not int or workers not in (1,2):raise ValueError('Choose one or two sort workers')
    batches=execution_settings(config)
    rules=(config.get('comparison_rules') or [])
    if not isinstance(rules,list) or any(not isinstance(r,dict) or not isinstance(r.get('column'),str) or not r['column'].strip() for r in rules):raise ValueError('Every rule needs a column name')
    names=[r['column'] for r in rules]
    if len({n.casefold() for n in names})!=len(names):raise ValueError('Only one settings rule per column is allowed')
    return dict(memory_mb=memory,sort_workers=workers,read_batch_size=batches['read_batch_size'],compare_batch_size=batches['compare_batch_size'],comparison_rules=validate_rules(rules,names,[],[]))

def resolved_rules(settings,config,columns,keys,ignored):
    available={name.casefold():name for name in columns if name not in keys and name not in ignored}
    if 'comparison_rules' in config:
        return validate_rules(config.get('comparison_rules') or [],columns,keys,ignored)
    rules={available[r['column'].casefold()]:dict(r,column=available[r['column'].casefold()]) for r in settings.get('comparison_rules',[]) if r['column'].casefold() in available}
    rules.update({r['column']:r for r in (config.get('comparison_rules') or [])})
    return validate_rules(list(rules.values()),columns,keys,ignored)
