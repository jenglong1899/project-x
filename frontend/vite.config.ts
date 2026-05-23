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
        // 调试时经常会在后端断点停很久，某些代理/中间层可能因为空闲超时而断开连接。
        // 这里尽量放宽代理超时，降低“调试一会儿就断线”的概率。
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})
