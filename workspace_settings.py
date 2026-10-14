"""Persistent workspace defaults; jobs retain their execution-time snapshot."""
from compare import execution_settings
from comparison_rules import validate_rules

DEFAULT_MEMORY_MB=15360
MEMORY_BUDGETS=(64,128,256,512,1024,2048,4096,8192,12288,15360)
PERFORMANCE=('memory_mb','comparison_engine','sort_workers','read_batch_size','compare_batch_size')

def validate_engine(value):
    if value not in ('auto','sort'):raise ValueError('Choose automatic comparison or disk sort')
    return value

def validate_settings(config,limit):
    memory=config.get('memory_mb',min(DEFAULT_MEMORY_MB,limit) if limit else DEFAULT_MEMORY_MB);workers=config.get('sort_workers',2)
    if type(memory) is not int or memory not in MEMORY_BUDGETS or (limit and memory>limit):raise ValueError('Invalid comparison memory setting')
    if type(workers) is not int or workers not in (1,2):raise ValueError('Choose one or two sort workers')
    engine=validate_engine(config.get('comparison_engine','auto'))
    batches=execution_settings(config)
    rules=(config.get('comparison_rules') or [])
    if not isinstance(rules,list) or any(not isinstance(r,dict) or not isinstance(r.get('column'),str) or not r['column'].strip() for r in rules):raise ValueError('Every rule needs a column name')
    names=[r['column'] for r in rules]
    if len({n.casefold() for n in names})!=len(names):raise ValueError('Only one settings rule per column is allowed')
    return dict(memory_mb=memory,comparison_engine=engine,sort_workers=workers,read_batch_size=batches['read_batch_size'],compare_batch_size=batches['compare_batch_size'],comparison_rules=validate_rules(rules,names,[],[]))

def resolved_rules(settings,config,columns,keys,ignored):
    available={name.casefold():name for name in columns if name not in keys and name not in ignored}
    if 'comparison_rules' in config:
        return validate_rules(config.get('comparison_rules') or [],columns,keys,ignored)
    rules={available[r['column'].casefold()]:dict(r,column=available[r['column'].casefold()]) for r in settings.get('comparison_rules',[]) if r['column'].casefold() in available}
    rules.update({r['column']:r for r in (config.get('comparison_rules') or [])})
    return validate_rules(list(rules.values()),columns,keys,ignored)
