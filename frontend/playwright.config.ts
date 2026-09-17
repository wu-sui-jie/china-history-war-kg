import { defineConfig, devices } from '@playwright/test'

/**
 * 浏览器端验收配置（2026-09-16 工作单 P1-12 / P1-13）。
 *
 * 默认连一个**已经在跑**的服务（`RAG_BASE_URL`，默认 http://127.0.0.1:8125），
 * 这样 CI 可以先启动后端再跑浏览器用例；本地也可以 `npm run dev` + `--base` 联调。
 * 不自动 webServer：后端是 Python 服务，交给测试脚本显式启动，避免双份配置漂移。
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: process.env.RAG_BASE_URL || 'http://127.0.0.1:8125',
    // 用完整 Chromium 的新 headless 模式（channel: 'chromium'），不依赖单独的
    // chromium-headless-shell 包：本机两次下载 headless shell 都被网络中断（ECONNRESET），
    // 而完整 Chromium 已可用（第五轮审核 P1-12 的"浏览器装不上"根因之一）。
    // channel 同时让本地与 CI 的行为更接近真实 Chrome。
    channel: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 7'] },
    },
  ],
})
