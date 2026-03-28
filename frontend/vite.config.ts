import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

const backendTarget = process.env.PROJECT_X_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/healthz': {
        target: backendTarget,
      },
      '/conversations': {
        target: backendTarget,
      },
      '/ws': {
        target: backendTarget,
        ws: true,
      },
    },
  },
})
