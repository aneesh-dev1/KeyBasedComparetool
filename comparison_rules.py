"""Explicit opt-in, non-key value normalization; never alters source values."""
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache

# Typed comparison: values are still read as exact text; a type only decides when two texts are equal.
TYPES = ('number', 'decimal', 'date', 'timestamp')


@lru_cache(maxsize=64)
def _zone(name):
    if name in ('', 'UTC'):
        return timezone.utc
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _decimal(text):
    if len(text) > 1000:
        raise InvalidOperation()
    # Plain numbers only: thousands separators are ambiguous across locales, so they stay mismatches.
    value = Decimal(text.strip())
    if not value.is_finite() or abs(value.adjusted()) > 1000:
        raise InvalidOperation()
    return value


def _date(text):
    text = text.strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return datetime.fromisoformat(text.replace('Z', '+00:00').replace('z', '+00:00')[:32]).date() if len(text) > 10 else date.fromisoformat(text)


def _instant(text, zone):
    moment = datetime.fromisoformat(text.strip().replace('Z', '+00:00').replace('z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_zone(zone))
    return moment.astimezone(timezone.utc)


def typed_equal(left, right, rule):
    """True when both texts parse as the rule's type and are equal as that type; False otherwise."""
    kind = rule['type']
    try:
        if kind == 'number':
            return _decimal(left) == _decimal(right)
        if kind == 'decimal':
            step = Decimal(1).scaleb(-rule['scale'])
            return _decimal(left).quantize(step, ROUND_HALF_UP) == _decimal(right).quantize(step, ROUND_HALF_UP)
        if kind == 'date':
            return _date(left) == _date(right)
        if kind == 'timestamp':
            return _instant(left, rule.get('timezone', 'UTC')) == _instant(right, rule.get('timezone', 'UTC'))
    except (InvalidOperation, ValueError, OverflowError):
        return False
    return False


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
        kind=rule.get('type','') or ''
        if kind:
            if kind not in TYPES: raise ValueError('Compare as must be number, decimal, date or timestamp')
            if 'tolerance' in clean or clean['left_date_format']: raise ValueError('Choose a compare-as type or a tolerance/date format for a column, not both')
            clean['type']=kind
            if kind=='decimal':
                scale=rule.get('scale')
                if not isinstance(scale,int) or isinstance(scale,bool) or not 0<=scale<=18: raise ValueError('Decimal scale must be a whole number from 0 to 18')
                clean['scale']=scale
            if kind=='timestamp':
                zone=rule.get('timezone','UTC') or 'UTC'
                try:
                    if not isinstance(zone,str) or len(zone)>64: raise ValueError()
                    _zone(zone)
                except Exception: raise ValueError('Use a time zone name such as UTC or Asia/Kolkata')
                clean['timezone']=zone
        result.append(clean)
    return result


def equivalent(left,right,rule):
    if left is None or right is None: return left is right
    if rule.get('trim'): left,right=left.strip(),right.strip()
    if rule.get('ignore_case'): left,right=left.casefold(),right.casefold()
    if left==right:return True
    if rule.get('type'):return typed_equal(left,right,rule)
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
