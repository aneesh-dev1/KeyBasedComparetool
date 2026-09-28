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
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass

REPORT_VERSION = 5

TABLES = ('differences', 'left_only', 'right_only')
TITLES = {'differences': 'Changed cells', 'left_only': 'Keys only in left file', 'right_only': 'Keys only in right file'}
STYLE = '''body{font:15px system-ui,sans-serif;color:#004364;background:#f7f9fa;margin:40px auto;max-width:1200px;padding:0 24px}h1{font-size:32px}a{color:#007b99}table{border-collapse:collapse;width:100%;background:white;margin:20px 0}th,td{padding:12px;border:1px solid #dce5eb;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e6f6fa}nav{display:flex;gap:24px}p{line-height:1.6}'''


def summary_rows(summary):
    yield ['Metric', 'Value']
    for key, value in summary.items():
        if key not in ('changed_cells_by_column', 'ignore_key_containers','analysis_notes'):
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


def report_summary(report):
    summary=json.loads((report/'summary.json').read_text())
    notes=report/'annotations.json'
    summary['analysis_notes']=json.loads(notes.read_text()) if notes.exists() else []
    return summary


def note_rows(summary):
    yield ['Column']+summary['keys']+['Classification','Comments']
    for note in summary.get('analysis_notes',[]):
        yield [note['column'] or 'All columns']+(note['key'] if note['key'] is not None else ['All keys']*len(summary['keys']))+[note['status'],note['comment']]


def column_stats(summary):
    matched = summary.get('matched_keys', 0)
    return [(name, count, 100 * count / matched if matched else None,
             100 * (matched-count) / matched if matched else None)
            for name, count in sorted(summary['changed_cells_by_column'].items(), key=lambda item: (-item[1], item[0]))]


def percent(value):
    return 'N/A' if value is None else f'{value:.4f}%'


def source_labels(summary):
    sources = {item['side']: item for item in summary.get('sources', [])}
    return [sources.get(side, {}).get('file', fallback) +
            (' [' + sources[side]['sheet'] + ']' if sources.get(side, {}).get('sheet') else '')
            for side, fallback in [('left', 'File 1'), ('right', 'File 2')]]


DASH_STYLE = """body{max-width:none;margin:0;padding:0;background:#f3f7f9}body>header,body>h1{display:none}.report-head{background:#004364;color:white;padding:20px 28px;display:flex;gap:24px;align-items:center}.report-head p{margin:0;overflow-wrap:anywhere}.report-grid{display:grid;grid-template-columns:220px minmax(0,1fr)}.report-side{background:#004364;padding:24px;min-height:100vh}.report-side a{display:block;color:white;padding:12px 0;text-decoration:none}.report-main{padding:28px;min-width:0}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.card,.report-section{background:white;border:1px solid #dce5eb;border-radius:8px;padding:18px;margin-bottom:20px}.card{border-top:4px solid #00a6ca}.card strong{display:block;font-size:28px;margin-top:8px}.card small{display:block;color:#526c7b}.report-section h2{font-size:18px;margin:0 0 18px}.table-wrap{overflow:auto}th{background:#004364;color:white}td.before{background:#fff0ee;color:#9b3025}td.after{background:#eaf7ec;color:#256238}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:#eaf7ec;color:#256238;padding:6px 10px;border-radius:4px}summary{cursor:pointer;padding:12px 0;font-weight:600}input[type=search]{padding:10px;max-width:100%;border:1px solid #9cbac5;border-radius:4px}.muted{color:#526c7b}.row-counts{margin:12px 0 24px}nav{flex-wrap:wrap} @media(max-width:900px){.report-grid{grid-template-columns:1fr}.report-side{min-height:0;display:flex;gap:18px;flex-wrap:wrap;padding:8px 20px}.report-main{padding:16px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}"""


def make_summary_html(summary, links=None, previews=None):
    links, previews = links or {}, previews or {}
    stats = column_stats(summary)
    changed = [row for row in stats if row[1]]
    equal = [row[0] for row in stats if not row[1]]
    matched = summary.get('matched_keys', 0)
    overall = 100 * (1-summary['changed_cells']/(matched*len(stats))) if matched and stats else None
    labels = source_labels(summary)
    out = html_start('Comparison report') + '<style>' + DASH_STYLE + '</style>'
    out += '<div class="report-head"><strong>Comparison Report</strong><p>' + html.escape(' vs '.join(labels)) + '</p></div><div class="report-grid"><aside class="report-side"><a href="#summary">Summary</a><a href="#statistics">Column Statistics</a><a href="#differences">Column Differences</a><a href="#matching">100% Match</a><a href="#scope">Scope &amp; exclusions</a></aside><main class="report-main">'
    out += '<section id="summary" class="cards">'
    for title, value in [('Rows matched', f'{matched:,}'), ('Overall cell match rate', percent(overall)), ('Columns with differences', f'{len(changed):,} of {len(stats):,}'), ('Total cell differences', f'{summary["changed_cells"]:,}')]:
        out += '<div class="card"><small>'+title+'</small><strong>'+value+'</strong></div>'
    out += '</section><p class="row-counts">Rows in both files: '+f'{matched:,} · Only in File 1: {summary.get("left_only",0):,} · Only in File 2: {summary.get("right_only",0):,}'+'</p><p class="muted">Rates use matched keys and compared non-key columns, after exclusions and value overrides. Keys found in only one file are reported separately. N/A means no comparable cells. Percentages are rounded to four decimal places.</p>'
    if summary.get('left_duplicate_keys') or summary.get('right_duplicate_keys'):
        out += '<section class="report-section" style="background:#fff8d9;border-left:6px solid #e2ae00"><h2>Warning: duplicate keys</h2><p>' + html.escape(f"Kept the {summary.get('duplicate_policy','first')} source occurrence per key. File 1 skipped rows: {summary.get('left_duplicate_rows_skipped',0):,}; File 2 skipped rows: {summary.get('right_duplicate_rows_skipped',0):,}. Match rates exclude skipped duplicates. The full HTML ZIP includes duplicate_keys.csv.") + '</p></section>'
    if summary.get('ignore_key_containers'):
        out += '<section class="report-section" style="background:#fff8d9;border-left:6px solid #e2ae00"><h2>Ignored key containers — excluded from comparison</h2><p>'+f"File 1 excluded rows: {summary.get('left_excluded_rows',0):,} · File 2 excluded rows: {summary.get('right_excluded_rows',0):,}"+'</p>'
        for item in summary['ignore_key_containers']:
            out += '<p><strong>'+html.escape(item['name'])+'</strong> — '+html.escape(item['reason'])+f" · {len(item['keys']):,} configured keys"+'</p>'
        out += '<a href="#scope">View all configured ignored keys</a></section>'
    out += '<section id="statistics" class="report-section"><h2>Column Statistics</h2><input type="search" id="columnSearch" placeholder="Search columns…" aria-label="Search column statistics"><div class="table-wrap"><table id="columnStats">'+html_row(['Column','Differences','Mismatch %','Match %','View'],True)
    for i,(name,count,mismatch,match) in enumerate(stats):
        link = '<a href="#column-'+str(i)+'">View</a>' if count else 'No differences' if matched else 'No matched rows'
        out += '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [name,count,percent(mismatch),percent(match)])+'<td>'+link+'</td></tr>'
    out += '</table></div></section><section id="differences" class="report-section"><h2>Column Differences</h2>'
    for i,(name,count,_,_) in enumerate(stats):
        if not count: continue
        out += '<details id="column-'+str(i)+'"><summary>'+html.escape(name)+f' · {count:,} differences</summary>'
        if name in previews:
            out += '<div class="table-wrap"><table>'+html_row(summary['keys']+labels,True)
            for row in previews[name]:
                out += '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row[:-2])+'<td class="before">'+html.escape(row[-2])+'</td><td class="after">'+html.escape(row[-1])+'</td></tr>'
            out += '</table></div>'
        if name in links:
            out += '<a href="'+html.escape(links[name],quote=True)+'">View all differences for this column</a>'
        else:
            out += '<p>Generate the full HTML export to browse this column’s mismatches.</p>'
        out += '</details>'
    out += '</section><section id="matching" class="report-section"><h2>Attributes with 100% match</h2><div class="chips">'
    out += ''.join('<span class="chip">'+html.escape(name)+'</span>' for name in equal) if matched else '<p>No matched rows; match rates cannot be calculated.</p>'
    out += '</div></section><section id="scope" class="report-section"><h2>Scope &amp; exclusions</h2><details><summary>Comparison settings and metrics</summary><div class="table-wrap"><table>'
    out += ''.join(html_row(row, i==0) for i,row in enumerate(summary_rows(summary)))+'</table></div></details>'+exclusion_html(summary)+'</section>'
    if summary.get('analysis_notes'):
        out += '<section class="report-section"><h2>Analysis classifications &amp; comments</h2><div class="table-wrap"><table>'+''.join(html_row(row,i==0) for i,row in enumerate(note_rows(summary)))+'</table></div></section>'
    out += """<script>document.getElementById('columnSearch').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('#columnStats tr').forEach((r,i)=>{if(i)r.hidden=!r.cells[0].textContent.toLowerCase().includes(q);});});document.querySelectorAll('a[href^="#column-"]').forEach(a=>a.addEventListener('click',()=>{document.querySelector(a.getAttribute('href')).open=true;}));</script>"""
    return out+'</main></div></body></html>'


@dataclass
class Link:
    text: str
    sheet: str
    cell: str = 'A1'


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


def write_xlsx(path, sheets, options=None, notify=lambda message:None):
    """Write worksheets sequentially; source text is never interpreted as a formula."""
    options = options or {}
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
        book.writestr('xl/styles.xml', f'''<styleSheet xmlns="{ns}"><fonts count="5"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font><font><color rgb="FF9B3025"/><sz val="11"/><name val="Calibri"/></font><font><color rgb="FF256238"/><sz val="11"/><name val="Calibri"/></font><font><color rgb="FF007B99"/><u/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF004364"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="6"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        for sheet_index, (name, rows) in enumerate(sheets, 1):
            header_rows = options.get(name, {}).get('header_rows', 2)
            hyperlinks = []
            with book.open(f'xl/worksheets/sheet{sheet_index}.xml', 'w', force_zip64=True) as stream:
                stream.write(f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_rows}" topLeftCell="A{header_rows+1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="16384" width="32" customWidth="1"/></cols><sheetData>'.encode())
                max_columns = number = 0
                for number, row in enumerate(rows, 1):
                    if number%10000==0: notify(f'Writing {name}: {number:,} rows')
                    if number > 1048576 or len(row) > 16384:
                        raise ValueError('A column exceeds Excel worksheet capacity. Download HTML or CSV results instead.')
                    max_columns = max(max_columns, len(row))
                    cells = []
                    for col, value in enumerate(row, 1):
                        ref = f'{column_letter(col)}{number}'
                        style = 1 if number <= header_rows else 0
                        if options.get(name, {}).get('audit'):
                            style = 5
                        if number > header_rows:
                            color_columns = options.get(name, {}).get('value_columns', [])
                            if col in color_columns:
                                style = 2 + color_columns.index(col)
                        if isinstance(value, Link):
                            style = 4
                            location = "'" + value.sheet.replace("'", "''") + "'!" + value.cell
                            hyperlinks.append(f'<hyperlink ref="{ref}" location="{html.escape(location, quote=True)}"/>')
                            value = value.text
                        if isinstance(value, (int, float)):
                            cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
                        else:
                            cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>')
                    stream.write((f'<row r="{number}">' + ''.join(cells) + '</row>').encode('utf-8'))
                stream.write((f'</sheetData><autoFilter ref="A{header_rows}:{column_letter(max_columns)}{max(header_rows, number)}"/>' + ('<hyperlinks>'+''.join(hyperlinks)+'</hyperlinks>' if hyperlinks else '') + '</worksheet>').encode())


@contextlib.contextmanager
def partition_columns(report, columns, parent, notify):
    # Partition in a single pass. Limit open files even with 2,000 changed columns.
    with tempfile.TemporaryDirectory(dir=parent) as work:
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
                    for index,line in enumerate(source):
                        if index%10000==0:notify('Partitioning column results…')
                        column, values = json.loads(line)
                        handles[column].write(json.dumps(values, ensure_ascii=False) + '\n')
            bucket.unlink()
        yield paths


def export_excel(report, destination, notify=lambda message: None):
    summary = report_summary(report)
    stats = column_stats(summary)
    columns = [name for name,count,_,_ in stats if count]
    if any(summary['changed_cells_by_column'][name] > 1048574 for name in columns):
        raise ValueError('One column has more than 1,048,574 mismatches and cannot fit in one Excel sheet. Download HTML or CSV instead.')
    has_duplicates = bool(summary.get('left_duplicate_keys') or summary.get('right_duplicate_keys'))
    reserved = ['File Summary', 'TOC'] + (['Duplicate keys'] if has_duplicates else []) + (['Ignored key containers'] if summary.get('ignore_key_containers') else []) + (['Analysis notes'] if summary.get('analysis_notes') else [])
    names = sheet_names(reserved + columns)
    mapped = dict(zip(columns, names[len(reserved):]))
    column_notes={n['column']:n['status']+': '+n['comment'] for n in summary.get('analysis_notes',[]) if n['key'] is None}
    def toc():
        yield ['Column', 'DifferenceCount', 'Mismatch %', 'Match %', 'Link', 'Comments']
        for name,count,mismatch,match in stats:
            yield [name,count,round(mismatch,4) if mismatch is not None else 'N/A',round(match,4) if match is not None else 'N/A',Link('View',mapped[name]) if count else 'No differences' if summary.get('matched_keys') else 'No matched rows',column_notes.get(name,'')]
    def summary_sheet():
        yield ['Metric','Value']
        yield ['File 1',source_labels(summary)[0]]
        yield ['File 2',source_labels(summary)[1]]
        yield ['Rows matched',summary.get('matched_keys',0)]
        yield ['Columns with differences',len(columns)]
        yield ['Columns with 100% match',len(stats)-len(columns) if summary.get('matched_keys') else 'N/A']
        yield ['Overall match %',round(sum(row[3] for row in stats)/len(stats),4) if stats and summary.get('matched_keys') else 'N/A']
        yield ['Rate basis','Matched keys only; after exclusions and value overrides. One-sided keys are reported separately.']
        yield ['Types','Values are compared as exact text; typeA/typeB are text. diffAB is valueA minus valueB for finite decimal values only.']
        if summary.get('ignore_key_containers'):
            yield ['IGNORED KEY CONTAINERS',Link('Review exclusions and reasons','Ignored key containers')]
            yield ['Excluded rows — File 1',summary.get('left_excluded_rows',0)]
            yield ['Excluded rows — File 2',summary.get('right_excluded_rows',0)]
            for item in summary['ignore_key_containers']:
                yield [item['name'],item['reason']]
        if has_duplicates:
            yield ['WARNING: duplicate keys',Link('Review skipped rows; kept '+summary.get('duplicate_policy','first')+' source occurrence','Duplicate keys')]
        yield ['Contents',Link('Open TOC','TOC')]
        for row in itertools.islice(summary_rows(summary),1,None):
            value=row[1]
            if isinstance(value,str) and len(value)>15000:
                for start in range(0,len(value),15000):
                    yield [row[0] if not start else row[0]+' (continued)',value[start:start+15000]]
            else:
                yield row
    sheets = [('File Summary',summary_sheet()), ('TOC',toc())]
    if has_duplicates:
        def duplicate_rows():
            with (report / 'duplicate_keys.csv').open(encoding='utf-8', newline='') as stream:
                yield from csv.reader(stream)
        sheets.append(('Duplicate keys', duplicate_rows()))
    if summary.get('ignore_key_containers'):
        sheets.append(('Ignored key containers',exclusion_rows(summary)))
    if summary.get('analysis_notes'):
        sheets.append(('Analysis notes',note_rows(summary)))
    options = {name: {'header_rows':1} for name in ['File Summary','TOC','Analysis notes','Duplicate keys']+list(mapped.values())}
    if summary.get('ignore_key_containers'):
        options['Ignored key containers']={'header_rows':2,'audit':True}
    for name in mapped.values():
        options[name]['value_columns']=[len(summary['keys'])+1,len(summary['keys'])+2]
    with partition_columns(report, columns, destination.parent, notify) as paths:
        def rows(column):
            notify(f'Writing sheet for {column}')
            yield summary['keys']+['valueA','valueB','variable','typeA','typeB','diffAB',Link('Back to TOC','TOC')]
            with paths[column].open(encoding='utf-8') as source:
                for line in source:
                    values=json.loads(line)
                    yield values+[column,'text','text',numeric_difference(values[-2],values[-1])]
        sheets.extend((mapped[column],rows(column)) for column in columns)
        if not columns:
            sheets.append(('No mismatches',iter([['Comparison result'],['Status','Details'],['No changed cells','Check summary for keys found only in one file.']])))
        write_xlsx(destination,sheets,options,notify)


def numeric_difference(left, right):
    if len(left)>1000 or len(right)>1000:
        return ''
    try:
        a,b=Decimal(left),Decimal(right)
        if not a.is_finite() or not b.is_finite() or max(abs(a.adjusted()),abs(b.adjusted()))>1000:
            return ''
        # Diagnostic only; never use numeric coercion for comparison equality.
        from decimal import localcontext
        with localcontext() as context:
            context.prec=max(len(a.as_tuple().digits),len(b.as_tuple().digits))+abs(a.adjusted()-b.adjusted())+2
            return str(a-b)
    except (InvalidOperation, ValueError):
        return ''


def export_html(report, destination, notify=lambda message: None, page_size=1000):
    summary = report_summary(report)
    counts = {'differences': summary['changed_cells'], 'left_only': summary['left_only'], 'right_only': summary['right_only']}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
        columns = [name for name,count,_,_ in column_stats(summary) if count]
        links, previews = {}, {}
        preview_bytes = 0
        with partition_columns(report, columns, destination.parent, notify) as paths:
            for number, column in enumerate(columns,1):
                pages=(summary['changed_cells_by_column'][column]+page_size-1)//page_size
                links[column]=f'column_{number:05d}_00001.html'
                with paths[column].open(encoding='utf-8') as source:
                    previews[column]=[]
                    for page in range(1,pages+1):
                        notify(f'Writing {column}, page {page} of {pages}')
                        with bundle.open(f'column_{number:05d}_{page:05d}.html','w',force_zip64=True) as output:
                            def write(text): output.write(text.encode('utf-8'))
                            nav='<nav><a href="index.html#differences">Summary</a>'
                            if page>1: nav+=f'<a href="column_{number:05d}_{page-1:05d}.html">Previous</a>'
                            if page<pages: nav+=f'<a href="column_{number:05d}_{page+1:05d}.html">Next</a>'
                            nav+=f'<span>Page {page} of {pages}</span></nav>'
                            write(html_start(column)+'<style>td:nth-last-child(2){background:#fff0ee;color:#9b3025}td:last-child{background:#eaf7ec;color:#256238}</style>'+nav+'<table>'+html_row(summary['keys']+source_labels(summary),True))
                            for line in itertools.islice(source,page_size):
                                row=json.loads(line)
                                if len(previews[column])<5 and preview_bytes<1024*1024:
                                    preview=[v[:500] for v in row]
                                    size=sum(len(v.encode('utf-8')) for v in preview)
                                    if preview_bytes+size<=1024*1024:
                                        previews[column].append(preview)
                                        preview_bytes+=size
                                write(html_row(row))
                            write('</table>'+nav+'</body></html>')
        index = make_summary_html(summary,links,previews).replace('</main>', '<section class="report-section"><h2>All result records</h2><p>Every result is included. Per-column previews show up to five rows, with values limited to 500 characters and a 1 MiB overall preview budget. Column pages below contain full values.</p>')
        index = index.replace('</div></body></html>', '')
        if (report / 'duplicate_keys.csv').exists():
            notify('Writing duplicate key audit')
            with (report / 'duplicate_keys.csv').open('rb') as source, bundle.open('duplicate_keys.csv','w',force_zip64=True) as target:
                while block := source.read(1024*1024):
                    notify('Writing duplicate key audit')
                    target.write(block)
            index += '<p><a href="duplicate_keys.csv">Download complete duplicate-key audit CSV</a></p>'
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
        bundle.writestr('index.html', index + '</section></main></div></body></html>')
