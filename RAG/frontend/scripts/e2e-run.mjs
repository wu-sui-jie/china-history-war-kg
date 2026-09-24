#!/usr/bin/env node
/**
 * 离线浏览器验收运行器（2026-09-16 第五轮审核 P1-12）。
 *
 * 起桩后端（scripts/e2e-server.mjs）→ 等 /api/health 且确认是我们的桩 →
 * 跑 Playwright → 收掉桩进程。需要先 `npm run build`（桩后端托管 dist/）。
 *
 * 用法：
 *   npm run test:e2e:offline
 *   npm run test:e2e:offline -- --project=mobile
 */

import { spawn } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'

const port = process.env.E2E_PORT || '8125'
const baseURL = `http://127.0.0.1:${port}`
const extra = process.argv.slice(2)

// 桩身份的标记：运行器把它传给桩（桩用它当 health.version 与 demo.version），
// 再在探活时校验回来。第五轮整改复核 B6——旧实现只看"端口上 /api/health 是否 200"，
// 端口被上一次残留的桩或真实后端占用时会对着**错误的服务**跑绿。
const STUB_MARKER = process.env.E2E_STUB_MARKER || 'e2e-fixture'

// 直接跑本地 @playwright/test 的 CLI：Windows 上 spawn('npx.cmd') 会被
// Node 的 EINVAL 防护挡下（CVE-2024-27980 之后的默认行为），而经过 shell 又
// 容易在不同 shell 下行为不一致。用同一个 node 解释器执行 cli.js 最稳。
const cliJs = path.resolve('node_modules', '@playwright', 'test', 'cli.js')

function startServer() {
  const args = ['scripts/e2e-server.mjs', '--port', port, '--marker', STUB_MARKER]
  // 默认托管 dist/。注意：并入模式（build:integration，base=/rag/）的产物托管在 / 下会 404，
  // 想验证"独立访问形态"请先 `vite build --outDir dist-plain` 再用 E2E_DIST=dist-plain 跑。
  if (process.env.E2E_DIST) args.push('--dist', process.env.E2E_DIST)
  return spawn(process.execPath, args, { stdio: ['ignore', 'inherit', 'inherit'] })
}

async function waitForHealth(server, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  let lastError = ''
  let exited = null
  server.on('exit', (code, signal) => {
    exited = `桩后端提前退出（code=${code} signal=${signal}）`
  })
  while (Date.now() < deadline) {
    if (exited) throw new Error(exited)
    try {
      const resp = await fetch(`${baseURL}/api/health`)
      if (resp.ok) {
        const body = await resp.json()
        if (body?.version === STUB_MARKER) return
        throw new Error(
          `端口 ${port} 上的服务不是本次启动的桩（health.version=` +
          `${JSON.stringify(body?.version)}，期望 ${STUB_MARKER}）：` +
          '请先释放端口（或设置 E2E_PORT 换端口）再重跑',
        )
      }
      lastError = `HTTP ${resp.status}`
    } catch (err) {
      const message = String((err && err.message) || err)
      if (message.includes('不是本次启动的桩')) throw err
      lastError = message
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  throw new Error(`桩后端未在 ${timeoutMs} ms 内就绪: ${lastError}`)
}

const server = startServer()
let code = 1
try {
  await waitForHealth(server)
  console.log(`[e2e] 桩后端就绪: ${baseURL}（marker=${STUB_MARKER}）`)
  code = await new Promise((resolve) => {
    const runner = spawn(process.execPath, [cliJs, 'test', ...extra], {
      stdio: 'inherit',
      env: { ...process.env, RAG_BASE_URL: baseURL, CI: process.env.CI || '' },
    })
    runner.on('exit', (c) => resolve(c ?? 1))
    runner.on('error', () => resolve(1))
  })
} catch (err) {
  console.error(`[e2e] ${(err && err.message) || err}`)
} finally {
  server.kill()
}
process.exit(code)
