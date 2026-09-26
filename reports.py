"""Streaming report exporters used by the application; no third-party dependencies."""
import csv
import base64
import contextlib
import html
import itertools
import json
from pathlib import Path
import re
import tempfile
import zipfile

TABLES = ('differences', 'left_only', 'right_only')
TITLES = {'differences': 'Changed cells', 'left_only': 'Keys only in left file', 'right_only': 'Keys only in right file'}
STYLE = '''body{font:15px system-ui,sans-serif;color:#004364;background:#f7f9fa;margin:40px auto;max-width:1200px;padding:0 24px}h1{font-size:32px}a{color:#007b99}table{border-collapse:collapse;width:100%;background:white;margin:20px 0}th,td{padding:12px;border:1px solid #dce5eb;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e6f6fa}nav{display:flex;gap:24px}p{line-height:1.6}'''


def summary_rows(summary):
    yield ['Metric', 'Value']
    for key, value in summary.items():
        if key not in ('changed_cells_by_column', 'ignore_key_containers'):
            yield [key.replace('_', ' ').capitalize(), json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value]
    yield ['Column', 'Changed cells']
    for name, count in summary['changed_cells_by_column'].items():
        yield [name, count]


def html_start(title):
    logo = base64.b64encode((Path(__file__).parent / 'web/assets/transunion-logo.svg').read_bytes()).decode('ascii')
    brand = f'<header style="padding:0 0 24px;border-bottom:4px solid #fcd800;margin-bottom:32px"><img src="data:image/svg+xml;base64,{logo}" alt="TransUnion" width="201" height="53"></header>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>' + html.escape(title) + '</title><style>' + STYLE + '</style><body>' + brand + '<h1>' + html.escape(title) + '</h1>'


def html_row(row, heading=False):
    tag = 'th' if heading else 'td'
    return '<tr>' + ''.join(f'<{tag}>' + html.escape(str(value)) + f'</{tag}>' for value in row) + '</tr>\n'


def exclusion_rows(summary):
    yield ['Ignored key containers — configured exclusions (keys may be absent from source files)']
    yield ['Container', 'Reason'] + [f'Key: {key}' for key in summary['keys']]
    for item in summary.get('ignore_key_containers', []):
        for key in item['keys']:
            yield [item['name'], item['reason']] + key


def exclusion_html(summary):
    if not summary.get('ignore_key_containers'):
        return ''
    rows = iter(exclusion_rows(summary))
    title = next(rows)[0]
    return '<h2>Ignored key containers</h2><p>' + html.escape(title) + '</p><table>' + html_row(next(rows), True) + ''.join(html_row(row) for row in rows) + '</table>'


def make_summary_html(summary):
    rows = iter(summary_rows(summary))
    return html_start('CSV comparison summary') + '<p>Text comparison within the selected scope, including any explicit value overrides listed below. Counts refer to matched keys, changed cells, and keys unique to each file.</p><table>' + html_row(next(rows), True) + ''.join(html_row(row) for row in rows) + '</table>' + exclusion_html(summary) + '</body></html>'


def column_letter(number):
    value = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def xml_text(value):
    value = str(value)
    if len(value.encode('utf-16-le')) // 2 > 32767:
        raise ValueError('A value exceeds Excel’s 32,767-character cell limit. Download the HTML or CSV results to preserve the full value.')
    # Preserve literal Excel escape sequences, then encode XML-forbidden controls.
    value = re.sub(r'_x[0-9A-Fa-f]{4}_', lambda m: '_x005F_' + m.group()[1:], value)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]', lambda m: f'_x{ord(m.group()):04X}_', value)
    return html.escape(value, quote=False).replace('\r', '&#13;')


def sheet_names(columns):
    used = set()
    result = []
    for column in columns:
        base = re.sub(r"[\\/*?:\[\]\x00-\x1f]", '_', column).strip("'")[:31].strip("'") or 'Column'
        if base.casefold() == 'history':
            base = 'History_'
        candidate = base
        index = 1
        while candidate.casefold() in used:
            index += 1
            suffix = f' ({index})'
            candidate = base[:31-len(suffix)] + suffix
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def write_xlsx(path, sheets):
    """Write worksheets sequentially; source text is never interpreted as a formula."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as book:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1))
        book.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + overrides + '</Types>')
        book.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        entries = ''.join(f'<sheet name="{html.escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>{entries}</sheets></workbook>')
        relations = ''.join(f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1))
        book.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relations}<Relationship Id="styles" Type="{rel}/styles" Target="styles.xml"/></Relationships>')
        book.writestr('xl/styles.xml', f'''<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF004364"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        for sheet_index, (name, rows) in enumerate(sheets, 1):
            with book.open(f'xl/worksheets/sheet{sheet_index}.xml', 'w', force_zip64=True) as stream:
                stream.write(f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="16384" width="32" customWidth="1"/></cols><sheetData>'.encode())
                max_columns = number = 0
                for number, row in enumerate(rows, 1):
                    if number > 1048576 or len(row) > 16384:
                        raise ValueError('A column exceeds Excel worksheet capacity. Download HTML or CSV results instead.')
                    max_columns = max(max_columns, len(row))
                    cells = []
                    for col, value in enumerate(row, 1):
                        ref = f'{column_letter(col)}{number}'
                        style = 1 if number <= 2 else 0
                        if isinstance(value, (int, float)):
                            cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
                        else:
                            cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>')
                    stream.write((f'<row r="{number}">' + ''.join(cells) + '</row>').encode('utf-8'))
                stream.write((f'</sheetData><autoFilter ref="A2:{column_letter(max_columns)}{max(2, number)}"/></worksheet>').encode())


def export_excel(report, destination, notify=lambda message: None):
    summary = json.loads((report / 'summary.json').read_text())
    columns = [name for name, count in summary['changed_cells_by_column'].items() if count]
    if any(summary['changed_cells_by_column'][name] > 1048574 for name in columns):
        raise ValueError('One column has more than 1,048,574 mismatches and cannot fit in one Excel sheet. Download HTML or CSV instead.')
    audit = [('Ignored key containers', exclusion_rows(summary))] if summary.get('ignore_key_containers') else []
    if not columns:
        write_xlsx(destination, [('No mismatches', iter([['Comparison result'], ['Status', 'Details'], ['No changed cells', 'Keys found in only one file are available in the separate CSV downloads.']]))] + audit)
        return
    # Partition in a single pass. Limit open files even with 2,000 changed columns.
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        paths = {column: Path(work) / f'{i}.jsonl' for i, column in enumerate(columns)}
        # Two-stage partitioning avoids opening/closing a file for every cell
        # when each row has thousands of changed columns.
        groups = [columns[start:start+64] for start in range(0, len(columns), 64)]
        group_for = {column: index for index, group in enumerate(groups) for column in group}
        bucket_paths = [Path(work) / f'bucket-{i}.jsonl' for i in range(len(groups))]
        with contextlib.ExitStack() as stack:
            buckets = [stack.enter_context(path.open('w', encoding='utf-8')) for path in bucket_paths]
            with (report / 'differences.csv').open(encoding='utf-8', newline='') as source:
                for index, row in enumerate(csv.DictReader(source), 1):
                    column = row['column']
                    values = json.loads(row['key_json']) + [row['left_value'], row['right_value']]
                    buckets[group_for[column]].write(json.dumps([column, values], ensure_ascii=False) + '\n')
                    if index % 10000 == 0:
                        notify(f'Grouping mismatches by column: {index:,} cells')
        for group, bucket in zip(groups, bucket_paths):
            with contextlib.ExitStack() as stack:
                handles = {column: stack.enter_context(paths[column].open('w', encoding='utf-8')) for column in group}
                with bucket.open(encoding='utf-8') as source:
                    for line in source:
                        column, values = json.loads(line)
                        handles[column].write(json.dumps(values, ensure_ascii=False) + '\n')
            bucket.unlink()
        def rows(column):
            notify(f'Writing sheet for {column}')
            yield ['Column', column]
            yield [f'Key: {key}' for key in summary['keys']] + ['Left value', 'Right value']
            with paths[column].open(encoding='utf-8') as source:
                for line in source:
                    yield json.loads(line)
        names = sheet_names((['Ignored key containers'] if audit else []) + columns)
        sheets = list(zip(names[len(audit):], (rows(column) for column in columns)))
        write_xlsx(destination, sheets + audit)


def export_html(report, destination, notify=lambda message: None, page_size=1000):
    summary = json.loads((report / 'summary.json').read_text())
    counts = {'differences': summary['changed_cells'], 'left_only': summary['left_only'], 'right_only': summary['right_only']}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
        index = make_summary_html(summary).replace('</body></html>', '<h2>Full results</h2><p>Every result is included. Open a category and use Next to browse its pages.</p>')
        for table in TABLES:
            pages = (counts[table] + page_size - 1) // page_size
            index += f'<p>{TITLES[table]}: {counts[table]:,} records'
            if pages:
                index += f' — <a href="{table}_00001.html">Open results ({pages:,} pages)</a>'
            index += '</p>'
            with (report / f'{table}.csv').open(encoding='utf-8', newline='') as stream:
                rows = csv.reader(stream)
                headers = next(rows)
                for page in range(1, pages + 1):
                    notify(f'Writing {TITLES[table].lower()}, page {page} of {pages}')
                    with bundle.open(f'{table}_{page:05d}.html', 'w', force_zip64=True) as output:
                        def write(text):
                            output.write(text.encode('utf-8'))
                        write(html_start(TITLES[table]))
                        nav = '<nav><a href="index.html">Summary</a>'
                        if page > 1:
                            nav += f'<a href="{table}_{page-1:05d}.html">Previous</a>'
                        if page < pages:
                            nav += f'<a href="{table}_{page+1:05d}.html">Next</a>'
                        nav += f'<span>Page {page:,} of {pages:,}</span></nav>'
                        write(nav + '<table>' + html_row(headers, True))
                        for row in itertools.islice(rows, page_size):
                            write(html_row(row))
                        write('</table>' + nav + '</body></html>')
        bundle.writestr('index.html', index + '</body></html>')
