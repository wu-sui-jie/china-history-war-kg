/** 文本实体识别页的等待体验与图谱端点解析。
 *
 * 三件事在这里钉住：
 *
 * 1. **加载态要有"已用时"**（M3.1）。没有它，用户分不清"还在跑"与"已经卡死"，
 *    于是会去刷新页面——而刷新会让在跑的识别结果丢掉（请求随页面销毁）。
 * 2. **计时器不能泄漏**（M3.2）。请求不属于组件生命周期：切走页面后组件已卸载、
 *    `await` 却还会返回。计时器只在 `finally` 里收，切回来就会叠出好几个，秒数跳着涨。
 * 3. **图谱连线要能落到节点上**。关系的目标名可能是实体的另一个写法（事件-地点关系
 *    里的 `modern_name` 填的是地点名，而节点 id 用的是地点的 `geo_name`）——不解析的话
 *    `KgGraph` 会因"两端找不到节点"把这条线静默丢掉：关系列表里有、图谱上没有。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import { makeTestRouter, layerSpies } from './helpers'
import ExtractPage from '@/views/knowledge/TextEntityExtract.vue'
import { extractEntitiesEvents } from '@/api/module/node'
import { userInfo } from '@/api/module/user'

vi.mock('@/api/module/node', () => ({
  extractEntitiesEvents: vi.fn(),
}))

vi.mock('@/api/module/user', () => ({
  userInfo: vi.fn(),
  menu: vi.fn(),
  permission: vi.fn(),
}))

// echarts 画布与本用例无关，打桩成能读 props 的空组件
vi.mock('@/views/inference/components/KgGraph.vue', () => ({
  default: { name: 'KgGraph', props: ['data'], template: '<div />' },
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return { ...actual, layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() } }
})

const extractMock = extractEntitiesEvents as unknown as ReturnType<typeof vi.fn>
const userInfoMock = userInfo as unknown as ReturnType<typeof vi.fn>

const RESULT = {
  entities: {
    places: [{ geo_name: '潼关', modern_name: '潼关县' }],
    organizations: [],
    persons: [],
  },
  events: [{ EventName: '商汤灭夏战争', DynastyName: '商朝' }],
  relations: {
    // 目标填的是地点的现代名：图谱节点 id 是 geo_name，必须解析到它才能连上
    event_place: [{ EventName: '商汤灭夏战争', PlaceName: null, modern_name: '潼关县', relation: '主战场' }],
    event_person: [],
    event_organization: [],
    event_event: [],
  },
  summary: {
    place_count: 1, organization_count: 0, person_count: 0, event_count: 1,
    relation_count: 1,
  },
  process_time: 11.2,
}

/** 让旧登录态的历史记录里有一条识别结果，点开即渲染（页面的结果只能这么喂进去）。 */
function seedHistory() {
  localStorage.setItem('extractHistory:u3', JSON.stringify([
    { id: 1, title: '商灭夏', text: '商汤发起灭夏战争。', result: RESULT, time: Date.now() },
  ]))
}

async function mountPage(): Promise<VueWrapper<any>> {
  const wrapper = mount(ExtractPage, { global: { plugins: [Layui, makeTestRouter()] } })
  await vi.advanceTimersByTimeAsync(0)
  return wrapper
}

describe('文本实体识别页的等待体验', () => {
  let wrapper: VueWrapper<any> | undefined

  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    userInfoMock.mockResolvedValue({ code: 200, data: { id: 3, role: 'editor' } })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.useRealTimers()
  })

  test('加载态显示递增的已用时，且不限制用户行为', async () => {
    let release: (value: unknown) => void = () => {}
    extractMock.mockReturnValue(new Promise((resolve) => { release = resolve }))
    wrapper = await mountPage()

    await wrapper.find('textarea').setValue('商汤发起灭夏战争。')
    await wrapper.find('.extract-button').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(wrapper.find('.loading-state').exists()).toBe(true)
    expect(wrapper.find('.loading-elapsed').text()).toContain('0 秒')
    // 文案不能说"请保持页面打开"这类前提下不成立的限制（切页面其实不会中断）
    expect(wrapper.text()).not.toMatch(/保持页面打开|请勿关闭|不要关闭/)
    expect(wrapper.text()).toContain('历史记录')

    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper.find('.loading-elapsed').text()).toContain('4 秒')

    release({ code: 200, data: RESULT })
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.find('.loading-state').exists()).toBe(false)
  })

  test('完成提示点明结果已存入历史记录', async () => {
    extractMock.mockResolvedValue({ code: 200, data: RESULT })
    wrapper = await mountPage()

    await wrapper.find('textarea').setValue('商汤发起灭夏战争。')
    await wrapper.find('.extract-button').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(layerSpies().msg).toHaveBeenCalledTimes(1)
    expect(String(layerSpies().msg.mock.calls[0][0])).toContain('已存入左侧历史记录')
  })

  test('卸载后计时器被收掉，不会在切回来时重复计时', async () => {
    let release: (value: unknown) => void = () => {}
    extractMock.mockReturnValue(new Promise((resolve) => { release = resolve }))
    wrapper = await mountPage()

    await wrapper.find('textarea').setValue('商汤发起灭夏战争。')
    await wrapper.find('.extract-button').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(vi.getTimerCount()).toBe(1)

    wrapper.unmount()
    wrapper = undefined
    expect(vi.getTimerCount()).toBe(0)

    release({ code: 200, data: RESULT })
    await vi.advanceTimersByTimeAsync(0)
  })
})

describe('图谱连线解析到节点', () => {
  let wrapper: VueWrapper<any> | undefined

  beforeEach(() => {
    // 这一组不测计时，但 mountPage 用 advanceTimersByTimeAsync 收尾（见上），保持一致
    vi.useFakeTimers()
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    seedHistory()
    userInfoMock.mockResolvedValue({ code: 200, data: { id: 3, role: 'editor' } })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.useRealTimers()
  })

  test('关系目标用实体别名时，连线指到该实体的节点 id', async () => {
    wrapper = await mountPage()

    // 从历史记录里载入那条结果（页面没有别的入口把结果灌进去）
    await wrapper.find('.history-item-content').trigger('click')

    const graph = wrapper.findComponent({ name: 'KgGraph' })
    expect(graph.exists()).toBe(true)
    const data = graph.props('data') as { nodes: any[]; lines: any[] }
    const ids = data.nodes.map((node) => node.id)
    expect(ids).toContain('潼关')
    expect(data.lines).toEqual([
      { from: '商汤灭夏战争', to: '潼关', text: '主战场', inferred: false },
    ])
  })
})
