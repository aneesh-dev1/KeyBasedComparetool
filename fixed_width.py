"""Fixed-width files (mainframe-style .dat extracts) described by column positions or a COBOL copybook.

A file is converted once into the app's standard CSV form (UTF-8, header row), so every engine reads it the
same way as any other delimited file. Values are cut exactly at their positions; padding spaces are trimmed
only when asked, and an implied decimal point (PIC ...V9...) is inserted where the copybook places it.
"""
import csv
import re

ENCODINGS = ('utf-8', 'cp1252', 'latin-1', 'cp037')
MAX_FIELDS = 5000


def parse_positions(text):
    """Lines of ``name,start,length`` (start is 1-based). Blank lines and lines starting with # are ignored."""
    fields = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in re.split(r'[,\t|]', line)]
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError(f'Layout line {number}: use name,start,length, for example account_id,1,10')
        start, length = int(parts[1]), int(parts[2])
        if start < 1 or length < 1:
            raise ValueError(f'Layout line {number}: start and length must be at least 1')
        fields.append(dict(name=parts[0], start=start, length=length, decimals=0))
    return _checked(fields)


def _pic_size(picture):
    """Width in characters and implied decimals of a DISPLAY picture such as X(10), 9(5)V99 or S9(7)V9(2)."""
    picture = picture.upper().rstrip('.')
    if not re.fullmatch(r'S?([XA9V]|\(\d+\))+', picture):
        raise ValueError(f'Unsupported picture clause {picture}. Use DISPLAY fields such as X(10) or S9(7)V99.')
    width = decimals = 0
    after_point = False
    for symbol, count in re.findall(r'([XA9V])(?:\((\d+)\))?', picture.lstrip('S')):
        size = int(count or 1)
        if symbol == 'V':
            after_point = True
            continue
        width += size
        if after_point:
            decimals += size
    return width, decimals


def parse_copybook(text):
    """Elementary DISPLAY items of a copybook, in order. FILLER is skipped; COMP fields and OCCURS are rejected."""
    statements = re.sub(r'(?m)^.{0,6}\*.*$', '', text)  # comment lines
    statements = ' '.join(line[6:72] if len(line) > 6 and line[:6].strip().isdigit() else line for line in statements.splitlines())
    fields, position = [], 1
    for statement in statements.split('.'):
        words = statement.split()
        if len(words) < 2 or not words[0].isdigit():
            continue
        upper = [word.upper() for word in words]
        if 'OCCURS' in upper:
            raise ValueError(f'{words[1]}: OCCURS is not supported; list repeated fields as column positions instead')
        if 'REDEFINES' in upper:
            continue
        if any(word.startswith('COMP') or word in ('BINARY', 'PACKED-DECIMAL') for word in upper):
            raise ValueError(f'{words[1]}: binary and packed (COMP/COMP-3) fields are not supported; export the file as DISPLAY text')
        if 'PIC' not in upper and 'PICTURE' not in upper:
            continue  # group item
        picture = words[(upper.index('PIC') if 'PIC' in upper else upper.index('PICTURE')) + 1]
        width, decimals = _pic_size(picture)
        if upper[1] != 'FILLER':
            fields.append(dict(name=words[1], start=position, length=width, decimals=decimals))
        position += width
    if not fields:
        raise ValueError('No elementary PIC fields were found in the copybook')
    return _checked(fields)


def _checked(fields):
    if not fields:
        raise ValueError('Add at least one field')
    if len(fields) > MAX_FIELDS:
        raise ValueError(f'Use at most {MAX_FIELDS:,} fields')
    names = [field['name'].casefold() for field in fields]
    if len(set(names)) != len(names):
        raise ValueError('Field names must be unique')
    return fields


def parse_layout(text, kind):
    if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
        raise ValueError('Paste a layout')
    return parse_copybook(text) if kind == 'copybook' else parse_positions(text)


def _value(record, field, trim):
    value = record[field['start'] - 1:field['start'] - 1 + field['length']]
    if trim:
        value = value.strip(' ')
    if field['decimals'] and value:
        digits = value.strip(' ')
        sign = digits[0] if digits[:1] in '+-' else ''
        digits = digits[len(sign):]
        if digits.isdigit():
            value = f'{sign}{digits[:-field["decimals"]] or "0"}.{digits[-field["decimals"]:].rjust(field["decimals"], "0")}'
    return value


def records(path, encoding, record_length=0):
    """Fixed-length records, or one record per line (CR/LF removed) when no record length is given."""
    with open(path, encoding=encoding, newline='') as stream:
        if record_length:
            while True:
                record = stream.read(record_length)
                if not record:
                    return
                yield record
        else:
            for line in stream:
                yield line.rstrip('\r\n')


def convert(path, fields, target, encoding='utf-8', record_length=0, trim=True, notify=lambda count: None):
    """Write the file as UTF-8 CSV with a header row. Short records are an error rather than silently padded."""
    width = max(field['start'] - 1 + field['length'] for field in fields)
    count = 0
    with open(target, 'w', encoding='utf-8', newline='') as output:
        writer = csv.writer(output)
        writer.writerow([field['name'] for field in fields])
        for count, record in enumerate(records(path, encoding, record_length), 1):
            if not record:
                continue  # an empty line; a line of spaces is a record whose fields are blank
            if len(record) < width:
                raise ValueError(f'Record {count:,} is {len(record)} characters but the layout needs {width}. Check the layout or record length.')
            writer.writerow([_value(record, field, trim) for field in fields])
            if count % 100000 == 0:
                notify(count)
    return count
