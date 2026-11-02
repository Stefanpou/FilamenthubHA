<script>
  import PrinterView from '../lib/printer/PrinterView.svelte';
  import SlotStrip from '../lib/SlotStrip.svelte';
  import LowStock from '../lib/LowStock.svelte';
  import SpoolSheet from '../lib/SpoolSheet.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import { app, go, toast, toastErr } from '../lib/stores.svelte.js';
  import { api, post } from '../lib/api.js';
  import { fmtG1, fmtIso, fmtTs } from '../lib/util.js';
  import { onMount } from 'svelte';

  let open = $state(null);
  let recent = $state(null);
  const st = $derived(app.status);
  onMount(async () => {
    try { recent = ((await api('api/history?limit=6')).tasks || []).slice(0, 5); } catch { recent = []; }
  });
  let pw = $state(null);
  async function loadPower() { try { pw = await api('api/power'); } catch { pw = null; } }
  onMount(() => { loadPower(); const iv = setInterval(() => { if (!document.hidden) loadPower(); }, 30000); return () => clearInterval(iv); });
  let plug = $state(null), ft = $state(null), latest = $state(null), pbusy = $state(false);
  async function loadPlug() { try { plug = await api('api/plug'); } catch { plug = null; } }
  async function togglePlug() {
    const on = !plug.on;
    pbusy = true;
    try {
      try { await post('api/plug', { on }); } catch (e) {
        if (e.status === 409 && confirm(e.message)) await post('api/plug', { on, force: true }); else if (e.status !== 409) throw e; else return;
      }
      await loadPlug();
    } catch (e) { toastErr(e); } finally { pbusy = false; }
  }
  onMount(async () => {
    loadPlug();
    const iv = setInterval(() => { if (!document.hidden) loadPlug(); }, 30000);
    try { ft = await api('api/features'); if (ft.photo || ft.library) latest = (await api('api/library/latest')) || false; } catch { latest = false; }
    return () => clearInterval(iv);
  });
  const imgOf = (e) => (e.has_photo ? `api/library/${e.task_id}/photo.jpg` : `api/library/${e.task_id}/cover.png`);
  const kr = (v) => v.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const grams = (t) => (t.deductions || []).filter((d) => d.state === 'applied').reduce((a, d) => a + d.grams, 0);
</script>

<div class="page stack" style="gap:20px">
  {#if st}
    {#if !st.bambu?.logged_in}
      <div class="glass pad row wrap rise"><div class="grow"><b>Kom i gang</b><p class="muted small">Log ind med din Bambu Lab-konto, så FilamentHub kan se printeren og dit forbrug.</p></div>
        <button class="btn primary" onclick={() => go('/settings')}>Gå til Indstillinger</button></div>
    {:else if !st.device}
      <div class="glass pad row wrap rise"><div class="grow"><b>Vælg printer</b></div><button class="btn primary" onclick={() => go('/settings')}>Vælg printer</button></div>
    {:else if st.bambu?.mqtt === 'auth_failed'}
      <div class="alert err">Bambu-login er udløbet. <a href="#/settings">Log ind igen</a> – forbrug hentes automatisk bagefter.</div>
    {/if}
    {#if st.bambu?.cloud_error}<div class="alert warn">{st.bambu.cloud_error}</div>{/if}
    {#if st.pending}<div class="alert warn"><Icon name="warn" size={18} /> {st.pending} fratrækning(er) venter på dig. <a href="#/history">Se historik</a></div>{/if}
  {/if}

  <PrinterView />

  {#if plug?.enabled}
    <div class="glass pad row plug rise">
      <span class="pi" class:off={!plug.on}><Icon name="power" size={20} /></span>
      <div class="grow"><b>Printer {plug.on ? 'tændt' : 'slukket'}</b>{#if plug.name}<span class="tiny muted" style="display:block">{plug.name}</span>{/if}</div>
      <button class="btn small" class:primary={!plug.on} onclick={togglePlug} disabled={pbusy || plug.state === 'unavailable'}>{plug.on ? 'Sluk' : 'Tænd'}</button>
    </div>
  {/if}

  {#if latest}
    <button class="glass last rise" onclick={() => go('/library')}>
      {#if latest.has_photo || latest.has_cover}<img src={imgOf(latest)} alt="">{/if}
      <span class="grow lt"><span class="eyebrow">Seneste print</span><b class="ellipsis">{latest.title}</b>
        <span class="tiny muted">{fmtTs(latest.end)} · {fmtG1(latest.weight)}</span></span>
      <Icon name="chev" size={18} />
    </button>
  {/if}

  {#if pw?.configured}
    <div class="glass pad power rise">
      <span class="pi"><Icon name="bolt" size={20} /></span>
      {#if pw.ok}
        <div><span class="tiny muted">Strøm nu</span><b class="mono">{pw.power_w != null ? `${Math.round(pw.power_w)} W` : '–'}</b></div>
        {#if pw.price != null}<div><span class="tiny muted">Elpris</span><b class="mono">{kr(pw.price)} kr/kWh</b></div>{/if}
        {#if pw.current}<div><span class="tiny muted">Dette print</span><b class="mono">{pw.current.kwh.toLocaleString('da-DK', { maximumFractionDigits: 2 })} kWh{#if pw.current.cost != null} · {kr(pw.current.cost)} kr{/if}</b></div>{/if}
      {:else}
        <span class="small muted">Elforbrug: {pw.error || 'venter på første måling'}</span>
      {/if}
    </div>
  {/if}

  <section>
    <div class="row between head"><h2>I printeren</h2>
      <span class="muted small">Rulle ↔ lager</span></div>
    <SlotStrip />
  </section>

  <div class="two">
    <section>
      <div class="row between head"><h2>Lav beholdning</h2><a class="small" href="#/inventory?low=1">Se alle</a></div>
      <LowStock onopen={(id) => (open = id)} />
    </section>
    <section>
      <div class="row between head"><h2>Seneste forbrug</h2><a class="small" href="#/history">Historik</a></div>
      {#if recent === null}
        <div class="skeleton" style="height:120px"></div>
      {:else if !recent.length}
        <div class="glass pad small muted">Ingen prints registreret endnu. Forbrug trækkes automatisk fra, når et print er færdigt.</div>
      {:else}
        <div class="glass list">
          {#each recent as t}
            <div class="row line"><div class="grow"><b class="ellipsis" style="display:block">{t.title}</b><span class="tiny muted">{fmtIso(t.end_time || t.start_time)}</span></div><b class="mono">{fmtG1(grams(t))}</b></div>
          {/each}
        </div>
      {/if}
    </section>
  </div>
</div>

{#if open}<SpoolSheet id={open} onclose={() => (open = null)} />{/if}

<style>
  .head { margin-bottom: 10px; align-items: baseline; }
  .power { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 26px; padding: 14px 18px; }
  .power > div { display: flex; flex-direction: column; }
  .power b { font-size: 16px; }
  .pi { color: #ffd166; display: grid; }
  .pi.off { color: var(--muted); }
  .plug { padding: 12px 16px; }
  .last { display: flex; align-items: center; gap: 12px; padding: 10px 14px 10px 10px; text-align: left; cursor: pointer; color: inherit; font: inherit; width: 100%; }
  .last img { width: 64px; height: 64px; border-radius: 12px; object-fit: cover; flex: none; }
  .lt { display: flex; flex-direction: column; min-width: 0; }
  h2 { font-size: 18px; }
  .two { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }
  @media (min-width: 900px) { .two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .list { padding: 4px 16px; }
  .line { padding: 11px 0; border-bottom: 1px solid var(--panel-border); }
  .line:last-child { border: 0; }
</style>
