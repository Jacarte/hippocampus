import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backendTarget = process.env.VITE_BACKEND_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/admin': backendTarget,
      '/health': backendTarget,
      '/index': backendTarget,
      '/memories': backendTarget,
      '/query': backendTarget,
      '/retrieve': backendTarget,
      '/search': backendTarget,
    },
  },
})

