<!-- Resolve a pending deduction: pick the spool and the grams (or weigh the spool). -->
<script>
  import Sheet from '../lib/ui/Sheet.svelte';
  import Spool from '../lib/ui/Spool.svelte';
  import { app, toast, toastErr } from '../lib/stores.svelte.js';
  import { post } from '../lib/api.js';
  import { fmtG, fmtG1, num, pctOf, spoolName } from '../lib/util.js';
  let { d, onclose, ondone } = $props();
  let grams = $state(d.full_grams > 0 ? String(d.grams) : ''), gross = $state(''), q = $state(''), busy = $state(false);
  const unknown = !(d.full_grams > 0);
  const list = $derived.by(() => {
    const t = q.trim().toLowerCase();
    const mat = (d.material || '').toUpperCase();
    return (app.spools || []).filter((x) => !x.archived && (!t || `${x.vendor} ${x.material} ${x.name} #${x.id}`.toLowerCase().includes(t)))
      .sort((a, b) => (b.id === d.spool_id) - (a.id === d.spool_id) || (mat ? (b.material.toUpperCase() === mat) - (a.material.toUpperCase() === mat) : 0) || a.name.localeCompare(b.name));
  });
  async function pick(s) {
    const g = num(grams), gr = num(gross);
    if (!(g > 0) && gr === null) return toastErr(new Error('Indtast forbruget i gram, eller vægten på vægten'));
    const body = gr !== null && !(g > 0) ? { spool_id: s.id, gross_weight: gr } : { spool_id: s.id, grams: g };
    busy = true;
    try {
      try { await post(`api/deductions/${d.id}/resolve`, body); }
      catch (e) {
        if (!/kun .* g tilbage|vejet efter printet/.test(e.message) || !confirm(`${e.message}\n\nTræk fra alligevel?`)) throw e;
        await post(`api/deductions/${d.id}/resolve`, { ...body, force: true });
      }
      toast(body.grams ? `${fmtG1(g)} trukket fra #${s.id}` : `#${s.id} vejet – forbruget er trukket fra`);
      ondone();
    } catch (e) { toastErr(e); } finally { busy = false; }
  }
</script>
<Sheet title="Vælg rulle at trække fra" {onclose}>
  <div class="stack">
    <div class="grid2">
      <label class="f">Forbrug (gram)<input class="input" type="number" inputmode="decimal" step="0.1" min="0" bind:value={grams} placeholder="fx 42,5"></label>
      {#if unknown}<label class="f">… eller vej rullen (brutto)<input class="input" type="number" inputmode="decimal" min="0" bind:value={gross} placeholder="fx 1012"></label>{/if}
    </div>
    <input class="input" type="search" placeholder="Søg rulle …" bind:value={q}>
    <div class="stack" style="gap:6px">
      {#each list as s (s.id)}
        <button class="item" onclick={() => pick(s)} disabled={busy}>
          <Spool color={s.color} colors={s.colors} pct={pctOf(s)} size={42} glow={false} />
          <span class="grow t"><b class="ellipsis">{spoolName(s)}</b><span class="muted small">#{s.id} · {fmtG(s.remaining)}</span></span>
          {#if s.id === d.spool_id}<span class="badge accent">Forslag</span>{/if}
        </button>
      {/each}
    </div>
  </div>
</Sheet>
<style>
  .item { display: flex; align-items: center; gap: 12px; padding: 8px 10px; border-radius: 14px; background: rgba(255,255,255,.03); border: 1px solid transparent; text-align: left; cursor: pointer; transition: all .2s var(--ease); }
  .item:hover { background: rgba(255,255,255,.08); border-color: var(--panel-border-hi); }
  .t { display: flex; flex-direction: column; min-width: 0; }
</style>
