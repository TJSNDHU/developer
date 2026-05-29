import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Backend target: VITE_API_URL > REACT_APP_BACKEND_URL > localhost
  const apiTarget =
    env.VITE_API_URL ||
    env.REACT_APP_BACKEND_URL ||
    'http://localhost:8001'

  return {
    plugins: [react()],
    server: {
      port: 3000,
      host: '0.0.0.0',
      strictPort: true,
      allowedHosts: true,
      hmr: { clientPort: 443, protocol: 'wss' },
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    preview: {
      port: 3000,
      host: '0.0.0.0',
      allowedHosts: true,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
    define: {
      'process.env.REACT_APP_BACKEND_URL': JSON.stringify(env.REACT_APP_BACKEND_URL || ''),
    },
  }
})
