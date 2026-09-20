/** MessageBubble 组件交互用例（2026-09-16 工作单 P1-12 的 Vue Test Utils 层）。
 *
 * 覆盖：重试按钮的显示与行为、同名候选下拉的 entity_id 取值与 payload、
 * 引用点击事件、失败轮的纠正入口收起。
 */

import assert from 'node:assert/strict'
import { beforeEach, describe, test, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import MessageBubble from '@/components/chat/MessageBubble.vue'
import { useSessionStore } from '@/stores/session'
import type { AssistantMessage } from '@/stores/session'

function makeAssistant(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: 'a1',
    role: 'assistant',
    question: '介绍一下井陉之战。',
    answer: '井陉之战是……',
    citations: [{ index: 1, evidence_id: 'e1', kind: 'raw_text', title: '史料', snippet: '…' }],
    conflicts: [],
    entities: [
      { name: '井陉之战', standard_name: '井陉之战', type: '事件',
        entity_id: 'event_zhan', dynasty: '战国' },
    ],
    candidates: [{
      mention: '井陉之战',
      entity_type: '事件',
      options: [
        { name: '井陉之战', standard_name: '井陉之战', entity_id: 'event_zhan',
          dynasty: '战国', entity_type: '事件' },
        { name: '井陉之战', standard_name: '井陉之战', entity_id: 'event_han',
          dynasty: '西汉', entity_type: '事件' },
      ],
    }],
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

function mountBubble(message: any, store?: any) {
  const s = store || useSessionStore()
  s.messages = [message]
  return mount(MessageBubble, { props: { message }, global: { plugins: [] } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  if (typeof globalThis.localStorage?.clear === 'function') globalThis.localStorage.clear()
})

describe('重试入口', () => {
  test('失败轮显示“重试本轮”并调用 store.retryTurn', async () => {
    const store = useSessionStore()
    const spy = vi.spyOn(store, 'retryTurn').mockImplementation(() => {})
    const wrapper = mountBubble(makeAssistant({ turnStatus: 'failed', answer: '',
                                                finished: false }), store)
    const btn = wrapper.findAll('button').find((b) => b.text().includes('重试本轮'))
    assert.ok(btn, '失败轮必须提供重试入口')
    await btn!.trigger('click')
    assert.equal(spy.mock.calls.length, 1)
  })

  test('正常完成轮不显示重试按钮', () => {
    const wrapper = mountBubble(makeAssistant(), useSessionStore())
    const btn = wrapper.findAll('button').find((b) => b.text().includes('重试本轮'))
    assert.equal(btn, undefined)
  })

  test('失败轮不显示实体纠正输入框（只保留重试）', () => {
    const wrapper = mountBubble(makeAssistant({ turnStatus: 'failed', answer: '',
                                                finished: false }), useSessionStore())
    assert.equal(wrapper.findAll('input[type="text"]').length, 0)
  })
})

describe('同名候选', () => {
  test('下拉选项 value 使用 entity_id 而不是标准名', () => {
    const wrapper = mountBubble(makeAssistant(), useSessionStore())
    const options = wrapper.findAll('.candidate-select option')
    const values = options.map((o) => o.attributes('value')).filter(Boolean)
    assert.deepEqual(values, ['event_zhan', 'event_han'])
    // 展示文案带朝代，避免两个一模一样的选项
    assert.ok(options.some((o) => o.text().includes('西汉')))
  })

  test('选中第二个候选时纠正 payload 带 replacement_entity_id', async () => {
    const store = useSessionStore()
    const spy = vi.spyOn(store, 'correctEntity').mockImplementation(() => {})
    const wrapper = mountBubble(makeAssistant(), store)
    const select = wrapper.find('.candidate-select')
    await select.setValue('event_han')
    assert.equal(spy.mock.calls.length, 1)
    const payload = spy.mock.calls[0][1] as any
    assert.equal(payload.option.entity_id, 'event_han')
    assert.equal(payload.action, 'add')
  })
})

describe('引用与状态展示', () => {
  test('点击引用 chip 抛出 citation 事件（带轮次 id，历史轮也能正确定位）', async () => {
    const wrapper = mountBubble(makeAssistant(), useSessionStore())
    await wrapper.find('.cite-chip').trigger('click')
    assert.deepEqual(wrapper.emitted('citation')?.[0], [1, 'a1'])
  })

  test('interrupted 轮显示“回答可能不完整”提示', () => {
    const wrapper = mountBubble(
      makeAssistant({ turnStatus: 'interrupted', partial: true }), useSessionStore())
    assert.ok(wrapper.text().includes('可能不完整'))
    assert.ok(wrapper.text().includes('连接中断'))
  })
})
