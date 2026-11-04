<!-- Print library: finished prints with photo, cover, 3MF, filaments and cost. -->
<script>
  import Sheet from '../lib/ui/Sheet.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import { go, toast, toastErr } from '../lib/stores.svelte.js';
  import { api, post, del } from '../lib/api.js';
  import { fmtG1, fmtTs, hex, debounce, nf1, nf2 } from '../lib/util.js';
  import { onMount } from 'svelte';

  let items = $state(null), total = $state(0), q = $state(''), ft = $state(null), sel = $state(null), busy = $state(false), imp = $state(null);
  let ver = $state(0);

  async function load() {
    try { const r = await api(`api/library?${new URLSearchParams({ q, limit: '200' })}`); items = r.items; total = r.total; } catch (e) { items = items || []; toastErr(e); }
  }
  const search = debounce(load, 250);
  let gone = false;
  onMount(() => {
    load();
    (async () => {
      try { ft = await api('api/features'); } catch { ft = {}; }
      try { imp = await api('api/library/import'); if (imp.running && !gone) poll(); } catch { /* not available */ }
    })();
    return () => { gone = true; clearTimeout(timer); };
  });

  const dur = (s) => (s ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} t` : '–');
  const when = (e) => (e.end ? fmtTs(e.end) : '–');
  const mats = (e) => {
    const m = e.spools?.length ? e.spools.map((s) => ({ type: s.material, color: s.color })) : e.materials || [];
    return m.filter((x) => x.type || x.color);
  };
  const img = (e) => (e.has_photo ? `api/library/${e.task_id}/photo.jpg?v=${Math.round(e.photo_ts || 0)}-${ver}` : e.has_cover ? `api/library/${e.task_id}/cover.png` : '');

  async function retake() {
    busy = true;
    try { const r = await post(`api/library/${sel.task_id}/photo`, {}); ver++; toast('Nyt billede taget'); await load(); sel = items.find((x) => x.task_id === sel.task_id) || r; } catch (e) { toastErr(e); } finally { busy = false; }
  }
  async function remove() {
    if (!confirm(`Slet "${sel.title}" fra biblioteket?`)) return;
    busy = true;
    try { await del(`api/library/${sel.task_id}`); toast('Slettet'); sel = null; await load(); } catch (e) { toastErr(e); } finally { busy = false; }
  }

  let timer;
  async function startImport() {
    try { imp = await post('api/library/import?limit=50', {}); poll(); } catch (e) { toastErr(e); }
  }
  function poll() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try { imp = await api('api/library/import'); } catch { return; }
      if (gone) return;
      if (imp.running) poll(); else { if (imp.error) toastErr(new Error(imp.error)); await load(); }
    }, 2000);
  }
</script>

<div class="page stack" style="gap:16px">
  <h1>Printbibliotek</h1>
  <div class="search"><span class="ico"><Icon name="search" size={18} /></span>
    <input class="input" type="search" placeholder="Søg i prints …" bind:value={q} oninput={search}></div>

  {#if imp?.running}<div class="alert"><span class="spinner"></span> Henter {imp.done} af {imp.total} …</div>{/if}

  {#if items === null}
    <div class="grid"><div class="skeleton" style="height:190px"></div><div class="skeleton" style="height:190px"></div></div>
  {:else if !items.length}
    <div class="glass pad stack">
      {#if q}<span class="muted">Ingen prints matcher “{q}”.</span>
      {:else}
        <b>Biblioteket er tomt</b>
        <p class="small muted">Færdige prints dukker op her, når Printbibliotek eller Billede er slået til under <a href="#/settings">Indstillinger → Funktioner</a>.</p>
        {#if ft?.library}<div class="row"><button class="btn" onclick={startImport} disabled={imp?.running}>Hent tidligere print fra Bambu Cloud</button></div>{/if}
      {/if}
    </div>
  {:else}
    <div class="muted small">{total} print{total === 1 ? '' : 's'}</div>
    <div class="grid">
      {#each items as e}
        <button class="card glass" onclick={() => (sel = e)}>
          <div class="im">
            {#if img(e)}<img src={img(e)} alt="" loading="lazy">
            {:else}<div class="ph">{#each mats(e) as m}<i style="background:{hex(m.color)}"></i>{/each}</div>{/if}
            {#if e.has_photo}<span class="badge tag">Billede</span>{/if}
          </div>
          <div class="tx">
            <b class="ellipsis">{e.title}</b>
            <span class="tiny muted">{when(e)}</span>
            <span class="tiny muted">{fmtG1(e.weight)} · {dur(e.seconds)}</span>
            <span class="chips">{#each mats(e).filter((m) => m.type) as m}<span class="chip tiny"><i class="d" style="background:{hex(m.color)}"></i>{m.type}</span>{/each}</span>
          </div>
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if sel}
  <Sheet title={sel.title} onclose={() => (sel = null)} wide>
    <div class="stack">
      <div class="pics">
        {#if sel.has_photo}<img src={`api/library/${sel.task_id}/photo.jpg?v=${Math.round(sel.photo_ts || 0)}-${ver}`} alt="Billede af pladen">{/if}
        {#if sel.has_cover}<img src={`api/library/${sel.task_id}/cover.png`} alt="Cover">{/if}
      </div>
      <dl class="kv">
        <dt>Startet</dt><dd>{fmtTs(sel.start)}</dd>
        <dt>Færdig</dt><dd>{when(sel)}{sel.status === 'failed' ? ' (fejlet)' : ''}</dd>
        <dt>Varighed</dt><dd>{dur(sel.seconds)}</dd>
        <dt>Vægt</dt><dd>{fmtG1(sel.weight)}</dd>
        {#if sel.plate_name || sel.plate}<dt>Plade</dt><dd>{sel.plate_name || sel.plate}</dd>{/if}
        {#if sel.printer}<dt>Printer</dt><dd>{sel.printer}</dd>{/if}
        {#if sel.design_title}<dt>Design</dt><dd>{sel.design_title}</dd>{/if}
        {#if sel.energy}<dt>Strøm</dt><dd>{nf2.format(sel.energy.kwh)} kWh{sel.energy.cost != null ? ` · ${nf2.format(sel.energy.cost)} kr` : ''}</dd>{/if}
      </dl>
      {#if sel.spools?.length}
        <div class="eyebrow">Filamenter</div>
        {#each sel.spools as s}
          <div class="row small"><i class="d" style="background:{hex(s.color)}"></i><span class="grow ellipsis">{s.name || s.material}{s.name && s.material ? ` · ${s.material}` : ''}</span><b class="mono">{fmtG1(s.grams)}</b></div>
        {/each}
      {:else if sel.materials?.length}
        <div class="eyebrow">Filamenter</div>
        <div class="row wrap">{#each sel.materials as m}<span class="chip tiny"><i class="d" style="background:{hex(m.color)}"></i>{m.type}</span>{/each}</div>
      {/if}
      {#if sel.has_file}
        <a class="btn primary" href={`api/library/${sel.task_id}/model.3mf`} download><Icon name="download" size={18} /> Download 3MF</a>
        <span class="tiny muted">Åbn i Bambu Studio og send til printeren for at printe igen.</span>
      {/if}
      <div class="row wrap">
        {#if ft?.photo}<button class="btn" onclick={retake} disabled={busy}><Icon name="camera" size={18} /> Tag nyt billede</button>{/if}
        <button class="btn danger" onclick={remove} disabled={busy}><Icon name="trash" size={18} /> Slet fra biblioteket</button>
      </div>
    </div>
  </Sheet>
{/if}

<style>
  h1 { font-size: 28px; }
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  @media (min-width: 640px) { .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  @media (min-width: 1000px) { .grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
  .card { padding: 0; overflow: hidden; text-align: left; cursor: pointer; display: flex; flex-direction: column; color: inherit; font: inherit; }
  .im { position: relative; aspect-ratio: 4 / 3; background: rgba(255,255,255,.04); }
  .im img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .ph { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 6px; }
  .ph i { width: 22px; height: 22px; border-radius: 50%; }
  .tag { position: absolute; top: 8px; left: 8px; background: rgba(15,15,16,.8); }
  .tx { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
  .chip { padding: 2px 8px; }
  .d { display: inline-block; width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .pics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  .pics img { width: 100%; border-radius: 14px; background: rgba(255,255,255,.04); display: block; }
</style>
