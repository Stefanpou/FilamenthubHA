<!-- AMS drying: running now, the latest sessions with their electricity, and earlier ones read from Home Assistant. -->
<script>
  import { onMount } from 'svelte';
  import { api, post } from './api.js';
  import { toast, toastErr } from './stores.svelte.js';
  import { nf2 } from './util.js';

  let d = $state(null), busy = $state(false), all = $state(false);
  async function load() { try { d = await api('api/drying?limit=30'); } catch { d = d || null; } }
  onMount(() => { load(); const iv = setInterval(() => { if (!document.hidden) load(); }, 60000); return () => clearInterval(iv); });

  const hm = (h) => { const m = Math.round((h || 0) * 60); return m >= 60 ? `${Math.floor(m / 60)} t${m % 60 ? ` ${m % 60} min` : ''}` : `${m} min`; };
  const day = (ts) => new Date(ts * 1000).toLocaleString('da-DK', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const kr = (v) => (v == null ? '' : `${nf2.format(v)} kr`);
  const shown = $derived((d?.sessions || []).slice(0, all ? 30 : 4));
  async function importHa() {
    busy = true;
    try { const r = await post('api/drying/import', {}); toast(r.imported ? `${r.imported} tørring${r.imported === 1 ? '' : 'er'} hentet fra Home Assistant` : 'Ingen nye tørringer i Home Assistant'); await load(); }
    catch (e) { toastErr(e); } finally { busy = false; }
  }
</script>

{#if d}
<section>
  <div class="row between head"><h2>AMS-tørring</h2>
    {#if d.month.count}<span class="muted small">{d.month.cost != null ? kr(d.month.cost) : `${nf2.format(d.month.kwh)} kWh`} på 30 dage</span>{/if}</div>
  <div class="glass pad stack dry">
    {#each d.now as n}
      <div class="row now">
        <span class="dot"></span>
        <div class="grow"><b>Tørrer nu{n.temp ? ` ved ${Math.round(n.temp)} °C` : ''}</b>
          <span class="tiny muted" style="display:block">{hm(n.left_min / 60)} tilbage{n.filament ? ` · ${n.filament}` : ''}</span></div>
        {#if d.sessions[0] && !d.sessions[0].end}<b class="mono">{nf2.format(d.sessions[0].kwh)} kWh{d.sessions[0].cost != null ? ` · ${kr(d.sessions[0].cost)}` : ''}</b>{/if}
      </div>
    {/each}
    {#each shown.filter((s) => s.end) as s (s.id)}
      <div class="row line">
        <div class="grow"><span class="small">{day(s.start)}</span>
          <span class="tiny muted" style="display:block">{hm(s.hours)}{s.temp ? ` · ${Math.round(s.temp)} °C` : ''}{s.filament ? ` · ${s.filament}` : ''}{s.with_print ? ' · inkl. print' : ''}</span></div>
        <div class="val"><b class="mono">{kr(s.cost) || `${nf2.format(s.kwh)} kWh`}</b>{#if s.cost != null}<span class="tiny muted mono">{nf2.format(s.kwh)} kWh</span>{/if}</div>
      </div>
    {:else}
      {#if !d.now.length}<p class="small muted">Når AMS'en tørrer, ses det her med strøm og pris – målt på printerens stik.</p>{/if}
    {/each}
    <div class="row wrap foot">
      {#if (d.sessions || []).filter((s) => s.end).length > 4}<button class="linkbtn small" onclick={() => (all = !all)}>{all ? 'Vis færre' : 'Vis alle'}</button>{/if}
      <button class="linkbtn small" onclick={importHa} disabled={busy}>{busy ? 'Henter …' : 'Hent tidligere tørring fra Home Assistant'}</button>
    </div>
    {#if d.sessions.some((s) => s.with_print)}<p class="tiny muted">»inkl. print« – der kørte et print samtidig, så strømmen dækker også printet.</p>{/if}
  </div>
</section>
{/if}

<style>
  .head { margin-bottom: 10px; align-items: baseline; }
  h2 { font-size: 17px; font-weight: 500; }
  .dry { gap: 12px; }
  .line, .now { gap: 12px; align-items: center; }
  .line + .line, .now + .line { padding-top: 12px; border-top: 1px solid var(--panel-border); }
  .val { display: flex; flex-direction: column; align-items: flex-end; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--warn); flex: none; box-shadow: 0 0 0 4px color-mix(in srgb, var(--warn) 20%, transparent); }
  .foot { gap: 16px; }
</style>
