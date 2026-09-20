/** 提问历史侧栏用例（2026-09-20）。
 *
 * 覆盖：空态、倒序渲染与状态徽标、默认/手选高亮、点击 emit 该轮 id、
 * 被重查取代轮次的标注。
 */

import assert from 'node:assert/strict'
import { beforeEach, describe, expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import HistoryPane from '@/components/history/HistoryPane.vue'
import { useSessionStore } from '@/stores/session'
import type { AssistantMessage, UserMessage } from '@/stores/session'

function assistantWith(overrides: Partial<AssistantMessage>): AssistantMessage {
  return {
    id: 'a1',
    role: 'assistant',
    question: '介绍一下垓下之战。',
    answer: '垓下之战……',
    citations: [],
    conflicts: [],
    entities: [],
    candidates: [],
    entityCards: [],
    panel: null,
    status: [],
    turnStatus: 'completed',
    streaming: false,
    cancelled: false,
    finished: true,
    correctedEntities: [],
    createdAt: Date.now(),
    ...overrides,
  } as AssistantMessage
}

function userWith(id: string, question: string, at: number): UserMessage {
  return {
    id,
    role: 'user',
    question,
    filters: { dynasty: [], event_type: [] },
    createdAt: at,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('提问历史侧栏', () => {
  test('没有提问时显示空态', () => {
    const wrapper = mount(HistoryPane)
    expect(wrapper.text()).toContain('还没有提问记录')
  })

  test('按倒序渲染轮次与状态徽标', () => {
    const store = useSessionStore()
    const at = Date.now()
    store.messages = [
      userWith('u1', '介绍一下垓下之战。', at - 60000),
      assistantWith({ id: 'a1', question: '介绍一下垓下之战。', createdAt: at - 60000 }),
      userWith('u2', '介绍一下赤壁之战。', at),
      assistantWith({
        id: 'a2',
        question: '介绍一下赤壁之战。',
        turnStatus: 'cancelled',
        cancelled: true,
        finished: false,
        createdAt: at,
      }),
    ]
    const wrapper = mount(HistoryPane)
    const items = wrapper.findAll('.history-item')
    assert.equal(items.length, 2)
    assert.ok(items[0].text().includes('介绍一下赤壁之战。'), '最新一轮在前')
    assert.ok(items[0].text().includes('第 2 轮'))
    assert.ok(items[0].text().includes('已取消'), '取消轮也有状态徽标')
    assert.ok(items[1].text().includes('第 1 轮'))
    assert.ok(items[1].text().includes('已生成'))
  })

  test('点击条目 emit 对应轮次 id', async () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ id: 'a1' })]
    const wrapper = mount(HistoryPane)
    await wrapper.find('.history-item').trigger('click')
    assert.deepEqual(wrapper.emitted('pick')?.[0], ['a1'])
  })

  test('默认高亮最新轮，手选后高亮所选轮', async () => {
    const store = useSessionStore()
    store.messages = [
      userWith('u1', '问题一', 1),
      assistantWith({ id: 'a1', question: '问题一' }),
      userWith('u2', '问题二', 2),
      assistantWith({ id: 'a2', question: '问题二' }),
    ]
    const wrapper = mount(HistoryPane)
    assert.ok(
      wrapper.find('.history-item.active').text().includes('问题二'),
      '未手选时高亮最新一轮',
    )

    store.selectTurn('a1')
    await wrapper.vm.$nextTick()
    assert.ok(
      wrapper.find('.history-item.active').text().includes('问题一'),
      '手选后高亮所选轮次',
    )
  })

  test('被重查取代的轮次标注“已被重查取代”', () => {
    const store = useSessionStore()
    store.messages = [
      assistantWith({ id: 'a1', supersededBy: 'a2' }),
      assistantWith({ id: 'a2' }),
    ]
    const wrapper = mount(HistoryPane)
    assert.ok(wrapper.text().includes('已被重查取代'))
  })
})
