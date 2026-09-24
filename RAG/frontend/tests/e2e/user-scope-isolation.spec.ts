/** 问答记录按账号隔离的浏览器级验收（2026-09-25，问题二方案 A）。
 *
 * 为什么必须有一条浏览器用例：这个功能横跨两个应用（主应用 postMessage ↔ RAG 前端按 uid 分桶），
 * `installHostUserBridge` 与 `applyUserScope` 各自单测都过，但**接缝**曾经断过——桥改成传
 * `{uid, role}` 对象后 store 仍按字符串处理，activeUid 变成 "[object Object]"，所有账号落进
 * 同一个桶，线上表现就是"两个账号看到的记录一模一样"。单测抓不到，浏览器里一跑就现。
 *
 * 装置：本用例自己起一个页面容器（`/scope-harness.html`）把 RAG 页面放进 iframe，
 * 再从父页 postMessage 身份消息——与主应用 RagAssistant.vue 的做法一致（同源 + targetOrigin '/'）。
 * 存储桶的直接证据在 localStorage 的 key 上（`ragv5-session-v3:u{uid}`），不依赖界面文案。
 */

import { expect, test } from '@playwright/test'

const BASE_KEY = 'ragv5-session-v3'
const scopeKey = (uid: string) => `${BASE_KEY}:u${uid}`

/** 在 RAG 页面里提一个问题：会用问题自动命名会话，正好当该账号的标记用。 */
async function ask(frame: Parameters<Parameters<typeof test>[1]>[0], text: string) {
  const input = frame.locator('textarea, input[type="text"]').first()
  await input.fill(text)
  await frame.locator('button', { hasText: '发送' }).first().click()
  await frame.waitForTimeout(1200)   // 等一次持久化（后端可能不在，失败终态一样会落盘）
}

async function bucketTitles(page: Parameters<Parameters<typeof test>[1]>[0], key: string): Promise<string[]> {
  const raw = await page.evaluate((k) => localStorage.getItem(k), key)
  if (!raw) return []
  try {
    return (JSON.parse(raw).sessions || []).map((item: { title: string }) => item.title)
  } catch {
    return ['<解析失败>']
  }
}

test.describe('问答记录按账号隔离', () => {
  test('两个账号各存各的，切换与退出都不串', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '隔离逻辑与视口无关，桌面口径验证即可')

    await page.goto('/scope-harness.html')
    await page.evaluate(() => (window as any).ready)
    const frame = page.frames().find((f) => f.url().includes('scope-harness') === false && f !== page.mainFrame())
    expect(frame, 'RAG 页面应已进入 iframe').toBeTruthy()
    const rag = frame!

    // —— 账号 7 ——
    await page.evaluate(() => (window as any).postScope(7, 'admin'))
    await page.waitForTimeout(300)
    await ask(rag, '七号账号的问题')
    expect(JSON.parse(await page.evaluate((k) => localStorage.getItem(k) as string, scopeKey('7')) || '{}')
      .sessions?.some((s: { title: string }) => s.title.includes('七号账号'))).toBe(true)
    // 老记录留在共享桶，不会被搬进账号桶
    expect((await bucketTitles(page, BASE_KEY)).some((t) => t.includes('七号账号'))).toBe(false)

    // —— 换到账号 8：看不到账号 7 的记录，自己写的落在自己的桶 ——
    await page.evaluate(() => (window as any).postScope(8, 'viewer'))
    await page.waitForTimeout(400)
    expect(await rag.evaluate(() => document.body.innerText)).not.toContain('七号账号')
    await ask(rag, '八号账号的问题')
    expect((await bucketTitles(page, scopeKey('8'))).some((t) => t.includes('八号账号'))).toBe(true)
    expect((await bucketTitles(page, scopeKey('7'))).some((t) => t.includes('八号账号'))).toBe(false)

    // —— 切回账号 7：记录回来，且看不到账号 8 的 ——
    await page.evaluate(() => (window as any).postScope(7, 'admin'))
    await page.waitForTimeout(400)
    const back = await rag.evaluate(() => document.body.innerText)
    expect(back).toContain('七号账号')
    expect(back).not.toContain('八号账号')

    // —— 退出到无账号（独立访问形态）：两个账号的都不可见，三个桶并存 ——
    await page.evaluate(() => (window as any).postScope(null))
    await page.waitForTimeout(400)
    const shared = await rag.evaluate(() => document.body.innerText)
    expect(shared).not.toContain('七号账号')
    const keys = await page.evaluate(() => Object.keys(localStorage).sort())
    expect(keys).toEqual([BASE_KEY, scopeKey('7'), scopeKey('8')])
  })
})
