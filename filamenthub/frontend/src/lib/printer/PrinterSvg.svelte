<!-- Front-view illustration of a Bambu Lab X2D with an AMS 2 Pro on top (clear dome, spools seen edge-on) and the
     single external spool holder on the right side. Pure SVG in two stacked layers (back: housing, chamber, spools;
     front: toolhead, feed tube, glass) with the 3D canvas in between (children). Text lives in HTML (PrinterView),
     except the decorative logos. -->
<script>
  import { onMount } from 'svelte';
  import { hex, luminance } from './format.js';

  import SlotHotspots from './SlotHotspots.svelte';
  let { printer = null, slots = [], tone = 'idle', progress = 0, still = false, onslot = null, children } = $props();

  // viewBox (VX VY VW VH); the free column right of the printer holds the HUD (top) and the external holder ----
  const VX = 70, VY = 10, VW = 690, VH = 760;
  const BAYS = [188, 279, 371, 462];                 // AMS bay centres (x)
  const AMS = { sy: 170, rmax: 118, w: 64 };          // spool axis y (hidden behind the AMS front), max radius, width
  const EXT = { x: 590, y: 520, r: 82, w: 58 };       // external holder spool (edge-on, axis to the side)
  const CH = { x0: 154, x1: 495, y0: 330, y1: 734 };  // chamber interior behind the door
  const PLATE = { x0: 160, x1: 490, y0: 404, y1: 598 }; // where the 3D canvas sits (above the bed front edge)
  const RAIL_Y = 373;                                  // X gantry rail; the head only moves sideways (bed drops in Z)
  const NOZZLE_DX = [12, -12];                         // nozzle id 0 = right, id 1 = left (X2D/H2D), from the carriage centre
  const HEAD_IDLE = 325;

  const running = $derived(printer?.gcode_state === 'RUNNING');
  const online = $derived(!!printer?.online);
  const nozzles = $derived(printer?.nozzles || []);
  const activeNozzle = $derived(printer?.active_nozzle ?? 0);
  const lightOn = $derived(printer?.light !== false);
  const speedLvl = $derived(printer?.speed_level || 2);

  const amsSlots = $derived(slots.filter((s) => s.kind === 'ams' && s.unit === (slots.find((x) => x.kind === 'ams')?.unit ?? 0)));
  const bays = $derived([0, 1, 2, 3].map((i) => amsSlots.find((s) => s.tray === i) || null));
  const ext = $derived(slots.find((s) => s.kind === 'ext') || null);
  const active = $derived(slots.find((s) => s.active) || null);

  // ---- toolhead animation (JS so the feed tube can follow it) ----
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
    // state changes restart the animation; with reduced motion the head just stays centred
    running; speedLvl; still;
    if (still) hx = HEAD_IDLE; else wake();
  });

  // ---- helpers ----
  const heat = (t) => (t == null || t < 60 ? null : t < 150 ? '#ffb547' : t < 230 ? '#ff7a3d' : '#ff4d3d');
  const spoolColor = (s) => '#' + hex(s?.color);
  // filament radius from remaining % (hub radius 40% of the flange)
  const filR = (s, R) => {
    const hub = R * 0.4;
    return s?.remaining_pct == null ? R - 4 : hub + (R - 4 - hub) * Math.sqrt(Math.max(0.03, Math.min(1, s.remaining_pct / 100)));
  };
  // multi-colour filament: vertical bands across the spool width
  const bands = (s, w) => {
    const cs = s?.colors?.length > 1 ? s.colors : [s?.color];
    return cs.map((c, i) => ({ x: (i * w) / cs.length, w: w / cs.length + 0.3, c: '#' + hex(c) }));
  };

  const nozzleFill = (i) => heat(nozzles.find((x) => x.id === i)?.temp);
  const tipColor = (i) => (active && (active.nozzle ?? activeNozzle) === i ? spoolColor(active) : null);

  // feed tube of the active slot: AMS feeder / side holder -> the nozzle it is loaded to
  const HEAD_TOP = RAIL_Y - 32;
  const feed = $derived.by(() => {
    if (!active || !online) return null;
    const nz = active.nozzle ?? activeNozzle ?? 0;
    const ex = hx + (NOZZLE_DX[nz] ?? 0) * 0.6, ey = HEAD_TOP + 4;
    let d;
    if (active.kind === 'ext') {
      d = `M${EXT.x - EXT.w / 2 - 4} ${EXT.y - 30} C 520 ${EXT.y - 120}, ${ex + 60} ${ey - 40}, ${ex} ${ey}`;
    } else {
      const bx = BAYS[active.tray] ?? 325;
      d = `M${bx} 238 C ${bx} 300, ${ex} ${ey - 50}, ${ex} ${ey}`;
    }
    return { d, color: spoolColor(active) };
  });

  const LED = { idle: '#38e8c6', run: '#38e8c6', warn: '#ffb547', ok: '#3ddc84', bad: '#ff5c7a', prep: '#7c5cff' };
  const ledColor = $derived(LED[tone] || '#38e8c6');
  const pct = (v, o, n) => ((v - o) / n) * 100;
</script>

{#snippet rim(s, cx, cy, R, w, act)}
  <!-- one spool seen edge-on: two flanges + the wound filament between them -->
  {@const r = filR(s, R)}
  <g class="spool" class:act style="--glow: {spoolColor(s)}">
    <rect x={cx - w / 2 - 5} y={cy - R} width="5" height={R * 2} rx="2.5" fill="#9aa3b5" opacity="0.35"/>
    <rect x={cx + w / 2} y={cy - R} width="5" height={R * 2} rx="2.5" fill="#9aa3b5" opacity="0.35"/>
    <rect x={cx - w / 2} y={cy - R * 0.4} width={w} height={R * 0.8} fill="#1a1e27"/>
    <g transform="translate({cx - w / 2} {cy - r})">
      {#each bands(s, w) as b}<rect x={b.x} y="0" width={b.w} height={r * 2} fill={b.c}/>{/each}
      <rect width={w} height={r * 2} fill="url(#pv-wind)"/>
      <rect width={w} height={r * 2} fill="url(#pv-curve)"/>
      {#if luminance(s.color) < 0.08}<rect width={w} height={r * 2} fill="none" stroke="#ffffff26"/>{/if}
    </g>
  </g>
{/snippet}

<div class="stage" bind:this={root} style="--led: {ledColor}">
  <svg class="layer back" viewBox="{VX} {VY} {VW} {VH}" aria-hidden="true">
    <defs>
      <linearGradient id="pv-body" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#2a2e36"/><stop offset="0.06" stop-color="#3a3f49"/><stop offset="0.5" stop-color="#30343c"/>
        <stop offset="0.94" stop-color="#383d47"/><stop offset="1" stop-color="#24272e"/></linearGradient>
      <linearGradient id="pv-amsfront" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2b2f38"/><stop offset="0.12" stop-color="#1b1e25"/><stop offset="1" stop-color="#121419"/></linearGradient>
      <linearGradient id="pv-dome" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe8f5" stop-opacity="0.16"/><stop offset="0.35" stop-color="#dfe8f5" stop-opacity="0.05"/><stop offset="1" stop-color="#dfe8f5" stop-opacity="0.08"/></linearGradient>
      <linearGradient id="pv-feeder" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2a2e36"/><stop offset="0.45" stop-color="#4a4f5a"/><stop offset="1" stop-color="#23262d"/></linearGradient>
      <linearGradient id="pv-curve" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0.5"/><stop offset="0.3" stop-color="#fff" stop-opacity="0.12"/><stop offset="0.5" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.55"/></linearGradient>
      <linearGradient id="pv-chamber" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d1016"/><stop offset="0.6" stop-color="#11141b"/><stop offset="1" stop-color="#090b10"/></linearGradient>
      <radialGradient id="pv-light" cx="0.5" cy="0" r="0.9"><stop offset="0" stop-color="#e9f3ff" stop-opacity="0.3"/><stop offset="1" stop-color="#e9f3ff" stop-opacity="0"/></radialGradient>
      <pattern id="pv-wind" width="3.2" height="12" patternUnits="userSpaceOnUse"><rect width="1.1" height="12" fill="#000" opacity="0.22"/></pattern>
      <pattern id="pv-fins" width="7" height="20" patternUnits="userSpaceOnUse"><rect width="2" height="20" fill="#000" opacity="0.45"/></pattern>
      <clipPath id="pv-domeclip"><path d="M138 128 V54 Q138 26 166 26 H484 Q512 26 512 54 V128 Z"/></clipPath>
      <clipPath id="pv-clip"><rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height={CH.y1 - CH.y0} rx="4"/></clipPath>
    </defs>

    <!-- floor shadow -->
    <ellipse cx="320" cy="758" rx="250" ry="8" fill="#000" opacity="0.55"/>

    <!-- ===== AMS 2 Pro: clear dome with the four spools ===== -->
    <path d="M134 130 V52 Q134 22 166 22 H484 Q516 22 516 52 V130 Z" fill="#0b0d12" stroke="#4a5262" stroke-width="1.5"/>
    <g clip-path="url(#pv-domeclip)">
      {#each BAYS as bx, i (bx)}
        {@const s = bays[i]}
        {#if s && s.present}
          {@render rim(s, bx, AMS.sy, AMS.rmax, AMS.w, s.active)}
        {:else}
          <rect x={bx - AMS.w / 2} y={AMS.sy - AMS.rmax + 6} width={AMS.w} height={AMS.rmax} rx="4" fill="none" stroke="#3a4356" stroke-dasharray="3 6" opacity="0.7"/>
        {/if}
      {/each}
      <!-- bay dividers -->
      {#each [233, 325, 417] as dx}<rect x={dx - 3} y="30" width="6" height="100" fill="#2b313d" opacity="0.85"/>{/each}
    </g>
    <path d="M134 130 V52 Q134 22 166 22 H484 Q516 22 516 52 V130 Z" fill="url(#pv-dome)"/>
    <path d="M150 40 Q160 30 200 30 H450" fill="none" stroke="#fff" stroke-opacity="0.22" stroke-width="2" stroke-linecap="round"/>

    <!-- AMS front: lid seam, feeders with their windows, logo -->
    <rect x="126" y="126" width="398" height="120" rx="8" fill="url(#pv-amsfront)" stroke="#3a404c"/>
    <rect x="126" y="126" width="398" height="8" rx="4" fill="#3b414d"/>
    <rect x="312" y="118" width="26" height="12" rx="4" fill="#9aa1ae"/>
    {#each BAYS as bx, i (bx)}
      {@const s = bays[i]}
      {@const wc = s && s.present ? spoolColor(s) : '#2a2f3a'}
      <g style="--wc: {wc}" class="feeder" class:act={s?.active}>
        <rect x={bx - 23} y="156" width="46" height="86" rx="10" fill="#0f1116" stroke="#2c313b"/>
        <rect x={bx - 16} y="161" width="32" height="34" rx="8" fill="#06070a"/>
        <rect x={bx - 14} y="163" width="28" height="30" rx="7" fill={wc} opacity={s && s.present ? (s.active ? 0.95 : 0.7) : 0.25}/>
        <rect x={bx - 14} y="163" width="28" height="30" rx="7" fill="url(#pv-curve)"/>
        <rect x={bx - 10} y="165" width="5" height="22" rx="2.5" fill="#fff" opacity="0.18"/>
        <rect x={bx - 18} y="198" width="36" height="40" rx="9" fill="url(#pv-feeder)"/>
      </g>
    {/each}
    <g class="logo" fill="#dfe3ea">
      <text x="404" y="144" font-size="4.5" font-weight="700">Bambu</text>
      <text x="404" y="149" font-size="4.5" font-weight="700">Lab</text>
      <text x="421" y="150" font-size="13" font-weight="600" letter-spacing="2">AMS 2</text>
      <rect x="473" y="142.5" width="13" height="7" rx="1.5" fill="none" stroke="#dfe3ea" stroke-width="0.7"/>
      <text x="474.6" y="148" font-size="4.3" font-weight="700">PRO</text>
    </g>

    <!-- ===== X2D housing ===== -->
    <rect x="110" y="244" width="420" height="510" rx="12" fill="url(#pv-body)" stroke="#474c57"/>
    <rect x="110" y="244" width="420" height="4" rx="2" fill="#5a606c" opacity="0.6"/>
    <g class="logo" fill="#e4e7ec">
      <text x="416" y="279" font-size="5.5" font-weight="700">Bambu</text>
      <text x="416" y="285" font-size="5.5" font-weight="700">Lab</text>
      <text x="436" y="287" font-size="20" font-weight="700" font-style="italic" letter-spacing="1">X2D</text>
    </g>

    <!-- door frame + chamber -->
    <rect x="146" y="322" width="358" height="420" rx="5" fill="#07080b" stroke="#50566a" stroke-width="2"/>
    <rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height={CH.y1 - CH.y0} rx="4" fill="url(#pv-chamber)"/>
    <g clip-path="url(#pv-clip)">
      <rect x={CH.x0} y={CH.y0} width={CH.x1 - CH.x0} height="200" fill="url(#pv-light)" opacity={lightOn ? 1 : 0.12} style="transition: opacity .6s"/>
      <!-- back gantry belt -->
      <rect x={CH.x0} y="344" width={CH.x1 - CH.x0} height="12" fill="#0b0d11"/>
      <rect x={CH.x0} y="347" width={CH.x1 - CH.x0} height="6" fill="url(#pv-fins)" opacity="0.6"/>
      <!-- Z column (left) and heat sink (right) -->
      <rect x="166" y="425" width="62" height="140" rx="4" fill="#0c0e13" stroke="#171b22"/>
      <rect x="383" y="462" width="80" height="200" rx="3" fill="#0d0f14"/>
      <rect x="383" y="462" width="80" height="200" fill="url(#pv-fins)"/>
      <line x1="236" y1={CH.y0} x2="236" y2={CH.y1} stroke="#151920" stroke-width="3"/>
      <!-- build plate front edge -->
      <rect x="173" y="596" width="292" height="12" rx="2" fill="#1c1f26" stroke="#2c313b"/>
      <rect x="173" y="596" width="292" height="2" fill="#5a4a2c" opacity="0.7"/>
      <rect x="182" y="608" width="274" height="14" fill="#0b0d11"/>
      <!-- lower floor -->
      <rect x={CH.x0} y="690" width={CH.x1 - CH.x0} height="44" fill="#08090c"/>
    </g>
    <!-- door hinges -->
    {#each [[166, 336], [186, 336], [166, 726], [186, 726]] as [x, y]}<circle cx={x} cy={y} r="4.5" fill="#15181e" stroke="#2c313b"/>{/each}

    <!-- touch screen (overlaps the AMS front and the top of the printer, like the real one) -->
    <rect x="166" y="220" width="140" height="86" rx="11" fill="#0a0b0e" stroke="#5a606c" stroke-width="1.5"/>
    <rect x="173" y="226" width="126" height="74" rx="6" fill="#11151c"/>
    {#each [0, 1, 2, 3, 4] as i}<circle cx="181" cy={234 + i * 14} r="2.6" fill="#3a4250"/>{/each}
    {#each [0, 1, 2] as i}
      <rect x="190" y={232 + i * 22} width="44" height="18" rx="3" fill="#1b212b"/>
      <rect x="194" y={238 + i * 22} width="20" height="3" rx="1.5" fill="#566074"/>
    {/each}
    <rect x="194" y="244" width="22" height="3" rx="1.5" fill="var(--led)" opacity={online ? 0.9 : 0.2}/>
    <rect x="240" y="232" width="54" height="62" rx="3" fill="#1b212b"/>
    <path d="M246 240 H288 V288 H246 Z" fill="none" stroke="#3b4454"/>
    <path d="M250 276 L284 276 L290 284 L244 284 Z" fill="#6c7380"/>
    <rect x="190" y="296" width="104" height="2" rx="1" fill="#232a35"/>
    <rect x="190" y="296" width={Math.max(0, 104 * Math.min(1, progress))} height="2" rx="1" fill="var(--led)"/>
    <circle cx="289" cy="236" r="2" fill="var(--led)" class="dot" opacity={online ? 1 : 0.2}/>

    <!-- status light strip along the top of the door -->
    <rect x="200" y="314" width="250" height="3" rx="1.5" fill="var(--led)" class="led" class:pulse={running || tone === 'prep'} class:blink={tone === 'bad'} opacity={online ? 0.85 : 0.12}/>

    <!-- feet -->
    <rect x="128" y="752" width="44" height="6" rx="2" fill="#0d0f13"/><rect x="468" y="752" width="44" height="6" rx="2" fill="#0d0f13"/>

    <!-- ===== external spool holder (right side, spool seen edge-on) ===== -->
    <g class="extholder">
      <rect x="530" y={EXT.y - 6} width="34" height="12" rx="4" fill="#3a3f49" stroke="#4d535f"/>
      <rect x="526" y={EXT.y - 22} width="8" height="44" rx="3" fill="#2b2f37"/>
      {#if ext && ext.present}
        {@render rim(ext, EXT.x, EXT.y, EXT.r, EXT.w, ext.active)}
      {:else}
        <rect x={EXT.x - EXT.w / 2} y={EXT.y - EXT.r} width={EXT.w} height={EXT.r * 2} rx="6" fill="none" stroke="#3a4356" stroke-dasharray="3 6" opacity="0.7"/>
      {/if}
    </g>
  </svg>

  <!-- the 3D model sits on the plate in the chamber -->
  <div class="plate" style="left: {pct(PLATE.x0, VX, VW)}%; top: {pct(PLATE.y0, VY, VH)}%; width: {((PLATE.x1 - PLATE.x0) / VW) * 100}%; height: {((PLATE.y1 - PLATE.y0) / VH) * 100}%">
    {@render children?.()}
  </div>

  <svg class="layer front" viewBox="{VX} {VY} {VW} {VH}" aria-hidden="true">
    <defs>
      <linearGradient id="pv-glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#cfe6ff" stop-opacity="0.11"/><stop offset="0.35" stop-color="#cfe6ff" stop-opacity="0.02"/><stop offset="1" stop-color="#cfe6ff" stop-opacity="0.06"/></linearGradient>
      <linearGradient id="pv-handle" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e8ebf0"/><stop offset="1" stop-color="#9aa0aa"/></linearGradient>
      <linearGradient id="pv-head" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#262a31"/><stop offset="0.5" stop-color="#3a3f48"/><stop offset="1" stop-color="#22252b"/></linearGradient>
      <filter id="pv-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.5"/></filter>
    </defs>

    <!-- feed tube of the active slot -->
    {#if feed}
      <path d={feed.d} fill="none" stroke="#000" stroke-width="6" stroke-linecap="round" opacity="0.45"/>
      <path d={feed.d} fill="none" stroke={feed.color} stroke-width="9" stroke-linecap="round" opacity="0.25" filter="url(#pv-glow)"/>
      <path d={feed.d} fill="none" stroke={feed.color} stroke-opacity="0.55" stroke-width="3.5" stroke-linecap="round"/>
      <path d={feed.d} fill="none" stroke="#fff" stroke-opacity="0.85" stroke-width="1.8" stroke-linecap="round" stroke-dasharray="3 13" class="flow" class:go={running && !still}/>
    {/if}

    <!-- X rail + dual-nozzle toolhead -->
    <g clip-path="url(#pv-clip)">
      <rect x={CH.x0} y={RAIL_Y - 3} width={CH.x1 - CH.x0} height="6" rx="3" fill="#b9bfc9"/>
      <rect x={CH.x0} y={RAIL_Y - 3} width={CH.x1 - CH.x0} height="2" fill="#fff" opacity="0.5"/>
      <g transform="translate({hx} {RAIL_Y})">
        <rect x="-32" y="-35" width="64" height="70" rx="5" fill="url(#pv-head)" stroke="#4a505b"/>
        <circle cx="0" cy="-6" r="19" fill="#15171c" stroke="#4a505b"/>
        <circle cx="0" cy="-6" r="13" fill="#1d2026"/>
        {#each [0, 60, 120] as a}<line x1="0" y1="-6" x2={Math.cos((a * Math.PI) / 180) * 12} y2={-6 + Math.sin((a * Math.PI) / 180) * 12} stroke="#2a2e36" stroke-width="2"/>{/each}
        <rect x="34" y="-28" width="16" height="13" rx="2" fill="#2a2418" stroke="#a7762a" stroke-width="0.8"/>
        <circle cx="42" cy="-21.5" r="2" fill="#f0a63a" opacity="0.8"/>
        <circle cx="-24" cy="-28" r="2.2" fill="var(--led)" opacity={online ? 1 : 0.2}/>
        {#each (nozzles.length === 1 ? [0] : [0, 1]) as i}
          {@const dx = nozzles.length === 1 ? 0 : NOZZLE_DX[i]}
          {@const h = nozzleFill(i)}
          {@const tc = tipColor(i)}
          <g transform="translate({dx} 35)" opacity={i === activeNozzle || !running ? 1 : 0.65}>
            <path d="M-8 0 H8 L5 9 H-5 Z" fill="#20232a" stroke="#4a505b"/>
            <path d="M-3.5 9 H3.5 L1.6 16 H-1.6 Z" fill={h || '#9aa1ae'} stroke="#20232a" stroke-width="0.7"/>
            {#if h}<circle cx="0" cy="13" r="8" fill={h} opacity="0.35" filter="url(#pv-glow)"/>{/if}
            {#if tc && running}<circle cx="0" cy="18" r="2.2" fill={tc}/>{/if}
          </g>
        {/each}
      </g>
    </g>

    <!-- glass door reflections + handle -->
    <rect x="146" y="322" width="358" height="420" rx="5" fill="url(#pv-glass)"/>
    <path d="M176 322 L262 322 L188 742 L146 742 L146 470 Z" fill="#fff" opacity="0.03"/>
    <path d="M330 322 L360 322 L286 742 L256 742 Z" fill="#fff" opacity="0.022"/>
    <rect x="146" y="322" width="358" height="420" rx="5" fill="none" stroke="#ffffff1c"/>
    <rect x="470" y="501" width="48" height="19" rx="9.5" fill="url(#pv-handle)" stroke="#6b717c" stroke-width="0.8"/>
    <text x="494" y="513.5" text-anchor="middle" font-size="6" font-weight="700" fill="#3a3e45">Bambu Lab</text>
  </svg>
  <SlotHotspots view={[VX, VY, VW, VH]} {bays} {ext} extAt={{ x: EXT.x, y: EXT.y + EXT.r + 8 }} bayY={84} {onslot} />
</div>

<style>
  .stage { position: relative; width: 100%; max-width: calc(min(80vh, 920px) * 690 / 760); margin-inline: auto; aspect-ratio: 690 / 760; user-select: none; container-type: inline-size; }
  .layer { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .front { pointer-events: none; }
  .plate { position: absolute; overflow: hidden; border-radius: 6px; }
  .logo text { font-family: var(--font, system-ui, sans-serif); }

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
