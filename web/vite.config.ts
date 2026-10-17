import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // `airspace serve` owns the API; the dev server only forwards to it
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  // mapbox-gl is most of the bundle; it's served locally, so size matters little
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 2500 },
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts'],
  },
});
