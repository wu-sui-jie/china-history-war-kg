/** 身份头接线（第 12 轮审查 P1-1）。
 *
 * 光有"服务端会验签"不够：如果前端没把 token 放进请求头，开启 RAG_REQUIRE_AUTH 后
 * 所有问答与首屏接口都会 401——功能整体不可用。这里按最短路径钉住三件事：
 *   1. setAuthToken 之后，SSE 请求与 GET 请求都带上 `Token` 头；
 *   2. 没有 token（独立访问 :8000）时不带这个头，与服务端未开启校验的行为一致；
 *   3. 非法 token 被洗掉而不是原样进请求头。
 *
 * 断言的是真实发出的 Request（拦截全局 fetch），不是"某函数被调用过"——
 * 后者在参数写错时照样通过。
 */

import assert from 'node:assert/strict'
import { afterEach, test } from 'vitest'

import { getAuthToken, normalizeToken, setAuthToken, AUTH_HEADER } from '@/api/authToken'
import { getJson } from '@/api/http'
import { streamQuery } from '@/api/sse'

const TOKEN = 'header.payload.signature'

let captured: Request[] = []
const originalFetch = globalThis.fetch

function stubFetch(body: string, contentType = 'application/json') {
  captured = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    // 相对路径（/api/health）在 Node 的 Request 里需要显式基准 URL，否则 "Invalid URL"
    const target = typeof input === 'string' ? new URL(input, 'http://localhost') : input
    const request = new Request(target as RequestInfo, init)
    captured.push(request)
    return new Response(body, { status: 200, headers: { 'Content-Type': contentType } })
  }) as typeof fetch
}

afterEach(() => {
  globalThis.fetch = originalFetch
  setAuthToken('')
})

function tokenHeaderOf(request: Request): string | null {
  return request.headers.get(AUTH_HEADER)
}

test('带 token 时 SSE 请求与 GET 请求都发送身份头', async () => {
  setAuthToken(TOKEN)
  assert.equal(getAuthToken(), TOKEN)

  stubFetch('{"status":"ok"}')
  await getJson('/api/health')
  assert.equal(tokenHeaderOf(captured[0]), TOKEN, 'GET 请求应带 Token 头')

  const frames = 'data: {"type":"done","session_id":"s","data":{"finish_reason":"normal"}}\n\n'
  stubFetch(frames, 'text/event-stream; charset=utf-8')
  streamQuery({ session_id: 'ou_x:oc_y', question: '介绍一下长平之战' } as never, {
    onEvent: () => {},
    onError: () => {},
  })
  // 等一次微任务，让 fetch 真正发出
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(tokenHeaderOf(captured[0]), TOKEN, 'SSE 请求应带 Token 头')
})

test('没有 token 时不发送身份头（独立访问与未开启校验的场景）', async () => {
  setAuthToken('')

  stubFetch('{"status":"ok"}')
  await getJson('/api/health')

  assert.equal(tokenHeaderOf(captured[0]), null)
})

test('非法 token 被洗掉，不会原样进入请求头', async () => {
  // 换行会在 fetch 层报错（甚至可被用来注入额外请求头）；空格同样非法
  for (const bad of ['a.b.c\nX-Injected: 1', 'has space', 'short']) {
    setAuthToken(bad)
    assert.equal(getAuthToken(), '', `应被洗成空：${JSON.stringify(bad)}`)
  }

  stubFetch('{"status":"ok"}')
  await getJson('/api/health')
  assert.equal(tokenHeaderOf(captured[0]), null)
})

test('normalizeToken 只放行 JWT 形状', () => {
  assert.equal(normalizeToken(TOKEN), TOKEN)
  assert.equal(normalizeToken('  ' + TOKEN + '  '), TOKEN, '两侧空白应去掉')
  assert.equal(normalizeToken(''), '')
  assert.equal(normalizeToken(null), '')
  assert.equal(normalizeToken({ token: TOKEN }), '')
  assert.equal(normalizeToken('x'.repeat(5000)), '', '超长值必须拒绝')
})
