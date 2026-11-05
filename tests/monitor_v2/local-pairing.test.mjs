import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const pause=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(name) {
  const nodes=new Map(), listeners={}, calls=[], pending=[], navigation=[];
  const node=id=>{if(!nodes.has(id)) nodes.set(id,{value:'console-code',disabled:false,textContent:'',
    addEventListener:(name,fn)=>listeners[id+name]=fn,querySelector:()=>node('button')});return nodes.get(id);};
  const document={hidden:false,querySelector:node,addEventListener:(name,fn)=>listeners[name]=fn};
  const window={addEventListener:(name,fn)=>listeners[name]=fn};
  let timeout;
  const fetch=(path,options)=>{calls.push({path,options});return new Promise(resolve=>pending.push(resolve));};
  vm.runInNewContext(readFileSync(new URL(`../../oneil_runtime_v2/web/${name}.mjs`,import.meta.url),'utf8'),
    {document,window,fetch,AbortController,location:{replace:path=>navigation.push(path)},
      setTimeout:fn=>{timeout=fn;return 1;},clearTimeout:()=>{}});
  const start=()=>listeners[name==='pair'?'#pairsubmit':'#logoutclick']({preventDefault(){},target:node('#logout')});
  const resolve=(ok=true,data={csrf_token:'csrf'})=>pending.shift()({ok,json:async()=>data});
  return {start,resolve,calls,navigation,document,listeners,node,expire:()=>timeout()};
}
for(const name of ['pair','logout']) {
  test(`${name} repeated event is fenced; failure never retries`,async()=>{
    const f=fixture(name);const first=f.start();await f.start();assert.equal(f.calls.length,1);
    f.resolve(false);await first;assert.equal(f.calls.length,1);assert.deepEqual(f.navigation,[]);
  });
  for(const reason of ['hidden','pagehide','timeout']) test(`${name} ${reason} discards late success`,async()=>{
    const f=fixture(name);const operation=f.start();
    if(reason==='hidden'){f.document.hidden=true;f.listeners.visibilitychange();}
    else if(reason==='pagehide') f.listeners.pagehide(); else f.expire();
    assert.equal(f.calls[0].options.signal.aborted,true);
    f.resolve();await operation;assert.deepEqual(f.navigation,[]);assert.equal(f.calls.length,1);
  });
}
test('logout sends CSRF then navigates only after confirmed revocation',async()=>{
  const f=fixture('logout');const operation=f.start();f.resolve();await pause();
  assert.equal(f.calls.length,2);assert.deepEqual(JSON.parse(f.calls[1].options.body),{csrf_token:'csrf'});
  assert.deepEqual(f.navigation,[]);f.resolve();await operation;assert.deepEqual(f.navigation,['/pair.html']);
});
test('logout does not mutate when bootstrap lacks CSRF',async()=>{
  const f=fixture('logout');const operation=f.start();f.resolve(true,{});await operation;
  assert.equal(f.calls.length,1);assert.deepEqual(f.navigation,[]);
});
test('pair clears input and navigates only after success',async()=>{
  const f=fixture('pair');const operation=f.start();assert.equal(f.node('#code').value,'');
  assert.deepEqual(f.navigation,[]);f.resolve();await operation;assert.deepEqual(f.navigation,['/']);
});
