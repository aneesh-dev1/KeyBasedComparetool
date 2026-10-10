const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),storage=new Map();
class Element{
 constructor(id=''){this.id=id;this.children=[];this.events={};this.value='';this.options=[];this.hidden=false;this.dataset={};this.style={};this.firstChild={textContent:''};const classes=new Set();this.classList={add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n),toggle:(n,on)=>{if(on===undefined)on=!classes.has(n);on?classes.add(n):classes.delete(n);}};}
 addEventListener(n,fn){(this.events[n]??=[]).push(fn)}
 append(...n){this.children.push(...n)}
 replaceChildren(...n){this.children=n;this.options=[]}
 add(n){this.options.push(n);if(this.options.length===1)this.value=n.value}
 setAttribute(n,v){this[n]=v} removeAttribute(n){delete this[n]}
 querySelector(q){return this.parts??=new Element()}
 querySelectorAll(){return []} focus(){} scrollIntoView(){}
 insertAdjacentHTML(_,s){register(s)}
}
function register(s){for(const m of s.matchAll(/id="([^"]+)"/g))nodes.set(m[1],new Element(m[1]));}
register(fs.readFileSync('web/index.html','utf8'));
let activity='running';const notifications=[];
function findNode(id){const visit=n=>{if(n.id===id)return n;for(const c of n.children||[]){const found=visit(c);if(found)return found;}return null;};for(const n of nodes.values()){const found=visit(n);if(found)return found;}return null;}
const doc={getElementById:id=>nodes.get(id)||findNode(id),body:new Element(),querySelector:q=>{const el=new Element();el.content=q.includes('max-sort')?'4096':'token';return el;},querySelectorAll:()=>[],addEventListener(){},createElement:()=>new Element()};
class Notification{static permission='granted';static async requestPermission(){return 'granted'}constructor(title,opts){notifications.push(opts.body)}}
const context=vm.createContext({matchMedia:()=>({matches:false,addEventListener(){}}),document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}}),scrollTo(){},Notification},Notification,innerHeight:900,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setInterval(){},setTimeout(){},clearTimeout(){},Option:function(t,v){this.text=t;this.value=v},URL,URLSearchParams,Blob,TextDecoder,structuredClone,console,confirm:()=>true,location:{reload(){}},fetch:async path=>({ok:true,json:async()=>path==='/api/activity'?{activities:[{id:'a',kind:'Comparison',state:activity}]}:{containers:[],profiles:[]}})});
storage.set('keywise-job','a'.repeat(32));
vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
assert.equal(vm.runInContext('job',context),null,'Saved selection must not open automatically');
assert.equal(vm.runInContext('currentView',context),'uploadPanel');

(async()=>{
vm.runInContext(`job={id:'a',state:'complete',keys:['id','part'],exports:{},summary:{changed_cells_by_column:{large:3,small:1,equal:0},changed_rows:3,changed_cells:4,matched_keys:10,left_only:1,right_only:0}};currentView='analysisPanel';api=async()=>({notes:[]});`,context);
await vm.runInContext('openAnalysis()',context);
assert.equal(nodes.get('analysisTable').children.length,3); // header + two mismatching columns
assert.equal(nodes.get('saveNote').disabled,true);
assert.equal(findNode('analysisKeyPart0').placeholder,'Exact id');
nodes.get('analysisColumnSearch').value='small';await vm.runInContext('loadAnalysis()',context);
assert.equal(nodes.get('analysisTable').children.length,2);
await vm.runInContext("resetAnalysis('columns')",context);
vm.runInContext(`api=async path=>{globalThis.lastQuery=path;return {state:'complete',total:1,rows:[['["001","A,B"]','large','','new',0,3]]}}`,context);
findNode('analysisKeyPart0').value='001';findNode('analysisKeyPart1').value='A,B';
nodes.get('analysisSearch').events.click[0]();await vm.runInContext('loadAnalysis()',context);
assert.deepEqual(JSON.parse(new URL(context.lastQuery,'http://local').searchParams.get('key')),['001','A,B']);
const row=nodes.get('analysisTable').children[1];assert.equal(row.children[3].textContent,'(empty)');assert.equal(row.children[3].className,'analysis-before');assert.equal(row.children[4].className,'analysis-after');
assert(nodes.get('noteScope').textContent.includes('id: 001'));
findNode('analysisKeyPart0').value='unsubmitted';await vm.runInContext('loadAnalysis()',context);assert(!nodes.get('noteScope').textContent.includes('unsubmitted'));
vm.runInContext("api=async()=>({state:'complete',total:0,rows:[]})",context);
await vm.runInContext("resetAnalysis('keys')",context);
assert.equal(nodes.get('analysisKey').value,'');assert.equal(findNode('analysisKeyPart0').value,'');assert.equal(nodes.get('analysisColumnControls').hidden,true);assert.equal(nodes.get('saveNote').disabled,true);
vm.runInContext(`api=async path=>({state:'complete',total:1,rows:path.includes('mode=patterns')?[['large','Case only','None','none',3,4,4]]:[['["001","A,B"]','large','None','none',4,4]],categories:[['Case only',3]]})`,context);
await vm.runInContext("resetAnalysis('patterns')",context);
assert.equal(nodes.get('analysisKeyControls').hidden,true);assert.equal(nodes.get('patternSummary').textContent,'Case only: 3 cells');
assert.equal(nodes.get('analysisTable').children[1].children[4].textContent,3);
nodes.get('analysisTable').children[1].children[6].children[0].events.click[0]();await vm.runInContext('loadAnalysis()',context);
assert.equal(nodes.get('analysisColumn').value,'large');assert.equal(nodes.get('analysisMode').value,'columns');
vm.runInContext(`analysisNotesCache=[{column:'',key:['001','A,B'],status:'Expected',comment:'Key explanation'},{column:'large',key:null,status:'Needs investigation',comment:'Column explanation'}]`,context);
assert(vm.runInContext("commentsFor('large',['001','A,B'])",context).includes('Key explanation'));
assert(vm.runInContext("commentsFor('small',['001','A,B'])",context).includes('Key explanation'));
assert(!vm.runInContext("commentsFor('small',['002','A,B'])",context).includes('Key explanation'));
assert(vm.runInContext("commentsFor('large',['002','A,B'])",context).includes('Column explanation'));
assert(nodes.get('analysisColumnChoices').children.length>0);
vm.runInContext("selectAnalysisKey(['001','A,B']);$('analysisColumn').value='large';$('noteTarget').value='key';fillCommentEditor()",context);
assert.equal(nodes.get('noteComment').value,'Key explanation');assert.equal(vm.runInContext('selectedCommentTarget().column',context),'');
// One action generates and downloads exactly once, for the original job.
vm.runInContext(`job.exports={};globalThis.downloads=[];download=(name,id)=>downloads.push([name,id]);api=async(path,body)=>{if(path.includes('/export/'))return {};return {exports:{html:{state:'complete',size:100}}};}`,context);
await vm.runInContext("generateAndDownload('html')",context);assert.equal(context.downloads.length,1);assert.equal(context.downloads[0][1],'a');
// Upload-selected templates wait for worksheet preparation, then apply once.
vm.runInContext(`globalThis.pendingProfile='template-1';globalThis.appliedProfiles=[];localStorage.getItem=()=>pendingProfile;localStorage.removeItem=()=>{pendingProfile=null};applyProfile=async id=>appliedProfiles.push(id);job.state='preparing';`,context);
await vm.runInContext('applyPendingUploadProfile()',context);assert.equal(context.appliedProfiles.length,0);
vm.runInContext("job.state='ready'",context);await vm.runInContext('applyPendingUploadProfile()',context);await vm.runInContext('applyPendingUploadProfile()',context);assert.deepEqual(Array.from(context.appliedProfiles),['template-1']);
// Setup starts comparison directly; preview is not a prerequisite.
vm.runInContext(`job={id:'direct',state:'ready'};selected.clear();selected.add('id');$('compare').disabled=false;globalThis.starts=[];api=async(path,body)=>{starts.push(path);return {id:'direct',state:'queued'};};panel=name=>{currentView=name};refresh=async()=>{};`,context);
await nodes.get('scopeNext').events.click[0]();
assert.deepEqual(Array.from(context.starts),['/api/jobs/direct/start']);
assert.equal(vm.runInContext('currentView',context),'runningPanel');
assert.equal(vm.runInContext("phaseViews.includes('sourcePanel')||phaseViews.includes('overridesPanel')",context),false);
assert.equal(vm.runInContext('phaseViews.length',context),5);
// Restoring the previous run is an explicit user action; New retains its pointer.
vm.runInContext(`job=null;localStorage.getItem=()=> 'a'.repeat(32);api=async()=>({id:'a'.repeat(32),state:'complete'});`,context);
await vm.runInContext('restoreCurrentComparison()',context);
assert.equal(vm.runInContext('job.id',context),'a'.repeat(32));
assert.equal(vm.runInContext('currentView',context),'results');
vm.runInContext(`globalThis.reloaded=false;saveDraft=async()=>{};location.reload=()=>{reloaded=true};localStorage.removeItem=()=>{throw new Error('Must retain the last selection')};`,context);
await nodes.get('menuNewComparison').events.click[0]();assert.equal(context.reloaded,true);
register('<input id="consoleSearch"><button id="consolePause"></button><p id="consoleStatus"></p>');
nodes.get('consoleSearch').value='batch';
vm.runInContext("logText='Starting\\nBatch 1 complete\\nFinished';consolePaused=false;consoleFollow=false;paintConsole()",context);
assert.equal(nodes.get('consoleLog').textContent,'Batch 1 complete');
vm.runInContext("consolePaused=true;logText+='\\nBatch 2 complete';paintConsole()",context);
assert.equal(nodes.get('consoleLog').textContent,'Batch 1 complete');
vm.runInContext('consolePaused=false;paintConsole()',context);
assert.equal(nodes.get('consoleLog').textContent,'Batch 1 complete\nBatch 2 complete');
console.log('Analysis, direct start, fresh startup/restore, and console filter/pause checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Rules on overlapping selections accumulate without dropping prior normalization.
const composed=vm.runInContext(`mergeColumnRules([{column:'a',trim:true}],['a','b'],{ignore_case:true,tolerance:'0'})`,context);
assert.equal(composed.length,2);assert.equal(composed[0].trim,true);assert.equal(composed[0].ignore_case,true);assert.equal(composed[1].tolerance,'0');
assert.throws(()=>vm.runInContext(`mergeColumnRules([{column:'a',tolerance:'0'}],['a'],{left_date_format:'%Y',right_date_format:'%Y'})`,context),/cannot be combined/);
assert.equal(vm.runInContext(`mergeColumnRules([{column:'a',tolerance:'0'}],['a'],{tolerance:'0.1'})[0].tolerance`,context),'0.1');
assert.equal(vm.runInContext("detectedFileType({name:'sample.JSON'})",context),'JSON');
assert.equal(vm.runInContext("detectedFileType({name:'sample.xlsx'})",context),'Excel');
assert.equal(vm.runInContext("detectedFileType({name:'sample.csv'})",context),'CSV');
