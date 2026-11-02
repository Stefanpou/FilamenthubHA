<script>
  import BambuLogin from '../lib/settings/BambuLogin.svelte';
  import FmSettings from '../lib/settings/FmSettings.svelte';
  import ApiToken from '../lib/settings/ApiToken.svelte';
  import HaSettings from '../lib/settings/HaSettings.svelte';
  import PowerSettings from '../lib/settings/PowerSettings.svelte';
  import FeatureSettings from '../lib/settings/FeatureSettings.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import { app, auth, toast, toastErr, reloadAll } from '../lib/stores.svelte.js';
  import { api, post } from '../lib/api.js';
  import { fmtTs } from '../lib/util.js';
  import { onMount } from 'svelte';

  const b = $derived(app.status?.bambu);
  const exp = $derived(b?.expires_at ? new Date(b.expires_at * 1000).toLocaleDateString('da-DK') : 'ukendt');
  const MQTT = { connected: 'Forbundet', connecting: 'Forbinder …', reconnecting: 'Genforbinder …', auth_failed: 'Login udløbet', not_logged_in: 'Ikke logget ind', no_device: 'Ingen printer valgt' };
  let showLogin = $state(false), devices = $state(null), cur = $state(''), neu = $state(''), busy = $state(false), onPw = $state('');

  async function loadDevices() {
    try { devices = await api('api/bambu/devices'); } catch (e) { devices = []; if (e.status !== 400) toastErr(e); }
  }
  $effect(() => { if (b?.logged_in && devices === null) loadDevices(); });
  async function pick(d) {
    try { await post('api/bambu/device', { dev_id: d.dev_id }); toast('Printer valgt'); await loadDevices(); reloadAll(); } catch (e) { toastErr(e); }
  }
  async function logoutBambu() {
    if (!confirm('Log ud af Bambu? Forbrug registreres ikke, før du logger ind igen.')) return;
    try { await post('api/bambu/logout', {}); devices = null; await reloadAll(); } catch (e) { toastErr(e); }
  }
  async function changePw() {
    busy = true;
    try { await post('api/auth/password', { current: cur, new: neu }); toast('Adgangskode ændret'); cur = ''; neu = ''; } catch (e) { toastErr(e); } finally { busy = false; }
  }
  async function setLoginRequired(required) {
    if (!required && !confirm('Slå login fra? Alle der kan nå adressen kan så bruge appen – gør det kun bag fx Cloudflare Access.')) return;
    busy = true;
    try {
      const r = await post('api/auth/required', { required, password: onPw });
      auth.login_required = r.login_required; auth.authenticated = true; onPw = '';
      toast(required ? 'Login slået til' : 'Login slået fra');
    } catch (e) { toastErr(e); } finally { busy = false; }
  }
  async function logout() {
    try { await post('api/auth/logout', {}); } catch { /* ignore */ }
    auth.authenticated = false;
  }
</script>

<div class="page stack" style="gap:16px; max-width: 760px; margin: 0 auto">
  <h1>Indstillinger</h1>

  <section class="glass pad stack rise">
    <div class="row between"><h2><Icon name="printer" size={20} /> Bambu Lab-konto</h2>
      {#if b?.logged_in}<span class="badge ok">Logget ind</span>{/if}</div>
    {#if b?.logged_in}
      <dl class="kv">
        <dt>Konto</dt><dd>{b.email || '(token)'}</dd>
        <dt>Login udløber</dt><dd>{exp}</dd>
        <dt>Printer-forbindelse</dt><dd>{MQTT[b.mqtt] || b.mqtt}{b.mqtt_detail ? ` (${b.mqtt_detail})` : ''}</dd>
        <dt>Sidste data</dt><dd>{fmtTs(b.last_message)}</dd>
        <dt>Printhistorik hentet</dt><dd>{fmtTs(b.last_task_poll_ok)}</dd>
      </dl>
      {#if b.cloud_error}<div class="alert warn small">{b.cloud_error}</div>{/if}
      <div class="row wrap"><button class="btn small" onclick={() => (showLogin = !showLogin)}>Log ind igen</button><button class="btn small danger" onclick={logoutBambu}>Log ud</button></div>
      {#if showLogin}<hr><BambuLogin ondone={() => { showLogin = false; devices = null; }} />{/if}
      <hr>
      <div class="eyebrow">Printer</div>
      {#if devices === null}<div class="skeleton" style="height:50px"></div>
      {:else}
        {#each devices as d}
          <button class="dev" class:sel={d.selected} onclick={() => !d.selected && pick(d)}>
            <span class="grow" style="text-align:left"><b>{d.name}</b> <span class="muted small">{d.model || ''}</span><br><span class="tiny" style="color:{d.online ? 'var(--ok)' : 'var(--muted)'}">{d.online ? 'Online' : 'Offline'}</span></span>
            {#if d.selected}<span class="badge accent">Valgt</span>{:else}<span class="btn small">Vælg</span>{/if}
          </button>
        {:else}<p class="small muted">Ingen printere fundet på kontoen.</p>{/each}
      {/if}
    {:else}
      <BambuLogin />
    {/if}
  </section>

  <section class="glass pad stack rise" style="--i:1"><h2><Icon name="sparkle" size={20} /> Bambu Filament Manager</h2><FmSettings /></section>
  <section class="glass pad stack rise" style="--i:2"><h2><Icon name="link" size={20} /> Spoolman-kompatibel API</h2><ApiToken /></section>
  <section class="glass pad stack rise" style="--i:3"><h2><Icon name="home" size={20} /> Home Assistant</h2><HaSettings /></section>
  <section class="glass pad stack rise" style="--i:3"><h2><Icon name="bolt" size={20} /> Elforbrug</h2><PowerSettings /></section>
  <section class="glass pad stack rise" style="--i:3"><h2><Icon name="sliders" size={20} /> Funktioner</h2><FeatureSettings /></section>

  <section class="glass pad stack rise" style="--i:4">
    <div class="row between"><h2><Icon name="gear" size={20} /> Login til appen</h2>
      <span class="badge {auth.login_required ? 'ok' : 'warn'}">{auth.login_required ? 'Slået til' : 'Slået fra'}</span></div>
    {#if auth.login_required}
      <p class="muted tiny">Står appen bag en beskyttelse som Cloudflare Access, kan du slå appens eget login fra.</p>
      <div class="row"><button class="btn small" onclick={() => setLoginRequired(false)} disabled={busy}>Slå login fra</button></div>
    {:else}
      <p class="muted tiny">Login er slået fra: alle der kan nå adressen kan bruge appen. Indtast adgangskoden for at slå det til igen.</p>
      <label class="f">Adgangskode<input class="input" type="password" autocomplete="current-password" bind:value={onPw}></label>
      <div class="row"><button class="btn primary" onclick={() => setLoginRequired(true)} disabled={busy || !onPw}>Slå login til</button></div>
    {/if}
  </section>

  <section class="glass pad stack rise" style="--i:5">
    <h2><Icon name="gear" size={20} /> Adgangskode til appen</h2>
    <label class="f">Nuværende<input class="input" type="password" autocomplete="current-password" bind:value={cur}></label>
    <label class="f">Ny (mindst 10 tegn)<input class="input" type="password" autocomplete="new-password" bind:value={neu}></label>
    <div class="row"><button class="btn primary" onclick={changePw} disabled={busy || !cur || neu.length < 10}>Gem</button>
      {#if auth.login_required}<button class="btn danger" onclick={logout}><Icon name="logout" size={18} /> Log ud</button>{/if}</div>
  </section>
  <p class="center"><a class="small" href="#/setup">Kør opsætningsguiden igen</a></p>
  <p class="tiny muted center">FilamentHub · Tip: vælg “Føj til hjemmeskærm” i browseren for at bruge den som app.</p>
</div>

<style>
  h1 { font-size: 26px; }
  h2 { font-size: 17px; display: flex; align-items: center; gap: 8px; }
  .dev { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 14px; background: rgba(255,255,255,.03); border: 1px solid var(--panel-border); cursor: pointer; width: 100%; }
  .dev.sel { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
</style>
