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
uploadedSourcesBody.insertAdjacentHTML('afterbegin','<div class="source-mode" role="group" aria-label="File source"><button id="computerSource" class="subtle" aria-pressed="true">Upload from computer</button><button id="serverSource" class="subtle" aria-pressed="false">Use server paths</button></div><div id="serverSourceFields" class="server-source-workspace" hidden><div class="server-path-grid"><label class="server-path-card"><span class="file-top"><span class="label">CHAMPION DATASET</span><span class="file-symbol">A</span></span><span class="server-path-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="8" x="2" y="3" rx="2"/><rect width="20" height="8" x="2" y="13" rx="2"/><path d="M6 7h.01M6 17h.01"/></svg></span><span class="server-path-copy"><strong>Champion server path</strong><small>CSV, Excel, Parquet or JSON</small></span><input id="leftServerPath" aria-label="Champion server path" placeholder="/shared/input/champion.csv" autocomplete="off"></label><label class="server-path-card"><span class="file-top"><span class="label">CHALLENGER DATASET</span><span class="file-symbol">B</span></span><span class="server-path-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="8" x="2" y="3" rx="2"/><rect width="20" height="8" x="2" y="13" rx="2"/><path d="M6 7h.01M6 17h.01"/></svg></span><span class="server-path-copy"><strong>Challenger server path</strong><small>CSV, Excel, Parquet or JSON</small></span><input id="rightServerPath" aria-label="Challenger server path" placeholder="/shared/input/challenger.parquet" autocomplete="off"></label></div><div class="server-source-actions"><span class="server-source-note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg><span id="serverSourceHelp" role="status"></span></span><button id="useServerSources" class="primary">Load server files →</button></div></div>');
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
    const result=await api('/api/server-sources',{files:{left:$('leftServerPath').value,right:$('rightServerPath').value},delimiter:selectedDelimiter(),encoding:$('encoding').value});
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

function clearRuleBuilder(){for(const id of ['ruleTrim','ruleCase','ruleNumericFormat'])$(id).checked=false;for(const id of ['ruleTolerance','ruleDateLeft','ruleDateRight'])$(id).value='';$('ruleTolerance').disabled=false;}
function hasRuleDraft(){return ['ruleTrim','ruleCase','ruleNumericFormat'].some(id=>$(id).checked)||['ruleTolerance','ruleDateLeft','ruleDateRight'].some(id=>$(id).value!=='');}
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
 ['uploadedSources','Choose file sources','Upload CSV, Excel, Parquet or JSON from your computer, or read files inside configured server directories.'],
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
  const types=document.createElement('span');types.className='file-type-line';types.textContent='CSV · Excel · Parquet · JSON';
  card.querySelector('.file-top').after(icon);card.querySelector('.muted').after(types);
  card.querySelector('.label').textContent=label.toUpperCase()+' DATASET';
  const picker=card.querySelector('input[type=file]');
  const name=card.querySelector('strong');
  if(picker)picker.setAttribute('aria-label',`Choose ${label.toLowerCase()} CSV, Excel, Parquet or JSON`);
  if(name&&!files[side])name.textContent=`Choose ${label.toLowerCase()} CSV, Excel, Parquet or JSON`;
  const arrow=card.querySelector('.browse>span');if(arrow)arrow.innerHTML=lucide('chevron');
 }
}
const formatNote=$('uploadPanel').querySelector('.settings p');
formatNote?.remove();
const exactPill=$('uploadPanel').querySelector('.section-title>.pill');
exactPill?.remove();
const uploadSettings=$('uploadPanel').querySelector('.settings');
const uploadFormatSettings=document.createElement('details');uploadFormatSettings.id='uploadFormatSettings';uploadFormatSettings.className='upload-format-settings';
uploadFormatSettings.innerHTML=`<summary aria-label="File settings" title="CSV delimiter and encoding">${lucide('sliders')}</summary><div class="file-settings-menu"><div class="file-settings-heading"><span>${lucide('sliders')}</span><div><strong>CSV file settings</strong><small>Delimiter and text encoding</small></div></div><p>Used only when a selected source is CSV.</p></div>`;
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
 for(const [summary,label] of [[$('uploadFormatSettings').querySelector('summary'),'CSV settings'],[$('uploadPanel').querySelector('.upload-profile>summary'),'Profile templates']]){
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
 const SLICES=48;
 let running=null;
 function build(){
  const d=dialog.getBoundingClientRect(),f=!fab.hidden&&fab.getClientRects().length?fab.getBoundingClientRect():null;
  const g={L:d.left,T:d.top,W:d.width,H:d.height,tx:f?f.left+Math.min(f.width,f.height)/2:innerWidth/2,ty:f?f.top+f.height/2:innerHeight-24,tw:f?Math.min(f.width,f.height)*.8:40};
  const overlay=document.createElement('div');overlay.className='ds-genie';overlay.setAttribute('aria-hidden','true');overlay.setAttribute('popover','manual');
  const computed=getComputedStyle(dialog),copy=['backgroundColor','backgroundImage','borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor','borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius','paddingTop','paddingRight','paddingBottom','paddingLeft','color','fontFamily','fontSize','lineHeight','display','flexDirection','rowGap','columnGap','overflow'];
  const template=dialog.cloneNode(true);template.removeAttribute('id');template.querySelector('.tu-request-loader')?.remove();for(const node of template.querySelectorAll('[id]'))node.removeAttribute('id');
  const slices=[];
  for(let i=0;i<SLICES;i++){
   const slice=document.createElement('div');slice.className=dialog.className;slice.setAttribute('open','');
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
  dialog.animate([{opacity:0},{opacity:1}],{duration:420,easing:'ease-out',pseudoElement:'::backdrop'});
  play(1,0,600).finished.then(()=>{dialog.style.visibility='';});
 };
 let closing=false;
 async function genieClose(){
  if(!dialog.open||$('startRerun').disabled||closing)return;
  if(reduced()){nativeClose();return;}
  closing=true;
  const animation=play(0,1,560);dialog.style.visibility='hidden';
  dialog.animate([{opacity:1},{opacity:0}],{duration:460,easing:'ease-in',fill:'forwards',pseudoElement:'::backdrop'});
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
 const title=panel.querySelector('h2');if(title)title.textContent='Column health';
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
