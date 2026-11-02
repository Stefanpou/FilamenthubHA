<!-- What is loaded in each AMS slot / external holder, linked to inventory spools. -->
<script>
  import Spool from './ui/Spool.svelte';
  import Icon from './ui/Icon.svelte';
  import SlotPicker from './SlotPicker.svelte';
  import { app, reloadAll, toast, toastErr, go } from './stores.svelte.js';
  import { post } from './api.js';
  import { fmtG, hex, pctOf, spoolName, levelClass } from './util.js';

  let { compact = false, readonly = false } = $props();
  let picking = $state(null);
  let busyKey = $state('');

  const slots = $derived(app.status?.slots || []);
  const byId = $derived(Object.fromEntries((app.spools || []).map((s) => [s.id, s])));
  const groups = $derived.by(() => {
    const g = {};
    for (const s of slots) (g[s.kind === 'ext' ? 'ext' : `u${s.unit}`] ||= []).push(s);
    return Object.values(g).map((items) => {
      const f = items[0];
      const title = f.kind === 'ext' ? 'Ekstern holder' : `${f.unit_model || 'AMS'}${f.kind === 'ht' ? '' : ' ' + String.fromCharCode(65 + (f.unit || 0))}`;
      return { title, items };
    });
  });

  async function act(key, fn, msg) {
    busyKey = key;
    try { await fn(); if (msg) toast(msg); await reloadAll(); } catch (e) { toastErr(e); } finally { busyKey = ''; }
  }
  const unassign = (s) => act(s.key, () => post(`api/slots/${encodeURIComponent(s.key)}/unassign`, {}), 'Rullen er fjernet fra slottet');
  const quick = (s, id) => act(s.key, () => post(`api/slots/${encodeURIComponent(s.key)}/assign`, { spool_id: id }), 'Rulle valgt');
  const fromTray = (s) => act(s.key, () => post(`api/slots/${encodeURIComponent(s.key)}/create-from-tray`, {}), 'Rulle oprettet i lageret og sat i slottet');
</script>

{#if groups.length === 0}
  {#if !compact}
    <div class="glass pad muted center small">Venter på AMS-data fra printeren …</div>
  {/if}
{:else}
  <div class="groups" class:compact>
    {#each groups as g, gi}
      <section class="rise" style="--i:{gi}">
        <div class="eyebrow" style="margin: 0 4px 8px">{g.title}</div>
        <div class="slots">
          {#each g.items as s (s.key)}
            {@const sp = s.assignment?.spool}
            {@const real = sp && !sp.missing && !sp.unavailable}
            {@const col = real ? sp.color : s.color}
            {@const p = real ? pctOf({ remaining: sp.remaining, initial: sp.initial }) : (s.remain >= 0 ? s.remain / 100 : 1)}
            <div class="slot glass" class:active={s.active} class:empty={!s.present && !sp} style="--c:{hex(col)}">
              {#if s.active}<span class="live">I brug</span>{/if}
              <div class="top">
                <Spool color={col} colors={real ? sp.colors : s.colors} pct={s.present || sp ? p : 0} size={compact ? 58 : 74} />
                <div class="grow info">
                  <div class="lbl mono">{s.label}</div>
                  {#if real}
                    <b class="ellipsis nm">{spoolName(sp)}</b>
                    <span class="muted small mono">#{sp.id} · {fmtG(sp.remaining)}{#if sp.printing}<span class="use" title="Printet i gang har brugt ca. {fmtG(sp.printing)} indtil nu"> · −{fmtG(sp.printing)} i gang</span>{/if}</span>
                  {:else if sp?.missing}
                    <b class="nm" style="color:var(--warn)">Rulle #{sp.id} findes ikke</b>
                  {:else if sp?.unavailable}
                    <b class="nm">Rulle #{sp.id}</b>
                  {:else if s.present}
                    <b class="ellipsis nm">{s.material || 'Ukendt materiale'}</b>
                    <span class="muted small ellipsis">{s.sub_brand || 'Ingen rulle valgt'}</span>
                  {:else}
                    <b class="nm muted">{s.kind === 'ext' ? 'Ikke i brug' : 'Tom'}</b>
                  {/if}
                </div>
              </div>
              {#if real}<div class="bar {levelClass(p)}" style="--c:{hex(col)}"><i style="width:{Math.round(p * 100)}%"></i></div>{/if}
              {#each s.mismatch || [] as m}<div class="warn-line"><Icon name="warn" size={14} /> {m}</div>{/each}
              {#if !readonly}
                {#if !sp && s.suggestions?.length && !compact}
                  <div class="sugg">
                    {#each s.suggestions.map((id) => byId[id]).filter(Boolean).slice(0, 3) as x}
                      <button class="chip" onclick={() => quick(s, x.id)} disabled={busyKey === s.key}>
                        <i class="dot" style="background:{hex(x.color)}"></i> #{x.id} {x.name || x.material}
                      </button>
                    {/each}
                  </div>
                {/if}
                {#if s.unknown_rfid}
                  <button class="btn small primary block" onclick={() => fromTray(s)} disabled={busyKey === s.key}>
                    <Icon name="sparkle" size={16} /> Opret i lager (RFID genkendt)
                  </button>
                {/if}
                {#if s.present || s.kind === 'ext'}
                  <div class="acts">
                    <button class="btn small grow" onclick={() => (picking = s.key)}>{sp ? 'Skift' : 'Vælg rulle'}</button>
                    {#if sp}<button class="btn small danger icon-btn" aria-label="Fjern rulle" onclick={() => unassign(s)} disabled={busyKey === s.key}><Icon name="close" size={16} /></button>{/if}
                  </div>
                {/if}
              {/if}
            </div>
          {/each}
        </div>
      </section>
    {/each}
    {#each app.status?.orphans || [] as o}
      {#if !readonly}
        <div class="alert info small">{o.label} rapporteres ikke af printeren lige nu, men har rulle #{o.spool?.id ?? '?'} tildelt.
          <button class="btn small" onclick={() => act(o.slot, () => post(`api/slots/${encodeURIComponent(o.slot)}/unassign`, {}))}>Fjern</button></div>
      {/if}
    {/each}
  </div>
{/if}

{#if picking}<SlotPicker slotKey={picking} onclose={() => (picking = null)} />{/if}

<style>
  .use { color: var(--accent); }
  .groups { display: flex; flex-direction: column; gap: 18px; }
  .slots { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px; }
  .compact .slots { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }
  .slot { position: relative; padding: 14px; display: flex; flex-direction: column; gap: 10px; transition: transform .3s var(--ease), border-color .3s, box-shadow .3s; overflow: hidden; }
  .slot::before { content: ""; position: absolute; inset: -40% -20% auto auto; width: 70%; height: 90%; background: radial-gradient(circle, color-mix(in srgb, var(--c) 28%, transparent), transparent 70%); pointer-events: none; opacity: .7; }
  .slot:hover { transform: translateY(-2px); }
  .slot.active { border-color: color-mix(in srgb, var(--c) 70%, transparent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--c) 40%, transparent), 0 0 32px -6px var(--c), 0 12px 36px -12px rgba(0,0,0,.6); }
  .slot.empty { opacity: .6; }
  .live { position: absolute; top: 10px; right: 10px; font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--c); display: flex; align-items: center; gap: 5px; z-index: 1; }
  .live::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 10px var(--c); animation: pulse 1.4s infinite; }
  .top { display: flex; gap: 12px; align-items: center; position: relative; }
  .info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .lbl { font-size: 11px; color: var(--muted); letter-spacing: .08em; }
  .nm { font-size: 14.5px; }
  .warn-line { font-size: 12px; color: var(--warn); display: flex; gap: 6px; align-items: center; }
  .sugg { display: flex; flex-wrap: wrap; gap: 6px; }
  .sugg .chip { font-size: 12px; padding: 5px 10px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 6px currentColor; }
  .acts { display: flex; gap: 6px; }
  .acts .icon-btn { width: 34px; padding: 0; }
</style>
