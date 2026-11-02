// Tiny fetch wrapper: JSON in/out, Danish error messages, 401 -> login. Passes ?token= (HA kiosk) through.
let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export const isKiosk = () => location.pathname.replace(/\/+$/, '') === '/ha';

function withPassthrough(path) {
  const extra = new URLSearchParams(location.search);
  if (![...extra.keys()].length) return path;
  const [base, qs = ''] = path.split('?');
  const q = new URLSearchParams(qs);
  for (const [k, v] of extra) if (!q.has(k)) q.set(k, v);
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
}

function detailText(data, status) {
  let msg = data && data.detail;
  if (Array.isArray(msg)) {
    msg = msg.map((d) => { const f = (d.loc || []).slice(1).join('.'); return f ? `${f}: ${d.msg}` : d.msg; }).join(', ');
  }
  if (msg && typeof msg === 'object') msg = msg.message || JSON.stringify(msg);
  if (msg) return String(msg);
  if (status === 404) return 'Ikke fundet';
  if (status === 429) return 'For mange forsøg – vent lidt';
  if (status >= 500) return 'Serverfejl – prøv igen om lidt';
  return `Fejl ${status}`;
}

export async function api(path, opts = {}) {
  const method = opts.method || 'GET';
  const init = { method, headers: {}, credentials: 'same-origin' };
  // DELETE too: the server's guard answers 415 to any non-GET request without a JSON media type.
  if (method !== 'GET' && method !== 'HEAD') {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body ?? {});
  }
  if (opts.signal) init.signal = opts.signal;
  let res;
  try {
    res = await fetch(withPassthrough(path), init);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('Ingen forbindelse til FilamentHub – prøver igen', 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && !path.startsWith('api/auth')) {
    onUnauthorized();
    throw new ApiError('Log ind for at fortsætte', 401, data);
  }
  if (!res.ok) throw new ApiError(detailText(data, res.status), res.status, data);
  return data;
}

export const get = (p, o) => api(p, o);
export const post = (p, body, o) => api(p, { ...o, method: 'POST', body });
export const patch = (p, body, o) => api(p, { ...o, method: 'PATCH', body });
export const del = (p, o) => api(p, { ...o, method: 'DELETE' });
