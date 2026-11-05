<script>
  import SpoolCard from '../lib/SpoolCard.svelte';
  import InventoryViews from '../lib/InventoryViews.svelte';
  import SpoolSheet from '../lib/SpoolSheet.svelte';
  import Spool from '../lib/ui/Spool.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import { app, route, go } from '../lib/stores.svelte.js';
  import { fmtKg, isLow, pctOf, hex, category as cat } from '../lib/util.js';

  let q = $state(''), material = $state(''), vendor = $state(''), location = $state(''), colorFam = $state('');
  let low = $state(route.query.low === '1'), archived = $state(false), noPrice = $state(false), unweighed = $state(false), sort = $state('name'), showFilters = $state(false);
  let open = $state(null);

  // How the stock is shown; remembered per device.
  const VIEWS = [['cards', 'Kort', 'grid'], ['list', 'Liste', 'list'], ['table', 'Tabel', 'table'], ['group', 'Grupperet', 'group'], ['wall', 'Farvevæg', 'palette']];
  const GROUPS = [['material', 'Materiale'], ['vendor', 'Mærke'], ['location', 'Placering'], ['color', 'Farve']];
  const stored = (k, ok, d) => { try { const v = localStorage.getItem(k); return ok.includes(v) ? v : d; } catch { return d; } };
  let view = $state(stored('fh.inv.view', VIEWS.map((v) => v[0]), 'cards'));
  let groupBy = $state(stored('fh.inv.group', GROUPS.map((g) => g[0]), 'material'));
  $effect(() => { const v = view, g = groupBy; try { localStorage.setItem('fh.inv.view', v); localStorage.setItem('fh.inv.group', g); } catch { /* private mode */ } });

  function family(c) {
    const h = hex(c).slice(1);
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
    if (mx < 0.18) return 'Sort';
    if (d < 0.1) return l > 0.85 ? 'Hvid' : 'Grå';
    let hh = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    hh = (hh * 60 + 360) % 360;
    if (l < 0.38 && hh >= 15 && hh < 50) return 'Brun';
    return hh < 15 || hh >= 345 ? 'Rød' : hh < 40 ? 'Orange' : hh < 70 ? 'Gul' : hh < 165 ? 'Grøn' : hh < 200 ? 'Cyan' : hh < 260 ? 'Blå' : hh < 300 ? 'Lilla' : 'Pink';
  }
  const FAM_COL = { Sort: '#1a1a1a', Hvid: '#f4f4f4', Grå: '#8a8f99', Brun: '#7a4a2a', Rød: '#e5304a', Orange: '#ff8a1f', Gul: '#ffd21f', Grøn: '#2fbf5b', Cyan: '#25c9d8', Blå: '#2d6bff', Lilla: '#8a4dff', Pink: '#ff5fb0' };

  const all = $derived(app.spools || []);
  const live = $derived(all.filter((s) => !s.archived));
  const uniq = (f) => [...new Set(all.map(f).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'da'));
  const materials = $derived(uniq((s) => s.material));
  // One chip per category in stock ("PLA" = plain PLA, "PLA Silk" its own), sorted so a material's kinds follow it.
  const chips = $derived([...new Set(all.filter((s) => (archived ? s.archived : !s.archived)).map((s) => cat(s) || s.material))]
    .sort((a, b) => a.localeCompare(b, 'da')).map((v) => ({ v, sub: !materials.includes(v) })));
  const vendors = $derived(uniq((s) => s.vendor));
  const locations = $derived(uniq((s) => s.location));
  const fams = $derived([...new Set(live.map((s) => family(s.color)))]);
  const inSlot = $derived(Object.fromEntries((app.status?.slots || []).filter((s) => s.assignment).map((s) => [s.assignment.spool.id, s.label])));

  const byName = (a, b) => a.material.localeCompare(b.material) || a.vendor.localeCompare(b.vendor) || a.name.localeCompare(b.name);
  // Spools without the value (never dried, no price) go last; null = both have it, compare them.
  const last = (x, y) => (x == null ? (y == null ? 0 : 1) : y == null ? -1 : null);
  const shown = $derived.by(() => {
    const t = q.trim().toLowerCase();
    let l = all.filter((s) => (archived ? s.archived : !s.archived));
    if (t) l = l.filter((s) => `${s.vendor} ${s.material} ${cat(s)} ${s.name} #${s.id} ${s.location} ${s.comment}`.toLowerCase().includes(t));
    if (material) l = l.filter((s) => (cat(s) || s.material) === material);   // each spool in one chip: 'PLA' is plain PLA, 'PLA Silk' the silk
    if (vendor) l = l.filter((s) => s.vendor === vendor);
    if (location) l = l.filter((s) => s.location === location);
    if (colorFam) l = l.filter((s) => family(s.color) === colorFam);
    if (low) l = l.filter(isLow);
    if (noPrice) l = l.filter((s) => s.kg_price == null);
    if (unweighed) l = l.filter((s) => !s.weighed);
    const cmp = {
      name: byName,
      most: (a, b) => b.remaining - a.remaining,
      least: (a, b) => pctOf(a) - pctOf(b),
      newest: (a, b) => b.id - a.id,
      idAsc: (a, b) => a.id - b.id,
      used: (a, b) => String(b.last_used || '').localeCompare(String(a.last_used || '')),
      dried: (a, b) => String(b.dried || '').localeCompare(String(a.dried || '')),
      driedOld: (a, b) => last(a.dried, b.dried) ?? a.dried.localeCompare(b.dried),
      weighedOld: (a, b) => (a.weighed || 0) - (b.weighed || 0),
      priceHigh: (a, b) => last(a.kg_price, b.kg_price) ?? b.kg_price - a.kg_price,
      priceLow: (a, b) => last(a.kg_price, b.kg_price) ?? a.kg_price - b.kg_price,
    }[sort];
    return l.sort((a, b) => cmp(a, b) || byName(a, b));
  });
  const activeFilters = $derived([vendor, location, colorFam, low ? 'low' : '', noPrice ? 'price' : '', unweighed ? 'weight' : ''].filter(Boolean).length);
  const total = $derived(shown.filter((s) => !s.archived).reduce((a, s) => a + s.remaining, 0));
  const reset = () => { q = ''; material = ''; vendor = ''; location = ''; colorFam = ''; low = false; noPrice = false; unweighed = false; };
</script>

<div class="page stack" style="gap:16px">
  {#if app.spools === null}
    <div class="grid">{#each Array(6) as _}<div class="skeleton" style="height:250px"></div>{/each}</div>
  {:else if all.length === 0}
    <div class="empty glass rise">
      <div class="art"><span class="a"><Spool color="#3fd0b2" pct={0.8} size={110} tilt /></span><span class="b"><Spool color="#ff6a3d" pct={0.55} size={140} tilt /></span><span class="c"><Spool color="#e0a54a" pct={0.95} size={110} tilt /></span></div>
      <h1>Ingen ruller på lager</h1>
      <p class="dim">Tilføj din første rulle på få sekunder – søg i databasen, scan stregkoden eller opret selv.</p>
      <button class="btn primary big" onclick={() => go('/add')}><Icon name="plus" size={18} /> Tilføj første rulle</button>
    </div>
  {:else}
    <div class="row toolbar">
      <div class="search grow"><span class="ico"><Icon name="search" size={20} /></span>
        <input class="input" type="search" placeholder="Søg i {live.length} ruller …" bind:value={q}></div>
      <button class="btn icon-btn" class:on={showFilters || activeFilters} onclick={() => (showFilters = !showFilters)} aria-label="Filtre"><Icon name="sliders" size={20} />{#if activeFilters}<i class="cnt">{activeFilters}</i>{/if}</button>
      <button class="btn primary icon-btn" onclick={() => go('/add')} aria-label="Tilføj rulle"><Icon name="plus" size={22} /></button>
    </div>

    <div class="chips">
      <button class="chip" class:on={!material} onclick={() => (material = '')}>Alle</button>
      {#each chips as c}<button class="chip" class:sub={c.sub} class:on={material === c.v} onclick={() => (material = material === c.v ? '' : c.v)}>{c.v}</button>{/each}
    </div>

    {#if showFilters}
      <div class="glass pad stack filters rise">
        <div class="grid2">
          <label class="f">Producent<select class="input" bind:value={vendor}><option value="">Alle</option>{#each vendors as v}<option>{v}</option>{/each}</select></label>
          <label class="f">Placering<select class="input" bind:value={location}><option value="">Alle</option>{#each locations as v}<option>{v}</option>{/each}</select></label>
        </div>
        <div class="f">Farve
          <div class="row wrap" style="gap:8px">
            {#each fams as f}<button class="sw" class:on={colorFam === f} style="--c:{FAM_COL[f]}" title={f} aria-label={f} onclick={() => (colorFam = colorFam === f ? '' : f)}></button>{/each}
          </div></div>
        <div class="row wrap">
          <button class="chip" class:on={low} onclick={() => (low = !low)}>Lav beholdning</button>
          <button class="chip" class:on={noPrice} onclick={() => (noPrice = !noPrice)}>Pris mangler</button>
          <button class="chip" class:on={unweighed} onclick={() => (unweighed = !unweighed)}>Aldrig vejet</button>
          <button class="chip" class:on={archived} onclick={() => (archived = !archived)}>Arkiverede</button>
          <label class="f grow" style="min-width:150px"><select class="input" bind:value={sort} aria-label="Sortér">
            <option value="name">Sortér: Materiale / navn</option><option value="most">Mest tilbage</option><option value="least">Mindst tilbage</option><option value="idAsc">Rullenummer (laveste først)</option><option value="newest">Rullenummer (højeste først)</option><option value="used">Sidst brugt</option><option value="dried">Senest tørret</option><option value="driedOld">Længst siden tørret</option><option value="weighedOld">Længst siden vejet</option><option value="priceHigh">Dyreste (kr/kg)</option><option value="priceLow">Billigste (kr/kg)</option></select></label>
          <button class="btn small ghost" onclick={reset}>Nulstil</button>
        </div>
      </div>
    {/if}

    <div class="row between viewbar">
      <div class="small muted">{shown.length} {archived ? 'arkiverede ' : ''}ruller{#if !archived} · {fmtKg(total)} filament{/if}</div>
      <div class="row" style="gap:8px">
        {#if view === 'group'}
          <select class="input gsel" bind:value={groupBy} aria-label="Gruppér efter">{#each GROUPS as [k, t]}<option value={k}>{t}</option>{/each}</select>
        {/if}
        <div class="seg" role="radiogroup" aria-label="Visning">
          {#each VIEWS as [k, t, ic]}<button role="radio" aria-checked={view === k} class:on={view === k} title={t} aria-label={t} onclick={() => (view = k)}><Icon name={ic} size={18} /></button>{/each}
        </div>
      </div>
    </div>

    {#if shown.length}
      {#if view === 'cards'}
        <div class="grid">{#each shown as s, i (s.id)}<SpoolCard {s} index={i} slotLabel={inSlot[s.id] || ''} onclick={() => (open = s.id)} />{/each}</div>
      {:else}
        <InventoryViews {view} spools={shown} {inSlot} {groupBy} {family} onopen={(id) => (open = id)} />
      {/if}
    {:else}
      <div class="glass pad center stack"><p class="muted">Ingen ruller matcher.</p><button class="btn" onclick={reset}>Nulstil filtre</button></div>
    {/if}
  {/if}
</div>

{#if open}<SpoolSheet id={open} onclose={() => (open = null)} />{/if}

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 12px; }
  @media (min-width: 700px) { .grid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; } }
  .viewbar { flex-wrap: wrap; gap: 8px; }
  .seg { display: inline-flex; padding: 2px; gap: 2px; border-radius: 10px; background: var(--panel); border: 1px solid var(--panel-border); }
  .seg button { all: unset; cursor: pointer; display: grid; place-items: center; width: 32px; height: 28px; border-radius: 8px; color: var(--muted); transition: background-color .14s, color .14s; }
  .seg button:hover { color: var(--text); }
  .seg button.on { background: var(--panel-hi); color: var(--text); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); }
  .seg button:focus-visible { outline: 2px solid var(--accent); }
  .gsel { width: auto; padding: 4px 32px 4px 12px; font-size: 13px; min-height: 32px; height: auto; }
  .toolbar { position: sticky; top: calc(var(--safe-t) + 8px); z-index: 20; }
  .icon-btn { position: relative; flex: none; }
  .icon-btn.on { border-color: rgba(237,237,237,.22); color: var(--text); }
  .cnt { position: absolute; top: -6px; right: -6px; background: var(--accent); color: var(--accent-ink); font-size: 10px; font-weight: 600; border-radius: 8px; min-width: 16px; height: 16px; display: grid; place-items: center; font-style: normal; }
  .chips { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; scrollbar-width: none; margin: 0 -16px; padding: 0 16px 4px; }
  .chips::-webkit-scrollbar { display: none; }
  .chip.sub { font-size: 13px; color: var(--muted); }
  .sw { width: 28px; height: 28px; border-radius: 50%; border: 2px solid rgba(237,237,237,.16); background: var(--c); cursor: pointer; transition: border-color .14s, box-shadow .14s; }
  .sw:hover { border-color: rgba(237,237,237,.4); }
  .sw.on { border-color: var(--bg); box-shadow: 0 0 0 2px var(--text); }
  .empty { text-align: center; padding: 48px 24px; display: flex; flex-direction: column; align-items: center; gap: 16px; margin-top: 4vh; overflow: hidden; }
  .empty h1 { font-size: 28px; }
  .empty p { max-width: 380px; }
  .art { position: relative; height: 150px; width: 300px; max-width: 100%; margin-bottom: 8px; }
  .art span { position: absolute; }
  .art .a { left: 0; top: 30px; }
  .art .b { left: 80px; top: 0; z-index: 2; }
  .art .c { right: 0; top: 36px; }
</style>
