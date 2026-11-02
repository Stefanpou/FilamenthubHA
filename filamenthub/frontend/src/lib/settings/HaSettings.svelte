<!-- Home Assistant. Settings are shown only if /api/ha/settings exists (added by the HA agent). -->
<script>
  import { api, post } from '../api.js';
  import { toast, toastErr } from '../stores.svelte.js';
  import { onMount } from 'svelte';
  let cfg = $state(null), exists = $state(false), draft = $state({}), busy = $state(false);
  const kiosk = `${location.origin}/ha`;
  onMount(async () => {
    try { cfg = await api('api/ha/settings'); exists = true; draft = { ...cfg }; } catch { exists = false; }
  });
  const LABEL = { enabled: 'Slået til', token: 'Kiosk-token', url: 'Home Assistant URL', webhook: 'Webhook', webhook_url: 'Webhook-URL', notify: 'Notifikationer' };
  const keys = $derived(cfg ? Object.keys(cfg).filter((k) => ['string', 'number', 'boolean'].includes(typeof cfg[k])) : []);
  async function save() {
    busy = true;
    try { const body = {}; for (const k of keys) if (draft[k] !== cfg[k]) body[k] = draft[k]; cfg = await post('api/ha/settings', body); draft = { ...cfg }; toast('Gemt'); } catch (e) { toastErr(e); } finally { busy = false; }
  }
  async function copy(t) { try { await navigator.clipboard.writeText(t); toast('Kopieret'); } catch { toastErr(new Error('Kunne ikke kopiere')); } }
</script>

<div class="stack">
  <p class="small muted">Vis printeren og dit lager i Home Assistant med et iframe-kort. Kiosk-visningen har ingen menuer og en gennemsigtig baggrund.</p>
  <div class="f">Kiosk-adresse
    <div class="row"><input class="input mono" readonly value={kiosk}><button class="btn" onclick={() => copy(kiosk)}>Kopiér</button></div></div>
  <a class="btn" href="/ha" target="_blank" rel="noopener">Åbn kiosk-visning</a>
  {#if exists}
    <hr>
    {#each keys as k}
      {#if typeof cfg[k] === 'boolean'}
        <label class="row" style="gap:10px;cursor:pointer"><input type="checkbox" style="width:20px;height:20px;accent-color:var(--accent)" bind:checked={draft[k]}> <span>{LABEL[k] || k}</span></label>
      {:else}
        <label class="f">{LABEL[k] || k}<input class="input" bind:value={draft[k]}></label>
      {/if}
    {/each}
    <button class="btn primary" onclick={save} disabled={busy}>Gem</button>
  {/if}
</div>
