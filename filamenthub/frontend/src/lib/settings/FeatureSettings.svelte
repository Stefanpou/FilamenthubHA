<!-- Optional functions (off by default): printer plug, auto power-off, plate photo, print library. -->
<script>
  import { api, post } from '../api.js';
  import { toast, toastErr } from '../stores.svelte.js';
  import { onMount } from 'svelte';
  let ft = $state(null), ents = $state(null), entErr = $state(''), imp = $state(null);
  let wait = $state(''), maxT = $state('');
  onMount(async () => {
    try { ft = await api('api/features'); wait = String(ft.off_delay_min); maxT = String(ft.off_max_temp); if (ft.plug || ft.photo || ft.live_camera) loadEntities(); } catch (e) { toastErr(e); }
    try { imp = await api('api/library/import'); if (imp.running) poll(); } catch { /* library not available */ }
  });
  async function loadEntities() {
    if (ents || entErr) return;
    try { ents = await api('api/features/entities'); } catch (e) { entErr = e.message; }
  }
  async function save(patch) {
    try { ft = await post('api/features', patch); toast('Gemt'); } catch (e) { toastErr(e); }
  }
  function flip(key, on) { save({ [key]: on }); if (on && (key === 'plug' || key === 'photo' || key === 'live_camera')) loadEntities(); }
  const saveNum = (key, v, lo) => { const n = Number(String(v).replace(',', '.')); if (Number.isFinite(n) && n >= lo) save({ [key]: n }); };
  const label = (r) => `${r.name} (${r.entity_id}) – ${r.state}`;

  let timer;
  async function startImport() {
    try { imp = await post('api/library/import?limit=50', {}); poll(); } catch (e) { toastErr(e); }
  }
  function poll() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try { imp = await api('api/library/import'); } catch { return; }
      if (imp.running) poll(); else if (imp.error) toastErr(new Error(imp.error)); else toast(`Hentede ${imp.done} print`);
    }, 2000);
  }
  onMount(() => () => clearTimeout(timer));
</script>

{#if ft}
<div class="stack">
  <p class="small muted">Funktionerne er slået fra, til du slår dem til, og bruger forbindelsen til Home Assistant under Elforbrug.</p>

  <label class="row sw"><input type="checkbox" checked={ft.plug} onchange={(e) => flip('plug', e.target.checked)}>
    <span><b>Knap til printerens stikkontakt</b><br><span class="tiny muted">Tænd og sluk printerens stik fra Overblik.</span></span></label>
  {#if ft.plug}
    {#if ents}
      <label class="f">Stik
        <select class="input" value={ft.plug_entity} onchange={(e) => save({ plug_entity: e.target.value })}>
          <option value="">– vælg –</option>{#each ents.switches as r}<option value={r.entity_id}>{label(r)}</option>{/each}</select></label>
    {:else}
      <label class="f">Stik (entity-id)<input class="input mono" value={ft.plug_entity} placeholder="switch.printer" onchange={(e) => save({ plug_entity: e.target.value.trim() })}></label>
      {#if entErr}<span class="tiny muted">{entErr}</span>{/if}
    {/if}
    <label class="row sw"><input type="checkbox" checked={ft.auto_off} onchange={(e) => flip('auto_off', e.target.checked)}>
      <span><b>Sluk stikket når printet er færdigt</b><br><span class="tiny muted">Slukker kun efter et print, der blev færdigt (ikke fejlet), når ventetiden er gået og dysen er kølet af – aldrig hvis et nyt print er startet. Kendes temperaturen ikke, slukkes 20 min efter ventetiden.</span></span></label>
    {#if ft.auto_off}
      <div class="grid2">
        <label class="f">Vent mindst (min)<input class="input mono" inputmode="numeric" bind:value={wait} onchange={() => saveNum('off_delay_min', wait, 0)}></label>
        <label class="f">Dysen under (°C)<input class="input mono" inputmode="numeric" bind:value={maxT} onchange={() => saveNum('off_max_temp', maxT, 0)}></label>
      </div>
    {/if}
  {/if}

  <label class="row sw"><input type="checkbox" checked={ft.live_camera} onchange={(e) => flip('live_camera', e.target.checked)}>
    <span><b>Live-kamera på forsiden</b><br><span class="tiny muted">Viser printerens kamera fra Home Assistant under printeren. Tryk på billedet for fuld skærm.</span></span></label>
  <label class="row sw"><input type="checkbox" checked={ft.photo} onchange={(e) => flip('photo', e.target.checked)}>
    <span><b>Billede af pladen når printet er færdigt</b><br><span class="tiny muted">Tager et billede med et kamera i Home Assistant, når et print er færdigt.</span></span></label>
  {#if ft.photo || ft.live_camera}
    {#if ents}
      <label class="f">Kamera
        <select class="input" value={ft.camera_entity} onchange={(e) => save({ camera_entity: e.target.value })}>
          <option value="">– vælg –</option>{#each ents.cameras as r}<option value={r.entity_id}>{label(r)}</option>{/each}</select></label>
    {:else}
      <label class="f">Kamera (entity-id)<input class="input mono" value={ft.camera_entity} placeholder="camera.printer" onchange={(e) => save({ camera_entity: e.target.value.trim() })}></label>
      {#if entErr}<span class="tiny muted">{entErr}</span>{/if}
    {/if}
  {/if}

  <label class="row sw"><input type="checkbox" checked={ft.library} onchange={(e) => flip('library', e.target.checked)}>
    <span><b>Printbibliotek</b><br><span class="tiny muted">Gemmer hvert færdigt print med billede, cover, projektfilen (3MF fra Bambu Cloud), filamenter og pris. Kan søges i, og filen kan hentes og åbnes i Bambu Studio for at printe igen.</span></span></label>
  {#if ft.library}
    <div class="row wrap">
      <button class="btn small" onclick={startImport} disabled={imp?.running}>Hent tidligere print fra Bambu Cloud</button>
      {#if imp?.running}<span class="small muted"><span class="spinner"></span> Henter {imp.done} af {imp.total} …</span>{/if}
    </div>
    {#if imp?.error && !imp.running}<span class="tiny muted">{imp.error}</span>{/if}
  {/if}

  <div class="eyebrow" style="margin-top:8px">Udstyr</div>
  <label class="row sw"><input type="checkbox" checked={ft.multi_ams} onchange={(e) => flip('multi_ams', e.target.checked)}>
    <span><b>Flere AMS'er</b><br><span class="tiny muted">Til når der sidder mere end én AMS på printeren: tegningen får en fane pr. AMS (den der fører filament vælges selv), og fugt, temperatur og tilsluttet dyse vises for hver. Slots, forbrug og lager følger alle AMS'er uanset indstillingen.</span></span></label>
  <label class="row sw"><input type="checkbox" checked={ft.track_switch} onchange={(e) => flip('track_switch', e.target.checked)}>
    <span><b>Filament Track Switch</b><br><span class="tiny muted">Viser hvilken bakke der sidder i hver af Track Switch'ens to indgange, og hvilken dyse den fører til.</span></span></label>
</div>
{:else}<div class="skeleton" style="height:120px"></div>{/if}

<style>
  .sw { gap: 12px; cursor: pointer; align-items: flex-start; }
  .sw input { width: 22px; height: 22px; accent-color: var(--accent); flex: none; margin-top: 2px; }
</style>
