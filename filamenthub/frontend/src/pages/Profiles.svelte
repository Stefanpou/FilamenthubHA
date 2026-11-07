<!-- Filament profile database: search every slicer profile (OrcaSlicer's library), adjust the main values and
     send it to Bambu Studio - as a file to import or straight into the Bambu Cloud account. -->
<script>
  import Sheet from '../lib/ui/Sheet.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import Confirm from '../lib/ui/Confirm.svelte';
  import SlicerFields from '../lib/SlicerFields.svelte';
  import LibraryTabs from '../lib/LibraryTabs.svelte';
  import { toast, toastErr } from '../lib/stores.svelte.js';
  import { api, post, del } from '../lib/api.js';
  import { debounce } from '../lib/util.js';
  import { onMount } from 'svelte';

  const PAGE = 60;
  let q = $state(''), vendor = $state(''), material = $state(''), allPrinters = $state(false);
  let res = $state(null), items = $state([]), loading = $state(false);
  let cloud = $state(null), cloudErr = $state(''), showCloud = $state(false);
  let sel = $state(null), choice = $state(null), name = $state(''), busy = $state(false), gone = $state(null);
  let processes = $state([]), defProcess = '', proc = $state(null), procName = $state('');

  async function load(more = false) {
    loading = true;
    try {
      const p = new URLSearchParams({ q, vendor, material, limit: String(PAGE), offset: String(more ? items.length : 0) });
      if (allPrinters) p.set('all_printers', 'true');
      const r = await api(`api/slicer/profiles?${p}`);
      items = more ? [...items, ...r.items] : r.items;
      res = r;
    } catch (e) { res = res || { items: [], total: 0, vendors: [], materials: [] }; toastErr(e); } finally { loading = false; }
  }
  const search = debounce(() => load(), 250);
  async function loadCloud() {
    try { cloud = (await api('api/slicer/cloud')).items; cloudErr = ''; } catch (e) { cloud = []; cloudErr = e.message; }
  }
  onMount(() => {
    load(); loadCloud();
    api('api/slicer/processes').then((r) => { processes = r.items; defProcess = r.default; }).catch(() => {});
  });

  const inCloud = $derived(new Set((cloud || []).filter((p) => p.kind !== 'print').map((p) => (p.name || '').toLowerCase())));
  const mats = $derived((res?.materials || []).slice(0, 8));
  const short = (p) => p.profile.replace(/ @System$/, '').replace(/ @BBL (\w+)( 0\.4 nozzle)?$/, '');
  const temp = (s, k) => (s?.[k] && s[k] !== '0' ? `${s[k]} °C` : '–');

  function open(p) {
    sel = p;
    choice = { profile: p.profile, settings: { ...p.settings } };
    name = p.preset_name;
    const d = processes.find((x) => x.name === defProcess);
    proc = d ? { base: d.name, settings: { ...d.defaults } } : null;
    procName = d ? `${d.name} - ${p.preset_name.split(' @')[0]}` : '';
    lastBase = d?.name || '';
  }
  let lastBase = '';
  $effect(() => {
    const b = proc?.base || '';
    if (lastBase && b && b !== lastBase && procName.startsWith(lastBase)) procName = b + procName.slice(lastBase.length);
    lastBase = b;
  });
  const pnum = (v) => Number(String(v ?? '').replace(',', '.').replace('%', '').trim());
  const procOwn = $derived(!!proc && Object.entries(proc.settings).some(([k, v]) => {
    const d = processes.find((x) => x.name === proc.base)?.defaults?.[k];
    return d != null && String(v ?? '').trim() !== '' && pnum(v) !== pnum(d);
  }));
  const edits = () => Object.fromEntries(Object.entries(choice?.settings || {}).filter(([, v]) => v != null && v !== ''));
  const body = () => ({ profile: sel.profile, name: name.trim(), settings: edits(), process: procOwn ? proc : null, process_name: procName.trim() || null });

  async function run(fn) {
    busy = true;
    try { await fn(); } catch (e) { toastErr(e); } finally { busy = false; }
  }
  function save(data, fname) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 4)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${fname.trim().replace(/[^A-Za-z0-9 ._@-]+/g, '') || 'filament'}.json`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const download = () => run(async () => {
    save(await post('api/slicer/profiles/preset.json', body()), name);
    if (procOwn) save(await post('api/slicer/profiles/process.json', body()), procName);
  });
  const toCloud = () => run(async () => {
    let r;
    try { r = await post('api/slicer/profiles/cloud', body()); } catch (e) {
      if (e.status !== 409 || !window.confirm(`${e.message}.\n\nErstat den med de nye værdier?`)) throw e;
      r = await post('api/slicer/profiles/cloud', { ...body(), replace: true });
    }
    toast(`${r.replaced ? 'Erstattet' : 'Sendt'} i Bambu Cloud${r.process ? ' – filament- og procesprofil' : ''}. Bambu Studio henter ${r.process ? 'dem' : 'den'} ved næste synkronisering`);
    sel = null;
    loadCloud();
  });
  const remove = () => run(async () => {
    const p = gone;
    gone = null;
    await del(`api/slicer/cloud/${encodeURIComponent(p.setting_id)}`);
    toast(`»${p.name}« er slettet fra Bambu Cloud`);
    loadCloud();
  });
</script>

<div class="page stack" style="gap:16px">
  <h1>Filamentprofiler</h1>
  <LibraryTabs />

  <div class="search"><span class="ico"><Icon name="search" size={18} /></span>
    <input class="input" type="search" placeholder="Søg producent, filament eller materiale …" bind:value={q} oninput={search}></div>

  {#if res}
    <div class="chips">
      <button class="chip" class:on={!material} onclick={() => { material = ''; load(); }}>Alle</button>
      {#each mats as m}<button class="chip" class:on={material === m.name} onclick={() => { material = material === m.name ? '' : m.name; load(); }}>{m.name}</button>{/each}
    </div>
    <div class="row wrap filters">
      <select class="input" bind:value={vendor} onchange={() => load()} aria-label="Producent">
        <option value="">Alle producenter ({res.vendors.length})</option>
        {#each res.vendors as v}<option value={v.name}>{v.name} ({v.count})</option>{/each}
      </select>
      <label class="row small tog"><input type="checkbox" bind:checked={allPrinters} onchange={() => load()}> Vis også profiler til andre printere</label>
    </div>
  {/if}

  {#if cloud?.length || cloudErr}
    <section class="glass pad stack">
      <button class="row between head" onclick={() => (showCloud = !showCloud)} aria-expanded={showCloud}>
        <span class="eyebrow">I din Bambu Cloud</span>
        <span class="small muted">{cloudErr ? 'ikke tilgængelig' : `${cloud.length} profil${cloud.length === 1 ? '' : 'er'}`} <Icon name="chev" size={14} /></span>
      </button>
      {#if showCloud}
        {#if cloudErr}<p class="small muted">{cloudErr}</p>{/if}
        {#each cloud || [] as p (p.setting_id)}
          <div class="row cl"><span class="grow ellipsis small">{p.name}</span><span class="tiny muted">{p.kind === 'print' ? 'proces' : 'filament'}</span>
            <button class="btn small ghost" onclick={() => (gone = p)} disabled={busy} aria-label="Slet {p.name}"><Icon name="trash" size={16} /></button></div>
        {/each}
        <p class="tiny muted">Det er dine egne profiler, som Bambu Studio viser under brugerprofiler.</p>
      {/if}
    </section>
  {/if}

  {#if !res}
    <div class="list"><div class="skeleton" style="height:84px"></div><div class="skeleton" style="height:84px"></div></div>
  {:else if !items.length}
    <div class="glass pad"><span class="muted">Ingen profiler matcher{q ? ` “${q}”` : ''}.</span>
      {#if !allPrinters}<button class="linkbtn" onclick={() => { allPrinters = true; load(); }}>Søg i profiler til andre printere</button>{/if}</div>
  {:else}
    <div class="muted small">{res.total} profil{res.total === 1 ? '' : 'er'}{res.machine ? ` · ${allPrinters ? 'tilpasses' : 'til'} ${res.machine}` : ''}</div>
    <div class="list">
      {#each items as p (p.profile)}
        <button class="card glass" onclick={() => open(p)}>
          <div class="row between top">
            <span class="tiny muted ellipsis">{p.vendor}{p.model && p.model !== res.model ? ` · ${p.model}` : ''}</span>
            {#if inCloud.has(p.preset_name.toLowerCase())}<span class="badge ok">I Bambu Cloud</span>
            {:else if p.builtin}<span class="badge">Indbygget</span>{/if}
          </div>
          <b class="ellipsis">{short(p)}</b>
          <span class="vals tiny mono"><span class="chip-t">{p.type}</span>
            <span>Dyse {temp(p.settings, 'nozzle_temperature')}</span>
            <span>Plade {temp(p.settings, 'textured_plate_temp')}</span>
            {#if p.settings.filament_max_volumetric_speed}<span>{p.settings.filament_max_volumetric_speed} mm³/s</span>{/if}</span>
        </button>
      {/each}
    </div>
    {#if items.length < res.total}
      <div class="row"><button class="btn grow" onclick={() => load(true)} disabled={loading}>Vis flere ({res.total - items.length} tilbage)</button></div>
    {/if}
  {/if}
  <p class="tiny muted">Profilerne kommer fra OrcaSlicers åbne filamentbibliotek med Bambu Labs og producenternes egne indstillinger.</p>
</div>

{#if sel}
  <Sheet title={short(sel)} onclose={() => (sel = null)}>
    <div class="stack">
      <div class="small muted">{sel.vendor} · {sel.type}{sel.model ? ` · lavet til ${sel.model}` : ' · generisk profil'}</div>
      {#if sel.builtin}<p class="tiny muted">Denne profil er indbygget i Bambu Studio. Send den kun, hvis du har ændret noget.</p>{/if}
      <SlicerFields candidates={[sel]} bind:choice picker={false} bind:process={proc} {processes} />
      <label class="f">Profilnavn i Bambu Studio<input class="input" bind:value={name} maxlength="200"></label>
      {#if procOwn}<label class="f">Procesprofil i Bambu Studio<input class="input" bind:value={procName} maxlength="200"></label>{/if}
      {#if inCloud.has(name.trim().toLowerCase())}<p class="tiny warn">Der findes allerede en profil med det navn i Bambu Cloud – sender du, bliver du spurgt, om den skal erstattes.</p>{/if}
      {#if !res?.machine}<p class="tiny warn">Log ind på Bambu Cloud under Indstillinger, så FilamentHub kender din printer.</p>{/if}
      <p class="tiny muted">Profilen arver fra Bambus egen {res?.machine}-profil og ændrer kun det, der står her. Filen importeres i Bambu Studio under Filer → Importér → Importér konfigurationer.</p>
    </div>
    {#snippet footer()}
      <button class="btn grow" onclick={download} disabled={busy || !name.trim()}><Icon name="download" size={16} /> Download</button>
      <button class="btn primary grow" onclick={toCloud} disabled={busy || !name.trim()}><Icon name="upload" size={16} /> Til Bambu Cloud</button>
    {/snippet}
  </Sheet>
{/if}

{#if gone}
  <Confirm title="Slet profil fra Bambu Cloud?" message={`»${gone.name}« forsvinder fra Bambu Studio ved næste synkronisering.`} confirmLabel="Slet" danger onconfirm={remove} oncancel={() => (gone = null)} />
{/if}

<style>
  .filters select { flex: 1 1 220px; min-width: 0; }
  .tog { gap: 8px; align-items: center; cursor: pointer; }
  .list { display: grid; grid-template-columns: 1fr; gap: 8px; }
  @media (min-width: 700px) { .list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (min-width: 1200px) { .list { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  .card { padding: 12px 14px; text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 4px; color: inherit; font: inherit; min-width: 0; }
  .card:hover { border-color: var(--panel-border-hi); }
  .top { gap: 8px; min-height: 20px; }
  .vals { display: flex; flex-wrap: wrap; gap: 4px 12px; color: var(--text-dim); align-items: center; }
  .chip-t { padding: 1px 6px; border-radius: 5px; background: var(--panel-hi); color: var(--text); }
  .head { background: none; border: 0; color: inherit; font: inherit; padding: 0; cursor: pointer; width: 100%; }
  .cl { gap: 8px; border-top: 1px solid var(--panel-border); padding-top: 6px; }
  .warn { color: var(--warn); }
</style>
