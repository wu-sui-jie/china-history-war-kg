/** SSE 客户端守护用例（第四轮复核 P1-8 / P2-10 / P1-12）。
 *
 * 覆盖分帧兼容（CRLF、多行 data、注释心跳、UTF-8 分块、EOF 残帧）与三类超时，
 * 以及错误分类是否与真实行为一致（旧实现把解析失败也记成 eof）。
 */

import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'vitest'

import { streamQuery } from '@/api/sse'
import { delay, frame, hangingResponse, installBrowserShims, sseResponse } from './shims'

const REQ = {
  session_id: 's1',
  question: '介绍一下长平之战。',
  history: [],
  filters: { dynasty: [], event_type: [] },
}

let originalFetch: typeof fetch

beforeEach(() => {
  installBrowserShims()
  originalFetch = globalThis.fetch
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

function collect() {
  const events: any[] = []
  const errors: string[] = []
  return {
    events,
    errors,
    options: {
      onEvent: (e: any) => events.push(e),
      onError: (m: string) => errors.push(m),
    },
  }
}

test('解析 CRLF 换行的事件', async () => {
  globalThis.fetch = (async () => sseResponse([
    'data: {"type":"session_start","session_id":"s1"}\r\n\r\n',
    'data: {"type":"answer","session_id":"s1","data":{"delta":"甲"}}\r\n\r\n',
  ])) as typeof fetch
  const c = collect()
  const outcome = await streamQuery(REQ, c.options)
  assert.equal(outcome, 'eof')
  assert.deepEqual(c.events.map((e) => e.type), ['session_start', 'answer'])
})

test('忽略注释心跳行与多行 data', async () => {
  globalThis.fetch = (async () => sseResponse([
    ': ping\n\n',
    'data: {"type":"status",\n',
    'data: "session_id":"s1"}\n\n',
    ': ping\n\n',
  ])) as typeof fetch
  const c = collect()
  await streamQuery(REQ, c.options)
  assert.deepEqual(c.events.map((e) => e.type), ['status'])
})

test('UTF-8 跨分块与 EOF 残帧', async () => {
  const payload = JSON.stringify({ type: 'answer', session_id: 's1', data: { delta: '赤壁' } })
  const bytes = new TextEncoder().encode(`data: ${payload}\n\n`)
  // 故意把多字节字符与帧尾切开
  const chunks = [
    new TextDecoder().decode(bytes.slice(0, 12)),
    new TextDecoder('utf-8', { fatal: false }).decode(bytes.slice(12, 20)),
  ]
  const tail = new TextDecoder().decode(bytes.slice(20))
  globalThis.fetch = (async () => sseResponse([...chunks, tail])) as typeof fetch
  const c = collect()
  await streamQuery(REQ, c.options)
  assert.equal(c.events.length, 1, `残帧应被 flush 出来，实际 ${JSON.stringify(c.events)}`)
  assert.equal(c.events[0].data.delta, '赤壁')
})

test('非法 JSON 归类为 protocol_error 且不中断其余帧', async () => {
  globalThis.fetch = (async () => sseResponse([
    'data: {不是 JSON}\n\n',
    frame({ type: 'done', session_id: 's1', data: { finish_reason: 'normal' } }),
  ])) as typeof fetch
  const c = collect()
  const outcome = await streamQuery(REQ, c.options)
  assert.equal(outcome, 'protocol_error')
  assert.equal(c.events.length, 1)
  assert.equal(c.events[0].type, 'done')
  assert.ok(c.errors.some((m) => m.includes('解析失败')))
})

test('HTTP 4xx 透出后端 JSON 错误信息', async () => {
  globalThis.fetch = (async () => new Response(
    JSON.stringify({ status: 'error', error_code: 'rate_limited', message: '请求过于频繁' }),
    { status: 429, headers: { 'Content-Type': 'application/json' } },
  )) as typeof fetch
  const c = collect()
  const outcome = await streamQuery(REQ, c.options)
  assert.equal(outcome, 'http_error')
  assert.ok(c.errors[0].includes('请求过于频繁'), c.errors[0])
})

test('用户取消归类为 aborted', async () => {
  const controller = new AbortController()
  globalThis.fetch = (async (_url: any, init: any) => {
    const signal: AbortSignal = init.signal
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
      })
    })
  }) as typeof fetch
  const c = collect()
  const pending = streamQuery(REQ, { ...c.options, signal: controller.signal })
  controller.abort()
  const outcome = await pending
  assert.equal(outcome, 'aborted')
})

test('建连超时归类为 connect_timeout（不被 idle/total 掩盖）', async () => {
  globalThis.fetch = (async (_url: any, init: any) => {
    const signal: AbortSignal = init.signal
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
      })
    })
  }) as typeof fetch
  const c = collect()
  const outcome = await streamQuery(REQ, {
    ...c.options,
    connectTimeoutMs: 30,
    idleTimeoutMs: 5000,
    totalTimeoutMs: 5000,
  })
  assert.equal(outcome, 'connect_timeout')
  assert.ok(c.errors[0].includes('连接超时'), c.errors[0])
})

test('空闲超时归类为 idle_timeout', async () => {
  const hanging = hangingResponse()
  globalThis.fetch = (async (_url: any, init: any) => {
    hanging.attachAbort(init?.signal)
    return hanging.resp
  }) as typeof fetch
  const c = collect()
  const pending = streamQuery(REQ, {
    ...c.options,
    connectTimeoutMs: 5000,
    idleTimeoutMs: 40,
    totalTimeoutMs: 5000,
  })
  // 先发一帧让连接进入 body 阶段，然后保持静默
  hanging.push(frame({ type: 'session_start', session_id: 's1' }))
  const outcome = await pending
  assert.equal(outcome, 'idle_timeout')
  assert.ok(c.errors[0].includes('长时间没有新内容'), c.errors[0])
})

test('整体超时归类为 total_timeout', async () => {
  const hanging = hangingResponse()
  globalThis.fetch = (async (_url: any, init: any) => {
    hanging.attachAbort(init?.signal)
    return hanging.resp
  }) as typeof fetch
  const c = collect()
  const pending = streamQuery(REQ, {
    ...c.options,
    connectTimeoutMs: 5000,
    idleTimeoutMs: 5000,
    totalTimeoutMs: 60,
  })
  // 持续有心跳（活动）但整体上限到点
  const ticker = setInterval(() => hanging.push(': ping\n\n'), 10)
  const outcome = await pending
  clearInterval(ticker)
  assert.equal(outcome, 'total_timeout')
})

test('网络错误与读取失败分别归类', async () => {
  globalThis.fetch = (async () => { throw new TypeError('Failed to fetch') }) as typeof fetch
  let c = collect()
  assert.equal(await streamQuery(REQ, c.options), 'network_error')

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(': ping\n\n'))
      controller.error(new Error('connection reset'))
    },
  })
  globalThis.fetch = (async () => new Response(stream, { status: 200 })) as typeof fetch
  c = collect()
  assert.equal(await streamQuery(REQ, c.options), 'parse_error')
  await delay(0)
})
