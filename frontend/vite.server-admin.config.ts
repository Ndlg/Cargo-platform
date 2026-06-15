import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

function serverAdminEntryFallback(): Plugin {
  return {
    name: 'server-admin-entry-fallback',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const pathname = req.url?.split('?')[0] ?? ''
        if (
          pathname === '/' ||
          pathname === '/login' ||
          pathname.startsWith('/server-admin') ||
          pathname.startsWith('/admin')
        ) {
          req.url = '/server-admin.html'
        }
        next()
      })
    },
  }
}

const rootDir = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [serverAdminEntryFallback(), vue()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist-server-admin',
    rollupOptions: {
      input: resolve(rootDir, 'server-admin.html'),
    },
  },
})
