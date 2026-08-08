import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API_PROXY = {
  '/api': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
  '/health': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
  '/ready': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: API_PROXY,
  },
  // `vite preview` must also proxy — otherwise /api returns 502 Bad Gateway.
  preview: {
    port: 4173,
    proxy: API_PROXY,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
