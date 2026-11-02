<!-- The 3D model of the running print on a build plate. three.js is loaded lazily (dynamic import), so the first
     paint of the page does not wait for it. Falls back to the cover image with an animated "fill" effect. -->
<script>
  import { onMount } from 'svelte';
  import { withToken } from './live.svelte.js';

  // Cache-buster appended after the (optional) kiosk token, so the URL never gets two '?'.
  function bust(url, key) {
    const u = withToken(url);
    return u + (u.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(key);
  }

  let {
    modelUrl = null,      // /api/printer/model.3mf (FHM1 binary, see app/model3d.py)
    coverUrl = null,      // /api/printer/cover
    taskKey = '',         // changes when another job is shown
    progress = 0,         // 0..1: layer / total layers
    printing = false,
    color = 'cfd6e4',     // filament colour used when the model carries none
    still = false,        // prefers-reduced-motion
    compact = false,
    layer = 0,            // shown on the stand-in block when the job has no model in Bambu Cloud
    totalLayers = 0,
    percent = null,       // the printer's own % (matches the HUD ring); the stand-in block uses it
  } = $props();

  let host = $state();
  let canvas = $state();
  let mode = $state('empty'); // 'empty' (ambient plate) | 'mesh' | 'cover'
  let glFailed = $state(false);
  let coverFailed = $state(false);

  // ---- three.js state (not reactive) ----
  let T = null;              // the three module
  let renderer, scene, camera, pivot, modelGroup, plateMesh, sweep, ring, cutPlane, ghostPlane;
  let parts = [];            // {geo, solid, ghost, edges}
  let disposables = [];
  let bboxH = 1, radius = 160, target = null;
  let yaw = 0.6, elev = 0.45, userUntil = 0, dragging = false, last = null;
  let curProgress = 0, raf = 0, visible = true, destroyed = false, dirty = true, loadToken = 0;
  let size = { w: 0, h: 0 };
  let ro, io, lastInfo = '';

  const colorNum = (hex) => parseInt(String(hex || 'cfd6e4').replace('#', '').slice(0, 6), 16) || 0xcfd6e4;

  async function ensureGL() {
    if (renderer || glFailed) return !glFailed;
    try {
      T = await import('three');
      if (destroyed) return false;
      renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
      renderer.setClearColor(0x000000, 0);
      renderer.localClippingEnabled = true;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, compact ? 1.5 : 2));
      scene = new T.Scene();
      camera = new T.PerspectiveCamera(30, 1, 1, 6000);
      scene.add(new T.AmbientLight(0xffffff, 0.62));
      const key = new T.DirectionalLight(0xffffff, 1.35); key.position.set(160, 320, 220); scene.add(key);
      const rim = new T.DirectionalLight(0x7c9cff, 0.5); rim.position.set(-220, 120, -200); scene.add(rim);
      pivot = new T.Group(); scene.add(pivot);
      modelGroup = new T.Group(); pivot.add(modelGroup);
      cutPlane = new T.Plane(new T.Vector3(0, -1, 0), 0);
      ghostPlane = new T.Plane(new T.Vector3(0, 1, 0), 0);
      target = new T.Vector3();
      buildPlate();
      resize();
      return true;
    } catch (e) {
      glFailed = true;
      return false;
    }
  }

  function track(o) { disposables.push(o); return o; }

  function buildPlate() {
    // 256 x 256 mm plate in printer coordinates: x right, y back -> three (x, z = -y). Centre of the plate = (128,128).
    const c = document.createElement('canvas'); c.width = c.height = 256;
    const g = c.getContext('2d');
    g.fillStyle = '#161b26'; g.fillRect(0, 0, 256, 256);
    g.strokeStyle = 'rgba(120,140,180,.22)'; g.lineWidth = 1;
    for (let i = 0; i <= 256; i += 16) { g.beginPath(); g.moveTo(i + .5, 0); g.lineTo(i + .5, 256); g.stroke(); g.beginPath(); g.moveTo(0, i + .5); g.lineTo(256, i + .5); g.stroke(); }
    g.strokeStyle = 'rgba(56,232,198,.55)'; g.lineWidth = 3; g.strokeRect(1.5, 1.5, 253, 253);
    const tex = track(new T.CanvasTexture(c));
    tex.anisotropy = 4; tex.colorSpace = T.SRGBColorSpace;
    const mat = track(new T.MeshStandardMaterial({ map: tex, roughness: 0.85, metalness: 0.1, transparent: true, opacity: 0.95 }));
    const geo = track(new T.BoxGeometry(256, 5, 256));
    plateMesh = new T.Mesh(geo, mat);
    plateMesh.position.set(128, -2.5, -128);
    modelGroup.add(plateMesh);
    // soft light bar that sweeps over the empty plate (ambient animation)
    const sg = track(new T.PlaneGeometry(256, 10));
    const sm = track(new T.MeshBasicMaterial({ color: 0x38e8c6, transparent: true, opacity: 0.0, depthWrite: false, blending: T.AdditiveBlending }));
    sweep = new T.Mesh(sg, sm);
    sweep.rotation.x = -Math.PI / 2; sweep.position.set(128, 0.4, -128);
    modelGroup.add(sweep);
    // current-layer outline
    const rg = track(new T.BufferGeometry());
    rg.setAttribute('position', new T.Float32BufferAttribute(new Array(12).fill(0), 3));
    const rm = track(new T.LineBasicMaterial({ color: 0x38e8c6, transparent: true, opacity: 0.95 }));
    ring = new T.LineLoop(rg, rm);
    ring.visible = false;
    modelGroup.add(ring);
    frameDefault();
  }

  function frameDefault() {
    // no model: look at the plate
    modelGroup.position.set(-128, 0, 128);
    bboxH = 1; radius = 175; target.set(0, 0, 0);
    dirty = true;
  }

  function clearModel() {
    for (const p of parts) {
      modelGroup.remove(p.solid, p.ghost, p.edges);
      p.geo.dispose(); p.flat.dispose(); p.edgeGeo.dispose();
      p.solid.material.dispose(); p.ghost.material.dispose(); p.edges.material.dispose();
    }
    parts = [];
    if (ring) ring.visible = false;
  }

  function parseFHM(buf) {
    const u8 = new Uint8Array(buf);
    if (String.fromCharCode(u8[0], u8[1], u8[2], u8[3]) !== 'FHM1') throw new Error('bad model');
    const dv = new DataView(buf);
    const n = dv.getUint32(4, true);
    const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n)));
    let o = 8 + n;
    const out = [];
    for (const p of meta.parts) {
      const pos = new Float32Array(buf, o, p.v * 3); o += p.v * 12;
      const idx = new Uint32Array(buf, o, p.i); o += p.i * 4;
      out.push({ filament: p.filament, pos, idx });
    }
    return { meta, parts: out };
  }

  function buildModel(model) {
    clearModel();
    const { meta } = model;
    const bb = meta.bbox; // printer coordinates
    const colors = meta.colors || {};
    const firstColor = Object.values(colors)[0];
    for (const p of model.parts) {
      const col = colorNum(colors[String(p.filament)] || firstColor || color);
      // printer (x, y, z) -> three (x, z, -y); a proper rotation, winding is kept
      const pos = new Float32Array(p.pos.length);
      for (let i = 0; i < pos.length; i += 3) { pos[i] = p.pos[i]; pos[i + 1] = p.pos[i + 2]; pos[i + 2] = -p.pos[i + 1]; }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.BufferAttribute(pos, 3));
      geo.setIndex(new T.BufferAttribute(p.idx, 1));
      const flat = geo.toNonIndexed(); flat.computeVertexNormals();      // flat shading
      const edgeGeo = new T.EdgesGeometry(geo, 38);
      const solid = new T.Mesh(flat, new T.MeshStandardMaterial({
        color: col, roughness: 0.5, metalness: 0.06, side: T.DoubleSide, clippingPlanes: [cutPlane],
      }));
      const ghost = new T.Mesh(flat, new T.MeshBasicMaterial({
        color: 0x9fb4ff, transparent: true, opacity: 0.09, depthWrite: false, side: T.DoubleSide, clippingPlanes: [ghostPlane],
      }));
      const edges = new T.LineSegments(edgeGeo, new T.LineBasicMaterial({
        color: 0x8fa6ff, transparent: true, opacity: 0.32, clippingPlanes: [ghostPlane],
      }));
      modelGroup.add(solid, ghost, edges);
      parts.push({ geo, flat, edgeGeo, solid, ghost, edges });
    }
    bboxH = Math.max(1, bb[5] - bb[2]);
    const cx = (bb[0] + bb[3]) / 2, cy = (bb[1] + bb[4]) / 2;
    modelGroup.position.set(-cx, -bb[2] * 0, cy);           // model centre at the pivot axis; plate moves with it
    const w = bb[3] - bb[0], d = bb[4] - bb[1];
    radius = Math.max(40, 0.5 * Math.sqrt(w * w + d * d + bboxH * bboxH));
    target.set(0, bboxH * 0.42, 0);
    const a = ring.geometry.attributes.position;
    const pts = [[bb[0], bb[1]], [bb[3], bb[1]], [bb[3], bb[4]], [bb[0], bb[4]]];
    pts.forEach(([x, y], i) => a.setXYZ(i, x, 0, -y));
    a.needsUpdate = true;
    ring.geometry.computeBoundingSphere();
    curProgress = Math.min(curProgress, progress);
    dirty = true;
  }

  async function load(url, key) {
    const token = ++loadToken;
    if (!url) {
      if (renderer) { clearModel(); frameDefault(); }
      return;                       // `mode` for "no model" is derived by the effect below (cover or empty plate)
    }
    const ok = await ensureGL();
    if (token !== loadToken || destroyed) return;
    if (!ok) { mode = coverUrl ? 'cover' : 'empty'; return; }
    try {
      const r = await fetch(bust(url, key), { credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      const model = parseFHM(await r.arrayBuffer());
      if (token !== loadToken || destroyed) return;
      buildModel(model);
      mode = 'mesh';
      curProgress = 0;
      startLoop();
    } catch {
      if (token !== loadToken) return;
      mode = coverUrl ? 'cover' : 'empty';
    }
  }

  // ---- sizing / camera ----
  function resize() {
    if (!renderer || !host) return;
    const w = Math.max(1, Math.round(host.clientWidth)), h = Math.max(1, Math.round(host.clientHeight));
    if (w === size.w && h === size.h) return;
    size = { w, h };
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    dirty = true;
  }

  function placeCamera() {
    const vf = (camera.fov * Math.PI) / 180;
    const eff = Math.min(vf, 2 * Math.atan(Math.tan(vf / 2) * camera.aspect));
    const dist = (radius / Math.sin(eff / 2)) * 1.04;
    camera.position.set(
      target.x + dist * Math.cos(elev) * 0, target.y + dist * Math.sin(elev), target.z + dist * Math.cos(elev));
    camera.lookAt(target);
  }

  // ---- loop ----
  function startLoop() {
    if (raf || destroyed || !renderer) return;
    last = null;
    raf = requestAnimationFrame(frame);
  }

  function frame(t) {
    raf = 0;
    if (destroyed || !renderer) return;
    if (!visible || document.hidden) return;                       // resumed by observers
    const dt = last == null ? 0.016 : Math.min(0.1, (t - last) / 1000);
    last = t;
    const empty = mode !== 'mesh';
    let animating = false;
    // no auto-rotation: the model stands still (drag to turn it)
    // progress follows the printer smoothly
    const goal = printing || progress > 0 ? Math.min(1, Math.max(0, progress)) : 0;
    const diff = goal - curProgress;
    if (Math.abs(diff) > 0.0004) { curProgress += diff * Math.min(1, dt * 2.2); animating = true; dirty = true; }
    const done = curProgress >= 0.997;
    const cutY = done ? bboxH + 5 : curProgress * bboxH;
    cutPlane.constant = cutY;
    ghostPlane.constant = -cutY;
    ring.visible = mode === 'mesh' && printing && !done && curProgress > 0.002;
    if (ring.visible) ring.position.y = cutY;
    for (const p of parts) p.ghost.visible = p.edges.visible = !done;
    if (empty && !still && sweep) {                                // ambient: a light bar sweeps over the plate
      const ph = (t / 5200) % 1;
      sweep.position.z = -256 * ph;
      sweep.material.opacity = 0.28 * Math.sin(Math.PI * ph);
      animating = true;
    } else if (sweep) sweep.material.opacity = 0;
    if (printing && mode === 'mesh' && !still) animating = true;
    pivot.rotation.y = yaw;
    if (animating || dirty) {
      placeCamera();
      renderer.render(scene, camera);
      dirty = false;
    }
    // idle with reduced motion: draw once and stop; otherwise keep going (30 fps when only the ambient runs)
    if (animating || (!still && mode !== 'cover')) raf = requestAnimationFrame(frame);
  }

  function nudge() { dirty = true; if (!raf && !destroyed) startLoop(); }

  // ---- interaction ----
  let px = 0, py = 0;
  function down(e) {
    if (mode !== 'mesh' && mode !== 'empty') return;
    dragging = true; px = e.clientX; py = e.clientY;
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* ignore */ }
  }
  function move(e) {
    if (!dragging) return;
    yaw += (e.clientX - px) * 0.012;
    elev = Math.min(1.35, Math.max(0.08, elev + (e.clientY - py) * 0.008));
    px = e.clientX; py = e.clientY;
    dirty = true; userUntil = performance.now() + 4000; nudge();
  }
  function up() { dragging = false; userUntil = performance.now() + 3000; }
  function reset() { yaw = 0.6; elev = 0.45; dirty = true; userUntil = 0; nudge(); }

  onMount(() => {
    ro = new ResizeObserver(() => { resize(); nudge(); });
    ro.observe(host);
    io = new IntersectionObserver((en) => {
      visible = en[0]?.isIntersecting ?? true;
      if (visible) { dirty = true; startLoop(); }
    });
    io.observe(host);
    const vis = () => { if (!document.hidden) { dirty = true; startLoop(); } };
    document.addEventListener('visibilitychange', vis);
    return () => {
      destroyed = true;
      document.removeEventListener('visibilitychange', vis);
      ro?.disconnect(); io?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      try {
        clearModel();
        disposables.forEach((d) => d.dispose && d.dispose());
        renderer?.dispose();
        renderer?.forceContextLoss();
      } catch { /* ignore */ }
      renderer = null; scene = null; T = null;
    };
  });

  // load a model whenever the job (or its file) changes
  $effect(() => {
    const url = modelUrl, key = taskKey;
    const info = `${url}|${key}`;
    if (info === lastInfo) return;
    lastInfo = info;
    coverFailed = false;
    load(url, key);
  });
  // no model: show the cover (animated fill) if there is one, else the empty plate with its ambient animation
  $effect(() => { if (!modelUrl) mode = coverUrl && !coverFailed ? 'cover' : 'empty'; });
  $effect(() => { if (mode === 'empty' && !modelUrl) ensureGL().then((ok) => { if (ok) { frameDefault(); startLoop(); } }); });
  // progress / state changes wake the loop (it may be asleep with reduced motion)
  $effect(() => { progress; printing; color; nudge(); });

  const pct = $derived(Math.max(0, Math.min(100, Math.round(progress * 100))));
  const standPct = $derived(percent == null ? pct : Math.max(0, Math.min(100, Math.round(percent))));
</script>

<div class="mc" bind:this={host} class:compact>
  <canvas bind:this={canvas} class:hide={mode === 'cover'}
    onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up} ondblclick={reset}
    aria-label="3D-model af det aktuelle print"></canvas>
  {#if mode === 'cover' && coverUrl && !coverFailed}
    <div class="cover" class:still>
      <img class="ghost" src={bust(coverUrl, taskKey)} alt="" onerror={() => (coverFailed = true)} />
      <img class="solid" src={bust(coverUrl, taskKey)} alt="Printets forside"
        style="clip-path: inset({100 - pct}% 0 0 0)" />
      {#if printing && pct > 0 && pct < 100}<div class="edge" style="bottom: {pct}%"></div>{/if}
    </div>
  {/if}
  {#if mode === 'empty' && !modelUrl && (printing || totalLayers > 0)}
    <!-- No model in Bambu Cloud (started from the printer / USB): a stand-in block fills up with the progress -->
    <div class="stand" class:still class:compact style="--fc: #{color}">
      <div class="block">
        <div class="fill" style="height: {standPct}%"></div>
        {#if printing && standPct < 100}<div class="edge" style="bottom: {standPct}%"></div>{/if}
      </div>
      <div class="lbl"><b>{standPct}<small>%</small></b>{#if totalLayers > 0}<span>lag {layer}/{totalLayers}</span>{/if}</div>
    </div>
  {/if}
</div>

<style>
  .mc { position: absolute; inset: 0; container-type: inline-size; }
  canvas { width: 100%; height: 100%; display: block; touch-action: pan-y; cursor: grab; outline: none;
    -webkit-mask-image: radial-gradient(ellipse 62% 62% at 50% 52%, #000 62%, transparent 100%);
            mask-image: radial-gradient(ellipse 62% 62% at 50% 52%, #000 62%, transparent 100%); }
  canvas:active { cursor: grabbing; }
  canvas.hide { visibility: hidden; }
  .cover { position: absolute; inset: 6% 12% 4%; pointer-events: none; }
  .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .cover .ghost { filter: grayscale(1) brightness(.8); opacity: .22; }
  .cover .solid { transition: clip-path 1.2s var(--ease, ease); }
  .cover .edge { position: absolute; left: 4%; right: 4%; height: 2px; background: var(--accent); box-shadow: 0 0 12px var(--accent);
    transition: bottom 1.2s var(--ease, ease); animation: pulse 1.6s ease-in-out infinite; }
  .cover.still .edge { animation: none; }
  @keyframes pulse { 50% { opacity: .45; } }
  .stand { position: absolute; inset: 10% 22% 8%; display: flex; align-items: flex-end; justify-content: center; gap: 14px; pointer-events: none; }
  .stand .block { position: relative; width: 46%; height: 82%; border-radius: 6px 6px 3px 3px; overflow: hidden;
    background: repeating-linear-gradient(0deg, #ffffff10 0 2px, transparent 2px 6px), #ffffff08;
    border: 1px dashed #ffffff30; box-shadow: inset 0 0 18px #0006; }
  .stand .fill { position: absolute; left: 0; right: 0; bottom: 0; transition: height 1.2s var(--ease, ease);
    background: repeating-linear-gradient(0deg, #00000026 0 2px, transparent 2px 6px),
      linear-gradient(90deg, color-mix(in srgb, var(--fc) 70%, #000), var(--fc) 45%, color-mix(in srgb, var(--fc) 80%, #fff) 60%, color-mix(in srgb, var(--fc) 65%, #000));
    box-shadow: 0 0 22px -6px var(--fc); }
  .stand .edge { position: absolute; left: -2px; right: -2px; height: 2px; background: var(--accent); box-shadow: 0 0 12px var(--accent);
    transition: bottom 1.2s var(--ease, ease); animation: pulse 1.6s ease-in-out infinite; }
  .stand.still .edge { animation: none; }
  .stand .lbl { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; color: var(--text, #e8ecf4); text-shadow: 0 1px 6px #000; }
  .stand .lbl b { font-size: clamp(16px, 4.2cqi, 30px); font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; }
  .stand .lbl small { font-size: .55em; opacity: .7; margin-left: 1px; }
  .stand .lbl span { font-size: clamp(10px, 1.8cqi, 13px); color: var(--muted, #9aa3b5); white-space: nowrap; }
  .stand.compact .lbl span { display: none; }
  @media (prefers-reduced-motion: reduce) { .cover .edge, .stand .edge { animation: none; } .cover .solid, .cover .edge, .stand .fill, .stand .edge { transition: none; } }
</style>
