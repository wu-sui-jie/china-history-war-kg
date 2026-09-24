/** 两个关键页面的挂载用例（2026-09-25，S3-4）。
 *
 * 选这两页的原因：`GlobalSearch` 是站内跳转的公共入口（结果项的 entity_route /
 * graph_route / timeline_route 是三个知识页的入口，改路由或改接口都会断在这里），
 * `Dashboard` 是登录后的落地页（首屏数据来自 /api/dashboard/overview）。
 * 两页此前都没有任何测试，挂载即崩、接口打错也没人发现。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import Layui from '@layui/layui-vue'

import { layerSpies, makeTestRouter } from './helpers'
import Http from '@/api/http'
import GlobalSearch from '@/views/knowledge/GlobalSearch.vue'
import Dashboard from '@/views/workspace/Dashboard.vue'
import { getDashboardOverview } from '@/api/module/workspace'

vi.mock('@/api/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('@/api/module/workspace', () => ({
  getDashboardOverview: vi.fn(),
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

describe('GlobalSearch 全局搜索', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => vi.clearAllMocks())
  afterEach(() => wrapper?.unmount())

  test('空关键词只提示，不发请求', async () => {
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter()] } })
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
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter()] } })
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
    wrapper = mount(GlobalSearch, { global: { plugins: [Layui, makeTestRouter()] } })
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
})

describe('Dashboard 首页仪表盘', () => {
  let wrapper: VueWrapper<any>

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

  beforeEach(() => vi.clearAllMocks())
  afterEach(() => wrapper?.unmount())

  test('挂载即拉总览，卡片与刷新时间都有值，且首屏不弹提示', async () => {
    overview.mockResolvedValue({ code: 200, data: payload })
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter()] } })
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
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter()] } })
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
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter()] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('数据库炸了', { icon: 2 })
    expect(wrapper.vm.loading).toBe(false)
    expect(wrapper.vm.cards).toEqual([])
  })

  test('请求抛异常时提示兜底文案（不能让首屏一直转圈）', async () => {
    overview.mockRejectedValue(new Error('network down'))
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    wrapper = mount(Dashboard, { global: { plugins: [Layui, makeTestRouter()] } })
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('加载首页数据失败', { icon: 2 })
    expect(wrapper.vm.loading).toBe(false)
    consoleError.mockRestore()
  })
})
