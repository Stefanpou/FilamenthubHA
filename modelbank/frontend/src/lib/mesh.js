// FHM1 (see FilamentHub app/model3d.py) -> three.js group. three is imported lazily by the callers.
export function parseFHM(buf) {
  const u8 = new Uint8Array(buf);
  if (String.fromCharCode(u8[0], u8[1], u8[2], u8[3]) !== 'FHM1') throw new Error('bad model');
  const n = new DataView(buf).getUint32(4, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n)));
  let o = 8 + n;
  const parts = [];
  for (const p of meta.parts) {
    const pos = new Float32Array(buf, o, p.v * 3); o += p.v * 12;
    const idx = new Uint32Array(buf, o, p.i); o += p.i * 4;
    parts.push({ filament: p.filament, pos, idx });
  }
  return { meta, parts };
}

const DEFAULT = 0xc9ced8;

/** Meshes in three coordinates (printer x, y, z -> three x, z, -y), centred on x/z, standing on y = 0. */
export function buildGroup(T, model, colors = {}) {
  const g = new T.Group();
  const bb = model.meta.bbox;
  const cx = (bb[0] + bb[3]) / 2, cy = (bb[1] + bb[4]) / 2;
  const fallback = Object.values({ ...(model.meta.colors || {}), ...colors })[0];
  for (const p of model.parts) {
    const hex = colors[String(p.filament)] || (model.meta.colors || {})[String(p.filament)] || fallback;
    const col = hex ? parseInt(String(hex).replace('#', '').slice(0, 6), 16) : DEFAULT;
    const pos = new Float32Array(p.pos.length);
    for (let i = 0; i < pos.length; i += 3) {
      pos[i] = p.pos[i] - cx; pos[i + 1] = p.pos[i + 2] - bb[2]; pos[i + 2] = -(p.pos[i + 1] - cy);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(pos, 3));
    geo.setIndex(new T.BufferAttribute(p.idx, 1));
    const flat = geo.toNonIndexed();
    flat.computeVertexNormals();
    geo.dispose();
    g.add(new T.Mesh(flat, new T.MeshStandardMaterial({ color: col, roughness: 0.55, metalness: 0.05, side: T.DoubleSide })));
  }
  const size = [bb[3] - bb[0], bb[5] - bb[2], bb[4] - bb[1]];
  return { group: g, size };
}

export function addLights(T, scene) {
  scene.add(new T.AmbientLight(0xffffff, 0.65));
  const key = new T.DirectionalLight(0xffffff, 1.4); key.position.set(160, 320, 220); scene.add(key);
  const rim = new T.DirectionalLight(0x9fb4ff, 0.55); rim.position.set(-220, 120, -200); scene.add(rim);
}

/** Camera distance that fits a box of `size` in a perspective camera with vertical fov `fov` (deg). */
export function fitDistance(size, fov, aspect = 1) {
  const r = 0.5 * Math.hypot(size[0], size[1], size[2]) || 10;
  const f = (fov * Math.PI) / 180;
  return (r / Math.sin(f / 2)) * (aspect < 1 ? 1 / aspect : 1) * 1.05;
}

export function dispose(obj) {
  obj.traverse((o) => { o.geometry?.dispose(); o.material?.dispose?.(); });
}
