// One upload queue for the whole app (drag & drop, the upload button and "Find på computeren"), shown by UploadPanel.
// Files go one at a time; progress is tracked in bytes so the overall bar and the time left are honest.
import { ApiError } from './api.js';

export const uq = $state({ items: [], running: false, open: true, finishedAt: 0 });
let xhr = null, samples = [], listeners = [];

/** Called with {ok, failed} every time the queue has finished. */
export function onQueueDone(fn) { listeners.push(fn); return () => (listeners = listeners.filter((x) => x !== fn)); }

/** files: [{name, size, folder, get: async () => File, mtime?, onOk?}] */
export function enqueue(files) {
  for (const f of files) {
    uq.items.push({ id: Math.random().toString(36).slice(2), name: f.name, size: f.size || 0, folder: f.folder || '',
                    get: f.get, mtime: f.mtime, onOk: f.onOk, state: 'wait', loaded: 0, err: '' });
  }
  uq.open = true;
  uq.finishedAt = 0;
  if (!uq.running) run();
}

// Home Assistant's Ingress proxy refuses request bodies over 16 MB, so bigger files go in 8 MB pieces.
const SINGLE_MAX = 12 * 1024 * 1024;
let aborted = false;

function xhrPost(url, body, onProgress) {
  return new Promise((resolve, reject) => {
    xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', body instanceof Blob || body instanceof ArrayBuffer ? 'application/octet-stream' : 'application/json');
    if (onProgress) xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded);
    xhr.onload = () => {
      let d = null;
      try { d = JSON.parse(xhr.responseText); } catch { /* */ }
      if (xhr.status < 300) resolve(d);
      else reject(Object.assign(new ApiError((d && d.detail) || `Fejl ${xhr.status}`, xhr.status), { data: d }));
    };
    xhr.onerror = () => reject(new ApiError('Forbindelsen blev afbrudt', 0));
    xhr.onabort = () => reject(new ApiError('Stoppet', -1));
    xhr.send(body);
  });
}

async function sendChunked(file, folder, mtime, onProgress) {
  const t = mtime || file.lastModified;
  const st = await xhrPost('api/upload/start', JSON.stringify({ name: file.name, folder, size: file.size, mtime: t ? Math.floor(t / 1000) : null }));
  const id = st.upload_id, cs = st.chunk_size || 8 * 1024 * 1024;
  let off = 0, tries = 0;
  try {
    while (off < file.size) {
      if (aborted) throw new ApiError('Stoppet', -1);
      try {
        const r = await xhrPost(`api/upload/${id}/chunk?offset=${off}`, file.slice(off, off + cs), (n) => onProgress(off + n));
        off = r.received;
        tries = 0;
      } catch (e) {
        if (e.status === -1) throw e;
        if (e.status === 409 && e.data?.received != null) { off = e.data.received; continue; }   // server is elsewhere: go on from there
        if (++tries > 4 || (e.status >= 400 && e.status < 500)) throw e;
        await new Promise((r) => setTimeout(r, 1000 * 2 ** tries));                                 // network hiccup: retry this piece
      }
    }
    onProgress(file.size);
    return await xhrPost(`api/upload/${id}/finish`, '{}');
  } catch (e) {
    fetch(`api/upload/${id}`, { method: 'DELETE' }).catch(() => {});
    throw e;
  }
}

function send(file, folder, mtime, onProgress) {
  if (file.size > SINGLE_MAX) return sendChunked(file, folder, mtime, onProgress);
  return new Promise((resolve, reject) => {
    xhr = new XMLHttpRequest();
    const t = mtime || file.lastModified;
    xhr.open('POST', `api/upload?name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(folder)}${t ? `&mtime=${Math.floor(t / 1000)}` : ''}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded);
    xhr.onload = () => {
      let d = null;
      try { d = JSON.parse(xhr.responseText); } catch { /* */ }
      if (xhr.status < 300) resolve(d);
      else reject(new ApiError((d && d.detail) || `Fejl ${xhr.status}`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('Forbindelsen blev afbrudt', 0));
    xhr.onabort = () => reject(new ApiError('Stoppet', -1));
    xhr.send(file);
  });
}

const PRECHECK_MAX = 150 * 1024 * 1024;   // hash in the browser up to this size; bigger files are checked by the server

async function sha256(file) {
  if (!crypto?.subtle || file.size > PRECHECK_MAX) return null;
  const d = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function run() {
  uq.running = true;
  aborted = false;
  samples = [];
  let it;
  while ((it = uq.items.find((x) => x.state === 'wait'))) {
    it.state = 'send';
    try {
      const file = await it.get();
      it.size = file.size;
      // exactly this file already in Modelbank? then it is not sent again
      it.state = 'check';
      const sha = await sha256(file).catch(() => null);
      if (sha) {
        const r = await fetch(`api/exists?sha=${sha}`).then((x) => x.json()).catch(() => null);
        if (r?.exists) { dup(it, r.relpath); continue; }
      }
      it.state = 'send';
      const res = await send(file, it.folder, it.mtime, (n) => {
        it.loaded = n;
        if (n >= it.size) it.state = 'process';       // all bytes sent: the server now reads the model
        sample();
      });
      if (res?.duplicate) { dup(it, res.relpath); continue; }
      it.loaded = it.size;
      it.state = 'done';
      it.onOk?.();
    } catch (e) {
      it.state = e.status === -1 ? 'stopped' : 'error';
      it.err = e.message || 'Fejl';
    }
    xhr = null;
    sample();
  }
  uq.running = false;
  uq.finishedAt = Date.now();
  const ok = uq.items.filter((x) => x.state === 'done').length;
  const failed = uq.items.filter((x) => x.state === 'error').length;
  const dups = uq.items.filter((x) => x.state === 'dup').length;
  for (const fn of listeners) fn({ ok, failed, dups });
}

function dup(it, relpath) {
  it.state = 'dup';
  it.loaded = it.size;
  it.err = relpath ? `Findes allerede: ${relpath}` : 'Findes allerede';
  it.onOk?.();
  xhr = null;
  sample();
}

export function stop() {
  aborted = true;
  for (const it of uq.items) if (it.state === 'wait') { it.state = 'stopped'; it.err = 'Stoppet'; }
  xhr?.abort();
}

export function clear() {
  if (uq.running) return;
  uq.items = [];
}

function doneBytes() {
  return uq.items.reduce((a, x) => a + (['done', 'process', 'dup', 'error'].includes(x.state) ? x.size : x.state === 'send' ? x.loaded : 0), 0);
}

function sample() {
  const now = performance.now();
  samples.push([now, doneBytes()]);
  while (samples.length > 2 && now - samples[0][0] > 8000) samples.shift();
}

/** Bytes per second over the last ~8 s (null until there is enough to say). */
export function speed() {
  if (samples.length < 2) return null;
  const [t0, b0] = samples[0], [t1, b1] = samples[samples.length - 1];
  return t1 - t0 > 500 ? ((b1 - b0) * 1000) / (t1 - t0) : null;
}

export function totals() {
  const active = uq.items.filter((x) => x.state !== 'stopped');
  const total = active.reduce((a, x) => a + x.size, 0);
  return { total, done: doneBytes(), files: active.length, filesDone: active.filter((x) => ['done', 'error', 'dup'].includes(x.state)).length };
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (e) => { if (uq.running) { e.preventDefault(); e.returnValue = ''; } });
}
