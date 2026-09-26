#!/usr/bin/env node
/**
 * 浏览器验收用的桩后端。
 *
 * 为什么需要它：Playwright 用例要连一个真实后端，而 CI 里没有 `data/`
 * （快照/索引约 210 MB，不入 Git），于是浏览器用例要么跑不了、要么只能靠
 * release workflow 的受限环境。
 *
 * 本桩服务提供前端真正依赖的四个接口（健康、词典、示例题、SSE 问答），
 * 数据是固定 fixture，因此：
 * - 普通 ci.yml 就能跑 desktop + mobile 全部浏览器用例；
 * - 断言针对**前端行为**（键盘、焦点陷阱、tabs、引用定位、live region），
 *   不依赖检索质量，天然稳定；
 * - 需要真实数据链路的验收仍由 release workflow 的 `npm run test:e2e` 覆盖
 *   （那里用 RAG_BASE_URL 指向真实服务）。
 *
 * 用法：
 *   node scripts/e2e-server.mjs --port 8125 [--dist dist] [--slow 0]
 */

import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const args = process.argv.slice(2)
const getArg = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}

const port = Number(getArg('--port', process.env.E2E_PORT || '8125'))
const distDir = path.resolve(getArg('--dist', 'dist'))
// 每帧之间的间隔（ms）：模拟流式，0 = 尽快发完
const slowMs = Number(getArg('--slow', '0'))
// 桩身份标记：运行器（e2e-run.mjs）用它确认"端口上跑的是本次启动的桩"
const VERSION = getArg('--marker', 'e2e-fixture')

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
}

const health = {
  status: 'ok',
  version: VERSION,
  vector_available: true,
  llm_available: true,
  load_error: null,
  meta: {
    version_selection: 'cli_explicit',
    text_mode: 'hybrid',
    graph_entities: 3,
    graph_relations: 2,
  },
}

const dicts = {
  status: 'ok',
  version: VERSION,
  data_version: VERSION,
  generated_at: '2026-09-16T00:00:00',
  dynasty: [
    { standard: '战国', aliases: ['东周·战国'] },
    { standard: '东汉', aliases: [] },
  ],
  event_type: ['战役', '政治事件'],
  sources: { file: 'dicts.json', counts: { dynasty: 2, event_type: 2 } },
}

const demo = {
  status: 'ok',
  version: VERSION,
  generated_at: '2026-09-16T00:00:00',
  source_run: 'e2e_fixture_run',
  counts: { bank_total: 3, candidates: 3, selected: 3 },
  notes: '浏览器验收 fixture（非真实评测结论）',
  examples: [
    {
      id: 'E2E-1',
      question: '介绍一下长平之战。',
      category: 'entity_intro',
      category_label: '实体介绍',
      capability: 'both',
      expect: { graph_min: 1, text_min: 1 },
      measured: { first_answer_ms: 120, finish_reason: 'normal' },
    },
    {
      id: 'E2E-2',
      question: '介绍一下赤壁之战。',
      category: 'entity_intro',
      category_label: '实体介绍',
      capability: 'both',
      expect: { graph_min: 1, text_min: 1 },
      measured: { first_answer_ms: 130, finish_reason: 'normal' },
    },
  ],
}

const ENTITIES = [
  { name: '长平之战', standard_name: '长平之战', type: '事件', dynasty: '战国', confidence: 'high' },
]

const CITATIONS = [
  {
    index: 1,
    evidence_id: 'ev_text_1',
    kind: 'raw_text',
    title: '长平之战（原文）',
    snippet: '秦使白起击赵，赵括代廉颇，大败于长平。',
  },
  {
    index: 2,
    evidence_id: 'ev_graph_1',
    kind: 'graph_relation',
    title: '长平之战 — 参战 — 白起',
    snippet: '白起（人物）参与长平之战（事件）。',
  },
]

const PANEL = {
  entity_cards: [
    {
      entity_id: 'e_changping',
      type: '事件',
      name: '长平之战',
      event_type: '战役',
      dynasty: '战国',
      start_date: '-260',
      description: '秦赵之间的决战，赵国主力被歼。',
    },
    {
      entity_id: 'p_baiqi',
      type: '人物',
      name: '白起',
      dynasty: '战国',
      description: '秦国名将，长平之战中任主将。',
    },
  ],
  subgraph: {
    nodes: [
      { id: 'e_changping', type: 'event', name: '长平之战', dynasty: '战国' },
      { id: 'p_baiqi', type: 'person', name: '白起', dynasty: '战国' },
    ],
    edges: [{ source: 'p_baiqi', target: 'e_changping', relation: '参战' }],
  },
  timeline: {
    groups: [
      {
        label: '战国',
        items: [{ event_id: 'e_changping', name: '长平之战', start_date: '-260', dynasty: '战国' }],
      },
    ],
  },
  map_points: [{ place_id: 'pl_changping', name: '长平', modern_name: '山西高平', longitude: 112.9, latitude: 35.8, events: ['长平之战'] }],
}

const ANSWER = '长平之战是战国后期秦赵之间的决战，赵国主力被歼[1]。秦国主将白起在此战中取得决定性胜利[2]。'

function sseFrame(payload) {
  return `data: ${JSON.stringify(payload)}\n\n`
}

/** 完整事件序列：与 server/sse.py 的产出顺序**逐帧对齐**。
 *
 * 顺序（server/sse.py 的 run_query）：session_start → status(entity_linking) → entities
 * → status(graph_search) → status(text_search) → graph_results → text_results
 * → status(fusion) → fusion → status(generating) → answer… → citations → panel → done。
 * 注意两个检索 status 都先于两个 results 帧（别把 graph_results 插在 status(text_search)
 * 之前，那与后端顺序不一致，会误导下一个读代码的人）。
 */
function scriptedFrames(sessionId) {
  const frames = [
    { type: 'session_start', session_id: sessionId, stage: 'start' },
    { type: 'status', session_id: sessionId, stage: 'entity_linking', data: { stage: 'entity_linking' } },
    { type: 'entities', session_id: sessionId, data: { entities: ENTITIES, candidates: [], question_type: 'entity_intro', rewritten_question: '长平之战 介绍', elapsed_ms: 12 } },
    { type: 'status', session_id: sessionId, stage: 'graph_search', data: { stage: 'graph_search' } },
    { type: 'status', session_id: sessionId, stage: 'text_search', data: { stage: 'text_search' } },
    { type: 'graph_results', session_id: sessionId, stage: 'graph_search', data: { evidence: [{ evidence_id: 'ev_graph_1', kind: 'graph_relation', content: { text: '白起参与长平之战' } }], hit_entities: ['e_changping'] } },
    { type: 'text_results', session_id: sessionId, stage: 'text_search', data: { evidence: [{ evidence_id: 'ev_text_1', kind: 'raw_text', content: { text: '秦使白起击赵' } }], mode: 'hybrid' } },
    { type: 'status', session_id: sessionId, stage: 'fusion', data: { stage: 'fusion' } },
    { type: 'fusion', session_id: sessionId, stage: 'fusion', data: { evidence_count: 2, conflicts: [], citation_index: [] } },
    { type: 'status', session_id: sessionId, stage: 'generating', data: { stage: 'generating' } },
  ]
  // 正文按句切分，模拟打字机
  for (const part of ANSWER.match(/[^。]+。?/g) || [ANSWER]) {
    frames.push({ type: 'answer', session_id: sessionId, stage: 'generating', data: { delta: part } })
  }
  frames.push({ type: 'citations', session_id: sessionId, stage: 'generating', data: { citations: CITATIONS, conflicts: [] } })
  frames.push({ type: 'panel', session_id: sessionId, stage: 'generating', data: PANEL })
  frames.push({ type: 'done', session_id: sessionId, data: { finish_reason: 'normal', model_used: 'e2e-fixture' } })
  return frames
}

function sendJson(res, body, status = 200) {
  const text = JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(text) })
  res.end(text)
}

async function serveStatic(res, urlPath) {
  const rel = urlPath === '/' ? 'index.html' : urlPath.replace(/^\/+/, '')
  let file = path.resolve(distDir, rel)
  if (!file.startsWith(distDir)) {
    res.writeHead(403).end('forbidden')
    return
  }
  if (!existsSync(file)) {
    // SPA 回退
    file = path.join(distDir, 'index.html')
    if (!existsSync(file)) {
      res.writeHead(404).end(`dist 未构建: ${distDir}`)
      return
    }
  }
  const data = await readFile(file)
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' })
  res.end(data)
}

async function handleQuery(req, res) {
  let raw = ''
  for await (const chunk of req) raw += chunk
  let sessionId = 'e2e-session'
  let question = ''
  try {
    const body = JSON.parse(raw || '{}')
    sessionId = body.session_id || sessionId
    question = String(body.question || '')
  } catch {
    sendJson(res, { status: 'error', error_code: 'invalid_request', message: 'JSON 解析失败' }, 400)
    return
  }
  if (!question.trim()) {
    sendJson(res, { status: 'error', error_code: 'invalid_request', message: '问题不能为空' }, 400)
    return
  }
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
  })
  for (const frame of scriptedFrames(sessionId)) {
    res.write(sseFrame(frame))
    if (slowMs > 0) await new Promise((r) => setTimeout(r, slowMs))
  }
  res.end()
}

/** 账号隔离用例的父页容器（见 scope-harness.html 路由）。 */
const SCOPE_HARNESS_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>scope harness</title></head>
<body style="margin:0">
<iframe id="rag" src="/" style="width:100vw;height:100vh;border:0"></iframe>
<script>
  window.postScope = (uid, role) => {
    const f = document.getElementById('rag');
    f.contentWindow.postMessage({ type: 'cw-user', uid: uid === null || uid === undefined ? null : String(uid), role: role || '' }, '/');
  };
  window.ready = new Promise((resolve) => {
    document.getElementById('rag').addEventListener('load', () => resolve(true), { once: true });
  });
</script>
</body></html>`

const server = createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${port}`)
  try {
    // 账号隔离用例（tests/e2e/user-scope-isolation.spec.ts）需要一张"父页"来扮演主应用：
    // 它把 RAG 页面放进 iframe，再从父页 postMessage 身份消息——与 RagAssistant.vue 的做法一致。
    // 直接跑真实主应用需要账号口令，桩页面能把这条链路单独验出来。
    if (req.method === 'GET' && url.pathname === '/scope-harness.html') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      res.end(SCOPE_HARNESS_HTML)
      return
    }
    if (req.method === 'GET' && url.pathname === '/api/health') return sendJson(res, health)
    if (req.method === 'GET' && url.pathname === '/api/dicts') return sendJson(res, dicts)
    if (req.method === 'GET' && url.pathname === '/api/demo/examples') return sendJson(res, demo)
    if (req.method === 'POST' && url.pathname === '/api/query') return await handleQuery(req, res)
    if (req.method === 'GET') return await serveStatic(res, url.pathname)
    sendJson(res, { status: 'error', message: 'not found' }, 404)
  } catch (err) {
    sendJson(res, { status: 'error', message: String(err) }, 500)
  }
})

server.listen(port, '127.0.0.1', () => {
  console.log(`[e2e-server] http://127.0.0.1:${port} （dist=${distDir}）`)
})
