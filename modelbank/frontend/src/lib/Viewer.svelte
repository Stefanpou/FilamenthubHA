<!-- Interactive 3D view of a model (drag to turn, scroll/pinch to zoom). three.js loads lazily. -->
<script>
  import { onMount } from 'svelte';
  import { parseFHM, buildGroup, addLights, fitDistance, dispose } from './mesh.js';

  let { url, colors = {}, fallback = null } = $props();
  let host = $state(), canvas = $state();
  let state = $state('loading');   // loading | ok | none | error
  let T, renderer, scene, camera, controls, group, grid, ro, raf = 0, dead = false;

  async function init() {
    T = await import('three');
    const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
    if (dead) return false;
    renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    scene = new T.Scene();
    camera = new T.PerspectiveCamera(30, 1, 0.5, 20000);
    addLights(T, scene);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.addEventListener('change', draw);
    ro = new ResizeObserver(resize);
    ro.observe(host);
    resize();
    return true;
  }

  function resize() {
    if (!renderer || !host) return;
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
    draw();
  }

  function draw() {
    if (raf || !renderer) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (controls.update()) draw();
      renderer.render(scene, camera);
    });
  }

  async function load(u, cols) {
    state = 'loading';
    try {
      if (!renderer && !(await init())) return;
      const r = await fetch(u);
      if (r.status === 404) { state = 'none'; return; }
      if (!r.ok) throw new Error(String(r.status));
      const model = parseFHM(await r.arrayBuffer());
      if (dead) return;
      if (group) { scene.remove(group); dispose(group); }
      if (grid) { scene.remove(grid); dispose(grid); }
      const b = buildGroup(T, model, cols);
      group = b.group;
      scene.add(group);
      const span = Math.max(b.size[0], b.size[2], 20);
      const cell = span > 200 ? 20 : span > 60 ? 10 : 5;
      const n = Math.ceil((span * 1.6) / cell);
      grid = new T.GridHelper(n * cell, n, 0x3fd0b2, 0x2a2d33);
      grid.material.transparent = true;
      grid.material.opacity = 0.35;
      scene.add(grid);
      const d = fitDistance(b.size, 30, camera.aspect);
      const ty = b.size[1] * 0.45;
      controls.target.set(0, ty, 0);
      camera.position.set(d * 0.62, ty + d * 0.45, d * 0.62);
      camera.near = d / 100; camera.far = d * 20; camera.updateProjectionMatrix();
      controls.update();
      state = 'ok';
      draw();
    } catch {
      state = 'error';
    }
  }

  $effect(() => { if (canvas) load(url, colors); });

  onMount(() => () => {
    dead = true;
    ro?.disconnect();
    if (raf) cancelAnimationFrame(raf);
    if (group) dispose(group);
    controls?.dispose();
    renderer?.dispose();
  });
</script>

<div class="viewer" bind:this={host}>
  <canvas bind:this={canvas} class:hide={state !== 'ok'}></canvas>
  {#if state === 'loading'}<div class="msg"><span class="spinner"></span></div>
  {:else if state !== 'ok'}
    {#if fallback}<img src={fallback} alt="">{:else}<div class="msg muted">{state === 'none' ? 'Ingen 3D-visning af denne fil' : 'Kunne ikke vise modellen'}</div>{/if}
  {/if}
  {#if state === 'ok'}<span class="hint tiny muted">Træk for at dreje · rul for at zoome</span>{/if}
</div>

<style>
  .viewer { position: relative; width: 100%; aspect-ratio: 4 / 3; border-radius: var(--radius); overflow: hidden;
    background: radial-gradient(120% 90% at 50% 20%, #1f2126 0%, #121315 70%); border: 1px solid var(--panel-border); touch-action: none; }
  canvas { width: 100%; height: 100%; display: block; cursor: grab; }
  canvas:active { cursor: grabbing; }
  .hide { visibility: hidden; position: absolute; }
  .msg { position: absolute; inset: 0; display: grid; place-items: center; }
  img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .hint { position: absolute; left: 12px; bottom: 10px; pointer-events: none; }
</style>
