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
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/admin": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/index": "http://localhost:8000",
      "/memories": "http://localhost:8000",
      "/query": "http://localhost:8000",
      "/retrieve": "http://localhost:8000",
      "/search": "http://localhost:8000",
    },
  },
});
