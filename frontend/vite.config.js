import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出到 app-dist（不叫 dist，便于随源码一起提交、由 FastAPI 直接托管）。
// base 固定 /app/，与 service.py 中的 app.mount("/app", ...) 对齐。
export default defineConfig({
  base: '/app/',
  plugins: [vue()],
  build: {
    outDir: 'app-dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://127.0.0.1:8085',
      '/api': 'http://127.0.0.1:8085',
      '/uploads': 'http://127.0.0.1:8085',
      '/live-commerce': 'http://127.0.0.1:8085',
      '/viewer': 'http://127.0.0.1:8085',
      '/ws': { target: 'ws://127.0.0.1:8085', ws: true },
    },
  },
})
