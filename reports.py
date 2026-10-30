"""Streaming report exporters used by the application; no third-party dependencies."""
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
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

REPORT_VERSION = 15
# Report times are India Standard Time (UTC+5:30), labelled IST.
from datetime import timedelta as _timedelta, timezone as _timezone
IST = _timezone(_timedelta(hours=5, minutes=30), 'IST')

def summary_rows(summary):
    yield ['Metric', 'Value']
    for key, value in summary.items():
        if key not in ('changed_cells_by_column', 'ignore_key_containers','analysis_notes','review_revision','skipped_records_preview'):
            yield [key.replace('_', ' ').capitalize(), json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value]
    for key,value in summary.get('review_revision',{}).items():yield ['Review: '+key.replace('_',' '),value]
    yield ['Column', 'Changed cells']
    for name, count in summary['changed_cells_by_column'].items():
        yield [name, count]


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
    if summary.get('malformed_records') and (report/'skipped_keys.csv').exists():
        with (report/'skipped_keys.csv').open(encoding='utf-8', newline='') as stream:
            summary['skipped_records_preview'] = list(itertools.islice(csv.DictReader(stream), 20))
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


REPORT_STYLE = '''
:root{--ink:#002b40;--ink-2:#3d5566;--muted:#5f7682;--line:#d9e3e9;--line-soft:#eaf0f3;--canvas:#eef3f6;--paper:#fff;--cyan:#00a6ca;--link:#007b99;--yellow:#fcd800;--yellow-soft:#fff8d1;
--band-perfect:#c4dfe7;--band-low:#00a6ca;--band-medium:#e9b824;--band-high:#ce6256;
--key-equal:#007b99;--key-changed:#d7922b;--key-left:#004364;--key-right:#69cbd8;
--s1:#0089ab;--s2:#eb6834;--s3:#1baf7a;--s4:#6250d6;--s5:#e87ba4;--s6:#c98500;--s-other:#a8bac4;
--sans:"Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,"Helvetica Neue",Arial,sans-serif}
*{box-sizing:border-box}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
html{scroll-behavior:smooth;scroll-padding-top:64px}
body{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.55 var(--sans);font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
a{color:var(--link)}
:focus-visible{outline:3px solid var(--cyan);outline-offset:2px}
.page{max-width:1160px;margin:0 auto;padding:0 32px 48px}
.masthead{background:var(--paper);border-bottom:4px solid var(--yellow)}
.masthead-inner{max-width:1160px;margin:0 auto;padding:22px 32px 26px;display:grid;grid-template-columns:auto 1fr auto;gap:8px 28px;align-items:center}
.masthead img{display:block;width:150px;max-width:100%;height:auto}
.masthead h1{margin:0;font-size:22px;line-height:1.2;font-weight:600;padding-left:28px;border-left:1px solid var(--line)}
.masthead time{display:block;font-size:13px;color:var(--muted);font-weight:400;margin-top:2px}
.print{font:inherit;font-size:14px;font-weight:600;background:var(--ink);color:#fff;border:0;border-radius:6px;padding:10px 16px;cursor:pointer;white-space:nowrap}
.print:hover{background:#004364}
.files{grid-column:1/-1;margin:14px 0 0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px 24px;align-items:baseline;font-size:14px;color:var(--ink-2)}
.files .file-source{grid-column:1;display:grid;grid-template-columns:44px minmax(0,1fr);gap:10px;align-items:baseline}
.files .file-source:first-child{grid-row:1}
.files strong{color:var(--ink);font-weight:600;overflow-wrap:anywhere}
.files .keys{grid-column:2;grid-row:1;justify-self:end;color:var(--muted);overflow-wrap:anywhere}
.files .keys strong{color:var(--ink)}
.toc{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--canvas) 92%,transparent);backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.toc ol{max-width:1160px;margin:0 auto;padding:0 32px;list-style:none;display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.toc a{display:block;padding:13px 12px 11px;font-size:13.5px;font-weight:600;color:var(--muted);text-decoration:none;border-bottom:3px solid transparent;white-space:nowrap}
.toc a:hover{color:var(--ink)}
.toc a[aria-current=true]{color:var(--ink);border-bottom-color:var(--cyan)}
.toc .count{font-weight:400;margin-left:4px}
.block{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:28px 32px;margin-top:20px}
.block>h2,.split h2{margin:0 0 6px;font-size:19px;line-height:1.3;font-weight:600}
.lede{margin:0 0 20px;color:var(--muted);font-size:14px;max-width:72ch}
.verdict{display:grid;grid-template-columns:minmax(0,auto) minmax(0,1fr);gap:8px 48px;align-items:end;padding-bottom:26px;margin-bottom:24px;border-bottom:1px solid var(--line-soft)}
.rate{margin:0;font-size:60px;line-height:1;font-weight:300;letter-spacing:-.02em;white-space:nowrap}
.rate small{display:block;white-space:normal;font-size:14px;letter-spacing:0;font-weight:400;color:var(--muted);margin-top:10px}
.verdict-text{margin:0;font-size:17px;line-height:1.5;max-width:60ch;color:var(--ink-2)}
.verdict-text strong{color:var(--ink);font-weight:600}
.state{display:inline-flex;align-items:center;gap:8px;margin-bottom:10px;font-size:13px;font-weight:600;color:var(--ink)}
.state::before{content:"";width:10px;height:10px;border-radius:50%;background:var(--band-high)}
.state.is-clean::before{background:var(--key-equal)}
.state.is-empty::before{background:var(--s-other)}
.distribution{display:grid;grid-template-columns:120px minmax(0,1fr);gap:24px;align-items:center}
.distribution .donut{width:120px;margin:0}
.distribution h3{margin:0 0 12px;font-size:15px;font-weight:600}
.distribution .bands{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 24px}
.distribution .bands b{margin-left:auto}
@media(max-width:520px){.distribution{grid-template-columns:88px minmax(0,1fr);gap:16px}.distribution .donut{width:88px}.distribution .bands{grid-template-columns:1fr}}
.b0{fill:var(--band-perfect)}.b1{fill:var(--band-low)}.b2{fill:var(--band-medium)}.b3{fill:var(--band-high)}
.bands{display:flex;flex-wrap:wrap;gap:6px 22px;margin:0;padding:0;list-style:none;font-size:13px;color:var(--ink-2)}
.bands li{display:flex;align-items:center;gap:7px}
.bands b{color:var(--ink);font-weight:600}
.dot{width:10px;height:10px;border-radius:3px;flex:none;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.dot.b0{background:var(--band-perfect)}.dot.b1{background:var(--band-low)}.dot.b2{background:var(--band-medium)}.dot.b3{background:var(--band-high)}
.rate-note{margin:10px 0 0;font-size:12px;color:var(--muted)}
.notice{margin-top:20px;padding:16px 20px;border-radius:8px;background:var(--yellow-soft);border-left:4px solid var(--yellow)}
.notice h2{margin:0 0 4px;font-size:15px}.notice p{margin:4px 0;font-size:14px;color:var(--ink-2)}
.keybar{display:flex;gap:2px;height:14px;margin:4px 0 18px;border-radius:4px;overflow:hidden;background:var(--line-soft)}
.keybar span{min-width:3px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.k-equal{background:var(--key-equal)}.k-changed{background:var(--key-changed)}.k-left{background:var(--key-left)}.k-right{background:var(--key-right)}
.figures{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin:0}
.figures>div{padding:2px 20px 2px 0}
.figures>div+div{padding-left:20px;border-left:1px solid var(--line-soft)}
.figures dt{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.figures dt .dot{border-radius:2px}
.figures dd{margin:6px 0 0;font-size:26px;font-weight:600;line-height:1.1}
.figures dd small{display:block;font-size:12px;font-weight:400;color:var(--muted);margin-top:4px}
.sources{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:22px 0 0;padding-top:18px;border-top:1px solid var(--line-soft);font-size:13.5px;color:var(--ink-2)}
.sources p{margin:0}.sources strong{color:var(--ink);overflow-wrap:anywhere}
.split{display:grid;grid-template-columns:240px minmax(0,1fr);gap:32px;align-items:center;padding-bottom:24px;margin-bottom:8px;border-bottom:1px solid var(--line-soft)}
.donut{width:100%;max-width:240px;height:auto;display:block}
.donut path,.donut circle.slice{stroke:#fff;stroke-width:1.5}
.donut .total{font-size:24px;font-weight:600;fill:var(--ink)}
.donut .total-label{font-size:10.5px;fill:var(--muted)}
.s1{fill:var(--s1)}.s2{fill:var(--s2)}.s3{fill:var(--s3)}.s4{fill:var(--s4)}.s5{fill:var(--s5)}.s6{fill:var(--s6)}.s-other{fill:var(--s-other)}
.dot.s1{background:var(--s1)}.dot.s2{background:var(--s2)}.dot.s3{background:var(--s3)}.dot.s4{background:var(--s4)}.dot.s5{background:var(--s5)}.dot.s6{background:var(--s6)}.dot.s-other{background:var(--s-other)}
.slices{margin:12px 0 0;padding:0;list-style:none;max-width:520px}
.slices li{display:grid;grid-template-columns:10px minmax(0,1fr) auto 64px;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line-soft);font-size:14px}
.slices li:last-child{border-bottom:0}
.slices .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.slices b{font-weight:600;text-align:right}
.slices small{font-size:13px;color:var(--muted);text-align:right}
.toolbar{display:flex;justify-content:space-between;gap:16px;align-items:end;flex-wrap:wrap;margin:20px 0 4px}
.toolbar h3{margin:0;font-size:15px;font-weight:600}
.toolbar p{margin:2px 0 0;font-size:13px;color:var(--muted)}
.search-label{display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:var(--muted)}
input[type=search]{font:inherit;font-size:14px;color:var(--ink);border:1px solid #b8cbd5;border-radius:6px;padding:8px 12px;width:260px;max-width:100%;background:#fff}
.table-wrap{overflow:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:10px 12px;text-align:left;vertical-align:top;overflow-wrap:anywhere;white-space:pre-wrap}
thead th{position:sticky;top:0;background:#f3f7f9;color:var(--ink-2);font-size:12.5px;font-weight:600;border-bottom:1px solid var(--line);white-space:normal}
tbody td{border-bottom:1px solid var(--line-soft)}
td.num,th.num{text-align:right;white-space:nowrap}
#columnStats{min-width:760px}
#columnStats>tbody>tr{cursor:pointer;transition:background-color .15s}
#columnStats>tbody>tr:hover{background:#f5fafc}
#columnStats>tbody>tr>td:first-child{font-weight:600;color:var(--ink)}
#columnStats>tbody>tr.flash{animation:flash 1.6s ease-out}
@keyframes flash{from{background:#fff3a6}to{background:transparent}}
.share{display:inline-block;width:72px;height:6px;margin-left:10px;border-radius:3px;background:var(--line-soft);vertical-align:middle;overflow:hidden}
.share i{display:block;height:100%;width:0;background:var(--band-high);border-radius:3px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.share.b1 i{background:var(--band-low)}.share.b2 i{background:var(--band-medium)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.chip{font-size:13px;padding:4px 10px;border-radius:999px;background:#eef6f8;border:1px solid #d3e7ec;color:var(--ink-2)}
.empty{margin:12px 0 0;color:var(--muted)}
.callout{border-left:4px solid var(--yellow)}
.container{border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:12px 0}
.container h3{margin:0;font-size:15px}.container p{margin:4px 0 8px;color:var(--ink-2)}
.container summary{cursor:pointer;color:var(--link);font-weight:600;font-size:14px}
.defs{display:grid;grid-template-columns:minmax(160px,220px) minmax(0,1fr);gap:0;margin:16px 0 0;font-size:14px}
.defs dt,.defs dd{padding:10px 0;border-top:1px solid var(--line-soft);margin:0}
.defs dt{color:var(--muted);padding-right:16px}
.defs dd{overflow-wrap:anywhere;color:var(--ink)}
.scope-text p{max-width:78ch;color:var(--ink-2)}
footer{max-width:1160px;margin:0 auto;padding:20px 32px 40px;font-size:12.5px;color:var(--muted)}
@media (max-width:900px){.masthead img{width:120px}.print{font-size:13px;padding:8px 12px}.page,.masthead-inner,.toc ol,footer{padding-left:16px;padding-right:16px}.masthead-inner{grid-template-columns:auto 1fr}.masthead h1{grid-column:1/-1;border-left:0;padding-left:0}.print{grid-row:1;grid-column:2;justify-self:end}.block{padding:22px 18px}.verdict{grid-template-columns:1fr;gap:16px}.rate{font-size:48px}.figures{grid-template-columns:1fr 1fr;row-gap:18px}.figures>div:nth-child(3){padding-left:0;border-left:0}.split{grid-template-columns:1fr}.donut{max-width:200px;margin:0 auto}.sources{grid-template-columns:1fr}.defs{grid-template-columns:1fr}.defs dd{border-top:0;padding-top:0}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}#columnStats>tbody>tr.flash{animation:none}}
@media print{body{background:#fff;font-size:11.5px}.toc,.print,.search-label,.report-drawer{display:none!important}.masthead{border-bottom-width:3px}.block{border:0;border-top:1px solid var(--line);border-radius:0;padding:16px 0;margin:0;break-inside:auto}.verdict,.split,.notice,.container,tr{break-inside:avoid}.table-wrap{overflow:visible}thead{display:table-header-group}[hidden]{display:table-row!important}.chip[hidden]{display:inline-block!important}.donut,.keybar{-webkit-print-color-adjust:exact;print-color-adjust:exact}a{color:inherit;text-decoration:none}#columnStats{min-width:0}}
'''

# Column slices use a fixed, colour-blind-checked order; the same one the results page pie uses.
SLICE_LIMIT = 6


def html_start(title):
    assets = Path(__file__).parent / 'web/assets'
    logo = base64.b64encode((assets / 'transunion-logo.svg').read_bytes()).decode('ascii')
    # The same tab icon as the application, embedded so the single-file report keeps it offline.
    icon = base64.b64encode((assets / 'tu-favicon.svg').read_bytes()).decode('ascii')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
            + html.escape(title) + f'</title><link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{icon}"><style>' + REPORT_STYLE + '</style></head><body>'
            + f'<header class="masthead"><div class="masthead-inner"><img src="data:image/svg+xml;base64,{logo}" alt="TransUnion" width="201" height="53">')


def band_index(count, matched):
    return 0 if count == 0 else 1 if count * 100 <= matched else 2 if count * 100 <= matched * 5 else 3


def column_slices(summary):
    ranked = sorted(((name, count) for name, count in summary['changed_cells_by_column'].items() if count), key=lambda item: (-item[1], item[0]))
    slices = [(name, count, f's{index + 1}') for index, (name, count) in enumerate(ranked[:SLICE_LIMIT])]
    rest = ranked[SLICE_LIMIT:]
    if rest:
        slices.append((f'Other columns ({len(rest):,})', sum(count for _, count in rest), 's-other'))
    return slices


def donut_svg(slices, total, esc, label="mismatched cells", description="Mismatched cells by column"):
    import math
    out = '<svg class="donut" viewBox="0 0 200 200" role="img" aria-label="' + esc(description + ': ' + '; '.join(f'{name}: {count:,}' for name, count, _ in slices)) + '">'
    start = -math.pi / 2
    for name, count, colour in slices:
        tip = f'<title>{esc(name)}: {count:,} {esc(label)} ({100 * count / total:.2f}%)</title>'
        if count == total:
            out += f'<circle class="slice {colour}" cx="100" cy="100" r="96">{tip}</circle>'
            continue
        end = start + 2 * math.pi * count / total
        large = int(count > total / 2)
        point = lambda angle, radius: f'{100 + radius * math.cos(angle):.3f} {100 + radius * math.sin(angle):.3f}'
        out += (f'<path class="{colour}" d="M{point(start, 96)} A96 96 0 {large} 1 {point(end, 96)} '
                f'L{point(end, 60)} A60 60 0 {large} 0 {point(start, 60)} Z">{tip}</path>')
        start = end
    if slices and slices[0][1] == total:
        out += '<circle cx="100" cy="100" r="60" fill="#fff"/>'
    out += f'<text class="total" x="100" y="102" text-anchor="middle">{total:,}</text><text class="total-label" x="100" y="122" text-anchor="middle">{esc(label)}</text></svg>'
    return out


def make_summary_html(summary):
    from datetime import datetime, timezone
    esc = lambda value: html.escape(str(value))
    stats = column_stats(summary)
    matched = summary.get('matched_keys', 0)
    changed = [row for row in stats if row[1]]
    equal = [row for row in stats if not row[1]] if matched else []
    total = len(stats)
    cells = summary['changed_cells']
    left_only, right_only = summary.get('left_only', 0), summary.get('right_only', 0)
    overall = 100 * (1 - cells / (matched * total)) if matched and total else None
    generated = datetime.now(IST)
    files = source_labels(summary)

    out = html_start('TransUnion | Data comparison report')
    out += ('<h1>Data comparison report<time datetime="' + generated.isoformat(timespec='seconds') + '">Generated '
            + generated.strftime('%d %b %Y, %H:%M IST') + '</time></h1><button class="print" type="button" onclick="window.print()">Print or save as PDF</button>'
            + '<div class="files">' + ''.join(f'<div class="file-source"><span>{label}</span><strong>{esc(name)}</strong></div>'
                                       for label, name in zip(('File 1', 'File 2'), files)) + '<div class="keys">Matched on <strong>' + esc(', '.join(summary['keys'])) + '</strong>'
            + (f'<span> · random sample of <strong>{summary["sample"]["size"]:,}</strong> keys from File 1 (seed {summary["sample"]["seed"]})</span>' if summary.get('sample') else '')
            + '</div></div></div></header>')
    sections = [('summary', 'Summary', None), ('statistics', 'Mismatching columns', len(changed)), ('keys', 'Keys', None), ('matching', 'Matching columns', len(equal)), ('scope', 'Exclusions', None), ('interpretation', 'Scope', None)]
    if summary.get('malformed_records'):
        sections.insert(1, ('skipped-records', 'Skipped keys', summary.get('skipped_keys', 0)))
    if summary.get('analysis_notes'):
        sections.append(('comments', 'Review comments', len(summary['analysis_notes'])))
    out += '<nav class="toc" aria-label="Report sections"><ol>' + ''.join(
        f'<li><a href="#{anchor}">{label}' + (f'<span class="count">{count:,}</span>' if count is not None else '') + '</a></li>' for anchor, label, count in sections) + '</ol></nav>'
    out += '<main class="page">'

    # Summary: the overall answer and a compact distribution of column match rates.
    if not (matched and total):
        state, state_class = 'Insufficient comparable data', 'is-empty'
        sentence = 'No matched rows; match rates cannot be calculated.' if not matched else 'No non-key columns in comparison scope.'
    elif summary.get('malformed_records') and not (changed or left_only or right_only):
        state, state_class = 'Compared values match; records were excluded', 'is-empty'
        sentence = f'{matched:,} keys were compared. Review the skipped-record audit before approving this comparison.'
    elif changed or left_only or right_only:
        state, state_class = 'Differences identified', ''
        parts = [f'<strong>{cells:,}</strong> mismatched cells across <strong>{len(changed):,}</strong> of {total:,} columns'] if changed else ['Every compared column matches']
        if left_only or right_only:
            parts.append(f'<strong>{left_only:,}</strong> keys only in File 1 and <strong>{right_only:,}</strong> only in File 2')
        sentence = '; '.join(parts) + f'. {matched:,} keys were found in both files.'
    else:
        state, state_class = 'No differences within selected scope', 'is-clean'
        sentence = f'All {total:,} compared columns match for the <strong>{matched:,}</strong> keys found in both files.'
    out += ('<section class="block" id="summary"><div class="verdict"><p class="rate">' + percent(overall)
            + '<small>Overall cell match across matched keys and compared columns</small></p><div><span class="state ' + state_class + '">'
            + state + '</span><p class="verdict-text">' + sentence + '</p></div></div>')
    if matched and total:
        bands = match_bands(summary)
        slices = [(name, count, f'b{index}') for index, (name, count, _) in enumerate(bands) if count]
        out += '<div class="distribution">' + donut_svg(slices, total, esc, 'columns compared', 'Column match distribution')
        out += '<div><h3>Column match distribution</h3><ul class="bands">' + ''.join(f'<li><span class="dot b{index}"></span>{esc(name)} <b>{count:,}</b></li>' for index, (name, count, _) in enumerate(bands)) + '</ul>'
        out += '<p class="rate-note">100% means zero mismatches. Bands use exact counts before display rounding and do not overlap.</p></div></div>'
    else:
        out += '<p class="empty">' + ('No matched rows; match rates cannot be calculated.' if not matched else 'No non-key columns in comparison scope.') + '</p>'
    if summary.get('malformed_records'):
        out += '<div class="notice"><h2>Completed with skipped records</h2><p>' + f"{summary.get('malformed_records', 0):,} malformed records; {summary.get('skipped_keys', 0):,} identifiable keys excluded from both files. " + f"{summary.get('unidentified_records', 0):,} records had no readable key, so their counterparts could not be identified." + '</p><p>Skipped records are excluded from match rates. <a href="#skipped-records">Review reasons</a>.</p></div>'
    if summary.get('review_revision'):
        out += '<div class="notice"><h2>Review revision</h2><p>' + esc(', '.join(k.replace('_', ' ') + ': ' + str(v) for k, v in summary['review_revision'].items())) + '</p><p>Re-evaluated retained evidence; the original comparison is preserved.</p></div>'
    if summary.get('left_duplicate_keys') or summary.get('right_duplicate_keys'):
        out += '<div class="notice"><h2>Warning: duplicate keys</h2><p>' + esc(
            f"Kept the {summary.get('duplicate_policy', 'first')} source occurrence per key. File 1: {summary.get('left_duplicate_keys', 0):,} duplicate keys, {summary.get('left_duplicate_rows_skipped', 0):,} skipped rows. "
            f"File 2: {summary.get('right_duplicate_keys', 0):,} duplicate keys, {summary.get('right_duplicate_rows_skipped', 0):,} skipped rows.") + '</p><p>Skipped duplicates are outside the match-rate denominator. The detailed duplicate audit remains available in the app and the Excel workbook.</p></div>'
    out += '</section>'

    # Mismatching columns: where the differences concentrate, then the complete ranked list.
    notes = {n['column']: n['status'] + ': ' + n['comment'] for n in summary.get('analysis_notes', []) if n['key'] is None}
    out += '<section class="block" id="statistics"><h2>All mismatching columns (' + str(len(changed)) + ')</h2><p class="lede">Where the mismatched cells are. Percentages use ' + f'{matched:,}' + ' matched keys.</p>'
    out += '<div class="toolbar"><div><h3>Every column with differences</h3><p>Largest first. Select a column to see its mismatch patterns and sample keys.</p></div><label class="search-label">Find a column <input id="columnSearch" type="search" placeholder="Column name"></label></div>'
    out += '<div class="table-wrap"><table id="columnStats"><thead><tr><th>Column</th><th class="num">Mismatched cells</th><th class="num">Mismatch %</th><th class="num">Match %</th><th>Review notes</th></tr></thead><tbody>'
    largest = changed[0][1] if changed else 0
    for index, (name, count, mismatch, match) in enumerate(changed):
        out += (f'<tr id="column-{index}"><td>{esc(name)}</td><td class="num">{count:,}<span class="share b{band_index(count, matched)}" data-share="{count / largest:.4f}"><i></i></span></td>'
                f'<td class="num">{percent(mismatch)}</td><td class="num">{percent(match)}</td><td>{esc(notes.get(name, ""))}</td></tr>')
    out += '</tbody></table></div>'
    if not changed:
        out += '<p class="empty">No mismatching columns in the selected scope.</p>'
    out += '</section>'

    # Keys: every key lands in exactly one group, so the bar adds up to all keys in both files.
    groups = [('k-equal', 'Matching keys', summary.get('equal_rows', max(0, matched - summary.get('changed_rows', 0))), 'Identical in every compared column'),
              ('k-changed', 'Keys with differences', summary.get('changed_rows', 0), 'At least one mismatched cell'),
              ('k-left', 'Only in File 1', left_only, 'Not found in File 2'),
              ('k-right', 'Only in File 2', right_only, 'Not found in File 1')]
    all_keys = sum(group[2] for group in groups)
    out += '<section class="block" id="keys"><h2>Keys</h2><p class="lede">How the keys of both files line up. Only keys found in both files are compared cell by cell.</p>'
    if all_keys:
        out += '<div class="keybar" role="img" aria-label="' + esc('; '.join(f'{label}: {count:,}' for _, label, count, _ in groups)) + '">' + ''.join(
            f'<span class="{css}" data-grow="{count}" title="{esc(label)}: {count:,}"></span>' for css, label, count, _ in groups if count) + '</div>'
    out += '<dl class="figures">' + ''.join(
        f'<div><dt><span class="dot {css}"></span>{label}</dt><dd>{count:,}<small>' + (f'{100 * count / all_keys:.2f}% of all keys' if all_keys else note) + '</small></dd></div>'
        for css, label, count, note in groups) + '</dl>'
    out += '<div class="sources">'
    for label, side, name in (('File 1', 'left', files[0]), ('File 2', 'right', files[1])):
        rows = summary.get(side + '_rows', 'N/A')
        out += f'<p>{label}: <strong>{esc(name)}</strong><br>Source rows: <strong>{esc(f"{rows:,}" if isinstance(rows, int) else rows)}</strong></p>'
    out += '</div></section>'


    if summary.get('malformed_records'):
        out += '<section class="block" id="skipped-records"><h2>Skipped keys and records</h2><p class="lede">Correct the source records and run a new comparison to include them. Empty values are valid; an unexpected number of fields is not.</p>'
        out += '<p>Preview of up to 20 malformed records. The Excel workbook and skipped_keys.csv contain the complete audit.</p>'
        out += '<div class="table-wrap"><table><thead>' + html_row(['File', 'Key', 'Location', 'Expected fields', 'Actual fields', 'Reason', 'Action'], True) + '</thead><tbody>'
        for entry in summary.get('skipped_records_preview', []):
            out += html_row(['File 1' if entry['side'] == 'left' else 'File 2', entry['key_json'] or 'Key unavailable', entry['location'], entry['expected_fields'], entry['actual_fields'], entry['reason'], entry['action']])
        out += '</tbody></table></div></section>'

    out += ('<section class="block" id="matching"><div class="toolbar"><div><h2>Attributes with 100% match (' + str(len(equal)) + ')</h2><p>Each listed column has ' + f'{matched:,}'
            + ' matched cells and zero mismatches.</p></div>' + ('<label class="search-label">Find a column <input id="matchingSearch" type="search" placeholder="Column name"></label>' if equal else '')
            + '</div><div class="chips">' + ''.join('<span class="chip">' + esc(row[0]) + '</span>' for row in equal) + '</div>' + ('' if equal else '<p class="empty">No column matches on every key.</p>') + '</section>')

    out += ('<section class="block callout" id="scope"><h2>Ignored key containers — excluded from comparison</h2><p class="lede">Configured keys can overlap across containers or be absent from source files. Container counts are not additive; excluded-row counts reflect actual input rows.</p>'
            + '<dl class="figures"><div><dt>File 1 excluded rows</dt><dd>' + f"{summary.get('left_excluded_rows', 0):,}" + '</dd></div><div><dt>File 2 excluded rows</dt><dd>' + f"{summary.get('right_excluded_rows', 0):,}" + '</dd></div></dl>')
    for item in summary.get('ignore_key_containers', []):
        out += '<div class="container"><h3>' + esc(item['name']) + '</h3><p>' + esc(item['reason']) + '</p><details><summary>View ' + f'{len(item["keys"]):,}' + ' configured keys</summary><div class="table-wrap"><table><thead>' + html_row(summary['keys'], True) + '</thead><tbody>' + ''.join(html_row(key) for key in item['keys']) + '</tbody></table></div></details></div>'
    if not summary.get('ignore_key_containers'):
        out += '<p class="empty">No ignored key containers configured.</p>'
    out += '</section>'

    unmatched = summary.get('unmatched_columns', {})
    definitions = [('Ignored columns', ', '.join(summary.get('ignored_columns', [])) or 'None'),
                   ('Columns only in File 1', ', '.join(unmatched.get('left', [])) or 'None'),
                   ('Columns only in File 2', ', '.join(unmatched.get('right', [])) or 'None'),
                   ('Comparison', summary.get('comparison', 'exact text')),
                   ('Applied value rules', json.dumps(summary.get('comparison_rules', []), ensure_ascii=False)),
                   ('Value overrides', json.dumps(summary.get('value_overrides', []), ensure_ascii=False)),
                   ('Cells accepted by overrides', f"{summary.get('override_equivalent_cells', 0):,}"),
                   ('Cells accepted by rules', f"{summary.get('rule_equivalent_cells', 0):,}")]
    out += ('<section class="block scope-text" id="interpretation"><h2>Scope &amp; interpretation</h2><p>Match rate = matching cells ÷ compared cells for retained keys present in both files. One-sided keys, ignored rows, skipped duplicates, ignored columns and non-common headers are excluded. A perfect column match does not imply identical source files.</p>'
            + '<p>Ignored columns: ' + esc(definitions[0][1]) + '</p><dl class="defs">' + ''.join(f'<dt>{label}</dt><dd>{esc(value)}</dd>' for label, value in definitions[1:]) + '</dl>'
            + '<p>This single-file report includes every compared column and every configured ignored key. The Excel workbook contains the complete exported cell mismatches; use the application’s analysis drawer to inspect keys and their differences.</p></section>')
    if summary.get('analysis_notes'):
        out += '<section class="block" id="comments"><h2>Analysis classifications &amp; comments</h2><div class="table-wrap"><table>' + ''.join(html_row(row, i == 0) for i, row in enumerate(note_rows(summary))) + '</table></div></section>'
    out += '</main><footer>TransUnion data comparison report, generated by the comparison tool.</footer>'
    out += '''<script>
(function(){
const search=document.getElementById('columnSearch');
search.addEventListener('input',()=>{const q=search.value.toLowerCase();document.querySelectorAll('#columnStats > tbody > tr').forEach(r=>r.hidden=!r.cells[0].textContent.toLowerCase().includes(q));});
const chipSearch=document.getElementById('matchingSearch');
if(chipSearch)chipSearch.addEventListener('input',()=>{const q=chipSearch.value.toLowerCase();document.querySelectorAll('#matching .chip').forEach(c=>c.hidden=!c.textContent.toLowerCase().includes(q));});
for(const bar of document.querySelectorAll('.share'))bar.firstChild.style.width=Math.max(2,100*Number(bar.dataset.share))+'%';
for(const part of document.querySelectorAll('.keybar span'))part.style.flexGrow=part.dataset.grow;
// The section index follows the reader down the page.
const links=[...document.querySelectorAll('.toc a')],targets=links.map(a=>document.querySelector(a.getAttribute('href')));
function mark(){let current=0;targets.forEach((t,i)=>{if(t&&t.getBoundingClientRect().top<120)current=i;});links.forEach((a,i)=>a.setAttribute('aria-current',String(i===current)));}
addEventListener('scroll',mark,{passive:true});mark();
let opened=[];addEventListener('beforeprint',()=>{opened=[...document.querySelectorAll('.container details:not([open])')];opened.forEach(d=>d.open=true);});addEventListener('afterprint',()=>opened.forEach(d=>d.open=false));
})();
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


EXCEL_CELL_LIMIT = 32767
EXCEL_DATA_ROWS = 1048574


def xml_text(value, truncated=None):
    """Escape one cell. With a ``truncated`` counter, over-long text is cut and visibly flagged instead of failing."""
    value = str(value)
    length = len(value.encode('utf-16-le')) // 2
    if length > EXCEL_CELL_LIMIT:
        if truncated is None:
            raise ValueError('A value exceeds Excel’s 32,767-character cell limit. Download the CSV results to preserve the full value.')
        marker = f' … [TRUNCATED: {length:,} characters; full value in the CSV results]'
        keep = EXCEL_CELL_LIMIT - len(marker) - 1
        value = value.encode('utf-16-le')[:keep * 2].decode('utf-16-le', errors='ignore') + marker
        truncated['cells'] = truncated.get('cells', 0) + 1
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


def write_xlsx(path, sheets, options=None, notify=lambda message:None, truncated=None):
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
                            cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value, truncated)}</t></is></c>')
                    stream.write((f'<row r="{number}">' + ''.join(cells) + '</row>').encode('utf-8'))
                stream.write((f'</sheetData><autoFilter ref="A{header_rows}:{column_letter(max_columns)}{max(header_rows, number)}"/>' + ('<hyperlinks>'+''.join(hyperlinks)+'</hyperlinks>' if hyperlinks else '') + '</worksheet>').encode())


@contextlib.contextmanager
def partition_columns(report, columns, parent, notify, limit=None):
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
            written = {column: 0 for column in columns}
            full = 0
            with (report / 'differences.csv').open(encoding='utf-8', newline='') as source:
                for index, row in enumerate(csv.DictReader(source), 1):
                    column = row['column']
                    # With a row limit, a full column is skipped, and reading stops once every column is full.
                    if limit is not None and column in written:
                        if written[column] >= limit:
                            continue
                        written[column] += 1
                        if written[column] == limit:
                            full += 1
                            if full == len(columns):
                                break
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


def export_excel(report, destination, notify=lambda message: None, max_rows=None, truncate=False):
    """Excel workbook. ``max_rows`` caps each column sheet (Excel holds at most 1,048,574 data rows per sheet);
    ``truncate`` cuts over-long values to fit a cell and flags them instead of failing."""
    summary = report_summary(report)
    row_notes=comment_lookup(summary)
    stats = column_stats(summary)
    columns = [name for name,count,_,_ in stats if count]
    limit = min(int(max_rows), EXCEL_DATA_ROWS) if max_rows else EXCEL_DATA_ROWS
    capped = {name: summary['changed_cells_by_column'][name] for name in columns if summary['changed_cells_by_column'][name] > limit}
    truncated = {} if truncate else None
    has_duplicates = bool(summary.get('left_duplicate_keys') or summary.get('right_duplicate_keys'))
    audit_sheets = ['Skipped keys' + (f' ({i + 1})' if i else '') for i in range((summary.get('malformed_records', 0) + EXCEL_DATA_ROWS - 1) // EXCEL_DATA_ROWS)]
    reserved = audit_sheets + ['Comparison Summary', 'TOC'] + (['Duplicate keys'] if has_duplicates else []) + (['Ignored key containers'] if summary.get('ignore_key_containers') else []) + (['Analysis notes'] if summary.get('analysis_notes') else [])
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
        yield ['Types','CSV fields are text; Excel uses stored cell values; Parquet values follow its schema. Configured value rules may accept textual differences. Keys stay exact.']
        if summary.get('ignore_key_containers'):
            yield ['IGNORED KEY CONTAINERS',Link('Review exclusions and reasons','Ignored key containers')]
            yield ['Excluded rows — File 1',summary.get('left_excluded_rows',0)]
            yield ['Excluded rows — File 2',summary.get('right_excluded_rows',0)]
            for item in summary['ignore_key_containers']:
                yield [item['name'],item['reason']]
        if audit_sheets:
            yield ['WARNING: malformed records skipped', Link('Review keys, field counts and reasons', 'Skipped keys')]
            yield ['Identifiable keys excluded from both files', summary.get('skipped_keys', 0)]
            yield ['Unidentified records (counterparts cannot be identified)', summary.get('unidentified_records', 0)]
        if has_duplicates:
            yield ['WARNING: duplicate keys',Link('Review skipped rows; kept '+summary.get('duplicate_policy','first')+' source occurrence','Duplicate keys')]
        if capped:
            yield ['Rows per column sheet',f'First {limit:,} mismatches of each column; {len(capped):,} column(s) have more. The CSV results hold every mismatch.']
        if truncate:
            yield ['Long values','Values over Excel’s 32,767-character cell limit are cut and marked [TRUNCATED]; the CSV results hold the full values.']
        yield ['Contents',Link('Open TOC','TOC')]
        for row in itertools.islice(summary_rows(summary),1,None):
            value=row[1]
            if isinstance(value,str) and len(value)>15000:
                for start in range(0,len(value),15000):
                    yield [row[0] if not start else row[0]+' (continued)',value[start:start+15000]]
            else:
                yield row
    sheets = [('Comparison Summary',summary_sheet()), ('TOC',toc())]
    if audit_sheets:
        def full_audit():
            with (report/'skipped_keys.csv').open(encoding='utf-8', newline='') as stream:
                reader = csv.reader(stream)
                next(reader)
                yield from reader
        audit = full_audit()
        from invalid_rows import FIELDS
        def audit_page(count):
            yield FIELDS
            for row in itertools.islice(audit, count):
                # Excel's cell limit cannot hold arbitrarily large keys; keep the CSV exact.
                yield [value if len(value) <= 32767 else value[:32700] + ' [TRUNCATED; see skipped_keys.csv]' for value in row]
        for index, name in enumerate(audit_sheets):
            sheets.append((name, audit_page(min(EXCEL_DATA_ROWS, summary['malformed_records'] - index * EXCEL_DATA_ROWS))))
    if has_duplicates:
        def duplicate_rows():
            with (report / 'duplicate_keys.csv').open(encoding='utf-8', newline='') as stream:
                yield from csv.reader(stream)
        sheets.append(('Duplicate keys', duplicate_rows()))
    if summary.get('ignore_key_containers'):
        sheets.append(('Ignored key containers',exclusion_rows(summary)))
    if summary.get('analysis_notes'):
        sheets.append(('Analysis notes',note_rows(summary)))
    options = {name: {'header_rows':1} for name in ['Comparison Summary','TOC','Analysis notes','Duplicate keys']+audit_sheets+list(mapped.values())}
    if summary.get('ignore_key_containers'):
        options['Ignored key containers']={'header_rows':2,'audit':True}
    for name in mapped.values():
        options[name]['value_columns']=[len(summary['keys'])+1,len(summary['keys'])+2]
    with partition_columns(report, columns, destination.parent, notify, limit if capped else None) as paths:
        def rows(column):
            notify(f'Writing sheet for {column}')
            yield summary['keys']+['File 1 value','File 2 value','Column']+(['Comments'] if row_notes else [])+[Link('Back to TOC','TOC')]
            with paths[column].open(encoding='utf-8') as source:
                for line in source:
                    values=json.loads(line)
                    yield values+[column]+([applicable_comments(row_notes,column,values[:-2])] if row_notes else [])
        sheets.extend((mapped[column],rows(column)) for column in columns)
        if not columns:
            sheets.append(('No mismatches',iter([['Comparison result'],['Status','Details'],['No changed cells','Check summary for keys found only in one file.']])))
        write_xlsx(destination,sheets,options,notify,truncated)
    return dict(rows_per_column=limit, capped_columns=len(capped), truncated_cells=(truncated or {}).get('cells', 0))


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
    """One self-contained report; sample keys live with each column's mismatch patterns."""
    notify('Preparing leadership summary and column match distribution…')
    summary = report_summary(report)
    notify('Rendering all columns and ignored-key containers…')
    document = make_summary_html(summary)
    from report_patterns import pattern_drawer_chunks
    notify('Writing single-file HTML report…')
    with destination.open('w', encoding='utf-8', buffering=1024*1024) as output:
        head,_,tail=document.rpartition('</body>')
        for offset in range(0,len(head),1024*1024):
            notify('Writing single-file HTML report…');output.write(head[offset:offset+1024*1024])
        for chunk in pattern_drawer_chunks(report,summary,notify):output.write(chunk)
        output.write('</body>'+tail)


def _journey_svg(rounds, population, esc):
    """The project's journey as a static picture: keys checked (log scale) across, mismatching columns down."""
    import math
    from datetime import datetime, timezone
    if not rounds:
        return ''
    width, height, left, right, top, bottom = 760, 300, 50, 130, 50, 52
    total = max(population or 0, 10, *(r['keys_checked'] for r in rounds))
    most = max(1, *(r['mismatching'] for r in rounds))
    x = lambda keys: left + math.log10(max(1, keys)) / math.log10(total) * (width - left - right)
    y = lambda count: top + (1 - count / most) * (height - top - bottom)
    out = [f'<svg class="journey" viewBox="0 0 {width} {height}" role="img" aria-label="Journey of the project, round by round">']
    out.append(f'<line x1="{left}" x2="{x(total) + 20:.1f}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#c9d6dc"/>')
    out.append(f'<text x="{left - 8}" y="{y(most) + 4:.1f}" text-anchor="end" class="ax">{most:,}</text><text x="{left - 8}" y="{y(0) + 4:.1f}" text-anchor="end" class="ax">0</text>')
    out.append(f'<text x="{left}" y="{top - 30}" class="ax">Mismatching columns</text><text x="{(left + x(total)) / 2:.1f}" y="{height - 4}" text-anchor="middle" class="ax">Keys checked →</text>')
    # The goal: a soft ring centred on its point (all keys, nothing mismatching).
    out.append(f'<circle cx="{x(total):.1f}" cy="{y(0):.1f}" r="20" fill="#e8f5ef" stroke="#bfe3d1"/><text x="{x(total):.1f}" y="{y(0) + 34:.1f}" text-anchor="middle" class="v">All {total:,}</text>')
    points = ' '.join(f'{x(r["keys_checked"]):.1f},{y(r["mismatching"]):.1f}' for r in rounds)
    out.append(f'<polyline points="{points}" fill="none" stroke="#193a50" stroke-width="2" stroke-linejoin="round"/>')
    last = rounds[-1]
    if last['keys_checked'] < total or last['mismatching']:
        out.append(f'<line x1="{x(last["keys_checked"]):.1f}" y1="{y(last["mismatching"]):.1f}" x2="{x(total):.1f}" y2="{y(0):.1f}" stroke="#8a9ba6" stroke-width="2" stroke-dasharray="5 5"/>')
    reached = last['keys_checked'] >= total and last['clean']
    for r in rounds:
        cx, cy = x(r['keys_checked']), y(r['mismatching'])
        fill = '#007b99' if r['clean'] else '#b8433a'
        label = 'Goal reached' if reached and r is last else 'Clean' if r['clean'] else f'{r["mismatching"]:,} column' + ('' if r['mismatching'] == 1 else 's')
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="11" fill="{fill}" stroke="#fff" stroke-width="3"/><text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" class="n">{r["n"]}</text>')
        # Labels go above-right of a stop, or below-left where the right edge is too close.
        at_goal = reached and r is last
        near_edge = cx > width - right + 20 and not at_goal
        lx, ly, anchor = (cx + 26, cy - 28, 'start') if at_goal else (cx - 14, cy - 50 if cy + 50 > height - bottom else cy + 22, 'end') if near_edge else (cx + 14, cy - 36, 'start')
        date = datetime.fromtimestamp(r.get('finished') or r['created'], IST).strftime('%d %b')
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" class="v">{esc(label)}</text><text x="{lx:.1f}" y="{ly + 13:.1f}" text-anchor="{anchor}" class="ax">{r["keys_checked"]:,} keys</text>'
                   f'<text x="{lx:.1f}" y="{ly + 26:.1f}" text-anchor="{anchor}" class="ax">{date}</text>')
    return ''.join(out) + '</svg>'


def project_signoff_html(project, progress, latest, signoff, attachments):
    """The final project report, built like the comparison report (same masthead, section index, blocks and
    tables): the verdict and who signed off, the journey, the reasons for any remaining mismatches, every round with
    its own HTML report attached, and the sign-off details."""
    from datetime import datetime, timezone
    esc = lambda value: html.escape(str(value))
    rounds, population = progress['rounds'], progress['population']
    last = rounds[-1]
    stamp = lambda value: datetime.fromtimestamp(value, IST)
    signed = stamp(signoff['at'])
    clean = signoff['mode'] == 'clean'
    fixed = sum(r['fixed'] for r in rounds)
    files = [name for name in (last.get('title') or '').split(' ↔ ') if name] or ['File 1', 'File 2']
    keys = ', '.join(latest.get('key_columns') or [])
    coverage = f'all {population:,} keys' if population and last['keys_checked'] >= population else f'{last["keys_checked"]:,}' + (f' of {population:,}' if population else '') + ' keys'
    how = {'carry': 'Previous round’s keys + random', 'random': 'Random sample', 'files': 'Every key in the files', 'all': 'All keys'}
    attached = {item['id']: item for item in attachments}
    plural = lambda count, word: f'{count:,} {word}' + ('' if count == 1 else 's')
    reasons = signoff['reasons'].get('columns', []) if not clean else []

    out = html_start(f'TransUnion | {project["name"]} sign-off report')
    out = out.replace('</style>', PROJECT_STYLE + '</style>', 1)
    out += ('<h1>Project sign-off report<time datetime="' + signed.isoformat(timespec='seconds') + '">' + esc(project['name']) + ' · signed off '
            + signed.strftime('%d %b %Y, %H:%M IST') + '</time></h1><button class="print" type="button" onclick="window.print()">Print or save as PDF</button>'
            + '<div class="files">' + ''.join(f'<div class="file-source"><span>{label}</span><strong>{esc(name)}</strong></div>' for label, name in zip(('File 1', 'File 2'), files))
            + (f'<div class="keys">Matched on <strong>{esc(keys)}</strong></div>' if keys else '') + '</div></div></header>')
    sections = [('summary', 'Summary', None), ('journey', 'Journey', len(rounds))]
    if not clean:
        sections.append(('reasons', 'Reasons', len(reasons) + (1 if signoff['reasons'].get('keys') else 0)))
    available = [r for r in rounds if (attached.get(r['id']) or {}).get('content')]
    sections += [('rounds', 'Rounds', len(rounds)), ('round-reports', 'Round reports', len(available)), ('signoff', 'Sign-off', None)]
    out += '<nav class="toc" aria-label="Report sections"><ol>' + ''.join(
        f'<li><a href="#{anchor}">{label}' + (f'<span class="count">{count:,}</span>' if count is not None else '') + '</a></li>' for anchor, label, count in sections) + '</ol></nav>'
    out += '<main class="page">'

    # Summary: the answer first, in the comparison report's verdict layout.
    matched, columns, cells = latest.get('matched_keys') or 0, latest.get('compared_columns') or 0, latest.get('changed_cells') or 0
    rate = 100 * (1 - cells / (matched * columns)) if matched and columns else None
    if clean:
        state, state_class = 'Signed off at 100% match', 'is-clean'
        sentence = f'Round {last["n"]} matched every compared value on <strong>{esc(coverage)}</strong>.'
    else:
        state, state_class = 'Signed off with reasons', 'is-reasons'
        sentence = (f'Round {last["n"]} still had <strong>{plural(last["mismatching"], "mismatching column")}</strong>'
                    + (f' and <strong>{plural(last["left_only"] + last["right_only"], "key")}</strong> in only one file' if last['left_only'] or last['right_only'] else '')
                    + f' on {esc(coverage)}. The reviewer accepted each with a reason.')
    out += ('<section class="block" id="summary"><div class="verdict"><p class="rate">' + percent(rate)
            + f'<small>Overall cell match in round {last["n"]}, the round signed off</small></p><div><span class="state {state_class}">{state}</span><p class="verdict-text">{sentence}</p></div></div>')
    figures = [('Rounds', f'{len(rounds):,}', 'From first sample to sign-off'),
               ('Keys checked', f'{last["keys_checked"]:,}', 'All keys' if population and last['keys_checked'] >= population else (f'{100 * last["keys_checked"] / population:.2f}% of {population:,}' if population else 'In the last round')),
               ('Mismatching columns', f'{last["mismatching"]:,}', 'At sign-off'),
               ('Columns fixed', f'{fixed:,}', 'Over all rounds')]
    out += '<dl class="figures">' + ''.join(f'<div><dt>{label}</dt><dd>{value}<small>{note}</small></dd></div>' for label, value, note in figures) + '</dl>'
    out += (f'<div class="sources"><p>Signed off by <strong>{esc(signoff["reviewer"])}</strong><br>{signed.strftime("%d %b %Y, %H:%M IST")}</p>'
            + (f'<p>Note<br><strong>{esc(signoff["note"])}</strong></p>' if signoff.get('note') else f'<p>Round signed off<br><strong>Round {last["n"]} · {esc(coverage)}</strong></p>') + '</div></section>')

    # Journey: the same picture as the project page.
    out += ('<section class="block" id="journey"><h2>The journey</h2><p class="lede">Each dot is a round: right means more keys checked, down means fewer columns mismatch. '
            'The goal is the bottom-right corner: every key checked and nothing mismatching.</p>' + _journey_svg(rounds, population, esc)
            + '<ul class="bands"><li><span class="dot j-bad"></span>Round with mismatches</li><li><span class="dot j-ok"></span>Clean round</li><li><span class="dot j-goal"></span>Goal</li></ul></section>')

    # Reasons: like the mismatching-columns table, with the accepted reason in place of review notes.
    if not clean:
        largest = max([item['cells'] for item in reasons] or [1])
        out += (f'<section class="block" id="reasons"><h2>Reasons for the remaining mismatches ({len(reasons):,})</h2>'
                f'<p class="lede">Each column still mismatching in round {last["n"]}, largest first, with the reason the reviewer gave for accepting it.</p>'
                '<div class="toolbar"><div><h3>Accepted differences</h3><p>Percentages use ' + f'{matched:,}' + ' matched keys.</p></div>'
                '<label class="search-label">Find a column <input id="reasonSearch" type="search" placeholder="Column name"></label></div>'
                '<div class="table-wrap"><table id="reasonTable"><thead><tr><th>Column</th><th class="num">Mismatched cells</th><th class="num">Mismatch %</th><th>Mismatching since</th><th>Reason</th></tr></thead><tbody>')
        for item in reasons:
            share = 100 * item['cells'] / matched if matched else 0
            out += (f'<tr><td>{esc(item["column"])}</td><td class="num">{item["cells"]:,}<span class="share b{band_index(item["cells"], matched) if matched else 3}" data-share="{item["cells"] / largest:.4f}"><i></i></span></td>'
                    f'<td class="num">{share:.4f}%</td><td>{esc(item["since"])}</td><td>{esc(item["reason"])}</td></tr>')
        out += '</tbody></table></div>'
        if signoff['reasons'].get('keys'):
            out += (f'<div class="notice"><h2>Keys in only one file</h2><p>{last["left_only"]:,} keys only in File 1 and {last["right_only"]:,} only in File 2.</p>'
                    f'<p>{esc(signoff["reasons"]["keys"])}</p></div>')
        out += '</section>'

    # Rounds, each with its full comparison report attached inside this file.
    out += ('<section class="block" id="rounds"><h2>Rounds</h2><p class="lede">Every round of the project, oldest first. '
            'Each round’s full comparison report is included below; select View report to open it.</p><div class="table-wrap"><table><thead><tr>'
            '<th>Round</th><th>Ran</th><th class="num">Keys checked</th><th>Keys chosen</th><th>Result</th><th>Change</th><th>Report</th></tr></thead><tbody>')
    for r in rounds:
        item = attached.get(r['id'])
        link = (f'<a class="view-report" href="#round-reports" data-round="{r["n"]}">View report</a>'
                if item and item.get('content') else '<span class="muted">Not available: the comparison’s results were removed</span>')
        result = ('<span class="ok">Clean</span>' if r['clean'] else f'<span class="bad">{plural(r["mismatching"], "column")}</span>'
                  + (f'<br><small class="muted">{r["left_only"] + r["right_only"]:,} keys in one file</small>' if r['left_only'] or r['right_only'] else ''))
        change = '—' if r['n'] == 1 else f'{r["fixed"]:,} fixed · {r["new"]:,} new'
        out += (f'<tr><td><span class="round-n{" is-clean" if r["clean"] else ""}">{r["n"]}</span></td><td>{stamp(r.get("finished") or r["created"]).strftime("%d %b %Y")}</td>'
                f'<td class="num">{r["keys_checked"]:,}</td><td>{esc(how.get(r["how"], ""))}</td><td>{result}</td><td>{change}</td><td>{link}</td></tr>')
    out += '</tbody></table></div></section>'

    # Round reports: each round's own comparison report, shown in full inside this file. A frame keeps each report's
    # styles and scripts to itself, so its section index, search and pattern drawer work as they do on their own.
    if available:
        signed_round = next((r for r in rounds if r['id'] == signoff['round_id']), last)
        chips = ''.join(
            f'<button type="button" class="round-chip" data-round="{r["n"]}" aria-pressed="false"><span class="round-n{" is-clean" if r["clean"] else ""}">{r["n"]}</span>'
            f'<span><b>Round {r["n"]}</b><small>{r["keys_checked"]:,} keys · ' + ('clean' if r['clean'] else plural(r['mismatching'], 'column')) + '</small></span>'
            + ('<em>Signed off</em>' if r['id'] == signed_round['id'] else '') + '</button>' for r in available)
        data = json.dumps([dict(n=r['n'], ran=stamp(r.get('finished') or r['created']).strftime('%d %b %Y, %H:%M IST'), keys=f'{r["keys_checked"]:,}',
                                result='Clean' if r['clean'] else plural(r['mismatching'], 'mismatching column'), how=how.get(r['how'], ''),
                                html=base64.b64encode(attached[r['id']]['content']).decode()) for r in available])
        out += (f'<section class="block viewer" id="round-reports"><h2>Round reports ({len(available):,})</h2>'
                '<p class="lede">The full comparison report of each round, exactly as it was produced. Choose a round; each report has its own sections, search and pattern drawer.</p>'
                f'<div class="round-switch" role="group" aria-label="Choose a round">{chips}</div>'
                '<div class="viewer-head"><div><strong id="viewerTitle"></strong><span id="viewerMeta"></span></div>'
                '<div class="viewer-actions"><button type="button" class="viewer-button" id="viewerOpen">Open in a new tab</button><a class="viewer-button" id="viewerDownload">Download</a></div></div>'
                '<iframe id="viewerFrame" title="Round comparison report"></iframe>'
                '<p class="print-note">Round reports are interactive. To print one, open it in a new tab and use its Print button.</p>'
                f'<script type="application/json" id="roundReportData">{data}</script></section>')
        default_round = signed_round['n'] if any(r['id'] == signed_round['id'] for r in available) else available[-1]['n']
    else:
        default_round = 0

    # Sign-off details, as the comparison report lists its scope.
    details = [('Project', project['name']), ('Description', project.get('description') or '—'), ('Signed off by', signoff['reviewer']),
               ('Signed off on', signed.strftime('%d %b %Y, %H:%M IST')), ('Path', 'Latest round matched 100%' if clean else 'Mismatches accepted with reasons'),
               ('Round signed off', f'Round {last["n"]}, {coverage}'), ('Note', signoff.get('note') or '—'),
               ('Round reports included', f'{len(available):,} of {len(rounds):,} rounds')]
    out += ('<section class="block scope-text" id="signoff"><h2>Sign-off</h2><p>A new round in this project reopens it; this report stays as the record of this sign-off.</p>'
            '<dl class="defs">' + ''.join(f'<dt>{label}</dt><dd>{esc(value)}</dd>' for label, value in details) + '</dl></section>')
    out += '</main><footer>TransUnion project sign-off report, generated by the comparison tool.</footer>'
    out += """<script>
(function(){
const search=document.getElementById('reasonSearch');
if(search)search.addEventListener('input',()=>{const q=search.value.toLowerCase();document.querySelectorAll('#reasonTable > tbody > tr').forEach(r=>r.hidden=!r.cells[0].textContent.toLowerCase().includes(q));});
for(const bar of document.querySelectorAll('.share'))bar.firstChild.style.width=Math.max(2,100*Number(bar.dataset.share))+'%';
const links=[...document.querySelectorAll('.toc a')],targets=links.map(a=>document.querySelector(a.getAttribute('href')));
function mark(){let current=0;targets.forEach((t,i)=>{if(t&&t.getBoundingClientRect().top<120)current=i;});links.forEach((a,i)=>a.setAttribute('aria-current',String(i===current)));}
addEventListener('scroll',mark,{passive:true});mark();
// Round reports: the chosen round's full report fills the frame; its own scripts and styles stay inside it.
const store=document.getElementById('roundReportData');
if(store){
 const rounds=JSON.parse(store.textContent),frame=document.getElementById('viewerFrame'),chips=[...document.querySelectorAll('.round-chip')];
 const decode=b64=>new TextDecoder().decode(Uint8Array.from(atob(b64),c=>c.charCodeAt(0)));
 let current=null;
 function show(n,scroll){
  current=rounds.find(r=>r.n===Number(n))||rounds[rounds.length-1];
  chips.forEach(c=>c.setAttribute('aria-pressed',String(Number(c.dataset.round)===current.n)));
  document.getElementById('viewerTitle').textContent='Round '+current.n+' comparison report';
  document.getElementById('viewerMeta').textContent=' · ran '+current.ran+' · '+current.keys+' keys ('+current.how.toLowerCase()+') · '+current.result;
  const download=document.getElementById('viewerDownload');download.href='data:text/html;base64,'+current.html;download.download='round-'+current.n+'-report.html';
  frame.title='Round '+current.n+' comparison report';frame.srcdoc=decode(current.html);
  if(scroll)document.getElementById('round-reports').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 }
 chips.forEach(c=>c.addEventListener('click',()=>show(c.dataset.round)));
 document.querySelectorAll('.view-report').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();show(a.dataset.round,true);}));
 document.getElementById('viewerOpen').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([decode(current.html)],{type:'text/html'}));window.open(url,'_blank','noopener');setTimeout(()=>URL.revokeObjectURL(url),60000);});
 show(DEFAULT_ROUND);
}
})();
</script></body></html>""".replace('DEFAULT_ROUND', str(default_round))
    return out


# Additions to the comparison report's style for the project sign-off report.
PROJECT_STYLE = """
.state.is-reasons::before{background:var(--band-medium)}
.journey{width:100%;max-width:820px;height:auto;display:block;margin:4px 0 14px}
.journey text{font-family:var(--sans);font-size:11.5px;fill:var(--muted)}
.journey .v{fill:var(--ink);font-weight:600;font-size:12.5px}.journey .n{fill:#fff;font-weight:700;font-size:11.5px}
.dot.j-bad{background:#b8433a;border-radius:50%}.dot.j-ok{background:var(--link);border-radius:50%}.dot.j-goal{background:var(--ink);border-radius:50%}
.round-n{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:7px;background:#b8433a;color:#fff;font-weight:600;font-size:12.5px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.round-n.is-clean{background:var(--link)}
.ok{color:var(--link);font-weight:600}.bad{color:#b8433a;font-weight:600}.muted{color:var(--muted)}
#reasonTable{min-width:760px}#reasonTable>tbody>tr>td:first-child{font-weight:600}
#reasonTable td:last-child{min-width:260px}
.view-report{font-weight:600}
.viewer .round-switch{position:sticky;top:48px;z-index:3;display:flex;gap:8px;overflow-x:auto;margin:0 -32px;padding:10px 32px;background:color-mix(in srgb,var(--paper) 94%,transparent);backdrop-filter:blur(6px);border-bottom:1px solid var(--line-soft);scrollbar-width:thin}
.round-chip{display:flex;align-items:center;gap:10px;flex:none;padding:8px 14px 8px 8px;border:1px solid var(--line);border-radius:10px;background:var(--paper);font:inherit;color:var(--ink);cursor:pointer;text-align:left}
.round-chip:hover{border-color:#9fd7e3}
.round-chip[aria-pressed=true]{border-color:var(--link);box-shadow:inset 0 0 0 1px var(--link);background:#f1f9fb}
.round-chip b{display:block;font-size:13.5px;font-weight:600}.round-chip small{display:block;font-size:12px;color:var(--muted)}
.round-chip em{font-style:normal;font-size:11.5px;font-weight:600;color:var(--link);background:#e7f5f8;border-radius:999px;padding:2px 8px}
.viewer-head{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin:16px 0 10px}
.viewer-head strong{font-size:15px}.viewer-head span{font-size:13px;color:var(--muted)}
.viewer-actions{display:flex;gap:8px}
.viewer-button{font:inherit;font-size:13px;font-weight:600;padding:7px 12px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);cursor:pointer;text-decoration:none}
.viewer-button:hover{border-color:#9fd7e3;background:#f1f9fb}
#viewerFrame{display:block;width:100%;height:calc(100vh - 150px);min-height:620px;border:1px solid var(--line);border-radius:10px;background:var(--canvas)}
.print-note{display:none}
@media (max-width:900px){.viewer .round-switch{margin:0 -18px;padding:10px 18px}}
@media print{.viewer .round-switch,.viewer-actions,#viewerFrame{display:none!important}.print-note{display:block;color:var(--muted)}}
"""
