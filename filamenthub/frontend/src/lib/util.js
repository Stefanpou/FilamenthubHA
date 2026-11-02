export const nf = new Intl.NumberFormat('da-DK', { maximumFractionDigits: 0 });
export const nf1 = new Intl.NumberFormat('da-DK', { maximumFractionDigits: 1 });
export const nf2 = new Intl.NumberFormat('da-DK', { maximumFractionDigits: 2 });
export const fmtG = (g) => `${nf.format(Number(g) || 0)} g`;
export const fmtG1 = (g) => `${nf1.format(Number(g) || 0)} g`;
export const fmtKg = (g) => ((Number(g) || 0) >= 1000 ? `${nf2.format((Number(g) || 0) / 1000)} kg` : fmtG(g));
export const fmtKr = (v) => `${nf.format(Number(v) || 0)} kr`;
export const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString('da-DK', { dateStyle: 'short', timeStyle: 'short' }) : '–');
export const fmtIso = (iso) => (iso ? new Date(iso).toLocaleString('da-DK', { dateStyle: 'short', timeStyle: 'short' }) : '–');
export const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('da-DK', { day: 'numeric', month: 'short', year: 'numeric' }) : '–');

export function hex(c, fallback = '#7a8396') {
  const s = String(c || '').replace('#', '').trim();
  return /^[0-9a-fA-F]{6}/.test(s) ? `#${s.slice(0, 6).toUpperCase()}` : fallback;
}
export function isLight(c) {
  const h = hex(c).slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return (r * 299 + g * 587 + b * 114) / 1000 > 150;
}
export const pctOf = (s) => {
  const init = Number(s.initial) || 0;
  if (init <= 0) return 0;
  return Math.max(0, Math.min(1, (Number(s.remaining) || 0) / init));
};
export const isLow = (s) => !s.archived && Number(s.initial) > 0 && (pctOf(s) <= 0.2 || Number(s.remaining) < 100);
export const levelClass = (p) => (p <= 0.1 ? 'crit' : p <= 0.25 ? 'low' : '');
export const spoolName = (s) => [s.vendor, s.material, s.name].filter(Boolean).join(' ');
export const num = (v) => {
  const x = String(v ?? '').replace(',', '.').trim();
  return x === '' || isNaN(Number(x)) ? null : Number(x);
};
export const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// Days since a YYYY-MM-DD date (local calendar days) and a Danish phrase for it.
export const daysSince = (d) => (d ? Math.round((new Date(new Date().toDateString()) - new Date(d + 'T00:00:00')) / 86400000) : null);
export const agoDa = (d) => { const n = daysSince(d); return n == null ? '' : n <= 0 ? 'i dag' : n === 1 ? 'i går' : `for ${n} dage siden`; };
export const today = () => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`; };

// Sub-categories within a material ("PLA Silk", "PETG HF", "TPU 95A") from the finish, properties and name.
const KINDS = [['Silk'], ['Matte'], ['Marble'], ['Wood'], ['Glow', 'glow-in', 'selvlysende'], ['Sparkle', 'glitter', 'galaxy'],
  ['Metal', 'metallic'], ['Translucent', 'transparent', 'clear'], ['CF', 'carbon'], ['GF', 'glass fiber'], ['HF', 'high flow'],
  ['High Speed', 'hyper', 'rapid'], ['Gradient', 'rainbow', 'multicolor', 'dual color', 'tri color'], ['Basic']];
export const KIND_NAMES = KINDS.map((k) => k[0]);
// The kind found in the finish, properties and name ('' = none).
export function autoKind(s) {
  const text = ` ${s.details?.finish || ''} ${s.details?.properties || ''} ${s.name} `.toLowerCase();
  const hit = KINDS.find((ws) => ws.some((w) => new RegExp(`[^a-z]${w.toLowerCase()}[^a-z]`).test(text)));
  if (hit) return hit[0];
  return /[^0-9](\d{2}[ad])[^a-z]/i.exec(text)?.[1].toUpperCase() || '';   // TPU hardness, e.g. 95A
}
// The kind set by hand on the filament wins ("-" = plain material), else the one found in the text.
const kind = (s) => { const k = s.details?.category; return k === '-' ? '' : k || autoKind(s); };
export const category = (s) => (kind(s) ? `${s.material} ${kind(s)}` : '');
