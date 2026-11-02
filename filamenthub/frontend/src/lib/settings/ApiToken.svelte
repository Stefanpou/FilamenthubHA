<!-- Token for Spoolman-compatible clients (/api/v1). Endpoint is added by the backend agent. -->
<script>
  import Icon from '../ui/Icon.svelte';
  import { api, post } from '../api.js';
  import { toast, toastErr } from '../stores.svelte.js';
  import { onMount } from 'svelte';
  let info = $state(null), missing = $state(false), show = $state(false), busy = $state(false);
  const base = `${location.origin}/api/v1`;
  const token = $derived(info?.token || info?.api_token || '');
  onMount(async () => {
    try { info = await api('api/settings/api-token'); } catch (e) { if (e.status === 404 || e.status === 405) missing = true; else toastErr(e); }
  });
  async function copy(text, what) {
    try { await navigator.clipboard.writeText(text); toast(`${what} kopieret`); } catch { toastErr(new Error('Kunne ikke kopiere – markér og kopiér selv')); }
  }
  async function regen() {
    if (token && !confirm('Opret nyt token? Klienter med det gamle token mister adgangen.')) return;
    busy = true;
    try { info = await post('api/settings/api-token', {}); show = true; toast('Nyt token oprettet'); } catch (e) { toastErr(e); } finally { busy = false; }
  }
</script>

{#if missing}
  <p class="small muted">API-token er ikke tilgængelig endnu på denne server.</p>
{:else}
  <div class="stack">
    <p class="small muted">Brug disse oplysninger i apps der taler med Spoolman (fx OrcaSlicer, SpoolEase, Home Assistant).</p>
    <div class="f">Server-adresse (Spoolman URL)
      <div class="row"><input class="input mono" readonly value={base}><button class="btn icon-btn" onclick={() => copy(base, 'Adresse')} aria-label="Kopiér"><Icon name="copy" size={18} /></button></div></div>
    <div class="f">API-token
      <div class="row">
        <input class="input mono" readonly type={show ? 'text' : 'password'} value={token || (info ? 'Intet token oprettet' : 'Henter …')}>
        <button class="btn icon-btn" onclick={() => (show = !show)} aria-label="Vis"><Icon name="search" size={18} /></button>
        <button class="btn icon-btn" onclick={() => copy(token, 'Token')} disabled={!token} aria-label="Kopiér"><Icon name="copy" size={18} /></button>
      </div></div>
    <button class="btn small" onclick={regen} disabled={busy}><Icon name="refresh" size={16} /> {token ? 'Opret nyt token' : 'Opret token'}</button>
  </div>
{/if}
