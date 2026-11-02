<!-- Pick an inventory spool for a slot (or for a pending deduction). -->
<script>
  import Sheet from './ui/Sheet.svelte';
  import Spool from './ui/Spool.svelte';
  import Icon from './ui/Icon.svelte';
  import { app, refreshSpools, reloadAll, toast, toastErr, go } from './stores.svelte.js';
  import { post } from './api.js';
  import { fmtG, pctOf, spoolName } from './util.js';

  let { slotKey, onclose, onpick = null, title = '' } = $props();
  let q = $state('');
  let busy = $state(false);
  const slot = $derived((app.status?.slots || []).find((s) => s.key === slotKey));
  const assignedTo = $derived(Object.fromEntries((app.status?.slots || []).filter((s) => s.assignment).map((s) => [s.assignment.spool.id, s.label])));
  const sugg = $derived(new Set(slot?.suggestions || []));
  const list = $derived.by(() => {
    const t = q.trim().toLowerCase();
    let l = (app.spools || []).filter((x) => !x.archived);
    if (t) l = l.filter((x) => `${x.vendor} ${x.material} ${x.name} #${x.id} ${x.location}`.toLowerCase().includes(t));
    const mat = (slot?.material || '').toUpperCase();
    return l.sort((a, b) =>
      (sugg.has(b.id) - sugg.has(a.id)) || (!!assignedTo[a.id] - !!assignedTo[b.id]) ||
      (mat ? (b.material.toUpperCase() === mat) - (a.material.toUpperCase() === mat) : 0) ||
      a.material.localeCompare(b.material) || a.name.localeCompare(b.name));
  });

  async function choose(s) {
    if (busy) return;
    busy = true;
    try {
      if (onpick) { await onpick(s); onclose(); return; }
      try {
        await post(`api/slots/${encodeURIComponent(slotKey)}/assign`, { spool_id: s.id });
      } catch (e) {
        if (!/RFID/.test(e.message) || !confirm(`${e.message}. Overskriv RFID-tilknytningen?`)) throw e;
        await post(`api/slots/${encodeURIComponent(slotKey)}/assign`, { spool_id: s.id, force: true });
      }
      toast(`${spoolName(s)} sat i ${slot?.label || 'slot'}`);
      await reloadAll();
      onclose();
    } catch (e) { toastErr(e); } finally { busy = false; }
  }
</script>

<Sheet title={title || `Vælg rulle til ${slot?.label || slotKey}`} {onclose}>
  <div class="search" style="margin-bottom:12px">
    <span class="ico"><Icon name="search" size={20} /></span>
    <input class="input" type="search" placeholder="Søg mærke, farve, materiale, #nr" bind:value={q}>
  </div>
  <div class="list">
    {#each list as s (s.id)}
      <button class="item" onclick={() => choose(s)} disabled={busy}>
        <Spool color={s.color} colors={s.colors} pct={pctOf(s)} size={46} glow={false} />
        <span class="grow txt">
          <b class="ellipsis">{spoolName(s)}</b>
          <span class="muted small">#{s.id} · {fmtG(s.remaining)}{assignedTo[s.id] ? ` · sidder i ${assignedTo[s.id]}` : ''}{s.location ? ` · ${s.location}` : ''}</span>
        </span>
        {#if sugg.has(s.id)}<span class="badge accent">Forslag</span>{/if}
      </button>
    {:else}
      <p class="muted center" style="padding:24px 0">Ingen ruller matcher.</p>
    {/each}
  </div>
  {#snippet footer()}
    <button class="btn grow" onclick={onclose}>Luk</button>
    {#if !onpick}<button class="btn primary grow" onclick={() => { onclose(); go('/add', { slot: slotKey }); }}><Icon name="plus" size={18} /> Ny rulle</button>{/if}
  {/snippet}
</Sheet>

<style>
  .list { display: flex; flex-direction: column; gap: 6px; }
  .item { display: flex; align-items: center; gap: 12px; padding: 8px 10px; border-radius: 14px; background: rgba(255,255,255,.03); border: 1px solid transparent; text-align: left; cursor: pointer; transition: all .2s var(--ease); }
  .item:hover { background: rgba(255,255,255,.07); border-color: var(--panel-border-hi); transform: translateX(2px); }
  .txt { display: flex; flex-direction: column; min-width: 0; }
</style>
