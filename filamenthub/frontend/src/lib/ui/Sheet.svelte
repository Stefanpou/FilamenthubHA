<!-- Bottom sheet on mobile, centered dialog on desktop. -->
<script>
  import Icon from './Icon.svelte';
  let { title = '', onclose, wide = false, children, footer } = $props();
  function key(e) { if (e.key === 'Escape') onclose?.(); }
  $effect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  });
</script>
<svelte:window onkeydown={key} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="scrim" onclick={(e) => e.target === e.currentTarget && onclose?.()}>
  <div class="sheet glass" class:wide role="dialog" aria-modal="true" aria-label={title} tabindex="-1">
    <div class="grab"></div>
    <header>
      <h2 class="grow ellipsis">{title}</h2>
      <button class="btn ghost icon-btn" onclick={() => onclose?.()} aria-label="Luk"><Icon name="close" /></button>
    </header>
    <div class="body">{@render children?.()}</div>
    {#if footer}<div class="foot">{@render footer()}</div>{/if}
  </div>
</div>

<style>
  .scrim { position: fixed; inset: 0; z-index: 100; background: rgba(3, 5, 10, .66); backdrop-filter: blur(6px); display: flex; align-items: flex-end; justify-content: center; animation: fade .25s both; }
  .sheet { width: 100%; max-width: 560px; max-height: 92dvh; display: flex; flex-direction: column; border-radius: 26px 26px 0 0; background: rgba(16, 21, 34, .92);
    animation: up .4s var(--ease) both; padding-bottom: var(--safe-b); border-bottom: 0; }
  .sheet.wide { max-width: 720px; }
  .grab { width: 40px; height: 4px; border-radius: 4px; background: rgba(255,255,255,.2); margin: 8px auto 0; }
  header { display: flex; align-items: center; gap: 8px; padding: 8px 12px 4px 20px; }
  h2 { font-size: 19px; }
  .body { overflow-y: auto; padding: 8px 20px 20px; flex: 1; overscroll-behavior: contain; }
  .foot { padding: 12px 20px 16px; border-top: 1px solid var(--panel-border); display: flex; gap: 10px; }
  @keyframes up { from { transform: translateY(40px); opacity: 0; } to { transform: none; opacity: 1; } }
  @media (min-width: 720px) {
    .scrim { align-items: center; }
    .sheet { border-radius: 26px; border-bottom: 1px solid var(--panel-border); max-height: 86dvh; }
    .grab { display: none; }
  }
</style>
