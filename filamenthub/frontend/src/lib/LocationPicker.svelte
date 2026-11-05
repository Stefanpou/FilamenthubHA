<!-- Where a spool lives: a fixed location (pick a free place, or let it take the first free one) or free text.
     value is the location text: "" | "Tørreboks" (first free place) | "Tørreboks - 2" | "Skuffe". -->
<script>
  import { app } from './stores.svelte.js';
  import { hex, parseLoc } from './util.js';

  let { value = $bindable(''), spoolId = null, places = true, label = 'Placering' } = $props();
  const fixed = $derived(app.locations?.fixed || []);
  const other = $derived(app.locations?.other || []);
  const cur = $derived(parseLoc(value, fixed));
  let free = $state(false);
  $effect(() => { if (String(value || '').trim() && !parseLoc(value, fixed)) free = true; });
  $effect(() => { if (!places && cur?.[1]) value = cur[0].name; });   // several spools: each takes a free place
  const sel = $derived(cur ? cur[0].name : free ? '__free' : '');
  const spoolOf = (id) => (app.spools || []).find((s) => s.id === id);
  const freeCount = (l) => l.slots - Object.keys(l.places).filter((k) => l.places[k] !== spoolId).length;

  function pick(e) {
    const v = e.currentTarget.value;
    if (v === '__free') { free = true; value = ''; return; }
    free = false;
    // the spool's own place in that location is kept; otherwise the first free one is taken when saving
    const mine = fixed.find((l) => l.name === v);
    const own = mine && Object.entries(mine.places).find(([, id]) => id === spoolId);
    value = own ? `${v} - ${own[0]}` : v;
  }
</script>

<div class="f">
  <label class="f">{label}
    <select class="input" value={sel} onchange={pick}>
      <option value="">Ingen</option>
      {#if fixed.length}<optgroup label="Faste placeringer">
        {#each fixed as l}<option value={l.name}>{l.name} ({freeCount(l)} af {l.slots} ledige)</option>{/each}
      </optgroup>{/if}
      <option value="__free">Anden placering …</option>
    </select>
  </label>
  {#if sel === '__free'}
    <input class="input" list="locs-other" placeholder="fx Skuffe, Kasse på loftet" bind:value aria-label="Anden placering">
    <datalist id="locs-other">{#each other as t}<option value={t}></option>{/each}</datalist>
    {#if !fixed.length}<span class="tiny muted">Opret faste placeringer med pladser under Indstillinger → Placeringer.</span>{/if}
  {:else if cur && places}
    <div class="places" role="group" aria-label="Plads i {cur[0].name}">
      {#each Array.from({ length: cur[0].slots }, (_, i) => i + 1) as n}
        {@const occ = cur[0].places[n]}
        {@const taken = occ != null && occ !== spoolId}
        {@const sp = taken ? spoolOf(occ) : null}
        <button type="button" class="pl" class:on={cur[1] === n} class:taken disabled={taken}
          title={taken ? `Optaget af #${occ}${sp ? ` ${sp.vendor} ${sp.material} ${sp.name}` : ''}` : `Plads ${n}`}
          onclick={() => (value = `${cur[0].name} - ${n}`)}>
          {#if taken}<i style="--c:{hex(sp?.color)}"></i>{/if}<span class="mono">{n}</span>
        </button>
      {/each}
    </div>
    <span class="tiny muted">{cur[1] ? `Plads ${cur[1]}` : 'Får første ledige plads'} · farvede pladser er optaget</span>
  {:else if cur}
    <span class="tiny muted">Rullerne får de første ledige pladser</span>
  {/if}
</div>

<style>
  .places { display: grid; grid-template-columns: repeat(auto-fill, minmax(44px, 1fr)); gap: 6px; }
  .pl { position: relative; height: 40px; border-radius: 10px; border: 1px solid var(--panel-border); background: var(--bg-2);
    color: inherit; cursor: pointer; display: grid; place-items: center; font-size: 13px; }
  .pl:hover:not(:disabled) { border-color: color-mix(in srgb, var(--accent) 60%, transparent); }
  .pl.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, transparent); font-weight: 600; }
  .pl.taken { cursor: not-allowed; color: var(--muted); }
  .pl i { position: absolute; inset: 5px; border-radius: 7px; background: var(--c); opacity: .55; }
  .pl span { position: relative; }
</style>
