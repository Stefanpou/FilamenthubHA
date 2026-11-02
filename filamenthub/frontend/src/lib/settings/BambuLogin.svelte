<script>
  import Icon from '../ui/Icon.svelte';
  import { post } from '../api.js';
  import { toast, toastErr, reloadAll } from '../stores.svelte.js';
  let { ondone } = $props();
  let step = $state(1), mode = $state('code'), msg = $state(''), busy = $state(false);
  let email = $state(''), pw = $state(''), code = $state(''), token = $state('');

  async function run(fn) {
    busy = true;
    try { handle(await fn()); } catch (e) { toastErr(e); } finally { busy = false; }
  }
  function handle(r) {
    if (r.next === 'done') { toast('Logget ind på Bambu'); reloadAll(); ondone?.(); return; }
    mode = r.next; msg = r.message || ''; step = 2; code = '';
  }
  const login = () => run(() => post('api/bambu/login', { email: email.trim(), password: pw }));
  const codeOnly = () => { if (!email.trim()) return toastErr(new Error('Indtast e-mail')); run(() => post('api/bambu/send-code', { email: email.trim() })); };
  const verify = () => run(() => post(mode === 'tfa' ? 'api/bambu/tfa' : 'api/bambu/code', { code: code.trim(), email: email.trim() || null }));
  const resend = async () => { try { await post('api/bambu/send-code', { email: email.trim() }); toast('Ny kode sendt'); } catch (e) { toastErr(e); } };
  const useToken = () => run(() => post('api/bambu/token', { token: token.trim() }));
</script>

{#if step === 1}
  <div class="stack">
    <p class="small muted">Log ind med samme konto som i Bambu Handy. Adgangskoden sendes kun til Bambu og gemmes ikke.</p>
    <label class="f">E-mail<input class="input" type="email" autocomplete="username" bind:value={email}></label>
    <label class="f">Adgangskode<input class="input" type="password" autocomplete="current-password" bind:value={pw} onkeydown={(e) => e.key === 'Enter' && login()}></label>
    <div class="row wrap"><button class="btn primary grow" onclick={login} disabled={busy}>Log ind</button>
      <button class="btn grow" onclick={codeOnly} disabled={busy}>Log ind med e-mailkode</button></div>
    <p class="tiny muted">Bruger du Google- eller Apple-login? Vælg “Log ind med e-mailkode” (kræver en e-mail på kontoen), eller brug token nedenfor.</p>
    <details>
      <summary class="small">Avanceret: brug token</summary>
      <ol class="small muted"><li>Log ind på <b>bambulab.com</b> i en browser på en computer.</li><li>Åbn udviklerværktøjer (F12) → Application → Cookies → bambulab.com.</li><li>Kopiér værdien af cookien <b>token</b> og indsæt her.</li></ol>
      <textarea class="input" rows="3" placeholder="token" bind:value={token}></textarea>
      <button class="btn" style="margin-top:8px" onclick={useToken} disabled={busy}>Brug token</button>
    </details>
  </div>
{:else}
  <div class="stack">
    <div class="alert info"><Icon name="sparkle" size={18} /> <span>{msg || (mode === 'tfa' ? 'Indtast koden fra din authenticator-app' : 'Indtast koden fra e-mailen')}</span></div>
    <label class="f">{mode === 'tfa' ? 'Kode fra authenticator-app' : 'Kode fra e-mail'}
      <input class="input mono" type="text" inputmode="numeric" autocomplete="one-time-code" bind:value={code} onkeydown={(e) => e.key === 'Enter' && verify()}></label>
    <div class="row wrap"><button class="btn primary grow" onclick={verify} disabled={busy}>Bekræft</button>
      {#if mode !== 'tfa'}<button class="btn" onclick={resend}>Send ny kode</button>{/if}
      <button class="btn ghost" onclick={() => (step = 1)}>Tilbage</button></div>
  </div>
{/if}
