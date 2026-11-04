import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const proxy = { '/api': process.env.AIGC_API_TARGET || 'http://127.0.0.1:8022' }

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
