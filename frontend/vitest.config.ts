import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

/**
 * 前端测试配置（2026-09-16 工作单 P1-12）。
 *
 * 三个层级各自独立可跑，避免"跑一个命令等半天"：
 * - tests/unit      → SSE 解析、状态机、持久化（不需要 DOM 组件）
 * - tests/component → Vue Test Utils 组件交互（jsdom）
 * - tests/          → 旧的 Node 内置运行器用例（`npm run test:contract`）
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['tests/component/setup.ts'],
    globals: false,
    include: ['tests/**/*.test.ts'],
    exclude: ['tests/.build/**', 'node_modules/**'],
    reporters: ['default'],
    restoreMocks: true,
  },
})
