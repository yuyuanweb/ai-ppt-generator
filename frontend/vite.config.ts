import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // 端口统一使用 39xxx 段，避开 Vite 默认的 5173 等常见端口
    host: '127.0.0.1',
    // 可用 VITE_DEV_PORT 起第二个实例（例如对着另一端口的后端做联调），默认不变
    port: Number(process.env.VITE_DEV_PORT ?? 39173),
    strictPort: true,
    // 布局与主题定义位于仓库根的 shared/，在 frontend 之外，需显式放行
    fs: { allow: ['..'] },
    // 走代理而非直连，前端代码里所有请求都用同源相对路径，避免 CORS 与环境变量分叉
    proxy: {
      '/api': {
        // 默认指向 Python 后端；对着 Java 后端或临时实例联调时用 API_PROXY_TARGET 覆盖
        target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:39800',
        changeOrigin: true,
      },
    },
  },
})
