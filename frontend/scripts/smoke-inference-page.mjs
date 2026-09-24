/**
 * 旧问答助手的浏览器冒烟（第 7 轮 W2 的验证脚本）。
 *
 * 为什么需要它：模板拆进子组件、样式从"父组件 scoped"改成"命名空间全局"，类型检查与单测
 * 都看不出"样式掉没掉""空态还显不显示"——只有真浏览器 + 真构建产物能看出来。它断言的是
 * **计算样式**与**交互结果**，不是"元素存在"。写完这次拆分就是靠它抓到"isFirstLoad 忘了
 * 复位、空会话一直停在'正在加载聊天记录'"这个回归。
 *
 * 前置：先在 frontend/ 下 `pnpm build`（脚本托管 dist/）。
 * 运行：cd RAG/frontend && node ../../frontend/scripts/smoke-inference-page.mjs
 *   （本机只在 RAG/frontend 里装了 playwright-core，脚本会从这里解析；没装的话先
 *    `cd RAG/frontend && npm i`，或设置 PLAYWRIGHT_CORE 指向 playwright-core 的路径）
 * 产物：frontend/.smoke-inference*.png 两张截图（看板与对话），跑完可自行删除。
 */

import { createRequire } from 'node:module'
import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))          // frontend/scripts
const FRONTEND_DIR = path.resolve(HERE, '..')                      // frontend/
const DIST = path.join(FRONTEND_DIR, 'dist')
const PORT = Number(process.env.SMOKE_PORT || 8199)

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.json': 'application/json; charset=utf-8',
}

const SSE_FRAMES = [
  ['entities', { entities: ['赤壁', '曹操'] }],
  ['thinking', { content: '先定位战场：**赤壁**在长江南岸。' }],
  ['answer', { content: '赤壁之战发生在今湖北赤壁市一带，' }],
  ['answer', { content: '是**曹操**南下与孙刘联军的关键一战。' }],
  ['kg_data', { kg_data: { nodes: [{ id: 'e1', name: '赤壁之战', type: 'Event' }], lines: [] }, relations_text: '赤壁之战 —发生地→ 赤壁' }],
  ['complete', { kg_data: { nodes: [{ id: 'e1', name: '赤壁之战', type: 'Event' }], lines: [] }, relations_text: '赤壁之战 —发生地→ 赤壁' }],
]

const server = createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`)
  const json = (body) => {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(body))
  }

  if (url.pathname === '/api/userinfo') {
    return json({ code: 200, data: { id: 3, account: 'smoke', name: '冒烟账号', role: 'editor' } })
  }
  if (url.pathname === '/user/menu') {
    return json({ code: 200, data: [
      { id: '/workspace/dashboard', icon: 'i', title: '首页仪表盘' },
      { id: '/knowledge', icon: 'i', title: '知识图谱', children: [
        { id: '/knowledge/inference', icon: 'i', title: '历史问答助手' },
        { id: '/knowledge/rag', icon: 'i', title: 'RAG 智能问答' },
      ] },
    ] })
  }
  if (url.pathname === '/user/permission') return json({ code: 200, data: [] })

  if (url.pathname === '/api/ai/inference/stream') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    })
    let i = 0
    const timer = setInterval(() => {
      if (i >= SSE_FRAMES.length) {
        clearInterval(timer)
        res.end()
        return
      }
      const [status, payload] = SSE_FRAMES[i++]
      res.write(`data: ${JSON.stringify({ status, ...payload })}\n\n`)
    }, 80)
    req.on('close', () => clearInterval(timer))
    return
  }

  // 静态资源：vite base 是 /static/（生产由 nginx 按该前缀发文件），这里等价映射
  const rel = url.pathname.startsWith('/static/')
    ? url.pathname.slice('/static'.length)
    : (url.pathname === '/' ? '/index.html' : url.pathname)
  let filePath = path.join(DIST, rel)
  try {
    const info = await stat(filePath)
    if (info.isDirectory()) filePath = path.join(filePath, 'index.html')
    const body = await readFile(filePath)
    res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' })
    res.end(body)
  } catch {
    res.writeHead(404)
    res.end('not found')
  }
})

await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve))

const nodeRequire = createRequire(import.meta.url)
function loadPlaywrightCore() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    '../../RAG/frontend/node_modules/playwright-core/index.js',
  ].filter(Boolean)
  for (const candidate of candidates) {
    try {
      return nodeRequire(candidate)
    } catch {
      // 换下一个候选
    }
  }
  throw new Error('找不到 playwright-core：请在 RAG/frontend 下 npm i，或用 PLAYWRIGHT_CORE 指定路径')
}
const { chromium } = loadPlaywrightCore()
// 本机只装了完整 chromium（没有 headless shell），显式指定可执行文件
// 没装 headless shell 的环境（本机只装了完整 chromium）可用 CHROME_PATH 指定可执行文件
const browser = await chromium.launch(
  process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
)
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const failures = []
const check = (name, ok, extra = '') => {
  console.log(`${ok ? '✓' : '✗'} ${name}${extra ? ` — ${extra}` : ''}`)
  if (!ok) failures.push(name)
}

// 预置登录态（pinia-plugin-persistedstate 的 key 就是 store id）
await page.addInitScript(() => {
  localStorage.setItem('user', JSON.stringify({
    token: 'smoke-token',
    userInfo: { id: 3, account: 'smoke', name: '冒烟账号', role: 'editor' },
    permissions: [],
    menus: [],
  }))
  localStorage.setItem('chatHistory:u3', JSON.stringify([{
    id: 1, title: '冒烟历史会话', lastTime: Date.now(),
    messages: [{ role: 'user', content: '历史提问一条', time: Date.now(), fromKg: true }],
  }]))
})

const style = (selector, prop) =>
  page.$eval(selector, (el, p) => getComputedStyle(el)[p], prop)

page.on('console', (msg) => console.log(`  [页面控制台] ${msg.type()}: ${msg.text().slice(0, 200)}`))
page.on('pageerror', (err) => console.log(`  [页面异常] ${err.message.slice(0, 300)}`))
await page.goto(`http://127.0.0.1:${PORT}/#/knowledge/inference`, { waitUntil: 'networkidle' })
try {
  await page.waitForSelector('.chat-sidebar', { timeout: 10000 })
} catch (err) {
  console.log(`诊断：URL=${page.url()}`)
  console.log(`诊断：正文=${(await page.textContent('body'))?.slice(0, 300)}`)
  throw err
}
await page.waitForTimeout(600)   // 等 loadChatHistory 的 500ms 首屏延时

// —— 左侧会话列表：宽度来自 index.css（命名空间生效的证明）——
check('侧边栏宽度 230px（index.css 生效）', (await style('.chat-sidebar', 'width')) === '230px',
      await style('.chat-sidebar', 'width'))
check('历史会话渲染出来了', (await page.textContent('.chat-title'))?.includes('冒烟历史会话'))
check('历史消息渲染出来了', (await page.textContent('.message-item'))?.includes('历史提问一条'))

// —— 用户消息气泡的样式（拆分前由父组件 scoped 提供）——
const bubbleBg = await page.$eval('.user-message .message-content', (el) => getComputedStyle(el).backgroundColor)
check('用户消息气泡有背景色（样式没掉）', bubbleBg !== 'rgba(0, 0, 0, 0)' && bubbleBg !== 'transparent', bubbleBg)

// —— 输入区：底部固定布局来自 index.css ——
check('输入区存在', await page.$('.input-container') !== null)
check('输入提示文案在', (await page.textContent('.input-actions'))?.includes('Enter 发送'))

// —— 提问 → SSE 流式回答 → 图谱区块 → 实体卡片 ——
await page.fill('textarea', '赤壁之战在哪？')
await page.click('.send-button')
await page.waitForSelector('.kg-visualization', { timeout: 10000 })
await page.waitForTimeout(400)

const answerText = await page.textContent('.ai-message:last-of-type .markdown-body')
check('AI 回答按 Markdown 渲染（含 <strong>）',
      (await page.$$eval('.ai-message .markdown-body strong', (els) => els.map((e) => e.textContent))).includes('曹操'))
check('思考过程区块显示', await page.$('.thinking-section') !== null)
check('知识图谱区块显示', await page.$('.kg-graph-wrapper') !== null)
check('问题中的实体卡片显示', (await page.textContent('.entity-card'))?.includes('赤壁'))
check('回答正文包含流式内容', (answerText || '').includes('关键一战'), (answerText || '').slice(0, 40))

await page.screenshot({ path: path.join(FRONTEND_DIR, '.smoke-inference-chat.png') })

// —— 点击图谱里的节点 → 详情抽屉（lay-layer teleport 到 body 后样式是否还在）——
// 子图只有一个节点，力导向布局会把它放在容器中心
const box = await page.locator('.kg-graph').first().boundingBox()
if (box) {
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  await page.waitForTimeout(500)
}
const drawerOpened = await page.$('.kg-node-detail') !== null
if (drawerOpened) {
  const padding = await style('.kg-node-detail', 'padding')
  check('节点抽屉打开且样式生效（teleport 后 scoped 仍匹配）', padding === '22px', `padding=${padding}`)
} else {
  // 力导向布局未必正好命中：退一步校验"抽屉的 scoped 规则确实进了产物"
  const cssFiles = await page.evaluate(() =>
    [...document.querySelectorAll('link[rel=stylesheet]')].map((l) => l.getAttribute('href')))
  const cssText = await page.evaluate(async (hrefs) => {
    const parts = await Promise.all(hrefs.map(async (h) => (await fetch(h)).text()))
    return parts.join('\n')
  }, cssFiles)
  check('抽屉 scoped 规则进了构建产物（[data-v-*] 命中）', /\.kg-node-detail\[data-v-[0-9a-f]+\]/.test(cssText))
}

// —— 收起/展开图谱（派发 kg-toggle，页面做侧边栏保护）——
await page.click('.kg-header')
await page.waitForTimeout(150)
check('图谱可收起', await page.$('.kg-graph-wrapper') === null)
await page.click('.kg-header')
await page.waitForTimeout(150)
check('图谱可再展开', await page.$('.kg-graph-wrapper') !== null)

// —— 新建会话（子组件派发 create → 页面重置视图）——
await page.click('.new-chat-button')
await page.waitForTimeout(200)
check('新建会话后回到空态提示', (await page.textContent('.message-container'))?.includes('开始新的对话') ||
      (await page.textContent('.message-container'))?.includes('常用提示'))

// —— 导出按钮存在（不做真实下载断言）——
check('导出入口在', await page.$('.export-button') !== null)

const persistKey = await page.evaluate(() => localStorage.getItem('chatHistory:u3'))
check('问答历史仍按账号分桶落盘', !!persistKey && persistKey.includes('赤壁'))

await page.screenshot({ path: path.join(FRONTEND_DIR, '.smoke-inference.png'), fullPage: false })
await browser.close()
server.close()
console.log(failures.length ? `\n✗ ${failures.length} 项未通过：${failures.join('、')}` : '\n✓ 全部通过')
process.exit(failures.length ? 1 : 0)
