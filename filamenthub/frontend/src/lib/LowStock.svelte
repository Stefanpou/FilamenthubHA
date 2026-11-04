<script>
  import Spool from './ui/Spool.svelte';
  import Icon from './ui/Icon.svelte';
  import { app } from './stores.svelte.js';
  import { fmtG, isLow, pctOf, spoolName } from './util.js';
  let { onopen = null, max = 6 } = $props();
  const low = $derived((app.spools || []).filter(isLow).sort((a, b) => pctOf(a) - pctOf(b)).slice(0, max));
  const inSlots = $derived(new Set((app.status?.slots || []).map((s) => s.assignment?.spool?.id).filter(Boolean)));
</script>

{#if low.length}
  <div class="list">
    {#each low as s, i (s.id)}
      <button class="row glass item rise" style="--i:{i}" onclick={() => onopen?.(s.id)} disabled={!onopen}>
        <Spool color={s.color} colors={s.colors} pct={pctOf(s)} size={44} glow={false} />
        <span class="grow txt"><b class="ellipsis">{spoolName(s)}</b>
          <span class="small muted">#{s.id}{inSlots.has(s.id) ? ' · sidder i printeren' : ''}</span></span>
        <span class="mono g" class:crit={pctOf(s) <= 0.1}>{fmtG(s.remaining)}</span>
      </button>
    {/each}
  </div>
{:else}
  <div class="glass pad small muted row"><Icon name="check" size={18} /> Alle ruller har rigeligt filament.</div>
{/if}

<style>
  .list { display: flex; flex-direction: column; gap: 8px; }
  .item { padding: 8px 14px 8px 10px; text-align: left; cursor: pointer; width: 100%; }
  .item:disabled { cursor: default; }
  .txt { display: flex; flex-direction: column; min-width: 0; }
  .g { color: var(--warn); font-weight: 500; }
  .g.crit { color: var(--danger); }
</style>
