import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  base: './',
  resolve: { preserveSymlinks: false },
  build: { outDir: '../app/static/dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { proxy: { '/api': 'http://127.0.0.1:8099' } },
});
