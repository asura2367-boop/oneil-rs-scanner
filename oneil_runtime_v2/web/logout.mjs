let busy = false, abandoned = false, controller = null;
function abandon() { abandoned = true; controller?.abort(); }
window.addEventListener('pagehide', abandon);
document.addEventListener('visibilitychange', () => { if (document.hidden) abandon(); });
document.querySelector('#logout').addEventListener('click', async event => {
  if (busy || abandoned || document.hidden) return;
  busy = true;
  event.target.disabled = true;
  controller = new AbortController();
  const current = controller;
  const timer = setTimeout(() => current.abort(), 5000);
  const valid = () => !abandoned && !current.signal.aborted && !document.hidden;
  try {
    const session = await fetch('/api/v2/session', {credentials:'same-origin', cache:'no-store',
      redirect:'error', signal:current.signal});
    if (!valid()) return;
    if (!session.ok) throw new Error('Session unavailable.');
    const {csrf_token} = await session.json();
    if (!valid()) return;
    if (typeof csrf_token !== 'string' || !csrf_token) throw new Error('CSRF unavailable.');
    const result = await fetch('/api/v2/logout', {method:'POST', credentials:'same-origin', cache:'no-store',
      signal:current.signal, redirect:'error', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csrf_token})});
    if (!valid()) return;
    if (!result.ok) throw new Error('Logout not confirmed. No retry was made.');
    location.replace('/pair.html');
  } catch (error) {
    if (!abandoned) document.querySelector('#result').textContent = 'Logout not confirmed. No automatic retry.';
  } finally { clearTimeout(timer); busy = false; }
});
