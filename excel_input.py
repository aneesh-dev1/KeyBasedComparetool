"""Streaming OOXML worksheet input with a disk-backed shared-string table.

Uses stored values (not display formats); never executes formulas or macros.
"""
import csv
from file_io import replace_retry
from functools import lru_cache
import json
import os
from pathlib import Path
import posixpath
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def local(tag):
    return tag.rsplit('}', 1)[-1]


def metadata(book, name):
    if book.getinfo(name).file_size > 16 * 1024 * 1024:
        raise ValueError('Excel workbook metadata is too large')
    return ET.fromstring(book.read(name))


def sheets(path):
    try:
        with zipfile.ZipFile(path) as book:
            relations = {}
            for item in metadata(book, 'xl/_rels/workbook.xml.rels'):
                if item.get('TargetMode') == 'External':
                    continue
                target = item.get('Target', '')
                target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
                if not target.startswith('xl/') or not item.get('Type', '').endswith('/worksheet'):
                    continue
                relations[item.get('Id')] = target
            result = []
            for item in metadata(book, 'xl/workbook.xml').iter():
                if local(item.tag) != 'sheet':
                    continue
                relation = next((v for k, v in item.attrib.items() if local(k) == 'id'), None)
                if relation in relations:
                    result.append(dict(name=item.get('name'), path=relations[relation], state=item.get('state', 'visible')))
            if not result:
                raise ValueError('Workbook has no supported worksheets')
            return result
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Cannot read Excel workbook. Use an unencrypted .xlsx or .xlsm file.') from error


def elements(stream, target):
    stack = []
    for event, item in ET.iterparse(stream, events=('start', 'end')):
        if event == 'start':
            stack.append(item)
        else:
            if local(item.tag) == target:
                yield item
                if len(stack) > 1:
                    stack[-2].remove(item)
                item.clear()
            stack.pop()


def text_content(element):
    # Exclude phonetic guides while preserving rich-text runs and whitespace.
    return ''.join(item.text or '' for child in element for item in
                   (child.iter() if local(child.tag) != 'rPh' else []) if local(item.tag) == 't')


def convert(path, sheet_name, destination, notify=lambda text: None):
    entry = next((item for item in sheets(path) if item['name'] == sheet_name), None)
    if entry is None:
        raise ValueError('Choose an existing worksheet')
    destination = Path(destination)
    temporary = destination.with_suffix('.importing')
    try:
        with tempfile.TemporaryDirectory(dir=destination.parent) as scratch, zipfile.ZipFile(path) as book:
            db = sqlite3.connect(str(Path(scratch) / 'strings.sqlite'))
            try:
                db.execute('PRAGMA cache_size=-4096')
                db.execute('CREATE TABLE strings (id INTEGER PRIMARY KEY, value TEXT)')
                if 'xl/sharedStrings.xml' in book.namelist():
                    notify('Indexing Excel shared strings on disk')
                    with book.open('xl/sharedStrings.xml') as stream:
                        for index, item in enumerate(elements(stream, 'si')):
                            db.execute('INSERT INTO strings VALUES (?, ?)', (index, text_content(item)))
                            if index % 10000 == 0:
                                db.commit()
                        db.commit()
                @lru_cache(maxsize=1024)
                def string(index):
                    row = db.execute('SELECT value FROM strings WHERE id=?', (int(index),)).fetchone()
                    if row is None:
                        raise ValueError('Workbook references an invalid shared string')
                    return row[0]
                names = None
                count = 0
                notify(f'Reading worksheet: {sheet_name}')
                with book.open(entry['path']) as stream, temporary.open('w', encoding='utf-8', newline='') as output:
                    writer = csv.writer(output)
                    for row in elements(stream, 'row'):
                        values = {}
                        next_column = 0
                        for cell in row:
                            if local(cell.tag) != 'c':
                                continue
                            ref = cell.get('r', '')
                            letters = re.match(r'([A-Z]+)[0-9]+$', ref)
                            col = next_column
                            if letters:
                                col = 0
                                for char in letters[1]:
                                    col = col * 26 + ord(char) - 64
                                col -= 1
                            if col >= 16384:
                                raise ValueError('Worksheet exceeds Excel column limits')
                            next_column = col + 1
                            children = {local(child.tag): child for child in cell}
                            kind = cell.get('t', 'n')
                            value = children.get('v')
                            raw = '' if value is None else value.text or ''
                            if 'f' in children and (value is None or (not raw and kind != 'str')):
                                raise ValueError(f'{sheet_name}!{ref}: formula has no saved result. Recalculate and save in Excel before uploading.')
                            if kind == 's':
                                raw = string(raw)
                            elif kind == 'inlineStr':
                                raw = text_content(children['is']) if 'is' in children else ''
                            elif kind == 'b':
                                raw = 'TRUE' if raw == '1' else 'FALSE'
                            values[col] = raw
                        nonempty = [col for col, value in values.items() if value != '']
                        if names is None:
                            if row.get('r', '1') != '1' or not nonempty:
                                raise ValueError('The first worksheet row must contain nonempty, unique column headers')
                            width = max(nonempty) + 1
                            names = [values.get(i, '') for i in range(width)]
                            if any(not name for name in names) or len(set(names)) != len(names):
                                raise ValueError('Worksheet headers must be nonempty and unique')
                            writer.writerow(names)
                        elif nonempty:
                            if max(nonempty) >= len(names):
                                raise ValueError(f'{sheet_name}: data extends beyond the header columns')
                            writer.writerow([values.get(i, '') for i in range(len(names))])
                            count += 1
                            if count % 10000 == 0:
                                notify(f'Importing {sheet_name}: {count:,} rows')
                    if names is None:
                        raise ValueError('Selected worksheet is empty')
            finally:
                db.close()
        replace_retry(temporary, destination)
        return names, count
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Invalid Excel worksheet data') from error
    finally:
        temporary.unlink(missing_ok=True)
