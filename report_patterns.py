"""Disk-backed exact-pair aggregation and offline HTML pattern drawers."""
import csv,hashlib,json,sqlite3,tempfile,html
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
from collections import Counter
from pathlib import Path
from contextlib import closing

def safe_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')

def pattern_drawer_chunks(report,summary,notify):
    # Only the aggregation cache is retained; source values remain in differences.csv.
    target=report/'report-patterns.sqlite'
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='.pattern-',dir=report) as scratch:
            temp=Path(scratch)/'patterns.sqlite'
            with closing(sqlite3.connect(temp)) as db:
                db.executescript('PRAGMA temp_store=FILE; PRAGMA cache_size=-8192; CREATE TABLE patterns(c TEXT,a TEXT,b TEXT,l TEXT,r TEXT,n INTEGER,PRIMARY KEY(c,l,r));')
                pending=Counter();examples={}
                def flush():
                    db.executemany('INSERT INTO patterns VALUES(?,?,?,?,?,?) ON CONFLICT(c,l,r) DO UPDATE SET n=n+excluded.n',[(c,*examples[c,l,r],l,r,n) for (c,l,r),n in pending.items()]);db.commit();pending.clear();examples.clear()
                with (report/'differences.csv').open(encoding='utf-8',newline='') as stream:
                    for i,row in enumerate(csv.DictReader(stream),1):
                        a,b=row['left_value'],row['right_value'];key=(row['column'],hashlib.sha256(a.encode()).hexdigest(),hashlib.sha256(b.encode()).hexdigest());pending[key]+=1
                        examples[key]=(a[:1000]+(' … [truncated]' if len(a)>1000 else ''),b[:1000]+(' … [truncated]' if len(b)>1000 else ''))
                        if i%10000==0:flush();notify(f'Grouping mismatch patterns: {i:,} cells')
                flush();db.execute('CREATE INDEX pattern_rank ON patterns(c,n DESC,l,r)');db.commit()
            temp.replace(target)
    # All pairs are included; scripts are inert JSON until their column is opened.
    yield ('<style>.report-drawer{position:fixed;inset:0 0 0 auto;max-height:100dvh;width:min(760px,94vw);max-width:94vw;margin:0;border:0;border-left:4px solid #00a6ca;padding:24px 28px;box-sizing:border-box;overflow:auto;background:#fff;color:#002b40;font:14px/1.5 "Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,Arial,sans-serif;box-shadow:-12px 0 40px #002b4033}.report-drawer::backdrop{background:#002b4066}.report-drawer header{display:flex;justify-content:space-between;align-items:center;gap:12px}.report-drawer h2{font-size:19px;font-weight:600;margin:0;overflow-wrap:anywhere}.report-drawer h3{font-size:15px;font-weight:600}.report-drawer td{overflow-wrap:anywhere}.report-drawer button{font:inherit;font-size:13.5px;font-weight:600;cursor:pointer;padding:8px 14px;border:1px solid #c8d7df;border-radius:6px;background:#fff;color:#002b40}.report-drawer button:hover:not(:disabled){background:#eef6f8}.report-drawer button:disabled{opacity:.45;cursor:default}#closeReportDrawer{background:#002b40;color:#fff;border-color:#002b40}.report-pattern-table{table-layout:fixed;width:100%}#reportColumnDrawer[open]{display:flex;flex-direction:column;gap:10px;height:100dvh}#reportColumnDrawer>header,#reportColumnDrawer>h3,#reportColumnDrawer>p{flex-shrink:0;margin:0}#reportPatternRange{color:#5f7682;font-size:13px}#reportColumnDrawer>.table-wrap{min-height:120px;max-height:38dvh;overflow:auto;flex:1;border:1px solid #eaf0f3;border-radius:6px}#reportColumnNotes{max-height:12dvh;overflow:auto}#reportColumnNotes p{margin:6px 0;padding:8px 12px;background:#fff8d1;border-left:3px solid #fcd800}#reportColumnSamples{max-height:26dvh;overflow:auto}.report-pattern-pages{display:flex;gap:8px}.report-pattern-table th{position:sticky;top:0}.report-pattern-table th{width:25%}#columnStats>tbody>tr{cursor:pointer}#columnStats>tbody>tr:focus-visible{outline:3px solid #00a6ca;outline-offset:-3px}@media(prefers-reduced-motion:no-preference){.report-drawer[open]{animation:report-in .22s ease-out}@keyframes report-in{from{transform:translateX(40px);opacity:0}to{transform:translateX(0);opacity:1}}}</style>')
    with closing(sqlite3.connect(target)) as db:
        for c,count in summary['changed_cells_by_column'].items():
            if not count:continue
            yield ('<script type="application/json" class="column-pattern-data" data-column='+'"'+html.escape(c,quote=True)+'"'+'>[')
            first=True
            for i,(a,b,n) in enumerate(db.execute('SELECT a,b,n FROM patterns WHERE c=? ORDER BY n DESC,l,r',(c,)),1):
                if i%1000==0:notify(f'Writing {c} patterns: {i:,}')
                yield (('' if first else ',')+safe_json([a,b,n]));first=False
            yield (']</script>')
    yield ('<script type="application/json" id="report-comment-data">'+safe_json(summary.get('analysis_notes',[]))+'</script>')
    yield ('''<dialog id="reportColumnDrawer" class="report-drawer" aria-labelledby="reportColumnTitle"><header><h2 id="reportColumnTitle"></h2><button id="closeReportDrawer">Close</button></header><div id="reportColumnNotes"></div><p id="reportPatternRange"></p><div class="table-wrap"><table class="report-pattern-table"><thead><tr><th>File 1</th><th>File 2</th><th>Count</th><th>Share</th></tr></thead><tbody id="reportPatternRows"></tbody></table></div><div class="report-pattern-pages"><button id="reportPatternPrev">Previous patterns</button> <button id="reportPatternNext">Next patterns</button></div><h3>Mismatch key samples and comments</h3><div id="reportColumnSamples"></div></dialog><script>(function(){
const dialog=document.getElementById('reportColumnDrawer'),body=document.getElementById('reportPatternRows'),notes=JSON.parse(document.getElementById('report-comment-data').textContent);let pairs=[],page=0,denominator=0,opener;
const data=new Map([...document.querySelectorAll('.column-pattern-data')].map(n=>[n.dataset.column,n]));
function paint(){body.replaceChildren();for(const row of pairs.slice(page*50,page*50+50)){const tr=document.createElement('tr');for(const value of [row[0]===''?'(empty)':row[0],row[1]===''?'(empty)':row[1],row[2].toLocaleString(),(denominator?100*row[2]/denominator:0).toFixed(2)+'%']){const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);}document.getElementById('reportPatternRange').textContent=pairs.length?`${page*50+1}–${Math.min(page*50+50,pairs.length)} of ${pairs.length.toLocaleString()} patterns, ${denominator.toLocaleString()} mismatched cells`:'No mismatch patterns';document.getElementById('reportPatternPrev').disabled=page===0;document.getElementById('reportPatternNext').disabled=(page+1)*50>=pairs.length;}
for(const row of document.querySelectorAll('#columnStats>tbody>tr')){row.tabIndex=0;row.setAttribute('aria-haspopup','dialog');function open(event){if(event.target.closest('details'))return;const column=row.cells[0].textContent,node=data.get(column);if(!node)return;opener=row;pairs=JSON.parse(node.textContent);page=0;denominator=pairs.reduce((n,p)=>n+p[2],0);document.getElementById('reportColumnTitle').textContent='Mismatch patterns in '+column;const noteArea=document.getElementById('reportColumnNotes');noteArea.replaceChildren();for(const note of notes.filter(n=>n.column===column&&n.key===null)){const p=document.createElement('p');p.textContent=note.status+': '+note.comment;noteArea.append(p);}const samples=document.getElementById('reportColumnSamples');samples.replaceChildren();const original=row.querySelector('.mismatch-sample');if(original){const copy=original.cloneNode(true);copy.removeAttribute('id');copy.open=true;samples.append(copy);}paint();dialog.showModal();}row.addEventListener('click',open);row.addEventListener('keydown',e=>{if(e.target!==row)return;if(e.key==='Enter'||e.key===' '){e.preventDefault();open(e);}});}
document.getElementById('closeReportDrawer').onclick=()=>dialog.close();dialog.addEventListener('close',()=>opener?.focus());document.getElementById('reportPatternPrev').onclick=()=>{page--;paint();};document.getElementById('reportPatternNext').onclick=()=>{page++;paint();};})();</script>''')

