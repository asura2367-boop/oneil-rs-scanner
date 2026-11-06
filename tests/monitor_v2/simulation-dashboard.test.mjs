import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
const source=readFileSync(new URL('../../oneil_runtime_v2/web/simulation.mjs',import.meta.url),'utf8');
const pause=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({diagnostic} = {}) {
  const nodes=new Map(), listeners={}, windowListeners={}, timers=new Map(), holds=new Map(); let delayed=null, nextTimer=0, interval;
  let wall=Date.now(), mono=1000;
  class TestDate extends Date { static now() { return wall; } }
  const get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:true,value:id==='proposals'?'proposal-id':'',replaceChildren(...items){this.items=items;this.value=items[0]?.value||'';}});return nodes.get(id);};
  const document={hidden:false,getElementById:get,createElement:()=>({}),addEventListener:(name,fn)=>listeners[name]=fn};
  let mode='ok';const calls=[];
  const response=(path,status=200)=>({ok:status>=200&&status<300,status,json:async()=> status!==200 ? {error:'SYNTHETIC_PRIVATE_ERROR'} :
    path==='/api/v2/session'?{mode:'LOCAL_SQLITE_SIMULATION_ONLY',scopes:['monitor.read','simulation.command'],csrf_token:'test'}:
      {mode:'LOCAL_SQLITE_SIMULATION_ONLY',live_capable:false,production_identity_available:false,runtime_phase:'ACTIVE_SIMULATION',halted:false,control_revision:2,proposals:[{proposal_id:'proposal-id'}],stop_requested:false,kis_diagnostics:diagnostic}});
  const fetch=async(path,options)=>{
    calls.push({path,options});
    if(holds.has(path)){const result=holds.get(path);holds.delete(path);return result;}
    if(path==='/api/v2/previews')return await new Promise(resolve=>delayed=resolve);
    return response(path,mode==='unauthorized'?401:200);
  };
  vm.runInNewContext(source,{document,window:{addEventListener:(name,fn)=>windowListeners[name]=fn},
    fetch,crypto:webcrypto,Date:TestDate,performance:{now:()=>mono},AbortController,Error,JSON,
    setInterval:fn=>{interval=fn;return 0;},setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
  const resolvePreview=(status=201,malformed=false,expiresAt=new Date(wall+60000).toISOString())=>delayed({ok:status===201,status,json:async()=>{
    if(malformed)throw new Error('SYNTHETIC_PRIVATE_ERROR');
    return {confirmation_id:'confirmation',content_digest:'digest',expires_at:expiresAt,display:{quantity:10}};
  }});
  return {get,document,listeners,windowListeners,calls,resolvePreview,response,timers,
    unauthorize:()=>mode='unauthorized', advance:(w,m=w)=>{wall+=w;mono+=m;}, tick:()=>interval(),
    holdNext:path=>{let release;holds.set(path,new Promise(resolve=>release=resolve));return(value=response(path))=>release(value);},
    timeout:()=>{for(const [id,timer] of [...timers]){timers.delete(id);timer.fn();}}};
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

test('snapshot expires without another request and clears status, diagnostics, proposals and confirmation',async()=>{
  const f=fixture({diagnostic:{current:true,valid_for_seconds:30,report:{state:'parsed'}}});await pause();await pause();
  const operation=f.get('preview').onclick();await pause();f.resolvePreview();await operation;
  f.advance(15000);f.tick();
  assert.match(f.get('status').textContent,/Unavailable/);assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
  assert.equal(f.get('proposals').value,'');assert.equal(f.get('confirm').disabled,true);assert.equal(f.get('halt').disabled,true);
  assert.equal(f.calls.length,3);
});
test('diagnostic remaining lifetime shortens the complete browser snapshot lifetime',async()=>{
  const f=fixture({diagnostic:{current:true,valid_for_seconds:2,report:{state:'parsed'}}});await pause();await pause();
  f.advance(1999);f.tick();assert.equal(f.get('halt').disabled,false);
  f.advance(1);f.tick();assert.equal(f.get('halt').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);
});
test('current diagnostics require a finite positive bounded lifetime',async()=>{
  for(const ttl of [undefined,null,0,-1,NaN,Infinity,301,'30']) {
    const f=fixture({diagnostic:{current:true,valid_for_seconds:ttl}});await pause();await pause();
    assert.equal(f.get('halt').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);
  }
});
test('refresh clears the previous snapshot before a delayed session request',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/session');
  const operation=f.get('refresh').onclick();await pause();
  assert.match(f.get('status').textContent,/Unavailable/);assert.equal(f.get('halt').disabled,true);
  release();await operation;assert.equal(f.get('halt').disabled,false);
});
test('request transit is included in browser freshness rather than renewing on arrival',async()=>{
  const f=fixture();await pause();await pause();const session=f.holdNext('/api/v2/session'), host=f.holdNext('/api/v2/host-state');
  const operation=f.get('refresh').onclick();await pause();f.advance(4000);session();await pause();f.advance(4000);host();await operation;
  assert.equal(f.get('halt').disabled,false);f.advance(7000);f.tick();assert.equal(f.get('halt').disabled,true);
});
test('delayed host-state beyond snapshot age cannot become current',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/host-state');
  const operation=f.get('refresh').onclick();await pause();f.advance(15000);release();await operation;
  assert.equal(f.get('halt').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);
});
test('frozen, reversed or discontinuous clocks invalidate synchronously and do not recover old state',async()=>{
  for(const deltas of [[15000,0],[0,15000],[-1,1],[1,-1],[3000,0]]) {
    const f=fixture();await pause();await pause();f.advance(...deltas);f.tick();
    assert.equal(f.get('halt').disabled,true);f.advance(1000);f.tick();assert.equal(f.get('halt').disabled,true);
    assert.match(f.get('status').textContent,/Unavailable/);
  }
});
test('event handlers reject stale commands even if the timer did not run',async()=>{
  for(const name of ['resume','activate','preview','halt','cancel','confirm']) {
    const f=fixture();await pause();await pause();f.advance(15000);await f.get(name).onclick();
    assert.equal(f.calls.length,2);assert.equal(f.get('halt').disabled,true);
  }
});
test('snapshot expiry fences a late preview without relying on a timer',async()=>{
  const f=fixture();await pause();await pause();const operation=f.get('preview').onclick();await pause();
  f.advance(15000);f.resolvePreview();await operation;
  assert.equal(f.get('confirm').disabled,true);assert.equal(f.get('exact').textContent,'No preview selected');
});
test('a command response after expiry cannot automatically refresh or revive state',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/commands');
  const operation=f.get('halt').onclick();await pause();f.advance(15000);release();await operation;
  assert.equal(f.calls.length,3);assert.equal(f.get('halt').disabled,true);assert.match(f.get('receipt').textContent,/No automatic retry/);
});
test('hidden page fences command completion and clears both projections',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/commands');
  const operation=f.get('halt').onclick();await pause();f.document.hidden=true;f.listeners.visibilitychange();release();await operation;
  assert.equal(f.calls.length,3);assert.match(f.get('status').textContent,/Unavailable/);assert.match(f.get('kis-diagnostics').textContent,/Unavailable/);
});
test('superseded session response cannot start a host-state read',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/session');
  const operation=f.get('refresh').onclick();await pause();f.document.hidden=true;f.listeners.visibilitychange();release();await operation;
  assert.equal(f.calls.length,3);assert.equal(f.get('halt').disabled,true);
});
test('network timeout releases busy, aborts the request and permits explicit recovery',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/host-state');
  const operation=f.get('refresh').onclick();await pause();const call=f.calls.at(-1);f.timeout();await operation;
  assert.equal(call.options.signal.aborted,true);assert.equal(f.get('halt').disabled,true);
  await f.get('refresh').onclick();assert.equal(f.get('halt').disabled,false);
  release();await pause();assert.equal(f.get('halt').disabled,false);
});
test('body timeout is bounded and a late unauthorized response cannot erase a recovered snapshot',async()=>{
  for(const status of [200,401,403]) {
    const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/host-state');
    const operation=f.get('refresh').onclick();await pause();f.timeout();await operation;
    await f.get('refresh').onclick();assert.equal(f.get('halt').disabled,false);
    release(f.response('/api/v2/host-state',status));await pause();assert.equal(f.get('halt').disabled,false);
  }
});
test('response JSON decoding shares the request timeout budget',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/host-state');
  const operation=f.get('refresh').onclick();await pause();let body;
  release({ok:true,status:200,json:()=>new Promise(resolve=>body=resolve)});await pause();f.timeout();await operation;
  assert.equal(f.get('halt').disabled,true);await f.get('refresh').onclick();body({});await pause();assert.equal(f.get('halt').disabled,false);
});
test('malformed preview expiry never enables confirmation or transmits a command',async()=>{
  const f=fixture();await pause();await pause();const operation=f.get('preview').onclick();await pause();f.resolvePreview(201,false,'invalid');await operation;
  assert.equal(f.get('confirm').disabled,true);await f.get('confirm').onclick();
  assert.equal(f.calls.some(c=>c.path==='/api/v2/commands'),false);
});
test('back-forward cache restore cannot revive cached controls or status',async()=>{
  const f=fixture();await pause();await pause();f.windowListeners.pagehide();f.windowListeners.pageshow({persisted:true});
  assert.equal(f.get('halt').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);assert.equal(f.calls.length,2);
});
test('request failures never display untrusted exception or server error text',async()=>{
  const f=fixture();await pause();await pause();const operation=f.get('preview').onclick();await pause();f.resolvePreview(500,true);await operation;
  for(const id of ['connection','status','receipt','kis-diagnostics'])assert.doesNotMatch(f.get(id).textContent,/SYNTHETIC_PRIVATE_ERROR/);
});

test('overdue fetch or JSON completion is rejected even if the timeout callback has not run',async()=>{
  for(const stage of ['fetch','body']) {
    const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/host-state');
    const operation=f.get('refresh').onclick();await pause();
    if(stage==='fetch'){f.advance(5000);release();}
    else {let body;release({ok:true,status:200,json:()=>new Promise(resolve=>body=resolve)});await pause();f.advance(5000);body({});}
    await operation;assert.equal(f.get('halt').disabled,true);assert.match(f.get('status').textContent,/Unavailable/);
    assert.equal(f.calls.at(-1).options.signal.aborted,true);
  }
});
test('an overdue command cannot report success or start refresh even before timer dispatch',async()=>{
  const f=fixture();await pause();await pause();const release=f.holdNext('/api/v2/commands');
  const operation=f.get('halt').onclick();await pause();f.advance(6000);release();await operation;
  assert.equal(f.calls.length,3);assert.equal(f.get('halt').disabled,true);
  assert.match(f.get('receipt').textContent,/No automatic retry/);
});
