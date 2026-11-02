// Small formatting helpers (Danish).
export function fmtRemaining(min) {
  const m = Math.max(0, Math.round(Number(min) || 0));
  if (m <= 0) return '–';
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h === 0) return `${r} min`;
  if (h >= 24) return `${Math.floor(h / 24)} d ${h % 24} t`;
  return r ? `${h} t ${r} min` : `${h} t`;
}

export function fmtEta(min, now = Date.now()) {
  const m = Math.round(Number(min) || 0);
  if (m <= 0) return '';
  const d = new Date(now + m * 60000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const sameDay = d.toDateString() === new Date(now).toDateString();
  return sameDay ? `${hh}:${mm}` : `i morgen ${hh}:${mm}`;
}

export const temp = (v) => (v === null || v === undefined ? '–' : `${Math.round(v)}°`);
export const SPEEDS = { 1: 'Stille', 2: 'Standard', 3: 'Sport', 4: 'Ludicrous' };

// Light/dark helpers for filament colours (RRGGBB without #).
export function hex(c, fallback = '8a94a8') {
  const s = String(c || '').replace('#', '');
  return /^[0-9a-fA-F]{6}/.test(s) ? s.slice(0, 6) : fallback;
}
export function luminance(c) {
  const h = hex(c);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export const STATE_TONE = {
  IDLE: 'idle', RUNNING: 'run', PAUSE: 'warn', FINISH: 'ok', FAILED: 'bad', PREPARE: 'prep', SLICING: 'prep',
  UNKNOWN: 'idle',
};

// Drawing family from the live printer block: "h2" (dual nozzle: X2D/H2D), "x1p1" (enclosed cube: X1/P1/P2S), "a1" (open bedslinger).
export function printerFamily(p) {
  if (!p) return 'h2';   // before the first state arrives: the original X2D drawing
  if (p.family) return p.family;
  const m = String(p?.model || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  if (m.startsWith('a1')) return 'a1';
  if (/^(h2|x2)/.test(m)) return 'h2';
  if (/^(x1|p1|p2)/.test(m)) return 'x1p1';
  return (p?.nozzles?.length || 0) >= 2 ? 'h2' : 'x1p1';
}

// AMS humidity text: a real percentage when the unit reports one, else the 1-5 index (5 = driest).
export function humidityText(u) {
  if (!u) return '–';
  if (u.humidity != null) return `${u.humidity}%`;
  if (u.humidity_level != null) return `${u.humidity_level}/5`;
  return '–';
}
