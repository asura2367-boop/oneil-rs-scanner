const $ = id => document.getElementById(id);
const SNAPSHOT_AGE_MS = 15000, REQUEST_TIMEOUT_MS = 5000, CLOCK_SKEW_MS = 2000;
let session = null, state = null, pending = null, busy = false, generation = 0, previewGeneration = 0;
let snapshotAt = null, previousClock = null, snapshotLifetime = SNAPSHOT_AGE_MS;
const write = (id, value) => { $(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
const token = () => crypto.randomUUID().replaceAll('-', '');
function discard() { previewGeneration++; pending = null; $('confirm').disabled = true; write('exact', 'No preview selected'); }
function invalidate(message) {
  generation++; discard(); session = null; state = null; snapshotAt = null;
  $('proposals').replaceChildren();
  write('connection', message);
  write('status', 'Unavailable. No prior status is considered current.');
  write('kis-diagnostics', 'Unavailable. Refresh to obtain a new diagnostic snapshot.');
  return generation;
}
function clock() {
  const now = {wall: Date.now(), mono: performance.now()};
  const valid = Number.isFinite(now.wall) && now.wall >= 0 && Number.isFinite(now.mono) && now.mono >= 0 &&
    (!previousClock || (now.wall >= previousClock.wall && now.mono >= previousClock.mono &&
      Math.abs((now.wall - previousClock.wall) - (now.mono - previousClock.mono)) <= CLOCK_SKEW_MS));
  // Reset the observation base after a discontinuity. Recovery still needs an
  // explicit fresh request; an old snapshot or pending confirmation never revives.
  previousClock = valid || (Number.isFinite(now.wall) && Number.isFinite(now.mono)) ? now : null;
  return valid ? now : null;
}
function ageValid(started, now, lifetime = SNAPSHOT_AGE_MS) {
  return started && now && now.wall >= started.wall && now.mono >= started.mono &&
    now.wall - started.wall < lifetime && now.mono - started.mono < lifetime &&
    Math.abs((now.wall - started.wall) - (now.mono - started.mono)) <= CLOCK_SKEW_MS;
}
function fresh() {
  const now = clock();
  if (!now || (snapshotAt && !ageValid(snapshotAt, now, snapshotLifetime))) {
    invalidate('Status expired or the clock changed. Refresh before using simulation controls.');
    return false;
  }
  return !!(session && state && snapshotAt && !document.hidden);
}
function requireFresh() {
  if (!fresh()) throw new Error('FRESH_STATUS_REQUIRED');
}
function controls() {
  const current = fresh();
  const allowed = !busy && current && session.scopes.includes('simulation.command') && !state.stop_requested;
  $('resume').disabled = !allowed || !state.halted;
  $('activate').disabled = !allowed || state.halted || state.runtime_phase !== 'AWAITING_OWNER';
  $('halt').disabled = !allowed; $('cancel').disabled = !allowed;
  $('proposals').disabled = !allowed || state.runtime_phase !== 'ACTIVE_SIMULATION';
  $('preview').disabled = $('proposals').disabled || !$('proposals').value;
  $('confirm').disabled = !allowed || !pending || !Number.isFinite(Date.parse(pending.expires_at)) || Date.parse(pending.expires_at) <= Date.now();
}
async function request(path, body) {
  const current = generation, started = clock();
  if (!started) throw new Error('REQUEST_CLOCK_INVALID');
  const controller = new AbortController();
  const options = {signal: controller.signal, credentials:'same-origin', cache:'no-store', redirect:'error', headers:{}};
  if (body) {
    requireFresh();
    Object.assign(options, {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json',
      'X-CSRF-Token':session.csrf_token,'X-Request-Timestamp':String(Math.floor(Date.now()/1000)),
      'X-Request-Nonce':token(),'Idempotency-Key':token()}});
  }
  const checkDeadline = () => {
    if (current !== generation) throw new Error('REQUEST_SUPERSEDED');
    if (!ageValid(started, clock(), REQUEST_TIMEOUT_MS)) {
      controller.abort(); throw new Error('REQUEST_TIMEOUT');
    }
  };
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error('REQUEST_TIMEOUT')); }, REQUEST_TIMEOUT_MS);
  });
  try {
    return await Promise.race([timeout, (async () => {
      checkDeadline();
      const response = await fetch(path, options);
      checkDeadline();
      if ([401,403].includes(response.status)) invalidate('Authentication is unavailable. Refresh after authentication.');
      const data = await response.json();
      checkDeadline();
      if (body) requireFresh();
      if (!response.ok) throw new Error('REQUEST_FAILED');
      return data;
    })()]);
  } finally { clearTimeout(timer); }
}
async function refresh() {
  const current = invalidate('Refreshing. No previous snapshot is current.');
  controls();
  const started = clock();
  try {
    if (!started || document.hidden) throw new Error('FRESH_STATUS_REQUIRED');
    const auth = await request('/api/v2/session');
    if (current !== generation || document.hidden) return;
    const observed = await request('/api/v2/host-state');
    if (current !== generation || document.hidden) return;
    if (!ageValid(started, clock())) throw new Error('STATUS_EXPIRED');
    if (auth.mode !== 'LOCAL_SQLITE_SIMULATION_ONLY' || observed.mode !== 'LOCAL_SQLITE_SIMULATION_ONLY' ||
      observed.live_capable !== false || observed.production_identity_available !== false ||
      !Array.isArray(auth.scopes) || !Array.isArray(observed.proposals)) throw new Error('UNEXPECTED_HOST_MODE');
    const diagnostic = observed.kis_diagnostics;
    snapshotLifetime = SNAPSHOT_AGE_MS;
    if (diagnostic?.current) {
      const ttl = diagnostic.valid_for_seconds;
      if (typeof ttl !== 'number' || !Number.isFinite(ttl) || ttl <= 0 || ttl > 300) throw new Error('DIAGNOSTIC_EXPIRY_INVALID');
      snapshotLifetime = Math.min(SNAPSHOT_AGE_MS, ttl * 1000);
    }
    if (!ageValid(started, clock(), snapshotLifetime)) throw new Error('STATUS_EXPIRED');
    session=auth; state=observed; snapshotAt=started;
    write('connection','Authenticated local simulation snapshot. Expires within 15 seconds; refresh explicitly. Production identity is unavailable.');
    write('status',state);
    write('kis-diagnostics',state.kis_diagnostics || 'No diagnostic source is attached.');
    $('proposals').replaceChildren(...state.proposals.map(p => {const option=document.createElement('option');option.value=p.proposal_id;option.textContent=p.proposal_id;return option;}));
  } catch(error) {
    if (current === generation) invalidate('Status refresh failed. No automatic retry was made.');
  } finally { controls(); }
}
async function action(work, needsFresh = true) {
  if (busy) return; busy=true; controls();
  const current = generation;
  try { if (needsFresh) requireFresh(); await work(); } catch(error) {
    if (current === generation) invalidate('The simulation request did not complete with a confirmed current result. Refresh to inspect state.');
    write('receipt','Request failed or expired. No automatic retry was made. For an unknown result, preserve the original request and inspect durable state.');
  } finally { busy=false; controls(); }
}
async function preview(kind) {
  requireFresh(); discard(); const current=previewGeneration; const body={action:kind,expected_revision:state.control_revision};
  if (kind==='policy_order') body.proposal_id=$('proposals').value;
  const result=await request('/api/v2/previews',body);
  if (current!==previewGeneration || !fresh()) return;
  if (!Number.isFinite(Date.parse(result.expires_at)) || Date.parse(result.expires_at) <= Date.now()) throw new Error('CONFIRMATION_EXPIRED');
  pending={...result,action:kind}; write('exact',result);
}
$('refresh').onclick=()=>action(refresh, false);
$('resume').onclick=()=>action(()=>preview('resume_simulation'));
$('activate').onclick=()=>action(()=>preview('activate_runtime'));
$('preview').onclick=()=>action(()=>preview('policy_order'));
$('proposals').onchange=()=>{discard();controls();};
$('dismiss').onclick=()=>{discard();controls();};
$('confirm').onclick=()=>action(async()=>{
  if (!pending || !Number.isFinite(Date.parse(pending.expires_at)) || Date.parse(pending.expires_at)<=Date.now()) throw new Error('CONFIRMATION_EXPIRED');
  const approved=pending; discard();
  const result=await request('/api/v2/commands',{action:approved.action,confirmation_id:approved.confirmation_id,content_digest:approved.content_digest});
  write('receipt',result); await refresh();
});
$('halt').onclick=()=>action(async()=>{discard();const result=await request('/api/v2/commands',{action:'halt',expected_revision:state.control_revision});write('receipt',result);await refresh();});
$('cancel').onclick=()=>action(async()=>{discard();const result=await request('/api/v2/commands',{action:'cancel_pending',expected_revision:state.control_revision,request_id:$('cancel-id').value.trim()});write('receipt',result);await refresh();});
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){invalidate('Page hidden. Refresh after returning.');controls();}
  else action(refresh, false);
});
window.addEventListener('pagehide',()=>{invalidate('Page left. Refresh after returning.');controls();});
window.addEventListener('pageshow',event=>{if(event.persisted){invalidate('Restored page. Refresh to obtain current state.');controls();}});
setInterval(controls,500);
refresh();
