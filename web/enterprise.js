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
const originalPipeline=renderPipeline;renderPipeline=async function(){await originalPipeline();
  if(job?.state==='complete'&&!viewingHistoryJob&&$('tourCard')?.hidden!==false&&currentView==='runningPanel'&&completionJob!==job.id){
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
 const minimum=9000,remaining=Math.max(0,minimum-(performance.now()-appPreloaderStarted));
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

// Lucide-style line icons are embedded so the on-prem application stays offline.
const lucidePaths={
 compare:'<rect width="8" height="14" x="3" y="5" rx="2"/><path d="M7 8v2M7 14v2M14 8h7M14 12h7M14 16h7"/>',
 projects:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M8 17v-3m4 3v-6m4 6V9"/>',
 history:'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
 package:'<path d="m7.5 4.27 9 5.15M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
 profiles:'<path d="M9 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3"/><rect width="6" height="4" x="9" y="3" rx="1"/><path d="M9 12h6M9 16h6"/>',
 docs:'<path d="M12 7v14M3 5h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v14h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z"/>',
 files:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
 scope:'<path d="M3 6h18M7 12h10M10 18h4"/>',
 pipeline:'<rect width="6" height="6" x="3" y="3" rx="1"/><rect width="6" height="6" x="15" y="15" rx="1"/><path d="M9 6h3a3 3 0 0 1 3 3v6M15 18h-3a3 3 0 0 1-3-3V9"/>',
 results:'<path d="M3 3v18h18"/><path d="m7 16 4-5 4 3 5-7"/>',
 upload:'<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M20 15v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-4"/>',
 shield:'<path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3Z"/><path d="m9 12 2 2 4-4"/>',
 sliders:'<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
 chevron:'<path d="m9 18 6-6-6-6"/>',
 check:'<path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
 option:'<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r=".5"/><circle cx="3.5" cy="12" r=".5"/><circle cx="3.5" cy="18" r=".5"/>'
};
function lucide(name,className=''){return `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${lucidePaths[name]}</svg>`;}
for(const [id,name] of Object.entries({navNew:'compare',navProjects:'projects',navHistory:'history',navContainers:'package',navProfiles:'profiles',navDocs:'docs'})){
 const icon=$(id)?.querySelector('.side-icon');if(icon)icon.innerHTML=lucide(name);
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
 'Equal rows':['equal','Matched records'],
 'Changed rows':['rows','Records with differences'],
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
