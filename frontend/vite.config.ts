import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    strictPort: true,
    // `API_BASE` is the relative path `/api` (src/api/client.ts), so it is the dev server that
    // puts the browser on the same origin as the backend. The target comes from the
    // environment because the live stack's fixtures bind free ports — a baked-in address would
    // have the screenshots show a backend that is not the one the round started.
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 3000,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    // Specs live next to what they test or in `tests/`; both are picked up so a component's
    // spec can sit beside it while cross-cutting specs (parser, state machine) group in one
    // place.
    include: ['src/**/*.spec.ts', 'tests/**/*.spec.ts'],
    globals: false,
    // NOTE: jsdom cannot start on Node < 20.19. Its dependency chain (jsdom ->
    // html-encoding-sniffer 6.0.0, which is CJS) `require()`s @exodus/bytes, an ESM-only
    // package. Node only allows `require()` of ESM from 20.19 / 22.12 onwards, so an older
    // runtime fails with `ERR_REQUIRE_ESM` before a single test runs — the message points at
    // the dependency, not at the runtime, which is why it is written down here. The
    // `engines` field in package.json already requires `^20.19.0 || >=22.12.0`.
  },
})
