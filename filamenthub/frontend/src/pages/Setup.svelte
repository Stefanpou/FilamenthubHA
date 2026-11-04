<script>
  import BambuLogin from '../lib/settings/BambuLogin.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import { api, post } from '../lib/api.js';
  import { go, toast, toastErr, reloadAll } from '../lib/stores.svelte.js';
  import { onMount } from 'svelte';

  const TITLES = ['Velkommen', 'Bambu Cloud', 'Printer', 'Valgfrit', 'Færdig'];
  let step = $state(0), st = $state(null), devices = $state(null), chosen = $state(''), busy = $state(false);

  async function loadState() { try { st = await api('api/setup/state'); } catch { /* ignore */ } }
  async function loadDevices() {
    devices = null;
    try {
      devices = await api('api/bambu/devices');
      const sel = devices.find((d) => d.selected);
      chosen = sel ? sel.dev_id : devices.length === 1 ? devices[0].dev_id : '';
    } catch (e) { devices = []; if (e.status !== 400) toastErr(e); }
  }
  onMount(async () => {
    await loadState();
    if (st?.steps?.bambu && step === 0) { /* resumed wizard: stay on welcome, steps are skippable */ }
  });
  function next() { step = Math.min(4, step + 1); if (step === 2) loadDevices(); }
  const back = () => { step = Math.max(0, step - 1); };
  async function loggedIn() { await loadState(); next(); }
  async function savePrinter() {
    busy = true;
    try { await post('api/bambu/device', { dev_id: chosen }); toast('Printer valgt'); await loadState(); reloadAll(); next(); }
    catch (e) { toastErr(e); } finally { busy = false; }
  }
  async function finish(path) {
    busy = true;
    try { await post('api/setup/done', {}); await reloadAll(); go(path); } catch (e) { toastErr(e); } finally { busy = false; }
  }
  const canFinish = $derived(st?.steps?.bambu && st?.steps?.device);
</script>

<div class="page stack wiz">
  <div class="dots" aria-label="Trin">
    {#each TITLES as t, i}<span class="dot" class:on={i === step} class:done={i < step} title={t}></span>{/each}
  </div>
  <div class="eyebrow center">Trin {step + 1} af 5 · {TITLES[step]}</div>

  <section class="glass pad stack rise">
    {#if step === 0}
      <h1>Velkommen til FilamentHub</h1>
      <p>FilamentHub holder styr på dine spoler, hvor meget filament der er tilbage, og hvad hver print har kostet – automatisk, mens printeren arbejder.</p>
      <ul class="list small">
        <li><Icon name="printer" size={18} /><span>Den taler kun med <b>Bambu Cloud</b>. Du behøver ikke LAN-tilstand eller at ændre noget på printeren.</span></li>
        <li><Icon name="box" size={18} /><span>Dine data ligger i Home Assistants <b>/data</b> og er med i dine Home Assistant-backups.</span></li>
        <li><Icon name="sparkle" size={18} /><span>Opsætningen tager et par minutter: log ind, vælg printer, færdig.</span></li>
      </ul>
      <button class="btn primary big block" onclick={next}>Kom i gang</button>

    {:else if step === 1}
      <h1>Log ind på Bambu Cloud</h1>
      {#if st?.steps?.bambu}
        <div class="alert info"><Icon name="check" size={18} /><span>Du er allerede logget ind.</span></div>
      {:else}
        <p class="small muted">Det er nødvendigt: FilamentHub læser printerens status og forbrug fra Bambu Cloud, så uden login kan den ikke registrere noget.</p>
        <BambuLogin ondone={loggedIn} />
      {/if}
      <div class="row wrap"><button class="btn ghost" onclick={back}>Tilbage</button>
        <button class="btn primary grow" onclick={next} disabled={!st?.steps?.bambu}>Næste</button></div>

    {:else if step === 2}
      <h1>Vælg printer</h1>
      <p class="small muted">Det er nødvendigt: FilamentHub følger én printer ad gangen. Du kan skifte senere under Indstillinger.</p>
      {#if devices === null}<div class="skeleton" style="height:56px"></div>
      {:else}
        {#each devices as d}
          <button class="dev" class:sel={chosen === d.dev_id} onclick={() => (chosen = d.dev_id)}>
            <span class="grow" style="text-align:left"><b>{d.name}</b><br><span class="muted small">{d.model || 'Bambu Lab-printer'}</span></span>
            <span class="tiny" style="color:{d.online ? 'var(--ok)' : 'var(--muted)'}">{d.online ? 'Online' : 'Offline'}</span>
            {#if chosen === d.dev_id}<Icon name="check" size={18} />{/if}
          </button>
        {:else}<div class="alert warn"><Icon name="warn" size={18} /><span>Ingen printere fundet på kontoen. Tilføj printeren i Bambu Handy og prøv igen.</span></div>
          <button class="btn" onclick={loadDevices}>Prøv igen</button>{/each}
      {/if}
      <div class="row wrap"><button class="btn ghost" onclick={back}>Tilbage</button>
        <button class="btn primary grow" onclick={savePrinter} disabled={busy || !chosen}>Vælg printer</button></div>

    {:else if step === 3}
      <h1>Valgfrit</h1>
      <p class="small muted">Alt dette kan slås til senere under <b>Indstillinger</b>. Spring bare videre.</p>
      <ul class="list small">
        <li><Icon name="power" size={18} /><span><b>Smart-stikkontakt</b> – sluk printeren automatisk, når en print er færdig og dysen er kold.</span></li>
        <li><Icon name="camera" size={18} /><span><b>Foto af pladen</b> – tag et billede, når en print er færdig.</span></li>
        <li><Icon name="image" size={18} /><span><b>Printbibliotek</b> – gem hver print med billede og model, så du kan printe den igen.</span></li>
        <li><Icon name="home" size={18} /><span><b>Hjem-skærm</b> – en menu-fri visning af printeren og lageret til en tablet eller et dashboard.</span></li>
        <li><Icon name="bolt" size={18} /><span><b>Elforbrug</b> – pris pr. print ud fra dit el-stik og din elpris.</span></li>
      </ul>
      {#if st?.addon}
        <div class="alert info"><Icon name="check" size={18} /><span>Home Assistant er allerede forbundet automatisk{st.steps.ha ? '' : ' (forbindelsen svarer ikke lige nu – tjek igen senere)'}.</span></div>
      {:else}
        <div class="alert info"><Icon name="gear" size={18} /><span>Står appen åben på dit netværk? Sæt en adgangskode under <a href="#/settings" onclick={() => finish('/settings')}>Indstillinger</a>.</span></div>
      {/if}
      <div class="row wrap"><button class="btn ghost" onclick={back}>Tilbage</button>
        <button class="btn primary grow" onclick={next}>Næste</button></div>

    {:else}
      <h1>Så er du klar</h1>
      {#if canFinish}
        <p>FilamentHub følger nu din printer. Tilføj dine spoler, så kan forbruget trækkes fra dem automatisk.</p>
        <button class="btn primary big block" onclick={() => finish('/')} disabled={busy}>Gå til overblik</button>
        <button class="btn big block" onclick={() => finish('/add')} disabled={busy}><Icon name="plus" size={18} /> Tilføj din første rulle</button>
      {:else}
        <div class="alert warn"><Icon name="warn" size={18} /><span>Du mangler at {st?.steps?.bambu ? 'vælge en printer' : 'logge ind på Bambu Cloud'}. Det skal være på plads, før FilamentHub kan registrere forbrug.</span></div>
        <button class="btn primary block" onclick={() => (step = st?.steps?.bambu ? 2 : 1)}>{st?.steps?.bambu ? 'Vælg printer' : 'Log ind'}</button>
      {/if}
      <button class="btn ghost" onclick={back}>Tilbage</button>
    {/if}
  </section>
</div>

<style>
  .wiz { max-width: 520px; margin: 0 auto; width: 100%; box-sizing: border-box; }
  h1 { font-size: 22px; }
  .dots { display: flex; justify-content: center; gap: 8px; padding-top: 6px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: rgba(255,255,255,.14); transition: all .3s var(--ease); }
  .dot.done { background: color-mix(in srgb, var(--accent) 55%, transparent); }
  .dot.on { background: var(--accent); width: 24px; border-radius: 4px; }
  .list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 12px; color: var(--text-dim); }
  .list li { display: flex; gap: 10px; align-items: flex-start; }
  .list li :global(svg) { flex: none; color: var(--accent); margin-top: 1px; }
  .dev { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 14px; background: rgba(255,255,255,.03); border: 1px solid var(--panel-border); cursor: pointer; width: 100%; color: inherit; }
  .dev.sel { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  section, .alert { overflow-wrap: anywhere; }
</style>
