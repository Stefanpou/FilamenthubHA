<!-- /ha: chrome-free view for an iframe in Home Assistant. -->
<script>
  import PrinterView from '../lib/printer/PrinterView.svelte';
  import LowStock from '../lib/LowStock.svelte';
  import { app, refreshSpools } from '../lib/stores.svelte.js';
  import { onMount } from 'svelte';
  // The kiosk token only opens the live printer feed and the spool list (not /api/status): PrinterView shows the
  // slots itself, and the spool list is refreshed here for the low-stock list.
  onMount(() => {
    document.documentElement.classList.add('transparent-bg');
    refreshSpools();
    const timer = setInterval(() => { if (!document.hidden) refreshSpools(); }, 60000);
    return () => { clearInterval(timer); document.documentElement.classList.remove('transparent-bg'); };
  });
  const hasLow = $derived((app.spools || []).some((s) => !s.archived && s.initial > 0 && (s.remaining / s.initial <= 0.2 || s.remaining < 100)));
</script>

<div class="kiosk">
  <PrinterView compact />
  {#if hasLow}
    <div><div class="eyebrow" style="margin:0 4px 8px">Lav beholdning</div><LowStock max={4} /></div>
  {/if}
</div>

<style>
  .kiosk { display: flex; flex-direction: column; gap: 14px; padding: 12px; max-width: 900px; margin: 0 auto; }
</style>
