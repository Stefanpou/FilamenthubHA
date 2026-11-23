<script>
  import Icon from './Icon.svelte';
  import { get, post } from './api.js';

  let { onClose, toast } = $props();
  let s = $state(null), token = $state(''), url = $state(''), status = $state(null), saving = $state(false);
  let mg = $state({ mode: 'pct', pct: '', fixed: '', kg_price: '' });
  let dups = $state(null), cleaning = $state(false), showDups = $state(false);
  const loadDups = () => get('api/duplicates').then((d) => (dups = d)).catch(() => {});
  loadDups();
  async function cleanDups() {
    cleaning = true;
    try { const r = await post('api/duplicates/clean'); toast(`${r.moved} kopier flyttet til .slettet`); await loadDups(); }
    catch (e) { toast(e.message, true); } finally { cleaning = false; }
  }

  async function load() {
    s = await get('api/settings');
    url = s.fh_url;
    mg = { mode: s.margin.mode, pct: s.margin.pct ?? '', fixed: s.margin.fixed ?? '', kg_price: s.margin.kg_price ?? '' };
    status = await get('api/fh/status').catch(() => null);
  }
  load();

  async function save() {
    saving = true;
    try {
      const body = { fh_url: url, margin: mg };
      if (token.trim()) body.fh_token = token.trim();
      const r = await post('api/settings', body);
      s = r; status = r.fh; token = '';
      toast(r.fh.ok ? 'Gemt – forbundet til FilamentHub' : 'Gemt');
    } catch (e) { toast(e.message, true); }
    finally { saving = false; }
  }
</script>

<div class="page stack">
  <div class="row between"><h1>Indstillinger</h1><button class="btn ghost" onclick={onClose}><Icon name="back" size={18} /> Tilbage</button></div>
  {#if !s}<div class="skeleton" style="height:300px"></div>{:else}
  <section class="glass pad stack">
    <h2><Icon name="link" size={20} /> FilamentHub</h2>
    <p class="small dim">Modelbank henter lager, rullepriser, printbibliotek og strømmåling fra FilamentHub. Opret et API-token i
      FilamentHub under <b>Indstillinger → Spoolman-kompatibel API → API-token</b> og indsæt det her. Det kræver FilamentHub 0.3.27 eller nyere.</p>
    {#if status}
      <div class="alert {status.ok ? 'info' : 'warn'}">{#if status.ok}<Icon name="check" size={18} /> Forbundet til FilamentHub {status.version || ''}{:else}{status.error}{/if}</div>
    {/if}
    <label class="f">Adresse<input class="input mono" bind:value={url} placeholder={s.fh_url_default}></label>
    <div class="tiny muted">Standard: <span class="mono">{s.fh_url_default}</span> (FilamentHub-appen i samme arkiv)</div>
    <label class="f">API-token<input class="input mono" type="password" autocomplete="off" bind:value={token} placeholder={s.fh_token_set ? '•••••••• (gemt – skriv et nyt for at skifte)' : 'Indsæt tokenet'}></label>
  </section>
  <section class="glass pad stack">
    <h2><Icon name="calc" size={20} /> Pris</h2>
    <div class="grid2">
      <label class="f">Standardavance
        <select class="input" bind:value={mg.mode}><option value="pct">Procent</option><option value="fixed">Fast salgspris</option></select></label>
      {#if mg.mode === 'pct'}<label class="f">Procent<input class="input mono" inputmode="decimal" bind:value={mg.pct}></label>
      {:else}<label class="f">Salgspris (kr)<input class="input mono" inputmode="decimal" bind:value={mg.fixed}></label>{/if}
    </div>
    <label class="f">Kg-pris for ruller uden pris (kr/kg)<input class="input mono" inputmode="decimal" bind:value={mg.kg_price}></label>
  </section>
  <section class="glass pad stack">
    <h2><Icon name="box" size={20} /> Filer</h2>
    <p class="small dim">Modellerne ligger i <span class="mono">{s.models_dir}</span>. Undermapper bliver til mapper i Modelbank.
      Filer, du lægger ind via Samba, dukker op inden for {s.scan_minutes} minutter – eller med det samme, når du trykker »Scan«.
      Slettede modeller flyttes til <span class="mono">.slettet</span>.</p>
  </section>
  <section class="glass pad stack">
    <h2><Icon name="copy" size={20} /> Dubletter</h2>
    {#if !dups}<span class="spinner"></span>
    {:else if !dups.extra}<p class="small dim">Ingen filer findes flere gange.</p>
    {:else}
      <p class="small dim">{dups.groups.length} {dups.groups.length === 1 ? 'fil' : 'filer'} findes flere gange med præcis samme indhold
        ({dups.extra} overflødige kopier). »Ryd op« beholder én af hver (den med det oprindelige navn) og samler tags, noter,
        favorit og printhistorik på den. Kopierne flyttes til <span class="mono">.slettet</span>, så intet er væk for altid.</p>
      <button class="linkbtn small" style="align-self:flex-start" onclick={() => (showDups = !showDups)}>{showDups ? 'Skjul listen' : 'Vis hvilke'}</button>
      {#if showDups}
        <div class="dl">
          {#each dups.groups as g}
            <div class="small"><b>{g.keep.relpath}</b> <span class="muted">beholdes</span>
              {#each g.extra as x}<div class="tiny muted mono">– {x.relpath}</div>{/each}</div>
          {/each}
        </div>
      {/if}
      <div><button class="btn" disabled={cleaning} onclick={cleanDups}><Icon name="trash" size={18} /> {cleaning ? 'Rydder op …' : `Ryd op (${dups.extra} kopier)`}</button></div>
    {/if}
  </section>
  <div><button class="btn primary big" disabled={saving} onclick={save}>{saving ? 'Gemmer …' : 'Gem'}</button></div>
  {/if}
</div>

<style>
  h2 { font-size: 17px; display: flex; align-items: center; gap: 8px; }
  .page { max-width: 760px; gap: 18px; }
  .dl { display: flex; flex-direction: column; gap: 8px; max-height: 300px; overflow: auto; }
</style>
