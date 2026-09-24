/** 旧问答助手拆出的四个子组件（第 7 轮 W2）。
 *
 * 拆分的原则是"先测试后拆"：每个子组件先有用例钉住"渲染什么、派发什么"，
 * 再把模板搬过来。这里覆盖的是拆分后**对外契约**——props 怎么进来、emits 怎么出去，
 * 以及"展开/折叠、空态、加载态"这些分支；页面级的落盘与流式逻辑仍在
 * tests/component/inference-scope.test.ts 里。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import SessionSidebar from '@/views/inference/components/SessionSidebar.vue'
import ChatMessages from '@/views/inference/components/ChatMessages.vue'
import ChatInput from '@/views/inference/components/ChatInput.vue'
import KgNodeDrawer from '@/views/inference/components/KgNodeDrawer.vue'
import type { ChatMessage, ChatSession } from '@/types/inference'

// 图谱组件会 import echarts 并初始化画布，与这些用例无关
vi.mock('@/views/inference/components/KgGraph.vue', () => ({
  default: {
    name: 'KgGraph',
    props: ['data'],
    template: '<div class="kg-graph" />',
  },
}))

beforeEach(() => setActivePinia(createPinia()))

describe('SessionSidebar 会话列表', () => {
  const sessions: ChatSession[] = [
    { id: 1, title: '新对话', messages: [], lastTime: Date.UTC(2026, 8, 25, 4, 0) },
    { id: 2, title: '赤壁之战', messages: [], lastTime: Date.UTC(2026, 8, 25, 5, 0) },
  ]
  let wrapper: VueWrapper<any>

  afterEach(() => wrapper?.unmount())

  const mountSidebar = (props: Record<string, unknown> = {}) =>
    mount(SessionSidebar, {
      props: { sessions, activeIndex: 0, collapsed: false, ...props },
      global: { plugins: [Layui] },
    })

  test('渲染每个会话的标题，当前会话带 active 标记', () => {
    wrapper = mountSidebar({ activeIndex: 1 })
    const items = wrapper.findAll('.chat-item')
    expect(items).toHaveLength(2)
    expect(wrapper.text()).toContain('赤壁之战')
    expect(items[1].classes()).toContain('active')
    expect(items[0].classes()).not.toContain('active')
  })

  test('标题为空时显示"新对话"兜底', () => {
    wrapper = mount(SessionSidebar, {
      props: { sessions: [{ ...sessions[0], title: '' }], activeIndex: 0, collapsed: false },
      global: { plugins: [Layui] },
    })
    expect(wrapper.find('.chat-title').text()).toBe('新对话')
  })

  test('折叠态加 sidebar-collapsed', () => {
    wrapper = mountSidebar({ collapsed: true })
    expect(wrapper.find('.chat-sidebar').classes()).toContain('sidebar-collapsed')
  })

  test('新建 / 选择 / 删除分别派发 create / select / remove', async () => {
    wrapper = mountSidebar()
    await wrapper.find('.new-chat-button').trigger('click')
    expect(wrapper.emitted('create')).toHaveLength(1)

    await wrapper.findAll('.chat-item-content')[1].trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([1])

    await wrapper.findAll('.delete-icon')[0].trigger('click')
    expect(wrapper.emitted('remove')?.[0]).toEqual([0])
  })
})

describe('ChatMessages 消息列表', () => {
  let wrapper: VueWrapper<any>

  afterEach(() => wrapper?.unmount())

  const mountMessages = (props: Record<string, unknown> = {}) =>
    mount(ChatMessages, {
      props: {
        messages: [],
        loading: false,
        isFirstLoad: false,
        quickPrompts: ['赤壁之战在哪？'],
        ...props,
      },
      global: { plugins: [Layui] },
    })

  const assistant = (extra: Partial<ChatMessage> = {}): ChatMessage => ({
    role: 'assistant',
    content: '**赤壁**在今湖北',
    time: Date.now(),
    fromKg: true,
    ...extra,
  })

  test('空会话且非首次加载：显示空态与快捷提示，点提示派发 ask', async () => {
    wrapper = mountMessages()
    expect(wrapper.text()).toContain('开始新的对话')
    expect(wrapper.text()).toContain('常用提示')

    await wrapper.find('.prompt-item').trigger('click')
    expect(wrapper.emitted('ask')?.[0]).toEqual(['赤壁之战在哪？'])
  })

  test('首次加载显示加载态而不是空态', () => {
    wrapper = mountMessages({ isFirstLoad: true })
    expect(wrapper.text()).toContain('正在加载聊天记录')
    expect(wrapper.text()).not.toContain('开始新的对话')
  })

  test('用户消息原样显示，AI 消息走 Markdown 渲染', () => {
    wrapper = mountMessages({
      messages: [
        { role: 'user', content: '赤壁之战', time: Date.now(), fromKg: true },
        assistant(),
      ],
    })
    const items = wrapper.findAll('.message-item')
    expect(items).toHaveLength(2)
    expect(items[0].classes()).toContain('user-message')
    expect(items[1].classes()).toContain('ai-message')
    // Markdown 渲染后是 <strong>，不是原样的 **
    expect(items[1].find('strong').text()).toBe('赤壁')
  })

  test('思考过程按块渲染', () => {
    wrapper = mountMessages({ messages: [assistant({ thinking: ['先看**时间**', '再看地点'] })] })
    expect(wrapper.findAll('.thinking-section')).toHaveLength(1)
    expect(wrapper.find('.thinking-content').findAll('div')).toHaveLength(2)
  })

  test('复制按钮派发 copy 并带上消息正文', async () => {
    wrapper = mountMessages({ messages: [assistant()] })
    await wrapper.find('.message-actions .action-icon').trigger('click')
    expect(wrapper.emitted('copy')?.[0]).toEqual(['**赤壁**在今湖北'])
  })

  test('展开/收起知识图谱派发 kg-toggle(true/false)', async () => {
    wrapper = mountMessages({ messages: [assistant({ kgContext: '{"nodes":[],"lines":[]}' })] })
    const header = wrapper.find('.kg-header')

    await header.trigger('click')
    expect(wrapper.emitted('kg-toggle')?.[0]).toEqual([false])   // 默认展开 → 点一下收起

    await header.trigger('click')
    expect(wrapper.emitted('kg-toggle')?.[1]).toEqual([true])
  })

  test('默认展开：新到的 AI 消息直接显示图谱区块', () => {
    wrapper = mountMessages({ messages: [assistant({ kgContext: '{"nodes":[],"lines":[]}' })] })
    expect(wrapper.find('.kg-graph-wrapper').exists()).toBe(true)
  })

  test('"查看引用图谱信息"切换参考信息块', async () => {
    wrapper = mountMessages({ messages: [assistant({ kgContext: '关系文本' })] })
    expect(wrapper.find('.kg-content-formatted').exists()).toBe(false)

    await wrapper.find('.kg-reference button, .kg-reference .layui-btn').trigger('click')
    expect(wrapper.find('.kg-content-formatted').exists()).toBe(true)
  })

  test('图谱节点点击向上派发 node-click', async () => {
    wrapper = mountMessages({
      messages: [assistant({
        kgContext: 'x',
        kg_data: { nodes: [{ id: 'n1', name: '赤壁' }], lines: [] },
      })],
    })
    wrapper.findComponent({ name: 'KgGraph' }).vm.$emit('node-click', { id: 'n1' })
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('node-click')?.[0]).toEqual([{ id: 'n1' }])
  })

  test('生成中显示"思考中"气泡', () => {
    wrapper = mountMessages({ messages: [assistant()], loading: true })
    expect(wrapper.find('.thinking-animation').text()).toContain('思考中')
  })

  test('暴露滚动方法供父页面调用', () => {
    wrapper = mountMessages({ messages: [assistant()] })
    expect(typeof wrapper.vm.scrollToBottom).toBe('function')
    expect(typeof wrapper.vm.scrollToTop).toBe('function')
  })
})

describe('ChatInput 输入区', () => {
  let wrapper: VueWrapper<any>

  afterEach(() => wrapper?.unmount())

  const mountInput = (props: Record<string, unknown> = {}) =>
    mount(ChatInput, {
      props: { modelValue: '', loading: false, ...props },
      global: { plugins: [Layui] },
    })

  test('输入框内容经 update:modelValue 回传（v-model 契约）', async () => {
    wrapper = mountInput()
    await wrapper.findComponent({ name: 'LayTextarea' }).vm.$emit('update:modelValue', '赤壁')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['赤壁'])
  })

  test('点发送按钮派发 send；加载中置灰', async () => {
    wrapper = mountInput({ modelValue: '赤壁' })
    await wrapper.find('.send-button').trigger('click')
    expect(wrapper.emitted('send')).toHaveLength(1)

    wrapper.unmount()
    wrapper = mountInput({ modelValue: '赤壁', loading: true })
    expect(wrapper.find('.send-button').classes()).toContain('disabled')
  })

  test('Enter 派发 send，Shift+Enter 不派发（留给换行）', async () => {
    wrapper = mountInput({ modelValue: '赤壁' })
    const textarea = wrapper.find('textarea')

    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()

    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  test('点清空按钮派发 clear', async () => {
    wrapper = mountInput({ modelValue: '赤壁' })
    await wrapper.find('.clear-button').trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
  })
})

describe('KgNodeDrawer 节点详情抽屉', () => {
  let wrapper: VueWrapper<any>

  afterEach(() => wrapper?.unmount())

  const NODE = {
    type: 'Event',
    EventName: '赤壁之战',
    DynastyName: '三国',
    StartDate: '208',
    EndDate: '209',
    Place: '赤壁',
    source_text: '曹操南征…',
    relations: [{ relation: '发生地', targets: ['赤壁'], evidences: ['e1'] }],
  }

  test('节点为空时不渲染内容', () => {
    wrapper = mount(KgNodeDrawer, {
      props: { modelValue: true, node: null },
      global: { plugins: [Layui] },
      attachTo: document.body,
    })
    expect(document.querySelector('.kg-node-detail')).toBeNull()
  })

  test('渲染名称、摘要与来源原文（内容 teleport 到 body）', async () => {
    wrapper = mount(KgNodeDrawer, {
      props: { modelValue: true, node: NODE },
      global: { plugins: [Layui] },
      attachTo: document.body,
    })
    await wrapper.vm.$nextTick()

    const detail = document.querySelector('.kg-node-detail') as HTMLElement
    expect(detail).not.toBeNull()
    expect(detail.textContent).toContain('赤壁之战')
    expect(detail.textContent).toContain('三国')
    expect(detail.textContent).toContain('来源原文')
    expect(detail.querySelectorAll('.kg-node-chip').length).toBeGreaterThan(0)
  })
})
