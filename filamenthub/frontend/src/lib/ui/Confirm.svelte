<script>
  import Sheet from './Sheet.svelte';
  let { title, message = '', confirmLabel = 'Ja', danger = false, onconfirm, oncancel } = $props();
</script>
<Sheet {title} onclose={oncancel}>
  <p class="dim" style="margin: 4px 0 8px; white-space: pre-line">{message}</p>
  {#snippet footer()}
    <button class="btn grow" onclick={oncancel}>Annuller</button>
    <button class="btn grow {danger ? 'danger' : 'primary'}" onclick={onconfirm}>{confirmLabel}</button>
  {/snippet}
</Sheet>
