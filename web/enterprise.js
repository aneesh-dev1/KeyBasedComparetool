'use strict';
// Presentation extensions share app.js controllers and never start work implicitly.
let completionTimer=null,completionJob=null,completionRemaining=5,ruleDraftPrompt=null;
const appPreloaderStarted=performance.now();
const navStages=['navFiles','navScope','navPipeline','navResults'];
const stageNames=['Files & headers','Keys & scope','Pipeline & logs','Analysis'];
const filesDetails=document.createElement('details');filesDetails.id='uploadedSources';filesDetails.open=true;
const sourceSummary=document.createElement('summary');sourceSummary.id='uploadedSourcesTitle';sourceSummary.textContent='Choose your source files';filesDetails.append(sourceSummary);
const uploadedSourcesBody=document.createElement('div');uploadedSourcesBody.className='uploaded-sources-body is-visible';filesDetails.append(uploadedSourcesBody);
const fileGrid=$('uploadPanel').querySelector('.file-grid');fileGrid.before(filesDetails);uploadedSourcesBody.append(fileGrid,$('uploadPanel').querySelector('.settings'));
let uploadedSourcesCloseTimer=null;
function setUploadedSourcesOpen(open,animate=true){
 clearTimeout(uploadedSourcesCloseTimer);
 if(open){
  filesDetails.open=true;
  if(!animate)uploadedSourcesBody.classList.add('is-visible');
  else requestAnimationFrame(()=>uploadedSourcesBody.classList.add('is-visible'));
  sourceSummary.setAttribute('aria-expanded','true');
 }else{
  uploadedSourcesBody.classList.remove('is-visible');sourceSummary.setAttribute('aria-expanded','false');
  if(!animate)filesDetails.open=false;
  else uploadedSourcesCloseTimer=setTimeout(()=>{if(!uploadedSourcesBody.classList.contains('is-visible'))filesDetails.open=false;},420);
 }
}
sourceSummary.setAttribute('aria-expanded','true');
sourceSummary.addEventListener('click',event=>{event.preventDefault();setUploadedSourcesOpen(!filesDetails.open||!uploadedSourcesBody.classList.contains('is-visible'));});
$('uploadPanel').append($('headersPanel'));
$('navHeaders').closest('li').hidden=true;
$('navFiles').setAttribute('aria-label','Files & headers');$('navFiles').querySelector('.step-name').textContent='Files & headers';
for(const [i,id] of navStages.entries()){$(id).style.setProperty('--step',i);$(id).title=stageNames[i];$(id).setAttribute('aria-keyshortcuts','Alt+'+(i+1));const digit=$(id).querySelector('.step-number-value');if(digit)digit.textContent=String(i+1);}
// Preview follows applied headers and remains an optional inline view.
const preview=document.querySelector('.inline-preview');$('keysPanel').append(preview);$('keysPanel').querySelector('.section-title').append($('navPreview'));
const workspaceTransitionLoader=document.createElement('div');workspaceTransitionLoader.id='workspaceTransitionLoader';workspaceTransitionLoader.className='workspace-transition-loader';workspaceTransitionLoader.hidden=true;workspaceTransitionLoader.setAttribute('role','status');workspaceTransitionLoader.setAttribute('aria-live','polite');workspaceTransitionLoader.innerHTML='<span class="workspace-loader-card"><img src="/assets/transunion-loader.svg" alt=""><strong>Opening workspace…</strong></span>';$('mainWorkspace').append(workspaceTransitionLoader);
let workspaceTransitionStarted=0,workspaceTransitionTimer=null,workspaceTransitionPainted=false;
function startWorkspaceTransition(label='Opening workspace…'){
 clearTimeout(workspaceTransitionTimer);workspaceTransitionStarted=performance.now();workspaceTransitionPainted=false;
 workspaceTransitionLoader.querySelector('strong').textContent=label;workspaceTransitionLoader.hidden=false;
 requestAnimationFrame(()=>workspaceTransitionLoader.classList.add('active'));
}
function finishWorkspaceTransition(){
 workspaceTransitionPainted=true;if(typeof activeRequests!=='undefined'&&activeRequests>0)return;
 const remaining=Math.max(0,260-(performance.now()-workspaceTransitionStarted));clearTimeout(workspaceTransitionTimer);
 workspaceTransitionTimer=setTimeout(()=>{workspaceTransitionLoader.classList.remove('active');setTimeout(()=>{if(!workspaceTransitionLoader.classList.contains('active'))workspaceTransitionLoader.hidden=true;},180);},remaining);
}
for(const id of ['navFiles','navScope','navPipeline','navResults','navProjects','navHistory','navContainers','navProfiles','navDocs','menuContinueComparison'])$(id)?.addEventListener('click',event=>{if(!event.currentTarget.disabled)startWorkspaceTransition(`Opening ${event.currentTarget.getAttribute('aria-label')||event.currentTarget.textContent.trim()}…`);},{capture:true});
function syncFilesStage(name){
  const ready=!!job&&['ready','queued','running','complete'].includes(job.state);
  $('uploadPanel').classList.toggle('files-ready',ready);
  if(ready&&filesDetails.dataset.job!==job.id){filesDetails.dataset.job=job.id;setUploadedSourcesOpen(false,false);}
  sourceSummary.textContent=ready?`${job.files.left.name}  ↔  ${job.files.right.name}`:'Choose your source files';
  if(ready){
    for(const side of ['left','right']){$(side+'Name').textContent=job.files[side].name;$(side+'Size').textContent=bytes(job.files[side].size)+(job.source_paths?' · Server source':' · Uploaded');$(side+'File').disabled=true;}
    fileGrid.hidden=false;$('serverSourceFields').hidden=true;filesDetails.querySelector('.source-mode').hidden=true;
    $('delimiter').disabled=$('encoding').disabled=true;syncCustomDelimiter();
  }
  const fileSettings=$('uploadFormatSettings');if(fileSettings)fileSettings.hidden=ready;

  $('navPreview').hidden=!job||job.headers_reviewed===false||!['keysPanel','headersPanel'].includes(name);
  if(name==='headersPanel'){$('navFiles').setAttribute('aria-current','step');$('navFiles').classList.add('active');}
  if(!['keysPanel','headersPanel'].includes(name)&&previewExpanded)$('navPreview').click();
}
// Top-right actions are stable across both setup cards.
const scopeTitle=$('keysPanel').querySelector('.section-title'),scopeActions=document.createElement('div');scopeActions.className='stage-actions';scopeActions.append($('openComparisonRules'),$('scopeNext'));scopeTitle.append(scopeActions);
function fitKeysWorkspace(){
 const target=$('keysPanel'),grid=target.querySelector('.scope-grid');
 target.style.removeProperty('height');
 if(target.hidden||innerWidth<=1050){grid.style.removeProperty('--scope-grid-height');return;}
 const top=grid.getBoundingClientRect().top;
 grid.style.setProperty('--scope-grid-height',Math.max(320,innerHeight-top-40)+'px');
 requestAnimationFrame(syncScopeScrollbars);
}
window.addEventListener('resize',fitKeysWorkspace);
function compactScopeAction(button,label,icon){
 button.classList.add('scope-icon-action');button.setAttribute('aria-label',label);button.title=label;
 button.innerHTML=`<span class="scope-action-label">${label}</span><span class="scope-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icon}</svg></span>`;
}
compactScopeAction($('navPreview'),'Preview files','<path d="M2.1 12a10.8 10.8 0 0 1 19.8 0 10.8 10.8 0 0 1-19.8 0Z"/><circle cx="12" cy="12" r="3"/>');
compactScopeAction($('openComparisonRules'),'Comparison rules','<path d="M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3M1 14h6m2-6h6m2 8h6"/>');
for(const node of [...$('keysPanel').childNodes])if(node.nodeType===3&&node.textContent.trim()==='null')node.remove();
$('keysPanel').querySelector('.panel-footer').hidden=true;
$('headerContinue').before($('navPreview'));
// Keep preview beside the current stage title after header application.
const originalPanel=panel;panel=function(name){originalPanel(name);if(name==='keysPanel'){scopeActions.prepend($('navPreview'));$('keysPanel').append(preview);}else if(name==='headersPanel'){$('headerContinue').before($('navPreview'));$('headersPanel').append(preview);}const active=document.querySelector('#mainWorkspace>section:not([hidden]),#mainWorkspace>#results:not([hidden])');if(active&&!reducedWorkspaceMotion())active.animate([{opacity:.55,transform:'translateY(5px)'},{opacity:1,transform:'none'}],{duration:240,easing:'cubic-bezier(.22,1,.36,1)'});requestAnimationFrame(()=>{fitKeysWorkspace();finishWorkspaceTransition();});};
$('navPreview').addEventListener('click',()=>{$('keysPanel').classList.toggle('preview-expanded',previewExpanded);});

// Read server files directly; browser transfers remain resumable and independent.
uploadedSourcesBody.insertAdjacentHTML('afterbegin','<div class="source-mode" role="group" aria-label="File source"><button id="computerSource" class="subtle" aria-pressed="true">Upload from computer</button><button id="serverSource" class="subtle" aria-pressed="false">Use server paths</button></div><div id="serverSourceFields" class="server-source-workspace" hidden><div class="server-path-grid"><label class="server-path-card"><span class="file-top"><span class="label">CHAMPION DATASET</span><span class="file-symbol">A</span></span><span class="server-path-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="8" x="2" y="3" rx="2"/><rect width="20" height="8" x="2" y="13" rx="2"/><path d="M6 7h.01M6 17h.01"/></svg></span><span class="server-path-copy"><strong>Champion server path</strong><small>CSV, DAT, Excel, Parquet or JSON</small></span><input id="leftServerPath" aria-label="Champion server path" placeholder="/shared/input/champion.csv" autocomplete="off"></label><label class="server-path-card"><span class="file-top"><span class="label">CHALLENGER DATASET</span><span class="file-symbol">B</span></span><span class="server-path-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="8" x="2" y="3" rx="2"/><rect width="20" height="8" x="2" y="13" rx="2"/><path d="M6 7h.01M6 17h.01"/></svg></span><span class="server-path-copy"><strong>Challenger server path</strong><small>CSV, DAT, Excel, Parquet or JSON</small></span><input id="rightServerPath" aria-label="Challenger server path" placeholder="/shared/input/challenger.parquet" autocomplete="off"></label></div><div class="server-source-actions"><span class="server-source-note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg><span id="serverSourceHelp" role="status"></span></span><button id="useServerSources" class="primary">Load server files →</button></div></div>');
$('uploadPanel').querySelector('.enterprise-setup-actions').insertBefore($('useServerSources'),$('upload'));
$('useServerSources').hidden=true;
let sourceMode='computer';
let serverFilesEnabled=false,serverCheckTimer=null,serverCheckVersion=0;
function serverCheckMessage(message,state='idle'){$('serverSourceHelp').textContent=message;$('serverSourceFields').dataset.permissionState=state;}
async function checkServerSources(){
  const version=++serverCheckVersion,button=$('useServerSources'),left=$('leftServerPath').value.trim(),right=$('rightServerPath').value.trim();button.disabled=true;
  if(!serverFilesEnabled){serverCheckMessage('Server file access is not enabled by the administrator.','error');return;}
  if(!left||!right){serverCheckMessage('Enter both server paths to check file access.','idle');return;}
  serverCheckMessage('Checking that both files are allowed and readable…','checking');
  try{const result=await api('/api/server-sources/check',{files:{left,right}});if(version!==serverCheckVersion)return;button.disabled=!result.valid;serverCheckMessage(`Access confirmed · ${result.files.left.name} and ${result.files.right.name}`,'valid');}
  catch(error){if(version!==serverCheckVersion)return;button.disabled=true;serverCheckMessage(error.message||'File access check failed.','error');}
}
function scheduleServerCheck(delay=350){clearTimeout(serverCheckTimer);serverCheckVersion++;$('useServerSources').disabled=true;serverCheckMessage(serverFilesEnabled?'Waiting to check both server paths…':'Server file access is not enabled by the administrator.','idle');serverCheckTimer=setTimeout(checkServerSources,delay);}
function setSourceMode(mode){if(job||uploading){showError('Start a new comparison to change its file source.');return;}sourceMode=mode;fileGrid.hidden=mode==='server';$('serverSourceFields').hidden=mode!=='server';$('uploadedSources').dataset.sourceMode=mode;$('computerSource').setAttribute('aria-pressed',String(mode==='computer'));$('serverSource').setAttribute('aria-pressed',String(mode==='server'));$('upload').hidden=mode==='server';$('useServerSources').hidden=mode!=='server';if(mode==='server')scheduleServerCheck(0);else{clearTimeout(serverCheckTimer);serverCheckVersion++;}const frame=mode==='server'?$('serverSourceFields'):fileGrid;if(frame&&typeof frame.animate==='function'&&!matchMedia('(prefers-reduced-motion: reduce)').matches)frame.animate([{opacity:.25,transform:'translateY(7px) scale(.995)'},{opacity:1,transform:'none'}],{duration:280,easing:'cubic-bezier(.22,1,.36,1)'});}
$('computerSource').onclick=()=>setSourceMode('computer');$('serverSource').onclick=()=>setSourceMode('server');
for(const id of ['leftServerPath','rightServerPath'])$(id).addEventListener('input',()=>scheduleServerCheck());
$('useServerSources').disabled=true;
api('/api/source-options').then(config=>{serverFilesEnabled=config.enabled;serverCheckMessage(config.enabled?'Enter both paths to check access.':'Ask your administrator to enable server files with --source-root.');}).catch(error=>{serverFilesEnabled=false;serverCheckMessage(error.message||'Could not check server file access.','error');});
$('useServerSources').onclick=async()=>{
  const button=$('useServerSources');button.disabled=true;clearError();
  try{
    const result=await api('/api/server-sources',{files:{left:$('leftServerPath').value,right:$('rightServerPath').value},delimiter:selectedDelimiter(),encoding:$('encoding').value,header:$('headerRow').value});
    if(result.kind==='json'){
      for(const side of ['left','right'])files[side]={name:result.names[side]};
      $('jsonLeft').value=result.documents.left;$('jsonRight').value=result.documents.right;resetJsonDiscovery();invalidateJson();panel('jsonPanel');return;
    }
    job=result;localStorage.setItem('keywise-job',job.id);job=await api(endpoint('/finalize'),{});await attachUploadProject(job.id);hydratedId=null;hydrate();
    if(job.state==='selecting_sheets'){panel('uploadPanel');renderSheetSelection();}else{panel('headersPanel');renderHeaders();await loadProfiles();}
    $('computerSource').disabled=$('serverSource').disabled=true;
  }catch(error){showError(error);}finally{if(!job)scheduleServerCheck(0);}
};

// A delayed request indicator is attached inside an active modal when necessary.
const originalApi=api;let activeRequests=0,loaderTimer;
const requestLoader=document.createElement('div');requestLoader.className='tu-request-loader';requestLoader.hidden=true;requestLoader.setAttribute('role','status');requestLoader.innerHTML='<img src="/assets/transunion-loader.svg" alt=""><span>Loading…</span>';
api=async function(...args){activeRequests++;if(activeRequests===1)loaderTimer=setTimeout(()=>{const modal=[...document.querySelectorAll('dialog[open]')].at(-1);(modal||document.body).append(requestLoader);requestLoader.hidden=false;},220);try{return await originalApi(...args);}finally{if(--activeRequests===0){clearTimeout(loaderTimer);requestLoader.hidden=true;if(workspaceTransitionPainted)finishWorkspaceTransition();}}};

// A one-shot countdown follows a newly completed run, never a historical result.
// The countdown only follows a run this page watched finish, never a revisit of a completed job.
const watchedRuns=new Set();
const originalPipeline=renderPipeline;renderPipeline=async function(){await originalPipeline();
  if(['queued','running'].includes(job?.state))watchedRuns.add(job.id);
  if(job?.state==='complete'&&watchedRuns.has(job.id)&&!viewingHistoryJob&&$('tourCard')?.hidden!==false&&currentView==='runningPanel'&&completionJob!==job.id){
    clearInterval(completionTimer);completionJob=job.id;completionRemaining=5;
    $('viewResults').hidden=false;$('viewResults').textContent=`View results · ${completionRemaining}s`;
    const identity=job.id;completionTimer=setInterval(()=>{if(job?.id!==identity||currentView!=='runningPanel'){clearInterval(completionTimer);return;}completionRemaining--;if(completionRemaining<=0){clearInterval(completionTimer);transitionToResults().catch(showError);}else $('viewResults').textContent=`View results · ${completionRemaining}s`;},1000);
  }
};
$('viewResults').addEventListener('click',()=>clearInterval(completionTimer),{capture:true});

function clearRuleBuilder(){for(const id of ['ruleTrim','ruleCase','ruleNumericFormat'])$(id).checked=false;for(const id of ['ruleTolerance','ruleDateLeft','ruleDateRight'])$(id).value='';$('ruleTolerance').disabled=false;if($('ruleType')){$('ruleType').value='';$('ruleType').dispatchEvent(new Event('change'));}}
function hasRuleDraft(){return ['ruleTrim','ruleCase','ruleNumericFormat'].some(id=>$(id).checked)||['ruleTolerance','ruleDateLeft','ruleDateRight'].some(id=>$(id).value!=='')||!!$('ruleType')?.value;}
document.body.insertAdjacentHTML('beforeend','<dialog id="ruleDraftDialog" class="small-confirm" aria-labelledby="ruleDraftTitle"><h2 id="ruleDraftTitle">Rules have not been added</h2><p>Your current rule selection is still a draft.</p><div class="stage-actions"><button id="discardRuleDraft" class="subtle">Continue without these edits</button><button id="returnRuleDraft" class="primary">Return to add rules</button></div></dialog>');
function confirmRuleDraft(){if(!hasRuleDraft())return Promise.resolve(true);if(ruleDraftPrompt)return ruleDraftPrompt;ruleDraftPrompt=new Promise(resolve=>{const dialog=$('ruleDraftDialog');const finish=value=>{dialog.close();$('discardRuleDraft').onclick=$('returnRuleDraft').onclick=null;dialog.oncancel=null;ruleDraftPrompt=null;if(!value)$('addValueRule').focus();resolve(value);};$('discardRuleDraft').onclick=()=>{clearRuleBuilder();finish(true);};$('returnRuleDraft').onclick=()=>finish(false);dialog.oncancel=e=>{e.preventDefault();finish(false);};dialog.showModal();$('returnRuleDraft').focus();});return ruleDraftPrompt;}

// Overrides use the same focused editor pattern as column rules.
document.body.insertAdjacentHTML('beforeend','<dialog id="overrideEditor" class="comparison-rules-drawer" aria-labelledby="overrideEditorTitle"><div class="drawer-heading"><h2 id="overrideEditorTitle">Value overrides</h2><button id="closeOverrideEditor" class="subtle">Close</button></div><div class="comparison-rules-body"><label>Find column<input id="overrideEditorSearch" type="search" placeholder="Column name"></label><label>Column<select id="overrideEditorColumn"></select></label><div class="server-path-grid"><label>File 1 value<input id="overrideEditorLeft"></label><label>File 2 value<input id="overrideEditorRight"></label></div><p class="muted">Exact directional value pair. An empty input represents an empty value.</p><p id="overrideEditorError" role="status"></p></div><footer class="rules-drawer-footer"><button id="saveOverrideEditor" class="primary">Add override</button></footer></dialog>');
function filterOverrideEditor(){const select=$('overrideEditorColumn'),previous=select.value,q=$('overrideEditorSearch').value.toLowerCase();select.replaceChildren();for(const c of job.columns)if(!rerunDraft.keys.includes(c)&&!rerunDraft.ignore_columns.includes(c)&&c.toLowerCase().includes(q))select.add(new Option(c,c));if([...select.options].some(o=>o.value===previous))select.value=previous;}
function openOverrideEditor(){if(!rerunDraft)return;$('overrideEditorSearch').value=$('overrideEditorLeft').value=$('overrideEditorRight').value=$('overrideEditorError').textContent='';filterOverrideEditor();$('overrideEditor').showModal();$('overrideEditorSearch').focus();}
$('overrideEditorSearch').oninput=filterOverrideEditor;$('closeOverrideEditor').onclick=()=>$('overrideEditor').close();
$('saveOverrideEditor').onclick=()=>{const rule={column:$('overrideEditorColumn').value,left:$('overrideEditorLeft').value,right:$('overrideEditorRight').value};if(!rule.column){$('overrideEditorError').textContent='Select a value column.';return;}if(!rerunDraft.value_overrides.some(r=>JSON.stringify(r)===JSON.stringify(rule)))rerunDraft.value_overrides.push(rule);renderRerunRows('overrides');scheduleReviewPlan();$('overrideEditor').close();};
const originalRecomparison=openRecomparison;openRecomparison=function(){originalRecomparison();const box=$('rerun-scope');box.querySelector('select').disabled=true;const policy=[...box.querySelectorAll('label')].find(l=>l.firstChild?.textContent==='Duplicate keys');if(policy)policy.hidden=true;};
// Existing listener references the original controller; enforce locked keys on open too.
new MutationObserver(()=>{if($('rerunDialog').open){const select=$('rerun-scope').querySelector('select');if(select)select.disabled=true;for(const label of $('rerun-scope').querySelectorAll('label'))if(label.firstChild?.textContent==='Duplicate keys')label.hidden=true;}}).observe($('rerunDialog'),{attributes:true,attributeFilter:['open']});

// Project audit forms remain in Projects; the result action only attaches a project.
const originalProjectAudit=openProjectAudit;openProjectAudit=async function(identity,attach=false){
  if(currentView==='projectsPanel')return originalProjectAudit(identity,attach);
  const [recordData,catalog]=await Promise.all([api('/api/projects/records?id='+identity),api('/api/projects')]);const record=recordData.records[0];if(!record)throw Error('Comparison is not available.');
  openProjectDrawer('Attach to project');const body=$('projectDrawerBody'),label=document.createElement('label'),select=document.createElement('select');label.textContent='Project';select.append(new Option('No project',''));for(const p of catalog.projects)select.append(new Option(p.name,p.id));select.value=record.project_id||'';label.append(select);body.append(label,projectButton('Attach comparison',async()=>{await api('/api/projects/records',{comparison_id:identity,project_id:select.value||null,status:record.audit_status,note:record.audit_note,reviewer:record.reviewer});$('projectDrawer').close();},'primary'));
};
for(const button of $('results').querySelectorAll('button'))if(/Project & audit|Review and file/i.test(button.textContent))button.textContent='Attach to project';

// Rich console view reuses the exact log stream and existing pause/follow controls.
let consoleCategory='All';const categories=['All','Read','Compare','Write','Warn','Error'];
$('consoleLog').before(Object.assign(document.createElement('div'),{id:'consoleCategories',className:'console-categories'}));
for(const category of categories){const b=document.createElement('button');b.className='subtle';b.dataset.category=category;b.onclick=()=>{consoleCategory=category;const held=consolePaused;consolePaused=false;paintConsole();consolePaused=held;if(held)$('consoleStatus').textContent+=' · Display paused';};$('consoleCategories').append(b);}
const logDownload=document.createElement('button');logDownload.className='subtle';logDownload.textContent='Download log';logDownload.onclick=()=>saveJsonReport(logText,'text/plain','comparison.log');$('consoleCopy').after(logDownload);
function logCategory(line){return /error|failed/i.test(line)?'Error':/warn|duplicate/i.test(line)?'Warn':/report|writ|xlsx|html|excel/i.test(line)?'Write':/compar|review|retained|column/i.test(line)?'Compare':/sort|read|index|merge|keys/i.test(line)?'Read':'Read';}
paintConsole=function(){if(consolePaused)return;const search=$('consoleSearch').value.toLowerCase(),lines=logText.split('\n').filter(Boolean),counts=Object.fromEntries(categories.map(c=>[c,c==='All'?lines.length:lines.filter(l=>logCategory(l)===c).length]));for(const button of $('consoleCategories').children){const c=button.dataset.category;button.textContent=c+' '+counts[c];button.setAttribute('aria-pressed',String(c===consoleCategory));}const shown=lines.filter(l=>(consoleCategory==='All'||logCategory(l)===consoleCategory)&&(!search||l.toLowerCase().includes(search)));$('consoleLog').replaceChildren();const fragment=document.createDocumentFragment();for(const line of shown.slice(-1000)){const row=document.createElement('div');row.className='console-line';const tag=document.createElement('span');tag.className='console-tag '+logCategory(line).toLowerCase();tag.textContent=logCategory(line).toUpperCase();const text=document.createElement('span');text.textContent=line;row.append(tag,text);fragment.append(row);}$('consoleLog').append(fragment);if(!shown.length)$('consoleLog').textContent=search?'No matching log lines.':'Waiting for worker output…';if(consoleFollow)$('consoleLog').scrollTop=$('consoleLog').scrollHeight;$('consoleStatus').textContent=`${shown.length} lines · ${consoleFollow?'Following latest':'Scroll position held'} · ${consolePaused?'Paused':'Updating'}`;};

// Standard shortcuts: four visible stages, using physical codes on Mac Option.
document.addEventListener('keydown',event=>{if(event.defaultPrevented||document.querySelector('dialog[open]'))return;const key=applicationShortcut(event);if(key&&/^[1-4]$/.test(key)&&phaseViews.includes(currentView)){const button=$(navStages[Number(key)-1]);if(!button.disabled){event.preventDefault();event.stopImmediatePropagation();button.click();}}},{capture:true});
for(const dd of $('shortcutsDialog').querySelectorAll('dd'))dd.textContent=dd.textContent.replace('1–5','1–4');
if($('shortcutPreference')){$('shortcutPreference').value='on';$('shortcutPreference').closest('label').hidden=true;}localStorage.removeItem('compare-shortcuts');

// Full-app guide: it explains unavailable phases without creating sample jobs.
const tourSteps=[
 ['navNew','Compare files','Start a new comparison, or continue your last selection.'],
 ['uploadedSources','Choose file sources','Upload CSV, DAT, Excel, Parquet or JSON from your computer, or read files inside configured server directories.'],
 ['quickProfile','Profile templates','Save and reuse header mappings, keys, scope, overrides and value rules from Profile templates.'],
 ['uploadProject','Attach a project','Choose a shared project for this comparison, or create one here.'],
 ['navFiles','Files & headers','Files and editable headers share this stage. Expand the file summary to review sources. Only common headers proceed.'],
 ['navScope','Keys & scope','Choose keys, ignored columns, manual keys and ignore-key containers. Preview becomes available after applying headers.'],
 ['openComparisonRules','Comparison rules','Apply multiple rules to selected or all value columns. Add the configured selection before applying.'],
 ['navPipeline','Pipeline & console','Follow the active stages and searchable comparison logs. Comparison continues while you browse.'],
 ['navResults','Analysis','Review match-rate bands and mismatch counts. New completions open analysis after five seconds.'],
 ['recompareFab','Re-evaluate differences','Keys stay fixed. Adjust exclusions, value overrides or rules, then apply them to retained evidence.'],
 ['columnSummary','Column analysis','Select a mismatching column for its Mismatches and Patterns tabs. List keys filters to an exact value pair.'],
 ['noteComment','Comments','Column notes apply to the column. Key comments follow that key across mismatch columns and reports.'],
 ['exportMenu','Reports','Download one HTML report or one Excel workbook. Both include the current review and comments.'],
 ['navProjects','Projects','Shared project dashboards show recent comparisons, audit status, trends and timeline activity.'],
 ['navHistory','Job history','The team can browse saved jobs. Active uploads and job controls stay independent. Retention is configured by your administrator.'],
 ['navContainers','Ignore-key containers','Group excluded keys with a reason and include those details in the reports.'],
 ['navProfiles','Profile templates','Save reusable comparison configuration for future source files.'],
 ['navDocs','Docs & FAQ','Find instructions, replay this guide, and open the standard Windows/Mac shortcut guide.']
];
document.body.insertAdjacentHTML('beforeend','<div id="tourSpotlight" hidden></div><div id="tourCard" role="dialog" aria-labelledby="tourTitle" hidden><div class="tour-top"><span id="tourProgress"></span><button id="tourSkip" class="subtle">Skip tour</button></div><h2 id="tourTitle"></h2><p id="tourText"></p><div class="stage-actions"><button id="tourBack" class="subtle">Back</button><button id="tourNext" class="primary">Next →</button></div></div>');
let tourIndex=0,tourReturn;
function tourVisible(element){return element&&!element.closest('[hidden]')&&element.getClientRects().length&&getComputedStyle(element).visibility!=='hidden';}
function paintTour(){const [id,title,description]=tourSteps[tourIndex];let element=id==='quickProfile'?document.querySelector('.upload-profile>summary'):id==='noteComment'?$('columnSummary'):id==='exportMenu'?$('html'):$(id);if(!tourVisible(element))element=$(id==='openComparisonRules'?'navScope':id==='recompareFab'||id==='columnSummary'||id==='noteComment'||id==='exportMenu'?'navResults':'navFiles');const rect=element.getBoundingClientRect(),spot=$('tourSpotlight');Object.assign(spot.style,{left:Math.max(4,rect.left-5)+'px',top:Math.max(4,rect.top-5)+'px',width:Math.min(rect.width+10,innerWidth-8)+'px',height:Math.min(rect.height+10,innerHeight-8)+'px'});$('tourProgress').textContent=`${tourIndex+1} of ${tourSteps.length}`;$('tourTitle').textContent=title;$('tourText').textContent=description;$('tourBack').disabled=tourIndex===0;$('tourNext').textContent=tourIndex===tourSteps.length-1?'Finish tour':'Next →';const card=$('tourCard');card.style.left=Math.min(Math.max(16,rect.left),innerWidth-Math.min(380,innerWidth-32)-16)+'px';card.style.top=Math.max(16,Math.min(rect.bottom+18,innerHeight-260))+'px';}
function endTour(state){localStorage.setItem('compare-tour-v1',state);$('tourSpotlight').hidden=$('tourCard').hidden=true;tourReturn?.focus();}
function startTour(){clearInterval(completionTimer);tourReturn=document.activeElement;tourIndex=0;$('tourSpotlight').hidden=$('tourCard').hidden=false;paintTour();$('tourNext').focus();}
$('tourNext').onclick=()=>{if(tourIndex===tourSteps.length-1)endTour('complete');else{tourIndex++;paintTour();}};$('tourBack').onclick=()=>{tourIndex--;paintTour();};$('tourSkip').onclick=()=>endTour('skipped');
$('tourCard').onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();endTour('skipped');}if(event.key==='Tab'){const controls=[...$('tourCard').querySelectorAll('button:not(:disabled)')];const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}};window.addEventListener('resize',()=>{if(!$('tourCard').hidden)paintTour();});
const tourButton=document.createElement('button');tourButton.id='takeTour';tourButton.className='subtle';tourButton.textContent=localStorage.getItem('compare-tour-v1')?'Take a tour':'Take a tour · New';tourButton.onclick=startTour;const topbarActions=document.createElement('div');topbarActions.className='workspace-actions';$('keyboardHelp').before(topbarActions);topbarActions.append(tourButton,$('keyboardHelp'));
const workspaceHelp=document.createElement('details');workspaceHelp.id='workspaceHelp';workspaceHelp.className='workspace-help';workspaceHelp.innerHTML='<summary aria-label="Open help menu"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 1 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg><span>Help</span><svg class="help-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><div class="workspace-help-menu" role="group" aria-label="Help actions"></div>';
topbarActions.append(workspaceHelp);workspaceHelp.querySelector('.workspace-help-menu').append(tourButton,$('keyboardHelp'));
tourButton.addEventListener('click',()=>workspaceHelp.open=false);$('keyboardHelp').addEventListener('click',()=>workspaceHelp.open=false);
const replay=tourButton.cloneNode(true);replay.id='replayTour';replay.textContent='Replay application tour';replay.onclick=startTour;$('docsPanel').querySelector('h2').after(replay);
// Re-index amended help content using the existing search controller.
for(const details of $('docsPanel').querySelectorAll('details'))if(/AI analysis package|AI findings/.test(details.textContent))details.remove();
for(const p of $('docsPanel').querySelectorAll('p')){p.textContent=p.textContent.replace('Open Column headers','Open Files & headers').replace('Results → AI analysis package','AI package (currently deferred)');}
const help=document.createElement('article');help.className='docs-card';help.setAttribute('data-doc-entry','');help.innerHTML='<h3>Team workspace and the comparison flow</h3><p>Files &amp; headers → Keys &amp; scope → Pipeline &amp; logs → Results. Preview files after applying the headers. Use a server path only for files in administrator-configured directories; original server files are read without changing or deleting them.</p><p>Job history and Projects are shared across the team. Uploads and cancellation/deletion controls remain with the creating browser. Retention defaults to seven days of inactivity; the administrator can change it. Project access restrictions are not configured yet.</p><p>After completion, results open in five seconds, or choose View results immediately. Recompare keeps keys locked and creates a new review using retained evidence. Restoring originally unretained columns or keys needs a fresh comparison.</p><p>Click a mismatch column for Mismatches and Patterns. List keys filters to one exact pair. Column notes and key comments appear in the relevant mismatch rows and exported reports. HTML includes pattern drawers; Excel includes Comparison Summary, TOC and one detailed sheet per mismatching column.</p>';
$('docsEmpty').before(help);docsEntries.push(help);docsIndex.push(help.textContent.toLowerCase());filterDocs();syncFilesStage(currentView);
// Keep the startup canvas in place until both the minimum brand animation and
// the saved workspace restoration are complete, preventing partially-built cards.
let workspaceRestored=false,preloaderMinimumReached=false;
function revealPreparedWorkspace(){
 const loader=$('appPreloader');if(!loader||!workspaceRestored||!preloaderMinimumReached)return;
 loader.classList.add('ready');setTimeout(()=>loader.remove(),240);
}
window.addEventListener('workspace-restored',()=>{workspaceRestored=true;revealPreparedWorkspace();},{once:true});
function finishAppLoading(){
 // A soft in-app restart only waits for real restoration work; a fresh visit keeps the branded minimum.
 const minimum=document.documentElement.classList.contains('soft-reload')?300:9000,remaining=Math.max(0,minimum-(performance.now()-appPreloaderStarted));
 setTimeout(()=>{preloaderMinimumReached=true;revealPreparedWorkspace();},remaining);
}
if(document.readyState==='complete')finishAppLoading();else window.addEventListener('load',finishAppLoading,{once:true});
$('phaseLabel').textContent='Step 1 of 4 · Files & headers';
$('analysisByColumn').textContent='Mismatches';$('analysisByPattern').textContent='Patterns';
$('analysisFilterDisclosure').append($('analysisByKey'));
$('analysisReset').addEventListener('click',event=>{if(!analysisPatternFilter)return;event.preventDefault();event.stopImmediatePropagation();analysisPatternFilter=null;analysisOffset=0;loadAnalysis();},{capture:true});
$('recompareFab').addEventListener('click',()=>{for(const item of job?.ignore_key_containers||[])if(!keyContainers.some(c=>c.id===item.id))keyContainers.push(item);},{capture:true});

// Stage cards adapt to actual work, including the shorter review-only branch.
const pipelineIcons={validate:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6Z"/><path d="m8 12 3 3 5-6"/>',read:'<path d="M5 5h10M5 10h7M5 15h5M17 4v15m-4-4 4 4 4-4"/>',compare:'<path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"/>',write:'<path d="M6 3h8l4 4v14H6ZM14 3v5h4M9 12h6M9 16h6"/>',review:'<path d="M19 8a8 8 0 1 0 1 7M19 3v5h-5"/>',complete:'<path d="m5 12 5 5L20 7"/>'};
function decoratePipeline(){for(const li of $('pipelineStages').children){if(li.querySelector('.pipeline-label'))continue;const label=li.textContent.replace(/^[\d✓!]+\s*/,'');const kind=/valid/i.test(label)?'validate':/re-eval|retained|apply rules/i.test(label)?'review':/read|sort|index/i.test(label)?'read':/write|report/i.test(label)?'write':/complete/i.test(label)?'complete':'compare';li.dataset.kind=kind;li.replaceChildren();const icon=document.createElement('span');icon.className='pipeline-icon';icon.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+pipelineIcons[kind]+'</svg>';const title=document.createElement('strong');title.className='pipeline-label';title.textContent=label;const status=document.createElement('small');status.textContent=li.classList.contains('done')?'Completed':li.classList.contains('active')?'In progress':li.classList.contains('failed')?'Needs attention':'Waiting';li.append(icon,title,status);li.tabIndex=0;li.setAttribute('role','button');li.setAttribute('aria-label',label+' · '+status.textContent+' · Show stage logs');li.onclick=()=>{consoleCategory=kind==='write'?'Write':kind==='compare'||kind==='review'?'Compare':'Read';const held=consolePaused;consolePaused=false;paintConsole();consolePaused=held;if(held)$('consoleStatus').textContent+=' · Display paused';};li.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();li.click();}};}}
const pipelineWithCountdown=renderPipeline;renderPipeline=async function(){await pipelineWithCountdown();decoratePipeline();};

// Match-rate bands in Results use exact column counts before rounding.
renderResultChart=function(summary){const counts=Object.values(summary.changed_cells_by_column),matched=summary.matched_keys||0;const bands=[['100% match',0,'#007b99'],['99% to <100%',0,'#69cbd8'],['95% to <99%',0,'#e9ba37'],['Below 95%',0,'#ce6256']];if(matched)for(const count of counts){bands[count===0?0:count*100<=matched?1:count*20<=matched?2:3][1]++;}let offset=0;const total=matched?counts.length:0;const slices=bands.filter(b=>b[1]).map(([name,n,color])=>{const size=100*n/total;const path=`<circle cx="80" cy="80" r="62" pathLength="100" fill="none" stroke="${color}" stroke-width="20" stroke-dasharray="${size} ${100-size}" stroke-dashoffset="${-offset}" transform="rotate(-90 80 80)"><title>${name}: ${n}</title></circle>`;offset+=size;return path;});$('resultChartTitle').textContent='Column match distribution';$('resultKeyTotal').textContent=number(counts.length)+' compared value columns';$('resultDonut').innerHTML='<svg viewBox="0 0 160 160" role="img" aria-label="Column match distribution"><circle cx="80" cy="80" r="62" fill="none" stroke="#e5edf0" stroke-width="20"/>'+slices.join('')+`<text x="80" y="79" text-anchor="middle" class="result-chart-number">${matched?number(counts.length):'N/A'}</text><text x="80" y="98" text-anchor="middle" class="result-chart-caption">columns compared</text></svg>`;$('resultChartLegend').replaceChildren();for(const [label,n,color] of bands){const row=document.createElement('div'),swatch=document.createElement('span'),text=document.createElement('span'),value=document.createElement('strong');swatch.className='band-dot';swatch.style.background=color;text.textContent=label;value.textContent=matched?number(n):'—';row.append(swatch,text,value);$('resultChartLegend').append(row);}const note=document.createElement('p');note.textContent=matched?`${number(summary.changed_cells)} mismatched cells · ${number(summary.changed_rows)} keys with differences`:'No matched keys; column match rates are unavailable.';$('resultChartLegend').append(note);if(!reducedWorkspaceMotion())$('resultDonut').animate([{opacity:0,transform:'scale(.9)'},{opacity:1,transform:'scale(1)'}],{duration:550,easing:'cubic-bezier(.22,1,.36,1)'});};
$('historyPanel').querySelector('.section-title p:last-child').textContent='Completed comparisons shared with your team, plus your active jobs.';
// A guide can visit shared libraries without changing the current job's data.
const positionTour=paintTour;paintTour=async function(){
 const index=tourIndex,id=tourSteps[index][0];let destination={navProjects:'projectsPanel',navHistory:'historyPanel',navContainers:'containersPanel',navProfiles:'profilesPanel',navDocs:'docsPanel'}[id];
 if(['uploadedSources','quickProfile','uploadProject','navFiles'].includes(id))destination='uploadPanel';
 if(['navScope','openComparisonRules'].includes(id)&&!$('navScope').disabled)destination='keysPanel';
 if(id==='navPipeline'&&!$('navPipeline').disabled)destination='runningPanel';
 if(['navResults','recompareFab','columnSummary','noteComment','exportMenu'].includes(id)&&job?.state==='complete')destination='results';
 $('tourNext').disabled=$('tourBack').disabled=true;
 try{if(destination&&currentView!==destination)await goView(destination);if(index!==tourIndex)return;positionTour();}catch(error){showError(error);}finally{$('tourNext').disabled=false;$('tourBack').disabled=tourIndex===0;}
};
$('comparisonRuleList').before(Object.assign(document.createElement('input'),{id:'configuredRuleSearch',type:'search',placeholder:'Find configured column rules'}));$('configuredRuleSearch').setAttribute('aria-label','Find configured column rules');
function filterConfiguredRules(){const q=$('configuredRuleSearch').value.toLowerCase();for(const row of $('comparisonRuleList').children)row.hidden=!row.textContent.toLowerCase().includes(q);}
$('configuredRuleSearch').oninput=filterConfiguredRules;new MutationObserver(filterConfiguredRules).observe($('comparisonRuleList'),{childList:true});

// Keep help search synchronized with the visible documentation after revisions.
docsEntries.splice(0,docsEntries.length,...$('docsPanel').querySelectorAll('[data-doc-entry]'));docsIndex.splice(0,docsIndex.length,...docsEntries.map(entry=>entry.textContent.toLowerCase()));filterDocs();

window.addEventListener("DOMContentLoaded",()=>{
// Video-aligned pipeline workspace: one canvas, with a terminal beside its stage rail.
const pipelinePanel=$('runningPanel'),pipelineHeading=pipelinePanel.querySelector('.section-title');
const pipelineActions=document.createElement('div');pipelineActions.className='pipeline-actions';
pipelineActions.append($('resumeComparison'),$('cancelComparison'));
if($('viewResults'))pipelineActions.append($('viewResults'));
for(const hint of pipelinePanel.querySelectorAll(':scope > p.muted:not([id])'))hint.remove();
pipelineHeading.append(pipelineActions);pipelinePanel.dataset.liveTab='console';
const terminal=document.createElement('section');terminal.id='pipelineTerminal';terminal.setAttribute('aria-label','Comparison console');
const terminalHeading=pipelinePanel.querySelector('.console-title');terminalHeading.querySelector('h3').textContent='Console';
const terminalLights=document.createElement('span');terminalLights.className='terminal-lights';terminalLights.setAttribute('aria-hidden','true');terminalLights.innerHTML='<i></i><i></i><i></i>';terminalHeading.prepend(terminalLights);
const terminalNow=document.createElement('span');terminalNow.id='terminalNow';terminalHeading.querySelector('h3').after(terminalNow);
const terminalTools=pipelinePanel.querySelector('.console-controls');
terminalHeading.append($('consoleFollow'),$('consolePause'),$('logDownload'));
logDownload.remove();$('logDownload').textContent='↓ Log';$('logDownload').setAttribute('aria-label','Download complete comparison log');
terminalTools.append($('consoleCategories'));$('consoleCopy').textContent='Copy';
const terminalProgress=document.createElement('div');terminalProgress.className='terminal-progress';terminalProgress.innerHTML='<span id="terminalPhase"></span><span id="terminalStageCount"></span><div class="terminal-progress-track"><span id="terminalProgressFill"></span></div>';
const terminalColumns=document.createElement('div');terminalColumns.className='terminal-columns';terminalColumns.innerHTML='<span>Category</span><span>Time · Message</span>';
terminal.append(terminalHeading,terminalTools,terminalProgress,terminalColumns,$('consoleLog'),$('consoleStatus'));pipelinePanel.append(terminal);
const pipelineView=renderPipeline;renderPipeline=async function(){await pipelineView();const total=$('pipelineStages').children.length,done=$('pipelineStages').querySelectorAll('.done').length;terminalNow.textContent=$('phase').textContent;$('terminalPhase').textContent=job.state==='complete'?'Results and reports are ready':$('processed').textContent;$('terminalStageCount').textContent=`${done} of ${total} stages complete`;$('terminalProgressFill').style.width=(total?done/total*100:0)+'%';terminalProgress.setAttribute('aria-label',`${done} of ${total} stages complete`);};
$('workspaceSidebar').querySelector('.brand').setAttribute('aria-label','TransUnion Data Compare home');
$('workspaceSidebar').querySelector('.sidebar-product').textContent='DATA COMPARE';

},{once:true});

// The brand is the sidebar toggle; keep the existing accessible keyboard control.
const sidebarBrand=$('workspaceSidebar').querySelector('.sidebar-brand');
sidebarBrand.append($('sidebarToggle'));
sidebarBrand.querySelector('.brand').removeAttribute('href');
sidebarBrand.querySelector('.brand').setAttribute('aria-hidden','true');
$('sidebarToggle').title='Toggle sidebar · Alt / Option + B';
$('workspaceSidebar').querySelector('.sidebar-footer').hidden=true;
// Expanded navigation uses an inline dropdown; the compact rail uses a flyout.
$('navNew').after($('comparisonMenu'));
$('comparisonMenu').setAttribute('aria-label','Comparison actions');
$('menuNewComparison').textContent='New comparison';
$('menuContinueComparison').textContent='Continue last comparison';
const comparisonChevron=document.createElement('span');comparisonChevron.className='comparison-chevron';comparisonChevron.setAttribute('aria-hidden','true');comparisonChevron.textContent='⌄';$('navNew').append(comparisonChevron);
$('navNew').querySelector('.comparison-chevron').innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
$('sidebarToggle').addEventListener('click',closeComparisonMenu);

// Lucide icons come from a local sprite so the on-prem application stays offline.
// Older call sites use short names; map them onto Lucide's own icon ids.
const lucideNames={compare:'file-diff',projects:'folder-kanban',history:'history',package:'package-x',profiles:'layout-template',docs:'book-open',files:'files',scope:'key-round',pipeline:'workflow',results:'chart-column',upload:'file-up',shield:'shield-check',sliders:'sliders-horizontal',chevron:'chevron-right',check:'check',option:'circle'};
function lucide(name,className=''){return `<svg class="lucide ${className}" aria-hidden="true" focusable="false"><use href="/assets/lucide-sprite.svg#${lucideNames[name]||name}"/></svg>`;}
// The sidebar keeps its original icon artwork.
const sidebarIconPaths={
 compare:'<rect width="8" height="14" x="3" y="5" rx="2"/><path d="M7 8v2M7 14v2M14 8h7M14 12h7M14 16h7"/>',
 projects:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M8 17v-3m4 3v-6m4 6V9"/>',
 history:'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
 package:'<path d="m7.5 4.27 9 5.15M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
 profiles:'<path d="M9 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3"/><rect width="6" height="4" x="9" y="3" rx="1"/><path d="M9 12h6M9 16h6"/>',
 docs:'<path d="M12 7v14M3 5h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v14h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z"/>'
};
for(const [id,name] of Object.entries({navNew:'compare',navProjects:'projects',navHistory:'history',navContainers:'package',navProfiles:'profiles',navDocs:'docs'})){
 const icon=$(id)?.querySelector('.side-icon');if(icon)icon.innerHTML=`<svg class="" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${sidebarIconPaths[name]}</svg>`;
}
for(const [id,name] of Object.entries({navFiles:'files',navScope:'scope',navPipeline:'pipeline',navResults:'results'})){
 const button=$(id);if(!button)continue;const marker=button.querySelector('.step-number-value')||button.querySelector('span');if(marker){marker.innerHTML=lucide(name,'workflow-lucide');marker.classList.add('workflow-icon');}
}

// Give every single-choice form select the same accessible TransUnion menu.
// The original select remains the source of truth so existing comparison code is unchanged.
const enhancedSelects=new Set();
let openSelectWidget=null;
function closeSelectWidget(widget,returnFocus=false){
 if(!widget)return;
 widget.classList.remove('is-open');
 widget.querySelector('.tu-select-menu').hidden=true;
 widget.querySelector('.tu-select-trigger').setAttribute('aria-expanded','false');
 if(openSelectWidget===widget)openSelectWidget=null;
 if(returnFocus)widget.querySelector('.tu-select-trigger').focus();
}
function enhanceSelect(select){
 if(!select||enhancedSelects.has(select)||select.multiple||Number(select.size)>1||select.hidden||select.id==='analysisMode')return;
 enhancedSelects.add(select);
 const widget=document.createElement('span');widget.className='tu-select';
 const trigger=document.createElement('button');trigger.type='button';trigger.className='tu-select-trigger';trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
 const value=document.createElement('span');value.className='tu-select-value';
 const arrow=document.createElement('span');arrow.className='tu-select-chevron';arrow.innerHTML=lucide('chevron');
 const menu=document.createElement('span');menu.className='tu-select-menu';menu.setAttribute('role','listbox');menu.hidden=true;
 select.parentNode.insertBefore(widget,select);widget.append(trigger,menu,select);select.classList.add('tu-native-select');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
 trigger.append(value,arrow);
 const ownerLabel=select.closest('label');
 const directLabel=ownerLabel?[...ownerLabel.childNodes].filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent.trim()).filter(Boolean).join(' '):'';
 const label=select.getAttribute('aria-label')||directLabel||select.id||'Choose option';trigger.setAttribute('aria-label',label);
 function sync(){
  const chosen=select.options[select.selectedIndex]||select.options[0];
  value.textContent=chosen?.textContent||'Choose option';
  trigger.disabled=select.disabled;
  trigger.setAttribute('aria-disabled',String(select.disabled));
 }
 function build(){
  menu.replaceChildren();
  [...select.options].forEach((option,index)=>{
   const item=document.createElement('button');item.type='button';item.className='tu-select-option';item.setAttribute('role','option');item.setAttribute('aria-selected',String(option.selected));item.disabled=option.disabled;
   item.innerHTML=`<span class="tu-option-icon">${lucide(option.selected?'check':'option')}</span><span class="tu-option-label"></span>`;
   item.querySelector('.tu-option-label').textContent=option.textContent;
   item.addEventListener('click',event=>{event.preventDefault();select.selectedIndex=index;select.dispatchEvent(new Event('change',{bubbles:true}));sync();closeSelectWidget(widget,true);});
   menu.append(item);
  });
 }
 function open(){
  if(trigger.disabled)return;
  if(openSelectWidget&&openSelectWidget!==widget)closeSelectWidget(openSelectWidget);
  build();
  const rect=trigger.getBoundingClientRect(),estimatedHeight=Math.min(226,menu.childElementCount*42+10);
  widget.classList.toggle('opens-up',window.innerHeight-rect.bottom<estimatedHeight+12&&rect.top>estimatedHeight+12);
  menu.hidden=false;widget.classList.add('is-open');trigger.setAttribute('aria-expanded','true');openSelectWidget=widget;
  const selected=menu.querySelector('[aria-selected=true]')||menu.querySelector('button:not(:disabled)');selected?.focus();
 }
 trigger.addEventListener('click',event=>{event.preventDefault();widget.classList.contains('is-open')?closeSelectWidget(widget,true):open();});
 trigger.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Enter',' '].includes(event.key)){event.preventDefault();open();}});
 menu.addEventListener('keydown',event=>{
  const items=[...menu.querySelectorAll('button:not(:disabled)')];const current=items.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();closeSelectWidget(widget,true);}
  else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();items[(current+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}
  else if(event.key==='Home'){event.preventDefault();items[0]?.focus();}
  else if(event.key==='End'){event.preventDefault();items.at(-1)?.focus();}
 });
 select.addEventListener('change',sync);
 new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','label']});
 sync();
}
function enhanceAllSelects(root=document){root.querySelectorAll?.('select').forEach(enhanceSelect);}
enhanceAllSelects();
new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1){if(node.matches?.('select'))enhanceSelect(node);enhanceAllSelects(node);}}))).observe(document.body,{childList:true,subtree:true});
document.addEventListener('pointerdown',event=>{if(openSelectWidget&&!openSelectWidget.contains(event.target))closeSelectWidget(openSelectWidget);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&openSelectWidget)closeSelectWidget(openSelectWidget,true);});

// Ignore-key containers use the same compact menu language as file settings,
// while preserving checkbox multi-selection and the existing draft state.
const containerChoices=$('containerChoices');
const containerPicker=document.createElement('span');containerPicker.className='tu-select container-multiselect';
const containerTrigger=document.createElement('button');containerTrigger.type='button';containerTrigger.className='tu-select-trigger';containerTrigger.setAttribute('aria-haspopup','listbox');containerTrigger.setAttribute('aria-expanded','false');containerTrigger.setAttribute('aria-controls','containerChoices');containerTrigger.setAttribute('aria-label','Choose ignore key containers');
const containerValue=document.createElement('span');containerValue.className='tu-select-value';
const containerChevron=document.createElement('span');containerChevron.className='tu-select-chevron';containerChevron.innerHTML=lucide('chevron');
containerChoices.parentNode.insertBefore(containerPicker,containerChoices);containerPicker.append(containerTrigger,containerChoices);containerTrigger.append(containerValue,containerChevron);
containerChoices.classList.add('tu-select-menu','container-multiselect-menu');containerChoices.setAttribute('role','listbox');containerChoices.setAttribute('aria-multiselectable','true');containerChoices.hidden=true;
function syncContainerPicker(){
 const checks=[...containerChoices.querySelectorAll('input[type=checkbox]')],chosen=checks.filter(input=>input.checked).length;
 containerValue.textContent=!checks.length?'No saved containers':chosen?`${chosen} container${chosen===1?'':'s'} selected`:'Select containers';
 containerTrigger.classList.toggle('has-selection',chosen>0);
 for(const label of containerChoices.querySelectorAll('.container-option'))label.setAttribute('role','option');
 checks.forEach(input=>input.closest('.container-option')?.setAttribute('aria-selected',String(input.checked)));
}
function toggleContainerPicker(){
 if(containerPicker.classList.contains('is-open')){closeSelectWidget(containerPicker,true);return;}
 if(openSelectWidget&&openSelectWidget!==containerPicker)closeSelectWidget(openSelectWidget);
 containerPicker.classList.remove('opens-up');
 containerChoices.hidden=false;containerPicker.classList.add('is-open');containerTrigger.setAttribute('aria-expanded','true');openSelectWidget=containerPicker;
}
containerTrigger.addEventListener('click',event=>{event.preventDefault();toggleContainerPicker();});
containerTrigger.addEventListener('keydown',event=>{if(['ArrowDown','Enter',' '].includes(event.key)){event.preventDefault();toggleContainerPicker();}});
containerChoices.addEventListener('change',syncContainerPicker);
new MutationObserver(syncContainerPicker).observe(containerChoices,{childList:true,subtree:true,attributes:true,attributeFilter:['checked','disabled']});
syncContainerPicker();

const scopeScrollbarSync=[];
function syncScopeScrollbars(){for(const sync of scopeScrollbarSync)sync();}
function installScopeScrollbar(scroller){
 const card=scroller.closest('.scope-card'),rail=document.createElement('span'),thumb=document.createElement('span');
 rail.className='tu-scroll-rail';rail.setAttribute('aria-hidden','true');thumb.className='tu-scroll-thumb';rail.append(thumb);card.append(rail);
 function sync(){
  if(scroller.offsetParent===null)return;
  const overflow=scroller.scrollHeight-scroller.clientHeight>2;rail.hidden=false;rail.classList.toggle('is-idle',!overflow);thumb.classList.toggle('is-idle',!overflow);
  const cardBox=card.getBoundingClientRect(),listBox=scroller.getBoundingClientRect(),height=Math.max(0,listBox.height);
  rail.style.top=(listBox.top-cardBox.top)+'px';rail.style.right='3px';rail.style.height=height+'px';
  if(!overflow){thumb.style.height=Math.max(34,height-2)+'px';thumb.style.transform='translateY(1px)';return;}
  const thumbHeight=Math.max(34,height*(scroller.clientHeight/scroller.scrollHeight)),travel=Math.max(0,height-thumbHeight),range=Math.max(1,scroller.scrollHeight-scroller.clientHeight);
  thumb.style.height=thumbHeight+'px';thumb.style.transform=`translateY(${travel*(scroller.scrollTop/range)}px)`;
 }
 scroller.addEventListener('scroll',sync,{passive:true});
 rail.addEventListener('pointerdown',event=>{if(event.target===thumb)return;const box=rail.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientY-box.top)/box.height));scroller.scrollTop=ratio*(scroller.scrollHeight-scroller.clientHeight);});
 thumb.addEventListener('pointerdown',event=>{event.preventDefault();thumb.classList.add('is-dragging');thumb.setPointerCapture(event.pointerId);const startY=event.clientY,startScroll=scroller.scrollTop,travel=Math.max(1,rail.clientHeight-thumb.clientHeight),range=Math.max(1,scroller.scrollHeight-scroller.clientHeight);const move=e=>{scroller.scrollTop=startScroll+(e.clientY-startY)*range/travel;};const stop=e=>{thumb.classList.remove('is-dragging');thumb.releasePointerCapture?.(e.pointerId);thumb.removeEventListener('pointermove',move);thumb.removeEventListener('pointerup',stop);thumb.removeEventListener('pointercancel',stop);};thumb.addEventListener('pointermove',move);thumb.addEventListener('pointerup',stop);thumb.addEventListener('pointercancel',stop);});
 new MutationObserver(()=>requestAnimationFrame(sync)).observe(scroller,{childList:true,subtree:true});
 new ResizeObserver(sync).observe(scroller);scopeScrollbarSync.push(sync);requestAnimationFrame(sync);
}
installScopeScrollbar($('keyList'));installScopeScrollbar($('ignoreColumnList'));

// A compact, format-neutral upload experience keeps both sources visible in one view.
const uploadTitle=$('uploadPanel').querySelector('.section-title>div');
uploadTitle.querySelector('.upload-intro')?.remove();
for(const [side,label] of [['left','Champion'],['right','Challenger']]){
 const card=$(side+'Card');
 if(!card.querySelector('.file-drop-icon')){
  const icon=document.createElement('span');icon.className='file-drop-icon';icon.innerHTML=lucide('upload');
  const types=document.createElement('span');types.className='file-type-line';types.textContent='CSV · DAT · Excel · Parquet · JSON';
  card.querySelector('.file-top').after(icon);card.querySelector('.muted').after(types);
  card.querySelector('.label').textContent=label.toUpperCase()+' DATASET';
  const picker=card.querySelector('input[type=file]');
  const name=card.querySelector('strong');
  if(picker)picker.setAttribute('aria-label',`Choose ${label.toLowerCase()} CSV, DAT, Excel, Parquet or JSON`);
  if(name&&!files[side])name.textContent=`Choose ${label.toLowerCase()} file`;
  const arrow=card.querySelector('.browse>span');if(arrow)arrow.innerHTML=lucide('chevron');
 }
}
const formatNote=$('uploadPanel').querySelector('.settings p');
formatNote?.remove();
const exactPill=$('uploadPanel').querySelector('.section-title>.pill');
exactPill?.remove();
const uploadSettings=$('uploadPanel').querySelector('.settings');
const uploadFormatSettings=document.createElement('details');uploadFormatSettings.id='uploadFormatSettings';uploadFormatSettings.className='upload-format-settings';
uploadFormatSettings.innerHTML=`<summary aria-label="Text file settings" title="Delimiter, encoding and header row">${lucide('sliders')}</summary><div class="file-settings-menu"><div class="file-settings-heading"><span>${lucide('sliders')}</span><div><strong>Text file settings</strong><small>CSV, DAT, TXT and TSV files</small></div></div><p>Auto-detect reads the first 1 MB of each file. You can review and change the result per file on the next step.</p></div>`;
$('uploadPanel').querySelector('.enterprise-setup-actions').prepend(uploadFormatSettings);
uploadFormatSettings.querySelector('.file-settings-menu').append(uploadSettings);
uploadFormatSettings.hidden=!!job&&['ready','queued','running','complete'].includes(job.state);
document.addEventListener('pointerdown',event=>{if(uploadFormatSettings.open&&!uploadFormatSettings.contains(event.target))uploadFormatSettings.open=false;});
uploadFormatSettings.addEventListener('keydown',event=>{if(event.key==='Escape'&&!openSelectWidget){uploadFormatSettings.open=false;uploadFormatSettings.querySelector('summary').focus();}});

// Dense enterprise analysis dashboard. Icons use the same offline Lucide line language.
const analysisIcons={
 overview:'<path d="M3 3v18h18"/><path d="M7 16l4-5 4 3 5-7"/>',
 equal:'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
 rows:'<path d="M4 6h16M4 12h10M4 18h7"/><path d="m17 14 4 4m0-4-4 4"/>',
 cells:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18m6-12v12"/>',
 left:'<path d="M9 6 3 12l6 6M3 12h14"/><path d="M21 5v14"/>',
 right:'<path d="m15 6 6 6-6 6M7 12h14"/><path d="M3 5v14"/>',
 columns:'<path d="M4 5h16M4 12h10M4 19h13"/><circle cx="19" cy="12" r="2"/>',
 reports:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
 download:'<path d="M12 3v12m-4-4 4 4 4-4"/><path d="M5 21h14"/>',
 project:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'
};
const analysisSvg=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${analysisIcons[name]}</svg>`;
const analysisMetricMap={
 'Equal keys':['equal','Matched keys with identical values'],
 'Unequal keys':['rows','Matched keys with at least one difference'],
 'Changed cells':['cells','Individual value changes'],
 'Champion-only keys':['left','Only in champion dataset'],
 'Challenger-only keys':['right','Only in challenger dataset']
};
function enhanceAnalysisDashboard(){
 const results=$('results');if(!results||!job?.summary)return;results.classList.add('analysis-dashboard');
 const heading=results.querySelector('.result-heading'),copy=heading?.firstElementChild;
 if(copy&&!copy.classList.contains('analysis-heading-copy')){copy.classList.add('analysis-heading-copy');const icon=document.createElement('span');icon.className='analysis-heading-icon';icon.innerHTML=analysisSvg('overview');copy.prepend(icon);}
 const project=heading?.querySelector('button');if(project&&!project.querySelector('svg'))project.insertAdjacentHTML('afterbegin',analysisSvg('project'));
 const cards=[...$('metrics').children];
 for(const card of cards){
  const label=card.querySelector('small')?.textContent||'',config=analysisMetricMap[label];if(!config)continue;
  if(!card.querySelector('.analysis-metric-icon')){const icon=document.createElement('span');icon.className='analysis-metric-icon';icon.innerHTML=analysisSvg(config[0]);card.prepend(icon);const hint=document.createElement('span');hint.className='analysis-metric-hint';hint.textContent=config[1];card.append(hint);}
 }
 const summary=job.summary,matched=Math.max(0,Number(summary.matched_keys||0)),changeCounts=[...$('columnSummary').querySelectorAll('.column-row strong')].map(node=>Number(node.textContent.replace(/[^0-9.-]/g,''))||0),maxChange=Math.max(1,...changeCounts);
 for(const row of $('columnSummary').querySelectorAll('.column-row')){
  if(row.querySelector('.analysis-column-meter'))continue;
  const count=Number((row.querySelector('strong')?.textContent||'0').replace(/[^0-9.-]/g,''))||0;
  const matchRate=matched?Math.max(0,100-count*100/matched):0;
  row.dataset.risk=matchRate<95?'high':matchRate<99?'medium':'low';
  const meta=document.createElement('span');meta.className='analysis-column-rate';meta.textContent=matched?`${matchRate.toFixed(matchRate>99?2:1)}% match`:'No matched keys';
  const meter=document.createElement('span');meter.className='analysis-column-meter';meter.innerHTML=`<i style="width:${Math.max(3,count*100/maxChange)}%"></i>`;
  row.append(meta,meter);
 }
 const columnTitle=results.querySelector('.column-panel .section-title h2');if(columnTitle)columnTitle.innerHTML=analysisSvg('columns')+'<span>Differences by column</span>';
 const reportTitle=results.querySelector('.download-panel h2');if(reportTitle)reportTitle.innerHTML=analysisSvg('reports')+'<span>Reports</span>';
 for(const button of results.querySelectorAll('.download-button'))if(!button.querySelector('.analysis-download-icon')){const icon=document.createElement('span');icon.className='analysis-download-icon';icon.innerHTML=analysisSvg('download');button.append(icon);}
 results.querySelector('.download-panel>p:not(.eyebrow):not(.export-status):not(.fine)')?.remove();
}
const analysisSummaryRender=renderSummary;
renderSummary=function(){analysisSummaryRender();enhanceAnalysisDashboard();};
if(job?.state==='complete')enhanceAnalysisDashboard();

// Design system, phase 1: the app shell and Files & headers adopt tokens/components/shell.css.
// Presentation only; every control keeps its id, listeners and controller.
for(const element of [document.querySelector('.workspace-topbar'),document.querySelector('#mainWorkspace>.heading'),$('comparisonFlow'),$('uploadPanel'),$('headersPanel'),$('sheetPanel')])element?.classList.add('ds');
function setLucide(target,name){if(target)target.innerHTML=lucide(name);}
function lucideNode(name,className){const holder=document.createElement('span');holder.innerHTML=lucide(name,className);return holder.firstElementChild;}
setLucide($('navNew').querySelector('.comparison-chevron'),'chevron-down');
// Compare files menu: one inner wrapper lets the menu animate the space it takes, so the items below
// move with it. Closing plays the same motion in reverse before hiding (expanded sidebar only).
{
 const menu=$('comparisonMenu'),inner=document.createElement('div');inner.className='ds-menu-inner';inner.append(...menu.childNodes);menu.append(inner);
 let closingTimer=null;
 const finishClose=()=>{clearTimeout(closingTimer);menu.classList.remove('is-closing');menu.hidden=true;};
 menu.addEventListener('animationend',event=>{if(event.target===menu&&menu.classList.contains('is-closing'))finishClose();});
 // Clicking "Compare files" while the menu is still closing reopens it from where it is.
 $('navNew').addEventListener('click',event=>{if(!menu.classList.contains('is-closing'))return;event.stopImmediatePropagation();clearTimeout(closingTimer);menu.classList.remove('is-closing');$('navNew').setAttribute('aria-expanded','true');},{capture:true});
 closeComparisonMenu=function(){
  $('navNew').setAttribute('aria-expanded','false');
  if(menu.classList.contains('is-closing'))return;
  if(menu.hidden)return;
  const animate=!document.body.classList.contains('sidebar-collapsed')&&!document.body.classList.contains('reduce-motion')&&!matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(!animate){menu.hidden=true;return;}
  menu.classList.add('is-closing');closingTimer=setTimeout(finishClose,650);
 };
}
// Top bar: Lucide help icons.
{
 const summary=$('workspaceHelp').querySelector('summary');
 summary.querySelector('svg:not(.help-chevron)')?.replaceWith(lucideNode('circle-help'));
 summary.querySelector('.help-chevron')?.replaceWith(lucideNode('chevron-down','help-chevron'));
 $('takeTour').prepend(lucideNode('compass'));$('keyboardHelp').prepend(lucideNode('keyboard'));
}
// Stepper: numbered captions, completed steps and a progress line.
for(const [index,id] of navStages.entries())$(id).querySelector('.step-name')?.setAttribute('data-step',index+1);
function syncStepProgress(){
 const items=navStages.map(id=>$(id).closest('li'));const active=items.findIndex(li=>li.querySelector('.phase-button.active'));
 items.forEach((li,index)=>li.classList.toggle('is-complete',active>index&&!li.querySelector('.phase-button').disabled));
 $('comparisonFlow').style.setProperty('--ds-progress',active<0?0:(active+1)/items.length);
}
new MutationObserver(syncStepProgress).observe($('comparisonFlow'),{subtree:true,attributes:true,attributeFilter:['class','disabled']});syncStepProgress();
// Sources collapse: one inner wrapper lets the body animate its grid row to the content's real height.
{const inner=document.createElement('div');inner.className='uploaded-sources-inner';inner.append(...uploadedSourcesBody.childNodes);uploadedSourcesBody.append(inner);}
// Files: format note icon, source toggle, "vs" marker, format chips and Lucide icons.
{
 const status=$('detectedFileStatus');
 if(status){
  // app.js rewrites this text; keep the icon in front of it, and show the note only for a mismatched
  // pair or the JSON size limit (the idle default and detected-format echo add nothing).
  const decorate=()=>{status.classList.toggle('is-default',/^File format is detected automatically\.$|column headers are read after upload\.$/.test(status.textContent));if(status.firstElementChild?.matches('svg.lucide'))return;const message=status.textContent;status.replaceChildren(lucideNode('info'),Object.assign(document.createElement('span'),{textContent:message}));};
  decorate();new MutationObserver(decorate).observe(status,{childList:true});
 }
 const mode=$('uploadPanel').querySelector('.source-mode');mode.classList.add('ds-segmented');
 $('computerSource').prepend(lucideNode('upload'));$('serverSource').prepend(lucideNode('server'));
 for(const button of [$('computerSource'),$('serverSource')])button.classList.remove('subtle');
 // Both source modes share the same two-card layout with a ⇄ marker between the cards.
 for(const first of [$('leftCard'),$('leftServerPath').closest('.server-path-card')]){const vs=document.createElement('span');vs.className='ds-vs';vs.setAttribute('aria-hidden','true');vs.innerHTML=lucide('arrow-left-right');first.after(vs);}
 for(const line of $('uploadPanel').querySelectorAll('.file-type-line'))line.replaceChildren(...line.textContent.split('·').map(type=>Object.assign(document.createElement('span'),{textContent:type.trim()})));
 for(const icon of $('uploadPanel').querySelectorAll('.server-path-icon'))setLucide(icon,'hard-drive');
 $('uploadPanel').querySelector('.server-source-note>svg')?.replaceWith(lucideNode('lock'));
 $('uploadPanel').querySelector('.upload-profile>summary>svg')?.replaceWith(lucideNode('layout-template'));
 setLucide($('uploadPanel').querySelector('.profile-menu-icon'),'layout-template');
 // Manage templates is an icon button beside the template picker; its click handler is unchanged.
 const manage=$('manageProfiles'),picker=$('quickProfile').closest('.tu-select')||$('quickProfile'),pickerRow=document.createElement('div');
 pickerRow.className='ds-profile-row';picker.before(pickerRow);pickerRow.append(picker,manage);
 manage.className='ds-icon-btn';manage.type='button';manage.setAttribute('aria-label','Manage templates');manage.title='Manage templates';manage.innerHTML=lucide('settings');
 // The profile note only appears once a template is chosen or applied.
 const profileStatus=$('quickProfileStatus'),syncProfileStatus=()=>profileStatus.classList.toggle('is-idle',/^(Applied after both files|No template selected)/.test(profileStatus.textContent));
 new MutationObserver(syncProfileStatus).observe(profileStatus,{childList:true,characterData:true,subtree:true});syncProfileStatus();
 for(const [summary,label] of [[$('uploadFormatSettings').querySelector('summary'),'Text settings'],[$('uploadPanel').querySelector('.upload-profile>summary'),'Profile templates']]){
  summary.removeAttribute('title');summary.prepend(Object.assign(document.createElement('span'),{className:'ds-action-label',textContent:label}));
 }
 setLucide($('headerSearchToggle'),'search');
 // Reset edits: icon action that slides open to show its label (same motion as the upload toolbar).
 const reset=$('headerReset');reset.classList.remove('subtle');reset.classList.add('ds-expand-action');reset.type='button';reset.setAttribute('aria-label','Reset edits');
 reset.replaceChildren(Object.assign(document.createElement('span'),{className:'ds-action-label',textContent:'Reset edits'}),lucideNode('rotate-ccw'));
 // Server cards match the drop-zone height; the access note appears only once it has a result.
 new ResizeObserver(([entry])=>{const height=entry.borderBoxSize?.[0]?.blockSize||entry.target.offsetHeight;if(height)$('uploadPanel').style.setProperty('--ds-source-card-height',height+'px');}).observe($('leftCard'));
 const serverNote=$('serverSourceHelp'),serverActions=serverNote.closest('.server-source-actions');
 const syncServerNote=()=>serverActions.classList.toggle('is-prompt',/^Enter both/.test(serverNote.textContent));
 new MutationObserver(syncServerNote).observe(serverNote,{childList:true,characterData:true,subtree:true});syncServerNote();
}

// Pipeline & logs: "Console view" switch, off by default. On shows the stage rail beside the live console;
// off shows the stage graph across the full panel. The choice is remembered in this browser.
window.addEventListener('DOMContentLoaded',()=>{
 const pipelinePanel=$('runningPanel'),actions=pipelinePanel.querySelector('.pipeline-actions');if(!actions)return;
 const key='compare-console-view';let stored=null;try{stored=localStorage.getItem(key);}catch(error){}
 const toggle=document.createElement('button');toggle.type='button';toggle.id='consoleViewToggle';toggle.className='ds-switch';toggle.setAttribute('role','switch');
 toggle.innerHTML='<span class="ds-switch-label">Console view</span><span class="ds-switch-track" aria-hidden="true"><span class="ds-switch-thumb"></span></span>';
 const apply=on=>{pipelinePanel.dataset.liveTab=on?'console':'graph';toggle.setAttribute('aria-checked',String(on));};
 toggle.addEventListener('click',()=>{const on=toggle.getAttribute('aria-checked')!=='true';apply(on);try{localStorage.setItem(key,on?'on':'off');}catch(error){}});
 actions.prepend(toggle);apply(stored==='on');
});

// Pipeline graph (Console view off): start → stages → end on one line, with the Re-evaluate branch
// below Write results and Analysis. Built from #pipelineStages after every refresh, so it always
// shows the same stages and states as the console rail; that list stays available to screen readers.
{
 const graph=document.createElement('div');graph.id='pipelineGraph';graph.setAttribute('aria-hidden','true');
 $('pipelineStages').after(graph);
 const reusedNote='Reused from original run';
 function describe(li){
  const kind=li.dataset.kind,label=li.querySelector('.pipeline-label')?.textContent||li.textContent;
  const read=/file 1/i.test(label)?['Read file 1','Champion file']:/file 2/i.test(label)?['Read file 2','Challenger file']:/both/i.test(label)?['Read & sort','Both files']:['Read & index','Keys of both files'];
  const text={validate:['Validate','Check configuration'],read,compare:['Compare','Match keys, find changes'],write:['Write results','Results & HTML report'],complete:['Analysis','View results'],review:['Re-evaluate Differences','Apply rules to retained evidence']}[kind]||[label,''];
  const state=['done','active','failed'].find(name=>li.classList.contains(name))||'pending';
  return {kind,title:text[0],subtitle:text[1],state};
 }
 const icons={validate:'shield-check',read:'arrow-down-narrow-wide',compare:'arrow-left-right',write:'file-text',complete:'flag',review:'refresh-cw'};
 function node(stage,column,extra=''){
  const live=stage.kind==='compare'&&stage.state==='active'&&job?.progress?.rows!==undefined?`<b>${number(job.progress.rows)} keys</b>`:'';
  return `<div class="pg-node is-${stage.state} ${extra}" data-col="${column}"><span class="pg-box"><span class="pg-port pg-port-in"></span>${lucide(icons[stage.kind]||'circle','pg-icon')}<span class="pg-port pg-port-out"></span></span><strong>${stage.title}</strong><small>${stage.subtitle}</small>${live}</div>`;
 }
 function render(){
  const items=[...$('pipelineStages').children].map(describe);
  const review=items.find(stage=>stage.kind==='review');
  let main=items.filter(stage=>stage.kind!=='review');
  // A review re-run reuses the original read and compare work; show those stages as reused.
  if(review)main=[{kind:'validate',title:'Validate',subtitle:reusedNote,state:'reused'},{kind:'read',title:'Read & index',subtitle:reusedNote,state:'reused'},{kind:'compare',title:'Compare',subtitle:reusedNote,state:'reused'},...main];
  const branch=review||{kind:'review',title:'Re-evaluate Differences',subtitle:'Skipped for this run',state:'skipped'};
  const reached=state=>['done','active','failed','reused'].includes(state);
  const endState=job?.state==='complete'?'done':job?.state==='error'?'failed':'pending';
  let html=`<div class="pg-terminal pg-start${main.some(stage=>reached(stage.state))||job?.state!=='queued'?' is-on':''}" data-col="1">${lucide('play')}</div>`;
  main.forEach((stage,i)=>{const column=2+i*2;html+=`<span class="pg-link${reached(stage.state)?' is-on':''}" data-col="${column}"></span>`+node(stage,column+1);});
  const endColumn=2+main.length*2;
  html+=`<span class="pg-link${endState!=='pending'?' is-on':''}" data-col="${endColumn}"></span><div class="pg-terminal pg-end is-${endState}" data-col="${endColumn+1}">${lucide(endState==='done'?'check':endState==='failed'?'x':'square')}</div>`;
  // The branch leaves after Compare and rejoins after Analysis, as in the reference design.
  const write=main.findIndex(stage=>stage.kind==='write'),analysis=main.findIndex(stage=>stage.kind==='complete');
  if(write>=0&&analysis>=0){const from=3+write*2,to=4+analysis*2;html+=`<div class="pg-branch is-${branch.state}" data-from="${from}" data-to="${to}">${node(branch,0,'pg-branch-node')}</div>`;}
  // Columns: start, then connector + node pairs, then connector + end.
  graph.style.gridTemplateColumns=['var(--pg-terminal)',...main.flatMap(()=>['minmax(28px,1fr)','var(--pg-node-col)']),'minmax(28px,1fr)','var(--pg-terminal)'].join(' ');
  graph.innerHTML=html;
  // Grid placement goes through CSSOM: the page's Content-Security-Policy blocks inline style attributes.
  for(const element of graph.querySelectorAll('[data-col]'))if(element.dataset.col!=='0')element.style.gridColumn=element.dataset.col;
  for(const element of graph.querySelectorAll('[data-from]'))element.style.gridColumn=`${element.dataset.from} / ${element.dataset.to}`;
 }
 const previous=renderPipeline;renderPipeline=async function(){await previous();render();};
}

// Recompare button: Lucide list-restart icon on a cyan tile, title and hint, and the keyboard shortcut.
// Classes, listeners and the idle collapse in app.js are unchanged.
{
 const fab=$('recompareFab'),mac=/Mac|iPhone|iPad/.test(navigator.platform);
 fab.innerHTML=`<span class="fab-icon" aria-hidden="true">${lucide('list-restart')}</span><span class="fab-caption"><strong>Recompare</strong><small>Adjust rules · originals kept</small></span><kbd class="fab-key" aria-hidden="true">${mac?'⌥':'Alt'} R</kbd>`;
 fab.removeAttribute('title');
}

// Recompare dialog: macOS genie minimize/maximize. The dialog is drawn as thin horizontal slices; each slice is
// squeezed to the width of a funnel that ends at the Recompare button, so the window warps into the button
// (minimize) and pours back out of it (maximize). Slices are temporary visual copies in a top-layer overlay,
// hidden from assistive technology, and removed when the animation ends.
{
 const dialog=$('rerunDialog'),fab=$('recompareFab'),nativeShow=dialog.showModal.bind(dialog),nativeClose=dialog.close.bind(dialog);
 const reduced=()=>document.body.classList.contains('reduce-motion')||matchMedia('(prefers-reduced-motion: reduce)').matches;
 const smooth=u=>u<=0?0:u>=1?1:u*u*(3-2*u);
 const ease=u=>u<.5?4*u*u*u:1-Math.pow(-2*u+2,3)/2;
 const SLICES=64;
 let running=null;
 function build(){
  const d=dialog.getBoundingClientRect(),f=!fab.hidden&&fab.getClientRects().length?fab.getBoundingClientRect():null;
  const g={L:d.left,T:d.top,W:d.width,H:d.height,tx:f?f.left+Math.min(f.width,f.height)/2:innerWidth/2,ty:f?f.top+f.height/2:innerHeight-24,tw:f?Math.min(f.width,f.height)*.8:40};
  const overlay=document.createElement('div');overlay.className='ds-genie';overlay.setAttribute('aria-hidden','true');overlay.setAttribute('popover','manual');
  const computed=getComputedStyle(dialog),copy=['backgroundColor','backgroundImage','borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor','borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius','paddingTop','paddingRight','paddingBottom','paddingLeft','color','fontFamily','fontSize','lineHeight','display','flexDirection','rowGap','columnGap','overflow'];
  // The copy keeps its ids so every #rerunDialog style applies and it looks exactly like the dialog. It is
  // added after the dialog, so lookups by id still find the real elements first.
  const template=dialog.cloneNode(true);template.querySelector('.tu-request-loader')?.remove();
  const slices=[];
  for(let i=0;i<SLICES;i++){
   const slice=document.createElement('div');slice.className=dialog.className;slice.id=dialog.id;slice.setAttribute('open','');
   for(const name of copy)slice.style[name]=computed[name];
   Object.assign(slice.style,{position:'fixed',left:g.L+'px',top:g.T+'px',width:g.W+'px',height:g.H+'px',margin:'0',boxSizing:'border-box',transformOrigin:'0 0',clipPath:`inset(${(i*g.H/SLICES).toFixed(2)}px 0 ${(g.H-(i+1)*g.H/SLICES-.6).toFixed(2)}px 0)`,willChange:'transform'});
   slice.append(...[...template.cloneNode(true).childNodes]);slices.push(slice);
  }
  overlay.append(...slices);document.body.append(overlay);
  try{overlay.showPopover();}catch(error){}
  return {g,overlay,slices};
 }
 // Funnel half-edges at screen height y once fraction q of the funnel has formed.
 function edges(g,y,q){
  const half=g.tw/2;if(y>=g.T+g.H)return [g.tx-half,g.tx+half];
  const u=smooth((y-g.T)/g.H)*q;return [g.L+(g.tx-half-g.L)*u,g.L+g.W+(g.tx+half-g.L-g.W)*u];
 }
 // p: 0 = window fully open, 1 = fully inside the button.
 function frame(state,p){
  const {g,slices}=state,split=.4,q=ease(Math.min(1,p/split)),s=ease(Math.max(0,(p-split)/(1-split)));
  const offset=s*(g.ty-g.T+8),h=g.H/SLICES;
  slices.forEach((slice,i)=>{
   const y=g.T+(i+.5)*h+offset;
   if(y>g.ty){slice.style.opacity='0';return;}
   const [a,b]=edges(g,y,q),scale=Math.max(.001,(b-a)/g.W);
   slice.style.opacity='1';
   slice.style.transform=`translate(${(a-g.L).toFixed(2)}px,${offset.toFixed(2)}px) scaleX(${scale.toFixed(4)})`;
  });
 }
 function play(from,to,duration){
  if(running)running.cancel();
  const state=build();let stopped=false,done;
  const finished=new Promise(resolve=>{done=resolve;});
  const end=()=>{if(stopped)return;stopped=true;clearTimeout(fallback);try{state.overlay.hidePopover();}catch(error){}state.overlay.remove();running=null;done();};
  // A hidden tab stops animation frames; never leave the dialog stuck mid-genie.
  const fallback=setTimeout(end,duration+300),start=performance.now();
  const tick=now=>{if(stopped)return;const u=Math.min(1,(now-start)/duration);frame(state,from+(to-from)*u);if(u<1)requestAnimationFrame(tick);else end();};
  frame(state,from);requestAnimationFrame(tick);
  running={cancel:end,finished,state};
  return running;
 }
 dialog.showModal=function(){
  nativeShow();if(reduced())return;
  dialog.style.visibility='hidden';
  dialog.animate([{opacity:0},{opacity:1}],{duration:700,easing:'ease-out',pseudoElement:'::backdrop'});
  play(1,0,1000).finished.then(()=>{dialog.style.visibility='';});
 };
 let closing=false;
 async function genieClose(){
  if(!dialog.open||$('startRerun').disabled||closing)return;
  if(reduced()){nativeClose();return;}
  closing=true;
  const animation=play(0,1,900);dialog.style.visibility='hidden';
  dialog.animate([{opacity:1},{opacity:0}],{duration:800,easing:'ease-in',fill:'forwards',pseudoElement:'::backdrop'});
  try{await animation.finished;}finally{nativeClose();dialog.style.visibility='';dialog.getAnimations().forEach(item=>item.cancel());closing=false;}
 }
 $('closeRerun').addEventListener('click',event=>{event.stopImmediatePropagation();genieClose();},{capture:true});
 dialog.addEventListener('cancel',event=>{event.preventDefault();event.stopImmediatePropagation();genieClose();},{capture:true});
 // Alt/⌥ R (and R when not typing) toggles: it opens Recompare, and pressing it again closes it.
 document.addEventListener('keydown',event=>{
  if(!dialog.open||event.repeat||event.ctrlKey||event.metaKey)return;
  const typing=event.target?.closest?.('input,textarea,select,[contenteditable="true"]');
  if(event.code==='KeyR'&&(event.altKey||!typing)){event.preventDefault();event.stopImmediatePropagation();genieClose();}
 },{capture:true});
}

// Analysis: Reports is a header button beside "Attach to project". The existing download panel
// (Excel/HTML buttons, statuses, cancel controls and handlers) moves into its popover unchanged.
{
 const panel=$('results').querySelector('.download-panel'),heading=$('results').querySelector('.result-heading');
 const menu=document.createElement('details');menu.id='reportsMenu';menu.className='ds-reports';
 menu.innerHTML=`<summary aria-label="Export reports">${lucide('download')}<span>Export</span>${lucide('chevron-down','ds-reports-chevron')}</summary><div class="ds-reports-popover" role="group" aria-label="Export reports"></div>`;
 menu.querySelector('.ds-reports-popover').append(panel);
 // Export options: one row per format with its icon, description and live status. The original buttons,
 // status lines and cancel controls move into the rows, so app.js keeps updating them as before.
 panel.querySelector('.report-help')?.remove();panel.querySelector('.fine')?.remove();
 const popoverHead=document.createElement('div');popoverHead.className='ds-export-head';popoverHead.innerHTML='<strong>Export</strong><span>Reports include the current review and comments.</span>';
 const options=document.createElement('div');options.className='ds-export-options';
 for(const [kind,icon,cancel] of [['excel','file-spreadsheet','cancelExcel'],['html','file-code','cancelHtml']]){
  // The panel is still detached here, so look controls up inside it rather than with $().
  const find=id=>panel.querySelector('#'+id),button=find(kind),status=find(kind+'Status'),row=document.createElement('div');row.className='ds-export-option';
  const title=button.querySelector('strong'),subtitle=button.querySelector('small');
  const copy=document.createElement('span');copy.className='ds-export-copy';copy.append(title,subtitle,status);
  button.replaceChildren(lucideNode(icon,'ds-export-icon'),copy,lucideNode('download','ds-export-go'));
  row.append(button,find(cancel));options.append(row);
 }
 panel.replaceChildren(popoverHead,options,...[...panel.children].filter(child=>child.matches('#openAnalysis,.csv-links,.eyebrow,h2')&&(child.hidden=true)));
 const place=()=>{const attach=[...heading.querySelectorAll('button')].find(button=>/Attach to project/.test(button.textContent));if(attach)attach.after(menu);else heading.append(menu);};
 place();
 document.addEventListener('pointerdown',event=>{if(menu.open&&!menu.contains(event.target))menu.open=false;});
 menu.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu.open){event.stopPropagation();menu.open=false;menu.querySelector('summary').focus();}});
}

// Analysis redesign: ranked column table (rank · column · match · share of mismatches · count) and a
// "Column health" summary. Rows keep their buttons and click handlers; bar widths go through CSSOM
// because the Content-Security-Policy blocks inline style attributes.
function redesignAnalysis(){
 const results=$('results'),summary=job?.summary;if(!results||!summary)return;
 const list=$('columnSummary'),rows=[...list.querySelectorAll('.column-row')];
 const matched=Math.max(0,Number(summary.matched_keys||0)),counts=rows.map(row=>Number((row.querySelector('strong')?.textContent||'0').replace(/[^0-9.-]/g,''))||0),maxCount=Math.max(1,...counts);
 list.classList.add('ds-column-table');
 if(!list.previousElementSibling?.classList.contains('ds-column-head')&&rows.length){const head=document.createElement('div');head.className='ds-column-head';head.setAttribute('aria-hidden','true');head.innerHTML='<span>#</span><span>Column</span><span>Match</span><span>Share of mismatches</span><span>Changes</span>';list.before(head);}
 rows.forEach((row,index)=>{
  if(row.dataset.dsDone)return;row.dataset.dsDone='1';
  const name=row.firstElementChild?.textContent||'',count=counts[index],rate=matched?Math.max(0,100-count*100/matched):0;
  const risk=rate<95?'high':rate<99?'medium':'low';
  row.dataset.risk=risk;row.classList.add('ds-column-row');
  const rank=Object.assign(document.createElement('span'),{className:'ds-rank',textContent:String(index+1)});
  const label=Object.assign(document.createElement('span'),{className:'ds-col-name',textContent:name});label.title=name;
  const pill=Object.assign(document.createElement('span'),{className:'ds-rate is-'+risk,textContent:matched?`${rate.toFixed(rate>99&&rate<100?2:1)}%`:'—'});
  const bar=document.createElement('span');bar.className='ds-share';const fill=document.createElement('i');fill.style.width=Math.max(2,count*100/maxCount)+'%';bar.append(fill);
  const value=document.createElement('span');value.className='ds-count';value.innerHTML=`<b></b><small>changes</small>`;value.querySelector('b').textContent=number(count);
  const go=document.createElement('span');go.className='ds-go';go.innerHTML=lucide('chevron-right');
  row.replaceChildren(rank,label,pill,bar,value,go);
 });
 // Column health replaces the donut with a stacked bar and per-band rows.
 const panel=results.querySelector('.result-chart-panel');if(!panel)return;
 const all=Object.values(summary.changed_cells_by_column||{}),total=all.length;
 const bands=[['100% match','perfect'],['99% to <100%','low'],['95% to <99%','medium'],['Below 95%','high']].map(([label,key])=>({label,key,count:0}));
 if(matched)for(const count of all)bands[count===0?0:count*100<=matched?1:count*20<=matched?2:3].count++;
 let health=panel.querySelector('.ds-health');
 if(!health){health=document.createElement('div');health.className='ds-health';panel.append(health);}
 const perfect=bands[0].count,pct=total?Math.round(perfect*100/total):0;
 health.innerHTML=`<div class="ds-health-hero"><strong>${number(perfect)}<span> / ${number(total)}</span></strong><p>value columns match 100%</p><em>${pct}% fully matching</em></div><div class="ds-stack" role="img"></div><ul class="ds-bands"></ul><p class="ds-health-foot"></p>`;
 const stack=health.querySelector('.ds-stack'),list2=health.querySelector('.ds-bands');
 stack.setAttribute('aria-label',bands.map(b=>`${b.label}: ${b.count}`).join(', '));
 for(const band of bands){
  if(band.count){const seg=document.createElement('span');seg.className='is-'+band.key;seg.style.flexGrow=String(band.count);seg.title=`${band.label}: ${band.count}`;stack.append(seg);}
  const li=document.createElement('li');li.className='is-'+band.key+(band.count?'':' is-empty');
  li.innerHTML='<span class="ds-dot"></span><span class="ds-band-label"></span><span class="ds-band-bar"><i></i></span><b></b>';
  li.querySelector('.ds-band-label').textContent=band.label;li.querySelector('b').textContent=number(band.count);
  li.querySelector('i').style.width=(total?band.count*100/total:0)+'%';list2.append(li);
 }
 health.querySelector('.ds-health-foot').textContent=`${number(summary.changed_cells||0)} mismatched cells · ${number(summary.changed_rows||0)} keys with differences`;
 const title=panel.querySelector('h2');if(title)title.textContent='Match Mismatch Overview';
 // Records pie: every key lands in exactly one slice, so the slices add up to all keys in both files.
 const slices=[['Equal keys','equal',summary.equal_rows],['Unequal keys','unequal',summary.changed_rows],['Only in champion','left',summary.left_only],['Only in challenger','right',summary.right_only]].map(([label,key,count])=>({label,key,count:Math.max(0,Number(count)||0)}));
 const keys=slices.reduce((sum,slice)=>sum+slice.count,0);
 const pie=document.createElement('div');pie.className='ds-pie';
 const svgNS='http://www.w3.org/2000/svg',svg=document.createElementNS(svgNS,'svg');svg.setAttribute('viewBox','-1 -1 2 2');svg.setAttribute('role','img');
 svg.setAttribute('aria-label',slices.map(slice=>`${slice.label}: ${number(slice.count)}`).join(', '));
 let angle=-Math.PI/2;
 for(const slice of slices){
  if(!slice.count)continue;const share=slice.count/keys;let shape;
  if(share>=1){shape=document.createElementNS(svgNS,'circle');shape.setAttribute('r','1');}
  else{const end=angle+share*2*Math.PI,large=share>.5?1:0;shape=document.createElementNS(svgNS,'path');
   shape.setAttribute('d',`M0 0L${Math.cos(angle)} ${Math.sin(angle)}A1 1 0 ${large} 1 ${Math.cos(end)} ${Math.sin(end)}Z`);angle=end;}
  shape.setAttribute('class','is-'+slice.key);const tip=document.createElementNS(svgNS,'title');tip.textContent=`${slice.label}: ${number(slice.count)} (${percentText(slice.count,keys)})`;shape.append(tip);svg.append(shape);
 }
 const legend=document.createElement('ul');legend.className='ds-pie-legend';
 for(const slice of slices){
  const li=document.createElement('li');li.className='is-'+slice.key+(slice.count?'':' is-empty');
  li.innerHTML='<span class="ds-dot"></span><span class="ds-pie-label"></span><b></b><small></small>';
  li.querySelector('.ds-pie-label').textContent=slice.label;li.querySelector('b').textContent=number(slice.count);li.querySelector('small').textContent=keys?percentText(slice.count,keys):'—';
  legend.append(li);
 }
 pie.append(svg,legend);
 health.querySelector('.ds-stack')?.remove();
 const columnsHead=document.createElement('p');columnsHead.className='ds-health-sub';columnsHead.textContent='Columns';
 health.prepend(pie,columnsHead);
}
{const previousRender=renderSummary;renderSummary=function(){previousRender();redesignAnalysis();};if(job?.state==='complete')redesignAnalysis();}

// Keyboard: single keys, no modifiers, so navigation is one press. They work identically on Mac and Windows
// and are ignored while typing, in a dialog, or when shortcuts are switched off. The existing Alt/⌥
// shortcuts in app.js keep working for people who already use them.
{
 const pages={c:'navNew',p:'projectsPanel',h:'historyPanel',k:'containersPanel',t:'profilesPanel',d:'docsPanel'};
 const steps=['navFiles','navScope','navPipeline','navResults'];
 const usable=el=>el&&!el.disabled&&!el.closest('[hidden],[inert]')&&el.getClientRects().length>0;
 const typing=target=>target?.closest?.('input,textarea,select,[contenteditable="true"],[role="textbox"]');
 document.addEventListener('keydown',event=>{
  if(event.altKey||event.ctrlKey||event.metaKey||event.repeat||event.isComposing||event.defaultPrevented)return;
  if(typing(event.target)||document.querySelector('dialog[open]')||$('tourCard')?.hidden===false)return;
  try{if(localStorage.getItem('compare-shortcuts')==='off')return;}catch(error){}
  const key=event.key.length===1?event.key.toLowerCase():event.key;
  let target=null;
  if(/^[1-4]$/.test(key))target=$(steps[Number(key)-1]);
  else if(key==='['||key===']'){
   const current=steps.findIndex(id=>$(id).classList.contains('active'));if(current<0)return;
   const direction=key===']'?1:-1;
   for(let i=current+direction;i>=0&&i<steps.length;i+=direction)if(usable($(steps[i]))){target=$(steps[i]);break;}
  }
  else if(key==='c'){event.preventDefault();$('menuContinueComparison')?.click();return;}
  else if(pages[key]){event.preventDefault();goView(pages[key]).catch(showError);return;}
  else if(key==='r')target=$('recompareFab');
  else if(key==='b')target=$('sidebarToggle');
  else if(key==='?')target=$('keyboardHelp');
  else if(key==='/'){
   const view=document.querySelector('#mainWorkspace>section:not([hidden])')||document;
   const search=[...view.querySelectorAll('input[type="search"],input.search')].find(usable);
   if(search){event.preventDefault();search.focus();}
   else if(currentView==='headersPanel'||!$('headersPanel').hidden){event.preventDefault();$('headerSearchToggle').click();$('headerSearch').focus();}
   return;
  }
  if(!target||!usable(target))return;
  event.preventDefault();target.click();
 });
 // The guide lists the single keys, grouped by what they do.
 const dialog=$('shortcutsDialog'),list=dialog.querySelector('dl');
 const rows=[
  ['Navigate',[['Files & headers · Keys & scope · Pipeline · Analysis',['1','2','3','4']],['Previous / next step',['[',']']],['Continue current comparison',['C']],['Projects',['P']],['Job history',['H']],['Ignore key containers',['K']],['Profile templates',['T']],['Docs & FAQ',['D']]]],
  ['Actions',[['Search on this page',['/']],['Recompare (Analysis)',['R']],['Toggle sidebar',['B']],['This guide',['?']],['Close drawer or menu',['Esc']]]],
  ['Everywhere',[['Move between controls',['Tab','⇧ Tab']],['Activate',['Enter','Space']],['Move within tabs or steps',['← →']]]]
 ];
 list.replaceChildren();list.classList.add('ds-shortcut-list');
 for(const [group,items] of rows){
  const heading=document.createElement('dt');heading.className='ds-shortcut-group';heading.textContent=group;list.append(heading,document.createElement('dd'));
  for(const [label,keys] of items){const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');keys.forEach((k,i)=>{if(i)dd.append(Object.assign(document.createElement('span'),{className:'ds-key-sep',textContent:k.length===1&&keys[0].length===1?'':'/'}));dd.append(Object.assign(document.createElement('kbd'),{textContent:k}));});list.append(dt,dd);}
 }
 const note=[...dialog.querySelectorAll('p')].find(p=>/Windows|Mac|while typing/.test(p.textContent));
 if(note)note.textContent='Single keys, the same on Mac and Windows. They pause while you type in a field.';
 $('keyboardHelp')?.querySelector('kbd')&&($('keyboardHelp').querySelector('kbd').textContent='?');
 for(const [key,id] of [['1','navFiles'],['2','navScope'],['3','navPipeline'],['4','navResults'],['P','navProjects'],['H','navHistory'],['K','navContainers'],['T','navProfiles'],['D','navDocs'],['R','recompareFab'],['B','sidebarToggle']])$(id)?.setAttribute('aria-keyshortcuts',key);
 const fabKey=$('recompareFab')?.querySelector('.fab-key');if(fabKey)fabKey.textContent='R';
}

// Sidebar: Projects gets a dropdown listing every project (loaded when opened). Choosing one opens it
// in Projects; with no projects the list says so. The Projects row itself still navigates as before.
{
 const row=document.createElement('div');row.className='ds-side-row';$('navProjects').before(row);row.append($('navProjects'));
 const toggle=document.createElement('button');toggle.type='button';toggle.className='ds-side-expander';toggle.setAttribute('aria-label','Show projects');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','projectsSideMenu');toggle.innerHTML=lucide('chevron-down');
 row.append(toggle);
 const menu=document.createElement('div');menu.id='projectsSideMenu';menu.className='ds-side-submenu';menu.setAttribute('role','menu');menu.setAttribute('aria-label','Projects');menu.hidden=true;row.after(menu);
 const inner=document.createElement('div');inner.className='ds-menu-inner';menu.append(inner);
 async function fill(){
  inner.replaceChildren(Object.assign(document.createElement('p'),{className:'ds-side-empty',textContent:'Loading projects…'}));
  let projects=[];try{projects=(await api('/api/projects')).projects||[];}catch(error){inner.replaceChildren(Object.assign(document.createElement('p'),{className:'ds-side-empty',textContent:'Projects could not be loaded.'}));return;}
  if(!projects.length){inner.replaceChildren(Object.assign(document.createElement('p'),{className:'ds-side-empty',textContent:'No projects yet'}));return;}
  inner.replaceChildren(...projects.map(project=>{
   const item=document.createElement('button');item.type='button';item.setAttribute('role','menuitem');item.className='ds-side-subitem';
   const name=Object.assign(document.createElement('span'),{textContent:project.name}),count=Object.assign(document.createElement('small'),{textContent:String(project.comparisons??0)});
   item.title=`${project.name} · ${project.comparisons??0} comparisons`;item.append(name,count);
   item.onclick=async()=>{close();try{await goView('projectsPanel');await selectProject(project.id);}catch(error){showError(error);}};
   return item;
  }));
 }
 let closeTimer=null;
 function close(){
  toggle.setAttribute('aria-expanded','false');row.classList.remove('is-open');
  if(menu.hidden||menu.classList.contains('is-closing'))return;
  if(document.body.classList.contains('sidebar-collapsed')||document.body.classList.contains('reduce-motion')||matchMedia('(prefers-reduced-motion: reduce)').matches){menu.hidden=true;return;}
  menu.classList.add('is-closing');
  const finish=()=>{clearTimeout(closeTimer);if(menu.classList.contains('is-closing')){menu.classList.remove('is-closing');menu.hidden=true;}};
  menu.addEventListener('animationend',event=>{if(event.target===menu)finish();},{once:true});closeTimer=setTimeout(finish,650);
 }
 toggle.addEventListener('click',event=>{
  event.stopPropagation();
  if(document.body.classList.contains('sidebar-collapsed')){$('navProjects').click();return;}
  if(menu.classList.contains('is-closing')){clearTimeout(closeTimer);menu.classList.remove('is-closing');toggle.setAttribute('aria-expanded','true');row.classList.add('is-open');return;}
  if(!menu.hidden){close();return;}
  menu.hidden=false;toggle.setAttribute('aria-expanded','true');row.classList.add('is-open');fill();
 });
 menu.addEventListener('keydown',event=>{if(event.key==='Escape'){close();toggle.focus();}});
 new MutationObserver(()=>{if(document.body.classList.contains('sidebar-collapsed'))close();}).observe(document.body,{attributes:true,attributeFilter:['class']});
}

// Library pages: Ignore key containers and Profile templates. A creation card sits beside a searchable
// library of cards. The original inputs and buttons stay the source of truth; cards drive them.
{
 const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
 const libraryHeader=(title,subtitle,searchLabel)=>{const head=el('div','ds-lib-head');const copy=el('div');copy.append(el('h3','',title),el('p','',subtitle));const search=el('label','ds-lib-search');search.innerHTML=lucide('search');const input=el('input');input.type='search';input.placeholder=searchLabel;input.setAttribute('aria-label',searchLabel);search.append(input);const count=el('span','ds-badge');head.append(copy,search,count);return {head,input,count};};
 const empty=(icon,title,text)=>{const box=el('div','ds-lib-empty');box.innerHTML=lucide(icon);box.append(el('strong','',title),el('span','',text));return box;};

 // ── Ignore key containers ──
 const cPanel=$('containersPanel');cPanel.classList.add('ds','ds-library');
 cPanel.querySelector(':scope>.eyebrow')?.remove();cPanel.querySelector(':scope>h2')?.remove();
 const grid=cPanel.querySelector('.enterprise-container-grid'),[form,library]=grid.children;
 form.classList.add('ds-lib-form');library.classList.add('ds-lib-list');
 const formHead=el('div','ds-lib-form-head');formHead.innerHTML=`<span class="ds-lib-form-icon">${lucide('package-x')}</span>`;const fh=el('div');fh.append(el('h3','','New container'),el('p','','Group keys to exclude, with a reason that appears in reports.'));formHead.append(fh);form.prepend(formHead);
 const addHint=(id,text)=>{const field=$(id);const hint=el('small','ds-field-hint',text);(field.closest('label')||field).after(hint);return hint;};
 addHint('containerReason','Shown in the HTML and Excel reports next to the excluded keys.');
 const valuesHint=addHint('containerValues','Comma-separated values. For composite keys use JSON tuples, e.g. [["001","A"]].');
 const parsed=el('span','ds-parse-count');valuesHint.append(parsed);
 const countKeys=()=>{const text=$('containerValues').value.trim();let n=0;if(text){if(text.startsWith('[')){try{n=JSON.parse(text).length;}catch(e){n=-1;}}else n=text.split(',').map(v=>v.trim()).filter(Boolean).length;}parsed.textContent=n<0?' · Check the JSON format':n?` · ${n} key${n===1?'':'s'} detected`:'';parsed.classList.toggle('is-error',n<0);};
 $('containerValues').addEventListener('input',countKeys);
 $('saveContainer').prepend(lucideNode('plus'));
 cPanel.querySelector('.enterprise-guidance')?.remove();
 // The key-column count follows the values: JSON tuples give their length, plain values mean one key column.
 $('containerWidth').closest('label').hidden=true;
 $('saveContainer').addEventListener('click',()=>{const text=$('containerValues').value.trim();let width=1;if(text.startsWith('[')){try{const first=JSON.parse(text)[0];if(Array.isArray(first))width=first.length;}catch(error){}}$('containerWidth').value=String(width);},{capture:true});
 library.querySelector('h3')?.remove();
 const cHead=libraryHeader('Saved containers','Select these in Keys & scope to exclude their keys from a comparison.','Find a container');library.prepend(cHead.head);
 function paintContainers(){
  const list=$('containerLibrary'),q=cHead.input.value.trim().toLowerCase();
  const cards=[...list.querySelectorAll('.container-card')];cHead.count.textContent=`${cards.length} saved`;
  list.querySelector('.ds-lib-empty')?.remove();
  if(!cards.length){list.replaceChildren(empty('package-x','No containers yet','Create one on the left. It will be available to every comparison in this workspace.'));return;}
  let shown=0;
  for(const card of cards){
   if(!card.dataset.ds){card.dataset.ds='1';const item=keyContainers.find(c=>c.name===card.querySelector('h4')?.textContent);
    const top=el('div','ds-card-top');top.innerHTML=`<span class="ds-card-icon">${lucide('package-x')}</span>`;top.append(card.querySelector('h4'));
    if(item){const n=String(item.values||'').trim().startsWith('[')?(()=>{try{return JSON.parse(item.values).length;}catch(e){return null;}})():String(item.values||'').split(',').filter(v=>v.trim()).length;const meta=el('div','ds-card-meta');meta.append(el('span','ds-badge',n===null?'Keys':`${n} key${n===1?'':'s'}`));if(item.key_width>1)meta.append(el('span','ds-badge',`${item.key_width}-column keys`));top.append(meta);}
    card.prepend(top);const summary=card.querySelector('summary');if(summary)summary.textContent='View keys';}
   const match=!q||card.textContent.toLowerCase().includes(q);card.hidden=!match;if(match)shown++;
  }
  if(!shown)list.append(empty('search','No matching containers','Try a shorter search.'));
 }
 // Repaint when app.js rebuilds the list; the observer pauses while painting so it never re-triggers itself.
 const libraryObserver=new MutationObserver(()=>paintContainersSafely());
 function paintContainersSafely(){libraryObserver.disconnect();try{paintContainers();}finally{libraryObserver.observe($('containerLibrary'),{childList:true});}}
 cHead.input.addEventListener('input',paintContainersSafely);paintContainersSafely();

 // ── Profile templates ──
 const pPanel=$('profilesPanel');pPanel.classList.add('ds','ds-library');
 pPanel.querySelector(':scope>h2')?.remove();pPanel.querySelector(':scope>p')?.remove();
 const controls=$('profileControls'),legacy=controls.querySelector('.feature-box');legacy.hidden=true;
 const layout=el('div','enterprise-container-grid ds-lib-grid');controls.append(layout);
 const saveCard=el('section','ds-lib-form');
 const sHead=el('div','ds-lib-form-head');sHead.innerHTML=`<span class="ds-lib-form-icon">${lucide('layout-template')}</span>`;const sh=el('div');sh.append(el('h3','','Save as template'),el('p','','Store the current comparison’s headers, keys, scope and rules for reuse.'));sHead.append(sh);
 // Hold references: $() only finds elements attached to the document, and these move before the layout is attached.
 const nameInput=$('profileName'),saveButton=$('saveProfile'),statusLine=$('profileStatus');
 const nameLabel=el('label','','Template name');nameLabel.append(nameInput);nameInput.placeholder='e.g. Monthly bureau refresh';
 const saveNote=el('small','ds-field-hint');
 saveCard.append(sHead,nameLabel,saveNote,saveButton,statusLine);
 saveButton.className='primary';saveButton.textContent='Save template';saveButton.prepend(lucideNode('plus'));
 const libraryCard=el('section','ds-lib-list');const pHead=libraryHeader('Template library','Apply a template to the comparison you are configuring, or pick one on the Files & headers step.','Find a template');
 const cards=el('div','ds-template-grid');libraryCard.append(pHead.head,cards);layout.append(saveCard,libraryCard);
 let profiles=[];
 const syncSaveNote=()=>{const can=!!job&&['ready','complete'].includes(job.state);saveNote.textContent=can?`From: ${job.files?.left?.name||'current comparison'}`:'Load files and configure a comparison first, then save it here.';};
 function paintProfiles(){
  syncSaveNote();const q=pHead.input.value.trim().toLowerCase(),canApply=job?.state==='ready';
  pHead.count.textContent=`${profiles.length} saved`;cards.replaceChildren();
  if(!profiles.length){cards.append(empty('layout-template','No templates yet','Configure a comparison, then save it here to reuse its setup.'));return;}
  const shown=profiles.filter(p=>!q||p.name.toLowerCase().includes(q));
  if(!shown.length){cards.append(empty('search','No matching templates','Try a shorter search.'));return;}
  for(const profile of shown){
   const c=profile.config||{},card=el('article','ds-template-card');
   const top=el('div','ds-card-top');top.innerHTML=`<span class="ds-card-icon">${lucide('layout-template')}</span>`;top.append(el('h4','',profile.name));
   const facts=el('dl','ds-template-facts');
   const fact=(label,value)=>{facts.append(el('dt','',label),el('dd','',value));};
   fact('Keys',(c.keys||[]).join(', ')||'—');
   fact('Ignored columns',String((c.ignore_columns||[]).length));
   fact('Rules · overrides',`${(c.comparison_rules||[]).length} · ${(c.value_overrides||[]).length}`);
   const actions=el('div','ds-template-actions');
   const apply=el('button','primary','Apply');apply.type='button';apply.disabled=!canApply;apply.title=canApply?'Apply to the current comparison':'Load files first, then apply';
   apply.onclick=()=>{$('profileSelect').value=profile.id;$('profileSelect').dispatchEvent(new Event('change',{bubbles:true}));$('applyProfile').disabled=false;$('applyProfile').click();};
   const del=el('button','subtle');del.type='button';del.setAttribute('aria-label',`Delete ${profile.name}`);del.title='Delete template';del.append(lucideNode('trash-2'));
   del.onclick=()=>{$('profileSelect').value=profile.id;$('deleteProfile').click();};
   actions.append(apply,del);card.append(top,facts,actions);cards.append(card);
  }
 }
 pHead.input.addEventListener('input',paintProfiles);
 const previousLoad=loadProfiles;loadProfiles=async function(){await previousLoad();try{profiles=(await api('/api/profiles')).profiles||[];}catch(error){profiles=[];}paintProfiles();};
 api('/api/profiles').then(result=>{profiles=result.profiles||[];paintProfiles();}).catch(()=>paintProfiles());
}

// Keys & scope (phase 2): one title with a live summary of the match, three cards sized to the window whose
// lists scroll inside, numbered key order, container chips and a manual-key count. Every control keeps its
// id and handler; this only adds presentation and reads app.js state.
{
 const scopePanel=$('keysPanel');scopePanel.classList.add('ds','ds-scope');
 const titleBlock=scopePanel.querySelector('.section-title>div');titleBlock.replaceChildren();
 const summary=document.createElement('div');summary.className='ds-scope-summary';summary.setAttribute('aria-live','polite');titleBlock.append(summary);
 $('columnCount').hidden=true;
 const cards=[...scopePanel.querySelectorAll('.scope-grid>.scope-card')];
 const [keyCard,ignoreCard,excludeCard]=cards;
 const hints=['Rows are matched on these columns, in this order.','Left out of the value comparison.','Rows with these key values are skipped in both files.'];
 const titles=['Match keys','Ignore columns','Exclude keys'];
 cards.forEach((card,index)=>{
  const heading=card.querySelector('.scope-card-heading');heading.querySelector('.scope-card-index')?.remove();
  const h3=heading.querySelector('h3');h3.textContent=titles[index];
  const hint=document.createElement('p');hint.className='ds-card-hint';hint.textContent=hints[index];h3.after(hint);
  card.querySelector('.tu-scroll-rail')?.remove();
 });
 $('createContainerFromScope').innerHTML=lucide('plus')+'<span>New container</span>';
 // Chips for chosen containers and a count of manual keys.
 const chips=document.createElement('div');chips.className='ds-chip-row';excludeCard.querySelector('.ignore-key-section').append(chips);
 const manualCount=document.createElement('small');manualCount.className='ds-field-hint';$('ignoreKeys').after(manualCount);
 $('ignoreKeys').closest('label')?.firstChild&&($('ignoreKeys').closest('label').firstChild.textContent='Manual keys ');
 const countManual=()=>{const text=$('ignoreKeys').value.trim();if(!text)return 0;if(text.startsWith('['))try{return JSON.parse(text).length;}catch(error){return -1;}return text.split(',').map(v=>v.trim()).filter(Boolean).length;};
 function chip(text,kind){const node=document.createElement('span');node.className='ds-chip'+(kind?' is-'+kind:'');node.textContent=text;return node;}
 function syncScope(){
  if(typeof job==='undefined'||!job?.columns)return;
  const keys=[...selected],ignored=[...ignoredColumns],chosen=keyContainers.filter(item=>selectedContainers.has(item.id)),manual=countManual();
  // Numbered order on selected keys (composite keys match in this order).
  for(const label of $('keyList').querySelectorAll('.key-option')){const name=label.querySelector('span')?.textContent,position=keys.indexOf(name);label.classList.toggle('is-selected',position>=0);if(position>=0)label.dataset.order=String(position+1);else delete label.dataset.order;}
  for(const label of $('ignoreColumnList').querySelectorAll('.key-option')){const input=label.querySelector('input'),span=label.querySelector('span');label.classList.toggle('is-selected',!!input?.checked);const isKey=/ \(key\)$/.test(span?.textContent||'');label.classList.toggle('is-key',isKey);if(isKey&&!label.querySelector('.ds-key-tag')){span.textContent=span.textContent.replace(/ \(key\)$/,'');label.append(Object.assign(document.createElement('em'),{className:'ds-key-tag',textContent:'key'}));}}
  for(const label of $('keyList').querySelectorAll('.key-option')){const input=label.querySelector('input');label.classList.toggle('is-locked',!!input?.disabled&&!input.checked&&ignoredColumns.has(label.querySelector('span')?.textContent));}
  summary.replaceChildren();
  const block=(label,content)=>{const item=document.createElement('div');item.className='ds-summary-item';item.append(Object.assign(document.createElement('span'),{className:'ds-summary-label',textContent:label}),content);summary.append(item);};
  const keyLine=document.createElement('span');keyLine.className='ds-summary-value';
  if(keys.length)keys.forEach((key,i)=>{if(i)keyLine.append(Object.assign(document.createElement('span'),{className:'ds-summary-plus',textContent:'+'}));keyLine.append(chip(key,'key'));});
  else keyLine.append(Object.assign(document.createElement('span'),{className:'ds-summary-empty',textContent:'Choose at least one key'}));
  block('Match on',keyLine);
  block('Ignored',Object.assign(document.createElement('strong'),{textContent:`${ignored.length} column${ignored.length===1?'':'s'}`}));
  const excluded=chosen.length+(manual>0?1:0);
  block('Excluded keys',Object.assign(document.createElement('strong'),{textContent:excluded?`${chosen.length} container${chosen.length===1?'':'s'}${manual>0?` + ${manual} manual`:''}`:'None'}));
  block('Compared',Object.assign(document.createElement('strong'),{textContent:`${Math.max(0,job.columns.length-keys.length-ignored.length)} of ${job.columns.length} columns`}));
  chips.replaceChildren(...chosen.map(item=>chip(item.name,'container')));
  manualCount.textContent=manual<0?'Check the JSON format for composite keys.':manual?`${manual} key${manual===1?'':'s'} entered`:'Comma-separated values, or JSON tuples for composite keys.';
  manualCount.classList.toggle('is-error',manual<0);
 }
 // Coalesce bursts of changes into one update after the current task (no animation-frame dependency).
 let scopeQueued=false;const schedule=()=>{if(scopeQueued)return;scopeQueued=true;setTimeout(()=>{scopeQueued=false;syncScope();},0);};
 for(const name of ['renderKeys','renderIgnoredColumns','renderSelected','loadContainers']){const original=window[name];if(typeof original!=='function')continue;window[name]=function(...args){const result=original.apply(this,args);if(result&&typeof result.then==='function')result.then(schedule);else schedule();return result;};}
 scopePanel.addEventListener('change',schedule);scopePanel.addEventListener('input',schedule);
 new MutationObserver(schedule).observe($('containerChoices'),{subtree:true,attributes:true,attributeFilter:['checked']});
 // Preview opens between the summary and the cards (the earlier panel() hook appends it at the end).
 const placePreview=()=>{const preview=scopePanel.querySelector('.inline-preview');if(preview)scopePanel.querySelector('.scope-grid').before(preview);};
 const previousPanel=window.panel;
 window.panel=function(name){const result=previousPanel.apply(this,arguments);if(name==='keysPanel'){placePreview();schedule();}return result;};
 placePreview();schedule();
}

// Comparison rules drawer: design-system presentation, icon close button and numbered build steps.
{
 const drawer=$('comparisonRulesDrawer');drawer.classList.add('ds','ds-drawer');
 drawer.querySelector('.drawer-heading .eyebrow')?.remove();
 const title=$('comparisonRulesTitle'),sub=document.createElement('p');sub.className='ds-drawer-sub';sub.textContent='Normalise values before they are compared. Keys and ignored columns are excluded.';title.after(sub);
 const close=$('closeComparisonRules');close.className='ds-icon-btn';close.setAttribute('aria-label','Close comparison rules');close.innerHTML=lucide('x');
 drawer.querySelector('.comparison-rules-body>p.muted')?.remove();
 const builder=drawer.querySelector('.comparison-rule-builder');
 const step=(n,text)=>{const h=document.createElement('div');h.className='ds-step-head';h.innerHTML=`<span>${n}</span><strong></strong>`;h.querySelector('strong').textContent=text;return h;};
 builder.prepend(step(1,'Choose columns'));
 builder.querySelector('.rule-options')?.before(step(2,'Choose how values are normalised'));
 $('addValueRule').className='primary';$('addValueRule').prepend(lucideNode('plus'));
}

// Delimited text files: each header card shows the delimiter, encoding and header row used for that file,
// detected or chosen, and can change them before the comparison starts. Header-less extracts can take the
// other file's column names in one step.
{
 const delimiterNames={',':'Comma (,)','|':'Pipe (|)','\t':'Tab',';':'Semicolon (;)','\x01':'Ctrl-A (\\x01)','\x1f':'Unit separator','~':'Tilde (~)','^':'Caret (^)'};
 const encodingNames={'utf-8-sig':'UTF-8','utf-16':'UTF-16','cp1252':'Windows-1252'};
 const fileFormat=side=>job?.files?.[side]?.format||'csv';
 function dialectEditor(side){
  const form=document.createElement('form');form.className='ds-dialect-editor';form.hidden=true;
  const option=(value,label)=>Object.assign(document.createElement('option'),{value,textContent:label});
  const field=(label,select)=>{const wrap=document.createElement('label');wrap.append(label,select);return wrap;};
  const delimiter=document.createElement('select');delimiter.append(...Object.entries(delimiterNames).map(([value,label])=>option(value,label)),option('custom','Custom…'),option('fixed','Fixed width…'));
  const encoding=document.createElement('select');encoding.append(...Object.entries(encodingNames).map(([value,label])=>option(value,label)));
  const header=document.createElement('select');header.append(option('yes','Header'),option('no','Data'));
  const custom=Object.assign(document.createElement('input'),{maxLength:1,autocomplete:'off',spellcheck:false,placeholder:'^',className:'ds-dialect-custom'});custom.setAttribute('aria-label','Custom delimiter character');
  const actions=document.createElement('div');actions.className='ds-dialect-actions';
  const cancel=Object.assign(document.createElement('button'),{type:'button',className:'subtle',textContent:'Cancel'});
  const apply=Object.assign(document.createElement('button'),{type:'submit',className:'primary',textContent:'Apply'});
  const status=document.createElement('p');status.className='ds-dialect-status';status.setAttribute('role','status');
  actions.append(status,cancel,apply);
  const delimiterField=field('Delimiter',delimiter);delimiterField.append(custom);
  // Fixed width: a layout from column positions or a COBOL copybook; the file is converted once on Apply.
  const fixedBox=document.createElement('div');fixedBox.className='ds-fixed';fixedBox.hidden=true;
  fixedBox.innerHTML='<label><span>Layout</span><select class="ds-fixed-format"><option value="positions">Column positions</option><option value="copybook">COBOL copybook</option></select></label><label><span>Encoding</span><select class="ds-fixed-encoding"><option value="utf-8">UTF-8</option><option value="cp1252">Windows-1252</option><option value="latin-1">Latin-1</option><option value="cp037">EBCDIC (cp037)</option></select></label><label><span>Record length</span><input class="ds-fixed-length" type="number" min="0" value="0" inputmode="numeric" title="0 means one record per line"></label><label class="ds-fixed-wide"><span class="ds-fixed-hint"></span><textarea class="ds-fixed-layout" rows="4" spellcheck="false"></textarea></label><label class="ds-fixed-check"><input type="checkbox" class="ds-fixed-trim" checked><span>Trim padding spaces</span></label>';
  const fixedFormat=fixedBox.querySelector('.ds-fixed-format'),fixedLayout=fixedBox.querySelector('.ds-fixed-layout');
  const syncFixedFormat=()=>{const copybook=fixedFormat.value==='copybook';fixedBox.querySelector('.ds-fixed-hint').textContent=copybook?'Paste the copybook (DISPLAY fields; FILLER is skipped)':'One field per line: name,start,length (start at 1)';fixedLayout.placeholder=copybook?'01  ACCOUNT-RECORD.\n    05  ACCT-ID    PIC X(10).\n    05  BALANCE    PIC S9(9)V99.':'account_id,1,10\nname,11,30\nbalance,41,12';};
  fixedFormat.addEventListener('change',syncFixedFormat);syncFixedFormat();
  const encodingField=field('Encoding',encoding),headerField=field('First row',header);
  form.append(delimiterField,encodingField,headerField,fixedBox,actions);
  const syncCustom=()=>{custom.hidden=delimiter.value!=='custom';const fixed=delimiter.value==='fixed';fixedBox.hidden=!fixed;encodingField.hidden=headerField.hidden=fixed;form.classList.toggle('is-fixed',fixed);};
  delimiter.addEventListener('change',()=>{syncCustom();if(!custom.hidden)custom.focus();});
  form.load=chosen=>{
   const known=chosen.delimiter in delimiterNames;delimiter.value=chosen.fixed?'fixed':known?chosen.delimiter:'custom';custom.value=known?'':chosen.delimiter;
   if(chosen.fixed){fixedFormat.value=chosen.fixed.format;fixedLayout.value=chosen.fixed.layout||'';fixedBox.querySelector('.ds-fixed-encoding').value=chosen.fixed.encoding;fixedBox.querySelector('.ds-fixed-length').value=chosen.fixed.record_length||0;fixedBox.querySelector('.ds-fixed-trim').checked=chosen.fixed.trim!==false;for(const select of fixedBox.querySelectorAll('select'))select.dispatchEvent(new Event('change'));}
   encoding.value=chosen.encoding in encodingNames?chosen.encoding:'utf-8-sig';header.value=chosen.header===false?'no':'yes';status.textContent='';syncCustom();
   for(const select of [delimiter,encoding,header])select.dispatchEvent(new Event('change'));
  };
  cancel.addEventListener('click',()=>setDialectEditing(side,false));
  form.addEventListener('keydown',event=>{if(event.key==='Escape'&&!openSelectWidget){event.preventDefault();setDialectEditing(side,false);}});
  form.addEventListener('submit',async event=>{
   event.preventDefault();
   if(delimiter.value==='fixed'){
    if(!fixedLayout.value.trim()){status.textContent='Paste the layout first.';fixedLayout.focus();return;}
    apply.disabled=true;status.textContent='Converting…';status.classList.remove('is-error');
    try{
     job=await api(endpoint('/fixed-width'),{side,format:fixedFormat.value,layout:fixedLayout.value,encoding:fixedBox.querySelector('.ds-fixed-encoding').value,record_length:Number(fixedBox.querySelector('.ds-fixed-length').value)||0,trim:fixedBox.querySelector('.ds-fixed-trim').checked});
     // The conversion runs in the background; follow it here until the file is ready.
     while(job.state==='preparing'){status.textContent=job.preparation_message||'Converting…';await new Promise(resolve=>setTimeout(resolve,700));job=await api(endpoint());}
     if(job.fixed_width_error?.side===side)throw Error(job.fixed_width_error.message);
     headerDraftJob=null;hydratedId=null;$('headerChangesTable').dataset.job='';
     setDialectEditing(side,false);hydrate();renderHeaders();syncNavigation();
     if(typeof notify==='function')notify('success','Fixed-width file converted',job.preparation_message||'');
    }catch(error){status.textContent=error.message;status.classList.add('is-error');}
    finally{apply.disabled=false;}
    return;
   }
   const value=delimiter.value==='custom'?custom.value:delimiter.value;
   if([...value].length!==1||['\r','\n','\0','"'].includes(value)){status.textContent='Enter one delimiter character. Line breaks, null and double quotes are not supported.';custom.focus();return;}
   apply.disabled=true;status.textContent='Reading column names…';status.classList.remove('is-error');
   const other=side==='left'?'right':'left',kept=headerDirty&&headerDraftJob===job.id?headerDraft[other]:null;
   try{
    job=await api(endpoint('/dialect'),{side,delimiter:value,encoding:encoding.value,header:header.value==='yes'});
    headerDraftJob=null;hydratedId=null;$('headerChangesTable').dataset.job='';
    setDialectEditing(side,false);hydrate();renderHeaders();
    // Unsaved renames on the other file survive a dialect change on this one.
    if(kept&&kept.length===headerDraft[other].length){headerDraft[other]=kept;headerDirty=true;renderHeaders();}
    syncNavigation();
   }catch(error){status.textContent=error.message;status.classList.add('is-error');}
   finally{apply.disabled=false;}
  });
  form.openFixed=()=>{delimiter.value='fixed';delimiter.dispatchEvent(new Event('change'));fixedLayout.focus();};
  return form;
 }
 const parts={};
 for(const side of ['left','right']){
  const section=$(side==='left'?'headerLeftList':'headerRightList').closest('section');
  const strip=document.createElement('div');strip.className='ds-dialect';
  strip.innerHTML=`<span class="ds-dialect-icon">${lucide('file-text')}</span><span class="ds-dialect-summary"></span><span class="ds-dialect-badge">${lucide('sparkle')}Detected</span><button type="button" class="ds-dialect-change">${lucide('pencil')}<span>Change</span></button>`;
  const hint=document.createElement('div');hint.className='ds-dialect-hint';hint.hidden=true;
  hint.innerHTML=`${lucide('info')}<span class="ds-dialect-hint-text"></span><button type="button" class="ds-dialect-borrow"></button>`;
  const editor=dialectEditor(side);
  section.querySelector('h3').after(strip,editor,hint);
  for(const select of editor.querySelectorAll('select'))enhanceSelect(select);
  strip.querySelector('.ds-dialect-change').addEventListener('click',()=>setDialectEditing(side,editor.hidden));
  hint.querySelector('.ds-dialect-borrow').addEventListener('click',()=>{
   if(hint.dataset.mode==='fixed'){setDialectEditing(side,true);editor.openFixed();return;}
   const other=side==='left'?'right':'left';headerDraft[side]=[...headerDraft[other]];headerDirty=true;headerPage=0;renderHeaders();
  });
  parts[side]={section,strip,hint,editor};
 }
 function setDialectEditing(side,open){
  const {section,strip,editor}=parts[side];
  if(open)editor.load(job.dialects?.[side]||{delimiter:job.delimiter,encoding:job.encoding,header:true});
  editor.hidden=!open;section.classList.toggle('is-editing-dialect',open);
  strip.querySelector('.ds-dialect-change').setAttribute('aria-expanded',String(open));
  if(open)editor.querySelector('.tu-select-trigger')?.focus();else strip.querySelector('.ds-dialect-change').focus({preventScroll:true});
 }
 function renderDialects(){
  if(!job?.files)return;
  for(const side of ['left','right']){
   const {strip,hint,editor}=parts[side],format=fileFormat(side),chosen=job.dialects?.[side],text=format==='csv'&&chosen;
   const other=side==='left'?'right':'left',otherNumber=side==='left'?2:1;
   strip.querySelector('.ds-dialect-summary').textContent=format==='excel'?`Excel worksheet · ${job.files[side].sheet||''}`:format==='parquet'?'Parquet · typed columns':
    chosen?.fixed?`Fixed width · ${number(chosen.fixed.fields)} fields · ${number(chosen.fixed.records)} records · ${({'utf-8':'UTF-8',cp1252:'Windows-1252','latin-1':'Latin-1',cp037:'EBCDIC'})[chosen.fixed.encoding]||chosen.fixed.encoding}`:
    chosen?`${delimiterNames[chosen.delimiter]||`Custom (${chosen.delimiter})`} · ${encodingNames[chosen.encoding]||chosen.encoding} · ${chosen.header===false?'No header row':'Header row'}`:'Delimited text';
   strip.querySelector('.ds-dialect-badge').hidden=!(text&&chosen.detected?.length);
   strip.querySelector('.ds-dialect-badge').title=text&&chosen.detected?.length?'Detected from the first 1 MB: '+chosen.detected.join(', '):'';
   strip.querySelector('.ds-dialect-change').hidden=!text||job.state!=='ready';
   if(!text||job.state!=='ready')editor.hidden=true;
   const headerless=text&&chosen.header===false&&!chosen.fixed,sameWidth=headerDraft?.[side]?.length===headerDraft?.[other]?.length;
   const borrowed=headerless&&sameWidth&&headerDraft[side].every((name,index)=>name===headerDraft[other][index]);
   // A .dat or .txt file read as a single column is probably fixed width.
   const singleColumn=text&&!chosen.fixed&&/\.(dat|txt)$/i.test(job.files[side].name)&&(job.source_headers?.[side]?.length===1);
   hint.dataset.mode=singleColumn?'fixed':'borrow';
   hint.hidden=!(singleColumn||(headerless&&!borrowed));
   hint.querySelector('.ds-dialect-hint-text').textContent=singleColumn?'Read as a single column. If this is a fixed-width file, define its layout.':sameWidth?'No header row, so columns are numbered.':`No header row, so columns are numbered. Rename them to match File ${otherNumber}.`;
   const borrow=hint.querySelector('.ds-dialect-borrow');borrow.hidden=job.state!=='ready'||(!singleColumn&&!sameWidth);borrow.textContent=singleColumn?'Define layout':`Use File ${otherNumber} names`;
  }
 }
 const renderHeadersWithoutDialects=renderHeaders;
 renderHeaders=function(){renderHeadersWithoutDialects();renderDialects();};
 if(job)renderDialects();
}

// Column headers fill the window on desktop sizes: the header cards, and the preview when it is open,
// share exactly the height left below everything else, so the page itself does not scroll.
{
 const panel=$('headersPanel'),editors=panel.querySelector('.header-editors'),main=$('mainWorkspace');
 const narrow=matchMedia('(max-width:800px)');
 let frame=0;
 function fitHeaderCards(){
  frame=0;
  if(panel.hidden||!editors.offsetParent)return;
  const preview=panel.querySelector('.inline-preview');
  if(narrow.matches){
   // Stacked cards: remember their height with the preview closed; when it opens each card halves
   // and the preview takes exactly the height they gave up.
   panel.style.removeProperty('--ds-headers-space');
   if(!preview?.classList.contains('is-open'))panel.style.setProperty('--ds-headers-stacked',Math.round(editors.getBoundingClientRect().height)+'px');
   return;
  }
  // The main area has a minimum height, so the content bottom is its last in-flow child plus spacing.
  const below=child=>{const style=getComputedStyle(child);return style.display==='none'||style.position==='fixed'||style.position==='absolute'?0:child.getBoundingClientRect().bottom+parseFloat(style.marginBottom);};
  const contentBottom=Math.max(...[...main.children].map(below))+parseFloat(getComputedStyle(main).paddingBottom);
  const flexible=editors.getBoundingClientRect().height+(preview?.getBoundingClientRect().height||0);
  const space=Math.round(Math.min(720,Math.max(260,innerHeight-(contentBottom+scrollY-flexible))));
  panel.style.setProperty('--ds-headers-space',space+'px');
 }
 // A timer rather than an animation frame: it also runs in background tabs, after layout settles.
 const schedule=()=>{if(!frame)frame=setTimeout(fitHeaderCards,16);};
 addEventListener('resize',schedule);narrow.addEventListener?.('change',schedule);
 // Re-measure when anything above or below the cards changes height (status banners, the source bar).
 const observer=new ResizeObserver(schedule);
 for(const element of [main,$('uploadPanel'),panel.querySelector('.section-title'),$('headerLayoutStatus'),panel.querySelector('.list-navigation')])if(element)observer.observe(element);
 const renderHeadersBeforeFit=renderHeaders;renderHeaders=function(){renderHeadersBeforeFit();schedule();};
 schedule();
}

// Comparison rules drawer, two panes: rules are built on the left while the configured rules stay visible
// on the right. Each pane scrolls on its own, so nothing important falls below the fold.
{
 const drawer=$('comparisonRulesDrawer'),body=drawer.querySelector('.comparison-rules-body'),builder=drawer.querySelector('.comparison-rule-builder');
 const panes=document.createElement('div');panes.className='ds-rules-panes';
 const configured=document.createElement('section');configured.className='ds-rules-configured';configured.setAttribute('aria-label','Configured rules');
 builder.before(panes);panes.append(builder,configured);
 configured.append(body.querySelector(':scope>.section-title'),$('configuredRuleSearch'),$('ruleEditorEmpty'),$('comparisonRuleList'));
 $('ruleEditorEmpty').textContent='No rules yet. Values are compared as exact text.';
 // The add button says how many columns it will change.
 const add=$('addValueRule'),addText=document.createElement('span');
 for(const node of [...add.childNodes])if(node.nodeType===Node.TEXT_NODE)node.remove();
 add.append(addText);
 function syncAddLabel(){
  const all=$('ruleTargetScope').value==='all',count=all?eligibleRuleColumns().length:$('ruleColumnChoices').querySelectorAll('input:checked').length;
  addText.textContent=all?`Add rules to all ${count} columns`:count?`Add rules to ${count} column${count===1?'':'s'}`:'Add rules to selection';
 }
 const renderChoicesBeforeLabel=renderRuleColumnChoices;
 renderRuleColumnChoices=function(){renderChoicesBeforeLabel();syncAddLabel();};
 $('ruleTargetScope').addEventListener('change',syncAddLabel);
 // After rules are added the column selection clears too, ready for the next set of columns.
 const clearBuilderBeforeColumns=clearRuleBuilder;
 clearRuleBuilder=function(){clearBuilderBeforeColumns();for(const input of $('ruleColumnChoices').querySelectorAll('input:checked'))input.checked=false;renderRuleColumnChoices();};
 const cancel=Object.assign(document.createElement('button'),{type:'button',className:'subtle',textContent:'Cancel'});
 cancel.addEventListener('click',()=>drawer.close());$('applyComparisonRules').before(cancel);
}

// Mismatch review drawer: a wide data drawer. One toolbar row, a table that fills the height with compact
// rows and icon actions, and comments in a collapsible footer, so most mismatches are visible at once.
{
 const drawer=$('analysisDrawer'),panel=$('analysisPanel');
 if(drawer&&panel&&$('analysisFilterDisclosure')){
  drawer.classList.add('ds','ds-mismatch');
  const close=$('closeAnalysisDrawer');close.className='ds-icon-btn';close.innerHTML=lucide('x');
  drawer.querySelector('.analysis-drawer-eyebrow')?.remove();
  const tabs=panel.querySelector('.analysis-tabs'),filter=$('analysisFilterDisclosure'),back=$('analysisReset');
  const toolbar=document.createElement('div');toolbar.className='ds-mismatch-toolbar';tabs.before(toolbar);
  // The selection line repeats the title, except when one key is open: then it names that key.
  const selection=$('analysisSelection'),where=document.createElement('div');where.className='ds-mismatch-where';
  back.classList.add('ds-toolbar-btn','ds-back-btn');
  toolbar.append(tabs,where,back);where.append(selection);
  const syncWhere=()=>{selection.hidden=!$('analysisKey').value;};
  new MutationObserver(syncWhere).observe(selection,{childList:true,characterData:true,subtree:true});syncWhere();
  // Keys is a third view beside Mismatches and Patterns. Its key search shows only in that view.
  const byKey=$('analysisByKey');byKey.textContent='Keys';$('analysisByPattern').before(byKey);
  for(const view of [$('analysisByColumn'),$('analysisByPattern')])view.addEventListener('click',()=>{filter.open=false;});
  // "What is included in this view?" is not shown; its elements stay for the code that fills them.
  panel.querySelector('.analysis-help').hidden=true;
  // Row actions become labelled icon buttons; a single-column view drops its repeated Column column.
  const table=$('analysisTable');
  const icons={'All changes for this key':'scan-search','Comment':'message-square-text','View mismatching keys':'chevron-right','View this key':'chevron-right'};
  function enhanceTable(){
   const rows=[...table.rows];if(!rows.length)return;
   const head=[...rows[0].cells],columnIndex=head.findIndex(cell=>cell.textContent==='Column');
   const single=columnIndex>=0&&$('analysisMode').value==='columns'&&!!$('analysisColumn').value;
   for(const row of rows)if(row.cells[columnIndex])row.cells[columnIndex].hidden=single;
   head.at(-1)?.classList.add('ds-actions-head');
   for(const row of rows.slice(1)){
    row.cells[row.cells.length-1]?.classList.add('ds-actions-cell');
    for(const cell of row.cells)if(!cell.title&&cell.textContent.length>40)cell.title=cell.textContent;
   }
   for(const button of table.querySelectorAll('td button:not([data-ds-icon])')){
    const label=button.textContent.trim(),icon=icons[label];if(!icon)continue;
    button.dataset.dsIcon='1';button.classList.add('ds-row-action');button.title=label;button.setAttribute('aria-label',label);
    if(icon==='chevron-right'){button.classList.add('has-text');button.replaceChildren(Object.assign(document.createElement('span'),{textContent:'Open'}),lucideNode(icon));}
    else button.replaceChildren(lucideNode(icon));
   }
  }
  new MutationObserver(enhanceTable).observe(table,{childList:true});
  // Loader: while the disk index is prepared, or a page takes more than a moment, the table area shows
  // progress (cells indexed, percent and time left) instead of an empty table.
  const results=panel.querySelector('.analysis-results'),status=$('analysisStatus');
  const loader=document.createElement('div');loader.className='ds-analysis-loader';loader.hidden=true;loader.setAttribute('role','status');loader.setAttribute('aria-live','polite');
  loader.innerHTML='<img src="/assets/transunion-loader.svg" alt="" width="64" height="64"><strong class="ds-analysis-loader-title"></strong><span class="ds-analysis-loader-detail"></span><div class="ds-analysis-progress" hidden><i></i></div><span class="ds-analysis-loader-eta"></span>';
  results.querySelector('.table-scroll').before(loader);loader.append($('cancelAnalysis'));
  const bar=loader.querySelector('.ds-analysis-progress'),fill=bar.querySelector('i');
  let progress=null,samples=[],showTimer=0;
  analysisProgress=result=>{
   progress=result&&result.state!=='complete'?result:null;
   if(progress&&Number.isFinite(progress.done)&&progress.total)samples=[...samples.filter(s=>s.done<=progress.done).slice(-6),{done:progress.done,at:performance.now()}];
   paint();
  };
  function paint(){
   const preparing=!!progress;
   loader.querySelector('.ds-analysis-loader-title').textContent=preparing?'Preparing mismatch analysis':'Loading mismatches';
   loader.querySelector('.ds-analysis-loader-detail').textContent=preparing?(progress.message||'Preparing…'):'Fetching this page of results…';
   const total=progress?.total,done=progress?.done,known=preparing&&total>0&&Number.isFinite(done);
   bar.hidden=!known;let eta='';
   if(known){
    const share=Math.min(1,done/total);fill.style.width=(share*100).toFixed(1)+'%';
    const first=samples[0],last=samples.at(-1),rate=first&&last&&last.at>first.at?(last.done-first.done)/((last.at-first.at)/1000):0;
    const left=rate>0?(total-done)/rate:0;
    eta=`${Math.floor(share*100)}% indexed`+(left>5?` · about ${left<90?Math.round(left)+' s':Math.round(left/60)+' min'} left`:'');
   }
   loader.querySelector('.ds-analysis-loader-eta').textContent=eta;
  }
  function syncLoader(){
   const loading=status.classList.contains('loading-status');
   if(!loading){clearTimeout(showTimer);showTimer=0;progress=null;samples=[];loader.hidden=true;results.classList.remove('is-loading');return;}
   paint();
   if(progress){clearTimeout(showTimer);showTimer=0;loader.hidden=false;results.classList.add('is-loading');}
   else if(loader.hidden&&!showTimer)showTimer=setTimeout(()=>{showTimer=0;if(status.classList.contains('loading-status')){loader.hidden=false;results.classList.add('is-loading');}},250);
  }
  new MutationObserver(syncLoader).observe(status,{attributes:true,attributeFilter:['class'],childList:true,characterData:true,subtree:true});
  // The index starts building as soon as the Analysis page opens, so the drawer is usually ready.
  const renderSummaryBeforeIndex=renderSummary;
  renderSummary=function(){
   renderSummaryBeforeIndex();
   if(job?.state==='complete'&&job.summary?.changed_cells>0&&(!job.analysis||job.analysis.state==='not_started'))api(endpoint('/analysis'),{}).then(state=>{if(job)job.analysis=state;}).catch(()=>{});
  };
  // Comments & findings: a collapsible footer that shows how many comments are saved.
  const comments=panel.querySelector('.review-comments'),summary=comments.querySelector('summary');
  const badge=Object.assign(document.createElement('span'),{className:'ds-count-badge'});
  summary.replaceChildren(lucideNode('message-square-text'),Object.assign(document.createElement('span'),{textContent:'Comments & findings'}),badge,lucideNode('chevron-down','ds-summary-chevron'));
  const syncCount=()=>{const count=(typeof analysisNotesCache!=='undefined'&&analysisNotesCache?.length)||0;badge.textContent=count?`${count} saved`:'';badge.hidden=!count;};
  const loadNotesBeforeCount=loadNotes;loadNotes=async function(){await loadNotesBeforeCount();syncCount();};syncCount();
  comments.open=false;
  // The footer slides up when opened (by its header or a row's Comment button) and back down when closed.
  const nativeOpen=Object.getOwnPropertyDescriptor(HTMLDetailsElement.prototype,'open'),reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
  // A gentle ease-in-out: slow start, smooth middle, soft landing.
  const easing='cubic-bezier(.45,0,.2,1)';let slide=null;
  function slideComments(opening){
   const from=comments.getBoundingClientRect().height,borders=comments.offsetHeight-comments.clientHeight;
   slide?.cancel();comments.classList.toggle('is-expanded',opening);comments.classList.add('is-sliding');
   if(opening)nativeOpen.set.call(comments,true);
   const to=opening?comments.getBoundingClientRect().height:summary.offsetHeight+borders,duration=reducedMotion.matches?0:opening?720:560;
   slide=comments.animate([{height:from+'px'},{height:to+'px'}],{duration,easing});
   if(opening)for(const part of comments.children)if(part!==summary)part.animate([{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'none'}],{duration,delay:reducedMotion.matches?0:120,easing,fill:'backwards'});
   const finish=()=>{slide=null;comments.classList.remove('is-sliding');};
   slide.onfinish=()=>{finish();if(!opening)nativeOpen.set.call(comments,false);};slide.oncancel=finish;
  }
  summary.addEventListener('click',event=>{event.preventDefault();slideComments(!comments.classList.contains('is-expanded'));});
  // app.js opens the footer by setting .open; route that through the same slide.
  Object.defineProperty(comments,'open',{configurable:true,get(){return nativeOpen.get.call(this);},set(value){
   if(value&&!comments.classList.contains('is-expanded'))slideComments(true);
   else if(!value){slide?.cancel();comments.classList.remove('is-expanded');nativeOpen.set.call(this,false);}
  }});
 }
}

// Attach to project: a popover anchored to its button instead of a full drawer. One click files the
// comparison (its audit status, notes and reviewer are kept), a project can be created inline, and the
// button shows the project the comparison is filed under. Projects keeps the full audit drawer.
{
 const wraps=[];
 function setupAttach(button,identityOf){
  if(!button||button.dataset.attachTrigger)return;
  button.dataset.attachTrigger='1';
  const wrap=document.createElement('span');wrap.className='ds-attach';button.before(wrap);wrap.append(button);
  button.classList.add('ds-attach-btn');button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-expanded','false');
  const label=Object.assign(document.createElement('span'),{className:'ds-attach-label',textContent:'Attach to project'});
  button.replaceChildren(lucideNode('folder'),label,lucideNode('chevron-down','ds-attach-chevron'));
  const popover=document.createElement('div');popover.className='ds-attach-popover';popover.hidden=true;popover.setAttribute('role','dialog');popover.setAttribute('aria-label','Attach to project');
  wrap.append(popover);wraps.push({wrap,button,label,popover,identityOf});
 }
 function showLabel(entry,name){
  entry.label.textContent=name||'Attach to project';entry.wrap.classList.toggle('is-attached',!!name);
  entry.button.title=name?`Filed in ${name}. Click to change.`:'';
 }
 function close(entry,focus=false){if(entry.popover.hidden)return;entry.popover.hidden=true;entry.button.setAttribute('aria-expanded','false');if(focus)entry.button.focus();}
 async function render(entry,identity){
  const [recordData,catalog]=await Promise.all([api('/api/projects/records?id='+identity),api('/api/projects')]);
  const record=recordData.records[0];if(!record)throw Error('Comparison is not available.');
  const projects=catalog.projects||[],popover=entry.popover;popover.replaceChildren();
  const head=document.createElement('div');head.className='ds-attach-head';
  head.innerHTML='<strong>Attach to project</strong><span>Group this comparison with related runs. Its audit status and notes are kept.</span>';
  const search=Object.assign(document.createElement('input'),{type:'search',placeholder:'Find a project',className:'ds-attach-search'});search.setAttribute('aria-label','Find a project');search.hidden=projects.length<7;
  const list=document.createElement('div');list.className='ds-attach-list';list.setAttribute('role','listbox');list.setAttribute('aria-label','Projects');
  const status=Object.assign(document.createElement('p'),{className:'ds-attach-status'});status.setAttribute('role','status');
  const nameOf=id=>projects.find(project=>project.id===id)?.name||'';
  async function attach(projectId,name){
   status.textContent='Saving…';status.dataset.tone='';
   try{
    await api('/api/projects/records',{comparison_id:identity,project_id:projectId||null,status:record.audit_status,note:record.audit_note,reviewer:record.reviewer});
    record.project_id=projectId||null;showLabel(entry,name);
    for(const option of list.children)option.setAttribute('aria-selected',String(option.dataset.id===record.project_id));
    status.textContent=projectId?`Filed in ${name}.`:'Removed from project.';status.dataset.tone='success';
    setTimeout(()=>close(entry,true),650);
   }catch(error){status.textContent=error.message;status.dataset.tone='error';}
  }
  for(const project of projects){
   const option=document.createElement('button');option.type='button';option.className='ds-attach-option';option.dataset.id=project.id;
   option.setAttribute('role','option');option.setAttribute('aria-selected',String(project.id===record.project_id));
   const name=Object.assign(document.createElement('span'),{className:'ds-attach-name',textContent:project.name});name.title=project.name;
   option.append(lucideNode('folder'),name,lucideNode('check','ds-attach-check'));
   option.addEventListener('click',()=>{if(project.id!==record.project_id)attach(project.id,project.name);else close(entry,true);});
   list.append(option);
  }
  const empty=Object.assign(document.createElement('p'),{className:'ds-attach-empty',textContent:'No projects yet. Create the first one below.'});empty.hidden=!!projects.length;
  search.addEventListener('input',()=>{const q=search.value.toLowerCase();for(const option of list.children)option.hidden=!option.textContent.toLowerCase().includes(q);});
  list.addEventListener('keydown',event=>{
   if(!['ArrowDown','ArrowUp'].includes(event.key))return;event.preventDefault();
   const options=[...list.children].filter(option=>!option.hidden),index=options.indexOf(document.activeElement);
   options[(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length]?.focus();
  });
  // New project: created and attached in one step.
  const create=document.createElement('form');create.className='ds-attach-create';
  const nameInput=Object.assign(document.createElement('input'),{placeholder:'New project name',maxLength:100});nameInput.setAttribute('aria-label','New project name');
  const createButton=Object.assign(document.createElement('button'),{type:'submit',className:'subtle'});createButton.append(lucideNode('plus'),Object.assign(document.createElement('span'),{textContent:'Create'}));
  create.append(nameInput,createButton);
  create.addEventListener('submit',async event=>{
   event.preventDefault();const name=nameInput.value.trim();if(!name){nameInput.focus();return;}
   createButton.disabled=true;
   try{const created=await api('/api/projects',{name,description:''});await attach(created.id,name);}
   catch(error){status.textContent=error.message;status.dataset.tone='error';}
   finally{createButton.disabled=false;}
  });
  const remove=document.createElement('button');remove.type='button';remove.className='ds-attach-remove';remove.textContent='Remove from project';remove.hidden=!record.project_id;
  remove.addEventListener('click',()=>attach(null,''));
  popover.append(head,search,list,empty,create,remove,status);
  showLabel(entry,nameOf(record.project_id));
  return {search,list,nameInput,projects};
 }
 async function open(entry,identity){
  for(const other of wraps)if(other!==entry)close(other);
  $('reportsMenu')&&($('reportsMenu').open=false);
  const parts=await render(entry,identity);
  // Right-aligned under the button, unless that would push it off the left edge of the window.
  entry.popover.classList.toggle('align-left',entry.wrap.getBoundingClientRect().right<Math.min(340,innerWidth-32)+16);
  entry.popover.hidden=false;entry.button.setAttribute('aria-expanded','true');
  (parts.projects.length?(parts.search.hidden?parts.list.querySelector('[aria-selected=true]')||parts.list.firstElementChild:parts.search):parts.nameInput)?.focus();
 }
 const auditBeforePopover=openProjectAudit;
 openProjectAudit=async function(identity,attach=false){
  const entry=currentView==='projectsPanel'?null:wraps.find(item=>item.wrap.offsetParent&&item.identityOf()===identity);
  if(!entry)return auditBeforePopover(identity,attach);
  if(!entry.popover.hidden){close(entry,true);return;}
  await open(entry,identity);
 };
 document.addEventListener('pointerdown',event=>{for(const entry of wraps)if(!entry.wrap.contains(event.target))close(entry);});
 document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;for(const entry of wraps)if(!entry.popover.hidden){event.stopPropagation();close(entry,true);}},true);
 setupAttach([...$('results').querySelectorAll('button')].find(button=>/Attach to project/.test(button.textContent)),()=>job?.id);
 setupAttach([...($('jsonResult')?.querySelectorAll('button')||[])].find(button=>/Project & audit/.test(button.textContent)),()=>typeof jsonResult!=='undefined'?jsonResult?.audit_id:null);
 // The results button shows where this comparison is filed.
 async function syncResultsLabel(){
  const entry=wraps[0];if(!entry||!job?.id||job.state!=='complete')return;const identity=job.id;
  try{
   const [recordData,catalog]=await Promise.all([api('/api/projects/records?id='+identity),api('/api/projects')]);
   if(job?.id!==identity)return;const projectId=recordData.records[0]?.project_id;
   showLabel(entry,(catalog.projects||[]).find(project=>project.id===projectId)?.name||'');
  }catch{}
 }
 const renderSummaryBeforeAttach=renderSummary;renderSummary=function(){renderSummaryBeforeAttach();syncResultsLabel();};
 syncResultsLabel();
}

// Refresh beside Help: reloads the page content only; the sidebar stays in place (soft reload). On hover the
// button widens to the left and its label slides in beside the icon.
{
 const help=$('workspaceHelp');
 if(help){
  const refresh=document.createElement('button');refresh.type='button';refresh.id='softRefresh';refresh.className='ds-topbar-refresh';refresh.setAttribute('aria-label','Refresh');
  refresh.append(lucideNode('refresh-cw'),Object.assign(document.createElement('span'),{className:'ds-refresh-label',textContent:'Refresh'}));
  refresh.addEventListener('click',()=>{refresh.classList.add('is-spinning');softReload();});
  help.before(refresh);
 }
}

// Notifications: every message appears as a toast and is saved in this browser (latest 200), so the
// bell's dropdown in the top bar lists what happened even after a toast has gone. Errors stay until dismissed.
{
 const KEY='ds-notifications',LIMIT=200;
 const icons={error:'circle-alert',success:'circle-check',warning:'triangle-alert',info:'info'};
 const load=()=>{try{const list=JSON.parse(localStorage.getItem(KEY)||'[]');return Array.isArray(list)?list:[];}catch{return [];}};
 const save=list=>{try{localStorage.setItem(KEY,JSON.stringify(list.slice(0,LIMIT)));}catch{}};
 const stack=document.createElement('div');stack.className='ds-toasts';document.body.append(stack);
 function syncBadge(){const unread=load().filter(item=>!item.read).length,badge=$('notificationBadge');if(!badge)return;badge.textContent=unread>99?'99+':String(unread);badge.hidden=!unread;}
 function toast(item){
  const el=document.createElement('div');el.className='ds-toast is-'+item.kind;el.setAttribute('role',item.kind==='error'?'alert':'status');
  const copy=document.createElement('div');copy.className='ds-toast-copy';
  copy.append(Object.assign(document.createElement('strong'),{textContent:item.title}));
  if(item.message)copy.append(Object.assign(document.createElement('span'),{textContent:item.message}));
  const close=Object.assign(document.createElement('button'),{type:'button',className:'ds-toast-close'});close.setAttribute('aria-label','Dismiss notification');close.append(lucideNode('x'));
  el.append(lucideNode(icons[item.kind]||'info','ds-toast-icon'),copy,close);stack.append(el);
  const remove=()=>{if(el.classList.contains('is-leaving'))return;el.classList.add('is-leaving');setTimeout(()=>el.remove(),280);};
  close.addEventListener('click',remove);if(item.kind!=='error')setTimeout(remove,6000);
  while(stack.children.length>4)stack.firstElementChild.remove();
 }
 notify=function(kind,title,message='',options={}){
  const list=load(),text=String(message||'').trim();
  // The same event is saved once: by key when given, otherwise identical messages within a few seconds.
  if(options.key&&list.some(item=>item.key===options.key))return;
  if(!options.key&&list[0]&&list[0].title===title&&list[0].message===text&&Date.now()-list[0].at<4000)return;
  const item={id:Date.now().toString(36)+Math.random().toString(36).slice(2,7),kind,title,message:text,at:Date.now(),read:menuOpen(),job:options.job||null,key:options.key||null};
  list.unshift(item);save(list);syncBadge();if(menuOpen())render();toast(item);
 };
 // Errors become toasts instead of the banner at the top of the page.
 showError=function(error){const message=error?.message||String(error);$('error').hidden=true;notify('error','Something went wrong',message);};
 // A failed run gets one notification and an error card on the pipeline page with the actual reason.
 const card=document.createElement('section');card.id='pipelineErrorCard';card.className='ds-error-card';card.hidden=true;card.setAttribute('role','alert');
 $('runningPanel').prepend(card);
 reportJobError=function(failed){
  const text=String(failed?.error||'The comparison stopped unexpectedly.'),lines=text.split('\n').map(line=>line.trim()).filter(Boolean);
  // Show the file's own name instead of the server path it was stored under.
  const fileName=path=>{const base=path.split(/[\\/]/).pop(),side=/^(left|right)\.[a-z]+$/i.exec(base)?.[1];return side&&failed?.files?.[side]?.name||base;};
  const reason=(lines.filter(line=>/^ERROR:/i.test(line)).at(-1)||lines.at(-1)||text).replace(/^ERROR:\s*/i,'').replace(/(?:[A-Za-z]:)?[\\/][^:\n]*[\\/][^\\/:\n]+(?=:)/g,fileName);
  const cancelled=failed?.state==='cancelled';
  card.replaceChildren();card.classList.toggle('is-cancelled',cancelled);
  const head=document.createElement('div');head.className='ds-error-head';
  const copy=document.createElement('div');
  copy.append(Object.assign(document.createElement('strong'),{textContent:cancelled?'Comparison cancelled':'Comparison failed'}),Object.assign(document.createElement('p'),{textContent:reason}));
  head.append(lucideNode(cancelled?'circle-x':'circle-alert','ds-error-icon'),copy);
  const details=document.createElement('details');details.className='ds-error-details';
  details.append(Object.assign(document.createElement('summary'),{textContent:'Show details'}),Object.assign(document.createElement('pre'),{textContent:text}));
  const actions=document.createElement('div');actions.className='ds-error-actions';
  const consoleButton=Object.assign(document.createElement('button'),{type:'button',className:'subtle'});consoleButton.append(lucideNode('terminal'),Object.assign(document.createElement('span'),{textContent:'View console'}));
  consoleButton.addEventListener('click',()=>{const toggle=$('runningPanel').querySelector('.ds-switch[aria-checked=false]');toggle?.click();});
  const fresh=Object.assign(document.createElement('button'),{type:'button',className:'primary'});fresh.append(lucideNode('plus'),Object.assign(document.createElement('span'),{textContent:'New comparison'}));
  fresh.addEventListener('click',()=>$('menuNewComparison')?.click());
  actions.append(consoleButton,fresh);card.append(head,details,actions);card.hidden=false;
  if(!cancelled)notify('error','Comparison failed',reason,{job:failed?.id,key:failed?.id?failed.id+':Comparison:error':null});
 };
 // The dropdown under the bell: header with actions, filters, then the scrolling list.
 const view=document.createElement('div');view.id='notificationMenu';view.className='ds-notify-menu';view.hidden=true;
 view.setAttribute('role','dialog');view.setAttribute('aria-label','Notifications');
 const menuOpen=()=>!view.hidden;
 view.innerHTML='<div class="ds-notify-head"><div><h2>Notifications</h2><p>From this browser · latest 200 kept</p></div><div class="ds-notify-actions"><button type="button" class="subtle" id="markNotificationsRead"></button><button type="button" class="subtle" id="clearNotifications"></button></div></div><div class="ds-notify-filter" role="group" aria-label="Show"><button type="button" data-filter="all" aria-pressed="true">All</button><button type="button" data-filter="error" aria-pressed="false">Errors</button><button type="button" data-filter="success" aria-pressed="false">Completed</button></div><div id="notificationList" class="ds-notify-list"></div>';
 view.querySelector('#markNotificationsRead').append(lucideNode('check'),Object.assign(document.createElement('span'),{textContent:'Mark all read'}));
 view.querySelector('#clearNotifications').append(lucideNode('trash-2'),Object.assign(document.createElement('span'),{textContent:'Clear all'}));
 let filter='all';
 for(const button of view.querySelectorAll('[data-filter]'))button.addEventListener('click',()=>{filter=button.dataset.filter;for(const other of view.querySelectorAll('[data-filter]'))other.setAttribute('aria-pressed',String(other===button));render();});
 view.querySelector('#markNotificationsRead').addEventListener('click',()=>{save(load().map(item=>({...item,read:true})));syncBadge();render();});
 view.querySelector('#clearNotifications').addEventListener('click',()=>{if(!load().length||confirm('Clear all saved notifications?')){save([]);syncBadge();render();}});
 const dayLabel=time=>{const d=new Date(time),today=new Date();const start=x=>new Date(x.getFullYear(),x.getMonth(),x.getDate()).getTime();const diff=(start(today)-start(d))/864e5;return diff===0?'Today':diff===1?'Yesterday':d.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'});};
 function render(){
  const list=load().filter(item=>filter==='all'||item.kind===filter),target=view.querySelector('#notificationList');target.replaceChildren();
  if(!list.length){const empty=document.createElement('div');empty.className='ds-notify-empty';empty.append(lucideNode('bell'),Object.assign(document.createElement('strong'),{textContent:filter==='all'?'No notifications yet':'Nothing here'}),Object.assign(document.createElement('span'),{textContent:'Completions, alerts and errors will appear here.'}));target.append(empty);return;}
  let day='';
  for(const item of list){
   const label=dayLabel(item.at);if(label!==day){day=label;target.append(Object.assign(document.createElement('h3'),{className:'ds-notify-day',textContent:label}));}
   const row=document.createElement('article');row.className='ds-notify-item is-'+item.kind+(item.read?'':' is-unread');
   const copy=document.createElement('div');copy.className='ds-notify-copy';
   copy.append(Object.assign(document.createElement('strong'),{textContent:item.title}));
   if(item.message)copy.append(Object.assign(document.createElement('p'),{textContent:item.message}));
   const meta=document.createElement('div');meta.className='ds-notify-meta';
   meta.append(Object.assign(document.createElement('time'),{textContent:new Date(item.at).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}));
   if(item.job){const open=Object.assign(document.createElement('button'),{type:'button',className:'ds-notify-open'});open.append(Object.assign(document.createElement('span'),{textContent:'Open comparison'}),lucideNode('chevron-right'));open.addEventListener('click',()=>{closeMenu();openJob(item.job).catch(showError);});meta.append(open);}
   row.append(lucideNode(icons[item.kind]||'info','ds-notify-icon'),copy,meta);target.append(row);
  }
 }
 // The bell sits in the top bar between Refresh and Help. Like Refresh, it slides open on hover to show
 // its label; the label stays while the dropdown is open.
 const bell=$('navNotifications'),badge=$('notificationBadge');
 bell.className='ds-topbar-refresh ds-topbar-bell';bell.removeAttribute('data-tip');bell.type='button';
 bell.setAttribute('aria-haspopup','dialog');bell.setAttribute('aria-expanded','false');bell.setAttribute('aria-controls',view.id);
 bell.replaceChildren(lucideNode('bell'),Object.assign(document.createElement('span'),{className:'ds-refresh-label',textContent:'Notifications'}),badge);
 const anchor=document.createElement('div');anchor.className='ds-notify-anchor';
 $('workspaceHelp').before(anchor);anchor.append(bell,view);
 function openMenu(){
  view.hidden=false;bell.setAttribute('aria-expanded','true');render();
  // Opening the list counts as reading it; unread items keep their marker until it closes.
  save(load().map(item=>({...item,read:true})));syncBadge();
  $('workspaceHelp').open=false;
 }
 function closeMenu(){if(!menuOpen())return;view.hidden=true;bell.setAttribute('aria-expanded','false');}
 bell.addEventListener('click',()=>menuOpen()?closeMenu():openMenu());
 document.addEventListener('pointerdown',event=>{if(menuOpen()&&!anchor.contains(event.target))closeMenu();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menuOpen()){closeMenu();bell.focus();}});
 // The former Notifications page is now this dropdown; the bell's own click opens it, so the page's
 // shared navigation route does nothing.
 const panelBeforeNotifications=panel;
 panel=function(name){
  if(name==='notificationsPanel')return;
  panelBeforeNotifications(name);
  if(name==='runningPanel'&&!['error','cancelled'].includes(job?.state))card.hidden=true;
 };
 try{if(localStorage.getItem(workspaceViewKey)==='notificationsPanel')localStorage.removeItem(workspaceViewKey);}catch{}
 syncBadge();
}

// Live results: Analysis opens as soon as a comparison starts. Counts, the column ranking and the latest
// mismatches update about once a second from the engine's live snapshot; the final results replace them when
// the comparison finishes. Mismatches can only appear once both files have been read.
{
 const results=$('results'),view=document.createElement('section');view.id='liveResults';view.className='ds-live';view.hidden=true;results.prepend(view);
 const isLive=()=>!!job&&['queued','running'].includes(job.state);
 view.innerHTML=`<div class="ds-live-head"><div><span class="ds-live-pill"><i></i>Live</span><h2>Live results</h2><p class="ds-live-phase"></p></div><span class="ds-live-elapsed"></span></div>
  <section class="ds-live-estimate" hidden><div class="ds-live-estimate-head"><span class="ds-live-estimate-tag">Estimate</span><strong></strong><span class="ds-live-estimate-note"></span></div><div class="ds-live-estimate-columns"></div></section>
  <div class="ds-live-metrics"></div>
  <div class="ds-live-grid"><section class="ds-live-card"><h3>Differences by column</h3><div class="ds-live-columns"></div></section>
  <section class="ds-live-card"><h3>Latest mismatches</h3><div class="ds-live-feed table-scroll"><table><thead><tr><th>Key</th><th>Column</th><th>File 1 value</th><th>File 2 value</th></tr></thead><tbody></tbody></table></div></section></div>
  <p class="ds-live-foot">Counts are provisional until the comparison finishes; final results open here automatically.</p>`;
 const metrics=[['Equal keys','equal_rows','circle-check'],['Unequal keys','changed_rows','triangle-alert'],['Changed cells','changed_cells','table-2'],['Only in champion','left_only','arrow-left'],['Only in challenger','right_only','arrow-right']];
 const metricBox=view.querySelector('.ds-live-metrics');
 for(const [label,field,icon] of metrics){const card=document.createElement('div');card.className='ds-live-metric';card.dataset.field=field;card.append(lucideNode(icon),Object.assign(document.createElement('span'),{textContent:label}),Object.assign(document.createElement('strong'),{textContent:'—'}));metricBox.append(card);}
 let timer=0,polling=false,last=null;
 const keyText=value=>{try{const parts=JSON.parse(value);return Array.isArray(parts)?parts.join(' · '):String(value);}catch{return String(value);}};
 function phaseText(snapshot){
  const progress=job?.progress||{};
  if(job?.state==='queued')return 'Waiting for a server worker…';
  if(snapshot?.state==='writing'||snapshot?.stage==='reports'||progress.stage==='reports')return 'All keys compared · writing the result files and reports';
  if(snapshot?.state==='reading'&&snapshot.ranges_total)return `Reading both files · ${Math.floor(100*snapshot.ranges_read/snapshot.ranges_total)}% read · exact mismatches follow once both files are read`;
  if(snapshot?.key_ranges)return `Comparing · ${number(snapshot.key_ranges_done)} of ${number(snapshot.key_ranges)} key ranges done`;
  if(snapshot&&snapshot.matched_keys+snapshot.left_only+snapshot.right_only>0)return `Comparing · ${number(snapshot.matched_keys+snapshot.left_only+snapshot.right_only)} keys compared so far`;
  return `${progress.phase||'Reading both files'} · mismatches appear once both files have been read`;
 }
 function paint(snapshot){
  last=snapshot;
  view.querySelector('.ds-live-phase').textContent=phaseText(snapshot);
  const started=job?.started?Math.max(0,Date.now()/1000-job.started):0;
  view.querySelector('.ds-live-elapsed').textContent=started?`${Math.floor(started/60)}:${String(Math.floor(started%60)).padStart(2,'0')} elapsed`:'';
  const exact=snapshot&&snapshot.state!=='reading';
  for(const card of metricBox.children){const value=snapshot?.[card.dataset.field];card.querySelector('strong').textContent=exact&&Number.isFinite(value)?number(value):'—';}
  // Early estimate from sampled keys while the files are read, with a 95% margin of error.
  const estimate=snapshot?.estimate,estimateBox=view.querySelector('.ds-live-estimate');
  estimateBox.hidden=!(estimate&&snapshot.state==='reading');
  if(!estimateBox.hidden){
   const n=estimate.sampled_keys,rate=estimate.changed_rows/n,margin=1.96*Math.sqrt(Math.max(rate*(1-rate),.25/n)/n);
   estimateBox.querySelector('strong').textContent=`About ${(rate*100).toFixed(1)}% of matched keys differ (± ${(margin*100).toFixed(1)}%)`;
   estimateBox.querySelector('.ds-live-estimate-note').textContent=`From ${number(n)} sampled keys found in both files · ${Math.floor(100*snapshot.ranges_read/snapshot.ranges_total)}% of the files read. Exact counts replace this estimate.`;
   const top=Object.entries(estimate.changed_cells_by_column||{}).sort((a,b)=>b[1]-a[1]).slice(0,6),box=view.querySelector('.ds-live-estimate-columns');box.replaceChildren();
   for(const [name,count] of top){const chip=document.createElement('span');chip.className='ds-live-estimate-chip';chip.textContent=`${name} · ~${(100*count/n).toFixed(1)}%`;chip.title=`Estimated share of matched keys that differ in ${name}`;box.append(chip);}
  }
  const columns=Object.entries(snapshot?.changed_cells_by_column||{}).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,12);
  const max=Math.max(1,...columns.map(([,count])=>count)),box=view.querySelector('.ds-live-columns');box.replaceChildren();
  if(!columns.length)box.append(Object.assign(document.createElement('p'),{className:'ds-live-empty',textContent:'No column differences yet.'}));
  for(const [name,count] of columns){
   const row=document.createElement('div');row.className='ds-live-column';
   const bar=document.createElement('span');bar.className='ds-live-bar';const fill=document.createElement('i');fill.style.width=Math.max(2,count*100/max)+'%';bar.append(fill);
   row.append(Object.assign(document.createElement('span'),{className:'ds-live-name',textContent:name,title:name}),bar,Object.assign(document.createElement('b'),{textContent:number(count)}));box.append(row);
  }
  const body=view.querySelector('tbody');body.replaceChildren();
  const samples=(snapshot?.samples||[]).slice(0,50);
  if(!samples.length){const tr=document.createElement('tr');const td=Object.assign(document.createElement('td'),{colSpan:4,className:'ds-live-empty',textContent:'Mismatches will appear here as keys are compared.'});tr.append(td);body.append(tr);}
  for(const [key,column,left,right] of samples){
   const tr=document.createElement('tr');
   for(const [value,className] of [[keyText(key),''],[column,''],[left,'analysis-before'],[right,'analysis-after']]){const td=Object.assign(document.createElement('td'),{textContent:value===''?'(empty)':value,className});td.title=value;tr.append(td);}
   body.append(tr);
  }
 }
 async function poll(){
  clearTimeout(timer);if(polling||view.hidden||!isLive())return;polling=true;
  try{paint(await api(endpoint('/live')));}catch{}finally{polling=false;if(!view.hidden&&isLive())timer=setTimeout(poll,1000);}
 }
 function show(on){
  view.hidden=!on;results.classList.toggle('is-live',on);
  if(on){paint(last);poll();}else clearTimeout(timer);
 }
 const syncNavigationBeforeLive=syncNavigation;
 syncNavigation=function(){syncNavigationBeforeLive();if(isLive())$('navResults').disabled=false;};
 const renderSummaryBeforeLive=renderSummary;
 renderSummary=function(){if(isLive()){show(true);return;}show(false);renderSummaryBeforeLive();};
 const renderExportsBeforeLive=renderExports;
 renderExports=function(){if(isLive())return;renderExportsBeforeLive();};
 const panelBeforeLive=panel;
 panel=function(name){panelBeforeLive(name);if(name!=='results')show(false);else if(isLive())show(true);};
 // A shortcut from the pipeline page.
 const liveButton=document.createElement('button');liveButton.type='button';liveButton.id='openLiveResults';liveButton.className='ds-live-open';liveButton.hidden=true;
 liveButton.append(Object.assign(document.createElement('i'),{className:'ds-live-dot'}),Object.assign(document.createElement('span'),{textContent:'View live results'}),lucideNode('arrow-right'));
 liveButton.addEventListener('click',()=>goView('results').catch(showError));
 $('runningPanel').querySelector('#pipelineErrorCard')?.after(liveButton);
 setInterval(()=>{liveButton.hidden=!isLive()||currentView!=='runningPanel';if(!view.hidden&&!isLive()&&job?.state==='complete'&&currentView==='results'){show(false);renderSummary();renderExports();}},1000);
}

// Excel options in the Export menu: rows per column sheet, and whether values longer than an Excel cell are
// shortened (and flagged) instead of failing. Excel is generated only when asked for, never in the pipeline.
{
 const excelButton=$('excel'),row=excelButton?.closest('.ds-export-option');
 if(row){
  const box=document.createElement('div');box.className='ds-excel-options';
  box.innerHTML='<label><span>Rows per column sheet</span><select id="excelRows"><option value="1000">First 1,000</option><option value="10000" selected>First 10,000</option><option value="100000">First 100,000</option><option value="0">Maximum (1,048,574)</option></select></label><label class="ds-excel-check"><input type="checkbox" id="excelTruncate" checked><span>Shorten values too long for an Excel cell (marked [TRUNCATED])</span></label>';
  excelButton.before(box);
  enhanceSelect($('excelRows'));
  excelOptions=()=>({max_rows:Number($('excelRows').value),truncate:$('excelTruncate').checked});
  const sync=()=>{const saved=job?.excel_options;if(saved){$('excelRows').value=String(saved.max_rows||0);$('excelTruncate').checked=saved.truncate!==false;$('excelRows').dispatchEvent(new Event('change'));}};
  const renderExportsBeforeOptions=renderExports;renderExports=function(){renderExportsBeforeOptions();if(!document.activeElement?.closest?.('.ds-excel-options'))sync();};
 }
}


// Typed comparison in the rules drawer: compare a column as a number, a decimal at a scale, a date, or a
// timestamp in a time zone. Values stay exact text; the type only decides when two texts are equal.
{
 const options=$('comparisonRulesDrawer').querySelector('.rule-options');
 const typed=document.createElement('div');typed.className='ds-rule-type';
 typed.innerHTML='<label><span>Compare as</span><select id="ruleType"><option value="">Text, exactly</option><option value="number">Number (1 = 1.0)</option><option value="decimal">Decimal at a scale</option><option value="date">Date</option><option value="timestamp">Timestamp with time zone</option></select></label><label id="ruleScaleField" hidden><span>Decimal places</span><input id="ruleScale" type="number" min="0" max="18" value="2" inputmode="numeric"></label><label id="ruleTimezoneField" hidden><span>Time zone for values without one</span><input id="ruleTimezone" value="UTC" placeholder="UTC or Asia/Kolkata" autocomplete="off" spellcheck="false"></label>';
 options.before(typed);enhanceSelect($('ruleType'));
 const numericControls=['ruleNumericFormat','ruleTolerance','ruleDateLeft','ruleDateRight'];
 $('ruleType').addEventListener('change',()=>{
  const kind=$('ruleType').value;$('ruleScaleField').hidden=kind!=='decimal';$('ruleTimezoneField').hidden=kind!=='timestamp';
  // A type replaces tolerance and date formats for that column, so those inputs pause while one is chosen.
  for(const id of numericControls){const control=$(id);control.disabled=!!kind||(id==='ruleTolerance'&&$('ruleNumericFormat').checked);control.closest('label')?.classList.toggle('is-disabled',!!kind);}
 });
}

// Shareable links into the mismatch drawer. A link names the job, the view, the column and the key, so a
// teammate who opens it lands on that drawer view with the row highlighted. Nothing is shared by the link
// itself: the teammate still needs access to the job on this server.
{
 const drawer=$('analysisDrawer'),table=$('analysisTable');
 if(drawer&&table){
  const linkFor=state=>{
   const params=new URLSearchParams({review:state.job});
   if(state.column)params.set('column',state.column);
   if(state.key)params.set('key',JSON.stringify(state.key));
   if(state.view&&state.view!=='columns')params.set('view',state.view);
   return location.origin+location.pathname+'#'+params;
  };
  const currentKey=()=>{const text=$('analysisKey').value;if(!text)return null;try{return job.keys.length===1?[text]:JSON.parse(text);}catch(error){return null;}};
  async function copyLink(state,what){
   const url=linkFor(state);
   try{await navigator.clipboard.writeText(url);notify('success','Link copied',`Anyone with access to this job lands on ${what}.`);}
   catch(error){window.prompt('Copy this link',url);}
  }
  // Toolbar: a link to what the drawer shows now.
  const toolbar=drawer.querySelector('.ds-mismatch-toolbar'),share=document.createElement('button');
  share.type='button';share.className='ds-toolbar-btn ds-share-btn';share.innerHTML=lucide('link')+'<span>Copy link</span>';
  share.title='Copy a link to this view';
  share.addEventListener('click',()=>{
   const column=$('analysisColumn').value,key=currentKey(),view=$('analysisMode').value;
   copyLink({job:job.id,column,key,view},key?(column?`key ${key.join(' · ')} in ${column}`:`key ${key.join(' · ')}`):column?`the ${column} mismatches`:'this mismatch view');
  });
  (toolbar||drawer.querySelector('.drawer-heading')).append(share);
  // Rows: a link to one mismatch (key and column) or one key.
  function rowState(row){
   const head=[...table.rows[0].cells].map(cell=>cell.textContent),count=job.keys.length;
   if(!job.keys.every((name,i)=>head[i]===name))return null;
   const key=[...row.cells].slice(0,count).map(cell=>cell.textContent);
   const columnIndex=head.indexOf('Column');
   if(columnIndex>=0)return {job:job.id,key,column:row.cells[columnIndex].textContent,view:'columns'};
   if(head.includes('Status'))return {job:job.id,key,view:'keys'};
   return null;
  }
  let target=null;
  function decorate(){
   if(!table.rows.length)return;
   for(const row of [...table.rows].slice(1)){
    const actions=row.cells[row.cells.length-1];
    if(!actions||actions.querySelector('.ds-row-link'))continue;
    const state=rowState(row);if(!state)continue;
    const button=document.createElement('button');button.type='button';button.className='ds-row-action ds-row-link';
    button.title=button.ariaLabel=state.column?'Copy link to this mismatch':'Copy link to this key';button.setAttribute('aria-label',button.title);
    button.append(lucideNode('link'));
    button.addEventListener('click',()=>copyLink(state,state.column?`key ${state.key.join(' · ')} in ${state.column}`:`key ${state.key.join(' · ')}`));
    actions.append(button);
   }
   if(!target)return;
   // Highlight the shared row once it is on the page.
   const match=[...table.rows].slice(1).find(row=>{const state=rowState(row);return state&&JSON.stringify(state.key)===JSON.stringify(target.key)&&(!target.column||state.column===target.column);});
   if(match){table.querySelector('.ds-row-target')?.classList.remove('ds-row-target');match.classList.add('ds-row-target');match.scrollIntoView({block:'center'});target=null;}
  }
  new MutationObserver(decorate).observe(table,{childList:true});
  // Landing: open the job, then the drawer at the shared view.
  async function land(){
   const params=new URLSearchParams(location.hash.slice(1)),id=params.get('review');
   if(!id)return;
   history.replaceState(null,'',location.pathname+location.search);
   if(!/^[a-f0-9]{32}$/.test(id)){notify('error','This link is not valid','The job id in the link is not recognised.');return;}
   let key=null;
   try{key=params.has('key')?JSON.parse(params.get('key')):null;}catch(error){}
   const column=params.get('column')||'',view=params.get('view')||'columns';
   try{
    await openJob(id);
    if(job?.state!=='complete'){notify('info','Comparison not finished','The link opens its mismatches once the comparison completes.');return;}
    if(currentView!=='results')await goView('results');
    target=null;
    await showAnalysisDrawer(column);
    if(key&&Array.isArray(key)&&key.length===job.keys.length){
     $('analysisMode').value=view==='keys'||!column?'keys':'columns';
     if(!column)$('analysisColumn').value='';
     // The row is marked on this final load, which shows only the shared key.
     target={key,column};selectAnalysisKey(key);analysisOffset=0;await loadAnalysis();
    }else if(view==='keys'||view==='patterns'){$('analysisMode').value=view;$('analysisColumn').value=view==='keys'?'':column;await loadAnalysis();}
   }catch(error){notify('error','Could not open the shared link',error.message||String(error));}
  }
  window.addEventListener('workspace-restored',()=>land().catch(showError),{once:true});
  window.addEventListener('hashchange',()=>land().catch(showError));
 }
}
