/** 知识面板与证据列表的组件用例（2026-09-16 工作单 P1-12 / P1-13）。
 *
 * 覆盖：面板生命周期空状态（未提问 / 已取消 / 失败 / 完成但无数据）、
 * tabs 的 ARIA 语义、证据展开按钮的 aria-expanded、动态 chunk 加载失败的降级与重试。
 */

import assert from 'node:assert/strict'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import EvidenceView from '@/components/panel/EvidenceView.vue'
import MapView from '@/components/panel/MapView.vue'
import PanelPane from '@/components/panel/PanelPane.vue'
import SubGraphView from '@/components/panel/SubGraphView.vue'
import { useSessionStore } from '@/stores/session'
import type { AssistantMessage } from '@/stores/session'

const EMPTY_PANEL = {
  entity_cards: [],
  subgraph: { nodes: [], edges: [] },
  timeline: { groups: [] },
  map_points: [],
}

function assistantWith(overrides: Partial<AssistantMessage>): AssistantMessage {
  return {
    id: 'a1', role: 'assistant', question: '介绍一下井陉之战。',
    answer: '', citations: [], conflicts: [], entities: [], candidates: [],
    entityCards: [], panel: { ...EMPTY_PANEL }, status: [],
    turnStatus: 'completed', streaming: false, cancelled: false, finished: true,
    correctedEntities: [], createdAt: Date.now(),
    ...overrides,
  } as AssistantMessage
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('面板生命周期空状态', () => {
  test('未提问时提示“尚未提问”', () => {
    const wrapper = mount(PanelPane)
    expect(wrapper.text()).toContain('尚未提问')
  })

  test('已取消轮显示取消文案（P2-9 的可达性）', () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ turnStatus: 'cancelled', cancelled: true,
                                      finished: false, answer: '' })]
    const wrapper = mount(PanelPane)
    expect(wrapper.text()).toContain('本轮已取消')
  })

  test('失败轮与中断轮给出不同文案', () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ turnStatus: 'failed', finished: false, answer: '' })]
    expect(mount(PanelPane).text()).toContain('未正常完成')

    setActivePinia(createPinia())
    const store2 = useSessionStore()
    store2.messages = [assistantWith({ turnStatus: 'interrupted', finished: false,
                                       answer: '半截' })]
    expect(mount(PanelPane).text()).toContain('连接中断')
  })

  test('完成但无内容时提示没有额外内容', () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ turnStatus: 'completed' })]
    expect(mount(PanelPane).text()).toContain('没有额外的实体卡')
  })
})

describe('tabs 的 ARIA 语义（P1-13）', () => {
  test('tablist/tab/aria-selected 对应正确，方向键可切换', async () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ turnStatus: 'completed' })]
    const wrapper = mount(PanelPane, { attachTo: document.body })
    const tablist = wrapper.find('[role="tablist"]')
    assert.ok(tablist.exists(), '必须有 role=tablist')
    const tabs = wrapper.findAll('[role="tab"]')
    assert.equal(tabs.length, 5)
    assert.equal(tabs[0].attributes('aria-selected'), 'true')
    assert.equal(tabs[0].attributes('aria-controls'), 'panel-tabpanel-evidence')
    await tabs[0].trigger('keydown', { key: 'ArrowRight' })
    assert.equal(store.panelTab, 'cards', '右方向键应切到下一个 tab')
    wrapper.unmount()
  })
})

describe('证据列表', () => {
  const citations = [
    { index: 1, evidence_id: 'e1', kind: 'raw_text', title: '史料甲', snippet: '片段一' },
    { index: 2, evidence_id: 'e2', kind: 'graph_triple', title: '关系乙', snippet: '' },
  ]

  test('展开按钮带 aria-expanded 且可切换', async () => {
    const wrapper = mount(EvidenceView, { props: { citations, conflicts: [] } })
    const btn = wrapper.find('button.evidence-head')
    assert.equal(btn.attributes('aria-expanded'), 'false')
    await btn.trigger('click')
    assert.equal(btn.attributes('aria-expanded'), 'true')
    assert.ok(wrapper.find('#evidence-body-1').exists())
  })

  test('store 的引用定位会展开并高亮对应行', async () => {
    const store = useSessionStore()
    const wrapper = mount(EvidenceView, {
      props: { citations, conflicts: [] }, attachTo: document.body,
    })
    store.requestCitation(2)
    await new Promise((r) => setTimeout(r, 30))
    assert.equal(store.panelTab, 'evidence')
    assert.equal(store.panelOpen, true)
    const row = wrapper.find('.evidence-row.active')
    assert.equal(row.attributes('data-index'), '2')
    wrapper.unmount()
  })
})

describe('动态 chunk 加载失败降级（P2-8）', () => {
  test('图谱组件加载失败时显示错误与重试按钮', async () => {
    vi.mock('echarts/core', () => {
      throw new Error('chunk load failed')
    })
    const wrapper = mount(SubGraphView, {
      props: { graph: { nodes: [{ id: 'n1', type: '事件', name: '井陉之战' }], edges: [] } },
    })
    await new Promise((r) => setTimeout(r, 30))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('加载失败')
    const retry = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    assert.ok(retry, '失败后必须给出重试入口')
  })

  test('地图组件加载失败时显示错误与重试按钮', async () => {
    vi.mock('echarts/core', () => {
      throw new Error('chunk load failed')
    })
    const wrapper = mount(MapView, {
      props: { points: [{ place_id: 'p1', name: '井陉', longitude: 114, latitude: 38 }] },
    })
    await new Promise((r) => setTimeout(r, 30))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('加载失败')
  })
})
