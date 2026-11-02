<!-- Bambu Filament Manager sync. -->
<script>
  import { app, toast, toastErr } from '../stores.svelte.js';
  import { api, post } from '../api.js';
  import { fmtG, fmtTs, hex } from '../util.js';
  import { onMount } from 'svelte';

  let ov = $state(null), err = $state(''), busy = $state(false), sel = $state({});
  const spools = $derived(Object.fromEntries((app.spools || []).map((s) => [s.id, s])));
  const shadow = $derived(!!(ov?.shadow ?? ov?.shadow_mode ?? ov?.dry_run ?? app.status?.shadow ?? app.status?.shadow_mode));
  const linked = $derived(new Set(ov?.linked_spools || []));
  const opt = (id) => { const s = spools[id]; return s ? `#${s.id} ${s.vendor} ${s.material} ${s.name} · ${fmtG(s.remaining)}` : `#${id}`; };

  async function load(refresh = false) {
    try { ov = await api(`api/fm${refresh ? '?refresh=true' : ''}`); err = ''; } catch (e) { err = e.message; }
  }
  onMount(() => load());

  async function doPost(path, body, msg, tries = true) {
    if (busy) return;
    busy = true;
    try {
      const r = await post(path, body);
      ov = r;
      if (msg) toast(typeof msg === 'function' ? msg(r) : msg);
    } catch (e) {
      busy = false;
      if (tries && /allerede koblet/.test(e.message) && body?.spool_id && !body.force && confirm(`${e.message}. Flyt koblingen hertil?`)) return doPost(path, { ...body, force: true }, msg, false);
      toastErr(e);
    } finally { busy = false; }
  }
  const link = (u) => { const v = sel[u.fm_id]; if (!v) return toastErr(new Error('Vælg en rulle først')); doPost('api/fm/link', { fm_id: u.fm_id, spool_id: Number(v) }, 'Koblet'); };
</script>

<div class="stack" class:dim={busy}>
  {#if shadow}
    <div class="alert info"><span class="badge shadow">Skyggetilstand</span>
      <span class="small">FilamentHub læser fra Bambu Filament Manager, men skriver ikke noget dertil, før du slår det til. Intet i Bambu Handy/Studio ændres.</span></div>
  {/if}
  <p class="small muted">Spolelisten i Bambu Studio og Bambu Handy. FilamentHub kan holde vægten dér korrekt, og følge med når du vælger rulle til et AMS-slot i Bambu Studio.</p>
  {#if err}<div class="alert warn">{err}</div>{/if}
  {#if ov}
    <label class="row sw"><input type="checkbox" checked={ov.enabled} onchange={(e) => doPost('api/fm/settings', { enabled: e.target.checked }, e.target.checked ? 'Synkronisering slået til' : 'Slået fra')}> <span>Synkronisér med Bambu</span>
      {#if shadow}<span class="badge shadow">skygge</span>{/if}</label>
    {#if ov.enabled}
      <dl class="kv"><dt>Ruller i Bambu</dt><dd>{ov.fm_total}</dd><dt>Koblet</dt><dd>{ov.linked}</dd><dt>Sidst synkroniseret</dt><dd>{ov.last_sync ? fmtTs(ov.last_sync) : 'venter …'}</dd></dl>
      {#if ov.error}<div class="alert warn small">{ov.error}</div>{/if}
      {#if ov.empty_long}
        <div class="alert warn small"><div>Bambu har vist en tom rulleliste i over 10 minutter. Det er som regel en fejl hos Bambu. Har du selv slettet alle ruller i Bambu Studio/Handy?
          <div style="margin-top:6px"><button class="btn small danger" onclick={() => confirm('Glem alle koblinger til Bambu-ruller?') && doPost('api/fm/confirm-empty', {}, 'Koblingerne er nulstillet')}>Ja, nulstil koblingerne</button></div></div></div>
      {/if}
      {#if ov.unlinked_fm?.length}
        <h3 style="font-size:15px">Kobl dine Bambu-ruller ({ov.unlinked_fm.length})</h3>
        {#each ov.unlinked_fm as u (u.fm_id)}
          <div class="fmrow">
            <i class="dot" style="background:{hex(u.color)}"></i>
            <div class="grow stack" style="gap:6px;min-width:0">
              <b>{u.vendor || 'Generic'} {u.material} · {u.name} {#if u.rfid}<span class="badge">RFID</span>{/if}{#if u.slot}<span class="badge accent">{u.slot}</span>{/if}</b>
              <span class="tiny muted">{u.net} / {u.total} g i Bambu</span>
              <select class="input" bind:value={sel[u.fm_id]}>
                <option value="">Vælg rulle …</option>
                {#if u.suggestions?.length}<optgroup label="Forslag">{#each u.suggestions as id}<option value={id}>{opt(id)}</option>{/each}</optgroup>{/if}
                <optgroup label="Alle ukoblede ruller">{#each (app.spools || []).filter((s) => !s.archived && !linked.has(s.id) && !(u.suggestions || []).includes(s.id)) as s}<option value={s.id}>{opt(s.id)}</option>{/each}</optgroup>
              </select>
              <div class="row wrap" style="gap:6px"><button class="btn small primary" onclick={() => link(u)}>Kobl</button>
                <button class="btn small" onclick={() => doPost('api/fm/import', { fm_id: u.fm_id }, (r) => `Importeret som rulle #${r.spool_id}`)}>Findes ikke – importér</button>
                <button class="btn small ghost" onclick={() => doPost('api/fm/ignore', { fm_id: u.fm_id })}>Ignorér</button></div>
            </div>
          </div>
        {/each}
      {/if}
      {#if ov.missing_in_fm?.length}
        <div class="alert info small"><div>{ov.missing_in_fm.length} ruller i lageret findes ikke i Bambu.
          <div style="margin-top:6px"><button class="btn small primary" onclick={() => confirm(`Opret ${ov.missing_in_fm.length} ruller i Bambu Filament Manager?`) && doPost('api/fm/create', { spool_ids: ov.missing_in_fm }, (r) => `${r.created} oprettet${r.errors?.length ? ' – ' + r.errors[0] : ''}`)}>Opret dem i Bambu</button></div></div></div>
      {/if}
      <label class="row sw small"><input type="checkbox" checked={ov.auto_create} onchange={(e) => doPost('api/fm/settings', { auto_create: e.target.checked })}> <span>Opret nye ruller automatisk i Bambu</span></label>
      <div class="row wrap"><button class="btn small" onclick={() => doPost('api/fm/sync', {}, (r) => (r.result?.error ? r.result.error : 'Synkroniseret'))}>Synkronisér nu</button>
        <button class="btn small ghost" onclick={() => load(true)}>Opdater liste</button></div>
      {#if ov.ignored_fm?.length}
        <details><summary class="small">Ignorerede Bambu-ruller ({ov.ignored_fm.length})</summary>
          {#each ov.ignored_fm as u}<div class="row small" style="margin-top:8px"><i class="dot" style="background:{hex(u.color)}"></i><span class="grow">{u.vendor} {u.material} · {u.name}</span>
            <button class="btn small" onclick={() => doPost('api/fm/unignore', { fm_id: u.fm_id })}>Fortryd</button></div>{/each}</details>
      {/if}
    {/if}
    {#if ov.created_by_us}
      <button class="btn small danger" onclick={() => confirm('Slet de ruller FilamentHub har oprettet i Bambu Filament Manager? Automatisk oprettelse slås samtidig fra.') && doPost('api/fm/remove-created', {}, (r) => `${r.removed} fjernet`)}>Fjern de {ov.created_by_us} ruller FilamentHub har oprettet i Bambu</button>
    {/if}
  {:else if !err}<div class="skeleton" style="height:60px"></div>{/if}
</div>

<style>
  .dim { opacity: .6; pointer-events: none; }
  .sw { gap: 10px; cursor: pointer; }
  .sw input { width: 20px; height: 20px; accent-color: var(--accent); }
  .fmrow { display: flex; gap: 12px; padding: 12px 0; border-top: 1px solid var(--panel-border); }
  .dot { width: 16px; height: 16px; border-radius: 50%; flex: none; margin-top: 3px; border: 1.5px solid rgba(255,255,255,.3); }
</style>
