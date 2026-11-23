<!-- One model: 3D view, filament vs. FilamentHub stock, print history, price, and its details. -->
<script>
  import Icon from './Icon.svelte';
  import Viewer from './Viewer.svelte';
  import { get, post, patch, del, kr, num, dur, bytes, date } from './api.js';

  let { id, folders = [], onClose, onChanged, toast } = $props();
  let m = $state(null), err = $state(''), busy = $state(false);
  let tagText = $state(''), pick = $state(null), pickQ = $state(''), confirmDel = $state(false);
  let copies = $state(1), pct = $state(''), mode = $state('pct'), fixed = $state('');
  let man = $state({ grams: '', minutes: '', material: '' });
  let view = $state(null), big = $state(-1);   // view: 'photo' | '3d'; big: index of the photo shown large

  async function load() {
    err = '';
    try { set(await get(`api/models/${id}`)); } catch (e) { err = e.message; }
  }
  function set(d) {
    m = d;
    man = { grams: d.manual?.grams ?? '', minutes: d.manual?.minutes ?? '', material: d.manual?.material ?? '' };
    if (d.price) { mode = d.price.margin.mode; pct = String(d.price.margin.pct ?? ''); fixed = String(d.price.margin.fixed ?? ''); copies = d.price.copies; }
  }
  $effect(() => { id; load(); });

  async function save(fields, msg) {
    busy = true;
    try { set(await patch(`api/models/${id}`, fields)); onChanged?.(); if (msg) toast(msg); }
    catch (e) { toast(e.message, true); await load(); }
    finally { busy = false; }
  }
  const addTag = () => {
    const t = tagText.trim();
    if (!t) return;
    tagText = '';
    if (!m.tags.includes(t)) save({ tags: [...m.tags, t] });
  };

  let pt;
  function reprice() {
    clearTimeout(pt);
    pt = setTimeout(async () => {
      const body = { copies: Math.max(1, parseInt(copies) || 1), mode };
      const n = (v) => { const x = parseFloat(String(v).replace(',', '.')); return Number.isFinite(x) ? x : null; };
      if (mode === 'pct' && n(pct) != null) body.pct = n(pct);
      if (mode === 'fixed' && n(fixed) != null) body.fixed = n(fixed);
      try { const d = await post(`api/models/${id}/price`, body); m.price = d.price; } catch (e) { toast(e.message, true); }
    }, 350);
  }

  async function link(task_id, linked) {
    try { set(await post(`api/models/${id}/link`, { task_id, linked })); onChanged?.(); } catch (e) { toast(e.message, true); }
  }
  async function openPicker() {
    pick = { items: null, err: '' };
    try { pick.items = (await get(`api/fh/library?q=${encodeURIComponent(pickQ)}`)).items; } catch (e) { pick.err = e.message; }
  }
  async function remove() {
    try { await del(`api/models/${id}`); toast('Modellen er flyttet til mappen .slettet'); onChanged?.(); onClose(); }
    catch (e) { toast(e.message, true); }
  }

  const STATUS = { ok: ['ok', 'På lager'], color: ['warn', 'Anden farve'], low: ['danger', 'For lidt'],
                   missing: ['danger', 'Ikke på lager'], unknown: ['shadow', 'Mængde ukendt'] };
  const photo = (h) => `api/fh/print/${h.task_id}/${h.has_photo ? 'photo.jpg' : 'cover.png'}`;
  const myPhoto = (tid) => `api/models/${id}/photos/${tid}.jpg`;
  const photos = $derived(m?.photos || []);
  const shown = $derived(view || (photos.length ? 'photo' : '3d'));
  let sel = $state(0);
  $effect(() => { if (sel >= photos.length) sel = 0; });
  function keys(e) {
    if (big < 0) return;
    if (e.key === 'Escape') big = -1;
    if (e.key === 'ArrowRight') big = (big + 1) % photos.length;
    if (e.key === 'ArrowLeft') big = (big - 1 + photos.length) % photos.length;
  }
  const colors = $derived(Object.fromEntries((m?.meta?.filaments || []).filter((f) => f.color).map((f) => [String(f.id), f.color])));
  const linkedIds = $derived(new Set((m?.history || []).map((h) => h.task_id)));
</script>

<div class="detail stack">
  <div class="row between top">
    <button class="btn ghost" onclick={onClose}><Icon name="back" size={18} /> Alle modeller</button>
    {#if m}<div class="row">
      <a class="btn" href={`api/models/${id}/file`} download><Icon name="download" size={18} /> Hent fil</a>
      <button class="btn danger icon-btn" aria-label="Slet model" onclick={() => (confirmDel = true)}><Icon name="trash" size={18} /></button>
    </div>{/if}
  </div>

  {#if err}<div class="alert err">{err}</div>{/if}
  {#if !m && !err}<div class="skeleton" style="height:420px"></div>{/if}

  {#if m}
    <div class="row title">
      <input class="name" value={m.name} aria-label="Navn" onchange={(e) => e.target.value.trim() && save({ name: e.target.value.trim() })}>
      <button class="fav" class:on={m.favorite} aria-label="Favorit" onclick={() => save({ favorite: !m.favorite })}>
        <svg viewBox="0 0 24 24" width="22" height="22" fill={m.favorite ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>
      </button>
    </div>
    <div class="row wrap facts small muted">
      <span class="badge">{m.kind === 'stl' ? 'STL' : '3MF'}</span>
      {#if m.dims}<span class="mono">{m.dims.map((d) => num(d, 1)).join(' × ')} mm</span>{/if}
      {#if m.plates > 1}<span>{m.plates} plader</span>{/if}
      <span>{bytes(m.size)}</span><span>Tilføjet {date(m.added)}</span>
      {#if m.printed}<span class="badge accent">Printet {m.printed}×</span>{/if}
      {#if m.error}<span class="badge danger">Kunne ikke læse filen: {m.error}</span>{/if}
    </div>

    <div class="cols">
      <div class="stack">
        {#if photos.length}
          <div class="row seg">
            <button class="chip" class:on={shown === 'photo'} onclick={() => (view = 'photo')}><Icon name="camera" size={16} /> Fotos ({photos.length})</button>
            <button class="chip" class:on={shown === '3d'} onclick={() => (view = '3d')}><Icon name="box" size={16} /> 3D</button>
          </div>
        {/if}
        {#if shown === 'photo' && photos.length}
          {@const p = photos[sel] || photos[0]}
          <button class="hero" onclick={() => (big = sel)} aria-label="Vis foto i stor størrelse">
            <img src={myPhoto(p.task_id)} alt={`Foto af printet ${p.title}`}>
            <span class="cap tiny">{date(p.end)}{p.plate > 1 ? ` · plade ${p.plate}` : ''}</span>
          </button>
          {#if photos.length > 1}
            <div class="strip">
              {#each photos as ph, i (ph.task_id)}
                <button class="sthumb" class:on={i === sel} onclick={() => (sel = i)} aria-label={`Foto fra ${date(ph.end)}`}><img src={myPhoto(ph.task_id)} alt="" loading="lazy"></button>
              {/each}
            </div>
          {/if}
        {:else}
          <Viewer url={`api/models/${id}/mesh`} {colors} fallback={m.thumb ? `api/models/${id}/thumb?v=${m.mtime}` : null} />
          {#if m.meta.mesh_error}<div class="tiny muted">Ingen 3D-geometri i filen ({m.meta.mesh_error}) – viser billedet fra filen.</div>{/if}
        {/if}

        <section class="glass pad stack">
          <h2><Icon name="edit" size={20} /> Oplysninger</h2>
          <label class="f">Mappe
            <select class="input" value={m.folder} onchange={(e) => save({ folder: e.target.value }, 'Flyttet')}>
              <option value="">(Rodmappen)</option>
              {#each folders as f}<option value={f.path}>{f.path}</option>{/each}
            </select></label>
          <div class="f">Tags
            <div class="row wrap">
              {#each m.tags as t}<span class="chip on">{t}<button class="x" aria-label={`Fjern ${t}`} onclick={() => save({ tags: m.tags.filter((x) => x !== t) })}>×</button></span>{/each}
              <input class="input tagin" placeholder="Nyt tag" bind:value={tagText} onkeydown={(e) => e.key === 'Enter' && addTag()} onblur={addTag}>
            </div></div>
          <div class="grid2">
            <label class="f">Designer<input class="input" value={m.designer} onchange={(e) => save({ designer: e.target.value })}></label>
            <label class="f">Kilde (link)<input class="input" value={m.source_url} placeholder="https://makerworld.com/…" onchange={(e) => save({ source_url: e.target.value.trim() })}></label>
          </div>
          {#if m.source_url}<a class="small" href={m.source_url} target="_blank" rel="noopener noreferrer"><Icon name="link" size={14} /> Åbn kilden</a>{/if}
          <label class="f">Noter<textarea class="input" value={m.notes} onchange={(e) => save({ notes: e.target.value })}></textarea></label>
          {#if m.meta.description}<details class="small"><summary class="muted">Beskrivelse fra filen</summary><p class="desc">{m.meta.description}</p></details>{/if}
          <div class="tiny muted mono ellipsis" title={m.relpath}>/share/modelbank/{m.relpath}</div>
        </section>
      </div>

      <div class="stack">
        <section class="glass pad stack">
          <h2><Icon name="box" size={20} /> Filament og lager</h2>
          {#if !m.fh.configured}
            <div class="alert info">Sæt FilamentHub op under Indstillinger for at se, om du har filamentet på lager.</div>
          {:else if m.fh.error}
            <div class="alert warn">{m.fh.error}</div>
          {/if}
          {#if m.stock?.length}
            {#each m.stock as s}
              <div class="fil" style="--c:#{s.color || '7c8aa5'}">
                <div class="row between">
                  <span class="row"><i class="dot"></i><b>{s.type || 'Ukendt materiale'}</b>{#if s.grams != null}<span class="mono muted">{num(s.grams, 1)} g</span>{/if}</span>
                  <span class="badge {STATUS[s.status][0]}">{STATUS[s.status][1]}</span>
                </div>
                {#if s.spool}
                  <div class="row small spool" style="--s:#{s.spool.color || '7c8aa5'}"><i class="dot s"></i>#{s.spool.id} {s.spool.name}
                    <span class="mono muted">{num(s.spool.remaining)} g tilbage{s.spool.location ? ` · ${s.spool.location}` : ''}</span></div>
                {:else if s.status === 'missing'}<div class="small muted">Ingen {s.type || 'ruller'} på lager</div>{/if}
                {#if s.status === 'low'}<div class="tiny muted">I alt {num(s.total_material)} g {s.material_key} på lager, men ingen rulle har nok alene.</div>{/if}
              </div>
            {/each}
            {#if m.grams_source === 'print'}<div class="tiny muted">Gram er taget fra sidste print af modellen.</div>{/if}
            {#if m.grams_source === 'manual'}<div class="tiny muted">Gram og tid er dine egne tal (se nedenfor).</div>{/if}
          {:else if m.fh.configured && !m.fh.error}
            <div class="small muted">Filen siger ikke, hvilket filament den bruger. Skriv materiale og gram nedenfor.</div>
          {/if}
          <details open={!m.meta.sliced && m.kind === 'stl' && !m.grams_source}>
            <summary class="small muted">Egne tal (bruges i stedet for filens)</summary>
            <div class="grid3">
              <label class="f">Materiale<input class="input" bind:value={man.material} placeholder="PETG" onchange={() => save({ manual: man })}></label>
              <label class="f">Gram<input class="input mono" inputmode="decimal" bind:value={man.grams} onchange={() => save({ manual: man })}></label>
              <label class="f">Minutter<input class="input mono" inputmode="numeric" bind:value={man.minutes} onchange={() => save({ manual: man })}></label>
            </div>
          </details>
        </section>

        <section class="glass pad stack">
          <h2><Icon name="calc" size={20} /> Pris</h2>
          {#if m.price}
            {@const p = m.price}
            <div class="grid3">
              <label class="f">Antal<input class="input mono" inputmode="numeric" bind:value={copies} oninput={reprice}></label>
              <label class="f">Avance
                <select class="input" bind:value={mode} onchange={reprice}><option value="pct">Procent</option><option value="fixed">Fast pris</option></select></label>
              {#if mode === 'pct'}<label class="f">Procent<input class="input mono" inputmode="decimal" bind:value={pct} oninput={reprice}></label>
              {:else}<label class="f">Salgspris (kr)<input class="input mono" inputmode="decimal" bind:value={fixed} oninput={reprice}></label>{/if}
            </div>
            <dl class="kv">
              <dt>Filament ({num(p.lines.reduce((a, l) => a + l.grams, 0), 1)} g)</dt><dd class="mono">{kr(p.filament)}</dd>
              <dt>Strøm ({num(p.kwh, 2)} kWh · {num(p.watts)} W{p.watts_measured ? ' målt' : ''} · {dur(p.hours * 3600)})</dt>
              <dd class="mono">{p.electricity == null ? 'ingen elpris' : kr(p.electricity)}</dd>
              <dt>Kostpris</dt><dd class="mono">{kr(p.cost)}</dd>
              <dt><b>Salgspris</b></dt><dd class="mono sale">{kr(p.sale)}</dd>
            </dl>
            <div class="tiny muted">Avance {p.margin_pct == null ? '–' : `${num(p.margin_pct, 1)} %`}{p.el_source ? ` · ${p.el_source}` : ''}. Ruller uden pris regnes med {num(p.margin.kg_price)} kr/kg.</div>
          {:else if !m.fh.configured}
            <div class="small muted">Prisen bruger FilamentHubs rullepriser og strømmåling – sæt FilamentHub op under Indstillinger.</div>
          {:else}
            <div class="small muted">Mangler {m.price_missing === 'tid' ? 'printtid' : 'gram'} – brug en slicet 3MF, print modellen én gang, eller skriv egne tal ovenfor.</div>
          {/if}
        </section>

        <section class="glass pad stack">
          <div class="row between"><h2><Icon name="history" size={20} /> Printhistorik</h2>
            {#if m.fh.configured && !m.fh.error}<button class="btn small" onclick={openPicker}><Icon name="link" size={16} /> Kobl print</button>{/if}</div>
          {#if m.history?.length}
            <div class="hist">
              {#each m.history as h}
                <div class="h">
                  {#if h.photo}<button class="hbtn" onclick={() => { view = 'photo'; big = photos.findIndex((x) => x.task_id === h.task_id); }} aria-label="Vis foto"><img src={myPhoto(h.task_id)} alt="" loading="lazy"></button>{:else}<div class="noimg"></div>{/if}
                  <div class="grow stack" style="gap:2px">
                    <b class="ellipsis">{h.title}{h.plate > 1 ? ` · plade ${h.plate}` : ''}</b>
                    <span class="tiny muted">{date(h.end)} · {num(h.weight, 1)} g · {dur(h.seconds)}{h.status === 'failed' ? ' · fejlede' : ''}</span>
                    {#if h.energy?.cost != null}<span class="tiny muted">Strøm {kr(h.energy.cost)}</span>{/if}
                    {#if !h.in_fh}<span class="tiny muted">Gemt i Modelbank (slettet i FilamentHub)</span>{/if}
                  </div>
                  <button class="btn ghost small" title="Fjern koblingen" onclick={() => link(h.task_id, false)}><Icon name="close" size={16} /></button>
                </div>
              {/each}
            </div>
          {:else if m.history}
            <div class="small muted">Ikke printet endnu – eller printet under et andet navn. Brug »Kobl print«.</div>
          {:else}
            <div class="small muted">Printhistorikken kommer fra FilamentHubs printbibliotek.</div>
          {/if}
        </section>
      </div>
    </div>
  {/if}
</div>

{#if pick}
  <div class="scrim" role="presentation" onclick={(e) => e.target === e.currentTarget && (pick = null)}>
    <div class="sheet glass pad stack" role="dialog" aria-label="Kobl print">
      <div class="row between"><h2>Kobl et print fra FilamentHub</h2><button class="btn ghost icon-btn" aria-label="Luk" onclick={() => (pick = null)}><Icon name="close" size={18} /></button></div>
      <input class="input" placeholder="Søg i prints" bind:value={pickQ} oninput={openPicker}>
      {#if pick.err}<div class="alert err">{pick.err}</div>{/if}
      {#if !pick.items && !pick.err}<span class="spinner"></span>{/if}
      <div class="picklist">
        {#each pick.items || [] as e}
          <button class="h pickrow" disabled={linkedIds.has(e.task_id)} onclick={async () => { await link(e.task_id, true); pick = null; }}>
            {#if e.has_photo || e.has_cover}<img src={photo(e)} alt="" loading="lazy">{:else}<div class="noimg"></div>{/if}
            <span class="grow stack" style="gap:2px;text-align:left"><b class="ellipsis">{e.title}</b><span class="tiny muted">{date(e.end)} · {num(e.weight, 1)} g</span></span>
            {#if linkedIds.has(e.task_id)}<span class="badge ok">Koblet</span>{/if}
          </button>
        {/each}
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={keys} />
{#if big >= 0 && photos[big]}
  <div class="scrim lb" role="presentation" onclick={(e) => e.target === e.currentTarget && (big = -1)}>
    <figure role="dialog" aria-label="Foto">
      <img src={myPhoto(photos[big].task_id)} alt="">
      <figcaption class="row between small">
        <span>{photos[big].title} · {date(photos[big].end)}{photos[big].plate > 1 ? ` · plade ${photos[big].plate}` : ''}</span>
        <span class="row">
          {#if photos.length > 1}<button class="btn small" onclick={() => (big = (big - 1 + photos.length) % photos.length)} aria-label="Forrige"><Icon name="back" size={16} /></button>
          <span class="mono muted">{big + 1}/{photos.length}</span>
          <button class="btn small" onclick={() => (big = (big + 1) % photos.length)} aria-label="Næste"><Icon name="chev" size={16} /></button>{/if}
          <button class="btn small" onclick={() => (big = -1)}><Icon name="close" size={16} /> Luk</button>
        </span>
      </figcaption>
    </figure>
  </div>
{/if}

{#if confirmDel}
  <div class="scrim" role="presentation" onclick={(e) => e.target === e.currentTarget && (confirmDel = false)}>
    <div class="sheet glass pad stack" role="alertdialog" aria-label="Slet model" style="max-width:420px">
      <h2>Slet »{m.name}«?</h2>
      <p class="small dim">Filen flyttes til mappen <span class="mono">.slettet</span> i /share/modelbank, så du kan fortryde fra Samba. Tags, noter og koblinger til prints slettes.</p>
      <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (confirmDel = false)}>Annullér</button><button class="btn danger" onclick={remove}>Slet</button></div>
    </div>
  </div>
{/if}

<style>
  h2 { font-size: 17px; display: flex; align-items: center; gap: 8px; }
  .title { gap: 4px; }
  .name { flex: 1; min-width: 0; background: transparent; border: 1px solid transparent; border-radius: 10px; color: var(--text); font: inherit;
    font-size: 26px; font-weight: 500; padding: 4px 8px; margin-left: -8px; }
  .name:hover { border-color: var(--panel-border); }
  .name:focus { border-color: color-mix(in srgb, var(--accent) 60%, transparent); outline: none; }
  .fav { background: none; border: 0; color: var(--muted); cursor: pointer; padding: 8px; border-radius: 10px; }
  .fav.on { color: #f3c14b; }
  .facts { gap: 12px; }
  .cols { display: grid; gap: 18px; grid-template-columns: 1fr; }
  @media (min-width: 1100px) { .cols { grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); align-items: start; } }
  .grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .fil { border-left: 3px solid var(--c); padding: 8px 0 8px 12px; display: flex; flex-direction: column; gap: 6px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--c); box-shadow: 0 0 0 1px rgba(255,255,255,.18); display: inline-block; flex: none; }
  .dot.s { background: var(--s); width: 10px; height: 10px; }
  .spool { flex-wrap: wrap; }
  .sale { font-size: 18px; color: var(--accent); font-weight: 600; }
  .chip .x { background: none; border: 0; color: var(--muted); cursor: pointer; padding: 0 0 0 4px; font-size: 16px; line-height: 1; }
  .tagin { width: 140px; min-height: 32px; padding: 4px 10px; font-size: 13px; }
  .desc { white-space: pre-wrap; margin-top: 8px; color: var(--text-dim); max-height: 240px; overflow: auto; }
  .hist, .picklist { display: flex; flex-direction: column; gap: 8px; }
  .picklist { max-height: 60vh; overflow: auto; }
  .h { display: flex; align-items: center; gap: 12px; padding: 6px; border-radius: 10px; background: var(--bg-2); border: 1px solid var(--panel-border); min-width: 0; }
  .h img, .noimg { width: 64px; height: 48px; object-fit: cover; border-radius: 8px; flex: none; background: var(--panel-hi); }
  .pickrow { cursor: pointer; font: inherit; color: inherit; width: 100%; }
  .pickrow:hover:not(:disabled) { border-color: var(--panel-border-hi); }
  .pickrow:disabled { opacity: .6; cursor: default; }
  .scrim { position: fixed; inset: 0; z-index: 100; background: rgba(0,0,0,.55); display: grid; place-items: center; padding: 16px; }
  .sheet { width: 100%; max-width: 640px; background: var(--panel-solid); }
  details summary { cursor: pointer; }
  .seg { gap: 6px; }
  .hero { position: relative; display: block; width: 100%; aspect-ratio: 4 / 3; padding: 0; border: 1px solid var(--panel-border);
    border-radius: var(--radius); overflow: hidden; background: #0d0e10; cursor: zoom-in; }
  .hero img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cap { position: absolute; left: 10px; bottom: 10px; background: rgba(0,0,0,.6); padding: 3px 8px; border-radius: 6px; color: #ddd; }
  .strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
  .sthumb { flex: none; width: 84px; height: 63px; padding: 0; border-radius: 8px; overflow: hidden; border: 2px solid transparent; background: var(--panel-hi); cursor: pointer; }
  .sthumb.on { border-color: var(--accent); }
  .sthumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .hbtn { padding: 0; border: 0; background: none; cursor: zoom-in; border-radius: 8px; }
  .lb figure { margin: 0; max-width: min(1400px, 96vw); display: flex; flex-direction: column; gap: 10px; }
  .lb figure img { max-width: 100%; max-height: 82vh; object-fit: contain; border-radius: 10px; display: block; margin: 0 auto; }
  .lb figcaption { color: var(--text-dim); gap: 12px; flex-wrap: wrap; }
  details[open] summary { margin-bottom: 10px; }
</style>
