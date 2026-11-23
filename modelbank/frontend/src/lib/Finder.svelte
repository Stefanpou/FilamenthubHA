<!-- Finds .3mf/.stl files in folders the user picks on this computer and uploads the new ones.
     A web page cannot read the whole disk; the user chooses the folders (File System Access API in Chrome/Edge, which
     can remember them for "Scan igen"; elsewhere a folder upload field). Nothing is sent until "Upload" is pressed. -->
<script>
  import Icon from './Icon.svelte';
  import { get, bytes, date } from './api.js';
  import { enqueue, uq } from './uploads.svelte.js';
  import { onMount } from 'svelte';

  let { folders = [], toast, onClose } = $props();
  const fsApi = typeof window.showDirectoryPicker === 'function';
  const OK = /\.(3mf|stl)$/i;
  const SKIP = /^(\.|\$)|^(node_modules|appdata|application data|program files( \(x86\))?|programdata|windows|library|system volume information|temp|tmp|cache|caches)$/i;

  let found = $state([]);          // {key, name, path, size, mtime, root, status: 'new'|'known'|'dup', pick, get}
  let scanning = $state(false), seen = $state(0), saved = $state([]), stopFlag = false;
  let onlyNew = $state(true), keepTree = $state(true), dest = $state('');
  let input;

  // ---- remembered folders (Chrome/Edge): directory handles in IndexedDB
  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open('modelbank-finder', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('dirs');
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
  }
  async function loadSaved() {
    if (!fsApi) return;
    try {
      const db = await idb();
      saved = await new Promise((res) => {
        const out = [], tx = db.transaction('dirs').objectStore('dirs').openCursor();
        tx.onsuccess = () => { const c = tx.result; if (c) { out.push(c.value); c.continue(); } else res(out); };
        tx.onerror = () => res([]);
      });
    } catch { saved = []; }
  }
  async function remember(h) {
    try {
      const db = await idb();
      for (const s of saved) if (await s.isSameEntry?.(h)) return;
      db.transaction('dirs', 'readwrite').objectStore('dirs').put(h, h.name + ':' + Date.now());
      await loadSaved();
    } catch { /* not remembered, still works now */ }
  }
  async function forget(h) {
    const db = await idb();
    await new Promise((res) => {
      const st = db.transaction('dirs', 'readwrite').objectStore('dirs'), c = st.openCursor();
      c.onsuccess = async () => { const cur = c.result; if (!cur) return res(); if (cur.value === h || (await cur.value.isSameEntry(h))) cur.delete(); cur.continue(); };
    });
    await loadSaved();
  }
  onMount(loadSaved);

  // ---- scanning
  let known = new Map();
  async function fingerprints() {
    const r = await get('api/fingerprints');
    known = new Map(r.files.map((f) => [`${f.name.toLowerCase()}|${f.size}`, f.id]));
  }
  function add(name, path, size, mtime, root, getter) {
    const key = `${name.toLowerCase()}|${size}`;
    const status = known.has(key) ? 'known' : found.some((f) => f.key === key) ? 'dup' : 'new';
    found.push({ key, name, path, size, mtime, root, status, pick: status === 'new', get: getter });
  }
  async function walk(dir, path, root) {
    for await (const [name, h] of dir.entries()) {
      if (stopFlag) return;
      if (h.kind === 'directory') {
        if (SKIP.test(name)) continue;
        await walk(h, path ? `${path}/${name}` : name, root);
      } else if (OK.test(name)) {
        seen++;
        try { const f = await h.getFile(); add(name, path, f.size, f.lastModified, root, () => h.getFile()); } catch { /* unreadable */ }
      }
    }
  }
  async function scanHandles(handles) {
    scanning = true; stopFlag = false; seen = 0;
    try {
      await fingerprints();
      for (const h of handles) {
        if ((await h.queryPermission?.({ mode: 'read' })) !== 'granted' && (await h.requestPermission?.({ mode: 'read' })) !== 'granted') {
          toast(`Ingen adgang til ${h.name}`, true); continue;
        }
        found = found.filter((f) => f.root !== h.name);
        await walk(h, '', h.name);
      }
    } catch (e) { toast(e.message || 'Kunne ikke læse mappen', true); }
    finally { scanning = false; }
  }
  async function pick() {
    if (!fsApi) { input.click(); return; }
    let h;
    try { h = await window.showDirectoryPicker({ id: 'modelbank', mode: 'read' }); }
    catch (e) {
      if (e.name === 'AbortError') return;
      if (e.name === 'SecurityError' || e.name === 'NotAllowedError' && /frame|gesture/i.test(e.message)) { input.click(); return; }
      toast('Browseren tillader ikke den mappe – vælg en undermappe, fx Dokumenter eller Downloads', true); return;
    }
    await remember(h);
    await scanHandles([h]);
  }
  async function fromInput(e) {
    const files = [...e.target.files];
    e.target.value = '';
    if (!files.length) return;
    scanning = true; seen = 0;
    try {
      await fingerprints();
      for (const f of files) {
        const parts = (f.webkitRelativePath || f.name).split('/');
        const root = parts.length > 1 ? parts[0] : '';
        const rel = parts.slice(1, -1);
        if (!OK.test(f.name) || rel.some((p) => SKIP.test(p))) continue;
        seen++;
        add(f.name, rel.join('/'), f.size, f.lastModified, root, async () => f);
      }
    } finally { scanning = false; }
  }

  // ---- upload
  const clean = (p) => p.replace(/[\x00-\x1f<>:"|?*\\]/g, '_').replace(/^[.\s]+/, '').trim().slice(0, 120);
  const target = (f) => {
    const parts = [dest, ...(keepTree ? [f.root, ...f.path.split('/')] : [])].flatMap((p) => (p || '').split('/')).map(clean).filter(Boolean);
    return parts.join('/');
  };
  function send() {
    const list = found.filter((f) => f.pick);
    if (!list.length) return;
    for (const f of list) f.pick = false;
    enqueue(list.map((f) => ({ name: f.name, size: f.size, folder: target(f), mtime: f.mtime, get: f.get,
                               onOk: () => { f.status = 'known'; } })));
    toast(`${list.length} ${list.length === 1 ? 'fil' : 'filer'} sat i kø – se status nederst til højre`);
  }

  const shown = $derived(onlyNew ? found.filter((f) => f.status === 'new') : found);
  const picked = $derived(found.filter((f) => f.pick));
  const counts = $derived({ new: found.filter((f) => f.status === 'new').length, known: found.filter((f) => f.status === 'known').length,
                            dup: found.filter((f) => f.status === 'dup').length });
  const allOn = $derived(shown.length > 0 && shown.every((f) => f.pick || f.status !== 'new'));
  const toggleAll = () => { const v = !allOn; for (const f of shown) if (f.status === 'new') f.pick = v; };
</script>

<div class="page stack">
  <div class="row between wrap"><h1>Find modeller på computeren</h1><button class="btn ghost" onclick={onClose}><Icon name="back" size={18} /> Tilbage</button></div>
  <p class="small dim">Vælg en eller flere mapper, fx <b>Dokumenter</b>, <b>Downloads</b> eller din BambuStudio-mappe. Modelbank
    gennemsøger dem med alle undermapper og finder .3mf- og .stl-filer. Intet sendes, før du trykker <b>Upload</b>.
    Af sikkerhedshensyn kan en webside ikke selv gennemsøge hele computeren, og browseren kan nægte enkelte systemmapper.</p>

  <section class="glass pad stack">
    <div class="row wrap">
      <button class="btn primary" onclick={pick} disabled={scanning}><Icon name="plus" size={18} /> Vælg mappe</button>
      {#if saved.length}<button class="btn" onclick={() => scanHandles(saved)} disabled={scanning}><Icon name="refresh" size={18} /> Scan gemte mapper igen</button>{/if}
      {#if scanning}<span class="row small dim"><span class="spinner"></span> Søger … {seen} fundet <button class="btn ghost small" onclick={() => (stopFlag = true)}>Stop</button></span>{/if}
      <input type="file" webkitdirectory multiple hidden bind:this={input} onchange={fromInput}>
    </div>
    {#if saved.length}
      <div class="row wrap small">
        <span class="muted">Gemte mapper:</span>
        {#each saved as h}<span class="chip on">{h.name}<button class="x" aria-label={`Glem ${h.name}`} onclick={() => forget(h)}>×</button></span>{/each}
      </div>
    {/if}
    {#if !fsApi}<div class="tiny muted">Din browser kan ikke huske mapper – brug Chrome eller Edge for »Scan igen«. Browseren spørger om lov til at »uploade« mappen; det betyder kun, at siden må læse den.</div>{/if}
  </section>

  {#if found.length}
    <section class="glass pad stack">
      <div class="row between wrap">
        <div class="row wrap small">
          <span class="badge ok">{counts.new} nye</span><span class="badge">{counts.known} findes allerede</span>
          {#if counts.dup}<span class="badge shadow">{counts.dup} dubletter</span>{/if}
          <label class="row dim"><input type="checkbox" bind:checked={onlyNew}> Vis kun nye</label>
        </div>
        <button class="btn ghost small" onclick={toggleAll}>{allOn ? 'Fravælg alle' : 'Vælg alle nye'}</button>
      </div>
      <div class="list">
        {#each shown as f (f.root + '/' + f.path + '/' + f.name)}
          <label class="row frow" class:off={f.status !== 'new'}>
            <input type="checkbox" bind:checked={f.pick} disabled={f.status !== 'new'}>
            <span class="grow stack" style="gap:0">
              <b class="ellipsis">{f.name}</b>
              <span class="tiny muted ellipsis mono">{[f.root, f.path].filter(Boolean).join('/')}</span>
            </span>
            <span class="tiny muted mono nowrap">{bytes(f.size)} · {date(f.mtime / 1000)}</span>
            {#if f.status === 'known'}<span class="badge">Findes</span>{:else if f.status === 'dup'}<span class="badge shadow">Dublet</span>{/if}
          </label>
        {:else}<div class="small muted">Ingen nye filer – alt findes allerede i Modelbank.</div>{/each}
      </div>
      <div class="grid2">
        <label class="f">Læg dem i
          <select class="input" bind:value={dest}>
            <option value="">(Rodmappen)</option>
            {#each folders as fo}<option value={fo.path}>{fo.path}</option>{/each}
          </select></label>
        <label class="row small dim keep"><input type="checkbox" bind:checked={keepTree}> Behold mappestrukturen fra computeren</label>
      </div>
      {#if picked.length}<div class="tiny muted">Eksempel: <span class="mono">/share/modelbank/{target(picked[0]) ? target(picked[0]) + '/' : ''}{picked[0].name}</span></div>{/if}
      <div><button class="btn primary big" disabled={!picked.length} onclick={send}><Icon name="upload" size={18} /> Upload {picked.length} {picked.length === 1 ? 'fil' : 'filer'}</button></div>
    </section>
  {/if}
</div>

<style>
  .page { gap: 18px; max-width: 1000px; }
  .list { display: flex; flex-direction: column; gap: 4px; max-height: 50vh; overflow: auto; }
  .frow { gap: 10px; padding: 6px 8px; border-radius: 8px; cursor: pointer; min-width: 0; }
  .frow:hover { background: rgba(237,237,237,.04); }
  .frow.off { opacity: .55; cursor: default; }
  .nowrap { white-space: nowrap; }
  .keep { align-self: end; min-height: 44px; gap: 8px; }
  .chip .x { background: none; border: 0; color: var(--muted); cursor: pointer; padding: 0 0 0 4px; font-size: 16px; line-height: 1; }
</style>
