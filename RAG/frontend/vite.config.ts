import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // 联调期免 CORS：/api/* 转发到 RAG 后端
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 首屏预算（2026-09-15 审核 P1-15）：入口只应包含界面框架，
    // ECharts/地图这类重资源必须留在按需加载的分包里。
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // 用 id 匹配而不是 `{ echarts: ['echarts'] }` 的模块名写法：
        // 后者会把 echarts 整包（含全部图表类型）强制拉进依赖图，
        // 结果入口 chunk 静态 import 一个 1MB 的 echarts 包，懒加载完全失效。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
          if (
            id.includes('/vue/') ||
            id.includes('vue-router') ||
            id.includes('pinia') ||
            id.includes('markdown-it') ||
            id.includes('@vueuse') ||
            id.includes('/@vue/')
          ) {
            return 'vendor'
          }
          return undefined
        },
      },
    },
  },
})
