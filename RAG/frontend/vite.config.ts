import { fileURLToPath, URL } from 'node:url'
import { writeFileSync } from 'node:fs'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv, type Plugin } from 'vite'

/** 基路径与接口前缀参数化（并入旧知识库系统 Web 入口）。
 *
 * 默认值保持独立部署口径（base `/`、接口前缀 `/api`），`npm run build` 的产物用于
 * 同源托管与部署链路。并入模式由 `.env.integration` 覆盖：`npm run build:integration`
 * → base `/rag/`、接口前缀 `/rag/api`，配合旧系统的子路径反代使用。
 */

/** 把构建模式写进 dist，供 RAG 服务启动时核对。
 *
 * `dist/` 只有一份，`build`（base=/）与 `build:integration`（base=/rag/）互相覆盖；
 * 并入反代下若误用独立产物，页面会白屏且控制台只有 404、不看 Network 面板发现不了。
 * 服务端读这个文件（缺失时回退看 index.html 的资源前缀）并在日志与 /api/health 里报出模式。
 *
 * 写入目录取 `build.outDir` 而非写死 `./dist/`：用 `--outDir dist-plain`
 * 之类的旁路构建（浏览器 e2e 就是这么跑的）时，写死会让并入模式 dist 的标记被改成
 * standalone，而产物本身没动——服务启动时会报错模式，排查起来像"构建产物坏了"。
 */
function buildModeMarker(mode: string): Plugin {
  let outDir = 'dist'
  return {
    name: 'china-war:build-mode-marker',
    configResolved(config) {
      outDir = config.build.outDir || 'dist'
    },
    closeBundle() {
      writeFileSync(
        fileURLToPath(new URL(`./${outDir.replace(/\\/g, '/')}/build-mode.txt`, import.meta.url)),
        `${mode}\n`,
        'utf-8',
      )
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const basePath = env.VITE_BASE_PATH || '/'
  // 去尾斜杠：代理键与重写规则都不该出现 `//`
  const apiBase = (env.VITE_API_BASE || '/api').replace(/\/+$/, '')
  // `--mode integration` 即并入模式（npm run build:integration），其余一律按独立模式记
  const buildMode = mode === 'integration' ? 'integration' : 'standalone'

  return {
    base: basePath,
    plugins: [vue(), buildModeMarker(buildMode)],
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
      // 首屏预算：入口只应包含界面框架，
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
