'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="app-token"]').content;
const maxSortMb=Number(document.querySelector('meta[name="max-sort-mb"]').content)||4096;
for(const option of $('memory').options)option.disabled=Number(option.value)>maxSortMb;
const files = {left:null,right:null};
const selected = new Set();
const ignoredColumns = new Set();
let keyPage = 0, ignorePage = 0;
const pageSize = 100;
let job = null, uploading = false, pollTimer = null;
let pauseUploadRequested=false, lastUploadPaint=0;
let overrideRules=[], currentView='uploadPanel', hydratedId=null, historyOffset=0, sourceColumnOffset=0;
let logCursor=0, logText='', logJobId=null;
let viewingHistoryJob=false;
let keyContainers=[], selectedContainers=new Set();
const phaseLabels=['Files','Column headers','Keys & scope','Value overrides','File preview','Pipeline & logs','Results','Analysis'];
const views={uploadPanel:'navFiles',headersPanel:'navHeaders',keysPanel:'navScope',overridesPanel:'navOverrides',sourcePanel:'navPreview',runningPanel:'navPipeline',results:'navResults',analysisPanel:'navAnalysis',historyPanel:'navHistory',containersPanel:'navContainers',jsonPanel:'navJson',docsPanel:'navDocs',storagePanel:'navStorage',profilesPanel:'navProfiles',settingsPanel:'navSettings'};
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
function download(name,identity=job.id) {
  const a = document.createElement('a'); a.href = `/api/jobs/${identity}/download/${name}?token=${encodeURIComponent(token)}`; a.download = name; document.body.appendChild(a); a.click(); a.remove();
}
function setStep() { /* Navigation state is shown in the sidebar. */ }
function syncNavigation(){
  const ready=job?.state==='ready';
  $('navHeaders').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navScope').disabled=!ready||uploading||job.headers_reviewed===false;
  $('navOverrides').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navPreview').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navPipeline').disabled=!job||!['queued','running','complete','error','cancelled'].includes(job.state);
  if(ready&&job.headers_reviewed===false){$('navOverrides').disabled=$('navPreview').disabled=true;}
  $('cancelComparison').disabled=!['running','queued'].includes(job?.state);
  $('navResults').disabled=$('navAnalysis').disabled=job?.state!=='complete';
  $('navJson').disabled=$('navContainers').disabled=$('navHistory').disabled=$('navNew').disabled=false;
  $('reset').disabled=uploading;
}
function panel(name) {
  if(currentView!==name)window.scrollTo(0,0);
  currentView=name;
  $('mainWorkspace').classList.toggle('docs-view',name==='docsPanel');
  for (const [id,nav] of Object.entries(views)){ $(id).hidden=id!==name; $(nav).classList.toggle('active',id===name); }
  $('sheetPanel').hidden=name!=='uploadPanel'||!['selecting_sheets','preparing'].includes(job?.state);
  $('reset').hidden=!job;
  const history=currentView==='historyPanel';
  const library=['containersPanel','jsonPanel','docsPanel','storagePanel','profilesPanel','settingsPanel'].includes(currentView);
  $('comparisonFlow').hidden=history||library;
  $('navNew').classList.toggle('active',!history&&!library&&!viewingHistoryJob);
  $('navHistory').classList.toggle('active',history||(!library&&viewingHistoryJob));
  for(const id of ['navNew','navJson','navContainers','navHistory','navDocs','navStorage','navProfiles','navSettings']){if($(id).classList.contains('active'))$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');}
  if(typeof closeSidebarDrawer==='function')closeSidebarDrawer();
  $('flowTitle').textContent=viewingHistoryJob?'Saved comparison':'New comparison';
  const phase=Object.keys(views).indexOf(name);
  if(!history&&!library)$('phaseLabel').textContent=`Step ${phase+1} of 8 · ${phaseLabels[phase]}`;
  for(const [id,control] of Object.entries(views)){
    if(id!=='historyPanel'&&id!=='containersPanel'&&id!=='jsonPanel'&&id!=='docsPanel'&&id!=='storagePanel'&&id!=='profilesPanel'&&id!=='settingsPanel'){if(id===name)$(control).setAttribute('aria-current','step');else $(control).removeAttribute('aria-current');}
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
function configuration(){return {duplicate_policy:$('duplicatePolicy').value,keys:[...selected],ignore_columns:[...ignoredColumns],ignore_keys:$('ignoreKeys').value,ignore_container_ids:[...selectedContainers],value_overrides:overrideRules,comparison_rules:valueRules};}
async function saveDraft(){if(job?.state==='ready')await api(endpoint('/config'),configuration());}
async function goView(name){
  if(uploading&&name!=='uploadPanel'&&!['jsonPanel','containersPanel','historyPanel','docsPanel'].includes(name))return;
  clearError();await saveDraft();
  if(headerDirty&&headerDraftJob===job?.id&&['keysPanel','overridesPanel','sourcePanel'].includes(name))await applyHeaders();
  panel(name);
  if(name==='settingsPanel')await loadSettings();
  if(name==='profilesPanel')await loadProfiles();
  if(name==='storagePanel')await loadStorage();
  if(name==='analysisPanel')await openAnalysis();
  if(name==='headersPanel'){renderHeaders();await loadProfiles();}
  if(name==='keysPanel'){renderKeys();renderIgnoredColumns();await loadContainers();await loadProfiles();}
  if(name==='containersPanel')await loadContainers();
  if(name==='overridesPanel'){renderOverrideColumns();renderOverrides();renderValueRules();}
  if(name==='historyPanel')await loadHistory();
  if(name==='runningPanel'){await refresh();await loadDiagnostics();}
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
  if(uploading)return;
  clearError(); uploading = true;pauseUploadRequested=false;lastUploadPaint=0;
  $('pauseUpload').hidden=false;$('pauseUpload').disabled=false;$('pauseUpload').textContent='Pause upload';$('uploadActivity').hidden=false;
  syncNavigation(); $('upload').disabled = true; $('reset').disabled = true;
  for(const side of ['left','right']) $(`${side}File`).disabled = true;
  $('delimiter').disabled = $('encoding').disabled = true;
  try {
    if (!job) {
      const space=await api('/api/preflight',{bytes:files.left.size+files.right.size});
      if(!space.can_upload)throw Error('Insufficient server disk space for the uploaded copies. Use Storage & queue to clean up old jobs.');
      if(space.free<space.suggested_free_bytes&&!confirm(`${bytes(space.free)} free; at least ${bytes(space.suggested_free_bytes)} is suggested. Reports may need more. Continue uploading?`))return;
      job = await api('/api/jobs',{delimiter:$('delimiter').value==='tab'?'\t':$('delimiter').value,encoding:$('encoding').value,files:Object.fromEntries(['left','right'].map(side=>[side,{name:files[side].name,size:files[side].size}]))});
      localStorage.setItem('keywise-job',job.id);
    }
    if($('quickProfile').value)localStorage.setItem('upload-profile:'+job.id,$('quickProfile').value);
    job=await api(endpoint());
    for(const side of ['left','right']) {
      if(files[side].size!==job.files[side].size || files[side].name!==job.files[side].name) throw new Error('To resume, select the same files. Use New comparison to choose different files.');
    }
    $('uploadProgress').hidden = false;
    const total = files.left.size + files.right.size;
    for(const side of ['left','right']) {
      let offset = job.files[side].uploaded;
      while(offset < files[side].size) {
        if(pauseUploadRequested)return;
        const start = offset;
        const result = await sendChunkRecoverable(side,files[side].slice(start,start+8*1024*1024),start,loaded=>{
          const now=performance.now();if(now-lastUploadPaint<100&&loaded<Math.min(8*1024*1024,files[side].size-start))return;lastUploadPaint=now;
          const completed = (side==='right'?files.left.size:0)+start+loaded;
          const percent = Math.min(100,100*completed/total);
          const message=`Uploading ${side} file · ${bytes(completed)} of ${bytes(total)}`;
          $('uploadText').textContent=message;$('uploadActivityText').textContent=message;
          $('uploadPercent').textContent = `${percent.toFixed(1)}%`; $('uploadBar').value=percent;
        });
        offset = result.uploaded; job.files[side].uploaded = offset;
        await new Promise(resolve=>setTimeout(resolve,0));
      }
    }
    if(pauseUploadRequested)return;
    $('uploadText').textContent = 'Checking column names…';
    job = await api(endpoint('/finalize'),{});
    if(currentView==='uploadPanel')currentView=job.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):'uploadPanel';await refresh();if(currentView==='uploadPanel'&&job.state==='selecting_sheets')$('sheetPanel').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(error) {
    showError(error);
    if(job) {try {job=await api(endpoint());} catch { /* Keep current job to allow retry. */ }}
  } finally {
    uploading=false; $('upload').disabled=job&&job.state!=='uploading'; $('reset').disabled=false; $('reset').hidden=!job;
    for(const side of ['left','right']) $(`${side}File`).disabled=job&&job.state!=='uploading';
    $('pauseUpload').hidden=true;
    $('uploadActivity').hidden=job?.state!=='uploading';
    if(job?.state==='uploading'){const done=job.files.left.uploaded+job.files.right.uploaded;const message=`${pauseUploadRequested?'Upload paused':'Upload stopped'} · ${bytes(done)} saved. Click Resume upload to continue.`;$('uploadText').textContent=message;$('uploadActivityText').textContent=message;$('upload').textContent='Resume upload →';}
    if(!job) $('delimiter').disabled=$('encoding').disabled=false;syncNavigation();
  }
});
function renderKeys() {
  const changes=[...(job.header_changes||[]),...(job.header_layout_changes||[])];$('headerChanges').hidden=!changes.length;$('headerChangesTitle').textContent=`${changes.length} header names normalized — view ${changes.length>200?'first 200 changes':'changes'}`;
  if(changes.length&&$('headerChangesTable').dataset.job!==job.id){drawTable('headerChangesTable',['File','Column number','Original header','Comparison header'],changes.slice(0,200).map(c=>[c.side,c.column,c.original||'(empty)',c.normalized]));$('headerChangesTable').dataset.job=job.id;}
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
  const duplicates=(s.left_duplicate_rows_skipped||0)+(s.right_duplicate_rows_skipped||0);
  $('duplicateDownload').hidden=!duplicates;$('duplicateDownload').href=downloadUrl('duplicate_keys.csv');
  if(duplicates){$('resultTitle').textContent='Comparison complete — duplicate keys found';$('scopeSummary').textContent+=` Warning: ${number(s.left_duplicate_rows_skipped||0)} file 1 rows and ${number(s.right_duplicate_rows_skipped||0)} file 2 rows skipped as duplicates. Kept the ${s.duplicate_policy||'first'} source occurrence per key. Download the duplicate audit for details.`;}
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
const pendingExports=new Set();
function renderExports() {
  for(const kind of ['excel','html']) {
    const value=job.exports[kind];const button=$(kind);const status=$(`${kind}Status`);
    button.disabled=pendingExports.has(job.id+':'+kind)||!!value&&['queued','running'].includes(value.state);
    $(kind==='excel'?'cancelExcel':'cancelHtml').hidden=!button.disabled;
    button.classList.toggle('export-busy',button.disabled);button.setAttribute('aria-busy',String(button.disabled));
    button.querySelector('strong').textContent=button.disabled?(value?.state==='queued'?'Queued — waiting for a worker':'Generating '+(kind==='excel'?'Excel workbook':'HTML report')):(kind==='excel'?'Excel workbook':'HTML report');
    if(!value)status.textContent='';
    else if(value.state==='complete')status.textContent=`Ready to download · ${bytes(value.size)}`;
    else if(value.state==='error')status.textContent=`Export failed: ${value.error}`;
    else status.textContent=(value.message||'Queued for export…')+' Large reports may take time. You can continue reviewing results.';
  }
}
async function generateAndDownload(kind){
  clearError();const identity=job.id,key=identity+':'+kind,name=kind==='excel'?'mismatches.xlsx':'comparison-report.html';
  if(pendingExports.has(key))return;
  if(job.exports[kind]?.state==='complete')return download(name,identity);
  pendingExports.add(key);renderExports();
  try{
    await api(`/api/jobs/${identity}/export/${kind}`,{});
    for(;;){
      const latest=await api(`/api/jobs/${identity}`),state=latest.exports[kind];
      if(job?.id===identity){job.exports=latest.exports;renderExports();}
      if(state?.state==='complete'){download(name,identity);break;}
      if(['error','cancelled','outdated'].includes(state?.state))throw Error(state.error||state.message||'Export stopped');
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }catch(error){showError(error);}
  finally{pendingExports.delete(key);if(job?.id===identity)renderExports();}
}
for(const kind of ['excel','html']) $(kind).addEventListener('click',()=>generateAndDownload(kind));
let headerDraft=null, headerDraftJob=null, headerDirty=false, headerPage=0;
function headerSources(){return job.source_headers||{left:job.columns,right:job.columns};}
function renderHeaders(){
  if(headerDraftJob!==job.id){headerDraft=structuredClone(job.column_headers||headerSources());headerDraftJob=job.id;headerDirty=false;headerPage=0;}
  const search=$('headerSearch').value.toLowerCase();
  const source=headerSources();
  const matches=Object.fromEntries(['left','right'].map(side=>[side,source[side].map((name,i)=>i).filter(i=>(source[side][i]+' '+headerDraft[side][i]).toLowerCase().includes(search))]));
  const total=Math.max(matches.left.length,matches.right.length), size=50;
  headerPage=Math.min(headerPage,Math.max(0,Math.ceil(total/size)-1));
  for(const side of ['left','right']){
    const title=side==='left'?'Left':'Right', list=$('header'+title+'List');list.replaceChildren();
    $('header'+title+'Title').textContent=`File ${side==='left'?1:2} · ${job.files[side].name} · ${source[side].length} columns`;
    for(const index of matches[side].slice(headerPage*size,(headerPage+1)*size)){
      const label=document.createElement('label');label.className='header-edit';
      const original=document.createElement('span');original.textContent=`${index+1}. ${source[side][index]}`;
      const input=document.createElement('input');input.value=headerDraft[side][index];input.disabled=job.state!=='ready';input.dataset.side=side;input.dataset.index=index;
      input.setAttribute('aria-label',`File ${side==='left'?1:2} column ${index+1} comparison header`);
      input.addEventListener('input',()=>{headerDraft[side][index]=input.value;headerDirty=true;updateHeaderStatus();});
      label.append(original,input);list.append(label);
    }
  }
  $('headerRange').textContent=total?`${headerPage*size+1}–${Math.min(total,(headerPage+1)*size)} of ${total} positions (filtered separately per file)`:'No matching headers';
  $('headerPrev').disabled=headerPage===0;$('headerNext').disabled=(headerPage+1)*size>=total;
  $('headerContinue').hidden=$('headerReset').hidden=job.state!=='ready';updateHeaderStatus();
}
function updateHeaderStatus(){
  // Server casefold validation is authoritative for all Unicode headers.
  const maps={left:new Map(),right:new Map()};let invalid=0;
  for(const side of ['left','right'])for(const name of headerDraft[side]){const key=name.toLowerCase();maps[side].set(key,(maps[side].get(key)||0)+1);if(!name.trim())invalid++;}
  const missing={left:headerDraft.left.filter(n=>!maps.right.has(n.toLowerCase())),right:headerDraft.right.filter(n=>!maps.left.has(n.toLowerCase()))};
  const duplicate=Object.values(maps).reduce((n,m)=>n+[...m.values()].filter(v=>v>1).length,0);
  $('headerLayoutStatus').textContent=invalid||duplicate?`${invalid} empty names · ${duplicate} duplicate names. Every comparison header must be unique within its file, ignoring case.`:missing.left.length||missing.right.length?`Non-common columns will be excluded · File 1 only: ${missing.left.slice(0,8).join(', ')||'none'}${missing.left.length>8?' …':''} · File 2 only: ${missing.right.slice(0,8).join(', ')||'none'}${missing.right.length>8?' …':''}. Only shared columns will be available for keys and comparison. Rename a column if you want to match it.`:`All ${headerDraft.left.length} columns match ignoring case. ${headerDirty?'Apply your edits to continue.':'Ready to continue.'}`;
  for(const input of $('headersPanel').querySelectorAll('input[data-side]')){const side=input.dataset.side,key=input.value.toLowerCase();input.classList.toggle('header-unmatched',!maps[side==='left'?'right':'left'].has(key)||maps[side].get(key)>1||!input.value.trim());}
}
async function applyHeaders(){
  await saveDraft();
  job=await api(endpoint('/headers'),{column_headers:headerDraft});
  headerDirty=false;hydratedId=null;hydrate();syncNavigation();
  $('headerChangesTable').dataset.job='';
}
$('headerSearch').addEventListener('input',()=>{headerPage=0;renderHeaders();});
$('headerPrev').addEventListener('click',()=>{headerPage--;renderHeaders();});
$('headerNext').addEventListener('click',()=>{headerPage++;renderHeaders();});
$('headerReset').addEventListener('click',()=>{headerDraft=structuredClone(headerSources());headerDirty=true;renderHeaders();});
$('headerContinue').addEventListener('click',async()=>{
  clearError();$('headerContinue').disabled=true;
  try{await applyHeaders();panel('keysPanel');renderKeys();renderIgnoredColumns();await loadContainers();}
  catch(error){showError(error);}finally{$('headerContinue').disabled=false;}
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
  valueRules=Array.isArray(draft.comparison_rules)?draft.comparison_rules:[];
  $('ignoreKeys').value=typeof draft.ignore_keys==='string'?draft.ignore_keys:'';
  $('duplicatePolicy').value=draft.duplicate_policy||'first';

  keyPage=ignorePage=sourceColumnOffset=0;hydratedId=job.id;
}
$('resumeComparison').addEventListener('click',async()=>{try{$('resumeComparison').disabled=true;job=await api(endpoint('/resume'),{});clearError();await refresh();}catch(error){showError(error);}finally{$('resumeComparison').disabled=false;}});
async function refresh() {
  clearTimeout(pollTimer);
  const previousState=job.state;
  const requestedId=job.id;
  const latest=await api(endpoint());
  if(job.id!==requestedId)return;
  job=latest;await applyPendingUploadProfile();hydrate();
  $('reset').hidden=false;syncNavigation();
  if(['selecting_sheets','preparing'].includes(job.state)){
    if(currentView==='uploadPanel'){panel('uploadPanel');renderSheetSelection();}
    if(job.state==='preparing')pollTimer=setTimeout(()=>refresh().catch(pollError),1500);
  } else if(job.state==='ready'){
    if(previousState==='preparing')await loadContainers();
    if(currentView==='uploadPanel')panel('headersPanel');
    if(currentView==='headersPanel'){panel('headersPanel');renderHeaders();await loadProfiles();}
    if(currentView==='keysPanel'){panel('keysPanel');renderKeys();renderIgnoredColumns();}
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
  } else if(['error','cancelled'].includes(job.state)){
    if(!['historyPanel','containersPanel','jsonPanel','docsPanel','storagePanel','analysisPanel','profilesPanel','settingsPanel'].includes(currentView)){panel('runningPanel');await renderPipeline();showError(job.error);await loadDiagnostics();}
  } else if(!['historyPanel','containersPanel','jsonPanel','docsPanel','storagePanel','analysisPanel','profilesPanel','settingsPanel'].includes(currentView)){
    panel('uploadPanel');$('delimiter').disabled=$('encoding').disabled=true;$('delimiter').value=job.delimiter==='\t'?'tab':job.delimiter;$('encoding').value=job.encoding;
  }
}
function pollError(error){showError(error);pollTimer=setTimeout(()=>refresh().catch(pollError),5000);}
$('reset').addEventListener('click',()=>{localStorage.removeItem('keywise-job');location.reload();});
(async()=>{
  const id=localStorage.getItem('keywise-job');
  if(id&&/^[a-f0-9]{32}$/.test(id)){
    job={id};
    try{job=await api(endpoint());currentView=job.state==='complete'?'results':['running','queued','error','cancelled'].includes(job.state)?'runningPanel':job.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):'uploadPanel';panel(currentView);await refresh();if(job.state==='uploading')showError(`Upload not finished. Reselect ${job.files.left.name} and ${job.files.right.name} to resume, or start a new comparison.`);}
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
  $('resumeComparison').hidden=!job.can_resume;
  const stages=[['validate','Validate configuration'],...(job.sort_workers===2?[['sort','Read & sort both files']]:[['left','Read & sort file 1'],['right','Read & sort file 2']]),['compare','Compare matching keys'],['reports','Write results'],['complete','Complete']];
  const stage=job.state==='complete'?'complete':job.progress?.stage||'validate';const index=stages.findIndex(([id])=>id===stage);
  $('phase').textContent=job.state==='complete'?'Comparison complete':job.state==='error'?'Comparison failed':job.state==='queued'?'Queued':job.progress?.phase||'Starting worker';
  $('processed').textContent=job.state==='error'?job.error:job.progress?.rows!==undefined?`${number(job.progress.rows)} rows processed in the current phase`:job.state==='queued'?'Waiting for a server worker. Your files and results are kept in your own job.':'Stages update as the worker processes the files.';
  $('pipelineState').textContent=job.state;$('pipelineStages').replaceChildren();
  stages.forEach(([id,label],i)=>{const item=document.createElement('li');const state=job.state==='queued'?'pending':job.state==='complete'||i<index?'done':i===index?(job.state==='error'?'failed':'active'):'pending';item.className=state;item.textContent=`${state==='done'?'✓':state==='failed'?'!':i+1}  ${label}`;$('pipelineStages').append(item);});
  if(logJobId!==job.id){logJobId=job.id;logCursor=0;logText='';}
  const id=job.id;const log=await api(endpoint('/logs')+'?cursor='+logCursor);if(job.id!==id)return;
  logCursor=log.cursor;logText=(logText+log.text).split('\n').slice(-1000).join('\n');$('consoleLog').textContent=logText||'Waiting for worker output…';$('consoleLog').scrollTop=$('consoleLog').scrollHeight;
  $('logDownload').href=downloadUrl('run.log');
  if(log.text.length>=60000)pollTimer=setTimeout(()=>refresh().catch(pollError),300);
}
async function loadHistory(){
  const result=await api('/api/jobs?offset='+historyOffset);if(!['historyPanel','containersPanel','jsonPanel','docsPanel','storagePanel','analysisPanel','profilesPanel','settingsPanel'].includes(currentView))return;
  $('cleanupPolicy').textContent=result.retention_days?`Inactive jobs and all uploaded files, scratch data and reports are automatically deleted after ${result.retention_days} days. Download reports before expiry. Active jobs are protected.`:'Automatic cleanup is disabled. Delete unneeded jobs to reclaim disk space.';
  const table=$('historyTable');table.replaceChildren();
  const header=document.createElement('tr');for(const label of ['Created','Files','Status','Changed cells','Action']){const th=document.createElement('th');th.textContent=label;header.append(th);}const head=document.createElement('thead');head.append(header);table.append(head);
  const body=document.createElement('tbody');
  for(const item of result.jobs){const row=document.createElement('tr');for(const text of [new Date(item.created*1000).toLocaleString(),`${item.files.left.name}${item.files.left.sheet?' ['+item.files.left.sheet+']':''} ↔ ${item.files.right.name}${item.files.right.sheet?' ['+item.files.right.sheet+']':''}`,item.state,item.changed_cells==null?'—':number(item.changed_cells)]){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}const cell=document.createElement('td');const button=document.createElement('button');button.className='subtle';button.textContent='Open';button.disabled=uploading;button.setAttribute('aria-label',`Open comparison ${item.id}`);button.addEventListener('click',()=>openJob(item.id).catch(showError));cell.append(button);const remove=document.createElement('button');remove.className='subtle';remove.textContent='Delete';remove.disabled=item.busy||uploading;remove.setAttribute('aria-label',`Delete comparison ${item.id}`);remove.addEventListener('click',async()=>{if(!confirm('Permanently delete this job, uploaded copies, logs and all reports? Original files on your computer are unaffected.'))return;remove.disabled=true;try{await api('/api/jobs/'+item.id,undefined,'DELETE');if(localStorage.getItem('keywise-job')===item.id)localStorage.removeItem('keywise-job');if(job?.id===item.id){clearTimeout(pollTimer);job=null;hydratedId=null;renderedId=null;viewingHistoryJob=false;syncNavigation();}await loadHistory();}catch(error){showError(error);remove.disabled=false;}});cell.append(remove);row.append(cell);body.append(row);}table.append(body);
  $('historyRange').textContent=result.total?`${historyOffset+1}–${Math.min(historyOffset+25,result.total)} of ${result.total} saved jobs`:'No comparisons yet.';$('historyPrev').disabled=historyOffset===0;$('historyNext').disabled=historyOffset+25>=result.total;
}
async function openJob(id){
  if(uploading){showError('Pause the upload before opening another job.');return;}
  $('uploadActivity').hidden=true;
  clearTimeout(pollTimer);await saveDraft();clearError();viewingHistoryJob=true;job=await api('/api/jobs/'+id);localStorage.setItem('keywise-job',id);renderedId=null;hydratedId=null;hydrate();
  for(const side of ['left','right']){files[side]=null;$(`${side}File`).value='';$(`${side}Name`).textContent=job.files[side].name;$(`${side}Size`).textContent=bytes(job.files[side].size);}
  $('sourceLeftTable').replaceChildren();$('sourceRightTable').replaceChildren();
  $('sourceLeftTitle').textContent='File 1';$('sourceRightTitle').textContent='File 2';
  $('sourceStatus').textContent='Choose a row limit, then load this comparison’s source preview.';
  panel(job.state==='complete'?'results':['queued','running','error','cancelled'].includes(job.state)?'runningPanel':job.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):'uploadPanel');
  await refresh();
  if(job.state==='uploading')showError(`Reselect ${job.files.left.name} and ${job.files.right.name} to resume this upload.`);
}
$('historyRefresh').addEventListener('click',()=>loadHistory().catch(showError));
$('historyPrev').addEventListener('click',()=>{historyOffset=Math.max(0,historyOffset-25);loadHistory().catch(showError);});
$('historyNext').addEventListener('click',()=>{historyOffset+=25;loadHistory().catch(showError);});

const sidebarMedia=matchMedia('(max-width: 800px)');
let desktopSidebarCollapsed=true;
function hideSidebarTip(){
  $('sidebarTooltip').hidden=true;
  document.querySelectorAll('[aria-describedby="sidebarTooltip"]').forEach(el=>el.removeAttribute('aria-describedby'));
}
function applySidebar(collapsed){
  const drawer=sidebarMedia.matches&&!collapsed;
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  document.body.classList.toggle('sidebar-drawer-open',drawer);
  $('sidebarToggle').setAttribute('aria-expanded',String(!collapsed));
  $('sidebarToggle').setAttribute('aria-label',collapsed?'Expand sidebar':drawer?'Close navigation':'Collapse sidebar');
  document.querySelector('.toggle-label').textContent=drawer?'Close navigation':'Collapse sidebar';
  $('sidebarBackdrop').hidden=!drawer;
  $('mainWorkspace').inert=drawer;
  if(drawer){$('workspaceSidebar').setAttribute('role','dialog');$('workspaceSidebar').setAttribute('aria-modal','true');}
  else{$('workspaceSidebar').removeAttribute('role');$('workspaceSidebar').removeAttribute('aria-modal');}
  hideSidebarTip();
}
function closeSidebarDrawer(){
  if(document.body.classList.contains('sidebar-drawer-open')){applySidebar(true);$('sidebarToggle').focus();}
}
applySidebar(sidebarMedia.matches||desktopSidebarCollapsed);
$('sidebarToggle').addEventListener('click',()=>{
  const collapsed=!document.body.classList.contains('sidebar-collapsed');
  if(!sidebarMedia.matches){desktopSidebarCollapsed=collapsed;}
  applySidebar(collapsed);
});
sidebarMedia.addEventListener('change',()=>applySidebar(sidebarMedia.matches||desktopSidebarCollapsed));
$('sidebarBackdrop').addEventListener('click',closeSidebarDrawer);
document.addEventListener('keydown',event=>{
  if(!document.body.classList.contains('sidebar-drawer-open'))return;
  if(event.key==='Escape'){event.preventDefault();closeSidebarDrawer();}
  if(event.key==='Tab'){
    const controls=[...$('workspaceSidebar').querySelectorAll('a,button:not(:disabled)')].filter(el=>el.getClientRects().length);
    const first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
});
for(const button of document.querySelectorAll('.sidebar [data-tip]')){
  function showTip(){
    if(!document.body.classList.contains('sidebar-collapsed'))return;
    const box=button.getBoundingClientRect(),tip=$('sidebarTooltip');
    tip.textContent=button.dataset.tip;tip.hidden=false;
    tip.style.left=(box.right+12)+'px';tip.style.top=Math.max(8,Math.min(innerHeight-tip.offsetHeight-8,box.top+(box.height-tip.offsetHeight)/2))+'px';
    button.setAttribute('aria-describedby','sidebarTooltip');
  }
  button.addEventListener('mouseenter',showTip);button.addEventListener('focus',showTip);
  button.addEventListener('mouseleave',hideSidebarTip);button.addEventListener('blur',hideSidebarTip);
  button.addEventListener('click',hideSidebarTip);
}
$('workspaceSidebar').addEventListener('scroll',hideSidebarTip);
window.addEventListener('resize',hideSidebarTip);
$('navNew').addEventListener('click',async()=>{
  try{
    if(uploading){panel('uploadPanel');return;}
    if(['containersPanel','jsonPanel','docsPanel','storagePanel','profilesPanel','settingsPanel'].includes(currentView)&&!viewingHistoryJob){await goView(job?.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):job?.state==='complete'?'results':job&&['running','queued','error','cancelled'].includes(job.state)?'runningPanel':'uploadPanel');return;}
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

let jsonRules=[], jsonResult=null, jsonBusy=false, jsonArrays=[];
function resetJsonDiscovery(){jsonArrays=[];$('jsonArrayChoices').replaceChildren();$('jsonArrayStatus').textContent='Inputs changed. Find arrays again to review available keys. Existing rules remain in Advanced array rules.';}
for(const id of ['jsonLeft','jsonRight'])$(id).addEventListener('input',resetJsonDiscovery);
function invalidateJson(){jsonResult=null;$('jsonResult').hidden=true;$('jsonStatus').textContent='';}
for(const id of ['jsonLeft','jsonRight','jsonDefault'])$(id).addEventListener('input',invalidateJson);
$('jsonDefault').addEventListener('change',()=>renderJsonArrayChoices());
for(const side of ['Left','Right'])$('json'+side+'File').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;clearError();invalidateJson();resetJsonDiscovery();
  try{if(file.size>5*1024*1024)throw new Error('Each JSON input must be at most 5 MiB.');$('json'+side).value=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}catch(error){showError(error);}finally{event.target.value='';}
});
function renderJsonArrayChoices(){
  const container=$('jsonArrayChoices');container.replaceChildren();
  for(const array of jsonArrays){
    const row=document.createElement('div');row.className='json-array-choice';
    const info=document.createElement('div');const title=document.createElement('strong');title.textContent=array.path==='$'?'Root array':array.path.replace(/^\$\./,'');
    const count=document.createElement('small');count.textContent=`${array.left_items} items in file 1 · ${array.right_items} in file 2`;
    info.append(title,count);
    const label=document.createElement('label');label.textContent='Match using';const select=document.createElement('select');select.setAttribute('aria-label',`Match key for ${array.path}`);
    select.add(new Option('Pasted order (no key)', 'ordered'));
    array.fields.forEach((field,index)=>select.add(new Option(field, String(index))));
    const rule=jsonRules.find(rule=>rule.path===array.path);
    if(rule?.mode==='keyed'&&array.fields.includes(rule.field))select.value=String(array.fields.indexOf(rule.field));
    else if(rule && rule.mode!=='ordered'){select.add(new Option('Advanced rule — review below','advanced'));select.value='advanced';}
    else if(!rule&&$('jsonDefault').value==='unordered'){select.add(new Option('Advanced default: ignore order','advanced'));select.value='advanced';}
    select.disabled=jsonBusy;
    select.addEventListener('change',()=>{
      if(select.value==='advanced')return;
      const remaining=jsonRules.filter(rule=>rule.path!==array.path);
      if(remaining.length>=100){showError('Use up to 100 array rules. Remove an advanced rule first.');renderJsonArrayChoices();return;}
      jsonRules=[...remaining,{path:array.path,mode:select.value==='ordered'?'ordered':'keyed',field:select.value==='ordered'?'':array.fields[Number(select.value)]}];
      invalidateJson();renderJsonRules();
    });
    label.append(select);row.append(info,label);
    if(!array.fields.length){const note=document.createElement('small');note.textContent='No unique, non-null scalar field is available across these array items. Keep pasted order.';row.append(note);}
    container.append(row);
  }
}
$('jsonDiscover').addEventListener('click',async()=>{
  clearError();jsonBusy=true;$('jsonDiscover').disabled=true;$('jsonCompare').disabled=true;renderJsonRules();
  const left=$('jsonLeft').value,right=$('jsonRight').value;$('jsonArrayStatus').textContent='Finding arrays and checking unique keys…';
  try{
    const result=await api('/api/json-arrays',{left,right});
    if(left!==$('jsonLeft').value||right!==$('jsonRight').value){resetJsonDiscovery();return;}
    jsonArrays=result.arrays;
    $('jsonArrayStatus').textContent=jsonArrays.length?`${jsonArrays.length} array paths found. Choose a key only where order should be ignored. Keys must be unique within each array. Nested array rules apply to every parent item.`:'No arrays found. Object property order is already ignored.';
  }catch(error){resetJsonDiscovery();$('jsonArrayStatus').textContent='Unable to inspect arrays. Check both JSON inputs.';showError(error);}
  finally{jsonBusy=false;$('jsonDiscover').disabled=false;$('jsonCompare').disabled=false;renderJsonRules();}
});
$('jsonRuleMode').addEventListener('change',()=>{$('jsonRuleField').disabled=$('jsonRuleMode').value!=='keyed';});
function renderJsonRules(){
  renderJsonArrayChoices();
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
  const controls=['jsonDiscover','jsonLeft','jsonRight','jsonLeftFile','jsonRightFile','jsonDefault','jsonRulePath','jsonRuleMode','jsonRuleField','jsonAddRule','jsonCompare'];
  controls.forEach(id=>$(id).disabled=true);renderJsonRules();$('jsonStatus').textContent='Comparing JSON…';
  try{
    for(const id of ['jsonLeft','jsonRight'])if(new Blob([$(id).value]).size>5*1024*1024)throw new Error('Each JSON input must be at most 5 MiB.');
    jsonResult=await api('/api/json-compare',{left:$('jsonLeft').value,right:$('jsonRight').value,default_order:$('jsonDefault').value,rules:jsonRules});
    $('jsonResult').hidden=false;$('jsonResultTitle').textContent=jsonResult.equal?'JSON documents match':'JSON differences found';
    $('jsonCounts').textContent=`${jsonResult.counts.changed} changed · ${jsonResult.counts.added} added · ${jsonResult.counts.removed} removed`;
    $('jsonWarnings').textContent=jsonResult.warnings.join(' · ');
    prepareJsonView();$('jsonResult').scrollIntoView({block:'start'});
    $('jsonStatus').textContent='Comparison complete. Source JSON is unchanged.';
  }catch(error){$('jsonStatus').textContent='Comparison failed.';showError(error);}finally{jsonBusy=false;controls.forEach(id=>$(id).disabled=false);$('jsonRuleField').disabled=$('jsonRuleMode').value!=='keyed';renderJsonRules();}
});
function saveJsonReport(content,type,name){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('jsonDownload').addEventListener('click',()=>{if(jsonResult)saveJsonReport(JSON.stringify(jsonResult,null,2),'application/json','json-comparison.json');});
let jsonViewRows=[], jsonViewPage=0, jsonDiffIndex=-1, jsonDiffRows=[];
const jsonPageSize=200;
function prepareJsonView(){
  jsonViewPage=0;jsonDiffIndex=-1;jsonDiffRows=[];
  for(let i=0;i<jsonResult.view.length;i++){const d=jsonResult.view[i].difference;if(d!==null&&jsonDiffRows[d]===undefined)jsonDiffRows[d]=i;}
  filterJsonView();
}
function filterJsonView(){
  const view=jsonResult.view;
  if($('jsonChangesOnly').checked){
    const keep=new Set();
    for(let i=0;i<view.length;i++)if(view[i].kind!=='equal')for(let j=Math.max(0,i-2);j<=Math.min(view.length-1,i+2);j++)keep.add(j);
    jsonViewRows=[...keep].sort((a,b)=>a-b);
  }else jsonViewRows=Array.from({length:view.length},(_,i)=>i);
  jsonViewPage=0;renderJsonView();
}
function jsonCodeCell(token,other,kind){
  const cell=document.createElement('div');cell.className='json-code-cell'+(!token?' json-gap':'');
  if(!token){cell.setAttribute('aria-label','No corresponding line');return cell;}
  cell.title=token.path;
  const line=document.createElement('span');line.className='json-line-number';line.textContent=token.line;
  const code=document.createElement('code');const text=token.text.slice(0,2000);code.append('  '.repeat(token.depth));
  if(kind==='changed'&&other&&text!==other.text){
    const otherText=other.text;let start=0,end=0;
    while(start<Math.min(text.length,otherText.length)&&text[start]===otherText[start])start++;
    while(end<Math.min(text.length,otherText.length)-start&&text[text.length-1-end]===otherText[otherText.length-1-end])end++;
    code.append(text.slice(0,start));const mark=document.createElement('mark');mark.textContent=text.slice(start,text.length-end);code.append(mark,text.slice(text.length-end));
  }else code.append(text);
  if(token.text.length>2000)code.append(' … [long line; full value in download]');
  cell.append(line,code);return cell;
}
function renderJsonView(){
  const total=jsonViewRows.length;jsonViewPage=Math.min(jsonViewPage,Math.max(0,Math.ceil(total/jsonPageSize)-1));
  const fragment=document.createDocumentFragment();let previous=null;
  for(const index of jsonViewRows.slice(jsonViewPage*jsonPageSize,(jsonViewPage+1)*jsonPageSize)){
    if(previous!==null&&index>previous+1){const gap=document.createElement('div');gap.className='json-context-gap';gap.textContent=`${index-previous-1} unchanged aligned lines hidden`;fragment.append(gap);}
    const item=jsonResult.view[index],row=document.createElement('div');row.className='json-code-row '+item.kind;row.dataset.row=index;
    if(item.difference===jsonDiffIndex&&jsonDiffIndex>=0)row.classList.add('current-difference');
    row.append(jsonCodeCell(item.left,item.right,item.kind),jsonCodeCell(item.right,item.left,item.kind));fragment.append(row);previous=index;
  }
  if(!total){const p=document.createElement('p');p.textContent='No differences. Uncheck the context filter to view both documents.';fragment.append(p);}
  $('jsonCodeView').replaceChildren(fragment);$('jsonCodeView').scrollTop=0;
  $('jsonLineRange').textContent=total?`Aligned lines ${jsonViewPage*jsonPageSize+1}–${Math.min(total,(jsonViewPage+1)*jsonPageSize)} of ${number(total)} · hover a line for its original path`:'No changed lines';
  $('jsonPrevPage').disabled=jsonViewPage===0;$('jsonNextPage').disabled=(jsonViewPage+1)*jsonPageSize>=total;
  $('jsonPrevDiff').disabled=$('jsonNextDiff').disabled=!jsonDiffRows.length;
  $('jsonDiffPosition').textContent=jsonDiffRows.length?(jsonDiffIndex<0?`${number(jsonDiffRows.length)} differences`:`Difference ${jsonDiffIndex+1} of ${number(jsonDiffRows.length)}`):'Documents match';
}
function moveJsonDifference(direction){
  if(!jsonDiffRows.length)return;
  jsonDiffIndex=jsonDiffIndex<0?(direction>0?0:jsonDiffRows.length-1):(jsonDiffIndex+direction+jsonDiffRows.length)%jsonDiffRows.length;
  const row=jsonDiffRows[jsonDiffIndex],position=jsonViewRows.indexOf(row);
  jsonViewPage=Math.floor(position/jsonPageSize);renderJsonView();
  const element=$('jsonCodeView').querySelector(`[data-row="${row}"]`);if(element)$('jsonCodeView').scrollTop=element.offsetTop-$('jsonCodeView').offsetTop;
}
$('jsonChangesOnly').addEventListener('change',()=>{if(jsonResult)filterJsonView();});
$('jsonPrevDiff').addEventListener('click',()=>moveJsonDifference(-1));
$('jsonNextDiff').addEventListener('click',()=>moveJsonDifference(1));
$('jsonPrevPage').addEventListener('click',()=>{jsonViewPage--;renderJsonView();});
$('jsonNextPage').addEventListener('click',()=>{jsonViewPage++;renderJsonView();});
$('jsonHtml').addEventListener('click',()=>{
  if(!jsonResult)return;
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cell=t=>'<pre>'+(t?'<small>'+t.line+'</small> '+escape('  '.repeat(t.depth)+t.text):'')+'</pre>';
  const lines=jsonResult.view.map(row=>'<div class="row '+row.kind+'">'+cell(row.left)+cell(row.right)+'</div>').join('');
  const html='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JSON comparison</title><style>body{font:15px system-ui;color:#004364;margin:24px}.row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}pre{margin:0;padding:4px 10px;border-right:1px solid #ccdce3;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 monospace}small{color:#647d8b}.changed{background:#fff1c1}.removed{background:#ffe6e4}.added{background:#e0f4e8}.settings{white-space:pre-wrap;margin-bottom:20px}.head{position:sticky;top:0;background:#004364;color:white;padding:12px}h1{font-size:24px}</style><h1>JSON comparison</h1><p>'+escape($('jsonResultTitle').textContent)+' · '+escape($('jsonCounts').textContent)+'</p><p>Yellow: changed. Red: removed. Green: added. Blocks are aligned by the selected array rules. Line numbers refer to pretty-printed original documents; source order and values are preserved in the original inputs.</p><details><summary>Array settings and warnings</summary><pre class="settings">'+escape(JSON.stringify({default_order:jsonResult.default_order,rules:jsonResult.rules,warnings:jsonResult.warnings},null,2))+'</pre></details><div class="row head"><strong>File 1 · Original</strong><strong>File 2 · Updated</strong></div>'+lines+'</html>';
  saveJsonReport(html,'text/html','json-comparison.html');
});

$('pauseUpload').addEventListener('click',()=>{pauseUploadRequested=true;$('pauseUpload').disabled=true;$('pauseUpload').textContent='Pausing after current chunk…';});
$('returnUpload').addEventListener('click',()=>{goView(job?.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):'uploadPanel').catch(showError);});
async function sendChunkRecoverable(side,blob,offset,update){
  for(let attempt=0;attempt<3;attempt++){
    try{return await sendChunk(side,blob,offset,update);}catch(error){
      // The server may have saved the chunk before the response was lost.
      // Reconcile its durable file offset before retrying; never append twice.
      const latest=await api(endpoint());
      const uploaded=latest.files[side].uploaded;
      job.files[side].uploaded=uploaded;
      if(uploaded===offset+blob.size)return {uploaded};
      if(uploaded!==offset||attempt===2)throw error;
      $('uploadActivityText').textContent=`Retrying ${side} upload chunk (${attempt+1}/2)…`;
      await new Promise(resolve=>setTimeout(resolve,500*(attempt+1)));
    }
  }
}

if(!job){$('navNew').classList.add('active');$('navNew').setAttribute('aria-current','page');}

// Help stays available during uploads and comparison, without fetching data.
const docsEntries=[...$('docsPanel').querySelectorAll('[data-doc-entry]')];
const docsIndex=docsEntries.map(entry=>entry.textContent.toLowerCase());
function filterDocs(){
  const terms=$('docsSearch').value.trim().toLowerCase().split(/\s+/).filter(Boolean);let count=0;
  docsEntries.forEach((entry,i)=>{
    const match=terms.every(term=>docsIndex[i].includes(term));entry.hidden=!match;
    if(entry.tagName==='DETAILS')entry.open=terms.length>0&&match;
    if(match)count++;
  });
  $('docsSearchStatus').textContent=terms.length?`${count} matching topics`:'Search the guides and frequently asked questions.';
  $('docsEmpty').hidden=count>0;
  $('docs-faq').hidden=!docsEntries.some(entry=>entry.tagName==='DETAILS'&&!entry.hidden);
  document.querySelector('.docs-topics').hidden=terms.length>0;
}
$('docsSearch').addEventListener('input',filterDocs);
filterDocs();

let analysisOffset=0, analysisTimer=null, analysisGeneration=0, columnChoicesPage=0;
let analysisNotesCache=[],commentSelection="";
$('openAnalysis').addEventListener('click',()=>goView('analysisPanel').catch(showError));
function analysisKeyValues(){return job.keys.map((_,i)=>$('analysisKeyPart'+i).value);}
function selectAnalysisKey(values){
  job.keys.forEach((_,i)=>$('analysisKeyPart'+i).value=values?.[i]||'');
  $('analysisKey').value=values?(job.keys.length===1?values[0]:JSON.stringify(values)):'';
}
function analysisColumns(){
  const chosen=$('analysisColumn').value,query=$('analysisColumnSearch').value.toLowerCase();
  $('analysisColumn').replaceChildren(new Option('All mismatching columns',''));
  for(const [name,count] of Object.entries(job.summary.changed_cells_by_column).filter(([n,c])=>c>0&&(n===chosen||n.toLowerCase().includes(query))).sort((a,b)=>b[1]-a[1]))$('analysisColumn').add(new Option(`${name} · ${number(count)} mismatches`,name));
  $('analysisColumn').value=chosen;renderColumnChoices();
}
function renderColumnChoices(){
  const query=$('analysisColumnSearch').value.toLowerCase(),entries=Object.entries(job.summary.changed_cells_by_column).filter(([n,c])=>c>0&&n.toLowerCase().includes(query)).sort((a,b)=>b[1]-a[1]);columnChoicesPage=Math.min(columnChoicesPage,Math.max(0,Math.ceil(entries.length/50)-1));$('analysisColumnChoices').replaceChildren();
  for(const [name,count] of entries.slice(columnChoicesPage*50,columnChoicesPage*50+50)){const button=document.createElement('button');button.className='column-choice';button.textContent=`${name} · ${number(count)}`;button.setAttribute('aria-pressed',String($('analysisColumn').value===name));button.addEventListener('click',()=>{$('analysisColumn').value=name;analysisOffset=0;loadAnalysis();renderColumnChoices();});$('analysisColumnChoices').append(button);}
  $('columnChoicesRange').textContent=entries.length?`${columnChoicesPage*50+1}–${Math.min(entries.length,columnChoicesPage*50+50)} of ${entries.length} columns`:'No mismatching columns';$('columnChoicesPrev').disabled=!columnChoicesPage;$('columnChoicesNext').disabled=(columnChoicesPage+1)*50>=entries.length;
}
$('columnChoicesPrev').addEventListener('click',()=>{columnChoicesPage--;renderColumnChoices();});
$('columnChoicesNext').addEventListener('click',()=>{columnChoicesPage++;renderColumnChoices();});
function analysisContext(){
  const mode=$('analysisMode').value,column=$('analysisColumn').value,key=$('analysisKey').value;
  $('analysisByColumn').setAttribute('aria-pressed',String(mode==='columns'));$('analysisByKey').setAttribute('aria-pressed',String(mode==='keys'));$('analysisByPattern').setAttribute('aria-pressed',String(mode==='patterns'));$('analysisKeyControls').hidden=mode==='patterns';$('patternSummary').hidden=mode!=='patterns';
  $('analysisColumnControls').hidden=mode==='keys'&&!column;
  $('analysisHeading').textContent=mode==='patterns'?'Recurring mismatch patterns':key?'Differences for this key':column?`Mismatches in ${column}`:mode==='keys'?'Records to review':'Columns with differences';
  const activeKey=key?(job.keys.length===1?[key]:JSON.parse(key)):[];
  const keyLabel=key?job.keys.map((name,i)=>`${name}: ${activeKey[i]}`).join(' · '):'';
  $('analysisSelection').textContent=[column?'Column: '+column:'All mismatching columns',keyLabel||'All keys'].join(' · ');
  $('analysisReset').hidden=!column&&!key;$('analysisReset').textContent=mode==='patterns'?'Back to all patterns':mode==='keys'?'Back to all keys':'Back to all columns';
  const signature=JSON.stringify([column,key]);
  if(commentSelection!==signature){commentSelection=signature;$('noteTarget').value=key?'key':'column';fillCommentEditor();}
  renderCommentScope();

}
async function resetAnalysis(mode){
  $('analysisMode').value=mode;$('analysisColumn').value='';$('analysisColumnSearch').value='';selectAnalysisKey(null);analysisOffset=0;analysisColumns();await loadAnalysis();
}
async function openAnalysis(){
  clearTimeout(analysisTimer);analysisGeneration++;analysisOffset=0;
  $('analysisRetry').hidden=true;$('noteComment').value='';$('analysisMode').value='columns';$('analysisKey').value='';$('analysisColumn').value='';$('analysisColumnSearch').value='';
  $('analysisKeyFields').replaceChildren();
  job.keys.forEach((name,i)=>{const label=document.createElement('label'),input=document.createElement('input');label.textContent=name;input.id='analysisKeyPart'+i;input.placeholder='Exact '+name;input.addEventListener('keydown',event=>{if(event.key==='Enter')$('analysisSearch').click();});label.append(input);$('analysisKeyFields').append(label);});
  analysisColumns();$('analysisOverview').replaceChildren();
  for(const [label,value] of [['Columns with differences',Object.values(job.summary.changed_cells_by_column).filter(n=>n>0).length],['Changed keys',job.summary.changed_rows],['Mismatched cells',job.summary.changed_cells],['Keys in one file only',job.summary.left_only+job.summary.right_only]]){const card=document.createElement('div'),title=document.createElement('span'),count=document.createElement('strong');title.textContent=label;count.textContent=number(value||0);card.append(title,count);$('analysisOverview').append(card);}
  const containers=job.summary.ignore_key_containers||[];
  $('analysisExclusions').textContent=containers.length?'Excluded containers: '+containers.map(c=>`${c.name}: ${c.reason} (${c.keys.length} configured keys)`).join(' · '):'No ignored-key containers configured.';
  commentSelection='';columnChoicesPage=0;await loadNotes();await loadAnalysis();
}
async function loadAnalysis(){
  clearTimeout(analysisTimer);const generation=++analysisGeneration,id=job.id;
  analysisContext();
  const column=$('analysisColumn').value,keyText=$('analysisKey').value,mode=$('analysisMode').value;
  const table=$('analysisTable');table.replaceChildren();$('analysisStatus').classList.remove('loading-status');$('analysisRange').textContent='';clearError();
  $('analysisPrev').disabled=$('analysisNext').disabled=true;
  try{
    let key;
    if(keyText!==''){key=job.keys.length===1?[keyText]:JSON.parse(keyText);if(!Array.isArray(key)||key.length!==job.keys.length||key.some(v=>typeof v!=='string'))throw Error('Enter a JSON array with one text value for each key column.');}
    let rows,headers,total,keyStatus;
    if(mode==='columns'&&!column&&key===undefined){
      const entries=Object.entries(job.summary.changed_cells_by_column).filter(([name,count])=>count>0&&name.toLowerCase().includes($('analysisColumnSearch').value.toLowerCase())).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));total=entries.length;
      headers=['Column','Mismatches','Mismatch %','Match %','Inspect'];
      rows=entries.slice(analysisOffset,analysisOffset+50).map(([name,count])=>[name,count,job.summary.matched_keys?(100*count/job.summary.matched_keys).toFixed(4):'N/A',job.summary.matched_keys?(100*(1-count/job.summary.matched_keys)).toFixed(4):'N/A']);
    }else{
      const params=new URLSearchParams({mode:mode==='patterns'?'patterns':mode==='keys'&&!column&&key===undefined?'keys':'cells',offset:String(analysisOffset),column});if(key!==undefined)params.set('key',JSON.stringify(key));
      $('analysisStatus').textContent='Loading analysis…';$('analysisStatus').classList.add('loading-status');
      const result=await api(`/api/jobs/${id}/analysis?${params}`);if(generation!==analysisGeneration||job?.id!==id||currentView!=='analysisPanel')return;
      if(result.state!=='complete'){$('cancelAnalysis').hidden=false;
        if(['error','cancelled'].includes(result.state)){$('analysisRetry').hidden=false;throw Error(result.message);}
        if(result.state==='not_started')await api(`/api/jobs/${id}/analysis`,{});
        $('analysisStatus').textContent=result.message||'Preparing analysis…';$('analysisStatus').classList.add('loading-status');
        analysisTimer=setTimeout(()=>{if(currentView==='analysisPanel'&&job?.id===id)loadAnalysis();},1500);return;
      }
      total=result.total;keyStatus=result.key_status;
      if(mode==='patterns'){headers=['Column','Pattern','File 1 value','File 2 value','Occurrences'];rows=result.rows.map(r=>[r[0],r[1],r[2]+(r[5]>1000?' … [truncated]':''),r[3]+(r[6]>1000?' … [truncated]':''),r[4]]);$('patternSummary').textContent=(result.categories||[]).map(([name,count])=>`${name}: ${number(count)} cells`).join(' · ')||'No mismatch patterns in this scope.';}
      else if(params.get('mode')==='keys'){headers=[...job.keys,'Status','Mismatches','Inspect'];rows=result.rows.map(r=>[...JSON.parse(r[0]),({changed:'Values differ',left_only:'Only in File 1',right_only:'Only in File 2'}[r[1]]||r[1]),r[2]]);}
      else{headers=[...job.keys,'Column','File 1 value','File 2 value'];rows=result.rows.map(r=>[...JSON.parse(r[0]),r[1],r[2]+(r[4]>1000?' … [truncated]':''),r[3]+(r[5]>1000?' … [truncated]':'')]);}
    }
    $('analysisStatus').classList.remove('loading-status');$('cancelAnalysis').hidden=true;
    const head=document.createElement('tr');headers.forEach(value=>{const th=document.createElement('th');th.textContent=value==='Inspect'?'Comments':value;head.append(th);});if(headers.at(-1)!=='Inspect'){const th=document.createElement('th');th.textContent='Comments';head.append(th);}table.append(head);
    rows.forEach(row=>{
      if(mode==='patterns'){const tr=document.createElement('tr');row.forEach((value,i)=>{const td=document.createElement('td');td.textContent=(i===2||i===3)&&value===''?'(empty)':value;if(i===2)td.className='analysis-before';if(i===3)td.className='analysis-after';tr.append(td);});const comments=document.createElement('td');comments.textContent=commentsFor(row[0],null);tr.append(comments);const td=document.createElement('td'),button=document.createElement('button');button.className='subtle';button.textContent='Review column';button.addEventListener('click',()=>{$('analysisColumnSearch').value='';analysisColumns();$('analysisMode').value='columns';$('analysisColumn').value=row[0];analysisOffset=0;loadAnalysis();});td.append(button);tr.append(td);table.append(tr);return;}
      const tr=document.createElement('tr'),isDetails=headers.at(-1)!=='Inspect';
      row.forEach((value,i)=>{const td=document.createElement('td');td.textContent=isDetails&&i>=row.length-2&&value===''?'(empty)':value;if(isDetails&&i===row.length-2)td.className='analysis-before';if(isDetails&&i===row.length-1)td.className='analysis-after';tr.append(td);});
      const comments=document.createElement('td');comments.className='analysis-comments';comments.textContent=isDetails?commentsFor(row[job.keys.length],row.slice(0,job.keys.length)):mode==='columns'?commentsFor(row[0],null):commentsFor('',row.slice(0,job.keys.length));tr.append(comments);
      const td=document.createElement('td'),button=document.createElement('button');button.className='subtle';
      if(!isDetails){button.textContent=mode==='columns'?'View mismatching keys':'View this key';button.addEventListener('click',()=>{if(mode==='columns')$('analysisColumn').value=row[0];else selectAnalysisKey(row.slice(0,job.keys.length));analysisOffset=0;loadAnalysis();});}
      else{button.textContent='All changes for this key';button.addEventListener('click',()=>{$('analysisMode').value='keys';$('analysisColumn').value='';selectAnalysisKey(row.slice(0,job.keys.length));analysisOffset=0;loadAnalysis();});}
      td.append(button);tr.append(td);table.append(tr);
    });
    {const th=document.createElement('th');th.textContent=mode==='patterns'?'Review':headers.at(-1)==='Inspect'?'Open':'Explore record';head.append(th);}
    $('analysisStatus').textContent=total?`${number(total)} ${mode==='patterns'?'value-change patterns':headers.at(-1)==='Inspect'?(mode==='columns'?'columns':'keys'):'mismatched cells'}. ${headers.at(-1)==='Inspect'?'Choose a result below to review it.':'File 1 values are shaded red; File 2 values are shaded green.'}`:keyStatus==='left_only'?'This key exists only in File 1.':keyStatus==='right_only'?'This key exists only in File 2.':keyText?'No recorded differences for this key. It may be equal, excluded, or absent from both files.':column?'No mismatches in this column.':'No results for this view. Try a different column search or switch views.';
    $('analysisRange').textContent=total?`${analysisOffset+1}–${Math.min(analysisOffset+50,total)} of ${number(total)}`:'0 results';
    $('analysisPrev').disabled=!analysisOffset;$('analysisNext').disabled=analysisOffset+50>=total;
  }catch(error){$('analysisStatus').classList.remove('loading-status');$('analysisStatus').textContent=error.message;showError(error);}
}
$('analysisByPattern').addEventListener('click',()=>resetAnalysis('patterns'));
$('analysisByColumn').addEventListener('click',()=>resetAnalysis('columns'));
$('analysisByKey').addEventListener('click',()=>resetAnalysis('keys'));
$('analysisReset').addEventListener('click',()=>resetAnalysis($('analysisMode').value));
$('analysisSearch').addEventListener('click',()=>{const values=analysisKeyValues();if(values.some(v=>v==='')){showError('Enter a value for each key field. To browse instead, choose By column or By key.');return;}selectAnalysisKey(values);analysisOffset=0;loadAnalysis();});
$('analysisColumn').addEventListener('change',()=>{analysisOffset=0;loadAnalysis();});
$('analysisColumnSearch').addEventListener('input',()=>{analysisColumns();if(!$('analysisColumn').value&&!$('analysisKey').value){analysisOffset=0;loadAnalysis();}});
$('analysisPrev').addEventListener('click',()=>{analysisOffset=Math.max(0,analysisOffset-50);loadAnalysis();});
$('analysisNext').addEventListener('click',()=>{analysisOffset+=50;loadAnalysis();});

$('analysisRetry').addEventListener('click',async()=>{try{$('analysisRetry').hidden=true;await api(endpoint('/analysis'),{});await loadAnalysis();}catch(error){showError(error);}});

let valueRules=[], settingsRules=[];
$('profileControls').insertAdjacentHTML('beforeend',`<section class="feature-box"><h3>Template library</h3><div class="preview-controls"><select id="profileSelect" aria-label="Saved profile"><option value="">Choose profile</option></select><button id="applyProfile" class="subtle">Apply profile</button><input id="profileName" placeholder="Profile name" aria-label="Profile name"><button id="saveProfile" class="subtle">Save current settings</button><button id="deleteProfile" class="subtle">Delete profile</button></div><p id="profileStatus" role="status"></p></section>`);
$('settingsRules').insertAdjacentHTML('beforeend',`<section class="feature-box"><h3>Column comparison rules</h3><p>Exact text is the default. Rules never change source values or key matching. Invalid numbers or dates remain mismatches.</p><div class="preview-controls"><label>Column<input id="ruleColumn" list="ruleColumnOptions" placeholder="Column name"><datalist id="ruleColumnOptions"></datalist></label><label><input id="ruleTrim" type="checkbox"> Trim whitespace</label><label><input id="ruleCase" type="checkbox"> Ignore case</label><label><input id="ruleNumericFormat" type="checkbox"> Ignore numeric formatting (0 = 0.00, -11 = -11.00)</label><label>Absolute numeric tolerance<input id="ruleTolerance" placeholder="e.g. 0.01"></label><label>File 1 date format<input id="ruleDateLeft" placeholder="%Y-%m-%d"></label><label>File 2 date format<input id="ruleDateRight" placeholder="%d/%m/%Y"></label><button id="addValueRule" class="subtle">Save column rule</button></div><p>Use Python date formats: %Y year, %m month, %d day, %H hour, %M minute, %S second. Choose tolerance or date formats for a column.</p><div id="valueRuleList"></div></section>`);
$('analysisPanel').insertAdjacentHTML('beforeend',`<details class="feature-box review-comments"><summary>Comments</summary><label>Apply comment to<select id="noteTarget"><option value="column">Column — all mismatching keys</option><option value="key">Key — all mismatching columns</option><option value="cell">This key and column</option></select></label><p id="noteScope" class="analysis-note-scope"></p><p class="muted">Comments appear in regenerated reports.</p><div class="preview-controls"><select id="noteStatus" aria-label="Classification"><option>Needs investigation</option><option>Expected</option><option>Resolved</option></select><textarea id="noteComment" maxlength="2000" aria-label="Analysis comment" placeholder="Describe your finding"></textarea><button id="saveNote" class="subtle">Save comment</button></div><div id="analysisNotes"></div></details>`);
function renderValueRules(){
  $('ruleColumnOptions').replaceChildren();
  for(const column of job?.columns||[])if(!selected.has(column))$('ruleColumnOptions').append(new Option(column,column));
  $('valueRuleList').replaceChildren();
  for(const rule of settingsRules){const row=document.createElement('p'),label=document.createElement('span'),edit=document.createElement('button'),remove=document.createElement('button');label.textContent=rule.column+' · '+[rule.trim?'Trim whitespace':'',rule.ignore_case?'Ignore case':'',rule.tolerance==='0'?'Ignore numeric formatting':rule.tolerance!==undefined?'Tolerance '+rule.tolerance:'',rule.left_date_format?'Date formats':''].filter(Boolean).join(' · ');edit.textContent='Edit';edit.className=remove.className='subtle';edit.addEventListener('click',()=>{$('ruleColumn').value=rule.column;$('ruleTrim').checked=!!rule.trim;$('ruleCase').checked=!!rule.ignore_case;$('ruleNumericFormat').checked=rule.tolerance==='0';$('ruleTolerance').value=rule.tolerance==='0'?'':rule.tolerance||'';$('ruleTolerance').disabled=rule.tolerance==='0';$('ruleDateLeft').value=rule.left_date_format||'';$('ruleDateRight').value=rule.right_date_format||'';});remove.textContent='Remove';remove.addEventListener('click',()=>{settingsRules=settingsRules.filter(r=>r.column!==rule.column);renderValueRules();$('settingsStatus').textContent='Unsaved changes — save settings to apply.';});row.append(label,edit,remove);$('valueRuleList').append(row);}
}
function settingsConfig(){return {memory_mb:Number($('memory').value),sort_workers:Number($('sortWorkers').value),read_batch_size:Number($('readBatchSize').value),compare_batch_size:Number($('compareBatchSize').value),comparison_rules:settingsRules};}
async function loadSettings(){const result=await api('/api/settings');$('memory').value=result.memory_mb;$('sortWorkers').value=result.sort_workers;$('readBatchSize').value=result.read_batch_size;$('compareBatchSize').value=result.compare_batch_size;settingsRules=result.comparison_rules||[];renderValueRules();$('settingsStatus').textContent='These defaults apply when a new comparison starts. Rules match column names ignoring case; key and ignored columns are excluded.';}
$('saveSettings').addEventListener('click',async()=>{try{const result=await api('/api/settings',settingsConfig());settingsRules=result.comparison_rules;renderValueRules();$('settingsStatus').textContent='Settings saved. New runs use these defaults. Existing runs are unchanged.';}catch(error){showError(error);}});
$('ruleNumericFormat').addEventListener('change',()=>{$('ruleTolerance').disabled=$('ruleNumericFormat').checked;});
$('addValueRule').addEventListener('click',async()=>{try{
  const rule={column:$('ruleColumn').value,trim:$('ruleTrim').checked,ignore_case:$('ruleCase').checked,left_date_format:$('ruleDateLeft').value,right_date_format:$('ruleDateRight').value};
  if(!rule.column)throw Error('Choose a compared non-key column.');
  if($('ruleNumericFormat').checked)rule.tolerance='0';
  else if($('ruleTolerance').value!=='')rule.tolerance=$('ruleTolerance').value;
  const proposed=[...settingsRules.filter(r=>r.column.toLowerCase()!==rule.column.toLowerCase()),rule];
  const result=await api('/api/settings',{...settingsConfig(),comparison_rules:proposed});settingsRules=result.comparison_rules;renderValueRules();$('settingsStatus').textContent='Column rule saved for future comparisons. A profile’s explicit rule for the same column takes precedence.';
}catch(e){showError(e);}});
async function loadProfiles(){const result=await api('/api/profiles');for(const id of ['profileSelect','quickProfile']){const previous=$(id).value;$(id).replaceChildren(new Option(id==='quickProfile'?'No template — configure manually':'Choose profile',''));result.profiles.forEach(p=>$(id).add(new Option(p.name,p.id)));$(id).value=previous;}const ready=job?.state==='ready';$('applyProfile').disabled=!ready;$('saveProfile').disabled=!job||!['ready','complete'].includes(job.state);}
$('saveProfile').addEventListener('click',async()=>{try{if(headerDirty&&job?.state==='ready')await applyHeaders();await saveDraft();await api('/api/profiles',{job_id:job.id,name:$('profileName').value});await loadProfiles();$('profileStatus').textContent='Template saved with headers, keys, scope, value overrides and column rules.';}catch(e){showError(e);}});
async function applyProfile(id){if(!id)throw Error('Choose a template first.');job=await api(endpoint('/apply-profile'),{id});hydratedId=null;headerDraftJob=null;hydrate();renderHeaders();renderKeys();renderIgnoredColumns();renderOverrides();$('quickProfileStatus').textContent='Template applied. Review the headers, keys and overrides before running.';$('profileStatus').textContent=$('quickProfileStatus').textContent;}
$('applyProfile').addEventListener('click',()=>applyProfile($('profileSelect').value).catch(showError));
$('quickProfile').addEventListener('change',async()=>{
  if(job?.state==='ready'&&$('quickProfile').value){try{await applyProfile($('quickProfile').value);}catch(error){showError(error);}}
  else $('quickProfileStatus').textContent=$('quickProfile').value?'Template will be applied after files and worksheets are loaded.':'No template selected.';
  document.querySelector('.upload-profile').open=false;
});
async function applyPendingUploadProfile(){
  if(job?.state!=='ready')return;
  const key='upload-profile:'+job.id,id=localStorage.getItem(key);
  if(!id)return;
  try{await applyProfile(id);}
  catch(error){showError('Template could not be applied: '+error.message+' Select another template on the Files page, or configure this comparison manually.');}
  finally{localStorage.removeItem(key);}
}
loadProfiles().catch(showError);
$('manageProfiles').addEventListener('click',()=>{document.querySelector('.upload-profile').open=false;goView('profilesPanel').catch(showError);});
document.querySelector('.upload-profile').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.currentTarget.open=false;event.currentTarget.querySelector('summary').focus();}
});
$('deleteProfile').addEventListener('click',async()=>{try{if(!confirm('Delete this saved profile? Existing jobs are unaffected.'))return;await api('/api/profiles',{id:$('profileSelect').value},'DELETE');await loadProfiles();}catch(e){showError(e);}});
$('headerContinue').addEventListener('click',()=>setTimeout(()=>loadProfiles().catch(showError),300));
async function cancelTask(id,kind){if(!confirm(`Cancel ${kind}? Active work will stop at its next safe checkpoint.`))return;try{const result=await api(`/api/jobs/${id}/cancel`,{kind});$('completionNotice').textContent=result.message;if(job?.id===id)await refresh();}catch(e){showError(e);}}
$('cancelComparison').addEventListener('click',()=>cancelTask(job.id,'comparison'));
$('cancelExcel').addEventListener('click',()=>cancelTask(job.id,'excel'));
$('cancelHtml').addEventListener('click',()=>cancelTask(job.id,'html'));
$('cancelAnalysis').addEventListener('click',()=>cancelTask(job.id,'analysis'));
async function loadStorage(){
  $('storageStatus').textContent='Measuring your workspace files…';$('storageStatus').classList.add('loading-status');
  try{const data=await api('/api/storage');$('storageStatus').textContent=`${bytes(data.free)} free of ${bytes(data.total)} server disk · ${data.max_jobs} heavy jobs can run at once · ${data.retention_days?data.retention_days+' days retention':'Automatic cleanup disabled'}`;
    const table=$('storageTable');table.replaceChildren();const head=document.createElement('tr');['Job','State','Uploads','Reports','Analysis','Temporary / metadata','Work queue','Cleanup'].forEach(t=>{const th=document.createElement('th');th.textContent=t;head.append(th);});table.append(head);
    for(const item of data.jobs){const row=document.createElement('tr');[item.files.left.name+' ↔ '+item.files.right.name,item.state,...['uploads','reports','analysis','temporary'].map(k=>bytes(item.sizes[k]))].forEach(v=>{const td=document.createElement('td');td.textContent=v;row.append(td);});const tasks=document.createElement('td');for(const task of item.tasks){const line=document.createElement('p'),button=document.createElement('button');line.textContent=`${task.kind}: ${task.cancelling?'Cancelling':task.state} `;button.textContent='Cancel';button.disabled=task.cancelling;button.addEventListener('click',async()=>{await cancelTask(item.id,task.kind);await loadStorage();});line.append(button);tasks.append(line);}row.append(tasks);const td=document.createElement('td'),button=document.createElement('button');button.textContent='Delete job';button.disabled=item.busy;button.addEventListener('click',async()=>{if(!confirm('Permanently delete this job, uploads and all reports?'))return;try{await api(`/api/jobs/${item.id}`,undefined,'DELETE');if(job?.id===item.id){localStorage.removeItem('keywise-job');location.reload();return;}await loadStorage();}catch(e){showError(e);}});td.append(button);row.append(td);table.append(row);}
  }catch(e){showError(e);$('storageStatus').textContent='Unable to measure storage.';}finally{$('storageStatus').classList.remove('loading-status');}
}
$('storageRefresh').addEventListener('click',loadStorage);
async function loadDiagnostics(){const result=await api(endpoint('/diagnostics'));const box=$('duplicateDiagnostics');box.hidden=!result.key;box.replaceChildren();if(result.key){const title=document.createElement('h3'),detail=document.createElement('p'),scroll=document.createElement('div'),table=document.createElement('table');title.textContent=`Duplicate key in ${result.side} file · ${number(result.count)} rows`;detail.textContent=JSON.stringify(result.key)+' · '+result.note;scroll.className='table-scroll';table.id='duplicateSampleTable';scroll.append(table);box.append(title,detail,scroll);drawTable('duplicateSampleTable',result.sample_columns,result.samples);}}
function selectedCommentTarget(){
  const text=$('analysisKey').value,key=text===''?null:job.keys.length===1?[text]:JSON.parse(text),kind=$('noteTarget').value;
  return {column:kind==='key'?'':$('analysisColumn').value,key:kind==='column'?null:key};
}
function commentLabel(note){return (note.column?'Column '+note.column:'All columns')+' · '+(note.key?job.keys.map((k,i)=>k+': '+note.key[i]).join(', '):'All keys');}
function commentsFor(column,key){return analysisNotesCache.filter(n=>(!n.column||n.column===column)&&(n.key===null||key!==null&&JSON.stringify(n.key)===JSON.stringify(key))).map(n=>`${n.key?(n.column?'Key + column':'Key comment'):'Column comment'} · ${n.status}: ${n.comment}`).join('\n')||'—';}
function renderCommentScope(){const target=selectedCommentTarget(),kind=$('noteTarget').value;const valid=kind==='key'?!!target.key:kind==='column'?!!target.column:!!target.key&&!!target.column;$('saveNote').disabled=!valid;$('noteScope').textContent=valid?'Comment applies to '+commentLabel(target)+'.':'Select '+(kind==='key'?'a key':kind==='column'?'a column':'a key and column')+' above to add a comment.';}
function fillCommentEditor(){const target=selectedCommentTarget(),note=analysisNotesCache.find(n=>n.column===target.column&&JSON.stringify(n.key)===JSON.stringify(target.key));$('noteComment').value=note?.comment||'';$('noteStatus').value=note?.status||'Needs investigation';renderCommentScope();}
$('noteTarget').addEventListener('change',fillCommentEditor);
async function loadNotes(){const result=await api(endpoint('/annotations'));analysisNotesCache=result.notes;$('analysisNotes').replaceChildren();for(const note of result.notes.slice(0,100)){const p=document.createElement('p'),label=document.createElement('span'),edit=document.createElement('button');label.textContent=commentLabel(note)+' · '+note.status+': '+note.comment;edit.textContent='View / edit';edit.className='subtle';edit.addEventListener('click',async()=>{$('analysisMode').value=note.key?'keys':'columns';$('analysisColumnSearch').value='';analysisColumns();$('analysisColumn').value=note.column;selectAnalysisKey(note.key);analysisOffset=0;await loadAnalysis();$('noteTarget').value=note.key?(note.column?'cell':'key'):'column';fillCommentEditor();document.querySelector('.review-comments').open=true;$('noteComment').focus();});p.append(label,edit);$('analysisNotes').append(p);}if(!result.notes.length)$('analysisNotes').textContent='No comments yet. Select a column or key to begin.';if(result.notes.length>100){const p=document.createElement('p');p.textContent=`Showing 100 of ${result.notes.length} saved comments. Applicable comments still appear on every mismatch row.`;$('analysisNotes').append(p);}}
$('saveNote').addEventListener('click',async()=>{try{await api(endpoint('/annotations'),{...selectedCommentTarget(),status:$('noteStatus').value,comment:$('noteComment').value});await loadNotes();await loadAnalysis();job=await api(endpoint());renderExports();}catch(e){showError(e);}});

let notificationSnapshot=null,notificationPolling=false,notificationSince=Date.now()/1000;
$('enableNotifications').addEventListener('click',async()=>{if(!('Notification' in window)){showError('This browser does not support desktop notifications.');return;}try{const permission=await Notification.requestPermission();localStorage.setItem('comparison-notifications',permission==='granted'?'on':'off');$('notificationStatus').textContent=permission==='granted'?'Enabled while this page remains open.':'Notifications were not enabled. Check your browser permissions.';notificationSnapshot=null;notificationSince=Date.now()/1000;pollNotifications();}catch(e){showError(e);}});
$('disableNotifications').addEventListener('click',()=>{localStorage.setItem('comparison-notifications','off');notificationSnapshot=null;$('notificationStatus').textContent='Notifications disabled.';});
async function pollNotifications(){
 if(notificationPolling||localStorage.getItem('comparison-notifications')!=='on')return;notificationPolling=true;
 try{const result=await api('/api/activity'),next=new Map();for(const item of result.activities){const id=item.id+':'+item.kind;next.set(id,item.state);const old=notificationSnapshot?.get(id);if(notificationSnapshot&&old!==item.state&&(old||item.changed_at>=notificationSince)&&['complete','error','cancelled'].includes(item.state)){const message=`${item.kind}: ${item.state==='complete'?'Completed':item.state==='error'?'Failed':'Cancelled'}`;$('completionNotice').textContent=message;if('Notification' in window&&Notification.permission==='granted')new Notification('Comparison tool',{body:message,tag:id});}}notificationSnapshot=next;}catch(e){/* Transient network failure; retry on the next interval. */}finally{notificationPolling=false;}
}
setInterval(pollNotifications,10000);pollNotifications();

// Guidance works with pointer hover, keyboard focus and touch disclosure.
for(const help of document.querySelectorAll('.inline-help')){
  help.addEventListener('pointerenter',()=>{help.open=true;});
  help.addEventListener('pointerleave',()=>{if(!help.contains(document.activeElement))help.open=false;});
  help.addEventListener('focusin',()=>{help.open=true;});
  help.addEventListener('focusout',event=>{if(!help.contains(event.relatedTarget))help.open=false;});
  help.addEventListener('keydown',event=>{if(event.key==='Escape'){help.open=false;event.stopPropagation();}});
}
