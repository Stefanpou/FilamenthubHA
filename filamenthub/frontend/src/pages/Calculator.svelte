<!-- Price calculator: filament + electricity (price in the coming print window) + margin. -->
<script>
  import Icon from '../lib/ui/Icon.svelte';
  import { app, route } from '../lib/stores.svelte.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';

  const KEY = 'fh-calc';
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch { return {}; } })();
  const line = (o = {}) => ({ spool: '', grams: '', kg: saved.kg || '120', ...o });
  // Older saves had one spool; they become the first line.
  const { spool: _s, grams: _g, ...rest } = saved;
  let f = $state({ hours: '', minutes: '', kg: '120', watts: '', mode: 'pct', pct: '50', fixed: '', ...rest,
    lines: saved.lines?.length ? saved.lines.map((l) => line(l)) : [line({ spool: _s || '', grams: _g || '' })] });
  let win = $state(null), byMat = $state({}), avgW = $state('147'), measured = $state(null), taskTitle = $state('');

  const num = (v) => { const n = parseFloat(String(v ?? '').replace(',', '.')); return Number.isFinite(n) ? n : 0; };
  const kr = (v) => (v == null ? '–' : `${v.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kr`);
  const dec = (v, d = 1) => v.toLocaleString('da-DK', { maximumFractionDigits: d });

  $effect(() => { const s = JSON.stringify({ ...f }); try { localStorage.setItem(KEY, s); } catch { /* private mode */ } });

  onMount(async () => {
    try {
      const p = await api('api/power');
      avgW = String(p.avg_watts || p.watts || 147);
      byMat = p.by_material || {};
    } catch { /* default stays */ }
    if (!f.watts) f.watts = avgW;
    const tid = route.query.task;
    if (tid) {
      try {
        const t = ((await api('api/history?limit=300')).tasks || []).find((x) => x.task_id === tid);
        if (t) {
          taskTitle = t.title;
          const used = (t.deductions || []).filter((d) => d.grams > 0 && d.state !== 'skipped');
          if (used.length) f.lines = used.map((d) => line({ spool: d.spool_id ? String(d.spool_id) : '', grams: String(Math.round(d.grams * 100) / 100) }));
          const hrs = t.energy?.hours || ((Date.parse(t.end_time) - Date.parse(t.start_time)) / 3.6e6) || 0;
          if (hrs > 0) { f.hours = String(Math.floor(hrs)); f.minutes = String(Math.round((hrs % 1) * 60)); }
          if (t.energy?.cost != null) measured = t.energy;
        }
      } catch { /* the calculator works without it */ }
    }
  });

  const spools = $derived((app.spools || []).filter((s) => !s.archived));
  const spoolOf = (l) => (l.spool ? spools.find((s) => String(s.id) === l.spool) : null);
  const kgOf = (l) => spoolOf(l)?.kg_price ?? num(l.kg);
  const lineCost = (l) => (num(l.grams) / 1000) * kgOf(l);
  const totalGrams = $derived(f.lines.reduce((a, l) => a + num(l.grams), 0));
  const hours = $derived(num(f.hours) + num(f.minutes) / 60);
  const kwh = $derived(measured ? measured.kwh : (num(f.watts) * hours) / 1000);

  const mainMat = $derived.by(() => {
    let best = null, bg = 0;
    for (const l of f.lines) { const g = num(l.grams), m = spoolOf(l)?.material; if (m && g > bg) { bg = g; best = String(m).toUpperCase(); } }
    return best;
  });
  const matData = $derived(mainMat ? byMat[mainMat] : null);
  $effect(() => {
    if (f.wattsManual || measured) return;
    const w = matData?.watts ?? (mainMat ? avgW : null);
    if (w != null && String(Math.round(w)) !== f.watts) f.watts = String(Math.round(w));
  });

  let t;
  $effect(() => {
    const h = hours;
    clearTimeout(t);
    t = setTimeout(async () => { try { win = await api(`api/power/window?hours=${h || 0}`); } catch { win = null; } }, 300);
  });

  const filament = $derived(f.lines.reduce((a, l) => a + lineCost(l), 0));
  const power = $derived(measured ? measured.cost : win?.price != null ? kwh * win.price : null);
  const cost = $derived(filament + (power || 0));
  const sale = $derived(f.mode === 'fixed' ? num(f.fixed) : cost * (1 + num(f.pct) / 100));
  const margin = $derived(cost > 0 ? (sale / cost - 1) * 100 : null);
</script>

<div class="page stack" style="gap:18px">
  <h1>Prisberegner</h1>
  {#if taskTitle}<div class="alert">Udfyldt fra printet <b>{taskTitle}</b>{#if measured} – strømmen er målt{/if}. <a href="#/calc" onclick={() => { measured = null; taskTitle = ''; }}>Ryd</a></div>{/if}

  <div class="cols">
    <div class="stack" style="gap:18px">
      <section class="glass pad stack rise">
        <h2><Icon name="printer" size={20} /> Print-oplysninger</h2>
        {#each f.lines as l, i}
          {@const sp = spoolOf(l)}
          <div class="fl" style="--c:#{sp?.color || '7c8aa5'}">
            <div class="row between"><span class="eyebrow"><i class="dot"></i> Filament {f.lines.length > 1 ? i + 1 : ''}</span>
              {#if f.lines.length > 1}<button class="btn small ghost" aria-label="Fjern filament {i + 1}" onclick={() => f.lines.splice(i, 1)}><Icon name="close" size={16} /></button>{/if}</div>
            <label class="f">Spole
              <select class="input" bind:value={l.spool}>
                <option value="">Manuel kg-pris</option>
                {#each spools as s}<option value={String(s.id)} disabled={s.kg_price == null}>#{s.id} {s.vendor} {s.name} {s.material}{s.kg_price == null ? ' (ingen pris)' : ` – ${dec(s.kg_price, 2)} kr/kg`}</option>{/each}
              </select></label>
            <div class="grid2">
              <label class="f">Gram fra slicer<input class="input mono" inputmode="decimal" bind:value={l.grams} placeholder="0"></label>
              {#if sp}
                <div class="f">Tilbage på spolen<span class="mono val" class:short={num(l.grams) > sp.remaining}>{dec(sp.remaining, 0)} g{num(l.grams) > sp.remaining ? ' – for lidt' : ''}</span></div>
              {:else}
                <label class="f">Kg-pris (kr/kg)<input class="input mono" inputmode="decimal" bind:value={l.kg}></label>
              {/if}
            </div>
            <div class="row between small"><span class="muted">{dec(kgOf(l), 2)} kr/kg</span><b class="mono">{kr(lineCost(l))}</b></div>
          </div>
        {/each}
        <button class="btn" onclick={() => f.lines.push(line())}><Icon name="plus" size={16} /> Tilføj farve</button>
        <div class="grid2">
          <label class="f">Printtid – timer<input class="input mono" inputmode="numeric" bind:value={f.hours} placeholder="0"></label>
          <label class="f">Printtid – minutter<input class="input mono" inputmode="numeric" bind:value={f.minutes} placeholder="0"></label>
          {#if !measured}<label class="f">Gns. printeffekt (W)<input class="input mono" inputmode="decimal" bind:value={f.watts} oninput={() => (f.wattsManual = true)}></label>{/if}
        </div>
        {#if !measured && mainMat}
          <div class="tiny muted">{matData ? `Målt for ${mainMat}: ${dec(matData.watts, 0)} W (${matData.prints} print, ${dec(matData.hours, 0)} t)` : `Ingen målinger for ${mainMat} endnu – bruger gennemsnittet`}
            {#if f.wattsManual} · <a href="#/calc" onclick={(e) => { e.preventDefault(); f.wattsManual = false; }}>Brug målt</a>{/if}</div>
        {/if}
      </section>

      <section class="glass pad stack rise" style="--i:1">
        <h2><Icon name="tag" size={20} /> Avance</h2>
        <div class="seg">
          <button class:on={f.mode === 'pct'} onclick={() => (f.mode = 'pct')}>Procent</button>
          <button class:on={f.mode === 'fixed'} onclick={() => (f.mode = 'fixed')}>Fast pris</button>
        </div>
        {#if f.mode === 'pct'}
          <label class="f">Avance i procent<input class="input mono" inputmode="decimal" bind:value={f.pct}></label>
        {:else}
          <label class="f">Fast salgspris (kr)<input class="input mono" inputmode="decimal" bind:value={f.fixed}></label>
          <div class="row between small"><span class="muted">Avance ved fast pris</span><b class="mono">{margin == null ? '–' : `${Math.round(margin)} %`}</b></div>
        {/if}
      </section>
    </div>

    <section class="stack rise" style="--i:2; gap:12px">
      <div class="glass pad tile big"><span class="muted small">Kostpris i alt</span><b class="mono">{kr(cost)}</b></div>
      <div class="tiles">
        <div class="glass pad tile"><span class="muted small">Salgspris</span><b class="mono">{kr(sale)}</b></div>
        <div class="glass pad tile"><span class="muted small">Du tjener</span><b class="mono" class:neg={sale - cost < 0}>{kr(sale - cost)}</b></div>
        <div class="glass pad tile"><span class="muted small">Filament</span><b class="mono">{kr(filament)}</b></div>
        <div class="glass pad tile"><span class="muted small">Strøm</span><b class="mono">{kr(power)}</b></div>
      </div>
      <div class="glass pad list small">
        <div class="row between"><span class="muted">Filament i alt</span><span class="mono">{dec(totalGrams, 2)} g{f.lines.length > 1 ? ` · ${f.lines.length} farver` : ''}</span></div>
        <div class="row between"><span class="muted">Gns. filamentpris</span><span class="mono">{totalGrams ? `${dec(filament / totalGrams * 1000, 2)} kr/kg` : '–'}</span></div>
        <div class="row between"><span class="muted">Strømforbrug</span><span class="mono">{dec(kwh, 3)} kWh{measured ? ' (målt)' : ''}</span></div>
        {#if measured}
          <div class="row between"><span class="muted">Gns. elpris under printet</span><span class="mono">{measured.kwh ? dec(measured.cost / measured.kwh, 3) : '–'} kr/kWh</span></div>
        {:else}
          <div class="row between"><span class="muted">Gns. elpris i printvinduet (fra nu)</span><span class="mono">{win?.price != null ? `${dec(win.price, 3)} kr/kWh` : '–'}</span></div>
          <div class="row between"><span class="muted">Kendte elpriser i vinduet</span><span class="mono">{win ? `${Math.round(win.coverage * 100)} %` : '–'}</span></div>
          {#if win && win.price == null}<div class="muted tiny">Ingen elpris – vælg en prissensor eller en fast pris under <a href="#/settings">Indstillinger → Elforbrug</a>.</div>{/if}
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
  h1 { font-size: 26px; }
  h2 { font-size: 17px; display: flex; align-items: center; gap: 8px; }
  .cols { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; }
  @media (min-width: 900px) { .cols { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; } }
  .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .fl { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: 16px; border: 1px solid var(--panel-border);
    border-left: 3px solid var(--c); }
  .fl .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--c); margin-right: 4px; }
  .val { padding: 12px 0; color: var(--text); font-size: 15px; font-weight: 500; }
  .val.short { color: var(--warn); }
  .tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .tile { display: flex; flex-direction: column; gap: 4px; }
  .tile b { font-size: 19px; }
  .tile.big b { font-size: 28px; color: var(--accent); }
  .neg { color: var(--danger, #ff6b6b); }
  .list { display: flex; flex-direction: column; gap: 10px; }
  .list .row > :last-child { text-align: right; }
  .seg { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; padding: 4px; border-radius: 14px; background: var(--panel-border); }
  .seg button { border: 0; padding: 9px; border-radius: 10px; background: transparent; color: var(--muted); font-weight: 600; cursor: pointer; }
  .seg button.on { background: var(--accent); color: #061018; }
</style>
