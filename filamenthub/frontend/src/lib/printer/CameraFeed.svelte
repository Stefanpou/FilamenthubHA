<!-- The printer's camera, live: frames from the Home Assistant camera (api/printer/camera), the next one fetched once
     the last is shown. Pauses while the page is hidden or scrolled out of view. Tap for full screen. -->
<script>
  import { onMount } from 'svelte';
  import Icon from '../ui/Icon.svelte';

  const GAP = 700, RETRY = 5000;
  let fig;
  let src = $state('');
  let ok = $state(false), failed = $state(false);
  let visible = true, timer = null, destroyed = false, loading = false;

  function next(delay) {
    clearTimeout(timer);
    timer = setTimeout(load, delay);
  }
  function load() {
    if (destroyed || loading) return;
    if (document.hidden || !visible) return;          // resumed by the visibility / intersection handlers
    loading = true;
    src = `api/printer/camera?t=${Date.now()}`;
  }
  function shown() { loading = false; ok = true; failed = false; next(GAP); }
  function broke() { loading = false; failed = true; next(RETRY); }
  function full() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else fig?.requestFullscreen?.().catch(() => {});
  }

  onMount(() => {
    const io = new IntersectionObserver((en) => { visible = en[0]?.isIntersecting ?? true; if (visible) next(0); });
    io.observe(fig);
    const vis = () => { if (!document.hidden) next(0); };
    document.addEventListener('visibilitychange', vis);
    load();
    return () => { destroyed = true; clearTimeout(timer); io.disconnect(); document.removeEventListener('visibilitychange', vis); };
  });
</script>

<figure class="cam glass" bind:this={fig}>
  <div class="head">
    <span class="t"><i class="dot" class:on={ok && !failed}></i> Kamera</span>
    <button class="btn small ghost icon-btn" aria-label="Fuld skærm" onclick={full}><Icon name="expand" size={16} /></button>
  </div>
  <button class="view" onclick={full} aria-label="Vis kameraet i fuld skærm">
    {#if src}<img {src} alt="Printerens kamera" onload={shown} onerror={broke} class:dim={failed} />{/if}
    {#if !ok && !failed}<span class="msg muted small">Henter billede …</span>{/if}
    {#if failed}<span class="msg small">Kameraet svarer ikke lige nu – prøver igen</span>{/if}
  </button>
</figure>

<style>
  .cam { margin: 0; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .head { display: flex; align-items: center; justify-content: space-between; }
  .t { display: inline-flex; align-items: center; gap: 8px; font-weight: 500; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
  .dot.on { background: var(--ok); }
  .view { position: relative; display: block; width: 100%; aspect-ratio: 16 / 9; padding: 0; border: 1px solid var(--panel-border);
    border-radius: 10px; background: var(--bg-2); overflow: hidden; cursor: zoom-in; }
  img { width: 100%; height: 100%; object-fit: contain; display: block; }
  img.dim { opacity: .35; }
  .msg { position: absolute; inset: 0; display: grid; place-items: center; text-align: center; padding: 16px; }
  .cam:fullscreen { background: #000; padding: 0; justify-content: center; }
  .cam:fullscreen .head { position: absolute; top: 12px; right: 12px; z-index: 1; }
  .cam:fullscreen .t { display: none; }
  .cam:fullscreen .view { border: 0; border-radius: 0; height: 100%; aspect-ratio: auto; cursor: zoom-out; background: #000; }
</style>
