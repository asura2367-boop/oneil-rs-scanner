let busy = false, abandoned = false, controller = null;
function abandon() { abandoned = true; controller?.abort(); }
window.addEventListener('pagehide', abandon);
document.addEventListener('visibilitychange', () => { if (document.hidden) abandon(); });
const form = document.querySelector('#pair');
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || abandoned || document.hidden) return;
  busy = true;
  const button = form.querySelector('button');
  button.disabled = true;
  controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  const current = controller;
  const code = document.querySelector('#code');
  const value = code.value;
  code.value = '';
  try {
    const response = await fetch('/api/v2/pair', {method:'POST', credentials:'same-origin',
      signal:current.signal, redirect:'error', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:value}), cache:'no-store'});
    if (abandoned || current.signal.aborted || document.hidden) return;
    if (!response.ok) throw new Error('Pairing failed or expired. No automatic retry.');
    location.replace('/');
  } catch (error) {
    if (!abandoned) document.querySelector('#result').textContent = 'Pairing not confirmed. No automatic retry.';
  } finally { clearTimeout(timer); busy = false; if (!abandoned) button.disabled = false; }
});
