<script>
  import { toasts } from '../stores.svelte.js';
  import Icon from './Icon.svelte';
</script>
<div class="toasts" aria-live="polite">
  {#each toasts as t (t.id)}
    <div class="toast glass {t.kind}"><Icon name={t.kind === 'err' ? 'warn' : 'check'} size={18} /> <span>{t.message}</span></div>
  {/each}
</div>
<style>
  .toasts { position: fixed; z-index: 300; left: 0; right: 0; bottom: calc(var(--nav-h) + var(--safe-b) + 14px); display: flex; flex-direction: column; align-items: center; gap: 8px; pointer-events: none; padding: 0 16px; }
  .toast { display: flex; align-items: center; gap: 10px; padding: 11px 16px; border-radius: 14px; font-weight: 600; font-size: 14px; background: rgba(18, 24, 38, .94); animation: pop .45s var(--spring) both; max-width: 520px; }
  .toast.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, transparent); }
  .toast.err { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 45%, transparent); }
  .toast span { color: var(--text); }
  @keyframes pop { from { transform: translateY(14px) scale(.9); opacity: 0; } to { transform: none; opacity: 1; } }
  @media (min-width: 900px) { .toasts { bottom: 24px; left: var(--rail-w); } }
</style>
