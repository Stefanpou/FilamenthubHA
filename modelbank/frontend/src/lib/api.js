// fetch wrapper: JSON in/out, Danish error messages. All paths are relative (HA Ingress prefix).
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function detail(data, status) {
  let m = data && data.detail;
  if (Array.isArray(m)) m = m.map((d) => d.msg).join(', ');
  if (m) return String(m);
  if (status === 404) return 'Ikke fundet';
  if (status === 413) return 'Filen er for stor';
  if (status >= 500) return 'Serverfejl – prøv igen om lidt';
  return `Fejl ${status}`;
}

export async function api(path, { method = 'GET', body, raw, type } = {}) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (raw !== undefined) { init.body = raw; init.headers['Content-Type'] = type || 'application/octet-stream'; }
  else if (method !== 'GET') { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body ?? {}); }
  let res;
  try { res = await fetch(path, init); } catch { throw new ApiError('Ingen forbindelse til Modelbank', 0); }
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new ApiError(detail(data, res.status), res.status);
  return data;
}

export const get = (p) => api(p);
export const post = (p, body) => api(p, { method: 'POST', body });
export const patch = (p, body) => api(p, { method: 'PATCH', body });
export const del = (p) => api(p, { method: 'DELETE' });

/** Upload one file with progress (XHR: fetch has no upload progress). */
export function upload(file, folder, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', `api/upload?name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(folder || '')}${file.lastModified ? `&mtime=${Math.floor(file.lastModified / 1000)}` : ''}`);
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    x.onload = () => {
      let d = null;
      try { d = JSON.parse(x.responseText); } catch { /* */ }
      x.status < 300 ? resolve(d) : reject(new ApiError(detail(d, x.status), x.status));
    };
    x.onerror = () => reject(new ApiError('Upload afbrudt', 0));
    x.send(file);
  });
}

export const kr = (v) => (v == null ? '–' : `${Number(v).toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kr`);
export const num = (v, d = 0) => (v == null ? '–' : Number(v).toLocaleString('da-DK', { maximumFractionDigits: d }));
export function dur(sec) {
  if (!sec) return '–';
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)} t ${m % 60} min` : `${m} min`;
}
export function bytes(n) {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / 1024 / 1024).toLocaleString('da-DK', { maximumFractionDigits: 1 })} MB`;
}
export const date = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString('da-DK', { day: 'numeric', month: 'short', year: 'numeric' }) : '–');
