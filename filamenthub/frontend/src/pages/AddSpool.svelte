<script>
  import Spool from '../lib/ui/Spool.svelte';
  import Icon from '../lib/ui/Icon.svelte';
  import Scanner from '../lib/Scanner.svelte';
  import SpoolSheet from '../lib/SpoolSheet.svelte';
  import SlicerFields from '../lib/SlicerFields.svelte';
  import { app, route, go, toast, toastErr, reloadAll } from '../lib/stores.svelte.js';
  import { api, post, patch } from '../lib/api.js';
  import { debounce, hex, num, fmtG, today, autoKind, KIND_NAMES } from '../lib/util.js';
  import { onMount } from 'svelte';

  const slotKey = route.query.slot || '';
  // ?edit=<spool id>: the same form edits that spool's filament (in place), or moves it to one picked in the search.
  const editId = Number(route.query.edit) || null;
  let editFil = $state(null);   // the spool's own filament id while editing it in place
  let shared = $state(0), onlyThis = $state(false);   // other spools on the spool's filament; change just this one
  const slotLabel = $derived((app.status?.slots || []).find((s) => s.key === slotKey)?.label || slotKey);

  let step = $state('search');           // search | confirm | done
  let q = $state(''), loading = $state(false), results = $state(null), mine = $state([]), searchErr = $state('');
  let scanning = $state(false), lookupBusy = $state(false), notice = $state(''), existing = $state(null);
  let meta = $state({ vendors: [], materials: [] });

  // form
  let f = $state({});
  let item = $state(null), barcode = $state(''), saving = $state(false), created = $state([]);
  let showDetails = $state(false);

  // Slicer settings: the best profile for producer + name + material, until you pick or edit one yourself.
  let slCands = $state([]), slChoice = $state(null), slTouched = $state(false), slLoaded = $state(false), slKeep = false;
  const loadSl = debounce(async (vendor, name, material) => {
    try {
      slCands = (await api(`api/slicer/match?${new URLSearchParams({ vendor, name, material })}`)).candidates || [];
      slLoaded = true;
      if (!slTouched && !slKeep) slChoice = slCands[0] ? { profile: slCands[0].profile, settings: { ...slCands[0].settings } } : null;
    } catch { slCands = []; }
  }, 400);
  $effect(() => {
    const v = f.vendor || '', n = f.name || '', m = f.material || '';
    if (step === 'confirm' && m) loadSl(v, n, m);
  });

  // empty spool weight: from the database item, else guessed from manufacturer + size; the user can always override
  let tare = $state({ auto: true, hint: '' });
  let tareSeq = 0;
  async function guessTare() {
    const my = ++tareSeq;
    if (!tare.auto) return;
    if (item?.spool_weight != null && (f.vendor || '') === (item.manufacturer || item.vendor || '')) {
      f.spool_weight = item.spool_weight; tare.hint = item.source === 'mine' ? 'Fra dine egne filamenter' : 'Fra filamentdatabasen'; return;
    }
    if (!f.vendor?.trim()) { tare.hint = ''; return; }
    try {
      const r = await api(`api/catalog/spool-weight?${new URLSearchParams({ manufacturer: f.vendor.trim(), weight: String(num(f.weight) || 1000) })}`);
      if (my !== tareSeq || !tare.auto) return;
      const e = r?.estimate;
      if (e) { f.spool_weight = e.grams; tare.hint = `Anslået ud fra ${e.label}`; } else { f.spool_weight = ''; tare.hint = 'Ukendt – vej en tom rulle, eller lad feltet stå tomt'; }
    } catch { /* optional */ }
  }
  const tareInput = () => { tare.auto = false; tare.hint = 'Rettet manuelt'; };
  const tareReset = () => { tare.auto = true; guessTare(); };
  $effect(() => { if (step === 'confirm') { f.vendor; f.weight; guessTare(); } });

  function norm(r) {
    // Accept the flat list ({items}) and, defensively, grouped answers ({groups|products:[{..., items|colors|variants}]}).
    let items = [];
    const groups = r?.groups || r?.products;
    if (Array.isArray(groups)) {
      for (const g of groups) for (const it of g.items || g.colors || g.variants || [])
        items.push({ manufacturer: g.manufacturer || g.vendor, material: g.material, product: g.product || g.line || g.name, ...it });
    } else items = r?.items || [];
    return items;
  }
  function group(items) {
    const map = new Map();
    for (const it of items) {
      const man = it.manufacturer || it.vendor || '';
      const prod = it.product || it.line || it.series || '';
      const key = `${man}|${it.material}|${prod}|${it.weight || ''}`;
      if (!map.has(key)) map.set(key, { key, manufacturer: man, material: it.material, product: prod, weight: it.weight, mine: it.source === 'mine', items: [] });
      map.get(key).items.push(it);
    }
    return [...map.values()].sort((a, b) => (b.mine - a.mine) || (b.items.length - a.items.length));
  }
  const groups = $derived(results ? group(results.items) : []);

  let seq = 0;
  async function search(text) {
    const my = ++seq;
    if (!text.trim()) { results = null; loading = false; searchErr = ''; return; }
    loading = true; searchErr = '';
    try {
      const r = await api(`api/catalog/search?${new URLSearchParams({ q: text.trim(), limit: '300' })}`);
      if (my !== seq) return;
      results = { items: norm(r), total: r.total ?? 0 };
    } catch (e) { if (my === seq) searchErr = e.message; } finally { if (my === seq) loading = false; }
  }
  const later = debounce(search, 220);
  const onInput = () => { loading = !!q.trim(); later(q); };

  async function loadEdit() {
    try {
      const sp = await api(`api/v1/spool/${editId}`);
      const fil = sp.filament || {};
      const d = (app.spools || []).find((x) => x.id === editId)?.details
        || (await api('api/spools')).find((x) => x.id === editId)?.details || {};
      const multi = (fil.multi_color_hexes || '').split(',').filter(Boolean);
      startForm({
        manufacturer: fil.vendor?.name || '', material: fil.material || '', name: fil.name || '', color_hex: fil.color_hex || multi[0] || '',
        color_hexes: multi.length > 1 ? multi : [], weight: fil.weight || 1000, spool_weight: fil.spool_weight ?? '', density: fil.density ?? '',
        diameter: fil.diameter || 1.75, extruder_temp: fil.settings_extruder_temp ?? '', bed_temp: fil.settings_bed_temp ?? '',
        article_number: fil.article_number || '', ...d, properties: undefined,
      });
      f.properties = d.properties || '';
      item = null; editFil = fil.id; showDetails = true;
      try { shared = (await api('api/v1/spool?allow_archived=true')).filter((x) => x.id !== editId && x.filament?.id === fil.id).length; } catch { shared = 0; }
      try {
        const sl = await api(`api/spools/${editId}/slicer`);
        if (sl.current?.saved) { slKeep = true; slChoice = { profile: sl.current.profile, settings: { ...sl.current.settings } }; }
      } catch { /* optional */ }
    } catch (e) { toastErr(e); go('/inventory'); }
  }

  onMount(async () => {
    if (editId) loadEdit();
    try { mine = norm(await api('api/catalog/search?limit=300')).filter((i) => i.source === 'mine').slice(0, 12); } catch { /* optional */ }
    try { const m = await api('api/meta'); meta = { vendors: m.vendors || [], materials: m.materials || [] }; } catch { /* optional */ }
  });

  const SPOOL_TYPE = { cardboard: 'Pap', plastic: 'Plast', metal: 'Metal' };
  const nice = (a) => { const t = String(a).replace(/[_-]+/g, ' ').trim(); return t ? t[0].toUpperCase() + t.slice(1) : ''; };
  // Everything the databases say about the filament's character (finish is its own field).
  const props = (o) => [...new Set([...(o.attributes || []).map(nice), o.translucent ? 'Gennemsigtig' : '', o.glow ? 'Selvlysende' : ''].filter(Boolean))].join(', ');

  const mid = (r) => (Array.isArray(r) && r.length === 2 && r[0] != null ? Math.round((r[0] + r[1]) / 2 / 5) * 5 : '');
  const range = (r) => (Array.isArray(r) && r.length === 2 && r[0] != null ? (r[0] === r[1] ? `${r[0]}` : `${r[0]}–${r[1]}`) : '');

  function startForm(it, bc = '', note = '') {
    item = it;
    editFil = null;
    slTouched = false; slLoaded = false; slKeep = false;
    barcode = bc || '';
    notice = note;
    const o = it || {};
    const slot = (app.status?.slots || []).find((s) => s.key === slotKey);
    const col = (o.color_hex || (o.color_hexes || [])[0] || (!it && slot?.present ? slot.color : '') || '808080');
    f = {
      vendor: o.manufacturer || o.vendor || '', material: o.material || (!it && slot?.present ? slot.material : '') || '', name: o.name || '',
      color: hex(col).slice(1), weight: o.weight || 1000, spool_weight: o.spool_weight ?? '', density: o.density ?? '', diameter: o.diameter || 1.75,
      extruder_temp: o.extruder_temp ?? mid(o.extruder_temp_range), bed_temp: o.bed_temp ?? mid(o.bed_temp_range), price: '', remaining: '', location: '', count: 1, comment: '', dried: '',
      article_number: o.article_number || '', ean: o.ean || (/^\d{8,14}$/.test(bc || '') ? bc : ''), color_name: o.color_name || '',
      finish: o.finish || '', spool_type: SPOOL_TYPE[o.spool_type] || o.spool_type || '', properties: props(o),
      nozzle_range: o.nozzle_range || range(o.extruder_temp_range), bed_range: o.bed_range || range(o.bed_temp_range), td: o.td ?? '',
      category: o.category || '',
    };
    tare = { auto: true, hint: '' };
    showDetails = !it;
    step = 'confirm';
  }

  async function onScan(code) {
    scanning = false;
    lookupBusy = true;
    try {
      const r = await post('api/barcode/lookup', { code });
      if (r.kind === 'spool') { existing = r.spool.id; await reloadAll(); toast('Denne rulle findes allerede i lageret'); return; }
      if (r.kind === 'known') return startForm(r.item, r.code, `${r.source || 'Kendt'} – alt er udfyldt`);
      if (r.kind === 'match') return startForm(r.item, r.code, `Fundet: ${r.title || ''}${r.provider ? ` (${r.provider})` : ''}` + (r.confidence >= 0.8 ? '' : ' – tjek at farve og variant passer'));
      if (r.kind === 'title') {
        const p = r.parsed || {};
        const name = (p.color_words || []).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');
        return startForm({ manufacturer: p.vendor || '', material: p.material || '', name, weight: p.weight || 1000, diameter: p.diameter || 1.75 }, r.code,
          `Fundet: ${r.title} – tjek felterne. Stregkoden huskes til næste gang.`);
      }
      barcode = r.code || code;
      if (r.vendor_hint) q = r.vendor_hint;
      notice = r.message || 'Ukendt stregkode – vælg filamentet herunder, så huskes koden til næste gang.';
      if (q) search(q);
    } catch (e) { toastErr(e); } finally { lookupBusy = false; }
  }

  const preview = $derived({ color: f.color, colors: item?.color_hexes?.length > 1 && f.color === hex(item.color_hex || item.color_hexes[0]).slice(1) ? item.color_hexes : [] });
  const autoCat = $derived.by(() => { const k = autoKind({ name: f.name || '', details: { finish: f.finish, properties: f.properties } }); return k ? `${f.material} ${k}` : f.material || 'materiale'; });
  const total = $derived((num(f.price) ?? 0) * (f.count || 1));

  async function save() {
    const body = {
      vendor: (f.vendor || '').trim(), material: (f.material || '').trim(), name: (f.name || '').trim(),
      color_hex: String(f.color).replace('#', ''), weight: num(f.weight), density: num(f.density), diameter: num(f.diameter),
      extruder_temp: num(f.extruder_temp), bed_temp: num(f.bed_temp), spool_weight: num(f.spool_weight),
      remaining_weight: num(f.remaining), count: f.count || 1, price: num(f.price), comment: f.comment || null,
      slot: slotKey || null, barcode: barcode || null, dried: f.dried || null,
      article_number: f.article_number?.trim() || null, ean: f.ean?.trim() || null, color_name: f.color_name?.trim() || null,
      finish: f.finish?.trim() || null, spool_type: f.spool_type?.trim() || null,
      properties: (f.properties || '').split(',').map((x) => x.trim()).filter(Boolean),
      nozzle_range: String(f.nozzle_range || '').trim() || null, bed_range: String(f.bed_range || '').trim() || null, td: num(f.td),
      slicer: slTouched || (slLoaded && !editFil) ? (slChoice?.profile ? slChoice : {}) : undefined,
      category: (f.category || '').trim() || (editId ? '' : null),
    };
    if (!body.vendor || !body.material || !body.name) { showDetails = true; return toastErr(new Error('Udfyld producent, materiale og farve/navn')); }
    if (!(body.weight > 0)) { showDetails = true; return toastErr(new Error('Udfyld filamentets vægt')); }
    if (item) {
      const o = item;
      const same = body.vendor === (o.manufacturer || '') && body.material === (o.material || '') && body.name === (o.name || '')
        && body.color_hex.toUpperCase() === hex(o.color_hex || (o.color_hexes || [])[0]).slice(1) && Number(body.weight) === Number(o.weight || 1000);
      if (same && o.filament_id) body.filament_id = o.filament_id;
      if (same && o.external_id) body.external_id = o.external_id;
      if (same && o.color_hexes?.length > 1) { body.color_hexes = o.color_hexes; body.multi_color_direction = o.multi_color_direction; }
    }
    saving = true;
    if (editId) {
      for (const k of ['count', 'price', 'remaining_weight', 'comment', 'slot', 'barcode', 'dried', 'spool_weight']) delete body[k];
      if (editFil) body.filament_id = editFil;
      body.only_this = onlyThis;
      try {
        await post(`api/spools/${editId}/filament`, body);
        await reloadAll();
        toast(onlyThis && shared ? 'Rullen har fået sit eget filament' : editFil ? 'Filamentet er rettet' : 'Rullen har fået nyt filament');
        go('/inventory');
      } catch (e) { toastErr(e); } finally { saving = false; }
      return;
    }
    try {
      const res = await post('api/spools', body);
      created = Array.isArray(res) ? res : [];
      if (f.location?.trim()) {
        await Promise.all(created.map((s) => patch(`api/v1/spool/${s.id}`, { location: f.location.trim() }).catch(() => {})));
      }
      await reloadAll();
      if (slotKey) { toast(`${created.length} rulle oprettet og sat i ${slotLabel}`); go('/'); return; }
      step = 'done';
    } catch (e) { toastErr(e); } finally { saving = false; }
  }
  function again() { step = 'search'; q = ''; results = null; barcode = ''; notice = ''; created = []; }
  const bump = (d) => (f.count = Math.max(1, Math.min(20, (f.count || 1) + d)));
</script>

<div class="page add">
  {#if step === 'search'}
    <div class="stack" style="gap:16px">
      <div class="rise">
        {#if editId}
          <h1>Nyt filament til rulle #{editId}</h1>
          <p class="muted">Vælg det rigtige filament – rullens vægt og placering bliver som de er.</p>
          <button class="btn small" style="margin-top:8px" onclick={loadEdit}><Icon name="back" size={16} /> Behold nuværende og ret felterne</button>
        {:else}
          <h1>Tilføj rulle{slotKey ? ` til ${slotLabel}` : ''}</h1>
          <p class="muted">Søg efter filamentet – eller scan stregkoden på rullen.</p>
        {/if}
      </div>

      {#if notice}<div class="alert info rise"><Icon name="tag" size={18} /> <span>{notice}{#if barcode} <b class="mono">{barcode}</b>{/if}</span></div>{/if}

      <div class="row rise" style="--i:1">
        <div class="search grow big">
          <span class="ico"><Icon name="search" size={22} /></span>
          <input class="input" type="search" placeholder="fx “bambu pla matte black”" bind:value={q} oninput={onInput} autocomplete="off" autocapitalize="off" spellcheck="false">
        </div>
        <button class="btn primary scanbtn" onclick={() => (scanning = true)} aria-label="Scan stregkode" disabled={lookupBusy}>
          {#if lookupBusy}<span class="spinner"></span>{:else}<Icon name="scan" size={24} />{/if}<span class="lbl">Scan</span>
        </button>
      </div>

      {#if searchErr}<div class="alert err">{searchErr}</div>{/if}

      {#if results}
        {#if loading}<div class="tiny muted"><span class="spinner"></span> Søger …</div>{/if}
        {#each groups as g, gi (g.key)}
          <section class="glass pad grp rise" style="--i:{Math.min(gi, 6)}">
            <div class="row between wrap" style="gap:4px 10px">
              <div class="grow"><b>{g.manufacturer} {g.material}{g.product ? ` ${g.product}` : ''}</b>
                <span class="muted small"> · {g.weight ? fmtG(g.weight) : '?'}</span></div>
              {#if g.mine}<span class="badge accent">Dine</span>{/if}
            </div>
            <div class="colors">
              {#each g.items.slice(0, 80) as it}
                <button class="col" onclick={() => startForm(it, barcode)} title={it.name}>
                  <span class="dot" style="background:{it.color_hexes?.length > 1 ? `linear-gradient(90deg, ${it.color_hexes.map((c) => hex(c)).join(',')})` : hex(it.color_hex)}"></span>
                  <span class="nm ellipsis">{it.name}</span>
                </button>
              {/each}
              {#if g.items.length > 80}<span class="small muted">+{g.items.length - 80} flere – skriv mere for at indsnævre</span>{/if}
            </div>
          </section>
        {:else}
          {#if !loading}
            <div class="glass pad center stack rise"><p class="muted">Ingen filamenter matcher “{q}”.</p>
              <button class="btn" onclick={() => startForm(null, barcode)}>Opret manuelt</button></div>
          {/if}
        {/each}
      {:else if mine.length}
        <section class="rise" style="--i:2">
          <div class="eyebrow" style="margin-bottom:8px">Dine filamenter – tilføj en til</div>
          <div class="mine">
            {#each mine as it}
              <button class="glass minecard" onclick={() => startForm(it, barcode)}>
                <Spool color={it.color_hex || it.color_hexes?.[0]} colors={it.color_hexes} pct={1} size={48} ring={false} glow={false} />
                <span class="grow t"><b class="ellipsis">{it.name}</b><span class="muted tiny ellipsis">{it.manufacturer} {it.material}</span></span>
              </button>
            {/each}
          </div>
        </section>
      {:else}
        <div class="hint glass pad rise" style="--i:2">
          <Icon name="sparkle" size={22} />
          <div><b>Hurtigst: scan stregkoden</b><p class="muted small">Eller skriv producent, materiale og farve. Databasen dækker bl.a. Bambu Lab, Elegoo, Polymaker, eSun, Prusament m.fl.</p></div>
        </div>
      {/if}

      <button class="btn block rise" style="--i:3" onclick={() => startForm(null, barcode)}><Icon name="edit" size={18} /> Opret manuelt</button>
    </div>

  {:else if step === 'confirm'}
    <div class="stack" style="gap:16px">
      {#if editId}
        <div class="row between wrap">
          <h1 style="font-size:22px">Rediger rulle #{editId}</h1>
          <button class="btn small" onclick={() => (step = 'search')}><Icon name="search" size={16} /> Vælg andet filament</button>
        </div>
        {#if !editFil}<div class="alert info small">Rullen flyttes til dette filament, når du gemmer.</div>{/if}
        {#if shared}
          <div class="glass pad stack" style="gap:8px">
            <div class="small">Filamentet bruges også af {shared} {shared === 1 ? 'anden rulle' : 'andre ruller'}. Gem ændringerne for:</div>
            <label class="row small"><input type="radio" name="scope" value={true} bind:group={onlyThis}> Kun denne rulle (#{editId}) – den får sit eget filament</label>
            <label class="row small"><input type="radio" name="scope" value={false} bind:group={onlyThis}> Alle {shared + 1} ruller af filamentet</label>
          </div>
        {/if}
      {:else}
        <button class="btn ghost small back" onclick={() => (step = 'search')}><Icon name="back" size={18} /> Tilbage</button>
      {/if}
      <div class="glass hero rise" style="--c:#{f.color}">
        <Spool color={f.color} colors={preview.colors} pct={num(f.remaining) !== null && num(f.weight) ? Math.min(1, num(f.remaining) / num(f.weight)) : 1} size={120} tilt />
        <div class="grow" style="min-width:0">
          <div class="muted small ellipsis">{f.vendor || 'Producent'}</div>
          <h2 class="ellipsis">{f.name || 'Farve / navn'}</h2>
          <div class="row" style="gap:6px; margin-top:6px"><span class="badge accent">{f.material || 'Materiale'}</span><span class="badge">{fmtG(num(f.weight) || 0)}</span></div>
        </div>
      </div>
      {#if notice}<div class="alert info small">{notice}</div>{/if}
      <div class="glass pad rise" style="--i:1">
        <label class="f">Kategori
            <select class="input" bind:value={f.category}>
              <option value="">Automatisk ({autoCat})</option>
              <option value="-">{f.material} (almindelig)</option>
              {#each KIND_NAMES as k}<option value={k}>{f.material} {k}</option>{/each}
              {#if f.category && f.category !== '-' && !KIND_NAMES.includes(f.category)}<option value={f.category}>{f.material} {f.category}</option>{/if}
            </select></label>
      </div>

      {#if !editId}
      <div class="glass pad stack rise" style="--i:1">
        <div class="grid2">
          <label class="f">Antal ruller
            <div class="stepper"><button class="btn icon-btn" onclick={() => bump(-1)} aria-label="Færre">−</button><b class="mono">{f.count}</b><button class="btn icon-btn" onclick={() => bump(1)} aria-label="Flere">+</button></div></label>
          <label class="f">Pris pr. rulle (kr)<input class="input" type="number" inputmode="decimal" min="0" step="0.01" placeholder="valgfri" bind:value={f.price}></label>
        </div>
        <div class="grid2">
          <label class="f">Placering<input class="input" list="locs2" placeholder="fx Hylde 1" bind:value={f.location}></label>
          <datalist id="locs2">{#each [...new Set((app.spools || []).map((s) => s.location).filter(Boolean))] as l}<option value={l}></option>{/each}</datalist>
          <label class="f">Tilbage nu (g)<input class="input" type="number" inputmode="decimal" min="0" placeholder="fuld rulle" bind:value={f.remaining}></label>
        </div>
        <label class="f">Tom rulle vejer (g)<input class="input" type="number" inputmode="decimal" min="0" placeholder="ukendt" bind:value={f.spool_weight} oninput={tareInput}></label>
        <p class="tiny muted tarehint">{tare.hint}{#if !tare.auto} · <button class="linkbtn" onclick={tareReset}>Brug automatisk</button>{:else if tare.hint}{' – ret det, hvis du har respoolet'}{/if}</p>
        <div class="f">Tørret <span class="muted small">(valgfri)</span>
          <div class="row"><input class="input grow" type="date" max={today()} bind:value={f.dried} aria-label="Tørret dato">
            <button class="btn" type="button" onclick={() => (f.dried = today())}>I dag</button></div></div>
      </div>
      {/if}

      <details class="glass pad rise" style="--i:2" bind:open={showDetails}>
        <summary>Filament-detaljer <span class="muted small">{item ? '(udfyldt – ret hvis nødvendigt)' : ''}</span></summary>
        <div class="stack" style="margin-top:12px">
          <div class="grid2">
            <label class="f">Producent<input class="input" list="vend" bind:value={f.vendor}></label>
            <label class="f">Materiale<input class="input" list="mats" bind:value={f.material}></label>
          </div>
          <datalist id="vend">{#each meta.vendors as v}<option value={v}></option>{/each}</datalist>
          <datalist id="mats">{#each meta.materials as v}<option value={v}></option>{/each}</datalist>
          <label class="f">Farve / navn<input class="input" placeholder="fx Matte Black" bind:value={f.name}></label>
          <div class="f">Farvekode
            <div class="row"><input type="color" class="input colorin" value={'#' + f.color} oninput={(e) => (f.color = e.target.value.slice(1).toUpperCase())}>
              <input class="input mono" maxlength="7" value={f.color} onchange={(e) => { const h = e.target.value.replace('#', ''); if (/^[0-9a-fA-F]{6}$/.test(h)) f.color = h.toUpperCase(); else e.target.value = f.color; }}></div></div>
          <div class="grid2">
            <label class="f">Filament ny (g)<input class="input" type="number" min="1" bind:value={f.weight}></label>
            <label class="f">Densitet (g/cm³)<input class="input" type="number" step="0.01" placeholder="auto" bind:value={f.density}></label>
            <label class="f">Diameter (mm)<input class="input" type="number" step="0.01" bind:value={f.diameter}></label>
            <label class="f">Farvenavn<input class="input" placeholder="valgfri" bind:value={f.color_name}></label>
            <label class="f">Finish<input class="input" placeholder="fx Matte, Silk" bind:value={f.finish}></label>
            <label class="f">Varenummer<input class="input mono" placeholder="valgfri" bind:value={f.article_number}></label>
            <label class="f">EAN-stregkode<input class="input mono" inputmode="numeric" placeholder="valgfri" bind:value={f.ean}></label>
            <label class="f">Rulletype<input class="input" placeholder="fx Pap, Plast" bind:value={f.spool_type}></label>
            <label class="f">Egenskaber<input class="input" placeholder="fx High flow" bind:value={f.properties}></label>
            <label class="f">Dyse °C anbefalet<input class="input mono" placeholder="fx 230–250" bind:value={f.nozzle_range}></label>
            <label class="f">Plade °C anbefalet<input class="input mono" placeholder="fx 70–80" bind:value={f.bed_range}></label>
            <label class="f">TD-værdi (HueForge)<input class="input mono" type="number" step="0.1" placeholder="valgfri" bind:value={f.td}></label>
            <div class="f">Dyse / plade °C<div class="row"><input class="input" type="number" placeholder="dyse" bind:value={f.extruder_temp}><input class="input" type="number" placeholder="plade" bind:value={f.bed_temp}></div></div>
          </div>
          {#if !editId}<label class="f">Kommentar<input class="input" maxlength="500" bind:value={f.comment}></label>{/if}
        </div>
      </details>
      <details class="glass pad rise" style="--i:2">
        <summary>Printerindstillinger <span class="muted small">{slChoice ? `(SuperTack ${slChoice.settings.supertack_plate_temp ?? "–"} °C · Textured PEI ${slChoice.settings.textured_plate_temp ?? "–"} °C)` : "(ingen profil)"}</span></summary>
        <div style="margin-top:12px" oninput={() => (slTouched = true)} onchange={() => (slTouched = true)}>
          <SlicerFields candidates={slCands} bind:choice={slChoice} />
        </div>
        <p class="tiny muted" style="margin-top:8px">Gemmes på filamentet. Fra rullen kan du hente profilen til Bambu Studio eller sende den til Bambu Cloud.</p>
      </details>
      {#if barcode}<p class="tiny muted center">Stregkode {barcode} huskes, så næste scanning udfylder alt med det samme.</p>{/if}
      <button class="btn primary big block save rise" style="--i:3" onclick={save} disabled={saving}>
        {#if saving}<span class="spinner"></span> Gemmer …{:else}<Icon name="check" size={22} /> {#if editId}Gem ændringer{:else}Gem {f.count > 1 ? `${f.count} ruller` : 'rulle'}{/if}{#if total > 0}<span class="tot">· {total.toLocaleString('da-DK')} kr</span>{/if}{/if}
      </button>
    </div>

  {:else}
    <div class="done center stack" style="align-items:center; gap:18px; padding-top:6vh">
      <div class="burst">
        {#each created.slice(0, 3) as s, i}<span style="--k:{i}"><Spool color={s.color} colors={s.colors} pct={s.initial ? s.remaining / s.initial : 1} size={120} tilt /></span>{/each}
      </div>
      <h1 class="rise">{created.length > 1 ? `${created.length} ruller tilføjet` : 'Rullen er tilføjet'}</h1>
      <p class="muted rise" style="--i:1">{created[0]?.vendor} {created[0]?.material} · {created[0]?.name}</p>
      <div class="stack rise" style="--i:2; width:100%; max-width:360px">
        <button class="btn primary big block" onclick={again}><Icon name="plus" size={20} /> Tilføj en til</button>
        <button class="btn block" onclick={() => go('/inventory')}>Se lageret</button>
      </div>
    </div>
  {/if}
</div>

{#if scanning}<Scanner onresult={onScan} onclose={() => (scanning = false)} />{/if}
{#if existing}<SpoolSheet id={existing} onclose={() => { existing = null; }} />{/if}

<style>
  .add { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 26px; }
  .search.big .input { min-height: 56px; font-size: 17px; border-radius: 18px; padding-left: 50px; }
  .search.big .ico { left: 17px; }
  .scanbtn { min-height: 56px; border-radius: 18px; padding: 0 18px; flex: none; }
  .grp { display: flex; flex-direction: column; gap: 12px; }
  .colors { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
  .col { display: flex; align-items: center; gap: 9px; padding: 7px 10px; border-radius: 12px; background: rgba(255,255,255,.04); border: 1px solid transparent; cursor: pointer; text-align: left; font-size: 13.5px; font-weight: 500;
    transition: all .2s var(--ease); }
  .col:hover { background: rgba(255,255,255,.1); border-color: var(--panel-border-hi); transform: translateY(-1px); }
  .col:active { transform: scale(.96); }
  .dot { width: 22px; height: 22px; border-radius: 50%; flex: none; border: 1.5px solid rgba(255,255,255,.3); box-shadow: 0 0 10px -2px currentColor, inset 0 -3px 6px rgba(0,0,0,.25); }
  .nm { min-width: 0; }
  .mine { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px; }
  .minecard { display: flex; align-items: center; gap: 10px; padding: 8px 12px 8px 8px; text-align: left; cursor: pointer; transition: transform .2s var(--ease), border-color .2s; }
  .minecard:hover { transform: translateY(-2px); border-color: var(--panel-border-hi); }
  .minecard .t { display: flex; flex-direction: column; min-width: 0; }
  .hint { display: flex; gap: 14px; align-items: flex-start; color: var(--accent); }
  .hint b { color: var(--text); }
  .back { align-self: flex-start; margin-left: -10px; }
  .hero { display: flex; gap: 18px; align-items: center; padding: 18px; position: relative; overflow: hidden; }
  .hero::before { content: ""; position: absolute; left: -30px; top: -40px; width: 260px; height: 260px; background: radial-gradient(circle, color-mix(in srgb, var(--c) 32%, transparent), transparent 70%); pointer-events: none; }
  .hero h2 { font-size: 22px; }
  .stepper { display: flex; align-items: center; justify-content: space-between; background: rgba(8,11,19,.6); border: 1px solid var(--panel-border-hi); border-radius: 13px; padding: 3px; min-height: 46px; }
  .stepper b { font-size: 18px; }
  .stepper .btn { width: 40px; height: 38px; min-height: 0; font-size: 20px; }
  .colorin { width: 60px; flex: none; }
  summary { cursor: pointer; font-weight: 600; }
  .save { position: sticky; bottom: calc(var(--nav-h) + var(--safe-b) + 40px); z-index: 10; }   /* clear of the round + that sticks up from the nav */
  .tot { opacity: .75; font-weight: 500; }
  .burst { display: flex; justify-content: center; }
  .burst span { margin: 0 -18px; animation: drop .8s var(--spring) both; animation-delay: calc(var(--k) * 120ms); }
  @keyframes drop { from { opacity: 0; transform: translateY(-60px) scale(.6) rotate(-30deg); } to { opacity: 1; transform: none; } }
  @media (min-width: 900px) { .save { bottom: 20px; } .scanbtn .lbl { display: inline; } }
  @media (max-width: 480px) { .scanbtn .lbl { display: none; } }
  .tarehint { margin: -6px 0 0; }
  .linkbtn { background: none; border: 0; padding: 0; color: var(--accent); font: inherit; cursor: pointer; text-decoration: underline; }
</style>
