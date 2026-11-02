<!-- Electricity from Home Assistant: the printer's smart plug and the electricity price. -->
<script>
  import { api, post } from '../api.js';
  import { toast, toastErr } from '../stores.svelte.js';
  import { onMount } from 'svelte';
  let cfg = $state(null), draft = $state({}), token = $state(''), ents = $state(null), entErr = $state(''), busy = $state(false);
  onMount(load);
  async function load() {
    try { cfg = await api('api/power/settings'); draft = { ...cfg }; if (cfg.token_set) loadEntities(); } catch (e) { toastErr(e); }
  }
  async function loadEntities() {
    entErr = '';
    try { ents = await api('api/power/entities'); } catch (e) { ents = null; entErr = e.message; }
  }
  async function save() {
    busy = true;
    try {
      const body = { url: draft.url, power_entity: draft.power_entity, energy_entity: draft.energy_entity,
        price_entity: draft.price_entity, watts: Number(draft.watts) || undefined };
      if (draft.fixed_price === '' || draft.fixed_price == null) body.clear_fixed_price = true; else body.fixed_price = Number(String(draft.fixed_price).replace(',', '.'));
      if (token) body.token = token.trim();
      const hadToken = cfg.token_set;
      cfg = await post('api/power/settings', body); draft = { ...cfg }; token = '';
      toast('Gemt');
      if (cfg.token_set && (!hadToken || !ents)) loadEntities();
    } catch (e) { toastErr(e); } finally { busy = false; }
  }
  const fmt = (r) => `${r.name} – ${r.state} ${r.unit}`;
</script>

{#if cfg}
<div class="stack">
  <p class="small muted">Hent printerens strømforbrug fra en smart-stikdåse (fx Shelly) og elprisen fra Home Assistant. Hvert print får så sit forbrug i kWh og kroner, og prisberegneren bruger elprisen i det tidsrum, printet kører.</p>
  <label class="f">Home Assistant-adresse<input class="input mono" bind:value={draft.url} placeholder="http://homeassistant.local:8123"></label>
  <label class="f">Langtidsholdbart adgangstoken {#if cfg.token_set}<span class="badge ok" style="align-self:flex-start">gemt</span>{/if}
    <input class="input mono" type="password" autocomplete="off" bind:value={token} placeholder={cfg.token_set ? 'Uændret – indsæt et nyt for at skifte' : 'Opret under din profil → Sikkerhed i Home Assistant'}></label>
  {#if entErr}<div class="alert warn">{entErr}</div>{/if}
  {#if ents}
    <label class="f">Effekt (W) fra stikdåsen
      <select class="input" bind:value={draft.power_entity}><option value="">– ingen –</option>{#each ents.power as r}<option value={r.entity_id}>{fmt(r)}</option>{/each}</select></label>
    <label class="f">Energi (kWh) fra stikdåsen – mest præcis
      <select class="input" bind:value={draft.energy_entity}><option value="">– ingen –</option>{#each ents.energy as r}<option value={r.entity_id}>{fmt(r)}</option>{/each}</select></label>
    <label class="f">Elpris (kr/kWh)
      <select class="input" bind:value={draft.price_entity}><option value="">– fast pris –</option>{#each ents.price as r}<option value={r.entity_id}>{fmt(r)}</option>{/each}</select></label>
  {/if}
  <label class="f">Fast elpris i kr/kWh (bruges, hvis der ikke er en prissensor)<input class="input mono" inputmode="decimal" bind:value={draft.fixed_price} placeholder="fx 2,50"></label>
  <label class="f">Gns. printeffekt i W (start-værdi i prisberegneren)<input class="input mono" inputmode="decimal" bind:value={draft.watts}></label>
  <button class="btn primary" onclick={save} disabled={busy}>{cfg.token_set ? 'Gem' : 'Gem og hent sensorer'}</button>
</div>
{/if}
