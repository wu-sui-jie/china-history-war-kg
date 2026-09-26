/** 主应用身份桥与按账号存储 key。
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

test('uid 形状受约束：对象/超长/含分隔符一律按"没有账号"处理', () => {
  // 桥把对象当字符串会拼出 :u[object Object] 这种谁都不是的桶
  assert.equal(storageKeyFor({} as any), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor('[object Object]'), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor('a'.repeat(65)), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor('3:u4'), SESSION_STORAGE_KEY)
  assert.equal(storageKeyFor('中文账号'), SESSION_STORAGE_KEY)
  // 合法形状照常（数字、带下划线/连字符的字符串）
  assert.equal(storageKeyFor('u_1-a'), `${SESSION_STORAGE_KEY}:uu_1-a`)
  setActiveUid('[object Object]')
  assert.equal(getActiveUid(), null, '非法 uid 不能进 setActiveUid')
  assert.equal(activeStorageKey(), SESSION_STORAGE_KEY)
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

test('消息解析：只认 cw-user 且只认同源（结果带 role 与 token）', () => {
  const TOKEN = 'aaa.bbb.ccc'
  assert.deepEqual(
    parseUserScopeMessage(
      { origin: ORIGIN, data: { type: 'cw-user', uid: 3, role: 'admin', token: TOKEN } }, ORIGIN),
    { uid: '3', role: 'admin', token: TOKEN },
  )
  // 老版本主应用不发 role/token：按空串处理，仍然有效
  assert.deepEqual(
    parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: '7' } }, ORIGIN),
    { uid: '7', role: '', token: '' },
  )
  assert.deepEqual(
    parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: null } }, ORIGIN),
    { uid: null, role: '', token: '' },
  )
  assert.deepEqual(
    parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: '' } }, ORIGIN),
    { uid: null, role: '', token: '' },
  )
  // 非法 uid（对象/超长）：归成"没有账号"，绝不能拼出怪桶
  assert.deepEqual(
    parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: { id: 3 } } }, ORIGIN),
    { uid: null, role: '', token: '' },
  )
  assert.deepEqual(
    parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: 'x'.repeat(65) } }, ORIGIN),
    { uid: null, role: '', token: '' },
  )
  // 非法 token：一律归成"没有身份"。含空白的值进了 HTTP 头会被 fetch 拒绝，
  // 含换行的值甚至可能被用来注入额外请求头，所以必须在入口就洗干净。
  for (const bad of ['has space', 'a.b.c\nInjected: 1', { v: 1 }, 42, 'short']) {
    assert.deepEqual(
      parseUserScopeMessage({ origin: ORIGIN, data: { type: 'cw-user', uid: '7', token: bad } }, ORIGIN),
      { uid: '7', role: '', token: '' },
      `非法 token 必须归零：${JSON.stringify(bad)}`,
    )
  }
  // 其它来源：忽略（返回 undefined 表示"这条消息与我们无关"）
  assert.equal(parseUserScopeMessage({ origin: 'http://evil.example', data: { type: 'cw-user', uid: 9 } }, ORIGIN), undefined)
  // 形状不对：忽略，且不能把 uid 当有效身份
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: { type: 'other', uid: 9 } }, ORIGIN), undefined)
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: null }, ORIGIN), undefined)
  assert.equal(parseUserScopeMessage({ origin: ORIGIN, data: 'cw-user' }, ORIGIN), undefined)
})

test('身份桥：同源消息触发回调，换 uid 才再触发；桥自己不改 activeUid', () => {
  const seen: (string | null)[] = []
  const off = installHostUserBridge((scope) => seen.push(scope.uid), ORIGIN)

  window.dispatchEvent(message('5'))
  window.dispatchEvent(message('5'))  // 同一个账号不重复通知
  window.dispatchEvent(message('6'))
  assert.deepEqual(seen, ['5', '6'])
  // 换桶是 store 的事：桥只回调，避免"先改 uid 再写回"导致串数据（见文件头与装配级用例）
  assert.equal(getActiveUid(), null)

  // 异源消息被忽略
  window.dispatchEvent(message('99', 'http://evil.example'))
  assert.deepEqual(seen, ['5', '6'])

  off()
  window.dispatchEvent(message('7'))
  assert.deepEqual(seen, ['5', '6'])
})

test('身份桥：null 表示"退出到无账号"，回调照样送达', () => {
  const seen: (string | null)[] = []
  const off = installHostUserBridge((scope) => seen.push(scope.uid), ORIGIN)
  window.dispatchEvent(message('5'))
  window.dispatchEvent(message(null))
  assert.deepEqual(seen, ['5', null])
  off()
})

test('身份桥：去重位在回调之后才推进（回调失败后同一 uid 仍能重来）', () => {
  // jsdom 会把事件监听器里抛出的异常报成 unhandled error（拿不到 assert.throws），
  // 所以这里用"回调期间同一 uid 仍会被送达"来正面钉住推进顺序：如果去重位在回调**之前**
  // 就推进了，下面这次重入会被当成重复消息直接丢掉，seen 只会有一个 '5'。
  // 真实场景对应"回调抛错（换桶时读存储失败）→ 这个账号改不了桶且没有自愈机会"。
  const seen: string[] = []
  const off = installHostUserBridge((scope) => {
    seen.push(String(scope.uid))
    if (seen.length === 1) window.dispatchEvent(message('5'))
  }, ORIGIN)

  window.dispatchEvent(message('5'))

  assert.deepEqual(seen, ['5', '5'])
  off()
})

test('身份桥：role 原样转交（老版本主应用不发则为空串）', () => {
  const roles: string[] = []
  const off = installHostUserBridge((scope) => roles.push(scope.role), ORIGIN)
  window.dispatchEvent(new MessageEvent('message', { data: { type: 'cw-user', uid: '5', role: 'editor' }, origin: ORIGIN }))
  window.dispatchEvent(new MessageEvent('message', { data: { type: 'cw-user', uid: '6' }, origin: ORIGIN }))
  assert.deepEqual(roles, ['editor', ''])
  off()
})
