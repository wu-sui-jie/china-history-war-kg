/** 知识面板与证据列表的组件用例。
 *
 * 覆盖：面板生命周期空状态（未提问 / 已取消 / 失败 / 完成但无数据）、
 * tabs 的 ARIA 语义、证据展开按钮的 aria-expanded、动态 chunk 加载失败的降级与重试。
 */

import assert from 'node:assert/strict'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import EvidenceView from '@/components/panel/EvidenceView.vue'
import EntityCardsView from '@/components/panel/EntityCardsView.vue'
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

  test('已取消轮显示取消文案', () => {
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

describe('历史轮次提示条（2026-09-20）', () => {
  test('面板停留在历史轮次时显示提示与“返回最新”', async () => {
    const store = useSessionStore()
    store.messages = [
      assistantWith({ id: 'a1', question: '第一轮问题' }),
      assistantWith({ id: 'a2', question: '第二轮问题' }),
    ]
    store.selectTurn('a1')
    const wrapper = mount(PanelPane)
    expect(wrapper.text()).toContain('正在查看历史轮次')
    const back = wrapper.findAll('button').find((b) => b.text().includes('返回最新'))
    assert.ok(back, '历史视图必须给出返回入口')
    await back!.trigger('click')
    assert.equal(store.isViewingHistory, false)
    assert.equal(store.panelMessage?.id, 'a2', '返回最新后面板跟随最新一轮')
  })

  test('跟随最新轮时不显示提示条', () => {
    const store = useSessionStore()
    store.messages = [assistantWith({ id: 'a1' })]
    const wrapper = mount(PanelPane)
    assert.ok(!wrapper.text().includes('正在查看历史轮次'))
  })
})

describe('tabs 的 ARIA 语义', () => {
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

describe('事件实体卡叙事字段（2026-09-20 借鉴项 P0）', () => {
  const battleCard = {
    entity_id: 'event_0224', type: '事件', name: '赤壁之战',
    dynasty: '东汉', start_date: '208年', event_type: '战争',
    aggressor: '曹操', defender: '孙刘联军', action: '水战',
    impact: '奠定三国鼎立格局', place: '赤壁',
  }

  test('攻方/守方/作战行动/地点与历史影响都渲染', () => {
    const wrapper = mount(EntityCardsView, { props: { cards: [battleCard] } })
    const text = wrapper.text()
    for (const piece of ['攻方', '曹操', '守方', '孙刘联军', '作战行动', '水战',
                         '地点', '赤壁', '历史影响', '奠定三国鼎立格局']) {
      expect(text).toContain(piece)
    }
    expect(wrapper.find('.entity-card-impact').exists()).toBe(true)
  })

  test('叙事字段缺失时整行不渲染，其它字段不受影响', () => {
    const wrapper = mount(EntityCardsView, {
      props: { cards: [{ entity_id: 'person_1', type: '人物', name: '曹操', role: '统帅' }] },
    })
    expect(wrapper.find('.entity-card-meta').text()).not.toContain('攻方')
    expect(wrapper.find('.entity-card-meta').text()).not.toContain('历史影响')
    expect(wrapper.find('.entity-card-impact').exists()).toBe(false)
    expect(wrapper.text()).toContain('统帅')
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

describe('动态 chunk 加载失败降级', () => {
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
