#!/usr/bin/env node
/**
 * 契约级端到端检查（`npm run test:contract`）。
 *
 * 为什么不是 Playwright：本环境装不了浏览器依赖；但"前端能不能正确消费后端 SSE"
 * 这件事可以在 Node 里用同一份协议真实跑通——它验证的正是前端最脆弱的环节：
 * 事件顺序、心跳注释行、done 收尾、错误码与缓存命中。
 * 浏览器级用例（抽屉、焦点、渲染）配置见 docs/deploy.md 的"前端测试"一节，
 * 依赖就绪后可用 Playwright 直接复用本脚本的断言顺序。
 *
 * 用法：node scripts/e2e-check.mjs [--base http://127.0.0.1:8124] [--question 介绍一下长平之战。]
 */

const args = process.argv.slice(2)
const getArg = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}

const base = getArg('--base', process.env.RAG_BASE_URL || 'http://127.0.0.1:8000')
const question = getArg('--question', '介绍一下长平之战。')

const failures = []
const notes = []

function check(ok, label, detail = '') {
  if (ok) notes.push(`✔ ${label}`)
  else failures.push(`${label}${detail ? ` — ${detail}` : ''}`)
}

async function postQuery(sid, extra = {}) {
  const resp = await fetch(`${base}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ session_id: sid, question, ...extra }),
  })
  return resp
}

async function readEvents(resp) {
  const events = []
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let pings = 0
  const handle = (line) => {
    if (line.startsWith(':')) {
      pings += 1
      return
    }
    if (!line.startsWith('data: ')) return
    try {
      events.push(JSON.parse(line.slice(6)))
    } catch (err) {
      failures.push(`SSE 帧不是合法 JSON: ${String(err)}`)
    }
  }
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n')) >= 0) {
      let line = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 1)
      if (line.endsWith('\r')) line = line.slice(0, -1)
      handle(line)
    }
  }
  if (buffer) handle(buffer)
  return { events, pings }
}

function firstIndexOf(events, type) {
  return events.findIndex((e) => e.type === type)
}

async function main() {
  // 0) 健康检查：版本与发布可追溯字段
  const health = await fetch(`${base}/api/health`).then((r) => r.json())
  check(health.status === 'ok', 'health 返回 ok', JSON.stringify(health).slice(0, 200))
  check(typeof health.release_id === 'string' && health.release_id.length > 0,
    'health 暴露 release_id')
  check('demo_ready' in health, 'health 暴露 demo_ready')

  // 1) 正常链路
  const sid = `e2e-${Date.now()}`
  const resp = await postQuery(sid)
  check(resp.status === 200, 'POST /api/query 返回 200', `实际 ${resp.status}`)
  check((resp.headers.get('content-type') || '').startsWith('text/event-stream'),
    'Content-Type 是 text/event-stream')
  check((resp.headers.get('x-accel-buffering') || '') === 'no',
    '带反缓冲头 X-Accel-Buffering: no')

  const { events, pings } = await readEvents(resp)
  check(events.length > 0, '收到事件')
  check(events[0]?.type === 'session_start', '首事件是 session_start', events[0]?.type)
  check(events[events.length - 1]?.type === 'done', '末事件是 done',
    events[events.length - 1]?.type)
  const answer = events.find((e) => e.type === 'answer')
  check(!!answer, '收到 answer 事件')
  const citations = events.find((e) => e.type === 'citations')
  check(!!citations, '收到 citations 事件')
  check(!events.some((e) => e.type === 'thinking'),
    '默认不外发原始 reasoning（thinking）')
  const done = events[events.length - 1]?.data || {}
  check(['normal', 'refused', 'degraded'].includes(done.finish_reason),
    'finish_reason 属于可用终态', String(done.finish_reason))
  if (pings) notes.push(`（本次收到 ${pings} 条心跳注释行）`)

  // 2) 缓存命中：同题再问一次
  const sid2 = `e2e-${Date.now()}-2`
  const resp2 = await postQuery(sid2)
  const second = await readEvents(resp2)
  const done2 = second.events[second.events.length - 1]?.data || {}
  check(done2.cache_hit === true, '同题第二次请求命中缓存', JSON.stringify(done2))

  // 3) 参数边界：4xx 而不是 500
  const long = await postQuery('e2e-long', { question: 'q'.repeat(600) })
  check(long.status === 400, '超长问题返回 400', `实际 ${long.status}`)
  const bad = await fetch(`${base}/api/query`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{不是 JSON',
  })
  check(bad.status === 400, '非法 JSON 返回 400', `实际 ${bad.status}`)

  // 4) 同源托管（若已构建前端）
  const page = await fetch(`${base}/`)
  check(page.status === 200, '同源托管返回页面', `实际 ${page.status}`)
  const html = await page.text()
  check(!/assets\/echarts-[^"]+\.js/.test(html), 'index.html 未预加载 ECharts')
  check(!/assets\/china-map-[^"]+\.js/.test(html), 'index.html 未预加载地图')

  console.log(notes.join('\n'))
  if (failures.length) {
    console.error('\n失败项：')
    for (const f of failures) console.error(`  - ${f}`)
    process.exit(1)
  }
  console.log('\n契约级端到端检查全部通过')
}

main().catch((err) => {
  console.error(`端到端检查异常：${String(err)}`)
  console.error(`（服务是否已启动？当前 base=${base}）`)
  process.exit(1)
})
