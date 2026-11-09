<!-- The printer's camera. Off until it is switched on (each visit starts off). On: Home Assistant's video stream (HLS,
     passed through api/printer/camera/hls), smooth but a few seconds behind; if that cannot start, still frames
     (api/printer/camera), each one loaded off-screen before it replaces the last so the picture never blanks.
     Stops while the page is hidden or the card is scrolled away. Tap for full screen. -->
<script>
  import { onMount, tick } from 'svelte';
  import Icon from '../ui/Icon.svelte';

  const GAP = 150, RETRY = 5000;
  let fig, video = $state(null);
  let on = $state(false), mode = $state(''), src = $state(''), ok = $state(false), failed = $state(false);
  let visible = true, timer = null, hls = null, alive = false, run = 0;

  function stopAll() {
    run += 1;
    clearTimeout(timer);
    if (hls) { hls.destroy(); hls = null; }
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    mode = ''; ok = false; failed = false;
  }
  function active() { return alive && on && visible && !document.hidden; }

  async function start() {
    stopAll();
    if (!active()) return;
    const my = run;
    try {
      const r = await fetch('api/printer/camera/stream', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const { url } = await r.json();
      if (my !== run) return;
      mode = 'video';
      await tick();                          // (the <video> is rendered for this mode)
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
      } else {
        const { default: Hls } = await import('hls.js');
        if (my !== run) return;
        if (!Hls.isSupported()) throw new Error('no MSE');
        hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, backBufferLength: 10 });
        hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal && my === run) frames(my); });
        hls.loadSource(url);
        hls.attachMedia(video);
      }
      video.play().catch(() => {});
    } catch {
      if (my === run) frames(my);
    }
  }

  // ---- fallback: still frames, preloaded so the shown one stays until the next is ready
  function frames(my) {
    if (hls) { hls.destroy(); hls = null; }
    mode = 'frames';
    const next = (delay) => { clearTimeout(timer); timer = setTimeout(load, delay); };
    function load() {
      if (my !== run || !active()) return;
      const img = new Image();
      const u = `api/printer/camera?t=${Date.now()}`;
      img.onload = () => { if (my !== run) return; src = u; ok = true; failed = false; next(GAP); };
      img.onerror = () => { if (my !== run) return; failed = true; next(RETRY); };
      img.src = u;
    }
    load();
  }

  function toggle() { on = !on; if (on) start(); else stopAll(); }
  function full() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else fig?.requestFullscreen?.().catch(() => {});
  }

  onMount(() => {
    alive = true;
    const io = new IntersectionObserver((en) => {
      const v = en[0]?.isIntersecting ?? true;
      if (v === visible) return;
      visible = v;
      if (on) (v ? start() : stopAll());
    });
    io.observe(fig);
    const vis = () => { if (on) (document.hidden ? stopAll() : start()); };
    document.addEventListener('visibilitychange', vis);
    return () => { alive = false; stopAll(); io.disconnect(); document.removeEventListener('visibilitychange', vis); };
  });
</script>

<figure class="cam glass" bind:this={fig}>
  <div class="head">
    <span class="t"><i class="dot" class:on={on && ok && !failed}></i> Kamera
      {#if on && mode === 'video' && ok}<span class="tiny muted">· live, få sekunders forsinkelse</span>{/if}</span>
    <div class="row" style="gap:6px">
      {#if on}<button class="btn small ghost icon-btn" aria-label="Fuld skærm" onclick={full}><Icon name="expand" size={16} /></button>{/if}
      <button class="btn small" class:primary={!on} onclick={toggle}>{on ? 'Sluk' : 'Vis kamera'}</button>
    </div>
  </div>
  {#if on}
    <button class="view" onclick={full} aria-label="Vis kameraet i fuld skærm">
      {#if mode === 'video'}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={video} muted playsinline autoplay onplaying={() => { ok = true; failed = false; }}></video>
      {:else if src}<img {src} alt="Printerens kamera" class:dim={failed} />{/if}
      {#if !ok && !failed}<span class="msg muted small">Starter kameraet …</span>{/if}
      {#if failed}<span class="msg small">Kameraet svarer ikke lige nu – prøver igen</span>{/if}
    </button>
  {/if}
</figure>

<style>
  .cam { margin: 0; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .t { display: inline-flex; align-items: center; gap: 8px; font-weight: 500; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
  .dot.on { background: var(--ok); }
  .view { position: relative; display: block; width: 100%; aspect-ratio: 16 / 9; padding: 0; border: 1px solid var(--panel-border);
    border-radius: 10px; background: var(--bg-2); overflow: hidden; cursor: zoom-in; }
  img, video { width: 100%; height: 100%; object-fit: contain; display: block; }
  img.dim { opacity: .35; }
  .msg { position: absolute; inset: 0; display: grid; place-items: center; text-align: center; padding: 16px; }
  .cam:fullscreen { background: #000; padding: 0; justify-content: center; }
  .cam:fullscreen .head { position: absolute; top: 12px; right: 12px; z-index: 1; }
  .cam:fullscreen .t { display: none; }
  .cam:fullscreen .view { border: 0; border-radius: 0; height: 100%; aspect-ratio: auto; cursor: zoom-out; background: #000; }
</style>
