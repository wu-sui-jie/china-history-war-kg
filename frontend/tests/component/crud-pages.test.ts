/** 四个知识库管理页的"能加载、能提交"契约用例。
 *
 * 被测行为：这四个页面共用 `useNodeCrudPage`。请求链路或公共组合式函数改坏（例如 vite 代理
 * 切错导致开发态全挂）时不容易当场发现，因此用测试钉住四条底线：
 *   1) 挂载即按当前分页参数拉列表，并按各页自己的展示类型标注数据；
 *   2) 翻页与查询会改参数重拉（pageNum / pageSize / name）；
 *   3) 新增与编辑分别打 /create_node 与 /update_node，payload 带 type 与主名字段；
 *   4) 删除必须经 layer.confirm 确认后才打 /delete_node。
 * 改组合式函数时若改坏任何一条，这里立刻会红。
 */

import type { Component } from 'vue'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import Layui from '@layui/layui-vue'

import { layerSpies, makeTestRouter } from './helpers'
import Http from '@/api/http'
import EventNode from '@/views/knowledge-list/event/EventNode.vue'
import OrgNode from '@/views/knowledge-list/organization/OrgNode.vue'
import PersonNode from '@/views/knowledge-list/person/PersonNode.vue'
import PlaceNode from '@/views/knowledge-list/place/PlaceNode.vue'

vi.mock('@/api/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

// layer 打桩：只替换提示与确认框，组件本体仍用真实 layui 插件（表单校验要真的能跑）
vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return {
    ...actual,
    layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() },
  }
})

const post = Http.post as unknown as ReturnType<typeof vi.fn>

type PageCase = {
  title: string
  component: Component
  /** 提交 payload 里的节点类型 */
  nodeType: string
  /** 主名字段（表单里 required 的那个） */
  nameField: string
  /** 列表数据被标注的展示类型（各页文案不同，看的是这层映射） */
  displayType: string
  /** 列表接口返回的一行 */
  row: Record<string, unknown>
}

const PAGES: PageCase[] = [
  {
    title: 'EventNode 战争事件',
    component: EventNode,
    nodeType: 'Event',
    nameField: 'EventName',
    displayType: 'Event',
    row: { id: 7, EventName: '巨鹿之战', DynastyName: '秦' },
  },
  {
    title: 'OrgNode 参战组织',
    component: OrgNode,
    nodeType: 'Organization',
    nameField: 'OrgName',
    displayType: '参战组织',
    row: { id: 8, OrgName: '秦军' },
  },
  {
    title: 'PersonNode 历史人物',
    component: PersonNode,
    nodeType: 'Person',
    nameField: 'PersonName',
    displayType: '历史人物',
    row: { id: 9, PersonName: '项羽' },
  },
  {
    title: 'PlaceNode 战争地点',
    component: PlaceNode,
    nodeType: 'Place',
    nameField: 'geo_name',
    displayType: '战争地点',
    row: { id: 10, geo_name: '巨鹿' },
  },
]

function mountPage(component: Component, records: Record<string, unknown>[] = [], total = records.length) {
  // 按 URL 分派：列表接口要带 data，写接口只要 code —— 否则成功后的"刷新列表"会踩到空 data
  post.mockImplementation(async (url: string) => {
    if (url === '/api/find_node_page') return { code: 200, data: { records, total } }
    if (url === '/create_node') return { code: 200, msg: '创建成功' }
    if (url === '/update_node') return { code: 200, msg: '修改成功' }
    if (url === '/delete_node') return { code: 200, msg: '删除成功' }
    return { code: 200 }
  })
  return mount(component, {
    global: { plugins: [Layui, makeTestRouter()] },
    attachTo: document.body,
  })
}

/** 打开新增弹层：lay-layer 的内容（含表单）在 visible 为真后才挂载，formRef 那时才可用。 */
async function openForm(wrapper: VueWrapper<any>) {
  wrapper.vm.add()
  await flushPromises()
  await nextTick()
}

describe.each(PAGES)('$title', (page) => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  test('挂载即按当前分页参数拉列表，并按本页类型标注数据', async () => {
    wrapper = mountPage(page.component, [page.row], 1)
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/find_node_page', {
      pageNum: 1,
      pageSize: 10,
      name: '',
      node_type: page.nodeType,
    })
    expect(wrapper.vm.dataSource).toHaveLength(1)
    expect(wrapper.vm.dataSource[0].type).toBe(page.displayType)
    expect(wrapper.vm.page.total).toBe(1)
  })

  test('翻页与查询会带着新参数重拉', async () => {
    wrapper = mountPage(page.component, [page.row])
    await flushPromises()
    post.mockClear()

    // 注意：真实 lay-table 在 dataSource 变化时也会再 emit 一次 change，所以断言
    // "存在带新参数的调用"，而不是"最后一次调用"（后者会被表格自身的回调打乱）
    wrapper.vm.change({ current: 3, limit: 20 })
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/api/find_node_page', expect.objectContaining({
      pageNum: 3,
      pageSize: 20,
      node_type: page.nodeType,
    }))

    wrapper.vm.searchName = '秦'
    wrapper.vm.toSearch()
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/api/find_node_page', expect.objectContaining({
      pageNum: 1,
      name: '秦',
      node_type: page.nodeType,
    }))
  })

  test('新增：提交打 /create_node，payload 带类型与主名字段，未填字段为 null', async () => {
    wrapper = mountPage(page.component)
    await flushPromises()
    post.mockClear()

    await openForm(wrapper)
    wrapper.vm.formData[page.nameField] = '测试名称'
    wrapper.vm.submit()
    await flushPromises()

    const call = post.mock.calls.find(([url]) => url === '/create_node')
    expect(call, '提交后应打 /create_node').toBeTruthy()
    const payload = call![1]
    expect(payload.type).toBe(page.nodeType)
    expect(payload[page.nameField]).toBe('测试名称')
    expect(payload.id).toBeUndefined()
    // 空的可选字段统一送 null（后端按 null 落库为空，不能送空串）
    expect(Object.values(payload).some((value) => value === null)).toBe(true)
    expect(Object.values(payload).every((value) => value !== '')).toBe(true)
    expect(wrapper.vm.visible).toBe(false)
    expect(layerSpies().msg).toHaveBeenCalledWith('创建成功', { icon: 1 })
  })

  test('必填项为空时不发请求，只提示', async () => {
    wrapper = mountPage(page.component)
    await flushPromises()
    post.mockClear()

    await openForm(wrapper)
    wrapper.vm.formData[page.nameField] = ''
    wrapper.vm.submit()
    await flushPromises()

    expect(post.mock.calls.filter(([url]) => url === '/create_node')).toHaveLength(0)
    expect(wrapper.vm.visible).toBe(true)
  })

  test('编辑：表单带上行数据后提交打 /update_node 并携带 id', async () => {
    wrapper = mountPage(page.component, [page.row])
    await flushPromises()
    post.mockClear()

    wrapper.vm.viewDetail(page.row)
    await flushPromises()
    await nextTick()
    expect(wrapper.vm.formData.id).toBe(page.row.id)

    wrapper.vm.submit()
    await flushPromises()

    const call = post.mock.calls.find(([url]) => url === '/update_node')
    expect(call, '提交后应打 /update_node').toBeTruthy()
    expect(call![1].id).toBe(page.row.id)
    expect(call![1].type).toBe(page.nodeType)
  })

  test('删除：layer.confirm 确认后才打 /delete_node 并刷新列表', async () => {
    wrapper = mountPage(page.component, [page.row])
    await flushPromises()
    post.mockClear()

    wrapper.vm.deleteNode(page.row)
    expect(layerSpies().confirm).toHaveBeenCalledTimes(1)
    expect(post).not.toHaveBeenCalled()  // 未点确认前不许发请求

    const options = layerSpies().confirm.mock.calls[0][1] as { yes: (index: number) => void }
    options.yes(0)
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/delete_node', { type: page.nodeType, id: page.row.id })
    expect(post).toHaveBeenCalledWith('/api/find_node_page', expect.anything())
  })
})
