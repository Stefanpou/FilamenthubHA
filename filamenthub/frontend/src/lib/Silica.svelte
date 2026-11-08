<!-- When the silica gel was last dried: one date per AMS unit and per fixed location (a dry box). AMS units are
     always listed; a location shows once it has a date, others can be added from the list. -->
<script>
  import { onMount } from 'svelte';
  import { api } from './api.js';
  import { app, toast, toastErr } from './stores.svelte.js';
  import { live } from './printer/live.svelte.js';
  import { fmtDate, agoDa, daysSince, today } from './util.js';

  let dates = $state(null), adding = $state(''), busy = $state('');
  onMount(async () => { try { dates = (await api('api/silica')).dates; } catch { dates = {}; } });

  const units = $derived(live.data?.ams || []);
  const rows = $derived([
    ...units.map((u) => ({ key: `ams:${u.unit}`, name: `${u.model || 'AMS'}${units.length > 1 ? ' ' + String.fromCharCode(65 + (u.unit || 0)) : ''}` })),
    ...(app.locations?.fixed || []).filter((l) => dates?.[`loc:${l.name}`] || adding === l.name).map((l) => ({ key: `loc:${l.name}`, name: l.name })),
  ]);
  const free = $derived((app.locations?.fixed || []).filter((l) => !dates?.[`loc:${l.name}`] && adding !== l.name));

  async function set(key, dried) {
    busy = key;
    try {
      dates = (await api('api/silica', { method: 'PUT', body: { key, dried: dried || null } })).dates;
      toast(dried ? 'Silica-dato gemt' : 'Silica-dato fjernet');
      if (key === `loc:${adding}`) adding = '';
    } catch (e) { toastErr(e); } finally { busy = ''; }
  }
</script>

{#if dates && rows.length + free.length}
<section>
  <div class="row between head"><h2>Silica</h2><span class="muted small">Sidst tørret</span></div>
  <div class="glass pad stack sil">
    {#each rows as r (r.key)}
      {@const d = dates[r.key]}
      <div class="line stack">
        <div class="row between top"><b class="ellipsis">{r.name}</b>
          <span class="tiny" class:muted={!d || daysSince(d) < 30} class:warn={d && daysSince(d) >= 30}>{d ? `${fmtDate(d)} · ${agoDa(d)}` : 'Ikke registreret'}</span></div>
        <div class="row ctl">
          <input class="input date grow" type="date" max={today()} value={d || ''} aria-label="Silica tørret {r.name}"
            onchange={(e) => set(r.key, e.currentTarget.value)} disabled={busy === r.key}>
          <button class="btn small" onclick={() => set(r.key, today())} disabled={busy === r.key || d === today()}>Tørret i dag</button>
        </div>
      </div>
    {/each}
    {#if free.length}
      <select class="input add" value="" onchange={(e) => { adding = e.currentTarget.value; e.currentTarget.value = ''; }} aria-label="Tilføj placering">
        <option value="">+ Tilføj tørreboks eller placering …</option>
        {#each free as l}<option value={l.name}>{l.name}</option>{/each}
      </select>
    {/if}
  </div>
</section>
{/if}

<style>
  .head { margin-bottom: 10px; align-items: baseline; }
  h2 { font-size: 17px; font-weight: 500; }
  .sil { gap: 12px; }
  .line { gap: 8px; }
  .top { gap: 10px; align-items: baseline; }
  .top b { min-width: 0; }
  .top span { flex: none; }
  .ctl { gap: 10px; align-items: center; }
  .line + .line { padding-top: 12px; border-top: 1px solid var(--panel-border); }
  .date { min-width: 0; min-height: 36px; padding-top: 4px; padding-bottom: 4px; }
  .add { font-size: 14px; }
  .warn { color: var(--warn); }
</style>
