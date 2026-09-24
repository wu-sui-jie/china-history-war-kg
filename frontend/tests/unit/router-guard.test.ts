/** 路由守卫的角色分级（问题一方案 A 的"URL 直达也要拦"）。
 *
 * 三个角色 × 三类页面：
 *  - /workspace/dashboard      无需角色
 *  - /knowledge-list           需要 editor（数据维护组）
 *  - /admin/users              需要 admin（用户管理）
 * 分级语义：admin ⊃ editor ⊃ viewer，admin 能进 editor 级页面，反之不行。
 * 菜单入口是否出现由后端 get_menu 决定（见后端实测），这里只管"直达 URL"这一层。
 */

import { beforeEach, describe, expect, test, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { layerSpies } from '../component/helpers'
import router from '@/router'
import { useUserStore } from '@/store/user'

vi.mock('@/api/module/user', () => ({
  menu: vi.fn(async () => ({ code: 200, data: [] })),
  permission: vi.fn(async () => ({ code: 200, data: [] })),
  userInfo: vi.fn(async () => ({ code: 200, data: { id: 7, role: 'viewer' } })),
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return { ...actual, layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() } }
})

/** 以指定角色访问某路径，返回最终落地的路径。
 *
 * 每次都先退到一个中立页面：Vue Router 对"导航到当前路径"的重复导航不会跑守卫，
 * 而测试共用一个 router 实例，不退开会让后续用例静默跳过守卫（假通过）。
 */
async function visit(role: string, path: string): Promise<string> {
  const store = useUserStore()
  store.token = 'fake-token'
  store.userInfo = role ? { id: 7, role } : {}
  await router.push('/error/404').catch(() => undefined)
  await router.push(path).catch(() => undefined)
  await router.isReady()
  return router.currentRoute.value.path
}

describe('路由守卫：角色分级', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  test.each([
    ['admin', '/admin/users', '/admin/users'],
    ['editor', '/admin/users', '/workspace/dashboard'],
    ['viewer', '/admin/users', '/workspace/dashboard'],
  ])('%s 访问用户管理页 -> %s', async (role, target, expected) => {
    const landed = await visit(role, target)
    expect(landed).toBe(expected)
  })

  test.each([
    ['admin', '/knowledge-list/event', '/knowledge-list/event'],
    ['editor', '/knowledge-list/event', '/knowledge-list/event'],
    ['viewer', '/knowledge-list/event', '/workspace/dashboard'],
  ])('%s 访问数据维护页 -> %s', async (role, target, expected) => {
    const landed = await visit(role, target)
    expect(landed).toBe(expected)
  })

  test.each([
    ['admin', '/knowledge/text-extract'],
    ['editor', '/knowledge/text-extract'],
  ])('%s 可进文本实体识别页', async (role, target) => {
    expect(await visit(role, target)).toBe(target)
  })

  test('viewer 进文本实体识别页被拦下（会消耗 LLM 配额）', async () => {
    expect(await visit('viewer', '/knowledge/text-extract')).toBe('/workspace/dashboard')
  })

  test('viewer 可进只读页面', async () => {
    expect(await visit('viewer', '/knowledge/timeline')).toBe('/knowledge/timeline')
    expect(await visit('viewer', '/knowledge/rag')).toBe('/knowledge/rag')
  })

  test('角色未知（未加载）时按不足处理：拦到仪表盘', async () => {
    expect(await visit('', '/admin/users')).toBe('/workspace/dashboard')
  })

  test('被拦下时给出对应文案：管理员页与数据运营页提示不同', async () => {
    await visit('viewer', '/admin/users')
    expect(layerSpies().msg).toHaveBeenCalledWith('该页面仅管理员可访问', { icon: 2 })

    vi.clearAllMocks()
    await visit('viewer', '/knowledge-list/event')
    expect(layerSpies().msg).toHaveBeenCalledWith('当前账号为普通用户，无法访问数据运营页面', { icon: 2 })
  })
})
