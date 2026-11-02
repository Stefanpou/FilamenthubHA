<!-- Live printer dashboard (X2D/H2D, X1/P1/P2, A1): illustration + AMS + external holder + the 3D model of the running print.
     No required props. `compact` = small kiosk card (Home Assistant). Subscribes to /api/live itself. -->
<script>
  import { onMount } from 'svelte';
  import PrinterSvg from './PrinterSvg.svelte';
  import PrinterSvgGeneric from './PrinterSvgGeneric.svelte';
  import ModelCanvas from './ModelCanvas.svelte';
  import { live, useLive } from './live.svelte.js';
  import { fmtRemaining, fmtEta, temp, SPEEDS, hex, STATE_TONE, printerFamily, humidityText } from './format.js';

  let { compact = false } = $props();

  let still = $state(false);
  onMount(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    still = mq.matches;
    const on = (e) => (still = e.matches);
    mq.addEventListener?.('change', on);
    const stop = useLive();
    return () => { mq.removeEventListener?.('change', on); stop(); };
  });

  const d = $derived(live.data);
  const p = $derived(d?.printer || null);
  const slots = $derived(d?.slots || []);
  const ams = $derived(d?.ams || []);
  const model = $derived(d?.model || null);
  const tone = $derived(p?.online === false ? 'idle' : STATE_TONE[p?.gcode_state] || 'idle');
  const activeSlot = $derived(slots.find((s) => s.active) || null);
  const busy = $derived(['RUNNING', 'PAUSE', 'PREPARE'].includes(p?.gcode_state));
  const layerFrac = $derived.by(() => {
    if (!p) return 0;
    if (p.total_layers > 0) return Math.min(1, Math.max(0, p.layer / p.total_layers));
    return Math.min(1, (p.percent || 0) / 100);
  });
  // The model only grows with the print while it is running; a finished print shows the whole part, idle the last job.
  const shownProgress = $derived(p?.gcode_state === 'FINISH' ? 1 : busy ? layerFrac : model?.job === 'last' ? 1 : layerFrac);
  const filamentColor = $derived(hex(activeSlot?.color, 'cfd6e4'));
  const stateText = $derived(live.status === 'unauthorized' ? 'Log ind' : p ? (p.online ? p.state_da : 'Offline') : live.status === 'offline' ? 'Ingen forbindelse' : 'Forbinder');
  const eta = $derived(busy ? fmtEta(p?.remaining_min) : '');
  const pct = $derived(Math.max(0, Math.min(100, Math.round(p?.percent || 0))));
  const R = 26, C = 2 * Math.PI * R;
  const noz = (i) => p?.nozzles?.find((n) => n.id === i);
  // X2D/H2D: extruder id 0 is the right nozzle, id 1 the left one (single-nozzle printers: just "Dyse")
  const nozName = (i) => ((p?.nozzles?.length || 0) < 2 ? 'Dyse' : i === 1 ? 'Venstre dyse' : i === 0 ? 'Højre dyse' : `Dyse ${i + 1}`);
  const unit0 = $derived(ams[0] || null);
  const family = $derived(printerFamily(p));
  const jobLabel = $derived(model?.job === 'last' && !busy ? 'Seneste print' : '');
</script>

<section class="pv {tone}" class:compact class:off={p && !p.online} aria-label="Printer">
  <div class="hud">
    <div class="ring" aria-label="{pct} procent">
      <svg viewBox="0 0 64 64" width="64" height="64">
        <circle cx="32" cy="32" r={R} fill="none" stroke="var(--panel-border)" stroke-width="6" />
        <circle cx="32" cy="32" r={R} fill="none" stroke="var(--tone)" stroke-width="6" stroke-linecap="round"
          stroke-dasharray="{(C * (busy || p?.gcode_state === 'FINISH' ? pct : 0)) / 100} {C}" transform="rotate(-90 32 32)" style="transition: stroke-dasharray .8s var(--ease, ease)" />
      </svg>
      <b>{busy || p?.gcode_state === 'FINISH' ? pct : '–'}<small>%</small></b>
    </div>
    <div class="title">
      <div class="pill">{stateText}</div>
      <h3 class="ellipsis">{p?.job_name || (p?.online === false ? 'Printeren er offline' : 'Ingen aktivt print')}</h3>
      <div class="sub muted">
        {#if busy}
          <span>{fmtRemaining(p?.remaining_min)} tilbage</span>{#if eta}<span>færdig {eta}</span>{/if}
          {#if p?.total_layers}<span>lag {p.layer}/{p.total_layers}</span>{/if}
        {:else if jobLabel}<span>{jobLabel}</span>
        {:else}<span>{p?.name || p?.model || 'Printer'}</span>{/if}
      </div>
    </div>
    {#if compact}
      <div class="mini mono">
        <span title="Dyse">{temp(noz(p?.active_nozzle ?? 0)?.temp)}</span>
        <span title="Plade">{temp(p?.bed?.temp)}</span>
        <span title="Kammer">{temp(p?.chamber?.temp)}</span>
      </div>
    {/if}
  </div>

  <div class="frame glass">
    {#snippet canvas()}
      <ModelCanvas modelUrl={model?.model_url} coverUrl={model?.cover_url} taskKey={model?.task_id || ''}
        progress={shownProgress} printing={p?.gcode_state === 'RUNNING'} color={filamentColor} {still} {compact}
        layer={busy ? p?.layer || 0 : 0} totalLayers={busy ? p?.total_layers || 0 : 0} percent={busy ? pct : null} />
    {/snippet}
    {#if family === 'h2'}
      <PrinterSvg printer={p} {slots} {tone} progress={shownProgress} {still}>{@render canvas()}</PrinterSvg>
    {:else}
      <PrinterSvgGeneric printer={p} {slots} {tone} progress={shownProgress} {still} {family} amsModel={unit0?.model || ''}>{@render canvas()}</PrinterSvgGeneric>
    {/if}
    {#if live.status !== 'live' && !p}
      <div class="banner muted">{live.status === 'unauthorized' ? 'Log ind for at se printeren' : 'Forbinder til printeren …'}</div>
    {/if}
  </div>

  {#if !compact && p}
    <div class="grid">
      {#each [...p.nozzles].sort((a, b) => b.id - a.id) as n (n.id)}
        <div class="card" class:hot={n.id === p.active_nozzle && busy}>
          <span class="k">{nozName(n.id)}{#if n.id === p.active_nozzle && busy}&nbsp;· aktiv{/if}</span>
          <b class="mono">{temp(n.temp)}<small> / {n.target ? Math.round(n.target) + '°' : '–'}</small></b>
        </div>
      {/each}
      <div class="card"><span class="k">Printplade</span><b class="mono">{temp(p.bed?.temp)}<small> / {p.bed?.target ? Math.round(p.bed.target) + '°' : '–'}</small></b></div>
      <div class="card"><span class="k">Kammer</span><b class="mono">{temp(p.chamber?.temp)}</b></div>
      <div class="card"><span class="k">Hastighed</span><b>{SPEEDS[p.speed_level] || '–'}</b></div>
      <div class="card"><span class="k">Blæser</span><b class="mono">{p.fan_pct == null ? '–' : p.fan_pct + '%'}</b></div>
      <div class="card"><span class="k">Lys</span><b>{p.light == null ? '–' : p.light ? 'Tændt' : 'Slukket'}</b></div>
      {#if unit0}
        <div class="card"><span class="k">{unit0.model}</span><b class="mono">{#if unit0.humidity == null && unit0.humidity_level == null && unit0.temp == null}<small>ingen måling</small>{:else}{unit0.humidity != null ? unit0.humidity + '%' : unit0.humidity_level != null ? 'Fugt ' + humidityText(unit0) : '–'}{#if unit0.temp != null} <small>·</small> {unit0.temp}<small>°</small>{/if}{/if}</b></div>
      {/if}
    </div>
  {/if}
  {#if p}
    <!-- The slot list also shows in compact mode (Home Assistant kiosk). -->
    <ul class="slots">
      {#each slots as s (s.key)}
        <li class:act={s.active} class:empty={!s.present} style="--c: #{hex(s.color)}">
          <i class="dot"></i>
          <span class="lbl">{s.kind === 'ext' ? 'Ekstern' : s.label}</span>
          <span class="mat">{s.present ? s.material || '–' : 'tom'}</span>
          {#if s.remaining_pct != null}<span class="rem mono">{s.remaining_pct}%</span>{/if}
          {#if s.spool}<span class="nm muted ellipsis">{s.spool.name}</span>{/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .pv { --tone: var(--accent); position: relative; container-type: inline-size; display: flex; flex-direction: column; gap: 12px; width: 100%; }
  .pv.run { --tone: var(--accent); }
  .pv.idle { --tone: #6f7a90; }
  .pv.warn { --tone: var(--warn); }
  .pv.ok { --tone: var(--ok); }
  .pv.bad { --tone: var(--danger); }
  .pv.prep { --tone: var(--accent-2); }
  .pv.off { filter: saturate(.5); }

  .hud { display: flex; align-items: center; gap: 12px; padding: 0 4px; }
  .ring { position: relative; width: 64px; height: 64px; flex: none; }
  .ring b { position: absolute; inset: 0; display: grid; place-items: center; font-size: 17px; font-variant-numeric: tabular-nums; }
  .ring small { font-size: 10px; opacity: .7; margin-left: 1px; align-self: end; position: relative; top: -17px; left: 10px; display: none; }
  .title { min-width: 0; flex: 1; }
  .title h3 { font-size: 17px; margin: 2px 0; }
  .pill { display: inline-block; font-size: 11.5px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--tone);
    background: color-mix(in srgb, var(--tone) 14%, transparent); border: 1px solid color-mix(in srgb, var(--tone) 38%, transparent);
    padding: 1px 9px; border-radius: 99px; }
  .sub { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 13px; }
  .mini { display: flex; gap: 10px; font-size: 13px; color: var(--text-dim, var(--muted)); }
  .ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .frame { position: relative; padding: 6px 6px 2px; border-radius: var(--radius); overflow: hidden;
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--tone) 18%, transparent), 0 18px 50px -20px color-mix(in srgb, var(--tone) 40%, transparent); }
  .banner { position: absolute; left: 0; right: 0; bottom: 10px; text-align: center; font-size: 13px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 8px; }
  .card { background: var(--panel); border: 1px solid var(--panel-border); border-radius: var(--radius-sm, 12px); padding: 9px 12px; display: flex; flex-direction: column; gap: 2px; }
  .card.hot { border-color: color-mix(in srgb, var(--warn) 55%, transparent); box-shadow: 0 0 18px -6px var(--warn); }
  .k { font-size: 11.5px; color: var(--muted); }
  .card b { font-size: 17px; font-weight: 650; }
  .card small { font-size: 12px; color: var(--muted); font-weight: 500; }

  .slots { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
  .slots li { display: flex; align-items: center; gap: 7px; padding: 5px 11px 5px 8px; border-radius: 99px; font-size: 13px;
    background: var(--panel); border: 1px solid var(--panel-border); max-width: 100%; }
  .slots li.act { border-color: var(--c); box-shadow: 0 0 14px -3px var(--c); }
  .slots li.empty { opacity: .5; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--c); box-shadow: inset 0 0 0 1px #ffffff30; flex: none; }
  .lbl { font-weight: 650; }
  .mat { color: var(--muted); }
  .nm { max-width: 14ch; font-size: 12px; }

  /* wide containers: the HUD floats in the free top-right corner of the stage (right of the AMS, above the spool holder) */
  @container (min-width: 620px) {
    .pv:not(.compact) .hud { position: absolute; z-index: 2; top: 16px; right: 18px; width: 29%; pointer-events: none; gap: 10px; }
    .pv:not(.compact) .title h3 { font-size: 18px; }
    .pv:not(.compact) .sub { font-size: 12.5px; }
  }
  /* compact kiosk card */
  .compact .hud { padding: 0 2px; }
  .compact .ring { width: 52px; height: 52px; }
  .compact .ring svg { width: 52px; height: 52px; }
  .compact .ring b { font-size: 14px; }
  .compact .title h3 { font-size: 15px; }
  @container (max-width: 420px) { .compact .mini { display: none; } }
</style>
