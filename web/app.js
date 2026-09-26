'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="app-token"]').content;
const files = {left:null,right:null};
const selected = new Set();
const ignoredColumns = new Set();
let keyPage = 0, ignorePage = 0;
const pageSize = 100;
let job = null, uploading = false, pollTimer = null;
let overrideRules=[], currentView='uploadPanel', hydratedId=null, historyOffset=0, sourceColumnOffset=0;
let logCursor=0, logText='', logJobId=null;
let viewingHistoryJob=false;
let keyContainers=[], selectedContainers=new Set();
const phaseLabels=['Files','Keys & scope','Value overrides','File preview','Pipeline & logs','Results'];
const views={uploadPanel:'navFiles',keysPanel:'navScope',overridesPanel:'navOverrides',sourcePanel:'navPreview',runningPanel:'navPipeline',results:'navResults',historyPanel:'navHistory',containersPanel:'navContainers',jsonPanel:'navJson'};
const number = value => Number(value).toLocaleString();
const bytes = value => value >= 1024**3 ? `${(value / 1024**3).toFixed(2)} GB` : value >= 1024**2 ? `${(value / 1024**2).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;
function showError(error) { $('error').textContent = error.message || String(error); $('error').hidden = false; }
function clearError() { $('error').hidden = true; }
async function api(path, body, method) {
  const response = await fetch(path, {method:method || (body === undefined ? 'GET' : 'POST'), headers:{'X-App-Token':token, 'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function endpoint(suffix='') { return `/api/jobs/${job.id}${suffix}`; }
function downloadUrl(name) { return endpoint(`/download/${name}`) + '?token=' + encodeURIComponent(token); }
function download(name) {
  const a = document.createElement('a'); a.href = downloadUrl(name); a.download = name; document.body.appendChild(a); a.click(); a.remove();
}
function setStep() { /* Navigation state is shown in the sidebar. */ }
function syncNavigation(){
  const ready=job?.state==='ready';
  $('navScope').disabled=!ready||uploading;
  $('navOverrides').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navPreview').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navPipeline').disabled=!job||!['queued','running','complete','error'].includes(job.state);
  $('navResults').disabled=job?.state!=='complete';
  $('navJson').disabled=$('navContainers').disabled=$('navHistory').disabled=$('navNew').disabled=uploading;
  $('reset').disabled=uploading;
}
function panel(name) {
  if(currentView!==name)window.scrollTo(0,0);
  currentView=name;
  for (const [id,nav] of Object.entries(views)){ $(id).hidden=id!==name; $(nav).classList.toggle('active',id===name); }
  $('sheetPanel').hidden=name!=='uploadPanel'||!['selecting_sheets','preparing'].includes(job?.state);
  $('reset').hidden=!job;
  const history=currentView==='historyPanel';
  const library=['containersPanel','jsonPanel'].includes(currentView);
  $('comparisonFlow').hidden=history||library;
  $('navNew').classList.toggle('active',!history&&!library&&!viewingHistoryJob);
  $('navHistory').classList.toggle('active',history||(!library&&viewingHistoryJob));
  $('flowTitle').textContent=viewingHistoryJob?'Saved comparison':'New comparison';
  const phase=Object.keys(views).indexOf(name);
  if(!history&&!library)$('phaseLabel').textContent=`Step ${phase+1} of 6 · ${phaseLabels[phase]}`;
  for(const [id,control] of Object.entries(views)){
    if(id!=='historyPanel'&&id!=='containersPanel'&&id!=='jsonPanel'){if(id===name)$(control).setAttribute('aria-current','step');else $(control).removeAttribute('aria-current');}
  }
  syncNavigation();
  if(name==='uploadPanel'&&job&&job.state!=='uploading'){
    for(const side of ['left','right']){$(`${side}File`).disabled=true;$(`${side}Name`).textContent=job.files[side].name;$(`${side}Size`).textContent=bytes(job.files[side].size)+' · uploaded';}
    $('upload').disabled=true;$('upload').textContent='Files uploaded';
  } else if(name==='uploadPanel'){
    for(const side of ['left','right'])$(`${side}File`).disabled=uploading;
    $('upload').textContent='Upload & continue →';$('upload').disabled=uploading||!files.left||!files.right;
  }
}
function configuration(){return {keys:[...selected],memory_mb:Number($('memory').value),sort_workers:Number($('sortWorkers').value),ignore_columns:[...ignoredColumns],ignore_keys:$('ignoreKeys').value,ignore_container_ids:[...selectedContainers],value_overrides:overrideRules};}
async function saveDraft(){if(job?.state==='ready')await api(endpoint('/config'),configuration());}
async function goView(name){
  clearError();await saveDraft();panel(name);
  if(name==='keysPanel'){renderKeys();renderIgnoredColumns();await loadContainers();}
  if(name==='containersPanel')await loadContainers();
  if(name==='overridesPanel'){renderOverrideColumns();renderOverrides();}
  if(name==='historyPanel')await loadHistory();
  if(name==='runningPanel')await refresh();
  if(name==='results'){renderSummary();renderExports();await preview();}
}
for(const [view,nav] of Object.entries(views))$(nav).addEventListener('click',()=>goView(view).catch(showError));
function choose(side, file) {
  if (uploading || (job && job.state!=='uploading')) return;
  if(file&&!/\.(csv|xlsx|xlsm)$/i.test(file.name)){showError('Choose CSV, .xlsx or .xlsm. Save older .xls files as .xlsx first.');return;}
  files[side] = file || null;
  $(`${side}Name`).textContent = file ? file.name : `Choose ${side==='left'?'original':'updated'} CSV or Excel`;
  $(`${side}Size`).textContent = file ? bytes(file.size) : 'Click to browse or drop a file here';
  $('upload').disabled = !files.left || !files.right;
}
for (const side of ['left','right']) {
  $(`${side}File`).addEventListener('change', event => choose(side,event.target.files[0]));
  const card = $(`${side}Card`);
  card.addEventListener('dragover', event => {event.preventDefault();card.classList.add('drag');});
  card.addEventListener('dragleave', () => card.classList.remove('drag'));
  card.addEventListener('drop', event => {event.preventDefault();card.classList.remove('drag');choose(side,event.dataTransfer.files[0]);});
}
function sendChunk(side, blob, offset, update) {
  return new Promise((resolve,reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', endpoint(`/files/${side}?offset=${offset}`));
    xhr.setRequestHeader('X-App-Token',token);
    xhr.timeout = 180000;
    xhr.upload.onprogress = event => update(event.loaded);
    xhr.onerror = () => reject(new Error('Upload connection interrupted. Try again to resume.'));
    xhr.ontimeout = () => reject(new Error('Upload timed out. Try again to resume.'));
    xhr.onload = () => {
      try { const result=JSON.parse(xhr.responseText); if(xhr.status>=400) reject(new Error(result.error)); else resolve(result); }
      catch {reject(new Error('Invalid upload response. Try again to resume.'));}
    };
    xhr.send(blob);
  });
}
$('upload').addEventListener('click', async () => {
  clearError(); uploading = true;syncNavigation(); $('upload').disabled = true; $('reset').disabled = true;
  for(const side of ['left','right']) $(`${side}File`).disabled = true;
  $('delimiter').disabled = $('encoding').disabled = true;
  try {
    if (!job) {
      job = await api('/api/jobs',{delimiter:$('delimiter').value==='tab'?'\t':$('delimiter').value,encoding:$('encoding').value,files:Object.fromEntries(['left','right'].map(side=>[side,{name:files[side].name,size:files[side].size}]))});
      localStorage.setItem('keywise-job',job.id);
    }
    for(const side of ['left','right']) {
      if(files[side].size!==job.files[side].size || files[side].name!==job.files[side].name) throw new Error('To resume, select the same files. Use New comparison to choose different files.');
    }
    $('uploadProgress').hidden = false;
    const total = files.left.size + files.right.size;
    for(const side of ['left','right']) {
      let offset = job.files[side].uploaded;
      while(offset < files[side].size) {
        const start = offset;
        const result = await sendChunk(side,files[side].slice(start,start+8*1024*1024),start,loaded=>{
          const completed = (side==='right'?files.left.size:0)+start+loaded;
          const percent = Math.min(100,100*completed/total);
          $('uploadText').textContent = `Uploading ${side} file · ${bytes(completed)} of ${bytes(total)}`;
          $('uploadPercent').textContent = `${percent.toFixed(1)}%`; $('uploadBar').value=percent;
        });
        offset = result.uploaded; job.files[side].uploaded = offset;
      }
    }
    $('uploadText').textContent = 'Checking column names…';
    job = await api(endpoint('/finalize'),{});
    currentView=job.state==='ready'?'keysPanel':'uploadPanel';await refresh();if(job.state==='selecting_sheets')$('sheetPanel').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(error) {
    showError(error);
    if(job) {try {job=await api(endpoint());} catch { /* Keep current job to allow retry. */ }}
  } finally {
    uploading=false; $('upload').disabled=job&&job.state!=='uploading'; $('reset').disabled=false; $('reset').hidden=!job;
    for(const side of ['left','right']) $(`${side}File`).disabled=job&&job.state!=='uploading';
    if(!job) $('delimiter').disabled=$('encoding').disabled=false;syncNavigation();
  }
});
function renderKeys() {
  const matches=job.columns.filter(name=>name.toLowerCase().includes($('keySearch').value.toLowerCase()));
  keyPage=Math.min(keyPage,Math.max(0,Math.ceil(matches.length/pageSize)-1));
  $('keyList').replaceChildren();
  for(const column of matches.slice(keyPage*pageSize,(keyPage+1)*pageSize)) {
    const label=document.createElement('label');label.className='key-option';
    const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(column);input.disabled=ignoredColumns.has(column);
    input.addEventListener('change',()=>{if(input.checked)selected.add(column);else selected.delete(column);renderSelected();renderIgnoredColumns();});
    const name=document.createElement('span');name.textContent=column;label.append(input,name);$('keyList').append(label);
  }
  $('keyRange').textContent=rangeLabel(matches.length,keyPage);
  $('keyPrev').disabled=keyPage===0;$('keyNext').disabled=(keyPage+1)*pageSize>=matches.length;
  $('columnCount').textContent=`${number(job.columns.length)} columns`;renderSelected();
}
function rangeLabel(total,page){return total?`${page*pageSize+1}–${Math.min(total,(page+1)*pageSize)} of ${number(total)}`:'No matching columns';}
function renderIgnoredColumns(){
  const matches=job.columns.filter(name=>name.toLowerCase().includes($('ignoreColumnSearch').value.toLowerCase()));
  ignorePage=Math.min(ignorePage,Math.max(0,Math.ceil(matches.length/pageSize)-1));
  $('ignoreColumnList').replaceChildren();
  for(const column of matches.slice(ignorePage*pageSize,(ignorePage+1)*pageSize)){
    const label=document.createElement('label');label.className='key-option';
    const input=document.createElement('input');input.type='checkbox';input.checked=ignoredColumns.has(column);input.disabled=selected.has(column);
    input.setAttribute('aria-label',`Ignore column ${column}`);
    input.addEventListener('change',()=>{if(input.checked)ignoredColumns.add(column);else ignoredColumns.delete(column);renderKeys();renderIgnoredColumns();});
    const name=document.createElement('span');name.textContent=column+(selected.has(column)?' (key)':'');label.append(input,name);$('ignoreColumnList').append(label);
  }
  $('ignoreRange').textContent=rangeLabel(matches.length,ignorePage);
  $('ignorePrev').disabled=ignorePage===0;$('ignoreNext').disabled=(ignorePage+1)*pageSize>=matches.length;
  $('ignoredColumnCount').textContent=`${ignoredColumns.size} ignored`;
  $('ignoredColumnSelection').textContent=ignoredColumns.size?`Ignored: ${[...ignoredColumns].join(', ')}`:'No columns ignored';
}
function renderSelected(){
  $('selectedKeys').textContent=selected.size?`Selected keys, in order: ${[...selected].join(', ')}`:'No key columns selected';
  $('compare').disabled=$('scopeNext').disabled=!selected.size;
  $('ignoreKeysHint').textContent=selected.size>1?`Composite key order: ${[...selected].join(', ')}. Enter JSON tuples, for example [["001","A"],["002","B"]] for two keys.`:'Enter comma-separated key values, for example 001, 002, 003. Put quotes around values containing commas or leading spaces.';
  $('ignoreKeys').placeholder=selected.size>1?'[["001","A"],["002","B"]]':'001, 002, 003';
}
$('keySearch').addEventListener('input',()=>{keyPage=0;renderKeys();});
$('ignoreColumnSearch').addEventListener('input',()=>{ignorePage=0;renderIgnoredColumns();});
$('keyPrev').addEventListener('click',()=>{keyPage--;renderKeys();});
$('keyNext').addEventListener('click',()=>{keyPage++;renderKeys();});
$('ignorePrev').addEventListener('click',()=>{ignorePage--;renderIgnoredColumns();});
$('ignoreNext').addEventListener('click',()=>{ignorePage++;renderIgnoredColumns();});
$('compare').addEventListener('click',async()=>{
  clearError();$('compare').disabled=true;
  try {job=await api(endpoint('/start'),configuration());panel('runningPanel');await refresh();}
  catch(error){showError(error);$('compare').disabled=false;}
});
function renderSummary() {
  const s=job.summary;
  const changes=Object.entries(s.changed_cells_by_column).filter(([,count])=>count>0).sort((a,b)=>b[1]-a[1]);
  $('resultTitle').textContent=s.changed_rows||s.left_only||s.right_only?'Your comparison is ready.':'Both files match within the selected scope.';
  $('resultSubtitle').textContent=`${number(s.left_rows)} left rows · ${number(s.right_rows)} right rows · Keys: ${s.keys.join(', ')}`;
  $('scopeSummary').textContent=`${number(s.ignored_columns?.length||0)} columns ignored · ${number(s.left_excluded_rows||0)} left rows and ${number(s.right_excluded_rows||0)} right rows excluded by key. ${number(s.value_overrides?.length||0)} value overrides; ${number(s.override_equivalent_cells||0)} unequal cells accepted by rules. Counts refer to the selected scope.`;
  $('duration').textContent=`${s.elapsed_seconds.toFixed(s.elapsed_seconds<1?3:1)} seconds`;
  $('metrics').replaceChildren();
  for(const [label,value,style] of [['Equal rows',s.equal_rows,'accent'],['Changed rows',s.changed_rows,'amber'],['Changed cells',s.changed_cells,'amber'],['Left-only keys',s.left_only,''],['Right-only keys',s.right_only,'']]) {
    const card=document.createElement('div');card.className=`metric ${style}`;
    const title=document.createElement('small');title.textContent=label;
    const count=document.createElement('strong');count.textContent=number(value);card.append(title,count);$('metrics').append(card);
  }
  $('changedColumnCount').textContent=`${changes.length} columns`;$('columnSummary').replaceChildren();
  for(const [name,count] of changes) {
    const row=document.createElement('div');row.className='column-row';
    const label=document.createElement('span');label.textContent=name;
    const value=document.createElement('strong');value.textContent=number(count);row.append(label,value);$('columnSummary').append(row);
  }
  if(!changes.length)$('columnSummary').textContent='No changed values for matching keys.';
  for(const [id,file] of [['csvDownload','differences.csv'],['leftDownload','left_only.csv'],['rightDownload','right_only.csv'],['summaryDownload','summary.html']]) $(id).href=downloadUrl(file);
}
async function preview() {
  const result=await api(endpoint('/preview')+'?category='+$('previewCategory').value);
  const table=$('previewTable');table.replaceChildren();
  const head=document.createElement('thead');const hr=document.createElement('tr');
  for(const name of result.headers){const th=document.createElement('th');th.textContent=name;hr.append(th);}head.append(hr);table.append(head);
  const body=document.createElement('tbody');
  for(const values of result.rows){const tr=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);}
  if(!result.rows.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=result.headers.length;td.textContent='No records in this category.';tr.append(td);body.append(tr);}table.append(body);
}
$('previewCategory').addEventListener('change',()=>preview().catch(showError));
function renderExports() {
  for(const kind of ['excel','html']) {
    const value=job.exports[kind];const button=$(kind);const status=$(`${kind}Status`);
    button.disabled=!!value&&['queued','running'].includes(value.state);
    if(!value)status.textContent='';
    else if(value.state==='complete')status.textContent=`Ready to download · ${bytes(value.size)}`;
    else if(value.state==='error')status.textContent=`Export failed: ${value.error}`;
    else status.textContent=value.message||'Queued for export…';
  }
}
for(const kind of ['excel','html']) $(kind).addEventListener('click',async()=>{
  clearError();
  if(job.exports[kind]?.state==='complete')return download(kind==='excel'?'mismatches.xlsx':'html.zip');
  $(kind).disabled=true;
  try{await api(endpoint(`/export/${kind}`),{});await refresh();}catch(error){showError(error);$(kind).disabled=false;}
});
let renderedId=null;
function hydrate(){
  if(hydratedId===job.id)return;
  selected.clear();ignoredColumns.clear();
  const draft=job.state==='ready'?(job.draft||{}):job;
  for(const key of Array.isArray(draft.keys)?draft.keys:[])selected.add(key);
  for(const column of Array.isArray(draft.ignore_columns)?draft.ignore_columns:[])ignoredColumns.add(column);
  selectedContainers=new Set(draft.ignore_container_ids||[]);
  loadContainers().catch(showError);
  overrideRules=Array.isArray(draft.value_overrides)?draft.value_overrides:[];
  $('ignoreKeys').value=typeof draft.ignore_keys==='string'?draft.ignore_keys:'';
  $('memory').value=[64,128,256,512,1024,2048,4096,8192].includes(draft.memory_mb)?String(draft.memory_mb):'4096';
  $('sortWorkers').value=[1,2].includes(draft.sort_workers)?String(draft.sort_workers):job.state==='ready'?'2':'1';
  keyPage=ignorePage=sourceColumnOffset=0;hydratedId=job.id;
}
async function refresh() {
  clearTimeout(pollTimer);
  const previousState=job.state;
  const requestedId=job.id;
  const latest=await api(endpoint());
  if(job.id!==requestedId)return;
  job=latest;hydrate();
  $('reset').hidden=false;syncNavigation();
  if(['selecting_sheets','preparing'].includes(job.state)){
    if(currentView==='uploadPanel'){panel('uploadPanel');renderSheetSelection();}
    if(job.state==='preparing')pollTimer=setTimeout(()=>refresh().catch(pollError),1500);
  } else if(job.state==='ready'){
    if(previousState==='preparing')await loadContainers();
    if(currentView==='uploadPanel'||currentView==='keysPanel'){panel('keysPanel');renderKeys();renderIgnoredColumns();}
    $('diskInfo').textContent=`${bytes(job.free_disk_bytes)} free disk space. Allow room for scratch files and reports.`;
  } else if(['running','queued'].includes(job.state)){
    if(currentView==='runningPanel')await renderPipeline();
    pollTimer=setTimeout(()=>refresh().catch(pollError),1500);
  } else if(job.state==='complete'){
    if(currentView==='runningPanel'&&['running','queued'].includes(previousState)){await renderPipeline();panel('results');}
    if(currentView==='results'){
      renderExports();
      if(renderedId!==job.id){renderSummary();await preview();renderedId=job.id;}
    } else if(currentView==='runningPanel')await renderPipeline();
    if(Object.values(job.exports).some(value=>['queued','running'].includes(value.state)))pollTimer=setTimeout(()=>refresh().catch(pollError),1500);
  } else if(job.state==='error'){
    if(!['historyPanel','containersPanel','jsonPanel'].includes(currentView)){panel('runningPanel');await renderPipeline();showError(job.error);}
  } else if(!['historyPanel','containersPanel','jsonPanel'].includes(currentView)){
    panel('uploadPanel');$('delimiter').disabled=$('encoding').disabled=true;$('delimiter').value=job.delimiter==='\t'?'tab':job.delimiter;$('encoding').value=job.encoding;
  }
}
function pollError(error){showError(error);pollTimer=setTimeout(()=>refresh().catch(pollError),5000);}
$('reset').addEventListener('click',()=>{localStorage.removeItem('keywise-job');location.reload();});
(async()=>{
  const id=localStorage.getItem('keywise-job');
  if(id&&/^[a-f0-9]{32}$/.test(id)){
    job={id};
    try{job=await api(endpoint());currentView=job.state==='complete'?'results':['running','queued','error'].includes(job.state)?'runningPanel':job.state==='ready'?'keysPanel':'uploadPanel';panel(currentView);await refresh();if(job.state==='uploading')showError(`Upload not finished. Reselect ${job.files.left.name} and ${job.files.right.name} to resume, or start a new comparison.`);}
    catch(error){showError(error);$('reset').hidden=false;}
  }
})();

$('scopeNext').addEventListener('click',()=>goView('overridesPanel').catch(showError));
$('overrideBack').addEventListener('click',()=>goView('keysPanel').catch(showError));
function renderOverrideColumns(){
  const previous=$('overrideColumn').value;
  const search=$('overrideSearch').value.toLowerCase();
  const columns=(job?.columns||[]).filter(c=>!selected.has(c)&&!ignoredColumns.has(c)&&c.toLowerCase().includes(search));
  $('overrideColumn').replaceChildren();
  for(const column of columns.slice(0,100)){const option=document.createElement('option');option.value=column;option.textContent=column;$('overrideColumn').append(option);}
  if(columns.slice(0,100).includes(previous))$('overrideColumn').value=previous;
  $('addOverride').disabled=!columns.length;
}
$('overrideSearch').addEventListener('input',renderOverrideColumns);
function renderOverrides(){
  const locked=job.state!=='ready';
  document.querySelector('.rule-builder').hidden=locked;
  $('overrideBack').hidden=$('compare').hidden=locked;
  $('overrideTable').replaceChildren();
  const row=document.createElement('tr');
  for(const name of ['Column','File 1 value','File 2 value','Action']){const th=document.createElement('th');th.textContent=name;row.append(th);}
  const head=document.createElement('thead');head.append(row);$('overrideTable').append(head);
  const body=document.createElement('tbody');
  overrideRules.forEach((rule,index)=>{const row=document.createElement('tr');for(const value of [rule.column,rule.left,rule.right]){const cell=document.createElement('td');cell.textContent=value===''?'(empty field)':value;row.append(cell);}const cell=document.createElement('td');const remove=document.createElement('button');remove.className='subtle';remove.textContent=locked?'Saved rule':'Remove';remove.disabled=locked;remove.setAttribute('aria-label',`Remove rule ${index+1}`);remove.addEventListener('click',()=>{overrideRules.splice(index,1);renderOverrides();saveDraft().catch(showError);});cell.append(remove);row.append(cell);body.append(row);});
  $('overrideTable').append(body);$('overrideCount').textContent=`${overrideRules.length} rules`;$('overrideEmpty').hidden=!!overrideRules.length;$('compare').disabled=!selected.size;
}
$('addOverride').addEventListener('click',async()=>{
  clearError();const rule={column:$('overrideColumn').value,left:$('overrideLeft').value,right:$('overrideRight').value};
  if(!rule.column)return;
  if(!overrideRules.some(r=>r.column===rule.column&&r.left===rule.left&&r.right===rule.right))overrideRules.push(rule);
  renderOverrides();try{await saveDraft();}catch(e){showError(e);}
});
function drawTable(id,headers,rows){
  const table=$(id);table.replaceChildren();const head=document.createElement('thead');const tr=document.createElement('tr');
  for(const name of headers){const th=document.createElement('th');th.textContent=name;tr.append(th);}head.append(tr);table.append(head);
  const body=document.createElement('tbody');for(const values of rows){const row=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=String(value);row.append(td);}body.append(row);}table.append(body);
}
async function loadSource(){
  clearError();const rows=Number($('sourceRows').value);if(!Number.isInteger(rows)||rows<1||rows>1000)throw new Error('Preview rows must be between 1 and 1,000.');
  const id=job.id;$('loadSource').disabled=true;
  try{
    const results=await Promise.all(['left','right'].map(side=>api(endpoint('/source-preview')+`?side=${side}&rows=${rows}&column_offset=${sourceColumnOffset}`)));
    if(job.id!==id)return;
    ['Left','Right'].forEach((side,index)=>{drawTable('source'+side+'Table',results[index].headers,results[index].rows);$('source'+side+'Title').textContent=`File ${index+1}: ${job.files[side.toLowerCase()].name}${job.files[side.toLowerCase()].sheet?' · '+job.files[side.toLowerCase()].sheet:''}`;});
    $('sourceStatus').textContent=`Showing ${results[0].rows.length} / ${results[1].rows.length} rows from file 1 / file 2. Columns ${sourceColumnOffset+1}–${Math.min(sourceColumnOffset+20,results[0].total_columns)} of ${results[0].total_columns}. Values limited to 500 characters. Fewer rows can mean end of file or the 2 MB preview limit.`;
    $('sourcePrev').disabled=sourceColumnOffset===0;$('sourceNext').disabled=sourceColumnOffset+20>=results[0].total_columns;
  }finally{$('loadSource').disabled=false;}
}
$('loadSource').addEventListener('click',()=>loadSource().catch(showError));
$('sourcePrev').addEventListener('click',()=>{sourceColumnOffset=Math.max(0,sourceColumnOffset-20);loadSource().catch(showError);});
$('sourceNext').addEventListener('click',()=>{if(sourceColumnOffset+20<job.columns.length)sourceColumnOffset+=20;loadSource().catch(showError);});
async function renderPipeline(){
  const stages=[['validate','Validate configuration'],...(job.sort_workers===2?[['sort','Read & sort both files']]:[['left','Read & sort file 1'],['right','Read & sort file 2']]),['compare','Compare matching keys'],['reports','Write results'],['complete','Complete']];
  const stage=job.state==='complete'?'complete':job.progress?.stage||'validate';const index=stages.findIndex(([id])=>id===stage);
  $('phase').textContent=job.state==='complete'?'Comparison complete':job.state==='error'?'Comparison failed':job.state==='queued'?'Queued':job.progress?.phase||'Starting worker';
  $('processed').textContent=job.state==='error'?job.error:job.progress?.rows!==undefined?`${number(job.progress.rows)} rows processed in the current phase`:'Stages update as the worker processes the files.';
  $('pipelineState').textContent=job.state;$('pipelineStages').replaceChildren();
  stages.forEach(([id,label],i)=>{const item=document.createElement('li');const state=job.state==='queued'?'pending':job.state==='complete'||i<index?'done':i===index?(job.state==='error'?'failed':'active'):'pending';item.className=state;item.textContent=`${state==='done'?'✓':state==='failed'?'!':i+1}  ${label}`;$('pipelineStages').append(item);});
  if(logJobId!==job.id){logJobId=job.id;logCursor=0;logText='';}
  const id=job.id;const log=await api(endpoint('/logs')+'?cursor='+logCursor);if(job.id!==id)return;
  logCursor=log.cursor;logText=(logText+log.text).split('\n').slice(-1000).join('\n');$('consoleLog').textContent=logText||'Waiting for worker output…';$('consoleLog').scrollTop=$('consoleLog').scrollHeight;
  $('logDownload').href=downloadUrl('run.log');
  if(log.text.length>=60000)pollTimer=setTimeout(()=>refresh().catch(pollError),300);
}
async function loadHistory(){
  const result=await api('/api/jobs?offset='+historyOffset);if(!['historyPanel','containersPanel','jsonPanel'].includes(currentView))return;
  const table=$('historyTable');table.replaceChildren();
  const header=document.createElement('tr');for(const label of ['Created','Files','Status','Changed cells','Action']){const th=document.createElement('th');th.textContent=label;header.append(th);}const head=document.createElement('thead');head.append(header);table.append(head);
  const body=document.createElement('tbody');
  for(const item of result.jobs){const row=document.createElement('tr');for(const text of [new Date(item.created*1000).toLocaleString(),`${item.files.left.name}${item.files.left.sheet?' ['+item.files.left.sheet+']':''} ↔ ${item.files.right.name}${item.files.right.sheet?' ['+item.files.right.sheet+']':''}`,item.state,item.changed_cells==null?'—':number(item.changed_cells)]){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}const cell=document.createElement('td');const button=document.createElement('button');button.className='subtle';button.textContent='Open';button.setAttribute('aria-label',`Open comparison ${item.id}`);button.addEventListener('click',()=>openJob(item.id).catch(showError));cell.append(button);row.append(cell);body.append(row);}table.append(body);
  $('historyRange').textContent=result.total?`${historyOffset+1}–${Math.min(historyOffset+25,result.total)} of ${result.total} saved jobs`:'No comparisons yet.';$('historyPrev').disabled=historyOffset===0;$('historyNext').disabled=historyOffset+25>=result.total;
}
async function openJob(id){
  clearTimeout(pollTimer);await saveDraft();clearError();viewingHistoryJob=true;job=await api('/api/jobs/'+id);localStorage.setItem('keywise-job',id);renderedId=null;hydratedId=null;hydrate();
  for(const side of ['left','right']){files[side]=null;$(`${side}File`).value='';$(`${side}Name`).textContent=job.files[side].name;$(`${side}Size`).textContent=bytes(job.files[side].size);}
  $('sourceLeftTable').replaceChildren();$('sourceRightTable').replaceChildren();
  $('sourceLeftTitle').textContent='File 1';$('sourceRightTitle').textContent='File 2';
  $('sourceStatus').textContent='Choose a row limit, then load this comparison’s source preview.';
  panel(job.state==='complete'?'results':['queued','running','error'].includes(job.state)?'runningPanel':job.state==='ready'?'keysPanel':'uploadPanel');
  await refresh();
  if(job.state==='uploading')showError(`Reselect ${job.files.left.name} and ${job.files.right.name} to resume this upload.`);
}
$('historyRefresh').addEventListener('click',()=>loadHistory().catch(showError));
$('historyPrev').addEventListener('click',()=>{historyOffset=Math.max(0,historyOffset-25);loadHistory().catch(showError);});
$('historyNext').addEventListener('click',()=>{historyOffset+=25;loadHistory().catch(showError);});

function applySidebar(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  $('sidebarToggle').textContent=collapsed?'›':'‹';
  $('sidebarToggle').setAttribute('aria-expanded',String(!collapsed));
  $('sidebarToggle').setAttribute('aria-label',collapsed?'Expand sidebar':'Collapse sidebar');
  $('sidebarToggle').title=collapsed?'Expand sidebar':'Collapse sidebar';
}
applySidebar(localStorage.getItem('tu-sidebar-collapsed')==='true');
$('sidebarToggle').addEventListener('click',()=>{
  const collapsed=!document.body.classList.contains('sidebar-collapsed');
  applySidebar(collapsed);localStorage.setItem('tu-sidebar-collapsed',String(collapsed));
});
$('navNew').addEventListener('click',async()=>{
  try{
    if(['containersPanel','jsonPanel'].includes(currentView)&&!viewingHistoryJob){await goView(job?.state==='ready'?'keysPanel':job?.state==='complete'?'results':job&&['running','queued','error'].includes(job.state)?'runningPanel':'uploadPanel');return;}
    if(currentView==='historyPanel'||viewingHistoryJob){await saveDraft();localStorage.removeItem('keywise-job');location.reload();}
  }catch(error){showError(error);}
});

async function loadContainers(){
  const data=await api('/api/key-containers');keyContainers=data.containers;
  $('containerLibrary').replaceChildren();$('containerChoices').replaceChildren();
  for(const item of keyContainers){
    const card=document.createElement('article');card.className='container-card';
    const title=document.createElement('h4');title.textContent=item.name;
    const reason=document.createElement('p');reason.textContent=item.reason;
    const details=document.createElement('details');const caption=document.createElement('summary');caption.textContent=`View keys · ${item.key_width} key column(s)`;
    const values=document.createElement('pre');values.textContent=item.values;details.append(caption,values);card.append(title,reason,details);$('containerLibrary').append(card);
    const label=document.createElement('label');label.className='container-option';const check=document.createElement('input');check.type='checkbox';check.checked=selectedContainers.has(item.id);check.disabled=job?.state!=='ready';
    check.addEventListener('change',()=>{if(check.checked)selectedContainers.add(item.id);else selectedContainers.delete(item.id);saveDraft().catch(showError);});
    const text=document.createElement('span');text.textContent=`${item.name} — ${item.reason}`;label.append(check,text);$('containerChoices').append(label);
  }
  if(!keyContainers.length){$('containerLibrary').textContent='No containers yet.';$('containerChoices').textContent='No saved containers. Create one in Ignore key containers.';}
}
$('saveContainer').addEventListener('click',async()=>{
  clearError();$('saveContainer').disabled=true;
  try{await api('/api/key-containers',{name:$('containerName').value,reason:$('containerReason').value,values:$('containerValues').value,key_width:Number($('containerWidth').value)});$('containerStatus').textContent='Container saved. Select it in Keys & scope to apply it to a comparison.';for(const id of ['containerName','containerReason','containerValues'])$(id).value='';await loadContainers();}catch(error){showError(error);}finally{$('saveContainer').disabled=false;}
});

function renderSheetSelection(){
  $('sheetPanel').hidden=false;
  for(const side of ['left','right']){
    const item=job.files[side], control=$(side+'Sheet');
    $(side+'SheetLabel').hidden=item.format!=='excel';
    if(control.dataset.job!==job.id){
      control.replaceChildren();
      for(const sheet of item.sheets||[]){const option=document.createElement('option');option.value=sheet.name;option.textContent=sheet.name+(sheet.state!=='visible'?' (hidden)':'');control.append(option);}
      control.dataset.job=job.id;
    }
    control.disabled=job.state==='preparing';
    if(item.sheet)control.value=item.sheet;
  }
  $('prepareSheets').disabled=job.state==='preparing';
  $('sheetStatus').textContent=job.state==='preparing'?job.preparation_message:'Sheet names loaded. Worksheet rows have not been read yet.';
}
$('prepareSheets').addEventListener('click',async()=>{
  clearError();$('prepareSheets').disabled=true;
  try{job=await api(endpoint('/select-sheets'),{left:$('leftSheet').value,right:$('rightSheet').value});await refresh();}catch(error){showError(error);$('prepareSheets').disabled=false;}
});

let jsonRules=[], jsonResult=null, jsonBusy=false;
function invalidateJson(){jsonResult=null;$('jsonResult').hidden=true;$('jsonStatus').textContent='';}
for(const id of ['jsonLeft','jsonRight','jsonDefault'])$(id).addEventListener('input',invalidateJson);
for(const side of ['Left','Right'])$('json'+side+'File').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;clearError();invalidateJson();
  try{if(file.size>5*1024*1024)throw new Error('Each JSON input must be at most 5 MiB.');$('json'+side).value=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}catch(error){showError(error);}finally{event.target.value='';}
});
$('jsonRuleMode').addEventListener('change',()=>{$('jsonRuleField').disabled=$('jsonRuleMode').value!=='keyed';});
function renderJsonRules(){
  $('jsonRules').replaceChildren();
  jsonRules.forEach((rule,index)=>{const row=document.createElement('div');row.className='container-option';const label=document.createElement('span');label.textContent=rule.path+' · '+({ordered:'Preserve order',unordered:'Ignore order',keyed:'Match by field'}[rule.mode])+(rule.mode==='keyed'?' · '+rule.field:'');const remove=document.createElement('button');remove.className='subtle';remove.textContent='Remove';remove.setAttribute('aria-label','Remove array rule '+rule.path);remove.disabled=jsonBusy;remove.addEventListener('click',()=>{jsonRules.splice(index,1);invalidateJson();renderJsonRules();});row.append(label,remove);$('jsonRules').append(row);});
}
$('jsonAddRule').addEventListener('click',()=>{
  clearError();const path=$('jsonRulePath').value.trim(),mode=$('jsonRuleMode').value,field=$('jsonRuleField').value;
  if(!path.startsWith('$')||(mode==='keyed'&&!field)){showError('Enter an array path beginning with $ and a match field when applicable.');return;}
  if(jsonRules.some(rule=>rule.path===path)){showError('Remove the existing rule for this path before adding a replacement.');return;}
  jsonRules.push({path,mode,field:mode==='keyed'?field:''});invalidateJson();renderJsonRules();$('jsonRulePath').value='';
});
$('jsonCompare').addEventListener('click',async()=>{
  clearError();invalidateJson();jsonBusy=true;
  const controls=['jsonLeft','jsonRight','jsonLeftFile','jsonRightFile','jsonDefault','jsonRulePath','jsonRuleMode','jsonRuleField','jsonAddRule','jsonCompare'];
  controls.forEach(id=>$(id).disabled=true);renderJsonRules();$('jsonStatus').textContent='Comparing JSON…';
  try{
    for(const id of ['jsonLeft','jsonRight'])if(new Blob([$(id).value]).size>5*1024*1024)throw new Error('Each JSON input must be at most 5 MiB.');
    jsonResult=await api('/api/json-compare',{left:$('jsonLeft').value,right:$('jsonRight').value,default_order:$('jsonDefault').value,rules:jsonRules});
    $('jsonResult').hidden=false;$('jsonResultTitle').textContent=jsonResult.equal?'JSON documents match':'JSON differences found';
    $('jsonCounts').textContent=`${jsonResult.counts.changed} changed · ${jsonResult.counts.added} added · ${jsonResult.counts.removed} removed`;
    $('jsonWarnings').textContent=jsonResult.warnings.join(' · ');
    drawTable('jsonDiffTable',['Difference','File 1 path','File 2 path','File 1 value','File 2 value'],jsonResult.differences.slice(0,200).map(d=>[d.kind,d.left_path??'—',d.right_path??'—',d.left_value===null?'(missing)':d.left_value.slice(0,1000),d.right_value===null?'(missing)':d.right_value.slice(0,1000)]));
    $('jsonStatus').textContent='Comparison complete. Source JSON is unchanged.';
  }catch(error){$('jsonStatus').textContent='Comparison failed.';showError(error);}finally{jsonBusy=false;controls.forEach(id=>$(id).disabled=false);$('jsonRuleField').disabled=$('jsonRuleMode').value!=='keyed';renderJsonRules();}
});
function saveJsonReport(content,type,name){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('jsonDownload').addEventListener('click',()=>{if(jsonResult)saveJsonReport(JSON.stringify(jsonResult,null,2),'application/json','json-comparison.json');});
$('jsonHtml').addEventListener('click',()=>{
  if(!jsonResult)return;
  const escape=value=>String(value??'(missing)').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const rows=jsonResult.differences.map(d=>'<tr>'+[d.kind,d.left_path,d.right_path,d.left_value,d.right_value].map(v=>'<td>'+escape(v)+'</td>').join('')+'</tr>').join('');
  const html='<!doctype html><html lang="en"><meta charset="utf-8"><title>JSON comparison</title><style>body{font:15px system-ui;color:#004364;margin:32px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccdce3;padding:12px;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e6f6fa}pre{white-space:pre-wrap}</style><h1>JSON comparison</h1><p>'+escape($('jsonResultTitle').textContent)+' · '+escape($('jsonCounts').textContent)+'</p><h2>Array settings</h2><pre>'+escape(JSON.stringify({default_order:jsonResult.default_order,rules:jsonResult.rules,warnings:jsonResult.warnings},null,2))+'</pre><p>Paths use original array positions. Unordered blocks without matching fields are reported as removed/added. Duplicates remain significant.</p><table><tr><th>Difference</th><th>File 1 path</th><th>File 2 path</th><th>File 1 value</th><th>File 2 value</th></tr>'+rows+'</table></html>';
  saveJsonReport(html,'text/html','json-comparison.html');
});
