// Shared live connection to /api/live (Server-Sent Events). One EventSource for any number of PrinterView
// instances; closed when the last one goes away. Falls back to polling /api/live/snapshot.
const state = $state({ data: null, status: 'connecting', lastUpdate: 0 });
let es = null;
let timer = null;
let users = 0;
let failures = 0;

// Home Assistant kiosk passes ?token=... on the page URL; EventSource/fetch cannot set headers, so forward it.
export function withToken(path) {
  try {
    const t = new URLSearchParams(location.search).get('token');
    if (!t) return path;
    return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t);
  } catch { return path; }
}

function apply(d) {
  state.data = d;
  state.status = 'live';
  state.lastUpdate = Date.now();
  failures = 0;
}

async function poll() {
  try {
    const r = await fetch(withToken('api/live/snapshot'), { credentials: 'same-origin', cache: 'no-store' });
    if (r.status === 401) { state.status = 'unauthorized'; return; }
    if (!r.ok) throw new Error(String(r.status));
    apply(await r.json());
  } catch { state.status = 'offline'; }
}

function startPolling() {
  stopStream();
  if (timer) return;
  poll();
  timer = setInterval(poll, 3000);
}

function stopStream() {
  if (es) { es.close(); es = null; }
}

function connect() {
  if (typeof EventSource === 'undefined') { startPolling(); return; }
  stopStream();
  es = new EventSource(withToken('api/live'), { withCredentials: true });
  es.addEventListener('state', (ev) => {
    try { apply(JSON.parse(ev.data)); } catch { /* ignore a broken frame */ }
  });
  es.onerror = () => {
    failures += 1;
    state.status = 'reconnecting';
    // A 401 or a proxy that buffers SSE never recovers: after a few failures use plain polling (also detects 401).
    if (failures >= 3) { stopStream(); startPolling(); }
  };
}

function visibility() {
  if (document.hidden) {
    stopStream();
    if (timer) { clearInterval(timer); timer = null; }
  } else if (users > 0 && !es && !timer) {
    failures = 0;
    connect();
  }
}

export function useLive() {
  users += 1;
  if (users === 1) {
    document.addEventListener('visibilitychange', visibility);
    connect();
  }
  return () => {
    users -= 1;
    if (users === 0) {
      document.removeEventListener('visibilitychange', visibility);
      stopStream();
      if (timer) { clearInterval(timer); timer = null; }
    }
  };
}

export const live = state;
