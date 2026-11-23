// Renders a PNG thumbnail for models whose file has no picture (STL, plain 3MF) and stores it on the server.
// One shared offscreen renderer, one model at a time.
import { parseFHM, buildGroup, addLights, fitDistance, dispose } from './mesh.js';

let T, renderer, scene, camera;
const queue = [];
let busy = false;

async function setup() {
  if (renderer) return true;
  try {
    T = await import('three');
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 384;
    renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: true, preserveDrawingBuffer: true });
    renderer.setClearColor(0x000000, 0);
    scene = new T.Scene();
    camera = new T.PerspectiveCamera(30, 1, 0.5, 8000);
    addLights(T, scene);
    return true;
  } catch { return false; }
}

async function renderOne(id) {
  const r = await fetch(`api/models/${id}/mesh`);
  if (!r.ok) return false;
  const { group, size } = buildGroup(T, parseFHM(await r.arrayBuffer()));
  scene.add(group);
  const d = fitDistance(size, 30) * 0.95;
  const yaw = 0.75, elev = 0.5;
  const ty = size[1] * 0.5;
  camera.position.set(Math.sin(yaw) * Math.cos(elev) * d, ty + Math.sin(elev) * d, Math.cos(yaw) * Math.cos(elev) * d);
  camera.lookAt(0, ty, 0);
  renderer.render(scene, camera);
  const blob = await new Promise((res) => renderer.domElement.toBlob(res, 'image/png'));
  scene.remove(group);
  dispose(group);
  if (!blob) return false;
  await fetch(`api/models/${id}/thumb`, { method: 'POST', headers: { 'Content-Type': 'image/png' }, body: blob });
  return true;
}

/** onDone(id) is called after each stored thumbnail. */
export function requestThumbs(ids, onDone) {
  for (const id of ids) if (!queue.some((q) => q.id === id)) queue.push({ id, onDone });
  pump();
}

async function pump() {
  if (busy || !queue.length) return;
  busy = true;
  if (!(await setup())) { queue.length = 0; busy = false; return; }
  while (queue.length) {
    const { id, onDone } = queue.shift();
    try { if (await renderOne(id)) onDone?.(id); } catch { /* skip this one */ }
    await new Promise((r) => setTimeout(r, 30));
  }
  busy = false;
}

