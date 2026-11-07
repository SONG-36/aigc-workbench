import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import {fileURLToPath} from 'node:url'

const proxy = { '/api': process.env.AIGC_API_TARGET || 'http://127.0.0.1:8022' }

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  test: {
    alias: {'virtual:showcase-assets':fileURLToPath(new URL('./src/test/showcase-asset-urls.ts',import.meta.url))},
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
