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
let lastComparisonView='uploadPanel';
document.body.insertAdjacentHTML('beforeend','<div id="comparisonMenu" class="comparison-menu" role="menu" aria-label="CSV and Excel actions" hidden><button id="menuNewComparison" role="menuitem">＋ New comparison</button><button id="menuContinueComparison" role="menuitem">↪ Continue current selection</button></div>');
$('navNew').setAttribute('aria-haspopup','menu');$('navNew').setAttribute('aria-expanded','false');$('navNew').setAttribute('aria-controls','comparisonMenu');
$('historyPanel').querySelector('.section-title').append($('reset'));
$('reset').textContent='New comparison';$('reset').className='primary';
$('navNew').setAttribute('aria-label','Compare files');$('navNew').dataset.tip='Compare files';
$('navNew').querySelector('.side-label').textContent='Compare files';
$('navNew').querySelector('.side-description').textContent='Comparison workspace';
$('scopeNext').innerHTML='Start comparison <span>→</span>';
$('sourcePanel').insertAdjacentHTML('beforeend','<button id="compare" hidden disabled>Start comparison</button>');
const inlinePreview=document.createElement('div');inlinePreview.className='inline-preview';
inlinePreview.append($('sourcePanel'));$('uploadPanel').append(inlinePreview);
$('sourcePanel').hidden=false;$('sourcePanel').inert=true;$('sourcePanel').setAttribute('aria-hidden','true');
$('uploadPanel').querySelector('.section-title').append($('navPreview'));
$('navPreview').setAttribute('aria-expanded','false');$('navPreview').setAttribute('aria-controls','sourcePanel');
let previewExpanded=false;
$('analysisPanel').insertAdjacentHTML('afterbegin','<button id="reviewOverrides" class="subtle review-overrides-action">Review value overrides</button>');
const phaseLabels=['Files','Column headers','Keys & scope','Pipeline & logs','Results'];
const phaseViews=['uploadPanel','headersPanel','keysPanel','runningPanel','results'];
const views={uploadPanel:'navFiles',headersPanel:'navHeaders',keysPanel:'navScope',overridesPanel:'navOverrides',runningPanel:'navPipeline',results:'navResults',analysisPanel:'navAnalysis',historyPanel:'navHistory',containersPanel:'navContainers',jsonPanel:'navJson',docsPanel:'navDocs',storagePanel:'navStorage',profilesPanel:'navProfiles',settingsPanel:'navSettings',projectsPanel:'navProjects'};
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
  $('navScope').disabled=!(ready||job?.state==='complete')||uploading||(ready&&job.headers_reviewed===false);
  $('navOverrides').disabled=job?.state!=='complete'||uploading;
  $('navPreview').disabled=!job||!['ready','queued','running','complete'].includes(job.state)||uploading;
  $('navPipeline').disabled=!job||!['queued','running','complete','error','cancelled'].includes(job.state);
  if(ready&&job.headers_reviewed===false)$('navOverrides').disabled=true;
  $('cancelComparison').disabled=!['running','queued'].includes(job?.state);
  $('navResults').disabled=$('navAnalysis').disabled=job?.state!=='complete';
  $('navJson').disabled=$('navContainers').disabled=$('navHistory').disabled=$('navNew').disabled=false;
  $('reset').disabled=uploading;
}
function panel(name) {
  if(phaseViews.includes(name)||name==='jsonPanel')lastComparisonView=name;
  if(typeof updateReviewWorkspace==='function')updateReviewWorkspace(name);
  if(currentView!==name)window.scrollTo(0,0);
  currentView=name;
  $('mainWorkspace').classList.toggle('docs-view',name==='docsPanel');
  for (const [id,nav] of Object.entries(views)){ $(id).hidden=id!==name; $(nav).classList.toggle('active',id===name); }
  $('sheetPanel').hidden=name!=='uploadPanel'||!['selecting_sheets','preparing'].includes(job?.state);
  $('reset').hidden=false;
  const history=currentView==='historyPanel';
  const library=['projectsPanel','containersPanel','jsonPanel','docsPanel','storagePanel','profilesPanel','settingsPanel'].includes(currentView);
  $('comparisonFlow').hidden=history||library;
  $('navNew').classList.toggle('active',name==='jsonPanel'||(!history&&!library&&!viewingHistoryJob));
  $('navHistory').classList.toggle('active',history||(!library&&viewingHistoryJob));
  for(const id of ['navProjects','navNew','navJson','navContainers','navHistory','navDocs','navStorage','navProfiles','navSettings']){if($(id).classList.contains('active'))$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');}
  if(typeof closeSidebarDrawer==='function')closeSidebarDrawer();
  $('flowTitle').textContent=viewingHistoryJob?'Saved comparison':'New comparison';
  const phase=phaseViews.indexOf(name);
  if(!history&&!library)$('phaseLabel').textContent=name==='overridesPanel'?'Review · Value overrides':`Step ${phase+1} of 5 · ${phaseLabels[phase]}`;
  for(const [id,control] of Object.entries(views)){
    if(id!=='historyPanel'&&id!=='containersPanel'&&id!=='jsonPanel'&&id!=='docsPanel'&&id!=='storagePanel'&&id!=='profilesPanel'&&id!=='settingsPanel'){if(id===name)$(control).setAttribute('aria-current','step');else $(control).removeAttribute('aria-current');}
  }
  syncNavigation();
  if(typeof updateEnterpriseShell==='function')updateEnterpriseShell(name);
  if(name==='uploadPanel'&&job&&job.state!=='uploading'){
    for(const side of ['left','right']){$(`${side}File`).disabled=true;$(`${side}Name`).textContent=job.files[side].name;$(`${side}Size`).textContent=bytes(job.files[side].size)+' · uploaded';}
    $('upload').disabled=true;$('upload').textContent='Files uploaded';
  } else if(name==='uploadPanel'){
    for(const side of ['left','right'])$(`${side}File`).disabled=uploading;
    $('upload').textContent='Upload & continue →';$('upload').disabled=uploading||!files.left||!files.right;
  }
}
function configuration(){return {duplicate_policy:$('duplicatePolicy').value,keys:[...selected],ignore_columns:[...ignoredColumns],ignore_keys:$('ignoreKeys').value,ignore_container_ids:[...selectedContainers],value_overrides:overrideRules,comparison_rules:valueRules.filter(r=>!selected.has(r.column)&&!ignoredColumns.has(r.column))};}
async function saveDraft(){if(job?.state==='ready')await api(endpoint('/config'),configuration());}
async function goView(name){
  if(name==='analysisPanel'&&typeof showAnalysisDrawer==='function'){await showAnalysisDrawer();return;}
  if(uploading&&name!=='uploadPanel'&&!['jsonPanel','containersPanel','historyPanel','docsPanel'].includes(name))return;
  clearError();await saveDraft();
  if(headerDirty&&headerDraftJob===job?.id&&['keysPanel','overridesPanel','sourcePanel'].includes(name))await applyHeaders();
  panel(name);
  if(name==='settingsPanel')await loadSettings();
  if(name==='projectsPanel')await loadProjects();
  if(name==='uploadPanel')await loadUploadProjects();
  if(name==='profilesPanel')await loadProfiles();
  if(name==='storagePanel')await loadStorage();
  if(name==='analysisPanel')await openAnalysis();
  if(name==='headersPanel'){renderHeaders();await loadProfiles();}
  if(name==='keysPanel'){renderKeys();renderIgnoredColumns();await loadContainers();await loadProfiles();}
  if(name==='containersPanel')await loadContainers();
  if(name==='overridesPanel'){renderOverrideColumns();renderOverrides();renderValueRules();}
  if(name==='historyPanel')await loadHistory();
  if(name==='runningPanel'){await refresh();await loadDiagnostics();}
  if(name==='results'){renderSummary();renderExports();}
}
for(const [view,nav] of Object.entries(views))$(nav).addEventListener('click',()=>goView(view).catch(showError));
function choose(side, file) {
  if (uploading || (job && job.state!=='uploading')) return;
  if(file&&!/\.(csv|xlsx|xlsm|json|parquet)$/i.test(file.name)){showError('Choose CSV, Parquet, JSON, .xlsx or .xlsm. Save older .xls files as .xlsx first.');return;}
  files[side] = file || null;
  $(`${side}Name`).textContent = file ? file.name : `Choose ${side==='left'?'original':'updated'} CSV, Excel, Parquet or JSON`;
  $(`${side}Size`).textContent = file ? bytes(file.size) : 'Click to browse or drop a file here';
  $('upload').disabled = !files.left || !files.right;
  updateDetectedFiles();
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
  if(files.left&&files.right){const left=detectedFileType(files.left),right=detectedFileType(files.right);if((left==='JSON')!==(right==='JSON')){showError('Choose two JSON files, or a pair of CSV/Excel/Parquet files. JSON cannot be compared to a table.');return;}if(left==='JSON'){await loadUnifiedJson();return;}}
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
    await attachUploadProject(job.id);
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
    const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(column);input.disabled=job.state!=='ready'||ignoredColumns.has(column);
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
    const input=document.createElement('input');input.type='checkbox';input.checked=ignoredColumns.has(column);input.disabled=job.state!=='ready'||selected.has(column);
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
  $('compare').disabled=$('scopeNext').disabled=job.state!=='ready'||!selected.size;
  $('ignoreKeys').disabled=$('duplicatePolicy').disabled=job.state!=='ready';
  $('ignoreKeysHint').textContent=selected.size>1?`Composite key order: ${[...selected].join(', ')}. Enter JSON tuples, for example [["001","A"],["002","B"]] for two keys.`:'Enter comma-separated key values, for example 001, 002, 003. Put quotes around values containing commas or leading spaces.';
  $('ignoreKeys').placeholder=selected.size>1?'[["001","A"],["002","B"]]':'001, 002, 003';
}
$('keySearch').addEventListener('input',()=>{keyPage=0;renderKeys();});
$('ignoreColumnSearch').addEventListener('input',()=>{ignorePage=0;renderIgnoredColumns();});
$('keyPrev').addEventListener('click',()=>{keyPage--;renderKeys();});
$('keyNext').addEventListener('click',()=>{keyPage++;renderKeys();});
$('ignorePrev').addEventListener('click',()=>{ignorePage--;renderIgnoredColumns();});
$('ignoreNext').addEventListener('click',()=>{ignorePage++;renderIgnoredColumns();});
async function startComparison(){
  if(job?.state!=='ready'||!selected.size||$('compare').disabled)return;
  clearError();$('compare').disabled=$('scopeNext').disabled=true;
  try {job=await api(endpoint('/start'),configuration());panel('runningPanel');await refresh();}
  catch(error){showError(error);$('compare').disabled=$('scopeNext').disabled=false;}
}
$('compare').addEventListener('click',startComparison);
function renderSummary() {
  const s=job.summary;
  renderResultChart(s);
  const changes=Object.entries(s.changed_cells_by_column).filter(([,count])=>count>0).sort((a,b)=>b[1]-a[1]);
  $('resultTitle').textContent=s.changed_rows||s.left_only||s.right_only?'Your comparison is ready.':'Both files match within the selected scope.';
  $('resultSubtitle').textContent=`${number(s.left_rows)} left rows · ${number(s.right_rows)} right rows · Keys: ${s.keys.join(', ')}`;
  $('scopeSummary').textContent=`${number(s.ignored_columns?.length||0)} columns ignored · ${number(s.left_excluded_rows||0)} left rows and ${number(s.right_excluded_rows||0)} right rows excluded by key. ${number(s.value_overrides?.length||0)} value overrides; ${number((s.override_equivalent_cells||0)+(s.rule_equivalent_cells||0))} unequal cells accepted by rules. Counts refer to the selected scope.`;
  const duplicates=(s.left_duplicate_rows_skipped||0)+(s.right_duplicate_rows_skipped||0);
  $('duplicateDownload').hidden=!duplicates;$('duplicateDownload').href=downloadUrl('duplicate_keys.csv');
  if(duplicates){$('resultTitle').textContent='Comparison complete — duplicate keys found';$('scopeSummary').textContent+=` Warning: ${number(s.left_duplicate_rows_skipped||0)} file 1 rows and ${number(s.right_duplicate_rows_skipped||0)} file 2 rows skipped as duplicates. Kept the ${s.duplicate_policy||'first'} source occurrence per key. Download the duplicate audit for details.`;}
  if(s.review_revision){const r=s.review_revision;$('scopeSummary').textContent+=` Review revision: ${number(r.original_mismatches)} original mismatches · ${number(r.accepted_by_rules)} accepted · ${number(r.excluded_from_scope)} excluded · ${number(r.remaining_mismatches)} remaining.`;}
  $('duration').textContent=`${s.elapsed_seconds.toFixed(s.elapsed_seconds<1?3:1)} seconds`;
  $('metrics').replaceChildren();
  for(const [label,value,style] of [['Equal rows',s.equal_rows,'accent'],['Changed rows',s.changed_rows,'amber'],['Changed cells',s.changed_cells,'amber'],['Left-only keys',s.left_only,''],['Right-only keys',s.right_only,'']]) {
    const card=document.createElement('div');card.className=`metric ${style}`;
    const title=document.createElement('small');title.textContent=label;
    const count=document.createElement('strong');count.textContent=number(value);card.append(title,count);$('metrics').append(card);
  }
  $('changedColumnCount').textContent=`${changes.length} columns`;$('columnSummary').replaceChildren();
  for(const [name,count] of changes) {
    const row=document.createElement('button');row.type='button';row.className='column-row';row.setAttribute('aria-label',`Analyze ${name}, ${number(count)} mismatches`);row.addEventListener('click',()=>showAnalysisDrawer(name).catch(showError));
    const label=document.createElement('span');label.textContent=name;
    const value=document.createElement('strong');value.textContent=number(count);row.append(label,value);$('columnSummary').append(row);
  }
  if(!changes.length)$('columnSummary').textContent='No changed values for matching keys.';
  for(const [id,file] of [['csvDownload','differences.csv'],['leftDownload','left_only.csv'],['rightDownload','right_only.csv'],['summaryDownload','summary.html']]) $(id).href=downloadUrl(file);
}
function renderResultChart(s){
  const rows=[['Matching keys',s.equal_rows||0,'#009bbd'],['Mismatching keys',s.changed_rows||0,'#d87520'],['Left-only keys',s.left_only||0,'#7857a5'],['Right-only keys',s.right_only||0,'#bd4567']];
  const total=rows.reduce((n,r)=>n+r[1],0);let offset=0;
  const slices=rows.filter(r=>r[1]>0).map(([label,value,color])=>{const size=value/total*100;const segment=`<circle cx="80" cy="80" r="62" pathLength="100" fill="none" stroke="${color}" stroke-width="22" stroke-dasharray="${size} ${100-size}" stroke-dashoffset="${-offset}" transform="rotate(-90 80 80)"><title>${label}: ${number(value)} (${(size).toFixed(2)}%)</title></circle>`;offset+=size;return segment;});
  $('resultKeyTotal').textContent=number(total)+' unique keys';
  $('resultDonut').innerHTML=`<svg viewBox="0 0 160 160" role="img" aria-label="${rows.map(([label,value])=>label+': '+number(value)).join(', ')}"><circle cx="80" cy="80" r="62" fill="none" stroke="#e5edf0" stroke-width="22"/>${slices.join('')}<text x="80" y="77" text-anchor="middle" class="result-chart-number">${total?((s.equal_rows||0)/total*100).toFixed(1)+'%':'—'}</text><text x="80" y="96" text-anchor="middle" class="result-chart-caption">keys matched</text></svg>`;
  $('resultChartLegend').innerHTML=rows.map(([label,value,color])=>`<div><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="${color}"/></svg><span>${label}</span><strong>${number(value)}</strong></div>`).join('')+`<p><strong>${number(s.changed_cells||0)}</strong> mismatched cells · <strong>${Object.values(s.changed_cells_by_column).filter(n=>n>0).length}</strong> columns with differences</p>`;
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches)$('resultDonut').animate([{opacity:0,transform:'scale(.88) rotate(-18deg)'},{opacity:1,transform:'scale(1) rotate(0)'}],{duration:700,easing:'cubic-bezier(.22,1,.36,1)'});
}
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
const headerTitleGroup=$('headersPanel').querySelector('.section-title>div');headerTitleGroup.classList.add('header-title-group');
const headerSearchBox=document.createElement('div');headerSearchBox.className='header-search-box';
headerSearchBox.insertAdjacentHTML('beforeend','<button id="headerSearchToggle" type="button" aria-label="Search headers" title="Search headers" aria-expanded="false" aria-controls="headerSearch"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></button>');
headerSearchBox.append($('headerSearch'));headerTitleGroup.append(headerSearchBox);$('headerSearch').tabIndex=-1;$('headerSearch').inert=true;
function setHeaderSearchOpen(open){headerSearchBox.classList.toggle('is-expanded',open);$('headerSearchToggle').setAttribute('aria-expanded',String(open));$('headerSearch').tabIndex=open?0:-1;$('headerSearch').inert=!open;}
$('headerSearchToggle').addEventListener('click',()=>{setHeaderSearchOpen(true);$('headerSearch').focus();});
$('headerSearch').addEventListener('focus',()=>setHeaderSearchOpen(true));
$('headerSearch').addEventListener('blur',()=>{if(!$('headerSearch').value)setHeaderSearchOpen(false);});
$('headerSearch').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('headerSearch').value='';headerPage=0;renderHeaders();setHeaderSearchOpen(false);$('headerSearchToggle').focus();}});
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
    if(currentView==='runningPanel'&&['running','queued'].includes(previousState))await renderPipeline();
    if(currentView==='results'){
      renderExports();
      if(renderedId!==job.id){renderSummary();renderedId=job.id;}
    } else if(currentView==='runningPanel')await renderPipeline();
    if(Object.values(job.exports).some(value=>['queued','running'].includes(value.state)))pollTimer=setTimeout(()=>refresh().catch(pollError),1500);
  } else if(['error','cancelled'].includes(job.state)){
    if(!['historyPanel','containersPanel','jsonPanel','docsPanel','storagePanel','analysisPanel','profilesPanel','settingsPanel'].includes(currentView)){panel('runningPanel');await renderPipeline();showError(job.error);await loadDiagnostics();}
  } else if(!['historyPanel','containersPanel','jsonPanel','docsPanel','storagePanel','analysisPanel','profilesPanel','settingsPanel'].includes(currentView)){
    panel('uploadPanel');$('delimiter').disabled=$('encoding').disabled=true;$('delimiter').value=job.delimiter==='\t'?'tab':job.delimiter;$('encoding').value=job.encoding;
  }
}
function pollError(error){showError(error);pollTimer=setTimeout(()=>refresh().catch(pollError),5000);}
$('reset').addEventListener('click',async()=>{
  if(uploading)return;
  try{await saveDraft();location.reload();}catch(error){showError(error);}
});
async function restoreCurrentComparison(){
  const id=localStorage.getItem('keywise-job');
  if(id&&/^[a-f0-9]{32}$/.test(id)){
    job={id};
    try{job=await api(endpoint());currentView=job.state==='complete'?'results':['running','queued','error','cancelled'].includes(job.state)?'runningPanel':job.state==='ready'?(job.headers_reviewed===false?'headersPanel':'keysPanel'):'uploadPanel';panel(currentView);await refresh();if(job.state==='uploading')showError(`Upload not finished. Reselect ${job.files.left.name} and ${job.files.right.name} to resume, or start a new comparison.`);}
    catch(error){job=null;showError(error);$('reset').hidden=false;}
  }
}

$('scopeNext').addEventListener('click',startComparison);
$('navPreview').addEventListener('click',async()=>{
  previewExpanded=!previewExpanded;
  $('uploadPanel').classList.toggle('preview-expanded',previewExpanded);
  $('sourcePanel').inert=!previewExpanded;
  $('sourcePanel').setAttribute('aria-hidden',String(!previewExpanded));
  $('navPreview').setAttribute('aria-expanded',String(previewExpanded));
  $('navPreview').textContent=previewExpanded?'Close preview':'Preview files';
  if(previewExpanded){$('sourceStatus').textContent='Loading preview…';try{await loadSource();}catch(error){$('sourceStatus').textContent='Preview could not load. Use Load preview to retry.';showError(error);}}
});
$('overrideBack').addEventListener('click',()=>goView('analysisPanel').catch(showError));
$('reviewOverrides').addEventListener('click',()=>goView('overridesPanel').catch(showError));
$('reviewCompare').addEventListener('click',async()=>{
  clearError();$('reviewCompare').disabled=true;
  try{
    job=await api(endpoint('/review-copy'),{value_overrides:overrideRules});
    localStorage.setItem('keywise-job',job.id);hydratedId=null;headerDraftJob=null;renderedId=null;hydrate();
    job=await api(endpoint('/start'),configuration());panel('runningPanel');await refresh();
  }catch(error){if(job?.state==='ready'){panel('keysPanel');renderKeys();renderIgnoredColumns();}showError(error);}finally{$('reviewCompare').disabled=job?.state!=='complete';}
});
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
  const locked=!['ready','complete'].includes(job.state);
  document.querySelector('.rule-builder').hidden=locked;
  $('overrideBack').hidden=locked;$('reviewCompare').disabled=job.state!=='complete';
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
  if($('viewResults'))$('viewResults').hidden=job.state!=='complete';
  $('resumeComparison').hidden=!job.can_resume;
  const stages=job.execution_mode==='review'?[['compare','Apply rules to retained results'],['reports','Write results & reports'],['complete','Complete']]:[['validate','Validate configuration'],...(job.sort_workers===2?[['sort','Read & sort both files']]:[['left','Read & sort file 1'],['right','Read & sort file 2']]),['compare','Compare matching keys'],['reports','Write results & reports'],['complete','Complete']];
  const stage=job.state==='complete'?'complete':job.progress?.stage||'validate';const index=stages.findIndex(([id])=>id===stage);
  $('phase').textContent=job.state==='complete'?'Comparison complete':job.state==='error'?'Comparison failed':job.state==='queued'?'Queued':job.progress?.phase||'Starting worker';
  $('processed').textContent=job.state==='error'?job.error:job.progress?.rows!==undefined?`${number(job.progress.rows)} rows processed in the current phase`:job.state==='queued'?'Waiting for a server worker. Your files and results are kept in your own job.':'Stages update as the worker processes the files.';
  $('pipelineState').textContent=job.state;
  const signature=job.id+':'+stages.map(s=>s[0]).join(',');
  if($('pipelineStages').dataset.signature!==signature){$('pipelineStages').replaceChildren();stages.forEach(()=>{const item=document.createElement('li');$('pipelineStages').append(item);});$('pipelineStages').dataset.signature=signature;}
  stages.forEach(([id,label],i)=>{const item=$('pipelineStages').children[i];const state=job.state==='queued'?'pending':job.state==='complete'||i<index?'done':i===index?(job.state==='error'?'failed':'active'):'pending';if(item.className!==state){item.className=state;item.textContent=`${state==='done'?'✓':state==='failed'?'!':i+1}  ${label}`;}});
  if(logJobId!==job.id){logJobId=job.id;logCursor=0;logText='';consolePaused=false;consoleFollow=true;if($('consolePause')){$('consolePause').textContent='Pause display';$('consolePause').setAttribute('aria-pressed','false');$('consoleFollow').setAttribute('aria-pressed','true');}}
  const id=job.id;const log=await api(endpoint('/logs')+'?cursor='+logCursor);if(job.id!==id)return;
  logCursor=log.cursor;logText=(logText+log.text).split('\n').slice(-1000).join('\n');paintConsole();
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
  clearTimeout(pollTimer);await saveDraft();clearError();viewingHistoryJob=true;job=await api('/api/jobs/'+id);await loadUploadProjects();localStorage.setItem('keywise-job',id);renderedId=null;hydratedId=null;hydrate();
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
// A single line-icon style keeps destinations distinct in the compact rail.
const navigationIcons={
  navNew:'<rect x="3" y="4" width="7" height="16" rx="2"/><rect x="14" y="4" width="7" height="16" rx="2"/><path d="M6 8h1m10 8h1M8 12h8m-2-2 2 2-2 2"/>',
  navJson:'<path d="M8 4H6a2 2 0 0 0-2 2v3l-2 3 2 3v3a2 2 0 0 0 2 2h2m8-16h2a2 2 0 0 1 2 2v3l2 3-2 3v3a2 2 0 0 1-2 2h-2M10 9l-2 3 2 3m4-6 2 3-2 3"/>',
  navProfiles:'<rect x="7" y="7" width="14" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h2m4-5h6m-6 4h6m-6 3h3"/>',
  navSettings:'<path d="M4 7h8m4 0h4M4 17h3m4 0h9"/><circle cx="14" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  navContainers:'<path d="M3 7l9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10m-5-5h3"/>',
  navStorage:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5"/>',
  navHistory:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
  navDocs:'<path d="M12 6v15M3 4h5a5 5 0 0 1 4 2 5 5 0 0 1 4-2h5v15h-5a5 5 0 0 0-4 2 5 5 0 0 0-4-2H3V4Zm3 4h2m8 0h2M6 12h2m8 0h2"/>'
};
for(const [id,paths] of Object.entries(navigationIcons)){
  const icon=$(id).querySelector('.side-icon');
  if(icon)icon.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}
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
function closeComparisonMenu(){ $('comparisonMenu').hidden=true;$('navNew').setAttribute('aria-expanded','false'); }
$('navNew').addEventListener('click',()=>{
  const menu=$('comparisonMenu');if(!menu.hidden){closeComparisonMenu();return;}
  $('menuNewComparison').disabled=uploading;
  $('menuContinueComparison').disabled=!(job||files.left||files.right||localStorage.getItem('keywise-job'));
  const rect=$('navNew').getBoundingClientRect();menu.style.left=Math.min(rect.right+10,window.innerWidth-290)+'px';menu.style.top=Math.min(rect.top,window.innerHeight-115)+'px';menu.hidden=false;$('navNew').setAttribute('aria-expanded','true');
  (uploading?$('menuContinueComparison'):$('menuNewComparison')).focus();
});
$('menuNewComparison').addEventListener('click',async()=>{if(uploading)return;try{await saveDraft();closeComparisonMenu();location.reload();}catch(error){showError(error);}});
$('menuContinueComparison').addEventListener('click',async()=>{
  closeComparisonMenu();clearError();
  try{if(!job&&!files.left&&!files.right){await restoreCurrentComparison();return;}viewingHistoryJob=false;await goView(uploading?'uploadPanel':lastComparisonView);}catch(error){showError(error);}
});
document.addEventListener('pointerdown',event=>{if(!$('comparisonMenu').contains(event.target)&&!$('navNew').contains(event.target))closeComparisonMenu();});
$('comparisonMenu').addEventListener('keydown',event=>{
  if(event.key==='Escape'){closeComparisonMenu();$('navNew').focus();event.preventDefault();}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const options=[$('menuNewComparison'),$('menuContinueComparison')].filter(b=>!b.disabled);const index=options.indexOf(document.activeElement);options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+1)%options.length]?.focus();}
  if(event.key==='Tab')closeComparisonMenu();
});
window.addEventListener('resize',closeComparisonMenu);
$('workspaceSidebar').addEventListener('scroll',closeComparisonMenu);

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
    jsonResult=await api('/api/json-compare',{left:$('jsonLeft').value,right:$('jsonRight').value,default_order:$('jsonDefault').value,rules:jsonRules,source_names:[files.left?.name||'File 1 JSON',files.right?.name||'File 2 JSON']});
    await attachUploadProject(jsonResult.audit_id);
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
  if(typeof renderAiFindings==='function')renderAiFindings().catch(showError);
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
$('analysisPanel').insertAdjacentHTML('beforeend',`<details class="feature-box review-comments"><summary>Comments</summary><label>Apply comment to<select id="noteTarget"><option value="column">Column — all mismatching keys</option><option value="key">Key — all mismatching columns</option><option value="cell">This key and column</option></select></label><p id="noteScope" class="analysis-note-scope"></p><p class="muted">Comments appear in regenerated reports.</p><div class="preview-controls"><select id="noteStatus" aria-label="Classification"><option>Needs investigation</option><option>Expected</option><option>Resolved</option></select><textarea id="noteComment" maxlength="2000" aria-label="Analysis comment" placeholder="Describe your finding"></textarea><button id="saveNote" class="subtle">Save comment</button></div><div id="analysisNotes"></div></details>`);
function renderValueRules(){}
function settingsConfig(){return {memory_mb:Number($('memory').value),sort_workers:Number($('sortWorkers').value),read_batch_size:Number($('readBatchSize').value),compare_batch_size:Number($('compareBatchSize').value),comparison_rules:[]};}
async function loadSettings(){const result=await api('/api/settings');$('memory').value=result.memory_mb;$('sortWorkers').value=result.sort_workers;$('readBatchSize').value=result.read_batch_size;$('compareBatchSize').value=result.compare_batch_size;$('settingsStatus').textContent='Performance defaults apply to new runs. Comparison rules are configured in Keys & scope.';}
$('saveSettings').addEventListener('click',async()=>{try{await api('/api/settings',settingsConfig());$('settingsStatus').textContent='Performance settings saved. Existing runs are unchanged.';}catch(error){showError(error);}});
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

// Review overlays preserve the result underneath and never edit the completed job.
var reviewUI=false, reviewRailJob=null, rerunDraft=null, resultsTransitionRunning=false, fabIdleTimer=null, consolePaused=false, consoleFollow=true, analysisReturnTarget=null;
function paintConsole(){
  if(consolePaused)return;
  const search=$('consoleSearch')?.value.toLowerCase()||'';
  const lines=logText.split('\n'),shown=search?lines.filter(line=>line.toLowerCase().includes(search)):lines;
  $('consoleLog').textContent=shown.join('\n')||(search?'No matching log lines.':'Waiting for worker output…');
  if(consoleFollow)$('consoleLog').scrollTop=$('consoleLog').scrollHeight;
  if($('consoleStatus'))$('consoleStatus').textContent=`${shown.filter(Boolean).length} lines${search?' matching filter':''} · ${consoleFollow?'Following latest':'Scroll position held'}`;
}
function scheduleFabCollapse(){
  clearTimeout(fabIdleTimer);
  if(!$('recompareFab')||$('recompareFab').hidden||!$('recompareFab').classList.contains('fab-ready'))return;
  fabIdleTimer=setTimeout(()=>{$('recompareFab').classList.add('fab-compact');},5000);
}
function updateReviewWorkspace(name){
  if(!reviewUI)return;
  if(name==='runningPanel')reviewRailJob=null;
  if(name==='results'&&job?.state==='complete')reviewRailJob=job.id;
  const rail=reviewRailJob===job?.id&&job?.state==='complete'&&phaseViews.includes(name);
  $('mainWorkspace').classList.toggle('review-rail',rail);
  $('recompareFab').hidden=name!=='results'||job?.state!=='complete';
  $('recompareFab').classList.toggle('fab-ready',!$('recompareFab').hidden&&!resultsTransitionRunning);
  $('recompareFab').classList.remove('fab-compact');scheduleFabCollapse();
  if($('analysisDrawer').open)$('analysisDrawer').close();
}
async function transitionToResults(){
  if(resultsTransitionRunning||job?.state!=='complete')return;
  if(reducedWorkspaceMotion()||$('mainWorkspace').classList.contains('review-rail')){await goView('results');return;}
  resultsTransitionRunning=true;
  const flights=[],animations=[],buttons=['navFiles','navHeaders','navScope','navPipeline','navResults'].map($);
  const workspace=$('mainWorkspace'),sidebar=$('workspaceSidebar'),identity=job.id;
  let interrupted=false;
  const finishMotion=()=>{interrupted=true;for(const animation of animations)animation.cancel();};
  const play=(element,frames,options)=>{const animation=element.animate(frames,options);animations.push(animation);return animation.finished.catch(()=>{});};
  try{
    // Snapshot the on-screen positions before changing layout. The real controls
    // remain in the DOM; decorative copies bridge the horizontal and vertical layouts.
    for(const button of buttons){
      const rect=button.getBoundingClientRect(),flight=document.createElement('div'),number=document.createElement('span'),label=document.createElement('span');
      flight.className='step-flight';flight.setAttribute('aria-hidden','true');number.className='flight-number';number.textContent=button.firstElementChild.textContent;label.className='flight-label';label.textContent=button.getAttribute('aria-label');
      Object.assign(flight.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px',background:getComputedStyle(button).backgroundColor,borderColor:getComputedStyle(button).borderColor});
      flight.append(number,label);document.body.append(flight);flights.push({flight,number,label,rect,circleX:rect.left+(rect.width-42)/2});
    }
    document.body.classList.add('steps-in-flight');workspace.inert=true;sidebar.inert=true;
    window.addEventListener('resize',finishMotion);
    await Promise.all(flights.map(({flight,number,label,rect,circleX})=>Promise.all([
      play(flight,[{width:rect.width+'px',height:rect.height+'px',transform:'translateX(0)',borderRadius:'8px'},{width:'42px',height:'42px',transform:`translateX(${circleX-rect.left}px)`,borderRadius:'50%'}],{duration:460,easing:'cubic-bezier(.22,1,.36,1)',fill:'forwards'}),
      play(label,[{opacity:1},{opacity:0}],{duration:160,fill:'forwards'}),
      play(number,[{left:'12px'},{left:'10px'}],{duration:460,easing:'ease-out',fill:'forwards'})
    ])));
    // Once the setup labels have folded away, use the same icons as the review rail.
    flights.forEach(({number},index)=>{const icon=buttons[index].querySelector('.step-rail-icon');if(icon){number.replaceChildren(icon.cloneNode(true));number.classList.add('flight-icon');}});
    // Keep the circles in place while Results establishes the destination geometry.
    panel('results');renderSummary();renderExports();
    if(job?.id!==identity)return;
    if(!interrupted){
      await Promise.all(flights.map(({flight,rect,circleX},index)=>{
        const target=buttons[index].getBoundingClientRect();
        return play(flight,[{transform:`translate(${circleX-rect.left}px,0)`},{transform:`translate(${target.left-rect.left}px,${target.top-rect.top}px)`}],{duration:720,delay:70+index*35,easing:'cubic-bezier(.45,0,.15,1)',fill:'forwards'});
      }));
    }
  }finally{
    window.removeEventListener('resize',finishMotion);
    for(const {flight} of flights)flight.remove();for(const animation of animations)animation.cancel();
    document.body.classList.remove('steps-in-flight');workspace.inert=false;sidebar.inert=false;resultsTransitionRunning=false;
    if(currentView==='results'){$('recompareFab').classList.add('fab-ready');scheduleFabCollapse();$('resultTitle').tabIndex=-1;$('resultTitle').focus({preventScroll:true});}
  }
}
async function showAnalysisDrawer(column=''){
  if(!reviewUI||job?.state!=='complete')return;
  analysisReturnTarget=document.activeElement;
  currentView='analysisPanel';$('analysisPanel').hidden=false;
  $('analysisDrawer').showModal();
  try{await openAnalysis();if(column&&$('analysisDrawer').open){$('analysisColumn').value=column;analysisOffset=0;renderColumnChoices();await loadAnalysis();}}catch(error){$('analysisStatus').textContent=error.message;}
}
function rerunField(label,value='',type='text'){
  const wrap=document.createElement('label'),input=document.createElement('input');wrap.textContent=label;input.type=type;
  if(type==='checkbox')input.checked=!!value;else input.value=value;
  wrap.append(input);return {wrap,input};
}
function rerunColumns(label,values,multiple=true){
  const wrap=document.createElement('label'),select=document.createElement('select');wrap.textContent=label;select.multiple=multiple;select.size=multiple?6:1;
  for(const column of job.columns){const option=new Option(column,column);option.selected=values.includes(column);select.append(option);}
  wrap.append(select);return {wrap,select};
}
function showRerunTab(tab){
  for(const name of ['scope','overrides','rules']){$('rerun-'+name).hidden=name!==tab;$('rerun-tab-'+name).setAttribute('aria-selected',String(name===tab));$('rerun-tab-'+name).tabIndex=name===tab?0:-1;}
}
function renderRerunRows(kind){
  if(kind==='rules'&&document.body.classList.contains('enterprise-ui')){renderRerunRuleSummary();return;}
  const target=$('rerun-'+kind+'-list');target.replaceChildren();
  const rules=kind==='overrides'?rerunDraft.value_overrides:rerunDraft.comparison_rules;
  rules.forEach((rule,index)=>{
    const row=document.createElement('div');row.className='rerun-rule-row';
    const col=rerunColumns('Column',[rule.column],false);col.select.addEventListener('change',()=>rule.column=col.select.value);row.append(col.wrap);
    const fields=kind==='overrides'?[['File 1 value','left','text'],['File 2 value','right','text']]:[['Trim spaces','trim','checkbox'],['Ignore case','ignore_case','checkbox'],['Numeric tolerance (0 = same number)','tolerance','text'],['File 1 date format','left_date_format','text'],['File 2 date format','right_date_format','text']];
    for(const [label,key,type] of fields){const f=rerunField(label,rule[key]??'',type);f.input.addEventListener('input',()=>{if(type==='checkbox')rule[key]=f.input.checked;else if(kind==='rules'&&!f.input.value)delete rule[key];else rule[key]=f.input.value;});row.append(f.wrap);}
    const remove=document.createElement('button');remove.className='subtle';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+rule.column+' rule');remove.addEventListener('click',()=>{rules.splice(index,1);renderRerunRows(kind);});row.append(remove);target.append(row);
  });
  if(!rules.length)target.textContent='No rules added.';
}
let reviewPlanTimer,reviewPlanRequest=0,reviewPlanMode=null;
function scheduleReviewPlan(){clearTimeout(reviewPlanTimer);reviewPlanMode=null;$('startRerun').textContent='Check changes →';reviewPlanTimer=setTimeout(updateReviewPlan,250);}
async function updateReviewPlan(){
  const seq=++reviewPlanRequest;try{const decision=await api(endpoint('/review-plan'),structuredClone(rerunDraft));if(seq!==reviewPlanRequest||!$('rerunDialog').open)return;reviewPlanMode=decision.mode;$('reviewPlan').textContent=decision.reason;$('startRerun').textContent=decision.mode==='review'?'Apply rules →':'Run new comparison →';}catch(e){if(seq===reviewPlanRequest){reviewPlanMode=null;$('reviewPlan').textContent=e.message;}}
}
function openRecomparison(){
  if(job?.state!=='complete')return;
  rerunDraft=structuredClone({keys:job.keys,ignore_columns:job.ignore_columns||[],ignore_keys:job.ignore_keys||'',ignore_container_ids:job.ignore_container_ids||[],duplicate_policy:job.duplicate_policy||'first',value_overrides:job.value_overrides||[],comparison_rules:job.comparison_rules||[]});
  const scope=$('rerun-scope');scope.replaceChildren();
  const keys=rerunColumns('Key columns (selection order retained)',rerunDraft.keys),ignored=rerunColumns('Ignored columns',rerunDraft.ignore_columns);
  keys.select.addEventListener('change',()=>{const selectedValues=Array.from(keys.select.selectedOptions,o=>o.value);rerunDraft.keys=[...rerunDraft.keys.filter(k=>selectedValues.includes(k)),...selectedValues.filter(k=>!rerunDraft.keys.includes(k))];});
  ignored.select.addEventListener('change',()=>rerunDraft.ignore_columns=Array.from(ignored.select.selectedOptions,o=>o.value));scope.append(keys.wrap,ignored.wrap);
  const manual=rerunField('Ignored keys (comma separated; JSON tuples for composite keys)',rerunDraft.ignore_keys);manual.input.addEventListener('input',()=>rerunDraft.ignore_keys=manual.input.value);scope.append(manual.wrap);
  const policy=document.createElement('label'),select=document.createElement('select');policy.textContent='Duplicate keys';select.append(new Option('Keep first row','first'),new Option('Keep last row','last'));select.value=rerunDraft.duplicate_policy;select.addEventListener('change',()=>rerunDraft.duplicate_policy=select.value);policy.append(select);scope.append(policy);
  for(const container of keyContainers){const f=rerunField(container.name,rerunDraft.ignore_container_ids.includes(container.id),'checkbox');f.input.addEventListener('change',()=>{rerunDraft.ignore_container_ids=rerunDraft.ignore_container_ids.filter(id=>id!==container.id);if(f.input.checked)rerunDraft.ignore_container_ids.push(container.id);});scope.append(f.wrap);}
  renderRerunRows('overrides');renderRerunRows('rules');showRerunTab('scope');$('rerunError').textContent='';$('rerunDialog').showModal();updateReviewPlan();
}
function initReviewWorkspace(){
  if(typeof document.createElement('dialog').showModal!=='function')return;
  reviewUI=true;
  $('navAnalysis').closest('li').hidden=true;
  $('reviewOverrides').hidden=true;
  $('runningPanel').insertAdjacentHTML('afterbegin','<button id="viewResults" class="primary heartbeat-results" hidden>View results →</button>');
  document.body.insertAdjacentHTML('beforeend',`<button id="recompareFab" class="recompare-fab" hidden aria-haspopup="dialog" title="Recompare with updated settings"><span aria-hidden="true">↻</span> Recompare</button>
  <dialog id="analysisDrawer" class="analysis-drawer" aria-label="Mismatch analysis"><div class="drawer-heading"><h2>Analyze mismatches</h2><button id="closeAnalysisDrawer" class="subtle" aria-label="Close analysis">✕</button></div></dialog>
  <dialog id="rerunDialog" class="rerun-dialog" aria-labelledby="rerunTitle"><div class="drawer-heading"><div><h2 id="rerunTitle">Recompare</h2><p>Apply rules to retained evidence · original results preserved</p></div><button id="closeRerun" class="subtle" aria-label="Close recomparison">✕</button></div><div class="rerun-tabs" role="tablist" aria-label="Recomparison settings"><button id="rerun-tab-scope" role="tab" aria-controls="rerun-scope">Keys & scope</button><button id="rerun-tab-overrides" role="tab" aria-controls="rerun-overrides">Value overrides</button><button id="rerun-tab-rules" role="tab" aria-controls="rerun-rules">Comparison rules</button></div><div class="rerun-body"><section id="rerun-scope" role="tabpanel" aria-labelledby="rerun-tab-scope"></section><section id="rerun-overrides" role="tabpanel" aria-labelledby="rerun-tab-overrides" hidden><div id="rerun-overrides-list"></div><button id="addRerunOverride" class="subtle">Add override</button></section><section id="rerun-rules" role="tabpanel" aria-labelledby="rerun-tab-rules" hidden><div id="rerun-rules-list"></div><button id="addRerunRule" class="subtle">Add column rule</button></section></div><p id="rerunError" role="alert"></p><p id="reviewPlan" role="status"></p><div class="rerun-footer"><button id="startRerun" class="primary">Check changes →</button></div></dialog>`);
  $('analysisDrawer').append($('analysisPanel'));
  $('openAnalysis').hidden=true;
  $('results').querySelector('.csv-links').hidden=true;
  $('results').querySelector('.column-panel .section-title p').textContent='Select a column to analyze its mismatches.';
  $('recompareFab').innerHTML='<span aria-hidden="true">↻</span><span class="fab-caption">Recompare</span>';$('recompareFab').setAttribute('aria-label','Recompare');
  for(const event of ['pointerenter','focus'])$('recompareFab').addEventListener(event,()=>{clearTimeout(fabIdleTimer);$('recompareFab').classList.remove('fab-compact');});
  for(const event of ['pointerleave','blur'])$('recompareFab').addEventListener(event,scheduleFabCollapse);
  $('consoleLog').insertAdjacentHTML('beforebegin','<div class="console-controls"><input id="consoleSearch" type="search" placeholder="Search logs…" aria-label="Search comparison logs"><button id="consolePause" class="subtle" aria-pressed="false">Pause display</button><button id="consoleFollow" class="subtle" aria-pressed="true">Follow latest</button><button id="consoleCopy" class="subtle">Copy visible logs</button></div><p id="consoleStatus" class="muted" role="status"></p>');
  $('consoleLog').tabIndex=0;
  $('consoleSearch').addEventListener('input',()=>{const paused=consolePaused;consolePaused=false;paintConsole();consolePaused=paused;});
  $('consolePause').addEventListener('click',()=>{consolePaused=!consolePaused;$('consolePause').textContent=consolePaused?'Resume display':'Pause display';$('consolePause').setAttribute('aria-pressed',String(consolePaused));if(consolePaused)$('consoleStatus').textContent='Display paused · comparison continues in the background';else paintConsole();});
  $('consoleFollow').addEventListener('click',()=>{consoleFollow=!consoleFollow;$('consoleFollow').setAttribute('aria-pressed',String(consoleFollow));paintConsole();});
  $('consoleLog').addEventListener('scroll',()=>{if($('consoleLog').scrollHeight-$('consoleLog').clientHeight-$('consoleLog').scrollTop>20){consoleFollow=false;$('consoleFollow').setAttribute('aria-pressed','false');}});
  $('consoleCopy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('consoleLog').textContent);$('consoleStatus').textContent='Visible logs copied';}catch(error){$('consoleStatus').textContent='Copy unavailable. Select the console text to copy manually.';}});
  const reportHelp=document.createElement('details');reportHelp.className='report-help';const helpTitle=document.createElement('summary');helpTitle.textContent='Report details';reportHelp.append(helpTitle);
  const downloadPanel=$('results').querySelector('.download-panel');
  for(const p of downloadPanel.querySelectorAll('p:not([id]):not(.eyebrow)'))reportHelp.append(p);
  reportHelp.querySelector('.fine').textContent='Excel includes detailed column mismatches and configured exclusion audits. HTML contains the leadership summary and mismatch samples. Excel sheet and cell limits apply.';
  downloadPanel.append(reportHelp);
  const closeOverlay=async(dialog,drawer=false)=>{if(!dialog.open||(dialog.id==='rerunDialog'&&$('startRerun').disabled))return;if(!reducedWorkspaceMotion())await dialog.animate([{opacity:1,transform:'translate(0)'},{opacity:0,transform:drawer?'translateX(60px)':'translateY(12px)'}],{duration:180,easing:'ease-in',fill:'forwards'}).finished;dialog.close();dialog.getAnimations().forEach(a=>a.cancel());};
  $('closeAnalysisDrawer').addEventListener('click',()=>closeOverlay($('analysisDrawer'),true));
  $('analysisDrawer').addEventListener('cancel',event=>{event.preventDefault();closeOverlay($('analysisDrawer'),true);});
  $('analysisDrawer').addEventListener('close',()=>{clearTimeout(analysisTimer);analysisGeneration++;$('analysisPanel').hidden=true;if(currentView==='analysisPanel')currentView='results';if(analysisReturnTarget?.isConnected)analysisReturnTarget.focus();});
  $('closeRerun').addEventListener('click',()=>closeOverlay($('rerunDialog')));
  $('rerunDialog').addEventListener('cancel',event=>{event.preventDefault();closeOverlay($('rerunDialog'));});
  $('recompareFab').addEventListener('click',openRecomparison);
  $('viewResults').addEventListener('click',()=>transitionToResults().catch(showError));
  for(const [index,id] of ['navFiles','navHeaders','navScope','navPipeline','navResults'].entries()){
    const button=$(id),label=phaseLabels[index];button.innerHTML=`<span>${index+1}</span><span class="step-name">${label}</span>`;button.style.setProperty('--step',index);button.setAttribute('aria-label',label);button.title=label;
  }
  for(const name of ['scope','overrides','rules']){
    $('rerun-tab-'+name).addEventListener('click',()=>showRerunTab(name));
    $('rerun-tab-'+name).addEventListener('keydown',event=>{const names=['scope','overrides','rules'];if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const index=event.key==='Home'?0:event.key==='End'?2:(names.indexOf(name)+(event.key==='ArrowRight'?1:2))%3;showRerunTab(names[index]);$('rerun-tab-'+names[index]).focus();}});
  }
  for(const kind of ['overrides','rules'])$('addRerun'+(kind==='overrides'?'Override':'Rule')).addEventListener('click',()=>{const column=job.columns.find(c=>!rerunDraft.keys.includes(c)&&!rerunDraft.ignore_columns.includes(c)&&(kind==='overrides'||!rerunDraft.comparison_rules.some(r=>r.column===c)));if(!column){$('rerunError').textContent='No unused value columns available.';return;}if(kind==='overrides')rerunDraft.value_overrides.push({column,left:'',right:''});else rerunDraft.comparison_rules.push({column,trim:true});renderRerunRows(kind);});
  $('rerunDialog').addEventListener('input',scheduleReviewPlan);$('rerunDialog').addEventListener('change',scheduleReviewPlan);
  $('startRerun').addEventListener('click',async()=>{
    if($('startRerun').disabled)return;
    const config=structuredClone(rerunDraft);
    $('startRerun').disabled=true;$('closeRerun').disabled=true;$('rerunDialog').querySelector('.rerun-body').inert=true;$('startRerun').textContent='Starting…';$('rerunError').textContent='';
    try{
      const decision=await api(endpoint('/review-plan'),config);
      if(decision.mode==='full'&&reviewPlanMode!=='full'){reviewPlanMode='full';$('reviewPlan').textContent=decision.reason+' A new comparison is required.';return;}
      config.execution_mode=decision.mode;
      const copy=await api(endpoint('/review-copy'),config);
      job=copy;localStorage.setItem('keywise-job',job.id);hydratedId=null;headerDraftJob=null;renderedId=null;hydrate();
      job=await api(endpoint('/start'),config);$('rerunDialog').close();viewingHistoryJob=false;panel('runningPanel');await refresh();
    }catch(error){$('rerunError').textContent=error.message;if(job?.state==='ready'){$('rerunDialog').close();panel('keysPanel');renderKeys();renderIgnoredColumns();showError(error);}}
    finally{$('startRerun').disabled=false;$('closeRerun').disabled=false;$('rerunDialog').querySelector('.rerun-body').inert=false;$('startRerun').textContent=reviewPlanMode==='full'?'Run new comparison →':'Apply rules →';}
  });
  updateReviewWorkspace(currentView);
}
initReviewWorkspace();

// Enterprise shell: presentation and keyboard affordances share the existing controllers.
function reducedWorkspaceMotion(){return document.body.classList.contains('reduce-motion')||matchMedia('(prefers-reduced-motion: reduce)').matches;}
function animateWorkspacePane(element){
  if(reducedWorkspaceMotion())return;
  element.animate([{opacity:.5,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'cubic-bezier(.22,1,.36,1)'});
}
function updateEnterpriseShell(name){
  const heading=document.querySelector('.heading h1');
  if(!$('workspaceContext'))return;
  const titles={projectsPanel:'Projects',uploadPanel:'New comparison',headersPanel:'Column headers',keysPanel:'Keys & scope',runningPanel:'Pipeline & logs',results:'Comparison results',jsonPanel:'JSON comparison',historyPanel:'Job history',containersPanel:'Ignore key containers',profilesPanel:'Profile templates',settingsPanel:'Settings',docsPanel:'Docs & FAQ',storagePanel:'Storage & queue',overridesPanel:'Value overrides'};
  heading.textContent=titles[name]||'Comparison workspace';
  $('workspaceContext').textContent=phaseViews.includes(name)?'Compare files':titles[name]||'Workspace';
  const changed=document.body.dataset.workspaceView!==name;
  document.body.dataset.workspaceView=name;
  const target=$(name);
  if(changed&&document.activeElement?.closest('.side-link:not(#navNew),.phase-button')){heading.tabIndex=-1;heading.focus({preventScroll:true});}
  if(changed&&target&&name!=='analysisPanel'&&!resultsTransitionRunning&&!reducedWorkspaceMotion()){
    target.getAnimations().filter(a=>a.id==='workspace-enter').forEach(a=>a.cancel());
    const animation=target.animate([{opacity:.4,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,easing:'cubic-bezier(.22,1,.36,1)'});animation.id='workspace-enter';
  }
}
function applicationShortcut(event){
  if(!event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||event.repeat||event.isComposing||event.getModifierState?.('AltGraph'))return null;
  if(event.target?.closest?.('input,textarea,select,[contenteditable="true"],[role="textbox"]'))return null;
  // Option produces symbols on macOS; physical codes keep these shortcuts usable.
  const codes={KeyB:'b',KeyF:'f',KeyR:'r',Slash:'/',Digit1:'1',Digit2:'2',Digit3:'3',Digit4:'4',Digit5:'5'};
  const logical=String(event.key||'').toLowerCase();
  return ['b','f','r','/','1','2','3','4','5'].includes(logical)?logical:codes[event.code]||null;
}
function initEnterpriseShell(){
  // The controller unit harness has no layout DOM. Browser checks cover this shell.
  if(typeof document.createDocumentFragment!=='function')return;
  document.body.classList.add('enterprise-ui');
  const stageIcons={
    navFiles:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
    navHeaders:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11M15 9v11"/>',
    navScope:'<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-6 0 3-3"/>',
    navPipeline:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M6 9v9h9M9 6h9v9"/>',
    navResults:'<path d="M4 3v17h17M8 16v-4m5 4V7m5 9v-7"/>'
  };
  for(const [id,paths] of Object.entries(stageIcons)){const marker=$(id).firstElementChild,digit=marker.textContent;marker.innerHTML=`<span class="step-number-value">${digit}</span><svg class="step-rail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;marker.setAttribute('aria-hidden','true');}
  const main=$('mainWorkspace'),heading=main.querySelector('.heading');
  heading.insertAdjacentHTML('beforebegin','<div class="workspace-topbar"><div><strong>Data Compare</strong><span aria-hidden="true">/</span><span id="workspaceContext">CSV &amp; Excel</span></div><button id="keyboardHelp" class="subtle" aria-haspopup="dialog" aria-keyshortcuts="Alt+/">Keyboard shortcuts <kbd>Alt /</kbd></button></div>');
  document.querySelector('.sidebar-product').textContent='DATA COMPARE';
  // Keep the primary action in the same top-right position in every setup phase.
  const uploadActions=document.createElement('div');uploadActions.className='enterprise-setup-actions';
  $('uploadPanel').querySelector('.section-title').append(uploadActions);uploadActions.append($('navPreview'),$('upload'));
  $('keysPanel').querySelector('.scope-title-actions').append($('scopeNext'));
  $('keysPanel').querySelector('.panel-footer').hidden=true;
  const duplicateSettings=$('keysPanel').querySelector(':scope > .settings');
  $('keysPanel').querySelector('.section-title').after(duplicateSettings);
  $('uploadPanel').querySelector('.upload-actions').classList.add('enterprise-profile-status');
  // Keep global destinations separate from the five comparison stages.
  const manage=$('navHistory').parentElement;
  manage.insertBefore($('navHistory'),$('navProfiles'));
  manage.insertBefore($('navContainers'),$('navProfiles'));
  $('navStorage').hidden=true;
  const sheet=$('sheetPanel');
  const sheetHelp=document.createElement('details');sheetHelp.className='compact-help';sheetHelp.innerHTML='<summary>Excel value handling</summary>';
  const longSheetHelp=[...sheet.children].find(el=>el.tagName==='P'&&el.textContent.startsWith('Excel uses'));
  if(longSheetHelp){sheetHelp.append(longSheetHelp);sheet.insertBefore(sheetHelp,$('sheetStatus'));}

  // Settings use local tabs without duplicating inputs or saving behind the user's back.
  const settings=$('settingsPanel'),settingsControls=settings.querySelector('.settings'),guidance=settings.querySelector('.compact-help');
  settingsControls.before(Object.assign(document.createElement('div'),{id:'enterpriseSettingsTabs',className:'enterprise-tabs'}));
  const perf=document.createElement('section');perf.id='settings-performance';settingsControls.before(perf);perf.append(settingsControls,guidance);
  const rules=$('settingsRules');rules.hidden=true;
  const access=document.createElement('section');access.id='settings-accessibility';access.hidden=true;
  access.innerHTML='<div class="settings"><label>Motion<select id="motionPreference"><option value="system">Follow system preference</option><option value="reduced">Reduce motion</option></select></label><label>Keyboard shortcuts<select id="shortcutPreference"><option value="on">Enabled</option><option value="off">Disabled</option></select></label><label>Table density<select id="densityPreference"><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label></div><p class="muted">Display preferences are saved for this browser. Tab, Enter and Escape remain available.</p>';
  rules.after(access);
  const storageLink=document.createElement('button');storageLink.className='subtle';storageLink.textContent='Storage & queue';storageLink.addEventListener('click',()=>goView('storagePanel').catch(showError));
  const tabs=$('enterpriseSettingsTabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Settings categories');
  const sections=[['Performance',perf],['Accessibility',access]];
  function selectSettings(index){sections.forEach(([label,section],i)=>{section.hidden=i!==index;const button=$('settings-tab-'+i);button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1;});$('saveSettings').hidden=index===1;animateWorkspacePane(sections[index][1]);}
  sections.forEach(([label,section],i)=>{const b=document.createElement('button');b.id='settings-tab-'+i;b.textContent=label;b.setAttribute('role','tab');b.setAttribute('aria-controls',section.id);section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',b.id);b.addEventListener('click',()=>selectSettings(i));tabs.append(b);});tabs.after(storageLink);selectSettings(0);
  const applyPreferences=()=>{const motion=localStorage.getItem('compare-motion')||'system',density=localStorage.getItem('compare-density')||'comfortable';document.body.classList.toggle('reduce-motion',motion==='reduced');document.body.classList.toggle('compact-density',density==='compact');$('motionPreference').value=motion;$('densityPreference').value=density;$('shortcutPreference').value=localStorage.getItem('compare-shortcuts')||'on';};
  for(const [id,key] of [['motionPreference','compare-motion'],['densityPreference','compare-density'],['shortcutPreference','compare-shortcuts']])$(id).addEventListener('change',()=>{localStorage.setItem(key,$(id).value);applyPreferences();});applyPreferences();

  // JSON has three local views; actual parsers, rules and exports remain unchanged.
  const json=$('jsonPanel'),inputs=json.querySelector('.json-inputs'),arraySetup=json.querySelector('.json-array-setup'),advanced=json.querySelector('.json-advanced');
  const jsonTabs=document.createElement('div');jsonTabs.className='enterprise-tabs';jsonTabs.setAttribute('role','tablist');jsonTabs.setAttribute('aria-label','JSON workspace');inputs.before(jsonTabs);
  const inputPane=document.createElement('section');inputPane.id='json-input-pane';inputs.before(inputPane);inputPane.append(inputs);
  const arrayPane=document.createElement('section');arrayPane.id='json-array-pane';arraySetup.before(arrayPane);arrayPane.append(arraySetup,advanced);
  const jsonSections=[['Inputs',inputPane],['Array alignment',arrayPane],['Comparison',$('jsonResult')]];
  let jsonTabIndex=0;
  const jsonSelect=index=>{jsonTabIndex=index;jsonSections.forEach(([label,section],i)=>{if(i!==2)section.hidden=i!==index;else section.classList.toggle('enterprise-inactive',i!==index);const b=$('json-tab-'+i);b.setAttribute('aria-selected',String(i===index));b.tabIndex=i===index?0:-1;});animateWorkspacePane(jsonSections[index][1]);};
  jsonSections.forEach(([label,section],i)=>{const b=document.createElement('button');b.id='json-tab-'+i;b.textContent=label;b.setAttribute('role','tab');b.setAttribute('aria-controls',section.id);section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',b.id);b.disabled=i===2;b.addEventListener('click',()=>jsonSelect(i));jsonTabs.append(b);});jsonSelect(0);
  new MutationObserver(()=>{const available=!$('jsonResult').hidden;$('json-tab-2').disabled=!available;if(available)jsonSelect(2);else if(jsonTabIndex===2)jsonSelect(0);}).observe($('jsonResult'),{attributes:true,attributeFilter:['hidden']});
  const jsonToolbar=document.createElement('div');jsonToolbar.className='enterprise-json-toolbar';jsonTabs.before(jsonToolbar);jsonToolbar.append(jsonTabs,$('jsonCompare'));
  const resultHeading=document.createElement('div');resultHeading.className='enterprise-json-result-heading';const resultTitles=document.createElement('div');$('jsonResultTitle').before(resultHeading);resultTitles.append($('jsonResultTitle'),$('jsonCounts'),$('jsonWarnings'));resultHeading.append(resultTitles,$('jsonResult').querySelector('.preview-controls'));
  const resultHelp=document.createElement('details');resultHelp.className='compact-help';resultHelp.innerHTML='<summary>Reading this comparison</summary>';const resultNote=[...$('jsonResult').children].find(el=>el.tagName==='P'&&!el.id);if(resultNote)resultHelp.append(resultNote);$('jsonResult').append(resultHelp);
  // Long guidance stays discoverable without consuming the working area.
  for(const host of [json,$('containersPanel')]){
    const notes=[...host.children].filter(n=>n.tagName==='P'&&!n.id&&!n.classList.contains('eyebrow'));
    if(notes.length){const help=document.createElement('details');help.className='compact-help enterprise-guidance';help.innerHTML='<summary>Usage guidance</summary>';notes.forEach(n=>help.append(n));host.append(help);}
  }
  const container=$('containersPanel'),library=$('containerLibrary'),libraryTitle=library.previousElementSibling;
  const containerGrid=document.createElement('div');containerGrid.className='enterprise-container-grid';
  const editor=document.createElement('section'),saved=document.createElement('section');editor.setAttribute('aria-label','Create ignore container');saved.setAttribute('aria-label','Saved ignore containers');
  let node=container.querySelector('.settings');while(node&&node!==libraryTitle){const next=node.nextElementSibling;editor.append(node);node=next;}
  saved.append(libraryTitle,library);containerGrid.append(editor,saved);container.insertBefore(containerGrid,container.querySelector('.enterprise-guidance'));
  // Guide cards become expandable, retaining IDs and the existing search index.
  for(const article of document.querySelectorAll('.docs-card')){const h=article.querySelector('h3');if(!h)continue;const disclosure=document.createElement('details'),summary=document.createElement('summary');summary.textContent=h.textContent;disclosure.append(summary);for(const child of [...article.children]){if(child===h||child.classList.contains('eyebrow'))child.remove();else disclosure.append(child);}article.append(disclosure);}
  $('docsSearch').addEventListener('input',()=>{for(const detail of document.querySelectorAll('.docs-card>details'))detail.open=!!$('docsSearch').value;});
  // Find columns locally from the already-loaded summary, without fetching source rows.
  const search=document.createElement('input');search.id='resultColumnSearch';search.type='search';search.placeholder='Find column';search.setAttribute('aria-label','Find mismatching column');search.className='search';$('changedColumnCount').before(search);
  const filterColumns=()=>{const q=search.value.toLowerCase();for(const row of $('columnSummary').children)row.hidden=!row.querySelector('span').textContent.toLowerCase().includes(q);};search.addEventListener('input',filterColumns);new MutationObserver(filterColumns).observe($('columnSummary'),{childList:true});

  // Column review is a focused drawer, with the mismatch table taking priority.
  const analysis=$('analysisPanel'),workbench=analysis.querySelector('.analysis-workbench');
  const filterDisclosure=document.createElement('details');filterDisclosure.className='analysis-filter-disclosure';filterDisclosure.id='analysisFilterDisclosure';filterDisclosure.innerHTML='<summary>Change column or find a key</summary>';
  workbench.before(filterDisclosure);filterDisclosure.append(analysis.querySelector('.analysis-filter-grid'));
  const drawerTitle=$('analysisDrawer').querySelector('.drawer-heading h2');drawerTitle.id='analysisDrawerTitle';$('analysisDrawer').setAttribute('aria-labelledby',drawerTitle.id);
  const contextLabel=document.createElement('p');contextLabel.className='analysis-drawer-eyebrow';contextLabel.textContent='MISMATCH REVIEW';drawerTitle.before(contextLabel);
  const titleWrap=document.createElement('div');contextLabel.before(titleWrap);titleWrap.append(contextLabel,drawerTitle);
  const updateDrawerTitle=()=>{drawerTitle.textContent=$('analysisHeading').textContent||'Mismatch review';};new MutationObserver(updateDrawerTitle).observe($('analysisHeading'),{childList:true,characterData:true,subtree:true});
  const comments=analysis.querySelector('.review-comments');comments.classList.add('enterprise-comments');comments.querySelector('summary').textContent='Comments & findings';
  comments.open=true;
  const savedNotes=document.createElement('details');savedNotes.className='saved-review-notes';savedNotes.innerHTML='<summary>Saved comments</summary>';$('analysisNotes').before(savedNotes);savedNotes.append($('analysisNotes'));
  $('analysisByKey').addEventListener('click',()=>{filterDisclosure.open=true;});
  $('analysisDrawer').addEventListener('close',()=>{filterDisclosure.open=false;});
  $('analysisSearch').addEventListener('click',()=>{if($('analysisKeyFields').querySelector('input')?.value)filterDisclosure.open=false;});

  document.body.insertAdjacentHTML('beforeend','<dialog id="shortcutsDialog" class="shortcuts-dialog" aria-labelledby="shortcutsTitle"><div class="drawer-heading"><h2 id="shortcutsTitle">Keyboard shortcuts</h2><button id="closeShortcuts" class="subtle" aria-label="Close keyboard shortcuts">Close</button></div><dl><dt>Move between controls</dt><dd>Tab / Shift + Tab</dd><dt>Activate selected control</dt><dd>Enter / Space</dd><dt>Toggle sidebar</dt><dd>Alt + B</dd><dt>Comparison steps</dt><dd>Alt + 1–5</dd><dt>Search current page</dt><dd>Alt + F</dd><dt>Recompare completed job</dt><dd>Alt + R</dd><dt>Open shortcut guide</dt><dd>Alt + /</dd><dt>Move within tabs / stages</dt><dd>Arrow keys · Home / End</dd><dt>Close drawer or menu</dt><dd>Escape</dd></dl><p>Shortcuts never start, cancel, delete or export a job. Activate those actions explicitly.</p></dialog>');
  const macPlatform=/Mac|iPhone|iPad/.test(navigator.userAgentData?.platform||navigator.platform||'');
  const modifier=macPlatform?'⌥ Option':'Alt';
  $('keyboardHelp').querySelector('kbd').textContent=modifier+' /';
  for(const item of $('shortcutsDialog').querySelectorAll('dd'))item.textContent=item.textContent.replace(/Alt/g,modifier);
  const platformNote=document.createElement('p');platformNote.textContent='Windows: Alt · Mac: ⌥ Option. App shortcuts are inactive while typing. Tab, Shift + Tab, Enter, Space and Escape work on both platforms.';$('shortcutsDialog').append(platformNote);
  for(const [id,key] of [['sidebarToggle','B'],['recompareFab','R'],['navFiles','1'],['navHeaders','2'],['navScope','3'],['navPipeline','4'],['navResults','5']])$(id)?.setAttribute('aria-keyshortcuts','Alt+'+key);
  let helpReturn;
  $('keyboardHelp').addEventListener('click',()=>{helpReturn=document.activeElement;$('shortcutsDialog').showModal();});$('closeShortcuts').addEventListener('click',()=>$('shortcutsDialog').close());$('shortcutsDialog').addEventListener('close',()=>helpReturn?.focus());
  const visible=el=>el&&!el.disabled&&!el.closest('[hidden],[inert],.enterprise-inactive')&&el.getClientRects().length>0;
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||event.isComposing)return;
    const group=event.target.closest('[role="tablist"],.phase-list');
    if(group&&event.target.tagName==='BUTTON'&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)){
      const controls=[...group.querySelectorAll('button')].filter(visible),index=controls.indexOf(event.target);if(!controls.length)return;event.preventDefault();const delta=['ArrowLeft','ArrowUp'].includes(event.key)?-1:1;controls[event.key==='Home'?0:event.key==='End'?controls.length-1:(index+delta+controls.length)%controls.length].focus();return;
    }
    const key=applicationShortcut(event);
    if(!key||localStorage.getItem('compare-shortcuts')==='off'||document.querySelector('dialog[open]')||resultsTransitionRunning)return;
    let control;
    if(key==='b')control=$('sidebarToggle');
    if(key==='/')control=$('keyboardHelp');
    if(key==='r')control=$('recompareFab');
    if(/^[1-5]$/.test(key)&&phaseViews.includes(currentView))control=$(['navFiles','navHeaders','navScope','navPipeline','navResults'][Number(key)-1]);
    if(key==='f'){control=[...$(currentView).querySelectorAll('input[type="search"],input.search')].find(visible);if(!control&&currentView==='headersPanel'){$('headerSearchToggle').click();control=$('headerSearch');}if(control){event.preventDefault();control.focus();}return;}
    if(visible(control)){event.preventDefault();control.click();}
  });
  // Provide a keyboard target in each global page when navigation replaces content.
  for(const button of document.querySelectorAll('.side-link:not(#navNew),.phase-button'))button.addEventListener('click',()=>{if(button.disabled)return;const before=currentView;requestAnimationFrame(()=>{if(currentView!==before){heading.querySelector('h1').tabIndex=-1;heading.querySelector('h1').focus({preventScroll:true});}});});
  updateEnterpriseShell(currentView);
}
initEnterpriseShell();

// A rule selection expands to explicit per-column settings for auditable profiles/reports.
let ruleEditorDraft=[],ruleEditorMode='setup',ruleEditorReturn=null;
function ruleDescription(rule){return [rule.trim?'Trim whitespace':'',rule.ignore_case?'Ignore case':'',rule.tolerance!==undefined?(Number(rule.tolerance)===0?'Same numeric value':'Tolerance '+rule.tolerance):'',rule.left_date_format?'Date formats: '+rule.left_date_format+' → '+rule.right_date_format:''].filter(Boolean).join(' · ')||'Exact text';}
function ruleScope(){return ruleEditorMode==='rerun'?rerunDraft:{keys:[...selected],ignore_columns:[...ignoredColumns]};}
function eligibleRuleColumns(){const scope=ruleScope();return (job?.columns||[]).filter(c=>!scope.keys.includes(c)&&!scope.ignore_columns.includes(c));}
function mergeColumnRules(existing,columns,patch){
  const merged=existing.map(r=>({...r}));
  for(const column of columns){let rule=merged.find(r=>r.column===column);if(!rule){rule={column};merged.push(rule);}Object.assign(rule,patch);
    if(rule.left_date_format&&rule.tolerance!==undefined)throw Error('Numeric and date rules cannot be combined for '+column+'. Remove its existing rule first.');
  }return merged;
}
function renderRuleEditor(){
  const target=$('comparisonRuleList');target.replaceChildren();
  for(const rule of ruleEditorDraft){const row=document.createElement('div');row.className='comparison-rule-card';const title=document.createElement('strong'),desc=document.createElement('span'),remove=document.createElement('button');title.textContent=rule.column;desc.textContent=ruleDescription(rule);remove.textContent='Remove';remove.className='subtle';remove.setAttribute('aria-label','Remove rules for '+rule.column);remove.addEventListener('click',()=>{ruleEditorDraft=ruleEditorDraft.filter(r=>r.column!==rule.column);renderRuleEditor();});row.append(title,desc,remove);target.append(row);}
  $('ruleEditorCount').textContent=ruleEditorDraft.length+' columns configured';
  $('ruleEditorEmpty').hidden=!!ruleEditorDraft.length;
}
function renderRuleColumnChoices(){
  const q=$('ruleColumnSearch').value.toLowerCase();let visible=0;
  for(const label of $('ruleColumnChoices').children){label.hidden=!label.textContent.toLowerCase().includes(q);if(!label.hidden)visible++;}
  $('ruleSelectionCount').textContent=$('ruleColumnChoices').querySelectorAll('input:checked').length+' selected · '+visible+' shown';
}
function openRuleEditor(mode='setup'){
  ruleEditorMode=mode;ruleEditorReturn=document.activeElement;
  const allowed=eligibleRuleColumns();ruleEditorDraft=structuredClone(mode==='rerun'?rerunDraft.comparison_rules:valueRules).filter(r=>allowed.includes(r.column));
  $('ruleColumnChoices').replaceChildren();for(const column of allowed){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=column;input.addEventListener('change',renderRuleColumnChoices);label.append(input,document.createTextNode(column));$('ruleColumnChoices').append(label);}
  $('ruleTargetScope').value='selected';$('ruleColumnPicker').hidden=false;$('ruleColumnSearch').value='';$('ruleEditorError').textContent='';
  for(const id of ['ruleTrim','ruleCase','ruleNumericFormat'])$(id).checked=false;
  for(const id of ['ruleTolerance','ruleDateLeft','ruleDateRight'])$(id).value='';
  $('ruleTolerance').disabled=false;renderRuleColumnChoices();renderRuleEditor();$('comparisonRulesDrawer').showModal();
}
function renderRerunRuleSummary(){const target=$('rerun-rules-list');target.replaceChildren();const p=document.createElement('p');p.textContent=rerunDraft.comparison_rules.length+' columns configured. Add rules to all compared columns or select specific columns.';target.append(p);for(const rule of rerunDraft.comparison_rules){const row=document.createElement('div');row.className='comparison-rule-card';const title=document.createElement('strong'),desc=document.createElement('span');title.textContent=rule.column;desc.textContent=ruleDescription(rule);row.append(title,desc);target.append(row);}}
function initComparisonRulesDrawer(){
  if(typeof document.createDocumentFragment!=='function')return;
  document.body.insertAdjacentHTML('beforeend',`<dialog id="comparisonRulesDrawer" class="comparison-rules-drawer" aria-labelledby="comparisonRulesTitle"><div class="drawer-heading"><div><p class="eyebrow">COMPARISON CONFIGURATION</p><h2 id="comparisonRulesTitle">Comparison rules</h2></div><button id="closeComparisonRules" class="subtle" aria-label="Close comparison rules">✕</button></div><div class="comparison-rules-body"><p class="muted">Choose columns, combine rules, then apply. Keys and ignored columns are excluded.</p><section class="comparison-rule-builder"><label>Apply to<select id="ruleTargetScope"><option value="selected">Selected columns</option><option value="all">All compared value columns</option></select></label><div id="ruleColumnPicker"><input id="ruleColumnSearch" type="search" aria-label="Find rule columns" placeholder="Find columns"><div class="rule-selection-tools"><button id="ruleSelectVisible" class="subtle">Select shown</button><button id="ruleClearSelection" class="subtle">Clear</button><span id="ruleSelectionCount"></span></div><div id="ruleColumnChoices"></div></div><div class="rule-options"><label><input id="ruleTrim" type="checkbox">Trim whitespace</label><label><input id="ruleCase" type="checkbox">Ignore case</label><label><input id="ruleNumericFormat" type="checkbox">Ignore numeric formatting</label><label>Numeric tolerance<input id="ruleTolerance" placeholder="e.g. 0.01" inputmode="decimal"></label><label>File 1 date format<input id="ruleDateLeft" placeholder="%Y-%m-%d"></label><label>File 2 date format<input id="ruleDateRight" placeholder="%d/%m/%Y"></label></div><details class="compact-help"><summary>How rules combine</summary><p>Whitespace and case rules run before numeric or date comparison. Adding rules preserves other rules on the selected columns; a new tolerance or date format replaces that setting. Numeric and date rules cannot coexist. Remove a column’s rules to reset it to exact text. All columns means the current compared value columns; profiles save this explicit selection.</p></details><button id="addValueRule" class="subtle">Add rules to selection</button></section><div class="section-title"><h3>Configured rules</h3><span id="ruleEditorCount"></span></div><p id="ruleEditorEmpty" class="muted">No rules. Values are compared as exact text.</p><div id="comparisonRuleList"></div></div><footer class="rules-drawer-footer"><p id="ruleEditorError" role="status"></p><button id="applyComparisonRules" class="primary">Apply rules</button></footer></dialog>`);
  const trigger=document.createElement('button');trigger.id='openComparisonRules';trigger.className='subtle';trigger.textContent='Comparison rules';trigger.setAttribute('aria-haspopup','dialog');trigger.addEventListener('click',()=>openRuleEditor());$('scopeNext').before(trigger);
  const prior=$('addRerunRule'),replacement=prior.cloneNode(true);replacement.textContent='Configure comparison rules';prior.replaceWith(replacement);replacement.addEventListener('click',()=>openRuleEditor('rerun'));
  $('closeComparisonRules').addEventListener('click',()=>$('comparisonRulesDrawer').close());$('comparisonRulesDrawer').addEventListener('close',()=>ruleEditorReturn?.focus());
  $('ruleTargetScope').addEventListener('change',()=>{$('ruleColumnPicker').hidden=$('ruleTargetScope').value==='all';});$('ruleColumnSearch').addEventListener('input',renderRuleColumnChoices);
  $('ruleSelectVisible').addEventListener('click',()=>{for(const label of $('ruleColumnChoices').children)if(!label.hidden)label.querySelector('input').checked=true;renderRuleColumnChoices();});$('ruleClearSelection').addEventListener('click',()=>{for(const input of $('ruleColumnChoices').querySelectorAll('input'))input.checked=false;renderRuleColumnChoices();});
  $('ruleNumericFormat').addEventListener('change',()=>{$('ruleTolerance').disabled=$('ruleNumericFormat').checked;});
  $('addValueRule').addEventListener('click',async()=>{try{
    const columns=$('ruleTargetScope').value==='all'?eligibleRuleColumns():[...$('ruleColumnChoices').querySelectorAll('input:checked')].map(i=>i.value);if(!columns.length)throw Error('Select at least one value column.');
    const patch={};if($('ruleTrim').checked)patch.trim=true;if($('ruleCase').checked)patch.ignore_case=true;
    if($('ruleNumericFormat').checked)patch.tolerance='0';else if($('ruleTolerance').value!=='')patch.tolerance=$('ruleTolerance').value;
    if($('ruleDateLeft').value||$('ruleDateRight').value){patch.left_date_format=$('ruleDateLeft').value;patch.right_date_format=$('ruleDateRight').value;}
    if(!Object.keys(patch).length)throw Error('Choose at least one rule.');
    const proposed=mergeColumnRules(ruleEditorDraft,columns,patch),scope=ruleScope();const result=await api(endpoint('/validate-rules'),{comparison_rules:proposed,keys:scope.keys,ignore_columns:scope.ignore_columns});ruleEditorDraft=result.rules;renderRuleEditor();$('ruleEditorError').textContent='Rules added. Apply to save this configuration.';
  }catch(error){$('ruleEditorError').textContent=error.message;}});
  $('applyComparisonRules').addEventListener('click',async()=>{const button=$('applyComparisonRules');button.disabled=true;try{const scope=ruleScope(),result=await api(endpoint('/validate-rules'),{comparison_rules:ruleEditorDraft,keys:scope.keys,ignore_columns:scope.ignore_columns});if(ruleEditorMode==='rerun'){rerunDraft.comparison_rules=result.rules;renderRerunRuleSummary();}else{if(job.state!=='ready')throw Error('Use Recompare to change rules for a completed comparison.');await api(endpoint('/config'),{...configuration(),comparison_rules:result.rules});valueRules=result.rules;} $('comparisonRulesDrawer').close();}catch(error){$('ruleEditorError').textContent=error.message;}finally{button.disabled=false;}});
}
initComparisonRulesDrawer();

function detectedFileType(file){return !file?'':/\.json$/i.test(file.name)?'JSON':/\.(xlsx|xlsm)$/i.test(file.name)?'Excel':/\.parquet$/i.test(file.name)?'Parquet':'CSV';}
function updateDetectedFiles(){
  if(!$('detectedFileStatus'))return;
  const types=['left','right'].map(side=>detectedFileType(files[side]));
  for(const side of ['left','right'])if(files[side])$(`${side}Size`).textContent=detectedFileType(files[side])+' · '+bytes(files[side].size);
  const json=types.includes('JSON'),mixed=types.every(Boolean)&&json&&types[0]!==types[1];
  $('detectedFileStatus').textContent=mixed?'Choose two JSON files, or two tabular files (CSV/Excel/Parquet).':json?'JSON detected · up to 5 MiB per file. Configure array matching after loading.':types.every(Boolean)?types.join(' ↔ ')+' · column headers are read after upload.':'File format is detected automatically.';
  $('upload').disabled=mixed||!files.left||!files.right;
  $('delimiter').closest('.settings').hidden=json;
  document.querySelector('.upload-profile').hidden=json;
  if(currentView==='uploadPanel')$('comparisonFlow').hidden=json;
}
async function loadUnifiedJson(){
  clearError();$('upload').disabled=true;uploading=true;
  try{
    const texts=[];for(const side of ['left','right']){const file=files[side];if(file.size>5*1024*1024)throw Error('Each JSON input must be at most 5 MiB.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());try{JSON.parse(text);}catch{throw Error(file.name+' is not valid JSON. Check its syntax and try again.');}texts.push(text);}
    $('jsonLeft').value=texts[0];$('jsonRight').value=texts[1];jsonRules=[];renderJsonRules();invalidateJson();resetJsonDiscovery();panel('jsonPanel');$('jsonSourceFiles').textContent=files.left.name+' ↔ '+files.right.name;$('jsonStatus').textContent='Files loaded. Choose array keys or compare using the original array order.';
  }catch(error){showError(error);}finally{uploading=false;$('upload').disabled=false;syncNavigation();}
}
function initUnifiedUpload(){
  if(typeof document.createDocumentFragment!=='function')return;
  $('navJson').hidden=true;
  $('navNew').querySelector('.side-description').textContent='CSV, Excel, Parquet & JSON';
  for(const side of ['left','right']){$(`${side}File`).accept='.csv,.xlsx,.xlsm,.parquet,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';$(`${side}File`).setAttribute('aria-label','Choose '+(side==='left'?'original':'updated')+' CSV, Excel, Parquet or JSON');$(`${side}Name`).textContent='Choose '+(side==='left'?'original':'updated')+' CSV, Excel, Parquet or JSON';}
  $('uploadPanel').querySelector('.section-title').insertAdjacentHTML('afterend','<p id="detectedFileStatus" class="muted detected-file-status" role="status">File format is detected automatically.</p>');
  $('jsonPanel').querySelector('h2').insertAdjacentHTML('afterend','<div class="json-source-bar"><span id="jsonSourceFiles"></span><button id="changeJsonFiles" class="subtle">Change files</button></div>');
  $('changeJsonFiles').addEventListener('click',()=>{panel('uploadPanel');updateDetectedFiles();});
  for(const side of ['Left','Right'])$('json'+side+'File').hidden=true;
  updateDetectedFiles();
}
initUnifiedUpload();

function projectEscape(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
let projectCatalog=[],activeProject=null,projectOffset=0,projectDetail=null,projectDrawerReturn=null,projectRecordPage=0;
const auditColors={'Unreviewed':'#a8bac4','In review':'#009bbd','Approved':'#2b8b65','Changes requested':'#d7922b'};
function projectTime(value){return value?new Date(value*1000).toLocaleString(): '—';}
function projectButton(text,action,className='subtle'){const button=document.createElement('button');button.textContent=text;button.className=className;button.addEventListener('click',()=>Promise.resolve(action()).catch(error=>{$('projectDrawer').open?$('projectDrawerError').textContent=error.message:showError(error);}));return button;}
async function loadProjects(){
  const result=await api('/api/projects');projectCatalog=result.projects;renderProjectLibrary();
  if(activeProject&&projectCatalog.some(p=>p.id===activeProject))await selectProject(activeProject,projectOffset);
  else if(projectCatalog.length)await selectProject(projectCatalog[0].id);
  $('projectStatus').textContent=projectCatalog.length+' projects · Audit status is recorded manually; comparison results are calculated.';
}
function renderProjectLibrary(){const target=$('projectList');target.replaceChildren();const q=$('projectSearch').value.toLowerCase();for(const p of projectCatalog.filter(p=>p.name.toLowerCase().includes(q))){const button=projectButton('',()=>selectProject(p.id),'project-item');button.setAttribute('aria-pressed',String(p.id===activeProject));const title=document.createElement('strong'),count=document.createElement('span');title.textContent=p.name;count.textContent=p.comparisons+' comparisons · '+(p.approved||0)+' approved';button.append(title,count);target.append(button);}}
function auditDonut(counts){
  let start=0;const total=Object.values(counts).reduce((a,b)=>a+b,0);
  const segments=Object.entries(counts).map(([label,value])=>{const length=total?value/total*100:0,arc=`<circle cx="56" cy="56" r="48" pathLength="100" fill="none" stroke="${auditColors[label]}" stroke-width="14" stroke-dasharray="${length} ${100-length}" stroke-dashoffset="${-start}" transform="rotate(-90 56 56)"/>`;start+=length;return value?arc:'';});
  return `<div class="audit-chart"><div class="audit-ring" role="img" aria-label="${projectEscape(Object.entries(counts).map(([s,n])=>s+': '+n).join(', '))}"><svg viewBox="0 0 112 112" aria-hidden="true"><circle cx="56" cy="56" r="48" fill="none" stroke="#e4ebef" stroke-width="14"/>${segments.join('')}</svg><div><strong>${number(total)}</strong><span>audits</span></div></div><div class="audit-legend">${Object.entries(counts).map(([s,n])=>`<div><svg width="8" height="8" aria-hidden="true"><circle cx="4" cy="4" r="3.5" fill="${auditColors[s]}"/></svg><span>${projectEscape(s)}</span><strong>${number(n)}</strong></div>`).join('')}</div></div>`;
}
function projectTrend(rows){if(!rows.length)return '<p class="muted">Complete a comparison to see the trend.</p>';const max=Math.max(1,...rows.map(r=>r.changed_cells||0));const points=rows.map((r,i)=>`${24+i*(332/Math.max(1,rows.length-1))},${92-(r.changed_cells||0)/max*68}`);return `<svg class="project-trend" viewBox="0 0 380 118" role="img" aria-label="Differences across the last ${rows.length} completed runs: ${rows.map(r=>r.changed_cells||0).join(', ')}"><path d="M24 20V92H360" fill="none" stroke="#d9e5eb"/><polyline points="${points.join(' ')}" fill="none" stroke="#009bbd" stroke-width="2.5"/>${points.map((p,i)=>`<circle cx="${p.split(',')[0]}" cy="${p.split(',')[1]}" r="4" fill="#007c9d"><title>${projectEscape(rows[i].title)}: ${number(rows[i].changed_cells||0)} differences</title></circle>`).join('')}<text x="24" y="112">Older</text><text x="326" y="112">Latest</text><text x="3" y="15">${number(max)}</text></svg>`;}
async function selectProject(identity,offset=0){
  activeProject=identity;projectOffset=offset;renderProjectLibrary();const detail=await api('/api/projects/'+identity+'?offset='+offset);if(activeProject!==identity)return;projectDetail=detail;
  const d=$('projectDashboard'),latest=detail.latest,complete=detail.states.complete||0,approved=detail.audit_counts.Approved;
  d.innerHTML=`<div class="project-heading"><div><h3>${projectEscape(detail.project.name)}</h3><p>${projectEscape(detail.project.description||'Project audit workspace')}</p></div><div id="projectAttachAction"></div></div><div class="project-metrics"><div><span>Comparisons</span><strong>${number(detail.total)}</strong></div><div><span>Completed</span><strong>${number(complete)}</strong></div><div><span>Approved audits</span><strong>${number(approved)}<small> / ${number(detail.total)}</small></strong></div><div><span>Latest run differences</span><strong>${latest?number(latest.changed_cells||0):'—'}</strong></div></div><div class="project-charts"><section><h4>Audit status</h4>${auditDonut(detail.audit_counts)}</section><section><h4>Comparison trend <span>Last ${detail.trend.length} completed runs</span></h4>${projectTrend(detail.trend)}</section></div><div class="project-bottom"><section class="project-records"><div class="section-title"><h4>Comparisons</h4><span>${detail.total?offset+1:0}–${Math.min(offset+25,detail.total)} of ${detail.total}</span></div><div id="projectRecordList"></div><div id="projectRecordPager" class="project-pager"></div></section><section class="project-timeline"><h4>Timeline <span>Latest 100 events</span></h4><ol>${detail.events.map(e=>`<li><span class="event-dot"></span><strong>${projectEscape(e.message)}</strong><time>${projectEscape(projectTime(e.at))}</time>${e.record_id?'<small>'+projectEscape(e.record_id.slice(0,8))+'</small>':''}</li>`).join('')||'<li>No activity yet.</li>'}</ol></section></div>`;
  $('projectAttachAction').append(projectButton('Timeline',()=>{openProjectDrawer('Project timeline');const list=document.createElement('ol');list.className='project-event-drawer';for(const e of projectDetail.events){const item=document.createElement('li');item.textContent=projectTime(e.at)+' · '+e.message;list.append(item);}$('projectDrawerBody').append(list);}),projectButton('＋ Attach comparison',()=>showProjectCandidates()));
  for(const r of detail.records){const button=projectButton('',()=>openProjectAudit(r.id),'project-record');button.innerHTML=`<span><strong>${projectEscape(r.title)}</strong><small>${projectEscape(r.formats.join(' / ').toUpperCase())} · ${projectEscape(r.state)} · ${projectEscape(projectTime(r.finished||r.created))}</small></span><span class="audit-badge audit-${Object.keys(auditColors).indexOf(r.audit_status)}">${projectEscape(r.audit_status)}</span><span class="project-difference">${r.changed_cells===null||r.changed_cells===undefined?'—':number(r.changed_cells)}<small>differences</small></span>`;$('projectRecordList').append(button);}
  const prev=projectButton('← Previous',()=>selectProject(identity,Math.max(0,offset-25))),next=projectButton('Next →',()=>selectProject(identity,offset+25));prev.disabled=offset===0;next.disabled=offset+25>=detail.total;$('projectRecordPager').append(prev,next);animateWorkspacePane(d);
}
function openProjectDrawer(title){projectDrawerReturn=document.activeElement;$('projectDrawerTitle').textContent=title;$('projectDrawerError').textContent='';$('projectDrawerBody').replaceChildren();if(!$('projectDrawer').open)$('projectDrawer').showModal();}
function createProjectDrawer(onCreated){openProjectDrawer('New project');const body=$('projectDrawerBody');body.innerHTML='<label>Project name<input id="newProjectName" maxlength="100" placeholder="e.g. Monthly reconciliation"></label><label>Description<textarea id="newProjectDescription" maxlength="2000" rows="4" placeholder="Purpose and scope"></textarea></label>';body.append(projectButton('Create project',async()=>{const result=await api('/api/projects',{name:$('newProjectName').value,description:$('newProjectDescription').value});activeProject=result.id;projectOffset=0;$('projectDrawer').close();if(typeof onCreated==='function')await onCreated(result.id);else await loadProjects();},'primary'));$('newProjectName').focus();}
async function showProjectCandidates(offset=0){
  projectRecordPage=offset;openProjectDrawer('Attach comparison');const result=await api('/api/projects/records?offset='+offset);const body=$('projectDrawerBody');
  const hint=document.createElement('p');hint.className='muted';hint.textContent='Select a comparison to attach and review. JSON entries retain audit summaries, not source documents.';body.append(hint);
  for(const r of result.records){const button=projectButton('',()=>openProjectAudit(r.id,true),'project-candidate');const title=document.createElement('strong'),meta=document.createElement('span');title.textContent=r.title;meta.textContent=r.formats.join(' / ').toUpperCase()+' · '+r.state+' · '+projectTime(r.created);button.append(title,meta);body.append(button);}
  const pager=document.createElement('div');pager.className='project-pager';const prev=projectButton('← Previous',()=>showProjectCandidates(Math.max(0,offset-25))),next=projectButton('Next →',()=>showProjectCandidates(offset+25));prev.disabled=offset===0;next.disabled=offset+25>=result.total;pager.append(prev,next);body.append(pager);
}
async function openProjectAudit(identity,attach=false){
  const [recordData,catalog]=await Promise.all([api('/api/projects/records?id='+identity),api('/api/projects')]);const record=recordData.records[0];if(!record)throw Error('Comparison audit is not available.');
  openProjectDrawer('Comparison audit');const body=$('projectDrawerBody');
  body.innerHTML=`<div class="project-audit-summary"><h3>${projectEscape(record.title)}</h3><p>${projectEscape(record.formats.join(' / ').toUpperCase())} · ${projectEscape(record.state)} · ${projectEscape(projectTime(record.finished||record.created))}</p><strong>${record.changed_cells===null||record.changed_cells===undefined?'—':number(record.changed_cells)} <small>differences</small></strong></div><label>Project<select id="auditProject"><option value="">No project</option></select></label><label>Audit status<select id="auditState">${Object.keys(auditColors).map(s=>`<option>${s}</option>`).join('')}</select></label><label>Recorded by <span class="muted">Optional name; no authenticated identity</span><input id="auditReviewer" maxlength="100"></label><label>Review notes<textarea id="auditNote" maxlength="2000" rows="4"></textarea></label><div id="auditActions" class="project-actions"></div><p class="muted">${record.source_available===false?'Source files and reports were removed by cleanup; this audit summary is retained.':record.kind==='json'?'JSON audit summaries are retained here. The original JSON and full result stay in the comparison session.':'Audit approval does not alter mismatch results. Original reports and comments remain attached to the comparison.'}</p>`;
  for(const p of catalog.projects)$('auditProject').append(new Option(p.name,p.id));$('auditProject').value=attach?activeProject||'':record.project_id||'';$('auditState').value=record.audit_status;$('auditReviewer').value=record.reviewer;$('auditNote').value=record.audit_note;
  $('auditActions').append(projectButton('Save audit',async()=>{await api('/api/projects/records',{comparison_id:identity,project_id:$('auditProject').value||null,status:$('auditState').value,note:$('auditNote').value,reviewer:$('auditReviewer').value});$('projectDrawer').close();if(currentView==='projectsPanel')await loadProjects();},'primary'));
  if(record.kind==='table'&&record.source_available!==false)$('auditActions').append(projectButton('Open comparison',async()=>{$('projectDrawer').close();await openJob(identity);}));
}
async function loadUploadProjects(selectedId){
  const select=$('uploadProject'), previous=selectedId===undefined?select.value:selectedId;
  const catalog=await api('/api/projects');
  select.replaceChildren(new Option('No project',''));
  for(const p of catalog.projects)select.add(new Option(p.name,p.id));
  select.value=previous;
  if(job && selectedId===undefined){const data=await api('/api/projects/records?id='+job.id);select.value=data.records[0]?.project_id||'';}
}
async function attachUploadProject(identity){
  const data=await api('/api/projects/records?id='+identity),record=data.records[0];
  if(!record)throw Error('Comparison audit is unavailable. Retry attaching the project.');
  const chosen=$('uploadProject').value||null;
  if((record.project_id||null)===chosen)return;
  await api('/api/projects/records',{comparison_id:identity,project_id:chosen,status:record.audit_status,note:record.audit_note,reviewer:record.reviewer});
}
function initProjects(){
  if(typeof document.createDocumentFragment!=='function')return;
  $('uploadCreateProject').addEventListener('click',()=>createProjectDrawer(async id=>{await loadUploadProjects(id);if(job)await attachUploadProject(job.id);}));
  $('uploadProject').addEventListener('change',()=>{if(job)attachUploadProject(job.id).catch(showError);});
  loadUploadProjects().catch(showError);
  $('createProject').addEventListener('click',createProjectDrawer);$('refreshProjects').addEventListener('click',()=>loadProjects().catch(showError));$('projectSearch').addEventListener('input',renderProjectLibrary);
  $('closeProjectDrawer').addEventListener('click',()=>$('projectDrawer').close());$('projectDrawer').addEventListener('close',()=>{if(projectDrawerReturn?.isConnected)projectDrawerReturn.focus();});
  $('results').querySelector('.result-heading').append(projectButton('Project & audit',()=>openProjectAudit(job.id)));
  $('jsonResult').querySelector('.preview-controls').append(projectButton('Project & audit',()=>openProjectAudit(jsonResult.audit_id)));
}
initProjects();

let aiFindingsJob=null,aiFindings=[],aiSelectedColumns=new Set();
function renderAiColumnChoices(){const query=$('aiColumnSearch').value.toLowerCase(),columns=Object.keys(job.summary.changed_cells_by_column).filter(c=>c.toLowerCase().includes(query));$('aiColumnChoices').replaceChildren();for(const c of columns.slice(0,100)){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=aiSelectedColumns.has(c);input.addEventListener('change',()=>{input.checked?aiSelectedColumns.add(c):aiSelectedColumns.delete(c);renderAiColumnCount(columns.length);});label.append(input,document.createTextNode(c));$('aiColumnChoices').append(label);}renderAiColumnCount(columns.length);}
function renderAiColumnCount(total){$('aiColumnCount').textContent=aiSelectedColumns.size+' selected'+(total>100?' · Showing first 100 matches. Search to find more.':'');}
async function renderAiFindings(){
  if(!$('aiSuggestions')||!job)return;const identity=job.id;
  if(aiFindingsJob!==identity){const data=await api(endpoint('/ai-findings'));if(job.id!==identity)return;aiFindings=data.findings;aiFindingsJob=identity;}
  const column=$('analysisColumn').value,box=$('aiSuggestions');box.replaceChildren();
  const rows=aiFindings.filter(f=>!column||f.column===column);box.hidden=!rows.length;
  const title=document.createElement('summary');title.textContent=`AI suggestions · ${rows.length} · Unreviewed`;box.append(title);
  for(const f of rows.slice(0,50)){const item=document.createElement('article');item.className='ai-finding';const heading=document.createElement('strong');heading.textContent=f.column+' · '+(f.confidence||'unspecified')+' confidence';item.append(heading);for(const [label,value] of [['Reason',f.reason],['Evidence',f.evidence],['Suggested action',f.recommended_action]]){if(value){const p=document.createElement('p');p.textContent=label+': '+value;item.append(p);}}box.append(item);}
}
function initAiPackage(){
  if(typeof document.createDocumentFragment!=='function')return;
  const button=document.createElement('button');button.className='subtle';button.textContent='AI analysis package';button.id='aiPackageOpen';$('results').querySelector('.download-panel').append(button);
  document.body.insertAdjacentHTML('beforeend',`<dialog id="aiPackageDialog" class="rerun-dialog" aria-labelledby="aiPackageTitle"><div class="drawer-heading"><h2 id="aiPackageTitle">AI analysis package</h2><button id="aiPackageClose" class="subtle">Close</button></div><div class="ai-package-body"><p>Compact CSV evidence, grouped patterns and scoped reviewer comments. Download locally, then give the ZIP contents to your AI app.</p><label>Report columns<select id="aiColumnScope"><option value="all">All compared columns</option><option value="selected">Selected columns — one or more</option></select></label><div id="aiColumnPicker" hidden><input id="aiColumnSearch" type="search" placeholder="Find columns" aria-label="Find AI report columns"><div id="aiColumnChoices" class="ai-column-choices"></div><p id="aiColumnCount" role="status"></p></div><div class="ai-detail-heading"><label for="aiBudget">Report detail</label><button type="button" id="aiDetailInfo" class="subtle info-button" aria-label="Explain report detail levels" aria-expanded="false" aria-controls="aiDetailHelp">ⓘ</button></div><div id="aiDetailHelp" class="ai-detail-help" hidden><p><strong>Brief</strong> — Quick triage: up to 1 example per column and mismatch pattern, with essential context and comments.</p><p><strong>Medium</strong> — Balanced investigation: up to 3 examples per column and pattern, with more room for comments and supporting values.</p><p><strong>Detailed</strong> — Deeper investigation: up to 5 examples per column and pattern, with the most room for comments and supporting values.</p><p>All levels retain mismatching-column totals and pattern counts. Samples may be shortened or omitted to keep the package compact; coverage is documented. Detailed is still a sample, not every mismatch.</p></div><select id="aiBudget"><option value="8000">Brief — concise patterns and key examples</option><option value="32000" selected>Medium — balanced evidence and comments</option><option value="128000">Detailed — more examples and supporting context</option></select><label>Supporting columns (optional, up to 5)<select id="aiSupportingColumns" multiple size="3"></select></label><p class="muted">Selecting supporting columns reads the source files for sampled keys and may take longer on large CSVs.</p><p class="muted">Every level includes column and pattern counts. Higher detail levels allow more examples and comments. Any omitted or shortened evidence is noted in the package.</p><button id="aiPackageDownload" class="primary">Create & download package</button><p id="aiPackageStatus" role="status"></p><hr><label>Import AI findings (JSON)<input id="aiFindingsFile" type="file" accept=".json,application/json"></label><p class="muted">Import an array with column, pattern, reason, evidence, confidence and recommended_action. Imported findings remain unreviewed suggestions in column analysis. They never apply rules automatically.</p></div></dialog>`);
  const suggestions=document.createElement('details');suggestions.id='aiSuggestions';suggestions.hidden=true;$('analysisPanel').append(suggestions);
  button.addEventListener('click',()=>{aiSelectedColumns.clear();$('aiColumnScope').value='all';$('aiColumnPicker').hidden=true;$('aiColumnSearch').value='';renderAiColumnChoices();$('aiSupportingColumns').replaceChildren();for(const c of job.columns.filter(c=>!job.keys.includes(c)))$('aiSupportingColumns').add(new Option(c,c));$('aiPackageDialog').showModal();});$('aiPackageClose').addEventListener('click',()=>$('aiPackageDialog').close());
  $('aiPackageDialog').addEventListener('close',()=>button.focus());
  $('aiColumnScope').addEventListener('change',()=>{$('aiColumnPicker').hidden=$('aiColumnScope').value==='all';});$('aiColumnSearch').addEventListener('input',renderAiColumnChoices);
  let detailCloseTimer,detailPinned=false;
  const showDetail=()=>{clearTimeout(detailCloseTimer);$('aiDetailHelp').hidden=false;$('aiDetailInfo').setAttribute('aria-expanded','true');};
  const hideDetail=()=>{clearTimeout(detailCloseTimer);$('aiDetailHelp').hidden=true;$('aiDetailInfo').setAttribute('aria-expanded','false');};
  const deferHide=()=>{if(!detailPinned)detailCloseTimer=setTimeout(()=>{if(document.activeElement!==$('aiDetailInfo'))hideDetail();},160);};
  $('aiDetailInfo').addEventListener('mouseenter',showDetail);$('aiDetailInfo').addEventListener('mouseleave',deferHide);$('aiDetailHelp').addEventListener('mouseenter',showDetail);$('aiDetailHelp').addEventListener('mouseleave',deferHide);
  $('aiDetailInfo').addEventListener('focus',showDetail);$('aiDetailInfo').addEventListener('blur',deferHide);
  $('aiDetailInfo').addEventListener('click',()=>{detailPinned=!detailPinned;detailPinned?showDetail():hideDetail();});
  $('aiPackageDialog').addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('aiDetailHelp').hidden){e.preventDefault();e.stopPropagation();detailPinned=false;hideDetail();}});
  $('aiPackageDialog').addEventListener('close',()=>{detailPinned=false;hideDetail();});
  $('aiPackageDownload').addEventListener('click',async()=>{
    const identity=job.id,control=$('aiPackageDownload');control.disabled=true;control.classList.add('export-busy');control.setAttribute('aria-busy','true');
    try{if($('aiColumnScope').value==='selected'&&!aiSelectedColumns.size)throw Error('Select at least one report column.');await api(`/api/jobs/${identity}/export/ai`,{selected_columns:$('aiColumnScope').value==='all'?null:[...aiSelectedColumns],token_budget:Number($('aiBudget').value),examples:({'8000':1,'32000':3,'128000':5})[$('aiBudget').value],supporting_columns:[...$('aiSupportingColumns').selectedOptions].map(o=>o.value)});
      for(;;){const latest=await api('/api/jobs/'+identity),state=latest.exports.ai;$('aiPackageStatus').textContent=state?.message||'Preparing package…';if(state?.state==='complete'){download('ai-analysis.zip',identity);$('aiPackageStatus').textContent='Downloaded. Sampling limits and comment coverage are listed in context.txt.';break;}if(['error','cancelled','outdated'].includes(state?.state))throw Error(state.error||state.message||'Package stopped');await new Promise(r=>setTimeout(r,1500));}
    }catch(e){$('aiPackageStatus').textContent=e.message.replace(/budget/gi,'detail level').replace(/larger AI package detail level/gi,'higher report detail level');}finally{control.disabled=false;control.classList.remove('export-busy');control.setAttribute('aria-busy','false');}
  });
  $('aiFindingsFile').addEventListener('change',async()=>{const file=$('aiFindingsFile').files[0];if(!file)return;try{if(file.size>4*1024*1024)throw Error('Use a findings file smaller than 4 MiB');const findings=JSON.parse(await file.text());await api(endpoint('/ai-findings'),{findings});aiFindingsJob=null;$('aiPackageStatus').textContent='Imported as unreviewed AI suggestions. Open a column to review them.';}catch(e){$('aiPackageStatus').textContent=e.message;}finally{$('aiFindingsFile').value='';}});
}
initAiPackage();
