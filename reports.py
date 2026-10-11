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

REPORT_VERSION = 10

STYLE = '''body{font:15px system-ui,sans-serif;color:#004364;background:#f7f9fa;margin:40px auto;max-width:1200px;padding:0 24px}h1{font-size:32px}a{color:#007b99}table{border-collapse:collapse;width:100%;background:white;margin:20px 0}th,td{padding:12px;border:1px solid #dce5eb;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e6f6fa}nav{display:flex;gap:24px}p{line-height:1.6}'''


def summary_rows(summary):
    yield ['Metric', 'Value']
    for key, value in summary.items():
        if key not in ('changed_cells_by_column', 'ignore_key_containers','analysis_notes','review_revision'):
            yield [key.replace('_', ' ').capitalize(), json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value]
    for key,value in summary.get('review_revision',{}).items():yield ['Review: '+key.replace('_',' '),value]
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


def report_summary(report):
    summary=json.loads((report/'summary.json').read_text())
    notes=report/'annotations.json'
    summary['analysis_notes']=json.loads(notes.read_text()) if notes.exists() else []
    return summary


def comment_lookup(summary):
    return {(n['column'],tuple(n['key']) if n['key'] is not None else None):n['status']+': '+n['comment'] for n in summary.get('analysis_notes',[])}


def applicable_comments(notes,column,key):
    key=tuple(key)
    return '\n'.join(notes[scope] for scope in ((column,None),('',key),(column,key)) if scope in notes)


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


def match_bands(summary):
    """Classify by exact counts, so a rounded 100% never enters the perfect band."""
    bands = [['100% match', 0, '#007b99'], ['99% to <100%', 0, '#69cbd8'],
             ['95% to <99%', 0, '#f0c82e'], ['Below 95%', 0, '#ce6256']]
    matched = summary.get('matched_keys', 0)
    if matched:
        for count in summary['changed_cells_by_column'].values():
            index = 0 if count == 0 else 1 if count * 100 <= matched else 2 if count * 100 <= matched * 5 else 3
            bands[index][1] += 1
    return bands


LEADERSHIP_STYLE = '''
body{max-width:1240px;margin:0 auto;background:#f4f7f9;padding:36px;color:#004364;font:14px system-ui,sans-serif}
body>header{display:flex;align-items:center;justify-content:space-between;background:white;padding:22px!important;margin-bottom:24px!important;border-radius:12px}body>h1{display:none}
.hero{background:#004364;color:white;border-radius:12px;padding:30px;border-bottom:5px solid #fcd800;margin-bottom:22px}.hero h1{font-size:34px;margin:8px 0}.hero p{color:#d6edf4}.eyebrow{text-transform:uppercase;letter-spacing:2px;font-size:11px;font-weight:700}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:24px 0}.card,section{background:white;border:1px solid #dce5eb;border-radius:12px;padding:22px;margin-bottom:22px}.card{margin:0;border-top:4px solid #00a6ca}.card strong{display:block;font-size:30px;margin:10px 0}.card small,.muted{color:#59717d}
h2{font-size:21px;margin:0 0 15px}h3{font-size:16px}.overview{display:grid;grid-template-columns:1fr 1fr;gap:24px}.chart-wrap{display:flex;align-items:center;gap:26px;flex-wrap:wrap}.pie{width:220px;max-width:100%;height:auto}.legend{list-style:none;padding:0;flex:1}.legend li{padding:10px 0;border-bottom:1px solid #e5edf0;display:flex;gap:10px;align-items:center}.swatch{width:12px;height:12px;border-radius:50%;flex-shrink:0}.legend strong{margin-left:auto}.source{overflow-wrap:anywhere;padding:10px 0}.source strong{display:block}.callout{background:#fff9df;border-left:5px solid #e5bd25}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:#e9f6f7;border:1px solid #c7e8eb;padding:7px 10px;border-radius:6px;color:#006779}.table-wrap{overflow:auto}table{margin:12px 0;font-size:13px}th{background:#004364;color:white;white-space:normal}td{border-width:0 0 1px;padding:11px}tr:nth-child(even){background:#f6fafb}.count{font-variant-numeric:tabular-nums}.container{border:1px solid #e5d79c;background:#fffdf3;border-radius:9px;padding:18px;margin:12px 0}.container summary{cursor:pointer;font-weight:650}input{font:inherit;border:1px solid #afc8d2;border-radius:6px;padding:10px;width:300px;max-width:100%}.toolbar{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.print{background:#007b99;color:white;border:0;border-radius:6px;padding:10px 18px;cursor:pointer}footer{padding:12px 0 30px;font-size:12px;color:#59717d}.rate-note{font-size:12px}.mismatch-sample{border:1px solid #dce5eb;border-radius:8px;margin:10px 0;padding:14px}.sample-dropdown{width:min(650px,70vw);min-width:260px}.mismatch-sample summary{cursor:pointer;font-weight:650;overflow-wrap:anywhere}.sample-before{background:#fff0ed}.sample-after{background:#eaf7f3}.badge{font-size:12px;border-radius:5px;padding:5px 9px;background:#e9f6f7;display:inline-block}
@media(max-width:850px){body{padding:16px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.overview{grid-template-columns:1fr}.hero h1{font-size:28px}}
.report-tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.report-tabs button{font:inherit;font-size:12px;border:1px solid #b6ced8;background:white;color:#004364;border-radius:7px;padding:10px 14px;cursor:pointer}.report-tabs button[aria-pressed=true]{background:#004364;color:white}.report-tabs button:focus-visible{outline:3px solid #00a6ca;outline-offset:2px}
@media screen{body.compact-report{box-sizing:border-box;max-width:none;height:100dvh;padding:18px 24px;display:flex;flex-direction:column;overflow:hidden}.compact-report>header{padding:10px 16px!important;margin-bottom:10px!important;flex-shrink:0}.compact-report main{display:flex;flex-direction:column;flex:1;min-height:0}.compact-report .hero{padding:14px 20px;margin:0}.compact-report .hero h1{font-size:25px;margin:4px 0}.compact-report .hero p{margin:4px 0}.compact-report .cards{gap:10px;margin:12px 0}.compact-report .card{padding:12px 16px}.compact-report .card strong{font-size:25px;margin:5px 0}.compact-report .report-panels{min-height:0;flex:1;overflow:auto;overscroll-behavior:contain}.compact-report .report-inactive{display:none!important}.compact-report section{padding:18px;margin-bottom:0}.compact-report footer{font-size:10px;padding:8px 0 0}.compact-report .overview{gap:14px}.compact-report .pie{width:170px}.compact-report .legend li{padding:6px 0}.compact-report th{position:sticky;top:0}.compact-report .table-wrap{max-width:100%}}
@media screen and (max-width:700px){body.compact-report{padding:12px;min-height:100dvh;height:auto;overflow:auto}.compact-report .report-panels{max-height:65dvh;flex:auto}.compact-report .hero h1{font-size:22px}.compact-report .report-tabs button{padding:8px}.compact-report .card{padding:10px}}
@media print{.report-tabs{display:none}.report-panels{overflow:visible!important}.report-inactive{display:block!important}}
@media print{body{background:white;padding:0;font-size:11px}.print,input,.search-label{display:none!important}.hero,th,.swatch,.chip{-webkit-print-color-adjust:exact;print-color-adjust:exact}section{break-inside:auto}.card,.container,.hero{break-inside:avoid}.cards{gap:8px}.card{padding:12px}.card strong{font-size:23px}.table-wrap{overflow:visible}tr{break-inside:avoid}thead{display:table-header-group}[hidden]{display:table-row!important}a{color:inherit;text-decoration:none}}
'''


def make_summary_html(summary, samples=None):
    samples = samples or {}
    row_notes=comment_lookup(summary)
    import math
    from datetime import datetime, timezone
    esc = lambda value: html.escape(str(value))
    stats = column_stats(summary)
    matched = summary.get('matched_keys', 0)
    changed = [row for row in stats if row[1]]
    equal = [row for row in stats if not row[1]] if matched else []
    total = len(stats)
    overall = 100 * (1-summary['changed_cells']/(matched*total)) if matched and total else None
    generated = datetime.now(timezone.utc).strftime('%d %b %Y · %H:%M UTC')
    out = html_start('TransUnion | Data comparison report') + '<style>'+LEADERSHIP_STYLE+'</style>'
    out += '<main><div class="hero"><div class="eyebrow">TransUnion · Data quality &amp; comparison</div><h1>Data comparison report</h1><p>Leadership summary · '+generated+'</p><span class="badge">'+('Differences identified' if changed or summary.get('left_only') or summary.get('right_only') else 'No differences within selected scope' if matched and total else 'Insufficient comparable data')+'</span></div>'
    out += '<div class="toolbar"><p>Complete column coverage. Match rates reflect the configured comparison scope.</p><button class="print" onclick="window.print()">Print / save as PDF</button></div><div class="cards">'
    for title, value, note in [('Overall cell match',percent(overall),'Across matched keys and compared columns'),('Fully matching columns',f'{len(equal):,} / {total:,}','Exactly zero mismatched cells'),('Columns with differences',f'{len(changed):,}','All listed below'),('Mismatched cells',f'{summary["changed_cells"]:,}','After configured exclusions and rules')]:
        out += '<div class="card"><small>'+title+'</small><strong>'+value+'</strong><small>'+note+'</small></div>'
    out += '</div><div class="overview"><section><h2>Column match distribution</h2><p class="muted">Number of columns in each match-rate band.</p><div class="chart-wrap">'
    bands = match_bands(summary)
    if matched and total:
        out += '<svg class="pie" viewBox="0 0 240 240" role="img" aria-label="Column match-rate distribution"><title>'+esc('; '.join(f'{name}: {count} columns' for name,count,_ in bands))+'</title>'
        start = -math.pi/2
        for name,count,color in bands:
            if not count: continue
            if count == total:
                out += f'<circle cx="120" cy="120" r="110" fill="{color}" />'
            else:
                end = start + 2*math.pi*count/total
                x1,y1=120+110*math.cos(start),120+110*math.sin(start)
                x2,y2=120+110*math.cos(end),120+110*math.sin(end)
                out += f'<path d="M120 120 L{x1:.4f} {y1:.4f} A110 110 0 {int(count>total/2)} 1 {x2:.4f} {y2:.4f} Z" fill="{color}" stroke="white" stroke-width="2"><title>{esc(name)}: {count}</title></path>'
                start=end
        out += '</svg><ul class="legend">'
        for name,count,color in bands:
            out += f'<li><span class="swatch" style="background:{color}"></span>{esc(name)}<strong>{count:,}</strong></li>'
        out += '</ul>'
    else:
        out += '<p>No matched rows; match rates cannot be calculated.</p>' if not matched else '<p>No non-key columns in comparison scope.</p>'
    out += '</div><p class="rate-note">100% means zero mismatches. Other bands use exact counts before display rounding; bands do not overlap.</p></section><section><h2>Comparison coverage</h2>'
    for label,source in zip(('File 1','File 2'),source_labels(summary)):
        out += '<div class="source"><strong>'+label+'</strong>'+esc(source)+'</div>'
    for title,value in [('Matched keys',matched),('Keys only in File 1',summary.get('left_only',0)),('Keys only in File 2',summary.get('right_only',0)),('Source rows · File 1',summary.get('left_rows','N/A')),('Source rows · File 2',summary.get('right_rows','N/A'))]:
        out += '<p>'+title+': <strong>'+esc(f'{value:,}' if isinstance(value,int) else value)+'</strong></p>'
    out += '<p>Key columns: <strong>'+esc(', '.join(summary['keys']))+'</strong></p></section></div>'
    if summary.get('review_revision'):
        out += '<section class="callout"><h2>Review revision</h2><p>'+esc(' · '.join(k.replace('_',' ')+': '+str(v) for k,v in summary['review_revision'].items()))+'</p><p>Re-evaluated retained evidence; original comparison preserved.</p></section>'
    if summary.get('left_duplicate_keys') or summary.get('right_duplicate_keys'):
        out += '<section class="callout"><h2>Warning: duplicate keys</h2><p>'+esc(f"Kept the {summary.get('duplicate_policy','first')} source occurrence per key. File 1: {summary.get('left_duplicate_keys',0):,} duplicate keys / {summary.get('left_duplicate_rows_skipped',0):,} skipped rows. File 2: {summary.get('right_duplicate_keys',0):,} duplicate keys / {summary.get('right_duplicate_rows_skipped',0):,} skipped rows.")+'</p><p>Skipped duplicates are outside the match-rate denominator. The detailed duplicate audit remains available in the app and Excel workbook.</p></section>'
    notes={n['column']:n['status']+': '+n['comment'] for n in summary.get('analysis_notes',[]) if n['key'] is None}
    out += '<section id="statistics"><div class="toolbar"><h2>All mismatching columns ('+str(len(changed))+')</h2><label class="search-label">Find a column <input id="columnSearch" type="search" placeholder="Column name"></label></div><p class="muted">Largest difference counts first. Percentages use '+f'{matched:,}'+' matched keys.</p><div class="table-wrap"><table id="columnStats"><thead>'+html_row(['Column','Mismatched cells','Matching cells','Mismatch %','Match %','Review notes','Samples'],True)+'</thead><tbody>'
    for index,(name,count,mismatch,match) in enumerate(changed):
        row = html_row([name,f'{count:,}',f'{max(0,matched-count):,}',percent(mismatch),percent(match),notes.get(name,'')]).rstrip()
        out += row[:-5]+'<td>'
        rows=samples.get(name,[])
        if rows:
            out += f'<details class="mismatch-sample" id="sample-{index}"><summary>View {len(rows)} keys</summary><div class="sample-dropdown"><p>'+esc(name)+f' · {len(rows)} sample keys of {count:,} mismatches. First keys in comparison order; values over 1,000 characters are marked as truncated.</p><div class="table-wrap"><table><thead>'+html_row(summary['keys']+['File 1 value','File 2 value']+(['Comments'] if row_notes else []),True)+'</thead><tbody>'
            for values in rows:
                out += '<tr>'+''.join('<td>'+esc(value)+'</td>' for value in values[:-2])+'<td class="sample-before">'+esc(values[-2])+'</td><td class="sample-after">'+esc(values[-1])+'</td>'+('<td>'+esc(applicable_comments(row_notes,name,values[:-2]))+'</td>' if row_notes else '')+'</tr>'
            out += '</tbody></table></div></div></details>'
        else:out += 'Generate HTML report to include samples'
        out += '</td></tr>'
    out += '</tbody></table></div>'
    if not changed: out += '<p>No mismatching columns in the selected scope.</p>'
    out += '</section><section id="matching"><h2>Attributes with 100% match ('+str(len(equal))+')</h2><p>Each listed column has '+f'{matched:,}'+' matched cells and zero mismatches.</p><div class="chips">'+''.join('<span class="chip">'+esc(row[0])+'</span>' for row in equal)+'</div></section>'
    out += '<section id="scope" class="callout"><h2>Ignored key containers — excluded from comparison</h2><p>File 1 excluded rows: <strong>'+f"{summary.get('left_excluded_rows',0):,}"+'</strong> · File 2 excluded rows: <strong>'+f"{summary.get('right_excluded_rows',0):,}"+'</strong></p><p>Configured keys can overlap across containers or be absent from source files. Container counts are not additive; excluded-row counts reflect actual input rows.</p>'
    for item in summary.get('ignore_key_containers',[]):
        out += '<div class="container"><h3>'+esc(item['name'])+'</h3><p>'+esc(item['reason'])+'</p><details><summary>'+f'{len(item["keys"]):,}'+' configured keys · view details</summary><div class="table-wrap"><table><thead>'+html_row(summary['keys'],True)+'</thead><tbody>'+''.join(html_row(key) for key in item['keys'])+'</tbody></table></div></details></div>'
    if not summary.get('ignore_key_containers'): out += '<p>No ignored key containers configured.</p>'
    out += '</section><section><h2>Scope &amp; interpretation</h2><p>Match rate = matching cells ÷ compared cells for retained keys present in both files. One-sided keys, ignored rows, skipped duplicates, ignored columns and non-common headers are excluded. A perfect column match does not imply identical source files.</p><p>Ignored columns: '+esc(', '.join(summary.get('ignored_columns',[])) or 'None')+'</p>'
    for side,label in [('left','File 1'),('right','File 2')]:
        out += '<p>Non-common columns · '+label+': '+esc(', '.join(summary.get('unmatched_columns',{}).get(side,[])) or 'None')+'</p>'
    out += '<p>Comparison: '+esc(summary.get('comparison','exact text'))+'. Accepted override cells: '+esc(summary.get('override_equivalent_cells',0))+'. Accepted rule cells: '+esc(summary.get('rule_equivalent_cells',0))+'.</p><p>This single-file report includes every compared column and every configured ignored key. Individual cell mismatches and one-sided key records remain available in the Excel/CSV downloads and Analysis view.</p></section>'
    if summary.get('analysis_notes'):
        out += '<section><h2>Analysis classifications &amp; comments</h2><div class="table-wrap"><table>'+''.join(html_row(row,i==0) for i,row in enumerate(note_rows(summary)))+'</table></div></section>'
    out += '''<footer>TransUnion · Data comparison report · Generated by the comparison tool</footer></main><script>
const search=document.getElementById('columnSearch');search.addEventListener('input',()=>{document.querySelectorAll('#columnStats > tbody > tr').forEach(r=>r.hidden=!r.cells[0].textContent.toLowerCase().includes(search.value.toLowerCase()));});
const main=document.querySelector('main'),panels=[...main.children].filter(e=>e.matches('.overview,section'));
const tabs=document.createElement('nav');tabs.className='report-tabs';tabs.setAttribute('aria-label','Report sections');
const area=document.createElement('div');area.className='report-panels';
main.insertBefore(tabs,panels[0]);main.insertBefore(area,panels[0]);
const buttons=panels.map((panel,i)=>{
  const button=document.createElement('button');button.type='button';
  button.textContent=panel.classList.contains('overview')?'Overview':panel.id==='statistics'?'Mismatching columns':panel.id==='matching'?'Matching columns':panel.id==='scope'?'Ignored keys & containers':panel.querySelector('h2').textContent;
  if(!panel.id)panel.id='report-panel-'+i;button.setAttribute('aria-controls',panel.id);
  button.addEventListener('click',()=>selectPanel(i));tabs.append(button);area.append(panel);return button;
});
function selectPanel(index){panels.forEach((panel,i)=>{panel.classList.toggle('report-inactive',i!==index);buttons[i].setAttribute('aria-pressed',String(i===index));});area.scrollTop=0;}
document.body.classList.add('compact-report');selectPanel(0);
let opened=[];window.addEventListener('beforeprint',()=>{opened=[...document.querySelectorAll('.container details:not([open])')];opened.forEach(d=>d.open=true);});window.addEventListener('afterprint',()=>opened.forEach(d=>d.open=false));
</script></body></html>'''
    return out


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
        raise ValueError('A value exceeds Excel’s 32,767-character cell limit. Download the CSV results to preserve the full value.')
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
                        raise ValueError('A column exceeds Excel worksheet capacity. Download CSV results instead.')
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
    row_notes=comment_lookup(summary)
    stats = column_stats(summary)
    columns = [name for name,count,_,_ in stats if count]
    if any(summary['changed_cells_by_column'][name] > 1048574 for name in columns):
        raise ValueError('One column has more than 1,048,574 mismatches and cannot fit in one Excel sheet. Download CSV instead.')
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
            yield summary['keys']+['valueA','valueB','variable','typeA','typeB','diffAB']+(['Comments'] if row_notes else [])+[Link('Back to TOC','TOC')]
            with paths[column].open(encoding='utf-8') as source:
                for line in source:
                    values=json.loads(line)
                    yield values+[column,'text','text',numeric_difference(values[-2],values[-1])]+([applicable_comments(row_notes,column,values[:-2])] if row_notes else [])
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


def mismatch_samples(report, summary, notify=lambda message: None):
    """Single streaming pass; stop when every changed column has its sample quota."""
    pending = {name: min(20, count) for name, count in summary['changed_cells_by_column'].items() if count}
    samples = {name: [] for name in pending}
    if not pending: return samples
    csv.field_size_limit(64 * 1024 * 1024)
    def short(value):
        return value if len(value) <= 1000 else value[:1000] + ' … [truncated]'
    notify('Collecting up to 20 mismatch keys per column…')
    with (report / 'differences.csv').open(encoding='utf-8', newline='') as source:
        for index, row in enumerate(csv.DictReader(source), 1):
            name = row['column']
            if name in pending:
                samples[name].append(json.loads(row['key_json']) + [short(row['left_value']), short(row['right_value'])])
                if len(samples[name]) >= pending[name]: del pending[name]
            if index % 10000 == 0:
                notify(f'Collecting mismatch samples: {index:,} records scanned; {len(pending):,} columns remaining')
            if not pending: break
    if pending:
        raise ValueError('Mismatch data is incomplete; unable to collect the expected column samples')
    return samples


def export_html(report, destination, notify=lambda message: None):
    """One self-contained leadership report with bounded per-column samples."""
    notify('Preparing leadership summary and column match distribution…')
    summary = report_summary(report)
    samples = mismatch_samples(report, summary, notify)
    notify('Rendering all columns, mismatch samples and ignored-key containers…')
    document = make_summary_html(summary, samples)
    notify('Writing single-file HTML report…')
    with destination.open('w', encoding='utf-8') as output:
        for offset in range(0, len(document), 1024*1024):
            notify('Writing single-file HTML report…')
            output.write(document[offset:offset+1024*1024])
