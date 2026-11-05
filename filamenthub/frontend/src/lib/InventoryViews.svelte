<!-- Alternative Lager views: compact list, sortable table, grouped cards and a colour wall. The card grid lives in Inventory. -->
<script>
  import SpoolCard from './SpoolCard.svelte';
  import { fmtG, fmtKg, fmtDate, hex, pctOf, levelClass, isLow, category } from './util.js';

  let { view, spools, inSlot = {}, groupBy = 'material', family, onopen } = $props();

  const label = (s) => category(s) || s.material || '–';
  const pct = (s) => Math.round(pctOf(s) * 100);
  // The sync writes the slot into the location ("X2D - A2"); only fall back to the slot label without one.
  const where = (s) => s.location || inSlot[s.id] || '';
  // Very dark filament would vanish on the dark page: draw its remaining-ring in grey.
  // multi-colour filament (silk tri-colour, gradients): every colour as a slice, else the one colour
  const fill = (s) => (s.colors?.length > 1 ? `conic-gradient(${s.colors.map((c, i) => `${hex(c)} ${(i * 100) / s.colors.length}% ${((i + 1) * 100) / s.colors.length}%`).join(', ')})` : hex(s.color));
  const ringOf = (c) => { const h = hex(c).slice(1); const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); return 0.2126 * r + 0.7152 * g + 0.0722 * b < 60 ? '#a9b2c4' : hex(c); };

  // ---- table: own column sort on top of the page's filtered/sorted list
  let tkey = $state(''), tdir = $state(1);
  const COLS = [
    ['id', '#', (s) => s.id],
    ['material', 'Materiale', (s) => label(s)],
    ['vendor', 'Mærke', (s) => s.vendor || ''],
    ['name', 'Navn', (s) => s.name || ''],
    ['remaining', 'Rest', (s) => Number(s.remaining) || 0],
    ['pct', '%', (s) => pctOf(s)],
    ['price', 'kr/kg', (s) => s.kg_price],
    ['location', 'Placering', (s) => where(s)],
    ['used', 'Sidst brugt', (s) => s.last_used || ''],
    ['dried', 'Tørret', (s) => s.dried || ''],
  ];
  function sortBy(k) { if (tkey === k) tdir = -tdir; else { tkey = k; tdir = k === 'remaining' || k === 'pct' || k === 'used' || k === 'dried' ? -1 : 1; } }
  const rows = $derived.by(() => {
    if (!tkey) return spools;
    const get = COLS.find((c) => c[0] === tkey)[2];
    return [...spools].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x == null || x === '') return y == null || y === '' ? 0 : 1;   // empty values always last
      if (y == null || y === '') return -1;
      return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'da')) * tdir;
    });
  });

  // ---- grouped
  const GROUP_KEY = { material: label, vendor: (s) => s.vendor || 'Ukendt mærke', location: (s) => s.location || 'Ingen placering', color: (s) => family(s.color) };
  const groups = $derived.by(() => {
    const m = new Map();
    for (const s of spools) { const k = GROUP_KEY[groupBy](s); if (!m.has(k)) m.set(k, []); m.get(k).push(s); }
    return [...m].sort((a, b) => a[0].localeCompare(b[0], 'da'));
  });

  // ---- colour wall: ordered by hue, dark/greys first
  function hsl(c) {
    const h = hex(c).slice(1);
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
    if (d < 0.1) return [-1, l];
    let hh = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [(hh * 60 + 360) % 360, l];
  }
  const wall = $derived([...spools].sort((a, b) => { const [ha, la] = hsl(a.color), [hb, lb] = hsl(b.color); return ha - hb || la - lb; }));
</script>

{#if view === 'list'}
  <ul class="list glass">
    {#each spools as s (s.id)}
      <li><button onclick={() => onopen(s.id)} class:arch={s.archived}>
        <i class="dot" style="--c:{fill(s)}"></i>
        <span class="txt">
          <b class="ellipsis">{s.name || label(s)}</b>
          <span class="sub ellipsis"><span>{label(s)}</span>{#if s.vendor}<span class="muted"> · {s.vendor}</span>{/if}{#if inSlot[s.id]}<span class="slot"> · {inSlot[s.id]}</span>{/if}</span>
        </span>
        <span class="amt"><span class="g mono">{fmtG(s.remaining)}</span><span class="p mono {levelClass(pctOf(s))}">{pct(s)}%</span></span>
      </button></li>
    {/each}
  </ul>
{:else if view === 'table'}
  <div class="tblwrap glass">
    <table>
      <thead><tr>
        {#each COLS as [k, t]}<th class:num={k === 'id' || k === 'remaining' || k === 'pct' || k === 'price'}><button onclick={() => sortBy(k)} class:on={tkey === k}>{t}{#if tkey === k}<span class="arr">{tdir > 0 ? '▲' : '▼'}</span>{/if}</button></th>{/each}
      </tr></thead>
      <tbody>
        {#each rows as s (s.id)}
          <tr onclick={() => onopen(s.id)} class:arch={s.archived}>
            <td class="num mono">{s.id}</td>
            <td><i class="dot" style="--c:{fill(s)}"></i>{label(s)}</td>
            <td>{s.vendor || '–'}</td>
            <td class="ellipsis nmc">{s.name || '–'}</td>
            <td class="num mono">{fmtG(s.remaining)}</td>
            <td class="num mono {levelClass(pctOf(s))}">{pct(s)}%</td>
            <td class="num mono">{s.kg_price == null ? '–' : Math.round(s.kg_price)}</td>
            <td>{where(s) || '–'}</td>
            <td>{s.last_used ? fmtDate(String(s.last_used).slice(0, 10)) : '–'}</td>
            <td>{s.dried ? fmtDate(s.dried) : '–'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{:else if view === 'group'}
  <div class="stack" style="gap:22px">
    {#each groups as [name, list] (name)}
      <section class="stack" style="gap:10px">
        <h3 class="gh"><span>{name}</span><span class="muted small">{list.length} {list.length === 1 ? 'rulle' : 'ruller'} · {fmtKg(list.reduce((a, s) => a + (Number(s.remaining) || 0), 0))}</span></h3>
        <div class="grid">{#each list as s, i (s.id)}<SpoolCard {s} index={i} slotLabel={inSlot[s.id] || ''} onclick={() => onopen(s.id)} />{/each}</div>
      </section>
    {/each}
  </div>
{:else if view === 'wall'}
  <div class="wall">
    {#each wall as s (s.id)}
      <button class="sw" onclick={() => onopen(s.id)} class:arch={s.archived} class:low={isLow(s)}
        title="{s.vendor} {label(s)} {s.name} – {fmtG(s.remaining)} ({pct(s)} %)" style="--c:{fill(s)};--rc:{ringOf(s.color)};--p:{pct(s)}">
        <span class="ring"><span class="disc"></span></span>
        <span class="wl ellipsis">{s.name || label(s)}</span>
        <span class="wm muted ellipsis">{label(s)} · {pct(s)}%</span>
      </button>
    {/each}
  </div>
{/if}

<style>
  .dot { display: inline-block; width: 14px; height: 14px; border-radius: 50%; background: var(--c); box-shadow: inset 0 0 0 1px #ffffff30; flex: none; vertical-align: -2px; margin-right: 8px; }
  .arch { opacity: .55; }
  .crit { color: var(--danger); }
  .low { color: var(--warn); }

  .list { list-style: none; margin: 0; padding: 4px; }
  .list li + li { border-top: 1px solid var(--panel-border); }
  .list button { all: unset; box-sizing: border-box; cursor: pointer; width: 100%; display: flex; align-items: center; gap: 10px; padding: 10px 10px; border-radius: 10px; font-size: 14px; }
  .list button:hover { background: color-mix(in srgb, var(--accent) 7%, transparent); }
  .list .dot { margin: 0; }
  .list .txt { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .list .txt b { font-weight: 500; }
  .list .sub { font-size: 12.5px; }
  .list .slot { color: var(--accent); }
  .list .amt { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
  .list .g { font-weight: 500; }
  .list .p { font-size: 12px; }
  .ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .tblwrap { overflow-x: auto; padding: 2px; touch-action: pan-x pan-y; overscroll-behavior-x: contain; }
  table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
  th { position: sticky; top: 0; text-align: left; font-weight: 600; white-space: nowrap; }
  th button { all: unset; cursor: pointer; padding: 10px 10px; display: inline-flex; gap: 4px; color: var(--muted); font-size: 13px; font-weight: 500; }
  th button.on { color: var(--accent); }
  .arr { font-size: 9px; }
  td { padding: 9px 10px; border-top: 1px solid var(--panel-border); white-space: nowrap; }
  tbody tr { cursor: pointer; }
  tbody tr:hover td { background: color-mix(in srgb, var(--accent) 7%, transparent); }
  .num { text-align: right; }
  th.num button { justify-content: flex-end; width: 100%; box-sizing: border-box; }
  .nmc { max-width: 22ch; }

  .gh { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; font-size: 17px; margin: 0; padding-bottom: 6px; border-bottom: 1px solid var(--panel-border); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 12px; }
  @media (min-width: 700px) { .grid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; } }

  .wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 16px 10px; }
  .sw { all: unset; box-sizing: border-box; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0; text-align: center; }
  .ring { width: 74px; height: 74px; border-radius: 50%; display: grid; place-items: center;
    background: conic-gradient(var(--rc) calc(var(--p) * 1%), color-mix(in srgb, var(--muted) 22%, transparent) 0);
    transition: box-shadow .14s; }
  .disc { width: 58px; height: 58px; border-radius: 50%; background: var(--c); box-shadow: inset 0 0 0 1px #ffffff30, inset 0 -8px 16px #0003, 0 0 0 4px var(--bg); }
  .sw:hover .ring { box-shadow: 0 0 0 1px rgba(237,237,237,.3); }
  .sw.low .ring { outline: 2px dashed var(--warn); outline-offset: 3px; }
  .wl { font-size: 12.5px; font-weight: 600; max-width: 100%; }
  .wm { font-size: 11px; max-width: 100%; }
</style>
