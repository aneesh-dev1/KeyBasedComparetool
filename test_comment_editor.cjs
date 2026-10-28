const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const app=fs.readFileSync('web/app.js','utf8'),enterprise=fs.readFileSync('web/enterprise.js','utf8');
const editor=app.slice(app.indexOf('function fillCommentEditor(){'),app.indexOf("$('noteTarget').addEventListener('change',fillCommentEditor)"));
// Exercise the production dropdown rendering and event subscriptions alongside the editor.
const sync=enterprise.slice(enterprise.indexOf(' function sync(){',enterprise.indexOf('function enhanceSelect(')),enterprise.indexOf(' function build(){',enterprise.indexOf('function enhanceSelect(')));
const subscriptions=enterprise.match(/ select.addEventListener\('change',sync\);\s*select.addEventListener\('input',sync\);/)[0];
class Select extends EventTarget{
 constructor(values){super();this.options=values.map(text=>({textContent:text}));this.disabled=false;this.value=values[0];}
 get selectedIndex(){return this.options.findIndex(o=>o.textContent===this.value);}
}
const fields={noteStatus:new Select(['Needs investigation','Expected','Resolved']),noteTarget:new Select(['column','key','cell']),noteComment:{value:''}};
const labels={};
for(const name of ['noteStatus','noteTarget']){const value={textContent:''};labels[name]=value;vm.runInNewContext(sync+subscriptions+'sync();',{select:fields[name],value,trigger:{setAttribute(){}}});}
let target={column:'balance',key:null},notes=[];
const context=vm.createContext({$:id=>fields[id],selectedCommentTarget:()=>target,get analysisNotesCache(){return notes;},renderCommentScope(){},Event});
vm.runInContext(editor,context);
const fill=()=>vm.runInContext('fillCommentEditor()',context);
const expect=(status,comment,scope)=>{assert.equal(fields.noteStatus.value,status);assert.equal(labels.noteStatus.textContent,status);assert.equal(fields.noteComment.value,comment);assert.equal(labels.noteTarget.textContent,scope);};
for(const status of ['Expected','Resolved','Needs investigation']){
 notes=[{column:'balance',key:null,status,comment:'Saved finding'}];target={column:'balance',key:null};fields.noteTarget.value='column';fill();expect(status,'Saved finding','column');
 // Moving to an unannotated column must reset BOTH label and submitted value.
 target={column:'region',key:null};fill();expect('Needs investigation','','column');
 // Simulate choosing a status and saving: posted native value equals the visible label.
 fields.noteStatus.value=status;fields.noteStatus.dispatchEvent(new Event('change'));assert.equal(labels.noteStatus.textContent,fields.noteStatus.value);
 target={column:'balance',key:null};fill();expect(status,'Saved finding','column');
 for(const scope of ['key','cell']){
  fields.noteTarget.value=scope;target={column:scope==='key'?'':'balance',key:['001']};notes=[{...target,status,comment:'Key finding'}];fill();expect(status,'Key finding',scope);
 }
}
console.log('Comment editor checks passed: all classifications, new columns, saved comments, key/cell scope and displayed/submitted agreement.');
