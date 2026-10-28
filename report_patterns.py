"""The HTML report's pattern drawer: each column's mismatch patterns and sample keys, offline."""
import html,json

def safe_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')

def pattern_drawer_chunks(report,summary,notify):
    """The drawer's data and markup. Patterns are counted while comparing (mismatch_patterns); runs from before
    that are grouped once from differences.csv and the result is kept with the run."""
    import mismatch_patterns
    index = mismatch_patterns.report_index(report)
    data = None
    if index is None:
        data = mismatch_patterns.load(report)
        if data is None:
            notify('Grouping mismatch patterns for an older run…')
            mismatch_patterns.save(mismatch_patterns.from_differences(report, notify), report)
            index = mismatch_patterns.report_index(report)
            if index is None:
                data = mismatch_patterns.load(report)
    # Saved offsets avoid rebuilding millions of Python values just to encode
    # them back to JSON. Escape script delimiters even on this direct-copy path.
    yield '<style>' + DRAWER_STYLE + '</style>'
    import contextlib
    source = (report / mismatch_patterns.FILE).open('rb') if index else contextlib.nullcontext()
    with source as stream:
        for number, (c, count) in enumerate(summary['changed_cells_by_column'].items()):
            if not count:
                continue
            if number % 32 == 0:
                notify('Writing column mismatch patterns…')
            item = (index if index else data)['columns'].get(c)
            if item is None:
                item = dict(patterns=[], more_patterns=0, more_cells=count, partial=True)
                patterns = '[]'
            elif index:
                stream.seek(item['offset'])
                raw = stream.read(item['length'])
                if len(raw) != item['length'] or not raw.startswith(b'[') or not raw.endswith(b']'):
                    raise ValueError('Saved mismatch patterns changed during report generation. Retry the export.')
                patterns = raw.decode('utf-8').replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            else:
                patterns = safe_json(item['patterns'])
            yield ('<script type="application/json" class="column-pattern-data" data-column="'+html.escape(c,quote=True)+f'" data-total="{count}" data-more-patterns="{item["more_patterns"]}" data-more-cells="{item["more_cells"]}" data-partial="{int(item["partial"])}">'+patterns+'</script>')
    yield ('<script type="application/json" id="report-comment-data">'+safe_json(summary.get('analysis_notes',[]))+'</script>')
    yield (DRAWER_HTML)

# The drawer for one column: its mismatch patterns as a ranked list, most common first. Each row reads
# File 1 value -> File 2 value with its count and share, a share bar along its foot, and up to five keys that
# have it. A value search narrows the list; paging stays at the bottom.
DRAWER_STYLE='''
.report-drawer{position:fixed;inset:0 0 0 auto;width:min(780px,96vw);max-width:96vw;height:100dvh;max-height:100dvh;margin:0;padding:0;border:0;border-left:4px solid #00a6ca;box-sizing:border-box;background:#fff;color:#002b40;font:14px/1.5 "Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,Arial,sans-serif;box-shadow:-16px 0 48px #002b4033}
.report-drawer::backdrop{background:#002b4066}
.report-drawer[open]{display:flex;flex-direction:column}
.pd-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:22px 28px 16px;border-bottom:1px solid #eaf0f3}
.pd-head p{margin:0 0 4px;font-size:13px;color:#5f7682}
.pd-head h2{margin:0;font:600 21px/1.25 ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;overflow-wrap:anywhere}
.pd-stats{margin:6px 0 0!important;font-variant-numeric:tabular-nums}
.pd-close{flex:none;display:grid;place-items:center;width:38px;height:38px;padding:0;border:1px solid #d9e3e9;border-radius:8px;background:#fff;color:#3d5566;font-size:20px;line-height:1;cursor:pointer}
.pd-close:hover{background:#fdeeea;border-color:#efc2b8;color:#8f3a2f}
.pd-notes:empty{display:none}
.pd-notes{padding:12px 28px 0}
.pd-notes p{margin:0 0 6px;padding:8px 12px;border-left:3px solid #fcd800;background:#fff8d1;font-size:13.5px}
.pd-tools{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 28px 10px}
.pd-tools input{flex:1;max-width:320px;height:36px;padding:0 12px;border:1px solid #b8cbd5;border-radius:8px;font:inherit;font-size:14px;color:#002b40}
.pd-tools span{font-size:12.5px;color:#5f7682;font-variant-numeric:tabular-nums;white-space:nowrap}
.pd-list{flex:1;min-height:0;margin:0;padding:4px 28px 16px;overflow:auto;list-style:none;overscroll-behavior:contain}
.pd-item{position:relative;display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:4px 14px;padding:14px 0 18px}
.pd-rank{padding-top:3px;font:600 12px/1 ui-monospace,Consolas,Menlo,monospace;color:#8aa0ab;text-align:right}
.pd-pair{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}
.pd-v{max-width:100%;padding:3px 10px;border-radius:6px;font:600 13px/1.5 ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;overflow-wrap:anywhere;white-space:pre-wrap}
.pd-v.is-before{background:#fdeeea;color:#8f3a2f}.pd-v.is-after{background:#e8f5ef;color:#2b6b4e}
.pd-v.is-empty{font-style:italic;font-weight:500}
.pd-arrow{color:#8aa0ab;font-size:14px}
.pd-figures{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pd-figures strong{display:block;font-size:16px;font-weight:650}
.pd-figures span{font-size:12.5px;color:#5f7682}
.pd-keys{grid-column:2/-1;display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:6px}
.pd-keys>span{margin-right:2px;font-size:12px;color:#5f7682}
.pd-keys button{padding:2px 8px;border:1px solid #d9e3e9;border-radius:5px;background:#f6f9fa;color:#002b40;font:500 12px/1.6 ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;cursor:copy}
.pd-keys button:hover{border-color:#00a6ca;background:#eaf6f8}
.pd-keys button.is-copied{border-color:#2b8b65;background:#e8f5ef}
.pd-bar{position:absolute;left:48px;right:0;bottom:-1px;height:3px;border-radius:2px;background:#eef3f6;overflow:hidden}
.pd-bar i{display:block;height:100%;background:#00a6ca}
.pd-empty{padding:24px 0;color:#5f7682}
.pd-more{padding:16px 0 4px 48px;font-size:13px;color:#5f7682}
.pd-notes p.pd-limit{border-left-color:#00a6ca;background:#eaf6f8}
.pd-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 28px;border-top:1px solid #eaf0f3;font-size:12.5px;color:#5f7682}
.pd-foot div{display:flex;gap:8px}
.pd-foot button{height:34px;padding:0 14px;border:1px solid #c8d7df;border-radius:8px;background:#fff;color:#002b40;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.pd-foot button:hover:not(:disabled){background:#eef6f8}
.pd-foot button:disabled{opacity:.45;cursor:default}
.report-drawer :focus-visible{outline:3px solid #00a6ca;outline-offset:2px}
#columnStats>tbody>tr{cursor:pointer}
#columnStats>tbody>tr:focus-visible{outline:3px solid #00a6ca;outline-offset:-3px}
@media (max-width:640px){.pd-head,.pd-tools,.pd-list,.pd-foot{padding-left:16px;padding-right:16px}.pd-item{grid-template-columns:26px minmax(0,1fr)}.pd-figures{grid-column:2;text-align:left}.pd-figures strong{display:inline;margin-right:6px}.pd-bar{left:40px}}
@media (prefers-reduced-motion:no-preference){.report-drawer[open]{animation:report-in .22s ease-out}@keyframes report-in{from{transform:translateX(40px);opacity:0}to{transform:none;opacity:1}}}
@media print{.report-drawer{display:none}}
'''

DRAWER_HTML='''<dialog id="reportColumnDrawer" class="report-drawer" aria-labelledby="reportColumnTitle">
<div class="pd-head"><div><p>Mismatch patterns</p><h2 id="reportColumnTitle"></h2><p class="pd-stats" id="reportColumnStats"></p></div><button id="closeReportDrawer" class="pd-close" aria-label="Close">&times;</button></div>
<div class="pd-notes" id="reportColumnNotes"></div>
<div class="pd-tools"><input id="reportPatternSearch" type="search" placeholder="Find a value" aria-label="Find a value in these patterns"><span id="reportPatternRange"></span></div>
<ol class="pd-list" id="reportPatternRows"></ol>
<div class="pd-foot"><span>Sample keys are shown for the 50 most common patterns. Select a key to copy it.</span><div><button id="reportPatternPrev">Previous</button><button id="reportPatternNext">Next</button></div></div>
</dialog><script>(function(){
const dialog=document.getElementById('reportColumnDrawer'),list=document.getElementById('reportPatternRows'),search=document.getElementById('reportPatternSearch'),notes=JSON.parse(document.getElementById('report-comment-data').textContent);
const data=new Map([...document.querySelectorAll('.column-pattern-data')].map(n=>[n.dataset.column,n]));
let pairs=[],shown=[],page=0,total=0,more=null,opener;const PAGE=50;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const value=(text,side)=>el('span','pd-v is-'+side+(text===''?' is-empty':''),text===''?'(empty)':text);
function paint(){
 list.replaceChildren();const start=page*PAGE,rows=shown.slice(start,start+PAGE);
 for(const [rank,pair] of rows){
  const [a,b,n,keys]=pair,item=el('li','pd-item'),main=el('div'),line=el('div','pd-pair'),figures=el('div','pd-figures');
  line.append(value(a,'before'),el('span','pd-arrow','→'),value(b,'after'));main.append(line);
  figures.append(el('strong','',n.toLocaleString()),el('span','',(total?100*n/total:0).toFixed(2)+'% of mismatches'));
  item.append(el('span','pd-rank',String(rank)),main,figures);
  if(keys&&keys.length){const box=el('div','pd-keys');box.append(el('span','',keys.length===1?'Key':'Keys'));
   for(const key of keys){const text=Array.isArray(key)?key.join(' · '):String(key),chip=el('button','',text);chip.type='button';chip.title='Copy this key';
    chip.addEventListener('click',()=>{try{navigator.clipboard.writeText(text).then(()=>{chip.classList.add('is-copied');setTimeout(()=>chip.classList.remove('is-copied'),1200);},()=>{});}catch(e){}});box.append(chip);}
   item.append(box);}
  const bar=el('span','pd-bar'),fill=el('i');fill.style.width=Math.max(.5,total?100*n/total:0)+'%';bar.append(fill);item.append(bar);
  list.append(item);
 }
 if(!rows.length)list.append(el('li','pd-empty',search.value?'No listed pattern contains that value.':'No mismatch patterns.'));
 // Less common patterns beyond the listed ones are summed, so the counts still add up to the column's total.
 if(more&&more.cells&&!search.value&&start+PAGE>=shown.length)list.append(el('li','pd-more',`${more.cells.toLocaleString()} more mismatched ${more.cells===1?'cell':'cells'} in ${more.patterns?more.patterns.toLocaleString()+(more.partial?'+':'')+' less common patterns':'less common patterns'}, not listed here. differences.csv has every mismatch.`));
 document.getElementById('reportPatternRange').textContent=shown.length?`${(start+1).toLocaleString()}–${Math.min(start+PAGE,shown.length).toLocaleString()} of ${shown.length.toLocaleString()} patterns`:'';
 document.getElementById('reportPatternPrev').disabled=page===0;document.getElementById('reportPatternNext').disabled=start+PAGE>=shown.length;
 list.scrollTop=0;
}
function filter(){const q=search.value.toLowerCase();shown=pairs.map((pair,i)=>[i+1,pair]).filter(([,p])=>!q||p[0].toLowerCase().includes(q)||p[1].toLowerCase().includes(q));page=0;paint();}
function open(row){
 const column=row.cells[0].textContent,node=data.get(column);if(!node)return;opener=row;
 pairs=JSON.parse(node.textContent);total=Number(node.dataset.total)||pairs.reduce((sum,p)=>sum+p[2],0);search.value='';
 more={patterns:Number(node.dataset.morePatterns)||0,cells:Number(node.dataset.moreCells)||0,partial:node.dataset.partial==='1'};
 document.getElementById('reportColumnTitle').textContent=column;
 const count=pairs.length+more.patterns;
 document.getElementById('reportColumnStats').textContent=`${total.toLocaleString()} mismatched cells in ${count.toLocaleString()}${more.partial?'+':''} ${count===1&&!more.partial?'pattern':'patterns'}`+(more.patterns||more.cells?`, the ${pairs.length.toLocaleString()} most common listed`:'');
 const box=document.getElementById('reportColumnNotes');box.replaceChildren();
 for(const note of notes.filter(n=>n.column===column&&n.key===null))box.append(el('p','',note.status+': '+note.comment));
 if(more.partial)box.append(el('p','pd-limit','This column has more distinct value pairs than the report follows, so a few counts here can be lower than in differences.csv.'));
 filter();dialog.showModal();search.focus();
}
for(const row of document.querySelectorAll('#columnStats>tbody>tr')){row.tabIndex=0;row.setAttribute('aria-haspopup','dialog');row.addEventListener('click',()=>open(row));row.addEventListener('keydown',e=>{if(e.target!==row)return;if(e.key==='Enter'||e.key===' '){e.preventDefault();open(row);}});}
search.addEventListener('input',filter);
document.getElementById('closeReportDrawer').onclick=()=>dialog.close();dialog.addEventListener('close',()=>opener?.focus());
document.getElementById('reportPatternPrev').onclick=()=>{page--;paint();};document.getElementById('reportPatternNext').onclick=()=>{page++;paint();};
})();</script>'''
