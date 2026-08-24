/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  // Pin to IPv4. On Windows, `localhost` often resolves to ::1 while uvicorn
  // is bound to 127.0.0.1, so the Vite proxy silently fails.
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': path.resolve(__dirname, './src') },
    },
    server: {
      port: 5173,
      // Listen on IPv4 so both http://127.0.0.1:5173 and http://localhost:5173 work.
      host: '0.0.0.0',
      strictPort: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          timeout: 0,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      globals: true,
      css: true,
    },
  }
})
