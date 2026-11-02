<!-- Barcode scanner: live camera via the vendored zxing-wasm BarcodeDetector, photo fallback, typed code. -->
<script>
  import Sheet from './ui/Sheet.svelte';
  import Icon from './ui/Icon.svelte';
  import { onMount } from 'svelte';
  let { onresult, onclose, title = 'Scan stregkode' } = $props();

  const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'qr_code', 'data_matrix'];
  const ZXING_JS = 'static/vendor/barcode-detector-3.2.2.js';
  const WASM_DIR = 'static/vendor/zxing-wasm-3.1.3/';
  const live = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;

  let video = $state();
  let msg = $state(live ? 'Hold stregkoden inden for rammen' : 'Tag et billede af stregkoden – eller skriv tallene');
  let text = $state('');
  let stream = null, done = false, timer = null;

  let detectorPromise = null;
  function getDetector() {
    if (detectorPromise) return detectorPromise;
    detectorPromise = new Promise((resolve, reject) => {
      const ready = () => {
        try {
          const a = window.BarcodeDetectionAPI;
          a.prepareZXingModule({ overrides: { locateFile: (p, prefix) => (p.endsWith('.wasm') ? WASM_DIR + p : prefix + p) }, fireImmediately: true });
          resolve(new a.BarcodeDetector({ formats: FORMATS }));
        } catch (e) { reject(e); }
      };
      if (window.BarcodeDetectionAPI) return ready();
      const s = document.createElement('script');
      s.src = ZXING_JS; s.onload = ready;
      s.onerror = () => { detectorPromise = null; reject(new Error('Stregkodelæseren kunne ikke indlæses – skriv koden i feltet')); };
      document.head.appendChild(s);
    });
    return detectorPromise;
  }

  function finish(val) {
    if (done) return;
    done = true;
    clearTimeout(timer);
    stream?.getTracks().forEach((t) => t.stop());
    const code = val ? String(val).trim() : null;
    if (code) onresult(code); else onclose();
  }

  function toCanvas(bmp, maxSide, deg = 0) {
    const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const w = bmp.width * k, h = bmp.height * k, a = (deg * Math.PI) / 180;
    const cw = Math.ceil(Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a)));
    const ch = Math.ceil(Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a)));
    const c = document.createElement('canvas'); c.width = cw; c.height = ch;
    const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, cw, ch);
    x.translate(cw / 2, ch / 2); x.rotate(a); x.drawImage(bmp, -w / 2, -h / 2, w, h);
    return c;
  }
  async function decodeImage(file) {
    const det = await getDetector();
    const bmp = await createImageBitmap(file);
    try {
      for (const [side, deg] of [[2048, 0], [4096, 0], [2048, 45], [2048, 90]]) {
        const hits = await det.detect(toCanvas(bmp, side, deg));
        if (hits.length) return hits[0].rawValue;
      }
      return null;
    } finally { bmp.close?.(); }
  }
  async function onfile(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    msg = 'Læser billedet …';
    try {
      const code = await decodeImage(f);
      if (code) return finish(code);
      msg = 'Kunne ikke læse stregkoden – prøv tættere på og uden genskin, eller skriv tallene.';
    } catch (err) { msg = err.message; }
  }

  onMount(() => {
    getDetector().catch(() => {});
    if (live) (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }, audio: false });
        if (done) { stream.getTracks().forEach((t) => t.stop()); return; }
        video.srcObject = stream;
        await video.play();
        const det = await getDetector();
        let busy = false;
        const tick = async () => {
          if (done) return;
          if (!busy && video.readyState >= 2) {
            busy = true;
            try {
              const r = await det.detect(video);
              if (r.length) { navigator.vibrate?.(60); return finish(r[0].rawValue); }
            } catch { /* keep trying */ }
            busy = false;
          }
          timer = setTimeout(tick, 120);
        };
        tick();
      } catch (err) {
        msg = `Kameraet kunne ikke startes (${err.message}). Brug "Tag billede" eller skriv koden.`;
      }
    })();
    return () => { done = true; clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
  });
</script>

<Sheet {title} onclose={() => finish(null)}>
  {#if live}
    <div class="cam">
      <video bind:this={video} playsinline muted></video>
      <div class="frame"><i></i><i></i><i></i><i></i><span class="laser"></span></div>
    </div>
  {/if}
  <p class="small muted center" style="margin: 12px 0">{msg}</p>
  <label class="btn block {live ? '' : 'primary'}" style="cursor:pointer">
    <Icon name="camera" size={18} /> Tag billede af stregkoden
    <input type="file" accept="image/*" capture="environment" hidden onchange={onfile}>
  </label>
  <div class="row" style="margin-top:12px">
    <input class="input" type="text" inputmode="numeric" autocomplete="off" placeholder="… eller skriv koden, fx 6970470250022" bind:value={text}
      onkeydown={(e) => e.key === 'Enter' && finish(text)}>
    <button class="btn primary" onclick={() => finish(text)}>OK</button>
  </div>
</Sheet>

<style>
  .cam { position: relative; border-radius: 18px; overflow: hidden; background: #000; aspect-ratio: 4/3; }
  video { width: 100%; height: 100%; object-fit: cover; }
  .frame { position: absolute; inset: 14% 10%; }
  .frame i { position: absolute; width: 26px; height: 26px; border: 3px solid var(--accent); filter: drop-shadow(0 0 6px var(--accent)); }
  .frame i:nth-child(1) { left: 0; top: 0; border-right: 0; border-bottom: 0; border-radius: 10px 0 0 0; }
  .frame i:nth-child(2) { right: 0; top: 0; border-left: 0; border-bottom: 0; border-radius: 0 10px 0 0; }
  .frame i:nth-child(3) { left: 0; bottom: 0; border-right: 0; border-top: 0; border-radius: 0 0 0 10px; }
  .frame i:nth-child(4) { right: 0; bottom: 0; border-left: 0; border-top: 0; border-radius: 0 0 10px 0; }
  .laser { position: absolute; left: 4%; right: 4%; height: 2px; background: var(--accent); box-shadow: 0 0 14px 2px var(--accent); animation: scan 2.2s ease-in-out infinite alternate; }
  @keyframes scan { from { top: 8%; } to { top: 92%; } }
</style>
