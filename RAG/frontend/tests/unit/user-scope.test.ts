/** 主应用身份桥与按账号存储 key（问题二方案 A）。
 *
 * 覆盖：key 组装、消息形状/origin 校验（只认同源）、重复 uid 不重复回调、解绑。
 */

import assert from 'node:assert/strict'
import { beforeEach, test } from 'vitest'

import {
  activeStorageKey,
  getActiveUid,
  installHostUserBridge,
  parseUserScopeMessage,
  setActiveUid,
  storageKeyFor,
  SESSION_STORAGE_KEY,
} from '@/utils/userScope'
import { installBrowserShims } from './shims'

const ORIGIN = 'http://localhost:3000'

function message(uid: unknown, origin = ORIGIN) {
  return new MessageEvent('message', { data: { type: 'cw-user', uid }, origin })
}

beforeEach(() => {
  installBrowserShims()
  setActiveUid(null)
})

test('存储 key：有账号带后缀，没账号保持原 key（独立访问行为不变）', () => {
  assert.equal(storageKeyFor(3), `${SESSION_STORAGE_KEY}:u3`)
  assert.equal(storageKeyFor(' 7 '), `${SESSION_STORAGE_KEY}:u7`)
  assert.equal(storageKeyFor(null), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor(undefined), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor(''), SESSION_STORAGE_KEY)
  // u3 与 u30 不能是同一个桶
  assert.notEqual(storageKeyFor(3), storageKeyFor(30))
})

test('activeStorageKey 跟随 setActiveUid', () => {
  assert.equal(activeStorageKey(), SESSION_STORAGE_KEY)
  setActiveUid('42')
  assert.equal(getActiveUid(), '42')
  assert.equal(activeStorageKey(), `${SESSION_STORAGE_KEY}:u42`)
  setActiveUid('')
  assert.equal(getActiveUid(), null)
  assert.equal(activeStorageKey(), SESSION_STORAGE_KEY)
})

test('消息解析：只认 cw-user 且只认同源', () => {
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: 3 } }, ORIGIN), '3')
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: null } }, ORIGIN), null)
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: '' } }, ORIGIN), null)
  // 其它来源：忽略（返回 undefined 表示"这条消息与我们无关"）
  assert.equal(parseUserScopeMessage({ origin: 'http://evil.example', data: { type: 'cw-user', uid: 9 } }, ORIGIN), undefined)
  // 形状不对：忽略，且不能把 uid 当有效身份
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: { type: 'other', uid: 9 } }, ORIGIN), undefined)
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: null }, ORIGIN), undefined)
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: 'cw-user' }, ORIGIN), undefined)
})

test('身份桥：同源消息触发回调，换 uid 才再触发', () => {
  const seen: (string | null)[] = []
  const off = installHostUserBridge((uid) => seen.push(uid), ORIGIN)

  window.dispatchEvent(message('5'))
  window.dispatchEvent(message('5'))  // 同一个账号不重复通知
  window.dispatchEvent(message('6'))
  assert.deepEqual(seen, ['5', '6'])
  assert.equal(getActiveUid(), '6')

  // 异源消息被忽略
  window.dispatchEvent(message('99', 'http://evil.example'))
  assert.deepEqual(seen, ['5', '6'])
  assert.equal(getActiveUid(), '6')

  off()
  window.dispatchEvent(message('7'))
  assert.deepEqual(seen, ['5', '6'])
})

test('身份桥：null 表示"退出到无账号"，可退回基础 key', () => {
  const seen: (string | null)[] = []
  const off = installHostUserBridge((uid) => seen.push(uid), ORIGIN)
  window.dispatchEvent(message('5'))
  window.dispatchEvent(message(null))
  assert.deepEqual(seen, ['5', null])
  assert.equal(activeStorageKey(), SESSION_STORAGE_KEY)
  off()
})
