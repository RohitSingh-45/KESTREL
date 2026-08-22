import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
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
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          timeout: 0,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              // #region agent log
              try {
                fs.appendFileSync(
                  path.resolve(__dirname, '../debug-41078f.log'),
                  `${JSON.stringify({ sessionId: '41078f', hypothesisId: 'G', location: 'vite.config.ts:proxy', message: 'proxy_error', data: { error: String(err), target: apiTarget }, timestamp: Date.now() })}\n`,
                )
              } catch {
                /* ignore */
              }
              // #endregion
            })
          },
        },
      },
    },
  }
})
