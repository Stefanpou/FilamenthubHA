<!-- Front-view illustration for single-nozzle Bambu Lab printers, same props/geometry as PrinterSvg (the X2D/H2D drawing):
       family "x1p1"  enclosed cube with glass door (X1C, X1E, P1S, P1P, P2S)
       family "a1"    open bedslinger with a C-shaped frame (A1, A1 mini)
     The AMS (AMS / AMS lite) sits on top when the printer has one; without it the external spool is drawn on top of
     the printer. With an AMS the external spool holder is drawn on the right side, like the X2D. Pure SVG in two
     layers (back / front) with the 3D canvas in between (children). Text lives in HTML (PrinterView) except logos. -->
<script>
  import { onMount } from 'svelte';
  import { hex, luminance } from './format.js';

  let { printer = null, slots = [], tone = 'idle', progress = 0, still = false, children, family = 'x1p1', amsModel = '' } = $props();

  const VX = 70, VY = 10, VW = 690, VH = 760;
  const BAYS = [188, 279, 371, 462];
  const AMS = { sy: 170, rmax: 118, w: 64 };
  const EXT = { x: 590, y: 520, r: 82, w: 58 };       // external holder on the side (printer with AMS)
  const TOP = { x: 325, y: 150, r: 100, w: 58 };      // external spool on top of the printer (no AMS)
  const CH = { x0: 154, x1: 495, y0: 330, y1: 734 };
  const PLATE = { x0: 160, x1: 490, y0: 404, y1: 598 };
  const RAIL_Y = 373;
  const HEAD_IDLE = 325;

  const a1 = $derived(family === 'a1');
  const running = $derived(printer?.gcode_state === 'RUNNING');
  const online = $derived(!!printer?.online);
  const nozzles = $derived(printer?.nozzles || []);
  const lightOn = $derived(printer?.light !== false);
  const speedLvl = $derived(printer?.speed_level || 2);
  const model = $derived(printer?.model && printer.model !== 'Bambu Lab printer' ? printer.model : '');

  const amsUnit = $derived(slots.find((x) => x.kind === 'ams')?.unit);
  const amsSlots = $derived(amsUnit == null ? [] : slots.filter((s) => s.kind === 'ams' && s.unit === amsUnit));
  const hasAms = $derived(amsSlots.length > 0);
  const bays = $derived([0, 1, 2, 3].map((i) => amsSlots.find((s) => s.tray === i) || null));
  const ext = $derived(slots.find((s) => s.kind === 'ext') || null);
  const active = $derived(slots.find((s) => s.active) || null);
  const amsLabel = $derived(amsModel || 'AMS');

  let hx = $state(HEAD_IDLE);
  let amp = 0, phase = 0, raf = 0, lastT = 0, visible = true, root;

  function tick(t) {
    raf = 0;
    if (!visible || document.hidden) return;
    const dt = lastT ? Math.min(0.08, (t - lastT) / 1000) : 0.016;
    lastT = t;
    amp += ((running ? 110 : 0) - amp) * Math.min(1, dt * 2);
    phase += dt * (0.7 + speedLvl * 0.55);
    const x = running || amp > 1 ? HEAD_IDLE + Math.sin(phase) * amp : HEAD_IDLE;
    const nx = hx + (x - hx) * Math.min(1, dt * (running ? 14 : 3));
    if (Math.abs(nx - hx) > 0.15) hx = nx;
    const settled = !running && amp < 1 && Math.abs(hx - HEAD_IDLE) < 0.5;
    if (!settled) raf = requestAnimationFrame(tick);
  }
  function wake() { if (!raf && !still) { lastT = 0; raf = requestAnimationFrame(tick); } }

  onMount(() => {
    const io = new IntersectionObserver((e) => { visible = e[0]?.isIntersecting ?? true; if (visible) wake(); });
    io.observe(root);
    const vis = () => { if (!document.hidden) wake(); };
    document.addEventListener('visibilitychange', vis);
    return () => { io.disconnect(); document.removeEventListener('visibilitychange', vis); if (raf) cancelAnimationFrame(raf); };
  });
  $effect(() => {
    running; speedLvl; still;
    if (still) hx = HEAD_IDLE; else wake();
  });

  const heat = (t) => (t == null || t < 60 ? null : t < 150 ? '#ffb547' : t < 230 ? '#ff7a3d' : '#ff4d3d');
  const spoolColor = (s) => '#' + hex(s?.color);
  const filR = (s, R) => {
    const hub = R * 0.4;
    return s?.remaining_pct == null ? R - 4 : hub + (R - 4 - hub) * Math.sqrt(Math.max(0.03, Math.min(1, s.remaining_pct / 100)));
  };
  const bands = (s, w) => {
    const cs = s?.colors?.length > 1 ? s.colors : [s?.color];
    return cs.map((c, i) => ({ x: (i * w) / cs.length, w: w / cs.length + 0.3, c: '#' + hex(c) }));
  };
  const nozzleHeat = $derived(heat(nozzles[0]?.temp));
  const tip = $derived(active ? spoolColor(active) : null);

  // feed tube of the active slot -> the single nozzle
  const HEAD_TOP = RAIL_Y - 32;
  const feed = $derived.by(() => {
    if (!active || !online) return null;
    const ex = hx, ey = HEAD_TOP + 4;
    let d;
    if (active.kind === 'ext') {
      d = hasAms
        ? `M${EXT.x - EXT.w / 2 - 4} ${EXT.y - 30} C 520 ${EXT.y - 120}, ${ex + 60} ${ey - 40}, ${ex} ${ey}`
        : `M${TOP.x} ${TOP.y + 40} C ${TOP.x} 260, ${ex} ${ey - 80}, ${ex} ${ey}`;
    } else {
      const bx = BAYS[active.tray] ?? 325;
      const y0 = a1 ? 196 : 238;
      d = `M${bx} ${y0} C ${bx} ${y0 + 62}, ${ex} ${ey - 50}, ${ex} ${ey}`;
    }
    return { d, color: spoolColor(active) };
  });

  const LED = { idle: '#38e8c6', run: '#38e8c6', warn: '#ffb547', ok: '#3ddc84', bad: '#ff5c7a', prep: '#7c5cff' };
  const ledColor = $derived(LED[tone] || '#38e8c6');
  const pct = (v, o, n) => ((v - o) / n) * 100;
</script>

{#snippet rim(s, cx, cy, R, w, act)}
  {@const r = filR(s, R)}
  <g class="spool" class:act style="--glow: {spoolColor(s)}">
    <rect x={cx - w / 2 - 5} y={cy - R} width="5" height={R * 2} rx="2.5" fill="#9aa3b5" opacity="0.35"/>
    <rect x={cx + w / 2} y={cy - R} width="5" height={R * 2} rx="2.5" fill="#9aa3b5" opacity="0.35"/>
    <rect x={cx - w / 2} y={cy - R * 0.4} width={w} height={R * 0.8} fill="#1a1e27"/>
    <g transform="translate({cx - w / 2} {cy - r})">
      {#each bands(s, w) as b}<rect x={b.x} y="0" width={b.w} height={r * 2} fill={b.c}/>{/each}
      <rect width={w} height={r * 2} fill="url(#pg-wind)"/>
      <rect width={w} height={r * 2} fill="url(#pg-curve)"/>
      {#if luminance(s.color) < 0.08}<rect width={w} height={r * 2} fill="none" stroke="#ffffff26"/>{/if}
    </g>
  </g>
{/snippet}

<div class="stage" bind:this={root} style="--led: {ledColor}">
  <svg class="layer back" viewBox="{VX} {VY} {VW} {VH}" aria-hidden="true">
    <defs>
      <linearGradient id="pg-body" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#2a2e36"/><stop offset="0.06" stop-color="#3a3f49"/><stop offset="0.5" stop-color="#30343c"/>
        <stop offset="0.94" stop-color="#383d47"/><stop offset="1" stop-color="#24272e"/></linearGradient>
      <linearGradient id="pg-amsfront" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2b2f38"/><stop offset="0.12" stop-color="#1b1e25"/><stop offset="1" stop-color="#121419"/></linearGradient>
      <linearGradient id="pg-lid" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe8f5" stop-opacity="0.14"/><stop offset="0.4" stop-color="#dfe8f5" stop-opacity="0.04"/><stop offset="1" stop-color="#dfe8f5" stop-opacity="0.07"/></linearGradient>
      <linearGradient id="pg-feeder" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2a2e36"/><stop offset="0.45" stop-color="#4a4f5a"/><stop offset="1" stop-color="#23262d"/></linearGradient>
      <linearGradient id="pg-curve" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0.5"/><stop offset="0.3" stop-color="#fff" stop-opacity="0.12"/><stop offset="0.5" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.55"/></linearGradient>
      <linearGradient id="pg-chamber" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d1016"/><stop offset="0.6" stop-color="#11141b"/><stop offset="1" stop-color="#090b10"/></linearGradient>
      <linearGradient id="pg-metal" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#23262d"/><stop offset="0.35" stop-color="#454a55"/><stop offset="1" stop-color="#1f2228"/></linearGradient>
      <radialGradient id="pg-light" cx="0.5" cy="0" r="0.9"><stop offset="0" stop-color="#e9f3ff" stop-opacity="0.3"/><stop offset="1" stop-color="#e9f3ff" stop-opacity="0"/></radialGradient>
      <pattern id="pg-wind" width="3.2" height="12" patternUnits="userSpaceOnUse"><rect width="1.1" height="12" fill="#000" opacity="0.22"/></pattern>
      <clipPath id="pg-amsclip"><rect x="138" y="26" width="374" height="102" rx="14"/></clipPath>
      <clipPath id="pg-clip"><rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height={CH.y1 - CH.y0} rx="4"/></clipPath>
    </defs>

    <ellipse cx="320" cy="758" rx="250" ry="8" fill="#000" opacity="0.55"/>

    <!-- ===== AMS / AMS lite on top ===== -->
    {#if hasAms}
      {#if !a1}
        <rect x="134" y="22" width="382" height="108" rx="16" fill="#0b0d12" stroke="#4a5262" stroke-width="1.5"/>
      {/if}
      <g clip-path={a1 ? undefined : 'url(#pg-amsclip)'}>
        {#each BAYS as bx, i (bx)}
          {@const s = bays[i]}
          {#if s && s.present}
            {@render rim(s, bx, AMS.sy, AMS.rmax, AMS.w, s.active)}
          {:else}
            <rect x={bx - AMS.w / 2} y={AMS.sy - AMS.rmax + 6} width={AMS.w} height={AMS.rmax} rx="4" fill="none" stroke="#3a4356" stroke-dasharray="3 6" opacity="0.7"/>
          {/if}
        {/each}
        {#if !a1}{#each [233, 325, 417] as dx}<rect x={dx - 3} y="30" width="6" height="100" fill="#2b313d" opacity="0.85"/>{/each}{/if}
      </g>
      {#if !a1}
        <rect x="134" y="22" width="382" height="108" rx="16" fill="url(#pg-lid)"/>
        <path d="M152 40 Q162 30 200 30 H450" fill="none" stroke="#fff" stroke-opacity="0.22" stroke-width="2" stroke-linecap="round"/>
      {/if}

      <!-- AMS front with the four feeders -->
      {#if a1}
        <rect x="126" y="126" width="398" height="78" rx="9" fill="url(#pg-amsfront)" stroke="#3a404c"/>
        {#each BAYS as bx, i (bx)}
          {@const s = bays[i]}
          {@const wc = s && s.present ? spoolColor(s) : '#2a2f3a'}
          <g style="--wc: {wc}" class="feeder" class:act={s?.active}>
            <rect x={bx - 21} y="136" width="42" height="60" rx="9" fill="#0f1116" stroke="#2c313b"/>
            <rect x={bx - 13} y="142" width="26" height="24" rx="7" fill={wc} opacity={s && s.present ? (s.active ? 0.95 : 0.7) : 0.25}/>
            <rect x={bx - 13} y="142" width="26" height="24" rx="7" fill="url(#pg-curve)"/>
            <rect x={bx - 15} y="171" width="30" height="20" rx="7" fill="url(#pg-feeder)"/>
          </g>
        {/each}
        <text x="140" y="196" font-size="9" font-weight="600" fill="#9aa3b5" letter-spacing="1">{amsLabel}</text>
      {:else}
        <rect x="126" y="126" width="398" height="120" rx="8" fill="url(#pg-amsfront)" stroke="#3a404c"/>
        <rect x="126" y="126" width="398" height="8" rx="4" fill="#3b414d"/>
        {#each BAYS as bx, i (bx)}
          {@const s = bays[i]}
          {@const wc = s && s.present ? spoolColor(s) : '#2a2f3a'}
          <g style="--wc: {wc}" class="feeder" class:act={s?.active}>
            <rect x={bx - 23} y="156" width="46" height="86" rx="10" fill="#0f1116" stroke="#2c313b"/>
            <rect x={bx - 16} y="161" width="32" height="34" rx="8" fill="#06070a"/>
            <rect x={bx - 14} y="163" width="28" height="30" rx="7" fill={wc} opacity={s && s.present ? (s.active ? 0.95 : 0.7) : 0.25}/>
            <rect x={bx - 14} y="163" width="28" height="30" rx="7" fill="url(#pg-curve)"/>
            <rect x={bx - 18} y="198" width="36" height="40" rx="9" fill="url(#pg-feeder)"/>
          </g>
        {/each}
        <text x="404" y="150" font-size="13" font-weight="600" letter-spacing="2" fill="#dfe3ea" class="logo">{amsLabel}</text>
      {/if}
    {:else}
      <!-- no AMS: the external spool sits on the holder on top of the printer -->
      <g class="extholder">
        <rect x="248" y={TOP.y + TOP.r - 4} width="154" height="10" rx="4" fill="#3a3f49" stroke="#4d535f"/>
        <rect x="244" y={TOP.y + 20} width="8" height={TOP.r - 16} rx="3" fill="#2b2f37"/>
        <rect x="398" y={TOP.y + 20} width="8" height={TOP.r - 16} rx="3" fill="#2b2f37"/>
        {#if ext && ext.present}
          {@render rim(ext, TOP.x, TOP.y, TOP.r, TOP.w, ext.active)}
        {:else}
          <rect x={TOP.x - TOP.w / 2} y={TOP.y - TOP.r} width={TOP.w} height={TOP.r * 2} rx="6" fill="none" stroke="#3a4356" stroke-dasharray="3 6" opacity="0.7"/>
        {/if}
      </g>
    {/if}

    {#if a1}
      <!-- ===== A1: open frame - C-shaped tower, bed slab and base ===== -->
      <rect x="124" y={hasAms ? 200 : 170} width="46" height={754 - (hasAms ? 200 : 170)} rx="8" fill="url(#pg-body)" stroke="#474c57"/>
      <rect x="132" y={hasAms ? 214 : 184} width="6" height={660 - (hasAms ? 214 : 184)} rx="3" fill="#1a1d23"/>
      <rect x="110" y="640" width="420" height="114" rx="12" fill="url(#pg-body)" stroke="#474c57"/>
      <rect x="110" y="640" width="420" height="4" rx="2" fill="#5a606c" opacity="0.6"/>
      <!-- heat bed -->
      <rect x="150" y="612" width="340" height="26" rx="4" fill="url(#pg-metal)" stroke="#474c57"/>
      <rect x="160" y="596" width="320" height="16" rx="3" fill="#1c1f26" stroke="#2c313b"/>
      <rect x="160" y="596" width="320" height="2" fill="#5a4a2c" opacity="0.7"/>
      <!-- little screen -->
      <rect x="400" y="662" width="102" height="60" rx="8" fill="#0a0b0e" stroke="#5a606c" stroke-width="1.5"/>
      <rect x="406" y="668" width="90" height="48" rx="5" fill="#11151c"/>
      <rect x="414" y="676" width="30" height="4" rx="2" fill="var(--led)" opacity={online ? 0.9 : 0.2}/>
      <rect x="414" y="690" width={Math.max(0, 72 * Math.min(1, progress))} height="3" rx="1.5" fill="var(--led)"/>
      <rect x="414" y="690" width="72" height="3" rx="1.5" fill="#232a35" opacity="0.6"/>
      <circle cx="486" cy="676" r="2.2" fill="var(--led)" class="dot" opacity={online ? 1 : 0.2}/>
      <g class="logo" fill="#e4e7ec">
        <text x="140" y="676" font-size="5.5" font-weight="700">Bambu</text>
        <text x="140" y="682" font-size="5.5" font-weight="700">Lab</text>
        <text x="140" y="712" font-size="22" font-weight="700" font-style="italic" letter-spacing="1">{model || 'A1'}</text>
      </g>
      <rect x="128" y="752" width="44" height="6" rx="2" fill="#0d0f13"/><rect x="468" y="752" width="44" height="6" rx="2" fill="#0d0f13"/>
      <!-- work light on the arm -->
      <rect x="190" y={RAIL_Y + 12} width="270" height="3" rx="1.5" fill="#e9f3ff" opacity={lightOn ? 0.22 : 0.04}/>
    {:else}
      <!-- ===== X1 / P1 / P2: enclosed cube ===== -->
      <rect x="110" y="244" width="420" height="510" rx="12" fill="url(#pg-body)" stroke="#474c57"/>
      <rect x="110" y="244" width="420" height="4" rx="2" fill="#5a606c" opacity="0.6"/>
      <g class="logo" fill="#e4e7ec">
        <text x="136" y="279" font-size="5.5" font-weight="700">Bambu</text>
        <text x="136" y="285" font-size="5.5" font-weight="700">Lab</text>
        <text x="156" y="287" font-size="20" font-weight="700" font-style="italic" letter-spacing="1">{model || 'P1S'}</text>
      </g>

      <rect x="146" y="322" width="358" height="420" rx="5" fill="#07080b" stroke="#50566a" stroke-width="2"/>
      <rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height={CH.y1 - CH.y0} rx="4" fill="url(#pg-chamber)"/>
      <g clip-path="url(#pg-clip)">
        <rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height="200" fill="url(#pg-light)" opacity={lightOn ? 1 : 0.12} style="transition: opacity .6s"/>
        <!-- Z lead screws left/right and the heat bed -->
        <rect x="164" y="360" width="6" height="320" fill="#151920"/><rect x="480" y="360" width="6" height="320" fill="#151920"/>
        <rect x="173" y="596" width="292" height="12" rx="2" fill="#1c1f26" stroke="#2c313b"/>
        <rect x="173" y="596" width="292" height="2" fill="#5a4a2c" opacity="0.7"/>
        <rect x="182" y="608" width="274" height="14" fill="#0b0d11"/>
        <rect x={CH.x0} y="690" width={CH.x1 - CH.x0} height="44" fill="#08090c"/>
      </g>
      {#each [[166, 336], [186, 336], [166, 726], [186, 726]] as [x, y]}<circle cx={x} cy={y} r="4.5" fill="#15181e" stroke="#2c313b"/>{/each}

      <!-- small front screen + status light strip -->
      <rect x="380" y="256" width="120" height="50" rx="9" fill="#0a0b0e" stroke="#5a606c" stroke-width="1.5"/>
      <rect x="386" y="262" width="108" height="38" rx="5" fill="#11151c"/>
      <rect x="394" y="270" width="34" height="4" rx="2" fill="var(--led)" opacity={online ? 0.9 : 0.2}/>
      <rect x="394" y="288" width="92" height="3" rx="1.5" fill="#232a35"/>
      <rect x="394" y="288" width={Math.max(0, 92 * Math.min(1, progress))} height="3" rx="1.5" fill="var(--led)"/>
      <circle cx="486" cy="270" r="2.2" fill="var(--led)" class="dot" opacity={online ? 1 : 0.2}/>
      <rect x="200" y="314" width="250" height="3" rx="1.5" fill="var(--led)" class="led" class:pulse={running || tone === 'prep'} class:blink={tone === 'bad'} opacity={online ? 0.85 : 0.12}/>
      <rect x="128" y="752" width="44" height="6" rx="2" fill="#0d0f13"/><rect x="468" y="752" width="44" height="6" rx="2" fill="#0d0f13"/>
    {/if}

    <!-- ===== external spool holder on the side (printers with an AMS) ===== -->
    {#if hasAms}
      <g class="extholder">
        <rect x="530" y={EXT.y - 6} width="34" height="12" rx="4" fill="#3a3f49" stroke="#4d535f"/>
        <rect x="526" y={EXT.y - 22} width="8" height="44" rx="3" fill="#2b2f37"/>
        {#if ext && ext.present}
          {@render rim(ext, EXT.x, EXT.y, EXT.r, EXT.w, ext.active)}
        {:else}
          <rect x={EXT.x - EXT.w / 2} y={EXT.y - EXT.r} width={EXT.w} height={EXT.r * 2} rx="6" fill="none" stroke="#3a4356" stroke-dasharray="3 6" opacity="0.7"/>
        {/if}
      </g>
    {/if}
  </svg>

  <!-- the 3D model sits on the plate -->
  <div class="plate" style="left: {pct(PLATE.x0, VX, VW)}%; top: {pct(PLATE.y0, VY, VH)}%; width: {((PLATE.x1 - PLATE.x0) / VW) * 100}%; height: {((PLATE.y1 - PLATE.y0) / VH) * 100}%">
    {@render children?.()}
  </div>

  <svg class="layer front" viewBox="{VX} {VY} {VW} {VH}" aria-hidden="true">
    <defs>
      <linearGradient id="pg-glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#cfe6ff" stop-opacity="0.11"/><stop offset="0.35" stop-color="#cfe6ff" stop-opacity="0.02"/><stop offset="1" stop-color="#cfe6ff" stop-opacity="0.06"/></linearGradient>
      <linearGradient id="pg-handle" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e8ebf0"/><stop offset="1" stop-color="#9aa0aa"/></linearGradient>
      <linearGradient id="pg-head" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#262a31"/><stop offset="0.5" stop-color="#3a3f48"/><stop offset="1" stop-color="#22252b"/></linearGradient>
      <filter id="pg-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.5"/></filter>
    </defs>

    {#if feed}
      <path d={feed.d} fill="none" stroke="#000" stroke-width="6" stroke-linecap="round" opacity="0.45"/>
      <path d={feed.d} fill="none" stroke={feed.color} stroke-width="9" stroke-linecap="round" opacity="0.25" filter="url(#pg-glow)"/>
      <path d={feed.d} fill="none" stroke={feed.color} stroke-opacity="0.55" stroke-width="3.5" stroke-linecap="round"/>
      <path d={feed.d} fill="none" stroke="#fff" stroke-opacity="0.85" stroke-width="1.8" stroke-linecap="round" stroke-dasharray="3 13" class="flow" class:go={running && !still}/>
    {/if}

    <!-- X rail / arm + single-nozzle toolhead -->
    <g clip-path={a1 ? undefined : 'url(#pg-clip)'}>
      {#if a1}
        <rect x="150" y={RAIL_Y - 12} width="350" height="24" rx="8" fill="url(#pg-metal)" stroke="#474c57"/>
        <rect x="150" y={RAIL_Y - 12} width="350" height="3" rx="1.5" fill="#fff" opacity="0.18"/>
      {:else}
        <rect x={CH.x0} y={RAIL_Y - 3} width={CH.x1 - CH.x0} height="6" rx="3" fill="#b9bfc9"/>
        <rect x={CH.x0} y={RAIL_Y - 3} width={CH.x1 - CH.x0} height="2" fill="#fff" opacity="0.5"/>
      {/if}
      <g transform="translate({hx} {RAIL_Y})">
        <rect x="-26" y="-35" width="52" height="70" rx="5" fill="url(#pg-head)" stroke="#4a505b"/>
        <circle cx="0" cy="-8" r="17" fill="#15171c" stroke="#4a505b"/>
        <circle cx="0" cy="-8" r="11.5" fill="#1d2026"/>
        {#each [0, 60, 120] as a}<line x1="0" y1="-8" x2={Math.cos((a * Math.PI) / 180) * 11} y2={-8 + Math.sin((a * Math.PI) / 180) * 11} stroke="#2a2e36" stroke-width="2"/>{/each}
        <rect x="28" y="-28" width="14" height="13" rx="2" fill="#2a2418" stroke="#a7762a" stroke-width="0.8"/>
        <circle cx="35" cy="-21.5" r="2" fill="#f0a63a" opacity="0.8"/>
        <circle cx="-19" cy="-28" r="2.2" fill="var(--led)" opacity={online ? 1 : 0.2}/>
        <g transform="translate(0 35)">
          <path d="M-8 0 H8 L5 9 H-5 Z" fill="#20232a" stroke="#4a505b"/>
          <path d="M-3.5 9 H3.5 L1.6 16 H-1.6 Z" fill={nozzleHeat || '#9aa1ae'} stroke="#20232a" stroke-width="0.7"/>
          {#if nozzleHeat}<circle cx="0" cy="13" r="8" fill={nozzleHeat} opacity="0.35" filter="url(#pg-glow)"/>{/if}
          {#if tip && running}<circle cx="0" cy="18" r="2.2" fill={tip}/>{/if}
        </g>
      </g>
    </g>

    {#if !a1}
      <!-- glass door reflections + handle -->
      <rect x="146" y="322" width="358" height="420" rx="5" fill="url(#pg-glass)"/>
      <path d="M176 322 L262 322 L188 742 L146 742 L146 470 Z" fill="#fff" opacity="0.03"/>
      <path d="M330 322 L360 322 L286 742 L256 742 Z" fill="#fff" opacity="0.022"/>
      <rect x="146" y="322" width="358" height="420" rx="5" fill="none" stroke="#ffffff1c"/>
      <rect x="470" y="501" width="48" height="19" rx="9.5" fill="url(#pg-handle)" stroke="#6b717c" stroke-width="0.8"/>
      <text x="494" y="513.5" text-anchor="middle" font-size="6" font-weight="700" fill="#3a3e45" class="logo">Bambu Lab</text>
    {/if}
  </svg>
</div>

<style>
  .stage { position: relative; width: 100%; max-width: calc(min(80vh, 920px) * 690 / 760); margin-inline: auto; aspect-ratio: 690 / 760; user-select: none; }
  .layer { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .front { pointer-events: none; }
  .plate { position: absolute; overflow: hidden; border-radius: 6px; }
  .logo, .logo text { font-family: var(--font, system-ui, sans-serif); }

  .spool { transition: filter .4s; }
  .spool.act { filter: drop-shadow(0 0 6px var(--glow)); animation: glow 2.4s ease-in-out infinite; }
  @keyframes glow { 50% { filter: drop-shadow(0 0 14px var(--glow)); } }
  .feeder.act { filter: drop-shadow(0 0 8px var(--wc)); }

  .led { transition: fill .4s; }
  .led.pulse { animation: breathe 2.6s ease-in-out infinite; }
  .led.blink { animation: blink 1s steps(2) infinite; }
  .dot { filter: drop-shadow(0 0 3px var(--led)); }
  @keyframes breathe { 50% { opacity: .35; } }
  @keyframes blink { 50% { opacity: .15; } }

  .flow.go { animation: flow 1s linear infinite; }
  @keyframes flow { to { stroke-dashoffset: -16; } }

  @media (prefers-reduced-motion: reduce) {
    .spool.act, .led.pulse, .led.blink, .flow.go { animation: none; }
  }
</style>
