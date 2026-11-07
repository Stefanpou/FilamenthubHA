<!-- Slicer settings for a filament: pick a profile (vendor's own or Generic) and adjust the main values.
     `choice` = {profile, settings}; settings start as the profile's and hold any edits. -->
<script>
  let { candidates = [], choice = $bindable(null), compact = false, picker = true } = $props();

  const EDIT = [
    ['supertack_plate_temp', 'Cool Plate SuperTack °C'],
    ['textured_plate_temp', 'Textured PEI °C'],
    ['nozzle_temperature', 'Dyse °C'],
    ['filament_flow_ratio', 'Flow ratio'],
    ['filament_max_volumetric_speed', 'Maks. flow mm³/s'],
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
  function pick(name) {
    const c = candidates.find((x) => x.profile === name);
    choice = c ? { profile: c.profile, settings: { ...c.settings } } : null;
  }
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
        <label class="f">{l}<input class="input mono" inputmode="decimal" bind:value={choice.settings[k]}>
          {#if k.includes('plate') && plate(choice.settings[k])}<span class="tiny muted">0 = {plate(choice.settings[k])}</span>{/if}</label>
      {/each}
    </div>
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
  details summary { cursor: pointer; }
  details .kv { margin-top: 8px; }
</style>
