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
  .card { display: flex; flex-direction: column; text-align: left; cursor: pointer; padding: 0; overflow: hidden; position: relative; width: 100%;
    transition: transform .35s var(--ease), border-color .3s, box-shadow .3s; }
  .card::before { content: ""; position: absolute; inset: 0 0 45% 0; background: radial-gradient(ellipse at 50% 0%, color-mix(in srgb, var(--c) 30%, transparent), transparent 70%); pointer-events: none; }
  .card:hover { transform: translateY(-4px); border-color: color-mix(in srgb, var(--c) 55%, transparent); box-shadow: 0 18px 44px -14px color-mix(in srgb, var(--c) 45%, transparent), 0 1px 0 rgba(255,255,255,.06) inset; }
  .card:active { transform: scale(.98); }
  .card.arch { opacity: .55; }
  .art { display: grid; place-items: center; padding: 18px 12px 6px; position: relative; }
  .meta { display: flex; flex-direction: column; gap: 6px; padding: 8px 14px 14px; position: relative; }
  .vendor { font-size: 11px; color: var(--muted); font-weight: 600; letter-spacing: .04em; }
  .name { font-size: 15px; }
  .g { font-weight: 700; font-size: 14px; white-space: nowrap; flex: none; }
  .badge.ellipsis { min-width: 0; }
  .in { color: var(--accent); }
  .use { color: var(--accent); font-weight: 600; font-size: 11.5px; margin-right: 6px; }
  .noprice { position: absolute; bottom: 4px; left: 10px; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 99px;
    color: var(--muted); background: color-mix(in srgb, var(--muted) 12%, transparent); border: 1px dashed color-mix(in srgb, var(--muted) 45%, transparent); }
  .dried { position: absolute; top: 10px; right: 10px; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 99px;
    color: #ffc27a; background: color-mix(in srgb, #ff9f43 16%, transparent); border: 1px solid color-mix(in srgb, #ff9f43 40%, transparent); }
</style>
