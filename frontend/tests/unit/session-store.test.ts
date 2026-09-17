/** 会话状态机守护用例（第四轮复核 P1-9 / P1-10 / P1-11 / P2-11 / P1-12）。
 *
 * 直接驱动真实的 Pinia store，只把网络边界（globalThis.fetch）替换成可控 SSE 流，
 * 因此覆盖的是"解析 → 状态转移 → 历史装配 → 持久化"的完整链路，而不是替身逻辑。
 */

import assert from 'node:assert/strict'
import { afterEach, beforeAll, beforeEach, test } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSessionStore } from '@/stores/session'
import { frame, hangingResponse, installBrowserShims } from './shims'

interface StreamHandle {
  push: (payload: unknown) => void
  raw: (text: string) => void
  close: () => void
  aborts: () => number
  /** 最近一次 /api/query 的请求体（纠正 payload 断言用） */
  lastBody: () => any
}

let lastStream: StreamHandle | null = null
const originalFetch = globalThis.fetch

function installStream(): StreamHandle {
  const hanging = hangingResponse()
  let aborts = 0
  let lastBody: any = null
  const handle: StreamHandle = {
    push: (payload) => hanging.push(frame(payload)),
    raw: (text) => hanging.push(text),
    close: () => hanging.close(),
    aborts: () => aborts,
    lastBody: () => lastBody,
  }
  lastStream = handle
  globalThis.fetch = (async (url: any, init: any) => {
    if (typeof url === 'string' && url.includes('/api/')) {
      if (url.includes('/api/query')) {
        try { lastBody = JSON.parse(init.body) } catch { lastBody = null }
      }
      const signal: AbortSignal | undefined = init?.signal
      signal?.addEventListener('abort', () => { aborts += 1 })
      hanging.attachAbort(signal)
      return hanging.resp
    }
    return new Response('{}', { status: 200 })
  }) as typeof fetch
  return handle
}

async function settle(): Promise<void> {
  // 让微任务与定时器都跑一轮
  await new Promise((r) => setTimeout(r, 0))
  await new Promise((r) => setTimeout(r, 0))
}

beforeAll(() => {
  // Node 里 crypto.randomUUID 已存在；只补 localStorage/window
})

beforeEach(() => {
  installBrowserShims()
  setActivePinia(createPinia())
})

afterEach(() => {
  globalThis.fetch = originalFetch
  lastStream = null
})

test('error 事件之后 normal done 不能把这一轮标成 completed', async () => {
  const stream = installStream()
  const store = useSessionStore()
  await store.sendQuestion('介绍一下长平之战。')
  const turn = store.messages[1] as any

  stream.push({ type: 'answer', session_id: 's', data: { delta: '半截' } })
  stream.push({ type: 'error', session_id: 's', data: { error_code: 'internal', message: '内部错误' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream.close()
  await store.whenIdle()

  assert.equal(turn.doneSeen, true)
  assert.notEqual(turn.turnStatus, 'completed', '带错误的轮次不得成为 completed')
  assert.equal(turn.turnStatus, 'interrupted')
  assert.ok(turn.error)
  assert.deepEqual(store.history, [], '带错误的轮次不得进入多轮历史')
})

test('error 且无正文时收敛为 failed', async () => {
  const stream = installStream()
  const store = useSessionStore()
  await store.sendQuestion('介绍一下赤壁之战。')
  const turn = store.messages[1] as any
  stream.push({ type: 'error', session_id: 's', data: { message: '内部错误' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream.close()
  await store.whenIdle()
  assert.equal(turn.turnStatus, 'failed')
  assert.equal(store.history.length, 0)
})

test('done(cancelled) 收敛为 cancelled 且不入历史', async () => {
  const stream = installStream()
  const store = useSessionStore()
  await store.sendQuestion('介绍一下官渡之战。')
  const turn = store.messages[1] as any
  stream.push({ type: 'answer', session_id: 's', data: { delta: '片段' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'cancelled' } })
  stream.close()
  await store.whenIdle()
  assert.equal(turn.turnStatus, 'cancelled')
  assert.equal(store.history.length, 0)
})

test('done 之后的业务帧被忽略并计入协议错误', async () => {
  const stream = installStream()
  const store = useSessionStore()
  await store.sendQuestion('介绍一下长平之战。')
  const turn = store.messages[1] as any
  stream.push({ type: 'answer', session_id: 's', data: { delta: '完整回答' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream.push({ type: 'answer', session_id: 's', data: { delta: '不该追加' } })
  stream.close()
  await store.whenIdle()
  assert.equal(turn.answer, '完整回答')
  assert.equal(turn.turnStatus, 'completed')
  assert.equal(turn.protocolErrors, 1)
})

test('重复 done 幂等', async () => {
  const stream = installStream()
  const store = useSessionStore()
  await store.sendQuestion('介绍一下长平之战。')
  const turn = store.messages[1] as any
  stream.push({ type: 'answer', session_id: 's', data: { delta: '回答' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'failed' } })
  stream.close()
  await store.whenIdle()
  assert.equal(turn.turnStatus, 'completed')
  assert.equal(turn.finishReason, 'normal')
})

test('completed 轮次进入历史，failed 轮次被排除', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下长平之战。')
  stream.push({ type: 'answer', session_id: 's', data: { delta: '长平之战是…' } })
  stream.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream.close()
  await store.whenIdle()

  const stream2 = installStream()
  await store.sendQuestion('第二个问题')
  stream2.push({ type: 'error', session_id: 's', data: { message: '内部错误' } })
  stream2.push({ type: 'done', session_id: 's', data: { finish_reason: 'failed' } })
  stream2.close()
  await store.whenIdle()

  const history = store.history
  assert.equal(history.length, 2)
  assert.equal(history[0].content, '介绍一下长平之战。')
  assert.equal(history[1].role, 'assistant')
})

test('活动流期间重试是空操作，不会开出第二条流', async () => {
  const store = useSessionStore()
  const first = installStream()
  await store.sendQuestion('介绍一下长平之战。')
  first.push({ type: 'answer', session_id: 's', data: { delta: '流式中' } })
  await settle()
  const turn1 = store.messages[1] as any
  assert.equal(turn1.turnStatus, 'streaming')

  store.retryTurn(turn1)          // 界面不会给出这个入口，store 也必须兜住
  await settle()
  assert.equal(store.messages.filter((m) => m.role === 'assistant').length, 1)
  assert.equal(turn1.turnStatus, 'streaming')

  first.close()
  await store.whenIdle()
})

test('重试沿用原问题并取代旧轮：历史使用新回答', async () => {
  const store = useSessionStore()
  const first = installStream()
  await store.sendQuestion('介绍一下长平之战。')
  first.push({ type: 'answer', session_id: 's', data: { delta: '旧的半截' } })
  await settle()
  const turn1 = store.messages[1] as any

  store.cancelStream()            // 用户点"停止"
  await store.whenIdle()
  assert.equal(turn1.turnStatus, 'cancelled')
  assert.ok(first.aborts() >= 1, '取消必须真正 abort 掉旧流')

  const second = installStream()
  store.retryTurn(turn1)
  await settle()
  const turn2 = store.messages[store.messages.length - 1] as any
  assert.equal(turn2.question, '介绍一下长平之战。', '重试必须沿用原问题')
  assert.equal(turn1.supersededBy, turn2.id)

  second.push({ type: 'answer', session_id: 's', data: { delta: '新的回答' } })
  second.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  second.close()
  await store.whenIdle()

  assert.equal(turn2.turnStatus, 'completed')
  // 历史里用的是重试后的回答，而不是被取代的旧轮
  const history = store.history
  assert.equal(history.length, 2)
  assert.equal(history[1].content, '新的回答')
})

test('串行链保证：并发调用 sendQuestion 时前一条先收尾', async () => {
  const store = useSessionStore()
  const stream = installStream()
  const p1 = store.sendQuestion('第一个问题')
  const p2 = store.sendQuestion('第二个问题')
  stream.close()
  await Promise.all([p1, p2])
  await store.whenIdle()
  await settle()
  const assistants = store.messages.filter((m) => m.role === 'assistant') as any[]
  assert.equal(assistants.length, 2)
  assert.equal(assistants[0].turnStatus, 'cancelled')
})

test('流式正文节流写入 localStorage，刷新后恢复为 interrupted', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下赤壁之战。')
  stream.push({ type: 'answer', session_id: 's', data: { delta: '半截正文' } })
  await new Promise((r) => setTimeout(r, 900))    // 等节流落盘
  const raw = localStorage.getItem('ragv5-session-v2')
  assert.ok(raw && raw.includes('半截正文'), '流式正文应被节流落盘')

  // 模拟刷新：用同一份 localStorage 重建 store
  setActivePinia(createPinia())
  const revived = useSessionStore()
  const assistant = revived.messages[1] as any
  assert.equal(assistant.turnStatus, 'interrupted')
  assert.equal(assistant.answer, '半截正文')
  assert.equal(revived.history.length, 0, '中断轮不得进入历史')
  stream.close()
})

test('schemaVersion 高于当前时隔离原值并新建会话', async () => {
  localStorage.setItem('ragv5-session-v2', JSON.stringify({
    schemaVersion: 99,
    sessionId: 'future',
    messages: [{ role: 'user', question: '未来结构' }],
  }))
  setActivePinia(createPinia())
  const store = useSessionStore()
  assert.equal(store.messages.length, 0)
  assert.notEqual(store.sessionId, 'future')
  const quarantined = localStorage.getItem('ragv5-session-quarantine')
  assert.ok(quarantined && quarantined.includes('schemaVersion=99'))
})

test('旧版本（无 schemaVersion）会话按迁移读取', async () => {
  localStorage.setItem('ragv3-session-v1', JSON.stringify({
    sessionId: 'legacy',
    messages: [
      { id: 'u1', role: 'user', question: '旧问题', filters: { dynasty: [], event_type: [] } },
      { id: 'a1', role: 'assistant', question: '旧问题', answer: '旧回答',
        finished: true, finishReason: 'normal', streaming: false, cancelled: false },
    ],
  }))
  setActivePinia(createPinia())
  const store = useSessionStore()
  assert.equal(store.sessionId, 'legacy')
  assert.equal(store.messages.length, 2)
  assert.equal((store.messages[1] as any).turnStatus, 'completed')
  assert.equal(store.history.length, 2)
})

test('损坏的持久化数据被隔离而不是静默丢弃', async () => {
  localStorage.setItem('ragv5-session-v2', '{不是 JSON')
  setActivePinia(createPinia())
  const store = useSessionStore()
  assert.equal(store.messages.length, 0)
  assert.ok(localStorage.getItem('ragv5-session-quarantine'))
})

test('取消流后状态为 cancelled 且可再次提问', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下长平之战。')
  stream.push({ type: 'answer', session_id: 's', data: { delta: '片段' } })
  store.cancelStream()
  await store.whenIdle()
  const turn = store.messages[1] as any
  assert.equal(turn.turnStatus, 'cancelled')

  const stream2 = installStream()
  await store.sendQuestion('再问一次')
  stream2.push({ type: 'answer', session_id: 's', data: { delta: '新回答' } })
  stream2.push({ type: 'done', session_id: 's', data: { finish_reason: 'normal' } })
  stream2.close()
  await store.whenIdle()
  assert.equal(store.messages.filter((m) => m.role === 'assistant').length, 2)
})

// ---------------------------------------------------------------- 纠正 payload（P1-3）
function _assistantWithSameNameEntities(store: any): any {
  const turn = store.messages.filter((m: any) => m.role === 'assistant').pop()
  turn.entities = [
    { name: '井陉之战', standard_name: '井陉之战', type: '事件',
      entity_id: 'event_zhan', dynasty: '战国' },
  ]
  turn.candidates = [{
    mention: '井陉之战',
    entity_type: '事件',
    options: [
      { name: '井陉之战', standard_name: '井陉之战', entity_id: 'event_zhan',
        dynasty: '战国', entity_type: '事件' },
      { name: '井陉之战', standard_name: '井陉之战', entity_id: 'event_han',
        dynasty: '西汉', entity_type: '事件' },
    ],
  }]
  return turn
}

test('replace 纠正携带源 ID 与用户选中的目标 ID', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下井陉之战。')
  await settle()
  const turn = _assistantWithSameNameEntities(store)
  const target = turn.candidates[0].options[1]     // 用户选“西汉”那条

  store.correctEntity(turn, { action: 'replace', option: target, entity: turn.entities[0] })
  await settle()

  const body = stream.lastBody()
  const item = body.corrected_entities[0]
  assert.equal(item.action, 'replace')
  assert.equal(item.source_entity_id, 'event_zhan', '源实体必须是被替换的那条')
  assert.equal(item.replacement_entity_id, 'event_han', '目标必须是用户选中的那条')
  assert.equal(item.entity_id, undefined, 'replace 不再发送含混的 entity_id')
  stream.close()
  await store.whenIdle()
})

test('不同候选产生不同的纠正 payload', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下井陉之战。')
  await settle()
  const turn = _assistantWithSameNameEntities(store)

  store.correctEntity(turn, { action: 'replace',
                              option: turn.candidates[0].options[0],
                              entity: turn.entities[0] })
  await settle()
  const first = stream.lastBody().corrected_entities[0]
  stream.close()
  await store.whenIdle()

  const stream2 = installStream()
  const turn2 = _assistantWithSameNameEntities(store)
  store.correctEntity(turn2, { action: 'replace',
                               option: turn2.candidates[0].options[1],
                               entity: turn2.entities[0] })
  await settle()
  const second = stream2.lastBody().corrected_entities[0]
  assert.notDeepEqual(first, second, '选不同候选必须产生不同请求')
  assert.equal(second.replacement_entity_id, 'event_han')
  stream2.close()
  await store.whenIdle()
})

test('add / remove 纠正的字段口径', async () => {
  const store = useSessionStore()
  const stream = installStream()
  await store.sendQuestion('介绍一下井陉之战。')
  await settle()
  const turn = _assistantWithSameNameEntities(store)

  store.correctEntity(turn, { action: 'add', option: turn.candidates[0].options[1],
                              candidate: turn.candidates[0] })
  await settle()
  const add = stream.lastBody().corrected_entities[0]
  assert.equal(add.action, 'add')
  assert.equal(add.name, '井陉之战')
  assert.equal(add.entity_type, '事件')
  assert.equal(add.replacement_entity_id, 'event_han')
  assert.ok(!('source_entity_id' in add) || add.source_entity_id === undefined)
  stream.close()
  await store.whenIdle()

  const stream2 = installStream()
  const turn2 = _assistantWithSameNameEntities(store)
  store.correctEntity(turn2, { action: 'remove', entity: turn2.entities[0] })
  await settle()
  const remove = stream2.lastBody().corrected_entities[0]
  assert.equal(remove.action, 'remove')
  assert.equal(remove.source_entity_id, 'event_zhan')
  assert.ok(!remove.name && !remove.replacement_entity_id)
  stream2.close()
  await store.whenIdle()
})
