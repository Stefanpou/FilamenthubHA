<!-- Fixed locations: shelves, drawers and dry boxes with a number of places. A spool there gets "Navn - plads". -->
<script>
  import { api } from '../api.js';
  import { app, toast, toastErr, refreshLocations, refreshSpools } from '../stores.svelte.js';
  import { hex } from '../util.js';
  import { onMount } from 'svelte';

  let rows = $state(null), busy = $state(false);
  onMount(async () => { await refreshLocations(); seed(); });
  function seed() { rows = (app.locations?.fixed || []).map((l) => ({ name: l.name, slots: l.slots, was: l.name })); }
  const placesOf = (r) => app.locations?.fixed.find((l) => l.name === r.was)?.places || {};
  const colorOf = (id) => hex((app.spools || []).find((s) => s.id === id)?.color);
  const add = () => rows.push({ name: '', slots: 4, was: null });
  const dirty = $derived(JSON.stringify((rows || []).map((r) => [r.name.trim(), Number(r.slots)]))
    !== JSON.stringify((app.locations?.fixed || []).map((l) => [l.name, l.slots])));

  async function save() {
    const list = rows.filter((r) => r.name.trim());
    busy = true;
    try {
      app.locations = await api('api/locations', { method: 'PUT', body: { locations: list.map((r) => ({ name: r.name.trim(), slots: Number(r.slots) || 1, was: r.was || undefined })) } });
      seed();
      refreshSpools();
      toast('Placeringer gemt');
    } catch (e) { toastErr(e); } finally { busy = false; }
  }
</script>

{#if rows}
<div class="stack">
  <p class="small muted">Opret faste steder som hylder, skuffer og tørrebokse, og angiv hvor mange ruller der er plads til. På en rulle vælger du så stedet og en ledig plads – eller lader den tage den første ledige.</p>
  {#each rows as r, i}
    {@const used = placesOf(r)}
    <div class="loc">
      <div class="row">
        <input class="input grow" placeholder="fx Tørreboks" maxlength="60" bind:value={r.name} aria-label="Navn">
        <label class="n"><input class="input mono" type="number" min="1" max="200" bind:value={r.slots} aria-label="Antal pladser"> <span class="small muted">pladser</span></label>
        <button class="btn small ghost" onclick={() => rows.splice(i, 1)} aria-label="Fjern {r.name}">Fjern</button>
      </div>
      {#if r.was}
        <div class="dots" aria-hidden="true">
          {#each Array.from({ length: Math.min(Number(r.slots) || 0, 200) }, (_, k) => k + 1) as n}<i class:on={used[n]} style={used[n] ? `--c:${colorOf(used[n])}` : ''}></i>{/each}
        </div>
        <span class="tiny muted">{Object.keys(used).length} af {r.slots} pladser i brug</span>
      {/if}
    </div>
  {/each}
  {#if !rows.length}<p class="small muted">Ingen faste placeringer endnu.</p>{/if}
  <div class="row wrap">
    <button class="btn" onclick={add}>Tilføj placering</button>
    <button class="btn primary" onclick={save} disabled={busy || !dirty}>Gem</button>
  </div>
  <p class="tiny muted">Omdøber du en placering, flytter rullerne med. Fjerner du en, beholder rullerne teksten som almindelig placering.</p>
</div>
{/if}

<style>
  .loc { display: flex; flex-direction: column; gap: 6px; padding: 10px 0; border-top: 1px solid var(--panel-border); }
  .loc:first-of-type { border-top: 0; }
  .n { display: inline-flex; align-items: center; gap: 6px; flex: none; }
  .n input { width: 74px; }
  .dots { display: flex; flex-wrap: wrap; gap: 4px; }
  .dots i { width: 12px; height: 12px; border-radius: 4px; border: 1px solid var(--panel-border); background: var(--bg-2); }
  .dots i.on { background: var(--c); border-color: transparent; }
</style>
