<script>
  import Icon from './lib/Icon.svelte';
  import Detail from './lib/Detail.svelte';
  import Settings from './lib/Settings.svelte';
  import Finder from './lib/Finder.svelte';
  import UploadPanel from './lib/UploadPanel.svelte';
  import { enqueue, onQueueDone } from './lib/uploads.svelte.js';
  import { get, post, num, dur } from './lib/api.js';
  import { requestThumbs } from './lib/thumbs.js';
  import { onMount } from 'svelte';

  // ---- routing (#/, #/m/12, #/settings)
  let hash = $state(location.hash);
  onMount(() => { const f = () => (hash = location.hash); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f); });
  const detailId = $derived((hash.match(/^#\/m\/(\d+)/) || [])[1]);
  const settings = $derived(hash.startsWith('#/settings'));
  const finder = $derived(hash.startsWith('#/find'));
  const go = (h) => { location.hash = h; };

  // ---- list state
  let items = $state([]), loaded = $state(false), fhError = $state(null);
  let folders = $state([]), total = $state(0), tags = $state([]);
  let q = $state(''), folder = $state(null), tag = $state(''), fav = $state(false), sort = $state('added');
  let bust = $state({}), broken = $state({});
  let grouped = $state((() => { try { return localStorage.getItem('mb-grouped') !== '0'; } catch { return true; } })());
  $effect(() => { try { localStorage.setItem('mb-grouped', grouped ? '1' : '0'); } catch { /* */ } });
  let toasts = $state([]);
  const toast = (text, bad = false) => {
    const t = { id: Math.random(), text, bad };
    toasts.push(t);
    setTimeout(() => (toasts = toasts.filter((x) => x.id !== t.id)), bad ? 6000 : 3000);
  };

  async function load() {
    const p = new URLSearchParams({ q, sort, grouped: String(grouped) });
    if (folder !== null) p.set('folder', folder);
    if (tag) p.set('tag', tag);
    if (fav) p.set('favorite', 'true');
    try {
      const r = await get(`api/models?${p}`);
      items = r.items; fhError = r.fh_error; loaded = true;
      const missing = r.items.filter((i) => i.mesh && !i.thumb).map((i) => i.id);
      if (missing.length) requestThumbs(missing, (id) => { const it = items.find((x) => x.id === id); if (it) it.thumb = 'client'; bust[id] = Date.now(); });
    } catch (e) { toast(e.message, true); }
  }
  async function loadMeta() {
    try {
      const [f, t] = await Promise.all([get('api/folders'), get('api/tags')]);
      folders = f.folders; total = f.total; tags = t.tags;
    } catch { /* list still works */ }
  }
  let lt;
  $effect(() => { q; folder; tag; fav; sort; grouped; clearTimeout(lt); lt = setTimeout(load, q ? 250 : 0); });
  onMount(loadMeta);
  const refresh = () => { load(); loadMeta(); };

  let scanning = $state(false);
  async function scan() {
    scanning = true;
    try {
      const r = await post('api/scan');
      toast(r.busy ? 'Scanner allerede' : `Scannet: ${r.added} nye, ${r.updated} opdateret, ${r.removed} fjernet`);
      refresh();
    } catch (e) { toast(e.message, true); } finally { scanning = false; }
  }

  async function newFolder() {
    const name = prompt('Navn på den nye mappe' + (folder ? ` (inde i ${folder})` : ''));
    if (!name?.trim()) return;
    try { const r = await post('api/folders', { path: folder ? `${folder}/${name.trim()}` : name.trim() }); await loadMeta(); folder = r.path; }
    catch (e) { toast(e.message, true); }
  }

  // ---- upload (button + drag & drop anywhere)
  let drag = $state(0), fileInput;

  // ---- delete from the overview (one card, or several in select mode); files go to .slettet
  let selecting = $state(false), selected = $state({}), ask = $state(null), deleting = $state(false);
  const selCards = $derived(items.filter((it) => selected[it.id]));
  const idsOf = (cards, all) => [...new Set(cards.flatMap((c) => (all && c.version_ids ? c.version_ids : [c.id])))];
  function askDelete(cards) {
    if (!cards.length) return;
    const more = idsOf(cards, true).length - cards.length;
    ask = { cards, more, all: false };
  }
  async function doDelete() {
    const ids = idsOf(ask.cards, ask.all);
    deleting = true;
    try {
      const r = await post('api/models/delete', { ids });
      toast(`${r.moved} ${r.moved === 1 ? 'model' : 'modeller'} flyttet til .slettet${r.failed.length ? ` – ${r.failed.length} fejlede` : ''}`, !!r.failed.length);
      ask = null; selected = {}; selecting = false;
      refresh();
    } catch (e) { toast(e.message, true); } finally { deleting = false; }
  }
  function cardClick(e, it) {
    if (!selecting) return;
    e.preventDefault();
    selected[it.id] = !selected[it.id];
  }
  const OK = /\.(3mf|stl)$/i;
  function send(files) {
    const list = [...files].filter((f) => OK.test(f.name));
    const skipped = files.length - list.length;
    if (skipped) toast(`${skipped} fil(er) sprunget over – kun .3mf og .stl`, true);
    const target = folder || '';
    enqueue(list.map((f) => ({ name: f.name, size: f.size, folder: target, get: async () => f })));
  }
  onMount(() => onQueueDone(({ ok, failed, dups }) => {
    const main = failed ? `${ok} uploadet, ${failed} fejlede` : ok ? `${ok} ${ok === 1 ? 'model' : 'modeller'} uploadet` : 'Ingen nye filer';
    const extra = dups ? ` – ${dups} ${dups === 1 ? 'fandtes' : 'fandtes'} allerede og blev ikke uploadet igen` : '';
    if (ok || failed || dups) toast(main + extra, !!failed);
    refresh();
  }));
  const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  function onEnter(e) { if (isFiles(e)) { e.preventDefault(); drag++; } }
  function onLeave(e) { if (isFiles(e)) drag = Math.max(0, drag - 1); }
  function onOver(e) { if (isFiles(e)) e.preventDefault(); }
  function onDrop(e) { if (!isFiles(e)) return; e.preventDefault(); drag = 0; send(e.dataTransfer.files); }

  const folderLabel = $derived(folder === null ? 'Alle modeller' : folder === '' ? 'Rodmappen' : folder.split('/').pop());
  const depth = (p) => p.split('/').length - 1;
  const thumbSrc = (it) => (it.photo ? `api/models/${it.id}/photos/${it.photo}.jpg`
    : it.group_photo ? `api/models/${it.group_photo[0]}/photos/${it.group_photo[1]}.jpg`
    : `api/models/${it.id}/thumb?v=${bust[it.id] || it.mtime}`);
</script>

<svelte:window ondragenter={onEnter} ondragleave={onLeave} ondragover={onOver} ondrop={onDrop} />
<div class="backdrop"></div>

<div class="shell">
  <nav class="rail glass" aria-label="Mapper">
    <a class="brand" href="#/" onclick={() => { folder = null; tag = ''; fav = false; }}>
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="var(--accent)" stroke-width="1.9" stroke-linejoin="round"><path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="M3.5 7.5 12 12.2l8.5-4.7M12 12.2v9"/></svg>
      <span>Modelbank</span></a>
    <button class="btn primary up" onclick={() => fileInput.click()}><Icon name="upload" size={18} /> Upload modeller</button>
    <input type="file" multiple accept=".3mf,.stl" hidden bind:this={fileInput} onchange={(e) => { send(e.target.files); e.target.value = ''; }}>
    <button class="item" class:on={finder} onclick={() => go('#/find')}><Icon name="search" size={18} /> Find på computeren</button>
    <button class="item" class:on={folder === null && !fav && !detailId && !settings && !finder} onclick={() => { folder = null; fav = false; go('#/'); }}><Icon name="grid" size={18} /> Alle modeller <span class="n mono">{total}</span></button>
    <button class="item" class:on={fav && !detailId && !settings && !finder} onclick={() => { fav = !fav; go('#/'); }}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg> Favoritter</button>
    <div class="sect row between"><span class="eyebrow">Mapper</span><button class="btn ghost small" aria-label="Ny mappe" title="Ny mappe" onclick={newFolder}><Icon name="plus" size={16} /></button></div>
    <div class="folders">
      {#each folders as f}
        <button class="item fold" class:on={folder === f.path && !detailId && !settings && !finder} style="--d:{depth(f.path)}" onclick={() => { folder = f.path; go('#/'); }}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/></svg>
          <span class="ellipsis">{f.path.split('/').pop()}</span><span class="n mono">{f.count}</span></button>
      {:else}<div class="tiny muted pad0">Ingen mapper endnu</div>{/each}
    </div>
    {#if tags.length}
      <div class="sect"><span class="eyebrow">Tags</span></div>
      <div class="row wrap tagcloud">{#each tags as t}<button class="chip" class:on={tag === t.tag} onclick={() => { tag = tag === t.tag ? '' : t.tag; go('#/'); }}>{t.tag} <span class="mono muted">{t.count}</span></button>{/each}</div>
    {/if}
    <button class="item set" class:on={settings} onclick={() => go('#/settings')}><Icon name="gear" size={18} /> Indstillinger</button>
  </nav>

  <main>
    {#if finder}
      <Finder {folders} {toast} onClose={() => go('#/')} />
    {:else if settings}
      <Settings onClose={() => { go('#/'); refresh(); }} {toast} />
    {:else if detailId}
      {#key detailId}<Detail id={+detailId} {folders} {toast} onClose={() => go('#/')} onChanged={refresh} />{/key}
    {:else}
      <div class="head row wrap between">
        <h1>{folderLabel}{tag ? ` · ${tag}` : ''}{fav ? ' · favoritter' : ''}</h1>
        <div class="row wrap tools">
          <div class="search grow"><span class="ico"><Icon name="search" size={18} /></span><input class="input" placeholder="Søg i navn, tags, materiale …" bind:value={q}></div>
          <select class="input sort" bind:value={sort} aria-label="Sortér">
            <option value="added">Nyeste</option><option value="name">Navn</option><option value="printed">Mest printet</option><option value="size">Størst</option>
          </select>
          <label class="row small dim grp" title="Vis kun den nyeste version af hver model"><input type="checkbox" bind:checked={grouped}> Grupér versioner</label>
          <button class="btn" class:on={selecting} onclick={() => { selecting = !selecting; selected = {}; }}><Icon name="check" size={18} /> {selecting ? 'Færdig' : 'Vælg'}</button>
          <button class="btn" onclick={scan} disabled={scanning} title="Find filer lagt ind via Samba"><Icon name="refresh" size={18} /> {scanning ? 'Scanner …' : 'Scan'}</button>
        </div>
      </div>
      {#if fhError}<div class="alert warn">FilamentHub: {fhError}</div>{/if}
      {#if selecting}
        <div class="selbar glass row wrap between">
          <span class="small">{selCards.length ? `${selCards.length} valgt` : 'Klik på de modeller, du vil slette'}</span>
          <span class="row wrap">
            <button class="btn small" onclick={() => { selected = Object.fromEntries(items.map((i) => [i.id, true])); }}>Vælg alle ({items.length})</button>
            {#if selCards.length}<button class="btn small" onclick={() => (selected = {})}>Fravælg</button>{/if}
            <button class="btn small danger" disabled={!selCards.length} onclick={() => askDelete(selCards)}><Icon name="trash" size={16} /> Slet valgte</button>
          </span>
        </div>
      {/if}

      {#if !loaded}
        <div class="grid">{#each Array(8) as _}<div class="skeleton" style="aspect-ratio:3/4"></div>{/each}</div>
      {:else if !items.length}
        <div class="empty glass pad stack center">
          <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="var(--muted)" stroke-width="1.4" stroke-linejoin="round"><path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="M3.5 7.5 12 12.2l8.5-4.7M12 12.2v9"/></svg>
          <h2>{q || tag || fav ? 'Ingen modeller passer' : 'Ingen modeller her endnu'}</h2>
          <p class="dim small">Træk .3mf- eller .stl-filer ind på siden, tryk »Upload modeller«, eller læg dem i <span class="mono">/share/modelbank</span> via Samba og tryk »Scan«.</p>
        </div>
      {:else}
        <div class="grid">
          {#each items as it, i (it.id)}
            <a class="card glass rise" class:stack3={it.versions > 1} class:sel={selected[it.id]} class:selecting style="--i:{i}" href={`#/m/${it.id}`} onclick={(e) => cardClick(e, it)}>
              <div class="pic">
                {#if (it.photo || it.group_photo || it.thumb) && !broken[it.id]}<img class:photo={it.photo || it.group_photo} src={thumbSrc(it)} alt="" loading="lazy" onerror={() => (broken[it.id] = true)}>{:else}<div class="ph"><span class="badge">{it.kind === 'stl' ? 'STL' : '3MF'}</span></div>{/if}
                {#if (it.group_printed ?? it.printed)}<span class="badge accent pr">Printet {it.group_printed ?? it.printed}×</span>{/if}
                {#if it.versions > 1}<span class="badge vers">{it.versions} versioner</span>{/if}
                {#if it.favorite}<span class="star">★</span>{/if}
                {#if selecting}<span class="tick" class:on={selected[it.id]}><Icon name="check" size={16} /></span>
                {:else}<button class="del" aria-label={`Slet ${it.name}`} title="Slet" onclick={(e) => { e.preventDefault(); e.stopPropagation(); askDelete([it]); }}><Icon name="trash" size={16} /></button>{/if}
              </div>
              <div class="body stack">
                <b class="nm" title={it.name}>{it.name}</b>
                <div class="row wrap tiny muted">
                  {#each it.filaments.slice(0, 6) as f}<i class="dot" style="--c:#{f.color || '7c8aa5'}" title={f.type}></i>{/each}
                  {#if it.filaments.length}<span>{[...new Set(it.filaments.map((f) => f.type).filter(Boolean))].join(' · ')}</span>{/if}
                </div>
                <div class="row between tiny muted mono">
                  <span>{it.grams ? `${num(it.grams, 1)} g` : it.dims ? it.dims.map((d) => num(d)).join('×') + ' mm' : ''}</span>
                  <span>{it.seconds ? dur(it.seconds) : ''}</span>
                </div>
                {#if folder === null && it.folder}<div class="tiny faint ellipsis">{it.folder}</div>{/if}
              </div>
            </a>
          {/each}
        </div>
      {/if}
    {/if}
  </main>
</div>

{#if drag}
  <div class="drop"><div class="dropbox"><Icon name="upload" size={32} /><b>Slip for at uploade</b><span class="small dim">til {folder ? folder : 'rodmappen'}</span></div></div>
{/if}

<UploadPanel />

{#if ask}
  <div class="scrim" role="presentation" onclick={(e) => e.target === e.currentTarget && !deleting && (ask = null)}>
    <div class="sheet glass pad stack" role="alertdialog" aria-label="Slet">
      <h2>{ask.cards.length === 1 ? `Slet »${ask.cards[0].name}«?` : `Slet ${ask.cards.length} modeller?`}</h2>
      {#if ask.more}
        <div class="stack" style="gap:6px">
          <label class="row small"><input type="radio" name="delv" checked={!ask.all} onchange={() => (ask.all = false)}> Kun {ask.cards.length === 1 ? 'den viste version' : 'de viste versioner'}</label>
          <label class="row small"><input type="radio" name="delv" checked={ask.all} onchange={() => (ask.all = true)}> Også de ældre versioner ({ask.more} {ask.more === 1 ? 'fil' : 'filer'} mere)</label>
        </div>
      {/if}
      <p class="small dim">{idsOf(ask.cards, ask.all).length} {idsOf(ask.cards, ask.all).length === 1 ? 'fil flyttes' : 'filer flyttes'} til <span class="mono">.slettet</span> i /share/modelbank, så du kan fortryde fra Samba. Tags, noter og koblinger til prints slettes.</p>
      <div class="row" style="justify-content:flex-end"><button class="btn" disabled={deleting} onclick={() => (ask = null)}>Annullér</button>
        <button class="btn danger" disabled={deleting} onclick={doDelete}><Icon name="trash" size={16} /> {deleting ? 'Sletter …' : 'Slet'}</button></div>
    </div>
  </div>
{/if}

<div class="toasts">{#each toasts as t (t.id)}<div class="toast" class:bad={t.bad}>{t.text}</div>{/each}</div>

<style>
  .shell { min-height: 100dvh; }
  main { padding: 20px 16px 48px; max-width: 1600px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
  .rail { display: flex; flex-direction: column; gap: 2px; padding: 16px 12px; border-radius: 0; border: 0; border-bottom: 1px solid var(--panel-border); background: var(--bg-2); }
  .brand { display: flex; align-items: center; gap: 8px; padding: 4px 8px 16px; color: var(--text); font-size: 16px; font-weight: 600; }
  .up { margin-bottom: 12px; }
  .item { display: flex; align-items: center; gap: 10px; height: 36px; padding: 0 8px; border-radius: 10px; color: var(--text-dim); background: none; border: 0;
    font: inherit; font-size: 14px; cursor: pointer; text-align: left; width: 100%; min-width: 0; }
  .item:hover { color: var(--text); background: rgba(237,237,237,.04); }
  .item.on { color: var(--text); background: var(--panel-hi); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); }
  .item.on :global(svg) { color: var(--accent); }
  .item .n { margin-left: auto; font-size: 12px; color: var(--muted); }
  .fold { padding-left: calc(8px + var(--d) * 14px); }
  .sect { padding: 16px 8px 4px; }
  .folders { display: flex; flex-direction: column; gap: 2px; max-height: 40vh; overflow: auto; }
  .pad0 { padding: 4px 8px; }
  .tagcloud { padding: 4px 4px 8px; gap: 6px; }
  .tagcloud .chip { min-height: 28px; padding: 2px 10px; }
  .set { margin-top: 12px; }
  @media (min-width: 900px) {
    .rail { position: fixed; top: 0; bottom: 0; left: 0; width: var(--rail-w); overflow: auto; border-bottom: 0; border-right: 1px solid var(--panel-border); }
    main { padding: 28px 32px 48px calc(var(--rail-w) + 32px); }
    .set { margin-top: auto; }
  }
  @media (max-width: 899px) { .folders { max-height: 160px; } }
  .head { gap: 12px; }
  .tools { gap: 8px; flex: 1; justify-content: flex-end; min-width: min(100%, 520px); }
  .tools .search { min-width: 220px; max-width: 420px; }
  .sort { width: 150px; }
  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
  .card { display: flex; flex-direction: column; overflow: hidden; color: var(--text); transition: border-color .14s, transform .14s var(--ease); }
  .card:hover { border-color: var(--panel-border-hi); transform: translateY(-2px); }
  .pic { position: relative; aspect-ratio: 1; background: radial-gradient(120% 90% at 50% 20%, #202227 0%, #141517 75%); }
  .pic img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .pic img.photo { object-fit: cover; }
  .ph { position: absolute; inset: 0; display: grid; place-items: center; }
  .pr { position: absolute; left: 8px; top: 8px; }
  .vers { position: absolute; right: 8px; bottom: 8px; background: rgba(10,12,14,.78); color: var(--text); }
  .stack3 { box-shadow: 4px 4px 0 -1px var(--bg), 4px 4px 0 0 var(--panel-border-hi), 8px 8px 0 -1px var(--bg), 8px 8px 0 0 var(--panel-border); }
  .grp { gap: 6px; cursor: pointer; white-space: nowrap; }
  .del { position: absolute; right: 8px; top: 8px; width: 32px; height: 32px; border-radius: 8px; border: 1px solid var(--panel-border-hi);
    background: rgba(10,12,14,.72); color: #f0a3a6; display: grid; place-items: center; cursor: pointer; opacity: 0; transition: opacity .14s; }
  .card:hover .del, .del:focus-visible { opacity: 1; }
  @media (hover: none) { .del { opacity: 1; } }
  .del:hover { background: color-mix(in srgb, var(--danger) 30%, #111); color: #fff; }
  .card:has(.del) .star { right: 46px; }
  .tick { position: absolute; right: 8px; top: 8px; width: 26px; height: 26px; border-radius: 50%; border: 2px solid rgba(255,255,255,.7);
    background: rgba(10,12,14,.55); display: grid; place-items: center; color: transparent; }
  .tick.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .card.selecting { cursor: pointer; }
  .card.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .selbar { padding: 8px 12px; gap: 8px; }
  .btn.on { border-color: var(--accent); color: var(--accent); }
  .scrim { position: fixed; inset: 0; z-index: 250; background: rgba(0,0,0,.55); display: grid; place-items: center; padding: 16px; }
  .sheet { width: 100%; max-width: 460px; background: var(--panel-solid); }
  .star { position: absolute; right: 10px; top: 6px; color: #f3c14b; font-size: 18px; }
  .body { padding: 10px 12px 12px; gap: 6px; }
  .nm { font-weight: 500; font-size: 14px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.3; min-height: 2.6em; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c); box-shadow: 0 0 0 1px rgba(255,255,255,.18); display: inline-block; }
  .faint { color: var(--faint); }
  .empty { align-items: center; padding: 48px 24px; max-width: 560px; margin: 40px auto; }
  .drop { position: fixed; inset: 0; z-index: 200; background: rgba(8,10,12,.6); display: grid; place-items: center; pointer-events: none; }
  .dropbox { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 32px 48px; border-radius: 16px; border: 2px dashed var(--accent);
    background: var(--panel-solid); color: var(--text); }
  .toasts { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); z-index: 300; display: flex; flex-direction: column; gap: 8px; align-items: center; pointer-events: none; }
  .toast { background: var(--panel-hi); border: 1px solid var(--panel-border-hi); padding: 10px 16px; border-radius: 10px; font-size: 14px; box-shadow: 0 8px 30px rgba(0,0,0,.4); animation: rise .3s var(--ease); }
  .toast.bad { border-color: color-mix(in srgb, var(--danger) 50%, transparent); color: #f0a3a6; }
</style>
