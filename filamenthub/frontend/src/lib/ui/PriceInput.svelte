<script>
  // Price field that takes kroner or euro. `value` is always kroner (string, '' = none); euro is converted with the
  // ECB rate from the server. The chosen currency is remembered per device.
  import { api } from '../api.js';

  let { value = $bindable(''), placeholder = 'valgfri', onenter = null, label = 'Pris' } = $props();

  let rateP;
  const loadRate = () => (rateP ??= api('api/fx/eur').catch(() => ({ rate: 7.46, source: 'fast' })));
  const read = () => { try { return localStorage.getItem('fh.price.cur') === 'eur' ? 'eur' : 'dkk'; } catch { return 'dkk'; } };

  let cur = $state(read()), eur = $state(''), fx = $state(null);
  const num = (v) => { const n = parseFloat(String(v ?? '').replace(',', '.')); return Number.isFinite(n) ? n : null; };
  const r2 = (n) => Math.round(n * 100) / 100;

  async function ensureRate() { if (!fx) fx = await loadRate(); }
  $effect(() => { if (cur === 'eur') ensureRate().then(() => { if (eur === '' && num(value) !== null) eur = String(r2(num(value) / fx.rate)); }); });

  function setCur(c) {
    cur = c;
    try { localStorage.setItem('fh.price.cur', c); } catch {}
    if (c === 'eur') eur = fx && num(value) !== null ? String(r2(num(value) / fx.rate)) : '';
  }
  function onEur(e) {
    eur = e.target.value;
    const n = num(eur);
    value = n === null ? (eur.trim() === '' ? '' : eur) : fx ? String(r2(n * fx.rate)) : '';
  }
  const kr = (n) => n.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
</script>

<div class="wrap">
<div class="pi">
  {#if cur === 'dkk'}
    <input class="input mono" type="number" inputmode="decimal" min="0" step="0.01" {placeholder} bind:value
      aria-label="{label} i kr" onkeydown={(e) => e.key === 'Enter' && onenter?.()}>
  {:else}
    <input class="input mono" type="number" inputmode="decimal" min="0" step="0.01" {placeholder} value={eur} oninput={onEur}
      aria-label="{label} i euro" onkeydown={(e) => e.key === 'Enter' && onenter?.()} disabled={!fx}>
  {/if}
  <div class="cur" role="radiogroup" aria-label="Valuta">
    <button type="button" role="radio" aria-checked={cur === 'dkk'} class:on={cur === 'dkk'} onclick={() => setCur('dkk')}>kr</button>
    <button type="button" role="radio" aria-checked={cur === 'eur'} class:on={cur === 'eur'} onclick={() => setCur('eur')}>€</button>
  </div>
</div>
{#if cur === 'eur' && fx && num(eur) !== null}
  <span class="tiny muted">= {kr(num(eur) * fx.rate)} kr · kurs {fx.rate.toLocaleString('da-DK', { maximumFractionDigits: 4 })}{fx.source === 'ECB' ? ` (ECB ${fx.date})` : ''}</span>
{/if}
</div>

<style>
  .wrap { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
  .pi { display: flex; gap: 6px; align-items: stretch; min-width: 0; flex: 1; }
  .pi .input { flex: 1; min-width: 0; }
  .cur { display: flex; border: 1px solid var(--line, rgba(255,255,255,.12)); border-radius: 10px; overflow: hidden; flex: none; }
  .cur button { background: transparent; color: var(--muted, #9aa3b5); border: 0; padding: 0 11px; font: inherit; font-weight: 700; cursor: pointer; min-width: 36px; }
  .cur button.on { background: var(--accent, #4f8cff); color: #fff; }
</style>
