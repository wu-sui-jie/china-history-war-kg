import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

/**
 * 旧前端（layui-vue 管理台）测试配置 —— 此前零测试（2026-09-25 S3-4）。
 *
 * 与 RAG 前端同一套工具链（vitest + Vue Test Utils + jsdom），只保留一层：
 * `tests/component` 下的组件挂载用例。旧前端的重灾区分两类——四个 CRUD 页的
 * 复制粘贴（FE-4）与大页面拆分（P2-2）——都在组件层，因此不另设 unit 层。
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
