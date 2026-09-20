import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

/** 基路径与接口前缀参数化（2026-09-20 并入旧知识库系统 Web 入口）。
 *
 * 默认值保持独立部署口径（base `/`、接口前缀 `/api`），所以 `npm run build` 的产物
 * 与并入前逐字节一致，RAGv5 的同源托管与部署链路不受影响。
 * 并入模式由 `.env.integration` 覆盖：`npm run build:integration`
 * → base `/rag/`、接口前缀 `/rag/api`，配合旧系统的子路径反代使用。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const basePath = env.VITE_BASE_PATH || '/'
  // 去尾斜杠：代理键与重写规则都不该出现 `//`
  const apiBase = (env.VITE_API_BASE || '/api').replace(/\/+$/, '')

  return {
    base: basePath,
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
        // 联调期免 CORS：接口前缀转发到 RAG 后端。
        // 独立开发是 `/api`；并入模式是 `/rag/api`，重写回后端的 `/api`，
        // 与旧系统反代的路径约定保持一致。
        [apiBase]: {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(new RegExp(`^${apiBase}(?=/|$)`), '/api'),
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
  }
})
