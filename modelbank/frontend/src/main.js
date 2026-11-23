import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';

// Reload when a new version has been installed (the page otherwise keeps running the old code until it is reloaded).
const bundle = () => [...document.scripts].map((x) => x.src).find((x) => x.includes('/assets/index-'));
async function checkVersion() {
  if (document.hidden) return;
  try {
    const html = await (await fetch('./', { cache: 'no-store' })).text();
    const m = html.match(/assets\/index-[^"]+\.js/);
    const mine = bundle();
    if (m && mine && !mine.endsWith(m[0])) location.reload();
  } catch { /* offline */ }
}
setInterval(checkVersion, 60_000);
document.addEventListener('visibilitychange', checkVersion);

export default mount(App, { target: document.getElementById('app') });
