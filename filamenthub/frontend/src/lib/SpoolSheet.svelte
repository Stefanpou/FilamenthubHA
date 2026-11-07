<!-- Spool detail: edit weight (incl. weigh-in), location, comment, slot, usage history, archive/delete. -->
<script>
  import Sheet from './ui/Sheet.svelte';
  import Spool from './ui/Spool.svelte';
  import Icon from './ui/Icon.svelte';
  import Confirm from './ui/Confirm.svelte';
  import PriceInput from './ui/PriceInput.svelte';
  import SlicerFields from './SlicerFields.svelte';
  import LocationPicker from './LocationPicker.svelte';
  import { app, reloadAll, toast, toastErr, go } from './stores.svelte.js';
  import { api, post, patch, del } from './api.js';
  import { fmtG, fmtG1, fmtTs, fmtIso, fmtDate, hex, num, pctOf, spoolName, agoDa, today, autoKind, KIND_NAMES } from './util.js';

  let { id, onclose } = $props();
  const s = $derived((app.spools || []).find((x) => x.id === id));
  const slots = $derived((app.status?.slots || []).filter((x) => x.present || x.kind === 'ext'));
  const inSlot = $derived((app.status?.slots || []).find((x) => x.assignment?.spool?.id === id));

  let rem = $state(''), gross = $state(''), loc = $state(''), comment = $state(''), price = $state(''), slotSel = $state('');
  let busy = $state(false), confirm = $state(null), usage = $state(null);
  let seeded = false;
  $effect(() => {
    if (s && !seeded) { seeded = true; rem = String(Math.round(s.remaining)); loc = s.location || ''; comment = s.comment || ''; price = s.price != null ? String(s.price) : ''; }
  });
  $effect(() => { loadUsage(); });
  $effect(() => { loadSlicer(id); });

  // Slicer settings (saved on the filament, else the best matching profile) -> Bambu Studio file / Bambu Cloud.
  // The process values (ironing, first layer and top surface flow) become a second preset when they differ from Bambu's.
  let sl = $state(null), slChoice = $state(null), slProc = $state(null), processes = $state([]);
  let presetName = $state(''), processName = $state(''), slBusy = $state(false);
  async function loadSlicer(sid) {
    try {
      sl = await api(`api/spools/${sid}/slicer`);
      seedSlicer();
      presetName = sl.preset_name;
      processName = sl.process_name;
      if (sl.process && !processes.length) processes = (await api('api/slicer/processes')).items;
    } catch { sl = null; }
  }
  function seedSlicer() {
    slChoice = sl.current ? { profile: sl.current.profile, settings: { ...sl.current.settings } } : null;
    slProc = sl.process ? { base: sl.process.base, settings: { ...sl.process.settings } } : null;
  }
  const procChanged = $derived(!!slProc && (slProc.base !== sl?.process?.base
    || JSON.stringify(slProc.settings) !== JSON.stringify(sl?.process?.settings)));
  const pnum = (v) => Number(String(v ?? '').replace(',', '.').replace('%', '').trim());
  const procOwn = $derived(!!slProc && Object.entries(slProc.settings).some(([k, v]) => {
    const d = processes.find((p) => p.name === slProc.base)?.defaults?.[k];
    return d != null && String(v ?? '').trim() !== '' && pnum(v) !== pnum(d);
  }));
  const slDirty = $derived(!!slChoice && (!sl?.current?.saved || sl.current.profile !== slChoice.profile
    || JSON.stringify(sl.current.settings) !== JSON.stringify(slChoice.settings) || procChanged));
  // a new base preset gives a new process preset name, unless it was typed by hand
  let lastBase = '';
  $effect(() => {
    const b = slProc?.base || '';
    if (lastBase && b && b !== lastBase && processName.startsWith(lastBase)) processName = b + processName.slice(lastBase.length);
    lastBase = b;
  });
  async function saveSlicer(quiet) {
    if (!slChoice?.profile) return;
    sl = await api(`api/spools/${id}/slicer`, { method: 'PUT', body: { ...slChoice, process: slProc } });
    seedSlicer();
    if (!quiet) toast(sl.updated > 1 ? `Printerindstillinger gemt på ${sl.updated} filamenter af samme type og farve` : 'Printerindstillinger gemt på filamentet');
  }
  async function slRun(fn) {
    slBusy = true;
    try { if (slDirty) await saveSlicer(true); await fn(); } catch (e) { toastErr(e); } finally { slBusy = false; }
  }
  function fetchFile(href, name) {
    const a = document.createElement('a');
    a.href = href; a.download = `${name}.json`;
    document.body.append(a); a.click(); a.remove();
  }
  const download = () => slRun(async () => {
    fetchFile(`api/spools/${id}/slicer/preset.json?${new URLSearchParams({ name: presetName })}`, presetName);
    if (procOwn) setTimeout(() => fetchFile(`api/spools/${id}/slicer/process.json?${new URLSearchParams({ name: processName })}`, processName), 400);
  });
  const toCloud = () => slRun(async () => {
    const body = { name: presetName, process_name: processName };
    let r;
    try { r = await post(`api/spools/${id}/slicer/cloud`, body); } catch (e) {
      if (e.status !== 409 || !window.confirm(`${e.message}.\n\nErstat den med de nye værdier?`)) throw e;
      r = await post(`api/spools/${id}/slicer/cloud`, { ...body, replace: true });
    }
    toast(`${r.replaced ? 'Erstattet' : 'Sendt'} i Bambu Cloud${r.process ? ' – filament- og procesprofil' : ''}. Bambu Studio henter ${r.process ? 'dem' : 'den'} ved næste synkronisering`);
  });

  async function loadUsage() {
    try {
      const h = await api('api/history?limit=300');
      const rows = [];
      for (const t of h.tasks || []) for (const d of t.deductions || []) {
        if (d.spool_id === id && d.state === 'applied' && !d.spool_deleted) rows.push({ title: t.title, ts: t.end_time || t.start_time, grams: d.grams, label: d.label });
      }
      usage = rows;
    } catch { usage = []; }
  }
  async function run(fn, msg) {
    busy = true;
    try { await fn(); if (msg) toast(msg); await reloadAll(); } catch (e) { toastErr(e); } finally { busy = false; }
  }
  const tare = $derived(s?.spool_weight != null ? Number(s.spool_weight) : null);
  let tareIn = $state(''), guess = $state(null), tareSeeded = false;
  $effect(() => { if (s && !tareSeeded) { tareSeeded = true; tareIn = tare != null ? String(tare) : ''; loadGuess(); } });
  async function loadGuess() {
    try {
      const r = await api(`api/catalog/spool-weight?${new URLSearchParams({ manufacturer: s.vendor || '', weight: String(s.initial || 1000) })}`);
      guess = r?.estimate || null;
      if (tareIn === '' && guess) tareIn = String(guess.grams);
    } catch { guess = null; }
  }
  const saveTare = () => {
    const v = num(tareIn);
    if (v === null || v < 0) return toastErr(new Error('Indtast hvad den tomme rulle vejer'));
    run(() => patch(`api/spools/${id}`, { spool_weight: v }), 'Tom rullevægt gemt');
  };
  const saveRem = () => {
    const v = num(rem);
    if (v === null) return toastErr(new Error('Indtast vægten'));
    run(() => patch(`api/spools/${id}`, { remaining_weight: v }), 'Vægt gemt');
  };
  const saveGross = () => {
    const v = num(gross);
    if (!v) return toastErr(new Error('Indtast vægten fra badevægten'));
    run(async () => { const r = await patch(`api/spools/${id}`, { gross_weight: v }); rem = String(Math.round(r?.remaining ?? v - tare)); gross = ''; }, 'Vejet og gemt');
  };
  const saveDetails = () => run(async () => {
    if (loc !== (s.location || '')) loc = (await patch(`api/spools/${id}`, { location: loc })).location || '';
    if (comment !== (s.comment || '')) await patch(`api/spools/${id}`, { comment });
    await putPrice();
  }, 'Gemt');
  async function putPrice() {
    const p = num(price);
    if (String(price ?? '').trim() !== '' && (p === null || p < 0)) throw new Error('Prisen skal være et tal');
    if (p !== (s.price ?? null)) await patch(`api/spools/${id}`, p === null ? { clear_price: true } : { price: p });
  }
  const savePrice = () => run(putPrice, num(price) === null ? 'Pris fjernet' : 'Pris gemt');
  const assign = () => {
    if (!slotSel) return toastErr(new Error('Vælg et slot'));
    run(async () => {
      try { await post(`api/slots/${encodeURIComponent(slotSel)}/assign`, { spool_id: id }); }
      catch (e) { if (!/RFID/.test(e.message) || !window.confirm(`${e.message}. Overskriv RFID-tilknytningen?`)) throw e; await post(`api/slots/${encodeURIComponent(slotSel)}/assign`, { spool_id: id, force: true }); }
    }, 'Rulle sat i printeren');
  };
  const archive = () => run(async () => { await patch(`api/spools/${id}`, { archived: !s.archived }); }, s.archived ? 'Gendannet' : 'Arkiveret').then(() => (confirm = null));
  const remove = () => run(async () => { await del(`api/v1/spool/${id}`); onclose(); }, 'Rullen er slettet').then(() => (confirm = null));
  const resetUsage = () => run(() => patch(`api/spools/${id}`, { reset_usage: true }), 'Forbruget er nulstillet').then(() => (confirm = null));
  const setDried = (d) => run(() => patch(`api/spools/${id}`, d ? { dried: d } : { clear_dried: true }), d ? 'Tørret-dato gemt' : 'Tørret-dato fjernet');
  const setCategory = (v) => run(() => patch(`api/spools/${id}`, { category: v }), 'Kategori gemt');
  const untag = () => run(() => patch(`api/spools/${id}`, { clear_tag: true }), 'RFID fjernet');
  const totalUsed = $derived((usage || []).reduce((a, r) => a + (Number(r.grams) || 0), 0));
</script>

{#if s}
<Sheet title={spoolName(s)} {onclose} wide>
  <div class="hero" style="--c:{hex(s.color)}">
    <Spool color={s.color} colors={s.colors} pct={pctOf(s)} size={150} tilt />
    <div class="stats">
      <div class="big mono">{fmtG1(s.remaining)}</div>
      <div class="muted small">af {fmtG(s.initial)} · {Math.round(pctOf(s) * 100)} % tilbage
        {#if s.remaining > 0 && s.remaining < s.initial}<button class="linkbtn" onclick={() => (confirm = 'reset')} disabled={busy}>Nulstil forbrug</button>{/if}</div>
      <div class="row wrap" style="margin-top:10px; gap:6px">
        <span class="badge">#{s.id}</span><span class="badge accent">{s.material}</span>
        {#if s.archived}<span class="badge warn">Arkiveret</span>{/if}
        {#if inSlot}<span class="badge ok">I {inSlot.label}</span>{/if}
        {#if s.tag}<span class="badge">RFID</span>{/if}
        {#if s.dried}<span class="badge dried">Tørret {agoDa(s.dried)}</span>{/if}
      </div>
      <button class="btn small" style="margin-top:12px" onclick={() => { const sid = id; onclose(); go('/add', { edit: sid }); }}><Icon name="edit" size={16} /> Rediger filament</button>
    </div>
  </div>

  <div class="stack" style="margin-top:18px">
    <section class="glass pad stack">
      <div class="eyebrow">Ret vægt</div>
      <div class="row">
        <label class="f grow">Filament tilbage (gram)
          <input class="input" type="number" inputmode="decimal" min="0" step="1" bind:value={rem}></label>
        <button class="btn primary" style="align-self:flex-end" onclick={saveRem} disabled={busy}>Gem</button>
      </div>
      <input class="slider" type="range" min="0" max={Math.max(s.initial || 1000, num(rem) ?? 0)} step="1" value={num(rem) ?? 0} oninput={(e) => (rem = e.target.value)} style="--c:{hex(s.color)}">
      <div class="row">
        <label class="f grow"><span class="row" style="gap:6px"><Icon name="scale" size={14} /> Vej hele rullen (brutto, gram)</span>
          <input class="input" type="number" inputmode="decimal" min="0" disabled={tare == null}
            placeholder={tare != null ? `fx ${Math.round(s.remaining + tare)}  (– tom rulle ${fmtG(tare)})` : 'Tom rullevægt ukendt'} bind:value={gross}></label>
        <button class="btn" style="align-self:flex-end" onclick={saveGross} disabled={busy || tare == null}>Vej</button>
      </div>
      <div class="row">
        <label class="f grow">Tom rulle vejer (gram)
          <input class="input" type="number" inputmode="decimal" min="0" step="1" placeholder="ukendt" bind:value={tareIn}></label>
        <button class="btn" style="align-self:flex-end" onclick={saveTare} disabled={busy || num(tareIn) === tare}>Opdater</button>
      </div>
      <p class="tiny muted" style="margin:-4px 0 0">
        {#if tare == null}Ikke gemt endnu{guess ? ` – forslag: ${fmtG(guess.grams)} (${guess.label})` : ''}.
        {:else if guess && Math.round(guess.grams) !== Math.round(tare)}Automatisk ville være {fmtG(guess.grams)} ({guess.label}). <button class="linkbtn" onclick={() => (tareIn = String(guess.grams))}>Brug den</button>
        {:else if guess}Passer med {guess.label}. Ret det, hvis du har respoolet.{/if}
      </p>
    </section>

    <section class="glass pad stack">
      <div class="eyebrow">Placering, pris og note</div>
      <label class="f">Kategori
        <select class="input" value={s.details?.category || ''} onchange={(e) => setCategory(e.currentTarget.value)} disabled={busy}>
          <option value="">Automatisk ({autoKind(s) ? `${s.material} ${autoKind(s)}` : s.material})</option>
          <option value="-">{s.material} (almindelig)</option>
          {#each KIND_NAMES as k}<option value={k}>{s.material} {k}</option>{/each}
          {#if s.details?.category && s.details.category !== '-' && !KIND_NAMES.includes(s.details.category)}<option value={s.details.category}>{s.material} {s.details.category}</option>{/if}
        </select>
        <span class="tiny muted">Gælder alle ruller af dette filament</span></label>
      <LocationPicker bind:value={loc} spoolId={id} />
      <label class="f">Pris for rullen
        <div class="row">
          <PriceInput bind:value={price} onenter={savePrice} label="Pris for rullen" />
          <button class="btn primary" onclick={savePrice} disabled={busy || num(price) === (s.price ?? null)}>Gem</button>
        </div>
        {#if num(price) > 0 && (s.nominal || s.initial) > 0}<span class="tiny muted">{(num(price) / (s.nominal || s.initial) * 1000).toLocaleString('da-DK', { maximumFractionDigits: 2 })} kr/kg – bruges til at regne printenes pris ud</span>
        {:else if s.price == null && s.kg_price != null}<span class="tiny muted">Bruger filamentets pris: {s.kg_price.toLocaleString('da-DK', { maximumFractionDigits: 2 })} kr/kg</span>{/if}
      </label>
      <label class="f">Kommentar <textarea class="input" rows="2" bind:value={comment}></textarea></label>
      <div class="f">Tørret
        <div class="row">
          <input class="input grow" type="date" max={today()} value={s.dried || ''} onchange={(e) => setDried(e.target.value)} aria-label="Tørret dato">
          <button class="btn" onclick={() => setDried(today())} disabled={busy || s.dried === today()}>I dag</button>
          {#if s.dried}<button class="btn ghost" onclick={() => setDried('')} disabled={busy} aria-label="Fjern tørret-dato">Fjern</button>{/if}
        </div>
        <span class="tiny muted">{s.dried ? `Tørret ${fmtDate(s.dried)} – ${agoDa(s.dried)}` : 'Ikke registreret'}</span>
      </div>
      <button class="btn" onclick={saveDetails} disabled={busy}>Gem detaljer</button>
    </section>

    <section class="glass pad stack">
      <div class="eyebrow">Sæt i printer</div>
      <div class="row">
        <select class="input" bind:value={slotSel}>
          <option value="">Vælg slot …</option>
          {#each slots as sl}<option value={sl.key}>{sl.label}{sl.material ? ` (${sl.material})` : ''}{sl.assignment ? ` – optaget af #${sl.assignment.spool.id}` : ''}</option>{/each}
        </select>
        <button class="btn primary" onclick={assign} disabled={busy}>Sæt i</button>
      </div>
    </section>

    <section class="glass pad stack">
      <div class="row between"><div class="eyebrow">Printerindstillinger</div>
        {#if sl?.manual?.length}<span class="badge warn" title="Ændret manuelt{sl.edited_at ? ' ' + fmtTs(sl.edited_at) : ''}">Ændret manuelt</span>
        {:else if sl?.machine}<span class="tiny muted">{sl.machine}</span>{/if}</div>
      {#if sl === null}<div class="skeleton" style="height:40px"></div>
      {:else if !sl.candidates.length && !sl.current}<p class="small muted">Ingen slicer-profil fundet til {s.material}.</p>
      {:else}
        {#if sl.current && !sl.current.saved}<p class="tiny muted">Forslag ud fra producent og materiale – gem for at knytte det til filamentet.</p>{/if}
        <SlicerFields candidates={sl.candidates} bind:choice={slChoice} bind:process={slProc} {processes}
          defaults={slChoice?.profile === sl.current?.profile ? sl.current?.defaults : null} />
        {#if slChoice?.profile}
          {#if slDirty}<button class="btn" onclick={() => slRun(() => saveSlicer(false))} disabled={slBusy}>Gem på filamentet</button>
            <p class="tiny muted">Gemmes på alle ruller af {s.name} – og på andre filamenter af samme type og farve.</p>{/if}
          <label class="f">Profilnavn i Bambu Studio<input class="input" bind:value={presetName}></label>
          {#if procOwn}<label class="f">Procesprofil i Bambu Studio<input class="input" bind:value={processName}></label>{/if}
          <div class="row wrap">
            <button class="btn grow" onclick={download} disabled={slBusy || !presetName.trim() || (procOwn && !processName.trim())}><Icon name="download" size={16} /> Download til Bambu Studio</button>
            <button class="btn primary grow" onclick={toCloud} disabled={slBusy || !presetName.trim() || (procOwn && !processName.trim())}><Icon name="upload" size={16} /> Send til Bambu Cloud</button>
          </div>
          <p class="tiny muted">Profilerne arver fra Bambus egne {sl.machine}-profiler og ændrer kun det, der står her. Filerne importeres i Bambu Studio under Filer → Importér → Importér konfigurationer.</p>
        {/if}
      {/if}
    </section>

    <section class="glass pad stack">
      <div class="row between"><div class="eyebrow">Forbrug</div>{#if usage?.length}<span class="small muted mono">{fmtG1(totalUsed)} i alt</span>{/if}</div>
      {#if usage === null}<div class="skeleton" style="height:40px"></div>
      {:else if !usage.length}<p class="small muted">Ingen registrerede prints på denne rulle endnu.</p>
      {:else}
        {#each usage.slice(0, 12) as r}
          <div class="row small"><span class="grow ellipsis">{r.title || 'Print'} <span class="muted">· {r.label}</span></span><span class="muted tiny">{fmtIso(r.ts)}</span><b class="mono">{fmtG1(r.grams)}</b></div>
        {/each}
      {/if}
      <dl class="kv small" style="margin-top:6px">
        <dt>Sidst brugt</dt><dd>{s.last_used ? fmtIso(s.last_used) : '–'}</dd>
        <dt>Tom rulle vejer</dt><dd>{tare != null ? fmtG(tare) : 'ukendt'}</dd>
        {#if s.temps?.[0] || s.temps?.[1]}<dt>Dyse / plade</dt><dd class="mono">{s.temps[0] ?? '–'} / {s.temps[1] ?? '–'} °C</dd>{/if}
        {#if s.details?.nozzle_range || s.details?.bed_range}<dt>Anbefalet</dt><dd class="mono">dyse {s.details.nozzle_range || '–'} · plade {s.details.bed_range || '–'} °C</dd>{/if}
        {#if s.details?.td}<dt>TD (HueForge)</dt><dd class="mono">{s.details.td}</dd>{/if}
        {#if s.kg_price != null}<dt>Pris</dt><dd class="mono">{s.kg_price.toLocaleString('da-DK', { maximumFractionDigits: 2 })} kr/kg</dd>{/if}
        {#if s.details?.color_name}<dt>Farvenavn</dt><dd>{s.details.color_name}</dd>{/if}
        {#if s.details?.finish}<dt>Finish</dt><dd>{s.details.finish}</dd>{/if}
        {#if s.details?.properties}<dt>Egenskaber</dt><dd>{s.details.properties}</dd>{/if}
        {#if s.details?.spool_type}<dt>Rulletype</dt><dd>{s.details.spool_type}</dd>{/if}
        {#if s.article_number}<dt>Varenummer</dt><dd class="mono">{s.article_number}</dd>{/if}
        {#if s.details?.ean}<dt>EAN</dt><dd class="mono">{s.details.ean}</dd>{/if}
        {#if s.tag}<dt>RFID</dt><dd class="mono">{s.tag.slice(0, 8)}… <button class="btn small ghost" onclick={untag}>Fjern</button></dd>{/if}
      </dl>
    </section>
  </div>

  {#snippet footer()}
    <button class="btn grow" onclick={() => (confirm = 'archive')}><Icon name="archive" size={18} /> {s.archived ? 'Gendan' : 'Arkivér'}</button>
    <button class="btn danger grow" onclick={() => (confirm = 'delete')}><Icon name="trash" size={18} /> Slet</button>
  {/snippet}
</Sheet>
{#if confirm === 'archive'}
  <Confirm title={s.archived ? 'Gendan rulle?' : 'Arkivér rulle?'} message={s.archived ? 'Rullen kommer tilbage i lageret.' : 'Rullen skjules fra lageret og fjernes fra printerens slot. Forbrugshistorikken bevares.'} confirmLabel={s.archived ? 'Gendan' : 'Arkivér'} onconfirm={archive} oncancel={() => (confirm = null)} />
{:else if confirm === 'reset'}
  <Confirm title="Nulstil forbrug?" message={`Rullen starter forfra på ${fmtG(s.remaining)} og vises som 100 % tilbage – fx når en ny rulle vejede lidt under ${fmtG(s.initial)}. Prisen pr. kg og printhistorikken ændres ikke.`} confirmLabel="Nulstil" onconfirm={resetUsage} oncancel={() => (confirm = null)} />
{:else if confirm === 'delete'}
  <Confirm danger title="Slet rulle permanent?" message="Rullen slettes for altid. Overvej at arkivere i stedet, hvis du vil bevare historikken." confirmLabel="Slet" onconfirm={remove} oncancel={() => (confirm = null)} />
{/if}
{/if}

<style>
  .hero { display: flex; gap: 22px; align-items: center; padding: 14px 4px 4px; position: relative; }
  .big { font-size: 36px; font-weight: 500; line-height: 1; font-family: var(--font); font-variant-numeric: tabular-nums; }
  .stats { position: relative; }
  .slider { width: 100%; accent-color: var(--c); height: 28px; }
  .badge.dried { color: var(--warn); }
  .linkbtn { background: none; border: 0; padding: 0; color: var(--accent); font: inherit; cursor: pointer; text-decoration: underline; }
</style>
