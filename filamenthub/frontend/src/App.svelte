<script>
  import Dashboard from './pages/Dashboard.svelte';
  import Inventory from './pages/Inventory.svelte';
  import AddSpool from './pages/AddSpool.svelte';
  import History from './pages/History.svelte';
  import Settings from './pages/Settings.svelte';
  import Library from './pages/Library.svelte';
  import Calculator from './pages/Calculator.svelte';
  import Login from './pages/Login.svelte';
  import Kiosk from './pages/Kiosk.svelte';
  import Setup from './pages/Setup.svelte';
  import Icon from './lib/ui/Icon.svelte';
  import Toasts from './lib/ui/Toasts.svelte';
  import { auth, app, route, go, initRouter, loadAuth, startPolling, stopPolling } from './lib/stores.svelte.js';
  import { isKiosk, api } from './lib/api.js';
  import { onMount } from 'svelte';

  const kiosk = isKiosk();
  const NAV = [
    { path: '/', label: 'Overblik', icon: 'home' },
    { path: '/inventory', label: 'Lager', icon: 'box' },
    { path: '/library', label: 'Bibliotek', icon: 'image' },
    { path: '/add', label: 'Tilføj', icon: 'plus', cta: true },
    { path: '/history', label: 'Historik', icon: 'chart' },
    { path: '/calc', label: 'Pris', icon: 'calc' },
    { path: '/settings', label: 'Indstillinger', icon: 'gear' },
  ];
  const PAGES = { '/': Dashboard, '/inventory': Inventory, '/add': AddSpool, '/history': History, '/settings': Settings, '/calc': Calculator, '/library': Library, '/setup': Setup };
  const Page = $derived(PAGES[route.path] || Dashboard);

  onMount(() => { initRouter(); if (!kiosk) loadAuth(); });
  $effect(() => {
    if (kiosk) return;
    if (auth.authenticated) { startPolling(); return () => stopPolling(); }
  });
  $effect(() => { document.title = kiosk ? 'FilamentHub' : `${(route.path === '/library' ? 'Printbibliotek' : route.path === '/setup' ? 'Opsætning' : NAV.find((n) => n.path === route.path)?.label) || 'FilamentHub'} · FilamentHub`; });
  let setupChecked = false;
  $effect(() => {
    if (kiosk || !auth.authenticated || setupChecked) return;
    setupChecked = true;
    api('api/setup/state').then((s) => { if (s.needed && route.path !== '/setup') go('/setup'); }).catch(() => {});
  });
  const setup = $derived(route.path === '/setup');
  const active = (p) => (p === '/' ? route.path === '/' : route.path.startsWith(p));
  const pending = $derived(app.status?.pending || 0);
</script>

<div class="backdrop"><div class="grid"></div><div class="noise"></div></div>

{#if kiosk}
  <Kiosk />
{:else if !auth.ready}
  <div class="boot"><span class="spinner"></span></div>
{:else if !auth.authenticated}
  <Login />
{:else}
  <div class="shell">
    {#if !setup}<nav class="nav glass" aria-label="Hovedmenu">
      <a class="brand" href="#/" aria-label="FilamentHub"><img src="static/icon.svg?v=2" alt="" width="34" height="34"></a>
      {#each NAV as n}
        <a href={'#' + n.path} class="item" class:on={active(n.path)} class:cta={n.cta} aria-current={active(n.path) ? 'page' : undefined}>
          <span class="ic"><Icon name={n.icon} size={n.cta ? 26 : 22} />{#if n.path === '/history' && pending}<i class="dot">{pending}</i>{/if}</span>
          <span class="lb">{n.label}</span>
        </a>
      {/each}
      <span class="conn" class:off={!app.online || (app.status && app.status.bambu?.mqtt !== 'connected')} title={app.online ? 'Printer-forbindelse' : 'Ingen forbindelse'}></span>
    </nav>{/if}
    <main class:setup>
      {#if !app.online}<div class="alert err offline">Ingen forbindelse til FilamentHub – prøver igen …</div>{/if}
      {#key route.path}<div class="view"><Page /></div>{/key}
    </main>
  </div>
{/if}
<Toasts />

<style>
  .boot { min-height: 100dvh; display: grid; place-items: center; }
  .shell { min-height: 100dvh; }
  main { padding: calc(var(--safe-t) + 16px) 16px calc(var(--nav-h) + var(--safe-b) + 28px); max-width: 1180px; margin: 0 auto; }
  /* "backwards", not "both": a kept transform makes .view the containing block of the fixed sheets/scrims inside it. */
  .view { animation: rise .5s var(--ease) backwards; }
  main.setup { padding-bottom: 28px; }
  @media (min-width: 900px) { main.setup { padding-left: 32px; } }
  .offline { margin-bottom: 12px; }
  .nav { position: fixed; z-index: 50; left: 10px; right: 10px; bottom: calc(var(--safe-b) + 10px); height: var(--nav-h); display: flex; align-items: center; justify-content: space-around;
    border-radius: 22px; background: rgba(14, 19, 31, .78); padding: 0 6px; }
  .brand, .conn { display: none; }
  .item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; color: var(--muted); font-size: 10.5px; font-weight: 600; padding: 8px 0; border-radius: 16px; transition: color .25s, transform .25s var(--ease); position: relative; }
  .item:active { transform: scale(.9); }
  .item.on { color: var(--accent); }
  .item.on .ic { filter: drop-shadow(0 0 8px var(--accent)); }
  .ic { position: relative; display: grid; place-items: center; }
  .dot { position: absolute; top: -6px; right: -10px; background: var(--warn); color: #1a1000; font-size: 10px; font-weight: 800; font-style: normal; min-width: 17px; height: 17px; border-radius: 99px; display: grid; place-items: center; padding: 0 4px; }
  .item.cta .ic { width: 52px; height: 52px; margin-top: -26px; border-radius: 50%; background: linear-gradient(135deg, var(--accent), #6ee7ff); color: var(--accent-ink);
    box-shadow: 0 10px 30px -6px var(--accent), 0 0 0 5px rgba(7, 9, 15, .85); transition: transform .3s var(--spring); }
  .item.cta.on .ic, .item.cta:hover .ic { transform: scale(1.08) rotate(90deg); filter: none; }
  .item.cta { color: var(--text-dim); }
  .item.cta .lb { margin-top: 5px; }   /* clear the 5px ring of the round + button */

  @media (min-width: 900px) {
    main { padding: 28px 32px 48px calc(var(--rail-w) + 32px); }
    .nav { left: 12px; top: 12px; bottom: 12px; right: auto; width: var(--rail-w); height: auto; flex-direction: column; justify-content: flex-start; gap: 6px; padding: 14px 8px; border-radius: 24px; }
    .brand { display: block; margin-bottom: 14px; }
    .item { flex: none; width: 100%; font-size: 11px; padding: 10px 0; }
    .item:hover { color: var(--text); background: rgba(255,255,255,.05); }
    .item.on { background: color-mix(in srgb, var(--accent) 10%, transparent); }
    .item.cta { margin: 10px 0; }
    .item.cta .ic { margin-top: 0; width: 46px; height: 46px; box-shadow: 0 8px 24px -6px var(--accent); }
    .item.cta .lb { margin-top: -2px; }
    .conn { display: block; margin-top: auto; width: 9px; height: 9px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 10px var(--ok); }
    .conn.off { background: var(--warn); box-shadow: 0 0 10px var(--warn); }
  }
</style>
