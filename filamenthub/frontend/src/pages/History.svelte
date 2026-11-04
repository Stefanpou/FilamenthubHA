<script>
  import Spool from '../lib/ui/Spool.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import Resolve from './Resolve.svelte';
  import { app, route, go, toast, toastErr, reloadAll } from '../lib/stores.svelte.js';
  import { api, post } from '../lib/api.js';
  import { fmtG, fmtG1, fmtKg, fmtKr, fmtIso, fmtTs, hex, nf } from '../lib/util.js';
  import { onMount } from 'svelte';

  let tab = $state(route.query.tab === 'prints' ? 'prints' : 'stats');
  let stats = $state(null), hist = $state(null), events = $state(null), err = $state('');
  let mode = $state('weeks'), sel = $state(null), resolving = $state(null), busy = $state(false);

  async function loadStats() { try { stats = await api('api/stats'); } catch (e) { err = e.message; } }
  async function loadHist() { try { hist = await api('api/history'); } catch (e) { err = e.message; } }
  onMount(() => { loadStats(); loadHist(); });

  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'maj', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
  function isoWeek(iso) {
    const d = new Date(`${iso}T12:00:00`);
    d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
    const w1 = new Date(d.getFullYear(), 0, 4);
    return 1 + Math.round(((d - w1) / 86400000 - 3 + ((w1.getDay() + 6) % 7)) / 7);
  }
  const series = $derived.by(() => {
    if (!stats) return [];
    return mode === 'weeks'
      ? stats.weeks.map((w) => ({ label: String(isoWeek(w.start)), long: `Uge ${isoWeek(w.start)}`, grams: w.grams }))
      : stats.months.map((m) => { const [y, mo] = m.month.split('-').map(Number); return { label: MONTHS[mo - 1], long: `${MONTHS[mo - 1]} ${y}`, grams: m.grams }; });
  });
  const maxG = $derived(Math.max(1, ...series.map((x) => x.grams)));
  const MAT_COL = ['#3fd0b2', '#e0a54a', '#6b9cf0', '#e07a5f', '#c9c26a', '#a68cd9', '#7fb069', '#a1a1a6'];
  const matTotal = $derived(stats ? stats.materials.reduce((a, m) => a + m.grams, 0) || 1 : 1);
  const grams = (t) => (t.deductions || []).filter((d) => d.state === 'applied').reduce((a, d) => a + d.grams, 0);
  const STATE = { applied: 'trukket', pending: 'venter', skipped: 'sprunget over', uncertain: 'tjek', applying: '…' };

  async function act(fn, msg) {
    busy = true;
    try { const r = await fn(); if (msg) toast(typeof msg === 'function' ? msg(r) : msg); await loadHist(); await reloadAll(); } catch (e) {
      if (/kun .* g tilbage|vejet efter printet/.test(e.message) && confirm(`${e.message}\n\nTræk fra alligevel?`)) return act(() => fn(true), msg);
      toastErr(e);
    } finally { busy = false; }
  }
  const direct = (d) => act((force) => post(`api/deductions/${d.id}/resolve`, { spool_id: d.spool.id, grams: d.grams, ...(force ? { force: true } : {}) }), `${fmtG1(d.grams)} trukket fra #${d.spool.id}`);
  const skip = (d) => act(() => post(`api/deductions/${d.id}/resolve`, { spool_id: null }), 'Sprunget over');
  const checked = (d) => act(() => post(`api/deductions/${d.id}/checked`, {}), 'Markeret som tjekket');
  const syncNow = () => act(() => post('api/sync-now', {}), (r) => (r.error ? r.error : `Synkroniseret (${r.processed || 0} nye prints)`));
  async function loadEvents(e) {
    if (!e.target.open || events) return;
    try { events = await api('api/events?limit=150'); } catch { events = []; }
  }
</script>

<div class="page stack" style="gap:16px">
  <div class="row between"><h1>Historik</h1>
    <div class="seg"><button class:on={tab === 'stats'} onclick={() => (tab = 'stats')}>Statistik</button>
      <button class:on={tab === 'prints'} onclick={() => (tab = 'prints')}>Prints{#if hist?.pending?.length}<i class="cnt">{hist.pending.length}</i>{/if}</button></div></div>
  <div class="row"><button class="btn small" onclick={() => go('/library')}><Icon name="image" size={16} /> Printbibliotek</button></div>
  {#if err}<div class="alert err">{err}</div>{/if}

  {#if tab === 'stats'}
    {#if !stats}<div class="skeleton" style="height:200px"></div>
    {:else}
      {#if stats.spoolman_error}<div class="alert err">Lageret svarer ikke – priser og rullenavne mangler: {stats.spoolman_error}</div>{/if}
      <div class="tiles">
        <div class="glass tile rise"><b class="mono">{fmtKg(stats.total)}</b><span>brugt i alt{stats.since ? ` siden ${new Date(stats.since * 1000).toLocaleDateString('da-DK', { month: 'short', year: 'numeric' })}` : ''}</span></div>
        <div class="glass tile rise" style="--i:1"><b class="mono">{fmtKg(stats.last30)}</b><span>seneste 30 dage</span></div>
        <div class="glass tile rise" style="--i:2"><b class="mono">{stats.prints}</b><span>prints trukket fra</span></div>
        <div class="glass tile rise" style="--i:3"><b class="mono">{stats.cost != null ? fmtKr(stats.cost) : '–'}</b><span>{stats.cost != null ? (stats.cost_coverage < 0.99 ? `filament (pris kendt for ${Math.round(stats.cost_coverage * 100)} %)` : 'filament') : 'pris ukendt – tilføj pris på ruller'}</span></div>
      </div>

      <section class="glass pad stack rise">
        <div class="row between"><h3>Forbrug over tid</h3>
          <div class="seg small"><button class:on={mode === 'weeks'} onclick={() => { mode = 'weeks'; sel = null; }}>Uger</button><button class:on={mode === 'months'} onclick={() => { mode = 'months'; sel = null; }}>Måneder</button></div></div>
        <svg viewBox="0 0 {series.length * 28} 140" class="chart" role="img" aria-label="Forbrug over tid">
          {#each [0.25, 0.5, 0.75, 1] as g}<line x1="0" x2={series.length * 28} y1={110 - g * 100} y2={110 - g * 100} stroke="rgba(237,237,237,.06)" />{/each}
          {#each series as x, i}
            {@const h = x.grams > 0 ? Math.max(3, (x.grams / maxG) * 100) : 1.5}
            <g onclick={() => (sel = i)} onkeydown={(e) => e.key === 'Enter' && (sel = i)} role="button" tabindex="0" class="bargrp" class:sel={sel === i}>
              <rect x={i * 28} y="0" width="28" height="130" fill="transparent"/>
              <rect class="bar" x={i * 28 + 5} y={110 - h} width="18" height={h} rx="3" fill={x.grams <= 0 ? 'rgba(237,237,237,.08)' : (sel === null ? i === series.length - 1 : sel === i) ? 'var(--accent)' : '#4a4a4f'}/>
              <text x={i * 28 + 14} y="127" text-anchor="middle" class="lbl">{mode === 'weeks' && i % 2 ? '' : x.label}</text>
            </g>
          {/each}
        </svg>
        <p class="small muted">{sel !== null ? `${series[sel].long}: ${fmtG(series[sel].grams)}` : 'Tryk på en søjle for at se forbruget.'}</p>
      </section>

      {#if stats.materials.length}
        <section class="glass pad stack rise">
          <h3>Materialer</h3>
          <div class="stackbar">{#each stats.materials as m, i}<i style="flex:{m.grams};background:{MAT_COL[i % 8]}" title="{m.material}: {fmtG(m.grams)}"></i>{/each}</div>
          {#each stats.materials as m, i}
            <div class="row small"><i class="sq" style="background:{MAT_COL[i % 8]}"></i><span class="grow">{m.material}</span><span class="muted">{Math.round((m.grams / matTotal) * 100)} %</span><b class="mono" style="min-width:70px;text-align:right">{fmtKg(m.grams)}</b></div>
          {/each}
        </section>
      {/if}

      {#if stats.spools.length}
        <section class="glass pad stack rise">
          <h3>Mest brugte farver</h3>
          {#each stats.spools as s}
            <div class="row small">
              <Spool color={s.color} colors={s.colors} pct={1} size={30} ring={false} glow={false} />
              <div class="grow" style="min-width:0">
                <div class="row between"><span class="ellipsis">#{s.id} {s.name || s.material}{s.deleted ? ' (slettet)' : s.archived ? ' (arkiveret)' : ''}</span><b class="mono">{fmtKg(s.grams)}</b></div>
                <div class="bar" style="--c:{hex(s.color)}; margin-top:4px"><i style="width:{(s.grams / stats.spools[0].grams) * 100}%"></i></div>
              </div>
            </div>
          {/each}
        </section>
      {/if}

      {#if stats.forecast.length}
        <section class="glass pad stack rise">
          <h3>Løber tør om</h3><p class="tiny muted">Ud fra forbruget de seneste 30 dage.</p>
          {#each stats.forecast as f}
            <div class="row small"><Spool color={f.color} colors={f.colors} pct={f.remaining / 1000} size={34} glow={false} />
              <div class="grow"><b>#{f.id} {f.name}</b><div class="muted">{fmtG(f.remaining)} tilbage · {fmtG1(f.per_day)}/dag</div></div>
              <span class="badge {f.days_left <= 7 ? 'warn' : ''}">ca. {f.days_left} {f.days_left === 1 ? 'dag' : 'dage'}</span></div>
          {/each}
        </section>
      {/if}
      {#if stats.spoolman && !stats.spoolman_error}
        <section class="glass pad rise"><h3 style="margin-bottom:10px">Hele lageret</h3>
          <dl class="kv"><dt>Brugt i alt</dt><dd>{fmtKg(stats.spoolman.used)}</dd><dt>Tilbage</dt><dd>{fmtKg(stats.spoolman.remaining)} på {stats.spoolman.active} ruller</dd></dl></section>
      {/if}
    {/if}

  {:else}
    {#if !hist}<div class="skeleton" style="height:200px"></div>
    {:else}
      {#if hist.pending.length}
        <section class="glass pad stack rise">
          <h3>Venter på dig</h3>
          {#each hist.pending as d (d.id)}
            {@const unknown = !(d.full_grams > 0)}
            <div class="pend">
              <i class="dot" style="background:{hex(d.color || d.spool?.color)}"></i>
              <div class="grow stack" style="gap:4px">
                <b>{d.title || d.task_id}</b>
                <div class="small">{d.label}{d.material ? ` · ${d.material}` : ''} · {#if unknown}<b>forbrug ukendt</b>{:else}<b>{fmtG1(d.grams)}</b>{/if}{d.spool ? ` · #${d.spool.id} ${d.spool.name}` : ''}</div>
                {#if d.note}<div class="tiny muted">{d.note}</div>{/if}
                <div class="row wrap" style="gap:6px">
                  {#if d.state === 'uncertain'}<button class="btn small primary" onclick={() => checked(d)} disabled={busy}>Vægten er tjekket</button>
                  {:else}
                    <button class="btn small primary" onclick={() => (resolving = d)}>{unknown ? 'Indtast forbrug' : 'Vælg rulle'}</button>
                    {#if d.spool && !unknown}<button class="btn small" onclick={() => direct(d)} disabled={busy}>Træk fra #{d.spool.id}</button>{/if}
                    <button class="btn small ghost" onclick={() => skip(d)} disabled={busy}>Spring over</button>
                  {/if}
                </div>
              </div>
            </div>
          {/each}
        </section>
      {/if}

      <div class="row between"><span class="eyebrow">Seneste prints</span><button class="btn small" onclick={syncNow} disabled={busy}><Icon name="refresh" size={16} /> Synkronisér nu</button></div>
      {#if !hist.tasks.length}
        <div class="glass pad muted small">Ingen prints registreret endnu. Forbrug trækkes automatisk fra, når et print er færdigt.</div>
      {/if}
      {#each hist.tasks as t, i (t.task_id)}
        <div class="glass pad stack rise" style="--i:{Math.min(i, 8)}; gap:8px">
          <div class="row between"><b class="grow ellipsis">{t.title}</b><b class="mono">{fmtG1(grams(t))}</b></div>
          <div class="row between tiny muted"><span>{fmtIso(t.end_time || t.start_time)}{#if t.energy} · {t.energy.kwh.toLocaleString('da-DK', { maximumFractionDigits: 2 })} kWh{#if t.energy.cost != null} · {t.energy.cost.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kr{/if}{/if}</span>
            <a href={`#/calc?task=${encodeURIComponent(t.task_id)}`}>Beregn pris</a></div>
          {#each t.deductions as d}
            <div class="row small"><i class="dot sm" style="background:{hex(d.color || d.spool?.color)}"></i>
              <span class="grow ellipsis">{d.label} → {d.spool ? `#${d.spool.id} ${d.spool.name}` : '–'}</span>
              <span class="mono">{fmtG1(d.grams)}</span><span class="badge {d.state === 'applied' ? 'ok' : d.state === 'skipped' ? '' : 'warn'}">{STATE[d.state] || d.state}</span></div>
          {/each}
        </div>
      {/each}
      <details class="glass pad" ontoggle={loadEvents}><summary>Hændelseslog</summary>
        <div class="small" style="margin-top:10px">
          {#if !events}Henter …{:else}
            {#each events as e}<div style="margin-bottom:4px"><span class="muted tiny">{fmtTs(e.ts)}</span> <span style="color:{e.level === 'error' ? 'var(--danger)' : e.level === 'warning' ? 'var(--warn)' : 'inherit'}">{e.message}</span></div>{:else}Tom{/each}
          {/if}
        </div></details>
    {/if}
  {/if}
</div>

{#if resolving}<Resolve d={resolving} onclose={() => (resolving = null)} ondone={() => { resolving = null; loadHist(); reloadAll(); }} />{/if}

<style>
  h1 { font-size: 28px; font-weight: 500; }
  .seg { display: inline-flex; background: var(--panel); border: 1px solid var(--panel-border); border-radius: 10px; padding: 2px; gap: 2px; }
  .seg button { border: 0; background: transparent; color: var(--muted); padding: 8px 12px; border-radius: 8px; font-weight: 500; cursor: pointer; transition: background-color .14s, color .14s; display: flex; gap: 8px; align-items: center; }
  .seg.small button { padding: 4px 12px; font-size: 13px; }
  .seg button.on { background: var(--panel-hi); color: var(--text); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); }
  .cnt { background: var(--warn); color: #1d1404; font-size: 10px; font-weight: 600; border-radius: 8px; min-width: 16px; height: 16px; display: grid; place-items: center; font-style: normal; }
  .tiles { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  @media (min-width: 800px) { .tiles { grid-template-columns: repeat(4, 1fr); } }
  .tile { padding: 16px; display: flex; flex-direction: column; gap: 4px; }
  .tile b { font-size: 28px; font-weight: 500; line-height: 1.1; }
  .tile span { font-size: 13px; color: var(--muted); }
  .chart { width: 100%; height: auto; max-height: 220px; overflow: visible; }
  .bargrp { cursor: pointer; outline: none; }
  .bar { transition: fill .14s; }
  .bargrp:hover .bar { opacity: .85; }
  .lbl { fill: var(--muted); font-size: 9px; font-family: var(--mono); }
  .stackbar { display: flex; height: 12px; border-radius: 4px; overflow: hidden; gap: 2px; }
  .stackbar i { display: block; min-width: 3px; }
  .sq { width: 10px; height: 10px; border-radius: 3px; }
  .pend { display: flex; gap: 12px; padding-top: 10px; border-top: 1px solid var(--panel-border); }
  .pend:first-of-type { border-top: 0; padding-top: 0; }
  .dot { width: 14px; height: 14px; border-radius: 50%; flex: none; margin-top: 4px; border: 1.5px solid rgba(255,255,255,.3); }
  .dot.sm { width: 10px; height: 10px; margin: 0; }
  summary { cursor: pointer; font-weight: 500; }
</style>
