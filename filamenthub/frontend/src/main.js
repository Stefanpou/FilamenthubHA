import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

// Reload when a new build has been deployed (the SPA otherwise keeps running the old bundle, e.g. the HA kiosk).
const bundle = () => [...document.scripts].map((x) => x.src).find((x) => x.includes('/assets/index-'));
async function checkVersion() {
  if (document.hidden) return;
  try {
    const html = await (await fetch('./', { cache: 'no-store' })).text();
    const m = html.match(/assets\/index-[^"]+\.js/);
    const mine = bundle();
    if (m && mine && !mine.endsWith(m[0])) location.reload();
  } catch {}
}
setInterval(checkVersion, 60_000);
document.addEventListener('visibilitychange', checkVersion);

export default mount(App, { target: document.getElementById('app') });
