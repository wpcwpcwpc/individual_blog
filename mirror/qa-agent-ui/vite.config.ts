import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig(({ mode }) => {
  const isBuild = mode === 'production'

  return {
    base: '/',
    plugins: [
      react(),
      tailwindcss(),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    // build:web 时 define 覆盖 import.meta.env.VITE_API_BASE_URL 为 '/api',/*.ts 的 `?? '/api'` 兜底:build 走同域 nginx /api/ 反代,
    // dev 走下方 vite proxy /api → localhost:8000,桌面壳/独立客户端走 .env.local
    // 注入的 http://127.0.0.1:8000/api 直连。
    define: isBuild
      ? { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('/api') }
      : undefined,
    build: {
      outDir: 'dist',
    },
    server: {
      port: 5173,
      host: '127.0.0.1',  // 强制 IPv4，确保桌面 WebView 壳能探测到
      proxy: {
        // dev 模式:前端 /api/* → localhost:8000/api/* (后端路由已统一 /api 前缀)
        // ws: true 透传 WebSocket (路径 /api/sessions/{id}/stream/ws)
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
