<!-- The two halves of Bibliotek: finished prints and the filament profile database. -->
<script>
  import { route } from './stores.svelte.js';
</script>

<div class="chips" role="tablist" aria-label="Bibliotek">
  <a class="chip" class:on={route.path === '/library'} href="#/library" role="tab" aria-selected={route.path === '/library'}>Prints</a>
  <a class="chip" class:on={route.path === '/library/profiles'} href="#/library/profiles" role="tab" aria-selected={route.path === '/library/profiles'}>Filamentprofiler</a>
</div>

<style>
  .chips { display: flex; gap: 8px; }
  a.chip { text-decoration: none; }
</style>
