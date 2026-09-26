/** 装配级回归：主应用发身份消息 → store 真的换桶（main.ts 与 store 的接缝）。
 *
 * 为什么单独有这一条：`installHostUserBridge` 与 `applyUserScope` 各自单测都过，
 * 但**合起来**有一条硬约束——bridge 若先把 activeUid 设为新值再回调 store 的
 * applyUserScope，后者的"同 uid 即空操作"守卫一看 uid 已经相等就直接返回，
 * 桶就永远换不过去（表现：两个账号看到的记录一模一样）。
 *
 * 这条用例刻意照 `main.ts` 的装配方式接线，任何一侧改动导致接缝失效都会红。
 */

import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSessionStore } from '@/stores/session'
import { getActiveUid, installHostUserBridge, setActiveUid, SESSION_STORAGE_KEY } from '@/utils/userScope'
import { installBrowserShims } from './shims'

const ORIGIN = 'http://localhost:3000'

let off: (() => void) | undefined

beforeEach(() => {
  installBrowserShims()
  setActiveUid(null)
  setActivePinia(createPinia())
})

// 每个用例都要解绑：桥装在 window 上，不摘会在后续用例里累积，
// 上一个用例的 store 也会被回调（换桶有副作用，会造成用例间互相污染）
afterEach(() => {
  off?.()
  off = undefined
})

function userMessage(uid: unknown) {
  return new MessageEvent('message', { data: { type: 'cw-user', uid }, origin: ORIGIN })
}

function keysWithTitle(title: string): string[] {
  const hits: string[] = []
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i) as string
    if ((localStorage.getItem(key) || '').includes(title)) hits.push(key)
  }
  return hits
}

test('照 main.ts 接线：收到账号消息后按账号存，不再写共享桶', () => {
  const store = useSessionStore()
  off = installHostUserBridge((scope) => store.applyUserScope(scope), ORIGIN)

  // 消息还没来（独立访问形态）：写共享桶
  store.renameSession(store.sessionId, '共享桶的会话')
  assert.deepEqual(keysWithTitle('共享桶的会话'), [SESSION_STORAGE_KEY])

  // 主应用发来账号 7
  window.dispatchEvent(userMessage('7'))
  assert.equal(getActiveUid(), '7', 'store 换桶后 activeUid 应跟着变')

  // 换桶后新建的会话落在账号桶；共享桶里的那条不该被搬过来
  store.renameSession(store.sessionId, '账号七的会话')
  assert.deepEqual(keysWithTitle('账号七的会话'), [`${SESSION_STORAGE_KEY}:u7`])
  assert.equal(store.sessionList.some((item) => item.title === '共享桶的会话'), false)
  assert.deepEqual(keysWithTitle('共享桶的会话'), [SESSION_STORAGE_KEY], '共享桶原样保留')

})

test('收到消息时先把当前状态写回旧桶，再换（顺序错会串数据）', () => {
  const store = useSessionStore()
  off = installHostUserBridge((scope) => store.applyUserScope(scope), ORIGIN)

  store.renameSession(store.sessionId, '切之前就存在的会话')
  window.dispatchEvent(userMessage('9'))

  const shared = localStorage.getItem(SESSION_STORAGE_KEY) || ''
  const scoped = localStorage.getItem(`${SESSION_STORAGE_KEY}:u9`) || ''
  assert.ok(shared.includes('切之前就存在的会话'), '旧桶必须留着切之前的状态')
  assert.equal(scoped.includes('切之前就存在的会话'), false, '不能把旧桶内容写进新桶')
})

test('重复的同账号消息不重复换桶（换桶有副作用：会重读存储）', () => {
  const store = useSessionStore()
  off = installHostUserBridge((scope) => store.applyUserScope(scope), ORIGIN)

  window.dispatchEvent(userMessage('7'))
  store.renameSession(store.sessionId, '七的会话')
  window.dispatchEvent(userMessage('7'))   // 再来一次同账号

  assert.equal(store.sessionList.some((item) => item.title === '七的会话'), true, '不该被重读清掉')
  assert.equal(getActiveUid(), '7')
})

test('退出到无账号：写回共享桶', () => {
  const store = useSessionStore()
  off = installHostUserBridge((scope) => store.applyUserScope(scope), ORIGIN)

  window.dispatchEvent(userMessage('7'))
  store.renameSession(store.sessionId, '七的会话')
  window.dispatchEvent(userMessage(null))

  assert.equal(getActiveUid(), null)
  store.renameSession(store.sessionId, '回到共享桶的会话')
  assert.deepEqual(keysWithTitle('回到共享桶的会话'), [SESSION_STORAGE_KEY])
  assert.deepEqual(keysWithTitle('七的会话'), [`${SESSION_STORAGE_KEY}:u7`], '账号桶保留')
})
