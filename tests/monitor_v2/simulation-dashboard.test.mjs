import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
const source=readFileSync(new URL('../../oneil_runtime_v2/web/simulation.mjs',import.meta.url),'utf8');
const pause=()=>new Promise(resolve=>setImmediate(resolve));
function fixture() {
  const nodes=new Map(); const listeners={}; let delayed=null;
  const get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:true,value:id==='proposals'?'proposal-id':'',replaceChildren(...items){this.items=items;this.value=items[0]?.value||'';}});return nodes.get(id);};
  const document={hidden:false,getElementById:get,createElement:()=>({}),addEventListener:(name,fn)=>listeners[name]=fn};
  let mode='ok';const calls=[];
  const fetch=async(path,options)=>{
    calls.push({path,options});
    if(path==='/api/v2/previews')return await new Promise(resolve=>delayed=resolve);
    if(mode==='unauthorized')return {ok:false,status:401,json:async()=>({error:'AUTHENTICATION_REQUIRED'})};
    const value=path==='/api/v2/session'?{mode:'LOCAL_SQLITE_SIMULATION_ONLY',scopes:['monitor.read','simulation.command'],csrf_token:'test'}:
      {mode:'LOCAL_SQLITE_SIMULATION_ONLY',live_capable:false,production_identity_available:false,runtime_phase:'ACTIVE_SIMULATION',halted:false,control_revision:2,proposals:[{proposal_id:'proposal-id'}],stop_requested:false};
    return {ok:true,status:200,json:async()=>value};
  };
  vm.runInNewContext(source,{document,fetch,crypto:webcrypto,Date,Error,JSON,setInterval:()=>0});
  const resolvePreview=(status=201,malformed=false)=>delayed({ok:status===201,status,json:async()=>{
    if(malformed)throw new Error('INVALID_JSON');
    return {confirmation_id:'confirmation',content_digest:'digest',expires_at:new Date(Date.now()+60000).toISOString(),display:{quantity:10}};
  }});
  return {get,document,listeners,calls,resolvePreview,unauthorize:()=>mode='unauthorized'};
}
test('discard fences a delayed exact preview',async()=>{
  const f=fixture();await pause();await pause();
  const operation=f.get('preview').onclick();await pause();
  f.get('dismiss').onclick();f.resolvePreview();await operation;
  assert.equal(f.get('confirm').disabled,true);
  assert.equal(f.get('exact').textContent,'No preview selected');
});
test('hidden document cannot resurrect pending confirmation',async()=>{
  const f=fixture();await pause();await pause();
  const operation=f.get('preview').onclick();await pause();
  f.document.hidden=true;f.listeners.visibilitychange();f.resolvePreview();await operation;
  assert.equal(f.get('confirm').disabled,true);assert.equal(f.get('preview').disabled,true);
});
test('preview sends reference only and requires a separate explicit confirmation',async()=>{
  const f=fixture();await pause();await pause();
  const operation=f.get('preview').onclick();await pause();f.resolvePreview();await operation;
  assert.equal(f.get('confirm').disabled,false);
  const request=f.calls.find(x=>x.path==='/api/v2/previews');
  assert.deepEqual(JSON.parse(request.options.body),{action:'policy_order',expected_revision:2,proposal_id:'proposal-id'});
  assert.equal(request.options.credentials,'same-origin');
  assert.equal(f.calls.some(x=>x.path==='/api/v2/commands'),false);
});
test('authentication failure clears old displayed state and command controls',async()=>{
  const f=fixture();await pause();await pause();f.unauthorize();await f.get('refresh').onclick();
  assert.equal(f.get('preview').disabled,true);assert.equal(f.get('halt').disabled,true);
  assert.equal(f.get('confirm').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);
});
test('diagnostic absence is explicit and cannot start broker access',async()=>{
  const f=fixture();await pause();await pause();
  assert.match(f.get('kis-diagnostics').textContent,/No diagnostic source/);
  assert.deepEqual(f.calls.map(c=>c.path),['/api/v2/session','/api/v2/host-state']);
});
test('diagnostic snapshot is cleared when authentication fails',async()=>{
  const f=fixture();await pause();await pause();f.unauthorize();await f.get('refresh').onclick();
  assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
});
test('hidden page clears the old diagnostic projection',async()=>{
  const f=fixture();await pause();await pause();
  f.document.hidden=true;f.listeners.visibilitychange();
  assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
});
test('preview authentication failure clears diagnostics even with malformed JSON',async()=>{
  for(const status of [401,403])for(const malformed of [false,true]) {
    const f=fixture();await pause();await pause();
    const operation=f.get('preview').onclick();await pause();
    f.resolvePreview(status,malformed);await operation;
    assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
    assert.match(f.get('status').textContent,/Unavailable/);
    assert.equal(f.get('confirm').disabled,true);
  }
});
test('command authentication failure clears diagnostics without a refresh',async()=>{
  const f=fixture();await pause();await pause();f.unauthorize();
  await f.get('halt').onclick();
  assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
  assert.equal(f.get('halt').disabled,true);
});
