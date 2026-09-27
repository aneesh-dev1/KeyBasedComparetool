"""Explicit opt-in, non-key value normalization; never alters source values."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def validate_rules(rules, columns, keys, ignored):
    if not isinstance(rules,list) or len(rules)>2000: raise ValueError('Use at most 2,000 column rules')
    result=[];seen=set()
    for rule in rules:
        if not isinstance(rule,dict): raise ValueError('Invalid comparison rule')
        name=rule.get('column')
        if name not in columns or name in keys or name in ignored or name in seen: raise ValueError('Rules need distinct compared non-key columns')
        seen.add(name)
        clean={'column':name,'trim':rule.get('trim',False),'ignore_case':rule.get('ignore_case',False)}
        if any(type(clean[f]) is not bool for f in ('trim','ignore_case')): raise ValueError('Trim and case settings must be boolean')
        tolerance=rule.get('tolerance','')
        if tolerance!='':
            try:
                value=Decimal(str(tolerance))
                if not value.is_finite() or value<0 or abs(value.adjusted())>1000 or len(str(tolerance))>100: raise ValueError()
            except (InvalidOperation,ValueError): raise ValueError('Tolerance must be a finite, non-negative decimal')
            clean['tolerance']=str(value)
        for side in ('left','right'):
            fmt=rule.get(side+'_date_format','')
            if not isinstance(fmt,str) or len(fmt)>100: raise ValueError('Date formats must be short text strings')
            if fmt:
                try:
                    if '%' not in fmt:raise ValueError()
                    datetime.strptime(datetime(2026,9,28,12,34,56,tzinfo=timezone.utc).strftime(fmt),fmt)
                except ValueError:raise ValueError('Use valid Python date directives, for example %Y-%m-%d')
            clean[side+'_date_format']=fmt
        if bool(clean['left_date_format'])!=bool(clean['right_date_format']): raise ValueError('Specify a date format for each file')
        if clean['left_date_format'] and 'tolerance' in clean: raise ValueError('Choose numeric tolerance or dates for a column, not both')
        result.append(clean)
    return result


def equivalent(left,right,rule):
    if rule.get('trim'): left,right=left.strip(),right.strip()
    if rule.get('ignore_case'): left,right=left.casefold(),right.casefold()
    if left==right:return True
    if rule.get('left_date_format'):
        try:return datetime.strptime(left,rule['left_date_format'])==datetime.strptime(right,rule['right_date_format'])
        except ValueError:return False
    if 'tolerance' in rule and max(len(left),len(right))<=1000:
        try:
            a,b,t=Decimal(left),Decimal(right),Decimal(rule['tolerance'])
            if not a.is_finite() or not b.is_finite() or max(abs(a.adjusted()),abs(b.adjusted()),abs(t.adjusted()))>1000:return False
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec=max(len(a.as_tuple().digits),len(b.as_tuple().digits),len(t.as_tuple().digits))+abs(a.adjusted()-b.adjusted())+10
                return abs(a-b)<=t
        except InvalidOperation:return False
    return False
