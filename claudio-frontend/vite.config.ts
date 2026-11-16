import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
    },
  },
  test: {
    // Unit tests for the store and the HTTP layer only need a DOM-less env for
    // localStorage, which the tests stub themselves; `node` keeps the suite fast
    // and avoids a jsdom dependency.
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
