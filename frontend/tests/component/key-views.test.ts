/** 关键页面的挂载用例。
 *
 * 选这些页面的原因：`GlobalSearch` 是站内跳转的公共入口（结果项的 entity_route /
 * graph_route / timeline_route 是三个知识页的入口，改路由或改接口都会断在这里），
 * `Dashboard` 是登录后的落地页（首屏数据来自 /api/dashboard/overview）。
 * `TimelineView` 与 `EntityDetail` 覆盖"请求失败必须给提示"这条契约：
 * 后端失败返回真正的 4xx/5xx，**只有 catch 分支才拿得到后端文案**，
 * 页面若不接住异常就会静默不动、用户完全看不到原因。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import { layerSpies, makeTestRouter } from './helpers'
import Http from '@/api/http'
import GlobalSearch from '@/views/knowledge/GlobalSearch.vue'
import Dashboard from '@/views/workspace/Dashboard.vue'
import EntityDetail from '@/views/knowledge/EntityDetail.vue'
import TimelineView from '@/views/knowledge/TimelineView.vue'
import { getDashboardOverview, getEntityDetail, getTimelineEvents } from '@/api/module/workspace'
import { useUserStore } from '@/store/user'

vi.mock('@/api/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('@/api/module/workspace', () => ({
  getDashboardOverview: vi.fn(),
  getEntityDetail: vi.fn(),
  getTimelineEvents: vi.fn(),
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return {
    ...actual,
    layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() },
  }
})

const get = Http.get as unknown as ReturnType<typeof vi.fn>
const overview = getDashboardOverview as unknown as ReturnType<typeof vi.fn>
const timelineEvents = getTimelineEvents as unknown as ReturnType<typeof vi.fn>
const entityDetail = getEntityDetail as unknown as ReturnType<typeof vi.fn>

// 组件 setup 里会 useUserStore（角色过滤）：每个用例先备好一个 Pinia 实例
let pinia: ReturnType<typeof createPinia>
beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

describe('GlobalSearch 全局搜索', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => vi.clearAllMocks())
  afterEach(() => wrapper?.unmount())

  test('空关键词只提示，不发请求', async () => {
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    wrapper.vm.search()
    await flushPromises()

    expect(get).not.toHaveBeenCalled()
    expect(layerSpies().msg).toHaveBeenCalledWith('请输入关键词', { icon: 2 })
    expect(wrapper.vm.searched).toBe(false)
  })

  test('搜索打 /api/search/global 并把结果渲染出来', async () => {
    get.mockResolvedValue({
      code: 200,
      data: [
        { id: 1, type: 'Event', name: '巨鹿之战', type_label: '战争事件', entity_route: '/knowledge/entity/Event/1' },
      ],
    })
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    wrapper.vm.keyword = '  巨鹿  '
    wrapper.vm.search()
    await flushPromises()

    // 关键词要去空白后再发
    expect(get).toHaveBeenCalledWith('/api/search/global', { keyword: '巨鹿' })
    expect(wrapper.vm.results).toHaveLength(1)
    expect(wrapper.text()).toContain('巨鹿之战')
    expect(wrapper.text()).toContain('战争事件')
  })

  test('接口非 200 时结果清空且显示空态', async () => {
    get.mockResolvedValue({ code: 500, msg: '炸了' })
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    wrapper.vm.keyword = '秦'
    wrapper.vm.search()
    await flushPromises()

    expect(wrapper.vm.results).toEqual([])
    expect(wrapper.vm.searched).toBe(true)
    expect(wrapper.text()).toContain('没有找到匹配实体')
  })

  test('带 keyword 查询参数进入时自动搜索（其他页面跳过来的入口）', async () => {
    get.mockResolvedValue({ code: 200, data: [] })
    const router = makeTestRouter()
    await router.push('/?keyword=垓下')
    await router.isReady()

    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, router] } })
    await flushPromises()

    expect(wrapper.vm.keyword).toBe('垓下')
    expect(get).toHaveBeenCalledWith('/api/search/global', { keyword: '垓下' })
  })

  test('接口失败（HTTP 5xx）时清空结果并提示后端文案', async () => {
    // 后端失败一律是 4xx/5xx，axios 走 reject 分支——
    // 组件必须 catch 之后再提示，否则"搜索失败"和"没有匹配"在界面上分不出来。
    get.mockRejectedValue({ response: { status: 500, data: { msg: '全局搜索失败，请稍后重试' } } })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    wrapper.vm.keyword = '秦'
    wrapper.vm.search()
    await flushPromises()

    expect(wrapper.vm.results).toEqual([])
    expect(wrapper.vm.searched).toBe(true)
    expect(layerSpies().msg).toHaveBeenCalledWith('全局搜索失败，请稍后重试', { icon: 2 })
    consoleError.mockRestore()
  })
})

describe('TimelineView 历史时间轴', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => vi.clearAllMocks())
  afterEach(() => wrapper?.unmount())

  test('接口失败时提示文案，而不是静默不动', async () => {
    timelineEvents.mockRejectedValue({
      response: { status: 500, data: { msg: '加载时间轴事件失败，请稍后重试' } },
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    wrapper = mount(TimelineView, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('加载时间轴事件失败，请稍后重试', { icon: 2 })
    consoleError.mockRestore()
  })

  test('接口正常时按 code 分支渲染', async () => {
    timelineEvents.mockResolvedValue({
      code: 200,
      data: { summary: { event_count: 1 }, dynasties: ['秦'], events: [] },
    })

    wrapper = mount(TimelineView, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    expect(wrapper.vm.timeline.summary.event_count).toBe(1)
    expect(layerSpies().msg).not.toHaveBeenCalled()
  })
})

describe('EntityDetail 实体详情', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    useUserStore().userInfo = { id: 1, account: 'tester', role: 'admin' }
  })
  afterEach(() => wrapper?.unmount())

  test('实体不存在（HTTP 404）时提示后端文案', async () => {
    // "实体不存在"在后端是 HTTP 404
    entityDetail.mockRejectedValue({ response: { status: 404, data: { msg: '实体不存在' } } })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    const router = makeTestRouter()
    await router.push('/knowledge/entity-detail?type=Event&id=999')
    await router.isReady()

    wrapper = mount(EntityDetail, { global: { plugins: [Layui, router, pinia] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('实体不存在', { icon: 2 })
    consoleError.mockRestore()
  })
})

describe('Dashboard 首页仪表盘', () => {
  let wrapper: VueWrapper<any>
  let pinia: ReturnType<typeof createPinia>

  const payload = {
    cards: [
      { title: '事件总数', value: 1050 },
      { title: '地点总数', value: 5316 },
    ],
    dynasty_distribution: [
      { name: '秦', value: 120 },
      { name: '汉', value: 0 },
    ],
    quality_snapshot: { missing_required_fields: 3 },
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // Dashboard 用 useHasWriteRole 过滤快捷卡：预置管理员角色，卡片全渲染
    useUserStore().userInfo = { id: 1, account: 'tester', role: 'admin' }
  })
  afterEach(() => wrapper?.unmount())

  test('挂载即拉总览，卡片与刷新时间都有值，且首屏不弹提示', async () => {
    overview.mockResolvedValue({ code: 200, data: payload })
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    expect(overview).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.cards).toHaveLength(2)
    expect(wrapper.vm.lastRefreshTime).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(wrapper.vm.loading).toBe(false)
    expect(layerSpies().msg).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('事件总数')
  })

  test('手动刷新给明确反馈；零值朝代被过滤后用全量兜底', async () => {
    overview.mockResolvedValue({ code: 200, data: payload })
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    await wrapper.vm.loadData(false)
    expect(overview).toHaveBeenCalledTimes(2)
    expect(layerSpies().msg).toHaveBeenCalledWith('仪表盘数据已刷新', { icon: 1 })

    // 有数据的朝代优先；全为 0 时退回全量展示
    expect(wrapper.vm.visibleDynastyDistribution.map((item: any) => item.name)).toEqual(['秦'])
    wrapper.vm.dashboard.dynasty_distribution = [{ name: '汉', value: 0 }]
    expect(wrapper.vm.visibleDynastyDistribution.map((item: any) => item.name)).toEqual(['汉'])
  })

  test('接口返回非 200 时提示后端文案，loading 复位', async () => {
    overview.mockResolvedValue({ code: 500, msg: '数据库炸了' })
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('数据库炸了', { icon: 2 })
    expect(wrapper.vm.loading).toBe(false)
    expect(wrapper.vm.cards).toEqual([])
  })

  test('请求抛异常时提示兜底文案（不能让首屏一直转圈）', async () => {
    overview.mockRejectedValue(new Error('network down'))
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter(), pinia] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('加载首页数据失败', { icon: 2 })
    expect(wrapper.vm.loading).toBe(false)
    consoleError.mockRestore()
  })
})
