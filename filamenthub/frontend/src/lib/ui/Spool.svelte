<!-- Stylised 3D spool: flange, filament winding in the real colour, remaining-weight ring. -->
<script>
  import { hex } from '../util.js';
  let { color = '', colors = [], pct = 1, size = 96, ring = true, glow = true, tilt = false, label = '' } = $props();
  const uid = 'sp' + Math.random().toString(36).slice(2, 8);
  const c = $derived(hex(color || colors?.[0]));
  const multi = $derived((colors || []).filter(Boolean).map((x) => hex(x)));
  const p = $derived(Math.max(0, Math.min(1, pct ?? 1)));
  const HUB = 15, R = 41;
  const fr = $derived(HUB + 1 + (R - HUB - 1) * Math.max(0.04, Math.sqrt(p)));   // area ~ weight
  const CIRC = 2 * Math.PI * 47.5;
  const ringColor = $derived(p <= 0.1 ? 'var(--danger)' : p <= 0.25 ? 'var(--warn)' : c);
  const windings = $derived(Array.from({ length: Math.max(0, Math.floor((fr - HUB) / 2.4)) }, (_, i) => HUB + 2 + i * 2.4));
  const wedges = $derived.by(() => {
    const n = multi.length;
    if (n < 2) return [];
    return multi.map((col, i) => {
      const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2, rr = 60;
      const x0 = 50 + rr * Math.cos(a0), y0 = 50 + rr * Math.sin(a0), x1 = 50 + rr * Math.cos(a1), y1 = 50 + rr * Math.sin(a1);
      return { col, d: `M50 50 L${x0} ${y0} A${rr} ${rr} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}Z` };
    });
  });
</script>

<div class="spool" class:tilt style="width:{size}px;height:{size}px;--c:{c};--glow:{glow ? 1 : 0}" role="img" aria-label={label || 'Spole'}>
  <svg viewBox="-4 -4 108 108" width={size} height={size}>
    <defs>
      <radialGradient id="{uid}f" cx="35%" cy="30%" r="85%">
        <stop offset="0" stop-color="#4a5470"/><stop offset=".55" stop-color="#232a3c"/><stop offset="1" stop-color="#121623"/>
      </radialGradient>
      <radialGradient id="{uid}w" cx="50%" cy="50%" r="50%">
        <stop offset="0.3" stop-color="#000" stop-opacity=".45"/><stop offset=".42" stop-color="#000" stop-opacity="0"/><stop offset=".92" stop-color="#fff" stop-opacity=".0"/><stop offset="1" stop-color="#000" stop-opacity=".35"/>
      </radialGradient>
      <linearGradient id="{uid}s" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#fff" stop-opacity=".5"/><stop offset=".35" stop-color="#fff" stop-opacity="0"/><stop offset=".75" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".45"/>
      </linearGradient>
      <mask id="{uid}m"><rect x="-4" y="-4" width="108" height="108" fill="#000"/><circle cx="50" cy="50" r={fr} fill="#fff"/><circle cx="50" cy="50" r={HUB} fill="#000"/></mask>
    </defs>
    <!-- flange -->
    <circle cx="50" cy="50" r="44" fill="url(#{uid}f)" stroke="rgba(255,255,255,.14)" stroke-width=".8"/>
    <circle cx="50" cy="50" r="42.4" fill="none" stroke="rgba(0,0,0,.35)" stroke-width="1"/>
    <!-- filament -->
    <g mask="url(#{uid}m)">
      {#if wedges.length}
        {#each wedges as w}<path d={w.d} fill={w.col}/>{/each}
      {:else}
        <rect x="-4" y="-4" width="108" height="108" fill={c}/>
      {/if}
      <rect x="-4" y="-4" width="108" height="108" fill="url(#{uid}w)"/>
      {#each windings as r}<circle cx="50" cy="50" r={r} fill="none" stroke="rgba(0,0,0,.16)" stroke-width=".5"/>{/each}
      <rect x="-4" y="-4" width="108" height="108" fill="url(#{uid}s)"/>
    </g>
    <!-- hub -->
    <circle cx="50" cy="50" r={HUB} fill="#0b0e17" stroke="rgba(255,255,255,.18)" stroke-width=".8"/>
    <circle cx="50" cy="50" r="7" fill="#05070c" stroke="rgba(255,255,255,.1)" stroke-width=".6"/>
    {#each [0, 120, 240] as a}
      <ellipse cx="50" cy="31" rx="2.2" ry="3.6" fill="#05070c" opacity=".7" transform="rotate({a} 50 50) translate(0 -4)"/>
    {/each}
    <!-- flange shine -->
    <path d="M14 30 A40 40 0 0 1 40 11" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="1.6" stroke-linecap="round"/>
    <!-- remaining ring -->
    {#if ring}
      <circle cx="50" cy="50" r="47.5" fill="none" stroke="rgba(255,255,255,.1)" stroke-width="2.6"/>
      <circle cx="50" cy="50" r="47.5" fill="none" stroke={ringColor} stroke-width="2.8" stroke-linecap="round"
        stroke-dasharray="{CIRC * p} {CIRC}" transform="rotate(-90 50 50)" style="transition: stroke-dasharray .9s cubic-bezier(.22,1,.36,1); filter: drop-shadow(0 0 2.5px {ringColor})"/>
    {/if}
  </svg>
</div>

<style>
  .spool { position: relative; display: inline-block; flex: none; transition: transform .5s var(--ease); }
  .spool svg { display: block; overflow: visible; filter: drop-shadow(0 calc(var(--glow) * 10px) calc(var(--glow) * 16px) color-mix(in srgb, var(--c) 38%, transparent)); }
  .tilt { transform: perspective(500px) rotateY(-14deg) rotateX(6deg); }
  :global(.spool-host:hover) .tilt, .tilt:hover { transform: perspective(500px) rotateY(0deg) rotateX(0deg) scale(1.05); }
</style>
