/** 按账号隔离存储的用例（问答记录隔离，问题二方案 A）。
 *
 * 重点：不同账号互不可见、账号未知时退回原 key（行为与改造前一致）、
 * 老全局记录被归档而不是归属某个账号、坏数据与 localStorage 不可用时不炸。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import {
  archivedStorageKey,
  readScoped,
  scopedStorageKey,
  writeScoped,
} from '@/utils/userScopedStorage'

const BASE = 'chatHistory'

describe('scopedStorageKey', () => {
  test('有账号拼账号 id，没有账号退回原 key', () => {
    expect(scopedStorageKey(BASE, 3)).toBe('chatHistory:u3')
    expect(scopedStorageKey(BASE, '3')).toBe('chatHistory:u3')
    expect(scopedStorageKey(BASE, ' 7 ')).toBe('chatHistory:u7')
    expect(scopedStorageKey(BASE, null)).toBe(BASE)
    expect(scopedStorageKey(BASE, undefined)).toBe(BASE)
    expect(scopedStorageKey(BASE, '')).toBe(BASE)
  })

  test('账号 id 不会串味：u3 与 u30 是两个 key', () => {
    expect(scopedStorageKey(BASE, 3)).not.toBe(scopedStorageKey(BASE, 30))
  })
})

describe('readScoped / writeScoped', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  test('写入后再读同一个账号拿得到，另一个账号读不到', () => {
    writeScoped(BASE, 3, [{ id: 'a' }])
    expect(readScoped(BASE, 3, [])).toEqual([{ id: 'a' }])
    expect(readScoped(BASE, 4, [])).toEqual([])
  })

  test('账号未知时退回原 key：独立使用/未登录的行为与改造前一致', () => {
    writeScoped(BASE, null, [{ id: 'shared' }])
    expect(localStorage.getItem(BASE)).toBeTruthy()
    expect(readScoped(BASE, null, [])).toEqual([{ id: 'shared' }])
  })

  test('老全局记录被归档而不是归属第一个登录的人', () => {
    localStorage.setItem(BASE, JSON.stringify([{ id: 'old', from: 'anyone' }]))

    // 第一个登录的账号读到的是空（不能把别人的记录给他）
    expect(readScoped(BASE, 8, [])).toEqual([])
    // 老数据挪到归档 key，原 key 清掉（只归档一次）
    expect(localStorage.getItem(BASE)).toBeNull()
    expect(JSON.parse(localStorage.getItem(archivedStorageKey(BASE)) as string)).toEqual([
      { id: 'old', from: 'anyone' },
    ])

    // 再来的账号也读不到，但归档不会被覆盖
    expect(readScoped(BASE, 9, [])).toEqual([])
    expect(JSON.parse(localStorage.getItem(archivedStorageKey(BASE)) as string)).toHaveLength(1)
  })

  test('没有账号时不触发归档（独立使用直接沿用原 key）', () => {
    localStorage.setItem(BASE, JSON.stringify([{ id: 'old' }]))
    expect(readScoped(BASE, null, [])).toEqual([{ id: 'old' }])
    expect(localStorage.getItem(archivedStorageKey(BASE))).toBeNull()
    expect(localStorage.getItem(BASE)).toBeTruthy()
  })

  test('坏数据当没有处理，不抛给页面', () => {
    localStorage.setItem(scopedStorageKey(BASE, 3), '{不是 JSON')
    expect(readScoped(BASE, 3, [])).toEqual([])
  })

  test('localStorage 不可用（隐私模式）时读返回兜底、写静默失败', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(readScoped(BASE, 3, ['fallback'])).toEqual(['fallback'])
    expect(() => writeScoped(BASE, 3, ['x'])).not.toThrow()
    getItem.mockRestore()
    setItem.mockRestore()
  })
})
