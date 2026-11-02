<script>
  import Spool from '../lib/ui/Spool.svelte';
  import { auth, loadAuth, toastErr } from '../lib/stores.svelte.js';
  import { post } from '../lib/api.js';
  let pw = $state(''), pw2 = $state(''), busy = $state(false), err = $state('');
  const setup = $derived(!auth.password_set);
  async function submit(e) {
    e.preventDefault();
    err = '';
    if (setup && pw !== pw2) { err = 'Adgangskoderne er ikke ens'; return; }
    busy = true;
    try {
      await post(setup ? 'api/auth/setup' : 'api/auth/login', { password: pw });
      await loadAuth();
      auth.authenticated = true;
    } catch (ex) { err = ex.message; } finally { busy = false; }
  }
</script>

<div class="wrap">
  <form class="glass card rise" onsubmit={submit}>
    <div class="logo"><Spool color="#38e8c6" pct={0.72} size={104} tilt /></div>
    <h1>FilamentHub</h1>
    <p class="muted center">{setup ? (auth.setup_allowed ? 'Vælg en adgangskode til appen (mindst 10 tegn).' : 'Første opsætning skal ske fra dit hjemmenetværk.') : 'Log ind for at fortsætte.'}</p>
    <input class="input" type="password" autocomplete={setup ? 'new-password' : 'current-password'} placeholder="Adgangskode" bind:value={pw} required>
    {#if setup}<input class="input" type="password" autocomplete="new-password" placeholder="Gentag adgangskode" bind:value={pw2} required>{/if}
    <button class="btn primary big block" type="submit" disabled={busy || (setup && !auth.setup_allowed)}>{#if busy}<span class="spinner"></span>{/if}{setup ? 'Opret adgangskode' : 'Log ind'}</button>
    {#if err}<div class="alert err">{err}</div>{/if}
  </form>
</div>

<style>
  .wrap { min-height: 100dvh; display: grid; place-items: center; padding: 20px; }
  .card { width: 100%; max-width: 380px; padding: 28px 24px; display: flex; flex-direction: column; gap: 14px; }
  .logo { display: grid; place-items: center; margin-bottom: 4px; }
  h1 { text-align: center; font-size: 28px; }
</style>
