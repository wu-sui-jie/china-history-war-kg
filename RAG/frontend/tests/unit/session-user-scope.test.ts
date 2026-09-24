/** 会话按账号隔离与切换（问题二方案 A 的 store 侧）。
 *
 * 最关键的一条是切换顺序：**先把当前内存状态写回原 key，再读新账号的存储**。
 * 反过来会把上一个账号的会话写进新账号的桶里（串数据），因此这里正面钉它。
 */

import assert from 'node:assert/strict'
import { beforeEach, test } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSessionStore } from '@/stores/session'
import { setActiveUid, SESSION_STORAGE_KEY } from '@/utils/userScope'
import { installBrowserShims } from './shims'

beforeEach(() => {
  installBrowserShims()
  setActiveUid(null)
  setActivePinia(createPinia())
})

type Store = ReturnType<typeof useSessionStore>

/** createSession() 在当前会话本来就空时会 no-op，这里直接给"当前会话"命名来落盘。 */
function labelCurrent(store: Store, title: string) {
  store.renameSession(store.sessionId, title)
}

function keysWithTitle(title: string): string[] {
  const hits: string[] = []
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i) as string
    const raw = localStorage.getItem(key) || ''
    if (raw.includes(title)) hits.push(key)
  }
  return hits
}

test('独立访问（无 uid）用基础 key：与改造前一致', () => {
  const store = useSessionStore()
  labelCurrent(store, '独立访问的会话')

  assert.deepEqual(keysWithTitle('独立访问的会话'), [SESSION_STORAGE_KEY])
  assert.equal(localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`), null)
})

test('切到某账号：看不到无账号时的记录，且不把那条记录搬过去', () => {
  const store = useSessionStore()
  labelCurrent(store, '共享桶里的会话')

  store.applyUserScope('7')

  assert.equal(localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`), null, '不该把共享桶的内容写进账号桶')
  assert.equal(store.sessionList.some((item) => item.title === '共享桶里的会话'), false, '界面里也不该出现')
  assert.ok((localStorage.getItem(SESSION_STORAGE_KEY) || '').includes('共享桶里的会话'), '共享桶原样保留')
})

test('两个账号各存各的，来回切换都还在', () => {
  const store = useSessionStore()

  store.applyUserScope('7')
  labelCurrent(store, '甲同学的会话')
  assert.deepEqual(keysWithTitle('甲同学的会话'), [`${SESSION_STORAGE_KEY}:u7`])

  // 切到另一个账号：看不到甲的记录，自己命名一条
  store.applyUserScope('8')
  assert.equal(store.sessionList.some((item) => item.title === '甲同学的会话'), false)
  labelCurrent(store, '乙同学的会话')
  assert.deepEqual(keysWithTitle('乙同学的会话'), [`${SESSION_STORAGE_KEY}:u8`])

  // 切回甲：甲的记录回来，乙的看不到
  store.applyUserScope('7')
  assert.equal(store.sessionList.some((item) => item.title === '甲同学的会话'), true)
  assert.equal(store.sessionList.some((item) => item.title === '乙同学的会话'), false)
  // 两个桶同时存在，互不覆盖
  assert.ok((localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`) || '').includes('甲同学的会话'))
  assert.ok((localStorage.getItem(`${SESSION_STORAGE_KEY}:u8`) || '').includes('乙同学的会话'))
})

test('切到同一账号是空操作', () => {
  const store = useSessionStore()
  store.applyUserScope('7')
  labelCurrent(store, '稳定会话')
  const before = localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`)
  store.applyUserScope('7')
  assert.equal(localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`), before)
})

test('退出到无账号：回到共享桶内容，账号桶保留', () => {
  const store = useSessionStore()
  labelCurrent(store, '共享的会话')

  store.applyUserScope('7')
  labelCurrent(store, '我的会话')

  store.applyUserScope(null)
  assert.equal(store.sessionList.some((item) => item.title === '共享的会话'), true)
  assert.equal(store.sessionList.some((item) => item.title === '我的会话'), false)
  assert.ok((localStorage.getItem(`${SESSION_STORAGE_KEY}:u7`) || '').includes('我的会话'))
})
