const $ = id => document.getElementById(id);
let session = null, state = null, pending = null, busy = false, generation = 0, previewGeneration = 0;
const write = (id, value) => { $(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
const token = () => crypto.randomUUID().replaceAll('-', '');
function discard() { previewGeneration++; pending = null; $('confirm').disabled = true; write('exact', 'No preview selected'); }
function controls() {
  const allowed = !busy && session?.scopes.includes('simulation.command') && state && !state.stop_requested;
  $('resume').disabled = !allowed || !state.halted;
  $('activate').disabled = !allowed || state.halted || state.runtime_phase !== 'AWAITING_OWNER';
  $('halt').disabled = !allowed; $('cancel').disabled = !allowed;
  $('proposals').disabled = !allowed || state.runtime_phase !== 'ACTIVE_SIMULATION';
  $('preview').disabled = $('proposals').disabled || !$('proposals').value;
  $('confirm').disabled = !allowed || !pending || Date.parse(pending.expires_at) <= Date.now();
}
async function request(path, body) {
  const options = {credentials:'same-origin', cache:'no-store', redirect:'error', headers:{}};
  if (body) Object.assign(options, {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json',
    'X-CSRF-Token':session.csrf_token,'X-Request-Timestamp':String(Math.floor(Date.now()/1000)),
    'X-Request-Nonce':token(),'Idempotency-Key':token()}});
  const response = await fetch(path, options);
  if ([401,403].includes(response.status)) {
    session=null; state=null;
    write('kis-diagnostics','Unavailable. Refresh after authentication.');
    write('status','Unavailable. No prior status is considered current.');
  }
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || 'REQUEST_FAILED');
  }
  return data;
}
async function refresh() {
  const current = ++generation;
  discard();
  try {
    const auth = await request('/api/v2/session');
    const observed = await request('/api/v2/host-state');
    if (current !== generation) return;
    if (auth.mode !== 'LOCAL_SQLITE_SIMULATION_ONLY' || observed.live_capable !== false || observed.production_identity_available !== false) throw new Error('UNEXPECTED_HOST_MODE');
    session=auth; state=observed;
    write('connection','Authenticated for this local simulation process only. Production identity is unavailable.');
    write('status',state);
    write('kis-diagnostics',state.kis_diagnostics || 'No diagnostic source is attached.');
    $('proposals').replaceChildren(...state.proposals.map(p => {const option=document.createElement('option');option.value=p.proposal_id;option.textContent=p.proposal_id;return option;}));
  } catch(error) {
    if (current !== generation) return;
    session=null; state=null; write('connection',error.message); write('status','Unavailable. No prior status is considered current.');
    write('kis-diagnostics','Unavailable. Refresh after authentication.');
  } finally { controls(); }
}
async function action(work) {
  if (busy) return; busy=true; controls();
  try { await work(); } catch(error) {
    discard(); write('receipt',error.message + '\nNo automatic retry was made. For an unknown result, preserve the original request and inspect durable state.');
  } finally { busy=false; controls(); }
}
async function preview(kind) {
  discard(); const current=previewGeneration; const body={action:kind,expected_revision:state.control_revision};
  if (kind==='policy_order') body.proposal_id=$('proposals').value;
  const result=await request('/api/v2/previews',body);
  if (current!==previewGeneration || !session || document.hidden) return;
  pending={...result,action:kind}; write('exact',result);
}
$('refresh').onclick=()=>action(refresh);
$('resume').onclick=()=>action(()=>preview('resume_simulation'));
$('activate').onclick=()=>action(()=>preview('activate_runtime'));
$('preview').onclick=()=>action(()=>preview('policy_order'));
$('proposals').onchange=()=>{discard();controls();};
$('dismiss').onclick=()=>{discard();controls();};
$('confirm').onclick=()=>action(async()=>{
  if (!pending || Date.parse(pending.expires_at)<=Date.now()) throw new Error('CONFIRMATION_EXPIRED');
  const approved=pending; discard();
  const result=await request('/api/v2/commands',{action:approved.action,confirmation_id:approved.confirmation_id,content_digest:approved.content_digest});
  write('receipt',result); await refresh();
});
$('halt').onclick=()=>action(async()=>{discard();const result=await request('/api/v2/commands',{action:'halt',expected_revision:state.control_revision});write('receipt',result);await refresh();});
$('cancel').onclick=()=>action(async()=>{discard();const result=await request('/api/v2/commands',{action:'cancel_pending',expected_revision:state.control_revision,request_id:$('cancel-id').value.trim()});write('receipt',result);await refresh();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){generation++;discard();session=null;state=null;write('kis-diagnostics','Unavailable. Refresh after returning.');controls();}else action(refresh);});
setInterval(controls,500);
refresh();
