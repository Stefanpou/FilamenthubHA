<script>
  import Dashboard from './pages/Dashboard.svelte';
  import Inventory from './pages/Inventory.svelte';
  import AddSpool from './pages/AddSpool.svelte';
  import History from './pages/History.svelte';
  import Settings from './pages/Settings.svelte';
  import Library from './pages/Library.svelte';
  import Profiles from './pages/Profiles.svelte';
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
  const PAGES = { '/': Dashboard, '/inventory': Inventory, '/add': AddSpool, '/history': History, '/settings': Settings, '/calc': Calculator, '/library': Library, '/library/profiles': Profiles, '/setup': Setup };
  const Page = $derived(PAGES[route.path] || Dashboard);

  onMount(() => { initRouter(); if (!kiosk) loadAuth(); });
  $effect(() => {
    if (kiosk) return;
    if (auth.authenticated) { startPolling(); return () => stopPolling(); }
  });
  $effect(() => { document.title = kiosk ? 'FilamentHub' : `${(route.path === '/library' ? 'Printbibliotek' : route.path === '/library/profiles' ? 'Filamentprofiler' : route.path === '/setup' ? 'Opsætning' : NAV.find((n) => n.path === route.path)?.label) || 'FilamentHub'} · FilamentHub`; });
  let setupChecked = false;
  $effect(() => {
    if (kiosk || !auth.authenticated || setupChecked) return;
    setupChecked = true;
    api('api/setup/state').then((s) => { if (s.needed && route.path !== '/setup') go('/setup'); }).catch(() => {});
  });
  const setup = $derived(route.path === '/setup');
  const active = (p) => (p === '/' ? route.path === '/' : route.path.startsWith(p));
  const pending = $derived(app.status?.pending || 0);
  const off = $derived(!app.online || (app.status && app.status.bambu?.mqtt !== 'connected'));
</script>

<div class="backdrop"></div>

{#if kiosk}
  <Kiosk />
{:else if !auth.ready}
  <div class="boot"><span class="spinner"></span></div>
{:else if !auth.authenticated}
  <Login />
{:else}
  <div class="shell">
    {#if !setup}<nav class="nav glass" aria-label="Hovedmenu">
      <a class="brand" href="#/" aria-label="FilamentHub"><img src="static/icon.svg?v=2" alt="" width="24" height="24"><span>FilamentHub</span></a>
      {#each NAV as n}
        <a href={'#' + n.path} class="item" class:on={active(n.path)} class:cta={n.cta} aria-current={active(n.path) ? 'page' : undefined}>
          <span class="ic"><Icon name={n.icon} size={n.cta ? 22 : 20} />{#if n.path === '/history' && pending}<i class="dot">{pending}</i>{/if}</span>
          <span class="lb">{n.cta ? 'Tilføj rulle' : n.label}</span>{#if n.path === '/history' && pending}<span class="count mono">{pending}</span>{/if}
        </a>
      {/each}
      <span class="conn" class:off><i></i>{off ? 'Ingen forbindelse til Bambu Cloud' : 'Forbundet til Bambu Cloud'}</span>
    </nav>{/if}
    <main class:setup>
      {#if !app.online}<div class="alert err offline">Ingen forbindelse til FilamentHub – prøver igen …</div>{/if}
      {#if app.reconnecting}<div class="alert info offline"><span class="spinner"></span> Forbinder til Home Assistant igen …</div>{/if}
      {#key route.path}<div class="view"><Page /></div>{/key}
    </main>
  </div>
{/if}
<Toasts />

<style>
  .boot { min-height: 100dvh; display: grid; place-items: center; }
  .shell { min-height: 100dvh; }
  main { padding: calc(var(--safe-t) + 16px) 16px calc(var(--nav-h) + var(--safe-b) + 24px); max-width: 1480px; margin: 0 auto; }
  /* "backwards", not "both": a kept transform makes .view the containing block of the fixed sheets/scrims inside it. */
  .view { animation: rise .4s var(--ease) backwards; }
  main.setup { padding-bottom: 24px; }
  @media (min-width: 900px) { main.setup { padding-left: 32px; } }
  .offline { margin-bottom: 12px; }

  /* phone: bottom bar */
  .nav { position: fixed; z-index: 50; left: 0; right: 0; bottom: 0; height: calc(var(--nav-h) + var(--safe-b)); padding: 0 4px var(--safe-b);
    display: flex; align-items: center; justify-content: space-around; background: var(--bg-2); border: 0; border-top: 1px solid var(--panel-border); border-radius: 0; }
  .brand, .conn, .count { display: none; }
  .item { flex: 1 1 auto; display: flex; flex-direction: column; align-items: center; gap: 4px; color: var(--muted); font-size: 11px; font-weight: 500;
    padding: 8px 0; border-radius: 10px; transition: color .14s, transform .14s var(--ease); position: relative; }
  .item .lb { white-space: nowrap; }
  .item:active { transform: scale(.97); }
  .item.on { color: var(--text); }
  .item.on .ic { color: var(--accent); }
  .ic { position: relative; display: grid; place-items: center; }
  .dot { position: absolute; top: -4px; right: -8px; background: var(--warn); color: #1d1404; font-size: 10px; font-weight: 600; font-style: normal;
    min-width: 16px; height: 16px; border-radius: 8px; display: grid; place-items: center; padding: 0 4px; }
  .item.cta .ic { width: 40px; height: 32px; border-radius: 10px; background: var(--accent); color: var(--accent-ink); box-shadow: inset 0 1px 0 rgba(255,255,255,.25); }
  .item.cta .lb { display: none; }
  .item.cta { justify-content: center; }

  /* desktop: sidebar */
  @media (min-width: 900px) {
    main { padding: 32px 32px 48px calc(var(--rail-w) + 32px); }
    .nav { left: 0; top: 0; bottom: 0; right: auto; width: var(--rail-w); height: auto; padding: 16px 12px; flex-direction: column; align-items: stretch;
      justify-content: flex-start; gap: 2px; border-top: 0; border-right: 1px solid var(--panel-border); }
    .brand { display: flex; align-items: center; gap: 8px; padding: 4px 8px 24px; color: var(--text); font-size: 16px; font-weight: 600; }
    .item { flex: none; flex-direction: row; gap: 12px; height: 36px; padding: 0 8px; font-size: 14px; color: var(--text-dim); }
    .item .ic { width: 20px; color: var(--muted); }
    .item:hover { color: var(--text); background: rgba(237,237,237,.04); }
    .item.on { color: var(--text); background: var(--panel-hi); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); }
    .item.on .ic { color: var(--accent); }
    .item .dot { display: none; }
    .count { display: inline; margin-left: auto; font-size: 12px; color: var(--warn); }
    .item.cta { order: -1; margin: 0 0 16px; height: 40px; justify-content: flex-start; background: var(--accent); color: var(--accent-ink); font-weight: 500;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.25); }
    .item.cta:hover { background: #52dbbe; color: var(--accent-ink); }
    .item.cta .ic { width: 20px; height: auto; background: none; box-shadow: none; color: var(--accent-ink); }
    .item.cta .lb { display: inline; }
    .brand { order: -2; }
    .conn { display: flex; align-items: center; gap: 8px; margin-top: auto; padding: 8px; font-size: 12px; color: var(--muted); }
    .conn i { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
    .conn.off i { background: var(--warn); }
  }
</style>
