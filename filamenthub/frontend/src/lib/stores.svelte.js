// Shared app state (Svelte 5 runes). Import the objects and mutate their fields.
import { api, setUnauthorizedHandler, isKiosk } from './api.js';

// ---- toasts ----
export const toasts = $state([]);
let toastId = 0;
export function toast(message, kind = 'ok', ms) {
  const id = ++toastId;
  toasts.push({ id, message, kind });
  setTimeout(() => { const i = toasts.findIndex((t) => t.id === id); if (i >= 0) toasts.splice(i, 1); }, ms ?? (kind === 'err' ? 5500 : 2600));
}
export const toastErr = (e) => toast(e?.message || String(e), 'err');

// ---- router (hash: #/path?query) ----
export const route = $state({ path: '/', query: {} });
function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [p, qs = ''] = raw.split('?');
  route.path = p.startsWith('/') ? p : '/' + p;
  route.query = Object.fromEntries(new URLSearchParams(qs));
}
export function initRouter() { parseHash(); window.addEventListener('hashchange', parseHash); }
export function go(path, query) {
  const qs = query ? '?' + new URLSearchParams(query).toString() : '';
  location.hash = '#' + path + qs;
}

// ---- auth ----
export const auth = $state({ ready: false, authenticated: false, password_set: true, login_required: true, setup_allowed: false, ingress: false });
export async function loadAuth() {
  try {
    const s = await api('api/auth/state');
    Object.assign(auth, s, { ready: true });
    // The kiosk (/ha) authenticates by itself (token); it never shows the login.
  } catch (e) {
    if (e.status === 401 && inIngress()) { recoverIngress(); return; }   // (the boot spinner stays meanwhile)
    auth.ready = true;
  }
}
setUnauthorizedHandler(() => {
  if (isKiosk()) app.error = 'Mangler adgang – tjek token i URL\'en';
  else if (inIngress()) recoverIngress();
  else auth.authenticated = false;
});

// Inside Home Assistant the app is reached through Ingress, which needs no password here. A 401 there means HA's
// ingress session ran out while the app sat in the background (a minimised phone app): HA's frontend makes a new one
// within about a minute. Wait for it instead of showing the login, and reload the page as a last resort.
const inIngress = () => auth.ingress || location.pathname.includes('/api/hassio_ingress/');
let recovering = false;
async function recoverIngress() {
  if (recovering) return;
  recovering = true;
  app.reconnecting = true;
  const until = Date.now() + 75000;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 3000));
    if (document.hidden) continue;
    try {
      const r = await fetch('api/auth/state', { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok) {
        recovering = false;
        app.reconnecting = false;
        if (!auth.ready || !auth.authenticated) loadAuth(); else reloadAll();
        return;
      }
    } catch { /* still offline */ }
  }
  location.reload();
}

// ---- data ----
export const app = $state({ status: null, spools: null, locations: null, error: '', online: true, reconnecting: false });

let statusBusy = false;
export async function refreshStatus() {
  if (statusBusy) return;
  statusBusy = true;
  try {
    app.status = await api('api/status');
    app.error = '';
    app.online = true;
  } catch (e) {
    if (e.status === 0) app.online = false;
    else if (e.status !== 401) app.error = e.message;
  } finally { statusBusy = false; }
}
export async function refreshSpools() {
  refreshLocations();
  try {
    const list = await api('api/spools?archived=true');
    app.spools = Array.isArray(list) ? list : [];
  } catch (e) {
    if (app.spools === null) app.spools = [];
    if (e.status !== 401) app.error = e.message;
  }
}
// Fixed locations (Indstillinger > Placeringer) with their places, and the free-text locations in use.
export async function refreshLocations() {
  if (isKiosk()) return;   // (not among the kiosk's paths)
  try { app.locations = await api('api/locations'); } catch { /* keeps the last list */ }
}
export const activeSpools = () => (app.spools || []).filter((s) => !s.archived);

let timer = null;
export function startPolling(ms = 5000) {
  stopPolling();
  refreshStatus(); refreshSpools();
  timer = setInterval(() => { if (!document.hidden) { refreshStatus(); } }, ms);
  document.addEventListener('visibilitychange', onVis);
}
function onVis() { if (!document.hidden) { refreshStatus(); refreshSpools(); } }
export function stopPolling() { clearInterval(timer); timer = null; document.removeEventListener('visibilitychange', onVis); }
export async function reloadAll() { await Promise.all([refreshStatus(), refreshSpools()]); }
