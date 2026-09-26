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


def compare_json(left_text, right_text, default='ordered', rules=None):
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
    def add(kind,lp,rp,a=None,b=None):
        if len(differences)>=MAX_DIFFERENCES:raise ValueError('More than 10,000 differences. Compare a smaller JSON document; no partial result was returned.')
        differences.append(dict(kind=kind,left_path=lp,right_path=rp,left_value=dump(a) if lp is not None else None,right_value=dump(b) if rp is not None else None))
    def walk(a,b,path,lp,rp):
        if fingerprint(a,path)==fingerprint(b,path):return
        if isinstance(a,dict) and isinstance(b,dict):
            for key in sorted(a.keys()|b.keys()):
                if key not in b:add('removed',property_path(lp,key),None,a[key])
                elif key not in a:add('added',None,property_path(rp,key),b=b[key])
                else:walk(a[key],b[key],path+(key,),property_path(lp,key),property_path(rp,key))
        elif isinstance(a,list) and isinstance(b,list):
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
                    if matches:matched.add(matches.popleft())
                    else:add('removed',f'{lp}[{i}]',None,item)
                for j,item in enumerate(b):
                    if j not in matched:add('added',None,f'{rp}[{j}]',b=item)
        else:add('changed',lp,rp,a,b)
    walk(left,right,(),'$','$')
    return dict(equal=not differences,counts={kind:sum(d['kind']==kind for d in differences) for kind in ('added','removed','changed')},differences=differences,default_order=default,rules=rules,warnings=[f'Rule did not match an array: {value[2]}' for key,value in indexed.items() if key not in used])
