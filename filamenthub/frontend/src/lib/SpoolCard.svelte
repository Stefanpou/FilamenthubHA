<script>
  import Spool from './ui/Spool.svelte';
  import { fmtG, fmtDate, hex, pctOf, levelClass, daysSince, category } from './util.js';
  let { s, onclick, index = 0, slotLabel = '' } = $props();
  const p = $derived(pctOf(s));
  // "31/10", with the year only when it is not this year ("3/6-25")
  const dayMonth = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${d}/${m}${y !== new Date().getFullYear() ? `-${String(y).slice(2)}` : ''}`; };
</script>

<button class="card glass rise spool-host" style="--i:{Math.min(index, 14)};--c:{hex(s.color)}" {onclick} class:arch={s.archived}>
  <div class="art"><Spool color={s.color} colors={s.colors} pct={p} size={104} tilt />
    {#if s.kg_price == null && !s.archived}<span class="noprice" title="Ingen pris på rullen – åbn den og skriv prisen ind, så printenes pris kan regnes ud">Pris mangler</span>{/if}
    {#if s.dried}{@const n = daysSince(s.dried)}<span class="dried" title="Tørret {fmtDate(s.dried)} – {n <= 0 ? 'i dag' : n === 1 ? 'i går' : `for ${n} dage siden`}">Tørret {dayMonth(s.dried)}</span>{/if}</div>
  <div class="meta">
    <span class="vendor ellipsis">{s.vendor || 'Ukendt'}</span>
    <b class="name ellipsis">{s.name || s.material}</b>
    <div class="row between">
      <span class="badge ellipsis">{category(s) || s.material}</span>
      <span class="mono g">{#if s.printing}<span class="use" title="Printet i gang har brugt ca. {fmtG(s.printing)} indtil nu – det endelige forbrug trækkes fra, når printet er færdigt">−{fmtG(s.printing)}</span>{/if}{fmtG(s.remaining)}</span>
    </div>
    <div class="bar {levelClass(p)}" style="--c:{hex(s.color)}"><i style="width:{Math.round(p * 100)}%"></i></div>
    <div class="foot small muted ellipsis">
      <span>#{s.id}</span>{#if s.location}<span> · {s.location}</span>{/if}{#if slotLabel}<span class="in"> · {slotLabel}</span>{/if}{#if s.archived}<span> · arkiveret</span>{/if}
    </div>
  </div>
</button>

<style>
  .card { display: flex; flex-direction: column; text-align: left; cursor: pointer; padding: 0; overflow: hidden; position: relative; width: 100%; color: inherit;
    transition: transform .14s var(--ease), border-color .14s, background-color .14s; }
  .card:hover { border-color: var(--panel-border-hi); background: #1d1d1f; }
  .card:active { transform: scale(.98); }
  .card.arch { opacity: .55; }
  .art { display: grid; place-items: center; padding: 20px 12px 8px; position: relative; }
  .meta { display: flex; flex-direction: column; gap: 8px; padding: 8px 16px 16px; position: relative; }
  .vendor { font-size: 12px; color: var(--muted); margin-bottom: -6px; }
  .name { font-size: 15px; font-weight: 500; }
  .g { font-weight: 500; font-size: 14px; white-space: nowrap; flex: none; }
  .badge.ellipsis { min-width: 0; }
  .in { color: var(--accent); }
  .use { color: var(--accent); font-size: 12px; margin-right: 8px; }
  .noprice { position: absolute; bottom: 4px; left: 12px; font-size: 12px; color: var(--faint); }
  .dried { position: absolute; top: 12px; right: 12px; font-size: 12px; padding: 2px 6px; border-radius: 6px; color: var(--warn); background: var(--panel-hi); }
</style>
