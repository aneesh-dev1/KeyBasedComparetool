"""Bounded, type-aware nested JSON comparison with explicit array matching rules."""
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

MAX_BYTES = 5 * 1024 * 1024
MAX_NODES = 200000
MAX_DIFFERENCES = 10000

class Number(str):
    pass


def dump(value):
    if isinstance(value, Number):
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False)+':'+dump(v) for k,v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(dump(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def parse(text, side):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'{side}: maximum JSON input size is 5 MiB')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'{side}: duplicate object property {key!r}')
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f'{side}: {value} is not valid JSON')
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_int=Number, parse_float=Number, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f'{side}: invalid or excessively nested JSON: {error}') from error
    count = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop(); count += 1
        if depth > 100 or count > MAX_NODES:
            raise ValueError(f'{side}: limit is 100 nesting levels and 200,000 values')
        if isinstance(item, dict): stack.extend((v,depth+1) for v in item.values())
        elif isinstance(item, list): stack.extend((v,depth+1) for v in item)
    return value


def parse_path(path):
    if not isinstance(path,str) or not path.startswith('$'):
        raise ValueError('Array paths must start with $, for example $.customers[*].addresses')
    result=[]; i=1
    while i<len(path):
        if path.startswith('[*]',i): result.append(None); i+=3
        elif path[i]=='.':
            match=re.match(r'[A-Za-z_][A-Za-z0-9_]*',path[i+1:])
            if not match: raise ValueError(f'Invalid array path: {path}')
            result.append(match[0]); i+=len(match[0])+1
        elif path.startswith('["',i):
            try: key,used=json.JSONDecoder().raw_decode(path[i+1:])
            except ValueError as error: raise ValueError(f'Invalid array path: {path}') from error
            i+=used+1
            if not isinstance(key,str) or i>=len(path) or path[i]!=']': raise ValueError(f'Invalid array path: {path}')
            result.append(key); i+=1
        else: raise ValueError('Use property names and [*] for array ancestors; numeric indexes and recursive JSONPath are not supported')
    return tuple(result)


def property_path(path,key):
    return path+'.'+key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key) else path+'['+json.dumps(key,ensure_ascii=False)+']'


def discover_arrays(left_text, right_text):
    """Find per-array direct scalar keys valid in every occurrence on either side."""
    groups = {}
    def atom(value):
        if isinstance(value, Number):
            try:
                return ('number', Decimal(value))
            except InvalidOperation as error:
                raise ValueError('JSON numeric exponent is too large') from error
        return (type(value).__name__, value)
    def walk(value, path, side):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, property_path(path, key), side)
        elif isinstance(value, list):
            if path not in groups:
                if len(groups) >= 100:
                    raise ValueError('Array discovery supports up to 100 distinct array paths. Use advanced rules for this document.')
                groups[path] = dict(path=path, left_items=0, right_items=0, candidates=None)
            group = groups[path]
            group[side + '_items'] += len(value)
            if value:
                candidates = set(value[0]) if isinstance(value[0], dict) else set()
                for item in value:
                    if not isinstance(item, dict):
                        candidates.clear(); break
                    candidates.intersection_update(item)
                valid = set()
                for field in candidates:
                    if not field:
                        continue
                    seen = set()
                    for item in value:
                        val = item[field]
                        if val is None or isinstance(val, (dict, list)):
                            break
                        key = atom(val)
                        if key in seen:
                            break
                        seen.add(key)
                    else:
                        valid.add(field)
                group['candidates'] = valid if group['candidates'] is None else group['candidates'] & valid
            for child in value:
                walk(child, path + '[*]', side)
    walk(parse(left_text, 'File 1'), '$', 'left')
    walk(parse(right_text, 'File 2'), '$', 'right')
    result = []
    for group in groups.values():
        fields = sorted(group.pop('candidates') or set(), key=lambda key: (key.lower() != 'id', key))
        result.append(dict(**group, fields=fields))
    return dict(arrays=result)


def compare_json(left_text, right_text, default='ordered', rules=None, include_view=False):
    if default not in ('ordered','unordered'): raise ValueError('Choose preserve or ignore array order')
    rules=[] if rules is None else rules
    if not isinstance(rules,list) or len(rules)>100: raise ValueError('Use up to 100 array rules')
    indexed={}; used=set()
    for rule in rules:
        if not isinstance(rule,dict): raise ValueError('Invalid array rule')
        path=parse_path(rule.get('path'))
        mode=rule.get('mode'); field=rule.get('field','')
        if mode not in ('ordered','unordered','keyed') or (mode=='keyed' and (not isinstance(field,str) or not field)):
            raise ValueError('Every match-by-field rule needs a field name')
        if path in indexed: raise ValueError('Each array path can have only one rule')
        indexed[path]=(mode,field,rule['path'])
    left=parse(left_text,'File 1');right=parse(right_text,'File 2')
    fingerprints={}
    def policy(path):
        if path in indexed:
            used.add(path)
            return indexed[path][:2]
        return default,''
    def scalar(value):
        if isinstance(value,Number):
            try: n=Decimal(value)
            except InvalidOperation as error: raise ValueError('JSON numeric exponent is too large') from error
            # Decimal hashing preserves numeric equality without expanding exponents.
            return ('number',n)
        if value is None:return ('null',)
        if isinstance(value,bool):return ('boolean',value)
        if isinstance(value,str):return ('string',value)
        raise ValueError('Match fields must contain a non-null scalar value')
    def key_map(items,field,path):
        result={}
        for index,item in enumerate(items):
            if not isinstance(item,dict) or field not in item or item[field] is None:
                raise ValueError(f'{path}: every array object must have a non-null {field!r} field')
            key=scalar(item[field])
            if key in result:raise ValueError(f'{path}: duplicate match value for {field!r}: {dump(item[field])}')
            result[key]=(index,item)
        return result
    def fingerprint(value,path):
        if isinstance(value,(dict,list)) and id(value) in fingerprints:return fingerprints[id(value)]
        if isinstance(value,dict):
            parts=['object']+[(key,fingerprint(value[key],path+(key,))) for key in sorted(value)]
        elif isinstance(value,list):
            mode,field=policy(path)
            if mode=='keyed':key_map(value,field,indexed[path][2])
            parts=[fingerprint(v,path+(None,)) for v in value]
            if mode!='ordered':parts.sort()
            parts=['array',*parts]
        else:
            atom=scalar(value)
            if atom[0]=='number':
                n=atom[1]; sign,digits,exponent=n.as_tuple(); digits=list(digits)
                while digits and digits[-1]==0:digits.pop();exponent+=1
                parts=['number',sign if digits else 0,digits,exponent if digits else 0]
            else:parts=atom
        result=hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        if isinstance(value,(dict,list)):fingerprints[id(value)]=result
        return result
    fingerprint(left,());fingerprint(right,())
    differences=[]
    view=[]
    # Each token retains its path and line in a pretty-printed original document.
    # Display pairing can move a block while its original position stays visible.
    def tokens(value):
        lines=[]; spans={}
        def visit(item,path,depth,prefix='',suffix=''):
            start=len(lines)
            def emit(text):
                lines.append(dict(text=text, depth=depth, path=path, line=len(lines)+1))
            if isinstance(item,(dict,list)):
                is_object=isinstance(item,dict)
                emit(prefix+('{' if is_object else '['))
                entries=list(item.items()) if is_object else list(enumerate(item))
                for i,(key,child) in enumerate(entries):
                    visit(child,property_path(path,key) if is_object else f'{path}[{key}]',depth+1,
                          json.dumps(key,ensure_ascii=False)+': ' if is_object else '', ',' if i<len(entries)-1 else '')
                emit(('}' if is_object else ']')+suffix)
            else:emit(prefix+dump(item)+suffix)
            spans[path]=(start,len(lines))
        visit(value,'$',0)
        return lines,spans
    alines,aspans=tokens(left) if include_view else ([],{})
    blines,bspans=tokens(right) if include_view else ([],{})
    def emit(lp,rp,kind='equal',difference=None,closing=False,whole=False):
        if not include_view:return
        def select(lines,spans,path):
            if path is None:return []
            start,end=spans[path]
            return lines[start:end] if whole else [lines[end-1 if closing else start]]
        aa=select(alines,aspans,lp);bb=select(blines,bspans,rp)
        for i in range(max(len(aa),len(bb))):
            view.append(dict(left=aa[i] if i<len(aa) else None,right=bb[i] if i<len(bb) else None,kind=kind,difference=difference))
    def add(kind,lp,rp,a=None,b=None):
        if len(differences)>=MAX_DIFFERENCES:raise ValueError('More than 10,000 differences. Compare a smaller JSON document; no partial result was returned.')
        emit(lp,rp,kind,len(differences),whole=True)
        differences.append(dict(kind=kind,left_path=lp,right_path=rp,left_value=dump(a) if lp is not None else None,right_value=dump(b) if rp is not None else None))
    def walk(a,b,path,lp,rp):
        equal=fingerprint(a,path)==fingerprint(b,path)
        if equal and not include_view:return
        if isinstance(a,dict) and isinstance(b,dict):
            emit(lp,rp)
            for key in sorted(a.keys()|b.keys()):
                if key not in b:add('removed',property_path(lp,key),None,a[key])
                elif key not in a:add('added',None,property_path(rp,key),b=b[key])
                else:walk(a[key],b[key],path+(key,),property_path(lp,key),property_path(rp,key))
            emit(lp,rp,closing=True)
        elif isinstance(a,list) and isinstance(b,list):
            emit(lp,rp)
            mode,field=policy(path)
            if mode=='ordered':
                for i in range(max(len(a),len(b))):
                    if i>=len(b):add('removed',f'{lp}[{i}]',None,a[i])
                    elif i>=len(a):add('added',None,f'{rp}[{i}]',b=b[i])
                    else:walk(a[i],b[i],path+(None,),f'{lp}[{i}]',f'{rp}[{i}]')
            elif mode=='keyed':
                am=key_map(a,field,lp);bm=key_map(b,field,rp)
                for key,(i,item) in am.items():
                    if key not in bm:add('removed',f'{lp}[{i}]',None,item)
                    else:
                        j,other=bm[key];walk(item,other,path+(None,),f'{lp}[{i}]',f'{rp}[{j}]')
                for key,(j,item) in bm.items():
                    if key not in am:add('added',None,f'{rp}[{j}]',b=item)
            else:
                remaining=defaultdict(deque)
                for j,item in enumerate(b):remaining[fingerprint(item,path+(None,))].append(j)
                matched=set()
                for i,item in enumerate(a):
                    matches=remaining[fingerprint(item,path+(None,))]
                    if matches:
                        j=matches.popleft();matched.add(j)
                        walk(item,b[j],path+(None,),f'{lp}[{i}]',f'{rp}[{j}]')
                    else:add('removed',f'{lp}[{i}]',None,item)
                for j,item in enumerate(b):
                    if j not in matched:add('added',None,f'{rp}[{j}]',b=item)
            emit(lp,rp,closing=True)
        elif equal:emit(lp,rp)
        else:add('changed',lp,rp,a,b)
    walk(left,right,(),'$','$')
    return dict(view=view,equal=not differences,counts={kind:sum(d['kind']==kind for d in differences) for kind in ('added','removed','changed')},differences=differences,default_order=default,rules=rules,warnings=[f'Rule did not match an array: {value[2]}' for key,value in indexed.items() if key not in used])
