import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'child_process'
import fs from 'fs'
import path from 'path'

// Resolve the frontend build sha at Vite config time. Priority:
//   1. explicit env var BUILD_SHA / GIT_COMMIT (deploy pipeline sets)
//   2. backend/BUILD_INFO.txt (canonical deploy-time sha — kept in
//      sync with backend so `/version` returns the same value)
//   3. `git rev-parse --short HEAD` (dev pod)
//   4. "unknown" (survives fresh clones with no git binary)
// Used by AdminSystemHealth.jsx to detect stale-bundle-on-prod.
function resolveBuildSha() {
  const explicit = process.env.BUILD_SHA || process.env.GIT_COMMIT
  if (explicit) return explicit.slice(0, 12)
  try {
    const buildInfo = path.resolve(__dirname, '..', 'backend', 'BUILD_INFO.txt')
    if (fs.existsSync(buildInfo)) {
      const val = fs.readFileSync(buildInfo, 'utf-8').trim()
      if (val) return val.slice(0, 12)
    }
  } catch { /* fall through */ }
  try {
    const out = execSync('git rev-parse --short=12 HEAD', {
      cwd: path.resolve(__dirname, '..'), stdio: ['ignore', 'pipe', 'ignore'],
    })
    return out.toString().trim()
  } catch { /* fall through */ }
  return 'unknown'
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Backend target: VITE_API_URL > REACT_APP_BACKEND_URL > localhost
  const apiTarget =
    env.VITE_API_URL ||
    env.REACT_APP_BACKEND_URL ||
    'http://localhost:8001'

  const buildSha = resolveBuildSha()
  console.log(`[vite.config] Frontend build sha resolved: ${buildSha}`)

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
      // Iter 309 · Batch-2 — frontend bundle sha, surfaced by
      // AdminSystemHealth.jsx to detect stale-bundle-vs-fresh-backend
      // deployments (Vite hash-fingerprints assets but index.html can
      // still be cache-served by a CDN; this marker makes the actual
      // shipped code identity visible in-browser).
      '__VITE_BUILD_SHA__': JSON.stringify(buildSha),
    },
  }
})
