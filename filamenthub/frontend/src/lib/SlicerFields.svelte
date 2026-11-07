<!-- Slicer settings for a filament: pick a profile (vendor's own or Generic) and adjust the main values.
     `choice` = {profile, settings}; settings start as the profile's and hold any edits.
     `process` = {base, settings}: the process values a filament may change (ironing, first layer and top surface
     flow) over one of Bambu's process presets (`processes` = [{name, defaults}]). A value that differs from the
     profile's own (or Bambu's) is marked as changed by hand. -->
<script>
  let { candidates = [], choice = $bindable(null), compact = false, picker = true,
        process = $bindable(null), processes = [], defaults = null } = $props();

  const EDIT = [
    ['supertack_plate_temp', 'Cool Plate SuperTack °C'],
    ['textured_plate_temp', 'Textured PEI °C'],
    ['nozzle_temperature', 'Dyse °C'],
    ['filament_flow_ratio', 'Flow ratio'],
    ['filament_max_volumetric_speed', 'Maks. flow mm³/s'],
  ];
  const PROC = [
    ['ironing_speed', 'Ironing mm/s'],
    ['ironing_flow', 'Ironing flow %'],
    ['initial_layer_flow_ratio', 'Flow ratio 1. lag'],
    ['top_solid_infill_flow_ratio', 'Flow ratio top'],
  ];
  const MORE = [
    ['nozzle_temperature_initial_layer', 'Dyse første lag', '°C'],
    ['nozzle_temperature_range_low', 'Dyse interval', '°C', 'nozzle_temperature_range_high'],
    ['supertack_plate_temp_initial_layer', 'SuperTack første lag', '°C'],
    ['textured_plate_temp_initial_layer', 'Textured PEI første lag', '°C'],
    ['hot_plate_temp', 'Smooth PEI / High Temp', '°C'],
    ['cool_plate_temp', 'Cool Plate (glat)', '°C'],
    ['eng_plate_temp', 'Engineering Plate', '°C'],
    ['fan_min_speed', 'Blæser', '%', 'fan_max_speed'],
    ['overhang_fan_speed', 'Blæser ved overhæng', '%'],
    ['chamber_temperatures', 'Kammer', '°C'],
    ['chamber_temperature', 'Kammer', '°C'],
    ['pressure_advance', 'Pressure advance', ''],
    ['filament_retraction_length', 'Retraktion', 'mm'],
    ['temperature_vitrification', 'Blødgøring', '°C'],
    ['filament_dev_ams_drying_temperature', 'Tørring i AMS', '°C', null, 'filament_dev_ams_drying_time', 't'],
    ['filament_density', 'Densitet', 'g/cm³'],
  ];

  const prof = $derived(candidates.find((c) => c.profile === choice?.profile) || null);
  const own = $derived(defaults || prof?.settings || {});
  const procDefaults = $derived(processes.find((p) => p.name === process?.base)?.defaults || {});
  function pick(name) {
    const c = candidates.find((x) => x.profile === name);
    choice = c ? { profile: c.profile, settings: { ...c.settings } } : null;
  }
  function pickBase(name) {
    // keep what was changed by hand, take the new preset's values for the rest
    const d = processes.find((p) => p.name === name)?.defaults || {};
    const kept = Object.fromEntries(PROC.filter(([k]) => changed(process?.settings?.[k], procDefaults[k])).map(([k]) => [k, process.settings[k]]));
    process = { base: name, settings: { ...d, ...kept } };
  }
  const num = (v) => Number(String(v ?? '').replace(',', '.').replace('%', '').trim());
  const changed = (v, d) => v != null && v !== '' && d != null && d !== '' && (Number.isFinite(num(v)) && Number.isFinite(num(d)) ? num(v) !== num(d) : String(v) !== String(d));
  const reset = (k) => { choice.settings[k] = own[k]; };
  const resetProc = (k) => { process.settings[k] = procDefaults[k]; };
  const plate = (v) => (v === '0' ? 'ikke anbefalet' : null);
  const label = (c) => `${c.profile.replace(/ @System$/, '').replace(/ @BBL (\w+)( 0\.4 nozzle)?$/, ' · $1')}${c.vendor === 'Generic' ? ' (generisk)' : ''}`;
  const more = $derived(MORE.filter(([k]) => choice?.settings?.[k] != null && choice.settings[k] !== ''));
</script>

<div class="stack" style="gap:10px">
  {#if picker}<label class="f">Slicer-profil
    <select class="input" value={choice?.profile || ''} onchange={(e) => pick(e.currentTarget.value)}>
      <option value="">– ingen –</option>
      {#each candidates as c}<option value={c.profile}>{label(c)}</option>{/each}
      {#if choice?.profile && !prof}<option value={choice.profile}>{choice.profile}</option>{/if}
    </select></label>{/if}
  {#if choice?.settings}
    <div class="grid">
      {#each EDIT as [k, l]}
        {@const m = changed(choice.settings[k], own[k])}
        <label class="f" class:mod={m}><span class="lb">{l}{#if m}<i class="mk" title="Ændret manuelt – profilen siger {own[k]}"></i>{/if}</span>
          <input class="input mono" inputmode="decimal" bind:value={choice.settings[k]}>
          {#if m}<button type="button" class="linkbtn tiny" onclick={() => reset(k)}>Profilens: {own[k]}</button>
          {:else if k.includes('plate') && plate(choice.settings[k])}<span class="tiny muted">0 = {plate(choice.settings[k])}</span>{/if}</label>
      {/each}
    </div>
    {#if process && processes.length}
      <div class="proc stack" style="gap:10px">
        <div class="row between"><span class="eyebrow">Proces</span>
          <select class="input base" value={process.base} onchange={(e) => pickBase(e.currentTarget.value)} aria-label="Bambus procesprofil">
            {#each processes as p}<option value={p.name}>{p.name.replace(/ @BBL \w+$/, '')}</option>{/each}
          </select></div>
        <div class="grid">
          {#each PROC as [k, l]}
            {@const m = changed(process.settings[k], procDefaults[k])}
            <label class="f" class:mod={m}><span class="lb">{l}{#if m}<i class="mk" title="Ændret manuelt – Bambu siger {procDefaults[k]}"></i>{/if}</span>
              <input class="input mono" inputmode="decimal" bind:value={process.settings[k]}>
              {#if m}<button type="button" class="linkbtn tiny" onclick={() => resetProc(k)}>Bambus: {procDefaults[k]}</button>{/if}</label>
          {/each}
        </div>
        <p class="tiny muted">Bambu Studio har disse i procesprofilen. Ændrer du dem, laves også en procesprofil, som du vælger sammen med filamentet.</p>
      </div>
    {/if}
    {#if !compact && more.length}
      <details>
        <summary class="small muted">Flere indstillinger fra profilen</summary>
        <dl class="kv small">
          {#each more as [k, l, u, k2, k3, u3]}
            <dt>{l}</dt><dd class="mono">{plate(choice.settings[k]) || `${choice.settings[k]}${k2 && choice.settings[k2] ? `–${choice.settings[k2]}` : ''} ${u}`}{k3 && choice.settings[k3] ? ` · ${choice.settings[k3]} ${u3}` : ''}</dd>
          {/each}
        </dl>
      </details>
    {/if}
  {/if}
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
  @media (min-width: 700px) { .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  .lb { display: inline-flex; align-items: center; gap: 6px; }
  .mk { width: 7px; height: 7px; border-radius: 50%; background: var(--warn); flex: none; }
  .mod .input { border-color: color-mix(in srgb, var(--warn) 55%, var(--panel-border)); }
  .mod .linkbtn { align-self: flex-start; color: var(--warn); font-size: 12px; }
  .proc { padding-top: 10px; border-top: 1px solid var(--panel-border); }
  .base { width: auto; max-width: 60%; min-height: 32px; padding-top: 4px; padding-bottom: 4px; font-size: 13px; }
  details summary { cursor: pointer; }
  details .kv { margin-top: 8px; }
</style>
