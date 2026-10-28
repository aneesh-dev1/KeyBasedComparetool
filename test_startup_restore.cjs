const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/app.js','utf8');
const functions=source.slice(source.indexOf('function recoverComparisonRestore('),source.indexOf("$('scopeNext').addEventListener"));
const id='a'.repeat(32);
async function run({error,saved='results',identity=id,state='complete',manual=false}={}){
 const store=new Map([['keywise-workspace-view',saved],['upload-profile:'+id,'example']]);
 if(identity!==null)store.set('keywise-job',identity);
 const errors=[],views=[];let calls=0;
 const context={job:null,pollTimer:null,hydratedId:null,renderedId:null,headerDraftJob:null,
 currentView:'uploadPanel',workspaceViewKey:'keywise-workspace-view',
 localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
 clearTimeout,endpoint:()=>'/api/jobs/'+id,
 api:async()=>{calls++;if(error)throw error;return {id,state,headers_reviewed:true}},
 panel:view=>{views.push(view);store.set('keywise-workspace-view',view)},
 refresh:async()=>{},showError:e=>errors.push(e),renderHeaders:()=>{},renderKeys:()=>{},
 renderIgnoredColumns:()=>{},loadContainers:async()=>{},loadProjects:async()=>{},loadHistory:async()=>{},loadProfiles:async()=>{}};
 vm.createContext(context);vm.runInContext(functions,context);
 await vm.runInContext(manual?'restoreCurrentComparison()':'restoreWorkspaceOnReload()',context);
 return {store,errors,views,calls,context};
}
(async()=>{
 for(const status of [400,404]){
  for(const manual of [false,true]){
   const r=await run({error:Object.assign(new Error('Comparison not found'),{status}),manual});
   assert.equal(r.store.has('keywise-job'),false);assert.equal(r.errors.length,0);
   assert.equal(r.views.at(-1),'uploadPanel');assert.equal(r.store.has('upload-profile:'+id),false);
  }
 }
 for(const error of [new TypeError('Failed to fetch'),Object.assign(new Error('Unexpected server error'),{status:500}),Object.assign(new Error('Forbidden'),{status:403})]){
  const r=await run({error});assert.equal(r.store.get('keywise-job'),id);
  assert.equal(r.store.get('keywise-workspace-view'),'results');assert.equal(r.errors.length,1);
 }
 const ready=await run({state:'ready',saved:'keysPanel'});assert.equal(ready.views.at(-1),'keysPanel');
 const completed=await run();assert.equal(completed.views.at(-1),'results');
 for(const identity of [null,'invalid']){
  const r=await run({identity});assert.equal(r.calls,0);assert.equal(r.views.at(-1),'uploadPanel');
 }
 const library=await run({saved:'projectsPanel'});assert.equal(library.calls,0);assert.equal(library.views.at(-1),'projectsPanel');
 console.log('Startup restoration: 12 scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
