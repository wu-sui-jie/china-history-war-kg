/** 提问历史侧栏用例。
 *
 * 覆盖：空态、倒序渲染与状态徽标、默认/手选高亮、点击 emit 该轮 id、
 * 被重查取代轮次的标注；以及多会话：会话列表渲染/切换/新建/删除/折叠。
 */

import assert from 'node:assert/strict'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
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
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
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

describe('会话列表（2026-09-20 借鉴项 P1）', () => {
  /** 让当前会话非空（新建会话的前置条件），返回该会话 id。 */
  function seedTurn(id: string, question: string): string {
    const store = useSessionStore()
    store.messages = [
      userWith(id, question, Date.now()),
      assistantWith({ id: `a-${id}`, question }),
    ]
    // 真实链路里首条提问会给会话命名（beginTurn → autoTitleSession）；
    // 直接塞 messages 不经过那一步，这里显式补上，改名用例才有非默认标题可比较。
    store.renameSession(store.sessionId, question)
    return store.sessionId
  }

  test('渲染会话列表，当前会话高亮且排在最前', () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')

    const wrapper = mount(HistoryPane)
    const items = wrapper.findAll('.session-item')
    assert.equal(items.length, 2)
    assert.ok(items[0].classes().includes('active'), '当前会话排最前且高亮')
    assert.ok(items[0].text().includes('2 条'), '显示该会话消息条数')
    assert.ok(!items[1].classes().includes('active'))
  })

  test('点击其它会话切过去并发出 session-change', async () => {
    const store = useSessionStore()
    const firstId = seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')

    const wrapper = mount(HistoryPane)
    const other = wrapper.findAll('.session-item').find((i) => !i.classes().includes('active'))
    await other!.find('.session-pick').trigger('click')

    assert.equal(store.sessionId, firstId, '切到被点击的会话')
    assert.equal(store.messages.length, 2, '带出该会话的消息')
    assert.equal(wrapper.emitted('session-change')?.length, 1)
  })

  test('新建按钮创建一个空会话', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    const wrapper = mount(HistoryPane)

    await wrapper.find('.session-new').trigger('click')
    assert.equal(store.sessionList.length, 2)
    assert.equal(store.messages.length, 0)
    assert.equal(store.sessionList[0].active, true)
  })

  test('删除会话先确认，确认后从列表移除', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action.danger').trigger('click')

    // 第一次点击只进入确认态：清单还没动
    assert.equal(store.sessionList.length, 2, '点删除不直接删，先出确认')
    assert.ok(wrapper.find('.session-confirm-text').exists())

    await wrapper.find('.session-inline-actions .session-action.danger').trigger('click')
    assert.equal(store.sessionList.length, 1)
    assert.equal(wrapper.emitted('session-change')?.length, 1)
  })

  test('删除确认被取消时不删（避免误触）', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action.danger').trigger('click')
    await wrapper.find('.session-inline-actions .session-action:not(.danger)').trigger('click')

    assert.equal(store.sessionList.length, 2)
    assert.ok(!wrapper.find('.session-confirm-text').exists(), '取消后回到普通条目')
  })

  /** 嵌入主应用时 iframe 的 sandbox 没有 allow-modals：
   *  window.confirm 恒返回 false、window.prompt 恒返回 null。
   *  所以改名/删除**不能**依赖原生弹窗——这条用例把它们 mock 成"被吞掉"的行为，
   *  功能仍须照常可用（回归锁：曾经就是被吞掉且毫无提示）。 */
  test('原生弹窗被 sandbox 吞掉时，改名与删除仍然可用', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    // 模拟 sandbox 下的原生行为：confirm=false、prompt=null
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue(null)

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    const input = wrapper.find('.session-edit-input')
    assert.ok(input.exists(), '改名入口是页内输入框，不是 window.prompt')
    await input.setValue('改过的标题')
    await wrapper.find('form.session-inline').trigger('submit')

    assert.equal(store.sessionList[0].title, '改过的标题')
    assert.equal(promptSpy.mock.calls.length, 0)
    assert.equal(confirmSpy.mock.calls.length, 0)
  })

  test('改名预填原标题，直接输入即可覆盖', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    assert.equal((wrapper.find('.session-edit-input').element as HTMLInputElement).value, '第一个问题')
  })

  test('改名取消（含 Esc）不改标题', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    await wrapper.find('.session-edit-input').setValue('不该生效')
    await wrapper.find('.session-edit-input').trigger('keydown.esc')

    assert.equal(store.sessionList[0].title, '第一个问题')
    assert.ok(!wrapper.find('.session-edit-input').exists(), 'Esc 后回到普通条目')
  })

  test('空标题回退默认名（与 store 口径一致）', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    await wrapper.find('.session-edit-input').setValue('   ')
    await wrapper.find('form.session-inline').trigger('submit')

    assert.equal(store.sessionList[0].title, '新会话')
  })

  test('改名输入框与删除确认按钮自动接管焦点', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    // 焦点断言需要元素真的在 document 里
    const wrapper = mount(HistoryPane, { attachTo: document.body })

    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    assert.equal(document.activeElement?.className, 'session-edit-input')

    await wrapper.find('form.session-inline .session-action:not([type="submit"])').trigger('click')
    await wrapper.find('.session-item.active').find('.session-action.danger').trigger('click')
    assert.ok(
      (document.activeElement as HTMLElement)?.textContent?.includes('取消'),
      '焦点落在取消而不是确认删除：破坏性操作不该由一次回车完成',
    )
    wrapper.unmount()
  })

  test('改另一条目的名时新输入框仍拿到焦点（这条锁住 v-focus-select 的必要性）', async () => {
    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')      // 列表新会话在前 → [第二个问题, 第一个问题]
    const wrapper = mount(HistoryPane, { attachTo: document.body })

    // 先点靠后的条目、再点靠前的条目：插在列表前面的那个先挂载、后一个才卸载，
    // "共享 ref + 挂载后统一聚焦"会被随后的卸载清成 null，聚焦落空。
    await wrapper.findAll('.session-item')[1].find('.session-action').trigger('click')
    await wrapper.findAll('.session-item')[0].find('.session-action').trigger('click')

    const input = wrapper.find('.session-edit-input').element as HTMLInputElement
    assert.equal(input.value, '第二个问题')
    assert.equal(document.activeElement, input)
    wrapper.unmount()
  })

  test('切换会话会收起未提交的改名/删除确认', async () => {    const store = useSessionStore()
    seedTurn('u1', '第一个问题')
    store.createSession()
    seedTurn('u2', '第二个问题')

    const wrapper = mount(HistoryPane)
    await wrapper.find('.session-item.active').find('.session-action').trigger('click')
    assert.ok(wrapper.find('.session-edit-input').exists())

    const other = wrapper.findAll('.session-item').find((i) => !i.classes().includes('active'))
    await other!.find('.session-pick').trigger('click')
    assert.ok(!wrapper.find('.session-edit-input').exists(), '页内控件跟着条目走，不跨会话残留')
  })

  test('会话列表可折叠（状态落到本地偏好）', async () => {
    const wrapper = mount(HistoryPane)
    assert.ok(wrapper.find('.session-list').exists(), '默认展开')

    await wrapper.find('.session-toggle').trigger('click')
    assert.ok(!wrapper.find('.session-list').exists(), '折叠后列表隐藏')
    assert.equal(localStorage.getItem('ragv5-ui-sessions-open'), '0')
  })
})
