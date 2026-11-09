const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
function setup(){
 const html=fs.readFileSync(path.join(__dirname,'../resources/templates/portal.html'),'utf8');
 const dom=new JSDOM(html,{url:'https://dashboard.test/actions',runScripts:'outside-only'});
 const w=dom.window;const requests=[];
 w.CPE_THEME_CONFIG={csrf:'test-csrf'};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
 w.HTMLDialogElement.prototype.close=function(){this.open=false};
 w.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,complete:(payload,status=202)=>resolve({ok:status<400,json:async()=>payload})}));
 w.eval(fs.readFileSync(path.join(__dirname,'../resources/static/portal.js'),'utf8'));
 const el=id=>w.document.getElementById(id);
 const type=value=>{el('restartConfirmation').value=value;el('restartConfirmation').dispatchEvent(new w.Event('input'))};
 const submit=()=>el('restartForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 return {dom,w,requests,el,type,submit};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('restart requires exact confirmation and cancel sends nothing',()=>{
 const {dom,requests,el,type,submit}=setup();
 try{el('restartRouter').click();assert.equal(el('restartDialog').open,true);assert.equal(el('confirmRestart').disabled,true);
 type('restart');submit();assert.equal(requests.length,0);el('cancelRestart').click();assert.equal(el('restartDialog').open,false);
 }finally{dom.window.close()}
});
test('restart sends CSRF once and blocks duplicate submissions while pending',async()=>{
 const {dom,requests,el,type,submit}=setup();
 try{el('restartVendor').innerHTML='<option value="huawei">Huawei</option>';el('restartRouter').click();type('RESTART');submit();submit();
 assert.equal(requests.length,1);assert.equal(requests[0].options.headers['X-CSRF-Token'],'test-csrf');
 assert.deepEqual(JSON.parse(requests[0].options.body),{vendor:'huawei',confirmation:'RESTART'});
 assert.equal(el('restartRouter').disabled,true);requests[0].complete({status:'unknown',message:'Check router status before retrying'});await tick();
 assert.match(el('actionStatus').textContent,/Check router/);assert.equal(el('actionStatus').classList.contains('error'),true);
 assert.equal(requests.length,1);
 }finally{dom.window.close()}
});
test('server errors display as text and allow a later deliberate retry',async()=>{
 const {dom,requests,el,type,submit}=setup();
 try{el('restartRouter').click();type('RESTART');submit();requests[0].complete({message:'<b>Forbidden</b>'},403);await tick();
 assert.equal(el('actionStatus').textContent,'<b>Forbidden</b>');assert.equal(el('actionStatus').querySelector('b'),null);assert.equal(el('restartRouter').disabled,false);
 }finally{dom.window.close()}
});
