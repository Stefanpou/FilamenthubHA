<!-- Upload status, bottom right: overall progress (files + bytes), the current file, speed, time left, errors, Stop. -->
<script>
  import Icon from './Icon.svelte';
  import { uq, stop, clear, speed, totals } from './uploads.svelte.js';
  import { bytes } from './api.js';

  let tick = $state(0);
  $effect(() => { const t = setInterval(() => tick++, 500); return () => clearInterval(t); });

  const t = $derived((tick, uq.items.map((x) => x.state + x.loaded).join(), totals()));
  const sp = $derived((tick, speed()));
  const cur = $derived(uq.items.find((x) => ['check', 'send', 'process'].includes(x.state)));
  const dups = $derived(uq.items.filter((x) => x.state === 'dup').length);
  const pct = $derived(t.total ? Math.min(100, (t.done / t.total) * 100) : 0);
  const left = $derived(sp && sp > 0 ? (t.total - t.done) / sp : null);
  const errors = $derived(uq.items.filter((x) => x.state === 'error'));
  const stopped = $derived(uq.items.filter((x) => x.state === 'stopped').length);
  const ok = $derived(uq.items.filter((x) => x.state === 'done').length);

  // hide by itself a few seconds after a clean finish
  $effect(() => {
    if (!uq.running && uq.finishedAt && !errors.length) { const h = setTimeout(clear, 5000); return () => clearTimeout(h); }
  });

  function eta(s) {
    if (s == null || !isFinite(s)) return 'beregner …';
    if (s < 60) return `ca. ${Math.max(1, Math.round(s))} sek. tilbage`;
    const m = Math.round(s / 60);
    return m < 60 ? `ca. ${m} min. tilbage` : `ca. ${Math.floor(m / 60)} t ${m % 60} min. tilbage`;
  }
  const curPct = (x) => (x.size ? Math.round((x.loaded / x.size) * 100) : 0);
  const STATE = { wait: 'Venter', check: 'Tjekker …', send: '', process: 'Læses …', done: '✓', dup: 'Findes allerede', error: 'Fejl', stopped: 'Stoppet' };
</script>

{#if uq.items.length}
  <div class="panel glass" role="status" aria-live="polite">
    <div class="row between head">
      <b class="small">{uq.running ? `Uploader ${Math.min(t.filesDone + 1, t.files)} af ${t.files}` : `Færdig: ${ok} uploadet${dups ? `, ${dups} fandtes allerede` : ''}${errors.length ? `, ${errors.length} fejlede` : ''}${stopped ? `, ${stopped} stoppet` : ''}`}</b>
      <span class="row">
        {#if uq.running}<button class="btn ghost small" onclick={stop}>Stop</button>{:else}<button class="btn ghost small icon" aria-label="Luk" onclick={clear}><Icon name="close" size={16} /></button>{/if}
        <button class="btn ghost small icon" aria-label={uq.open ? 'Skjul liste' : 'Vis liste'} onclick={() => (uq.open = !uq.open)}><Icon name={uq.open ? 'chev' : 'list'} size={16} /></button>
      </span>
    </div>

    <div class="bar big"><i style="width:{pct}%"></i></div>
    <div class="row between tiny muted mono">
      <span>{Math.floor(pct)} % · {bytes(t.done)} af {bytes(t.total)}</span>
      {#if uq.running}<span>{sp ? `${bytes(sp)}/s · ` : ''}{eta(left)}</span>{/if}
    </div>

    {#if cur}
      <div class="cur stack">
        <div class="row between small"><span class="ellipsis">{cur.name}</span>
          <span class="mono muted">{cur.state === 'process' ? 'Læses af serveren …' : cur.state === 'check' ? 'Tjekker om den findes …' : `${curPct(cur)} %`}</span></div>
        <div class="bar"><i class:pulse={cur.state !== 'send'} style="width:{cur.state === 'send' ? curPct(cur) : 100}%"></i></div>
      </div>
    {/if}

    {#if uq.open}
      <div class="list">
        {#each uq.items as x (x.id)}
          <div class="row between tiny it" class:err={x.state === 'error'} class:dim={['wait', 'stopped', 'dup'].includes(x.state)} title={x.err || ''}>
            <span class="ellipsis">{x.name}{x.folder ? ` → ${x.folder}` : ''}</span>
            <span class="mono nowrap">{x.state === 'send' ? `${curPct(x)} %` : STATE[x.state]}{x.size ? ` · ${bytes(x.size)}` : ''}</span>
          </div>
          {#if x.err && x.state === 'error'}<div class="tiny errtxt">{x.err}</div>{/if}
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .panel { position: fixed; right: 16px; bottom: 16px; width: min(380px, calc(100vw - 32px)); z-index: 150; background: var(--panel-solid);
    box-shadow: 0 12px 40px rgba(0,0,0,.45); padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .head { gap: 8px; }
  .icon { width: 32px; padding: 0; }
  .bar.big { height: 8px; }
  .cur { gap: 4px; padding-top: 4px; border-top: 1px solid var(--panel-border); }
  .list { display: flex; flex-direction: column; gap: 2px; max-height: 220px; overflow: auto; border-top: 1px solid var(--panel-border); padding-top: 6px; }
  .it { gap: 10px; padding: 2px 0; min-width: 0; }
  .it.dim { color: var(--muted); }
  .it.err { color: var(--danger); }
  .errtxt { color: var(--danger); padding-left: 8px; }
  .nowrap { white-space: nowrap; }
  .pulse { animation: pulse 1.2s ease-in-out infinite; }
</style>
