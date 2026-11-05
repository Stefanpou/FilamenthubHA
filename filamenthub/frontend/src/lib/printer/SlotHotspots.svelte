<!-- Tags over the drawn AMS bays and the external spool: what filament sits where. With `onslot` each tag is a button
     (pick or change the spool); without it (kiosk) the tags are only shown. Positions in the drawing's viewBox units. -->
<script>
  import { hex } from './format.js';

  let { view, bays = [], ext = null, extAt = null, bayY = 70, onslot = null } = $props();
  const [VX, VY, VW, VH] = $derived(view);
  const BAYS = [188, 279, 371, 462];
  const at = (x, y, w) => `left:${((x - w / 2 - VX) / VW) * 100}%; top:${((y - VY) / VH) * 100}%; width:${(w / VW) * 100}%`;
  const tags = $derived([
    ...BAYS.map((x, i) => ({ s: bays[i], x, y: bayY, w: 86 })),
    ...(ext && extAt ? [{ s: ext, x: extAt.x, y: extAt.y, w: 124 }] : []),
  ].filter((t) => t.s));
  const title = (s) => (s.present ? [s.label, s.spool ? `${s.spool.vendor} ${s.material} ${s.spool.name}` : s.material, s.remaining_pct != null ? `${s.remaining_pct}%` : ''].filter(Boolean).join(' · ') : `${s.label} – tom`);
</script>

{#each tags as { s, x, y, w } (s.key)}
  <svelte:element this={onslot ? 'button' : 'div'} class="tag" class:act={s.active} class:empty={!s.present} class:btn-like={!!onslot}
    style="{at(x, y, w)}; --c: #{hex(s.color)}" title={title(s)} aria-label={onslot ? `${title(s)} – vælg rulle` : undefined}
    onclick={onslot ? () => onslot(s.key) : undefined} role={onslot ? undefined : 'note'}>
    {#if s.present}
      <span class="m"><i></i>{s.material || '–'}</span>
      <span class="n">{#if s.remaining_pct != null}<b>{s.remaining_pct}%</b> {/if}{s.spool?.name || (onslot ? 'Vælg rulle' : 'Ukendt rulle')}</span>
    {:else}
      <span class="m">tom</span>
    {/if}
  </svelte:element>
{/each}

<style>
  .tag { position: absolute; z-index: 3; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; gap: 1px;
    padding: 3px 4px; border-radius: 7px; border: 1px solid color-mix(in srgb, var(--c) 55%, #ffffff22);
    background: color-mix(in srgb, #0b0d12 82%, var(--c)); color: #eef1f6; font: inherit; line-height: 1.15;
    font-size: clamp(8.5px, 2.3cqw, 12.5px); text-align: center; backdrop-filter: blur(4px); }
  .btn-like { cursor: pointer; transition: transform .12s, border-color .12s; }
  .btn-like:hover { border-color: var(--c); transform: translateY(-1px); }
  .btn-like:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .tag.act { box-shadow: 0 0 0 1px var(--c), 0 0 12px color-mix(in srgb, var(--c) 60%, transparent); }
  .tag.empty { opacity: .6; border-style: dashed; border-color: #ffffff30; background: #0b0d12cc; }
  .m { display: inline-flex; align-items: center; gap: 3px; font-weight: 600; white-space: nowrap; }
  .m i { width: .8em; height: .8em; border-radius: 50%; background: var(--c); box-shadow: inset 0 0 0 1px #ffffff40; flex: none; }
  .n b { font-weight: 600; }
  .n { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .8; font-size: .9em; }
</style>
