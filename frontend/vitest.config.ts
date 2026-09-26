import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

/**
 * 旧前端（layui-vue 管理台）测试配置。
 *
 * 与 RAG 前端同一套工具链（vitest + Vue Test Utils + jsdom），分两层：
 * `tests/unit` 放纯函数用例，`tests/component` 放组件挂载用例（旧前端的风险
 * 主要集中在组件层：四个 CRUD 页的公共逻辑、大页面拆分、页面挂载即崩）。
 */
export default defineConfig({
  plugins: [vue()],
  // 与 vite.config.ts 的 base 保持一致：产物里的静态资源路径都带 /static/ 前缀，
  // 测试环境若用默认的 '/'，`import.meta.env.BASE_URL` 的断言就与生产路径脱节（等于没测）。
  base: '/static/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['tests/component/setup.ts'],
    globals: false,
    include: ['tests/**/*.test.ts'],
    reporters: ['default'],
    restoreMocks: true,
  },
})
