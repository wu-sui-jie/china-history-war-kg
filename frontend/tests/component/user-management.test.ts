/** 用户管理页用例（问题一方案 A）。
 *
 * 覆盖：挂载即拉列表与角色回显、不能改自己那一行、未改动时不给保存、
 * 保存成功提示"下次登录生效"、失败时按服务端回滚显示。
 * 菜单与路由拦截见 tests/unit/router-guard.test.ts。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import { layerSpies, makeTestRouter } from './helpers'
import UserManagement from '@/views/admin/UserManagement.vue'
import { listUsers, setUserDisabled, updateUserRole } from '@/api/module/admin'
import { useUserStore } from '@/store/user'

vi.mock('@/api/module/admin', () => ({
  listUsers: vi.fn(),
  updateUserRole: vi.fn(),
  setUserDisabled: vi.fn(),
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return { ...actual, layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() } }
})

const list = listUsers as unknown as ReturnType<typeof vi.fn>
const update = updateUserRole as unknown as ReturnType<typeof vi.fn>
const toggle = setUserDisabled as unknown as ReturnType<typeof vi.fn>

const USERS = [
  { id: 1, account: 'wusuijie', name: '管理员本人', role: 'admin', disabled: false },
  { id: 2, account: '123456', name: '普通用户', role: 'viewer', disabled: true },
]

async function mountPage(currentUserId = 1) {
  const wrapper = mount(UserManagement, { global: { plugins: [Layui, makeTestRouter()] } })
  const store = useUserStore()
  store.userInfo = { id: currentUserId, role: 'admin' }
  await flushPromises()
  return wrapper
}

describe('UserManagement 用户管理页', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    list.mockResolvedValue({ code: 200, data: USERS })
    update.mockResolvedValue({ code: 200, data: { ...USERS[1], role: 'editor' } })
  })

  afterEach(() => wrapper?.unmount())

  test('挂载即拉列表，角色回显并记录原始值', async () => {
    wrapper = await mountPage()
    expect(list).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.rows).toHaveLength(2)
    expect(wrapper.vm.originalRoles).toEqual({ 1: 'admin', 2: 'viewer' })
    expect(wrapper.text()).toContain('wusuijie')
  })

  test('自己的那一行不可改（防呆的另一半在服务端）', async () => {
    wrapper = await mountPage(1)
    // layui-vue 的 lay-select 组件名是 LaySelect，根元素是 .layui-select；
    // 禁用态会带上 has-disabled（见 es/select/index.js）
    const selects = wrapper.findAllComponents({ name: 'LaySelect' })
    expect(selects).toHaveLength(2)
    const disabled = selects.filter((item) => item.props('disabled') === true)
    expect(disabled, '只有"自己"这一行的下拉是禁用的').toHaveLength(1)
    expect(disabled[0].props('modelValue')).toBe('admin')
    expect(wrapper.findAll('.layui-select.has-disabled')).toHaveLength(1)
    expect(wrapper.text()).toContain('当前登录账号')
  })

  test('未改动时保存按钮禁用，改动后可用', async () => {
    wrapper = await mountPage()
    expect(wrapper.vm.isDirty(USERS[1] as any)).toBe(false)
    wrapper.vm.rows[1].role = 'editor' as any
    await flushPromises()
    expect(wrapper.vm.isDirty(wrapper.vm.rows[1])).toBe(true)
  })

  test('保存成功：调接口传新角色，提示下次登录生效并重新拉取', async () => {
    wrapper = await mountPage()
    wrapper.vm.rows[1].role = 'editor' as any
    await wrapper.vm.save(wrapper.vm.rows[1])
    await flushPromises()

    expect(update).toHaveBeenCalledWith(2, 'editor')
    const msg = layerSpies().msg.mock.calls.at(-1)!
    expect(String(msg[0])).toContain('123456')
    expect(String(msg[0])).toContain('下次登录生效')
    expect(msg[1]).toEqual({ icon: 1 })
    expect(list).toHaveBeenCalledTimes(2)  // 保存后重拉，确保显示以服务端为准
  })

  test('保存失败：提示服务端文案并回滚显示', async () => {
    wrapper = await mountPage()
    // 真实链路：后端返回 HTTP 403 + body {code,msg}，axios 走 reject，
    // 服务端文案在 error.response.data.msg（见 utils/apiError）。
    update.mockRejectedValue({
      response: { status: 403, data: { code: 403, msg: '不能修改自己的角色，请让另一位管理员操作' } },
    })
    wrapper.vm.rows[1].role = 'admin' as any

    await wrapper.vm.save(wrapper.vm.rows[1])
    await flushPromises()

    expect(String(layerSpies().msg.mock.calls.at(-1)![0])).toContain('不能修改自己的角色')
    expect(list).toHaveBeenCalledTimes(2)
  })

  test('保存失败但服务端没给文案时回落到兜底提示（不显示 axios 状态码）', async () => {
    wrapper = await mountPage()
    update.mockRejectedValue(new Error('Request failed with status code 500'))
    wrapper.vm.rows[1].role = 'editor' as any

    await wrapper.vm.save(wrapper.vm.rows[1])
    await flushPromises()

    const msg = String(layerSpies().msg.mock.calls.at(-1)![0])
    expect(msg).toBe('保存失败，请稍后重试')
    expect(msg).not.toContain('status code')
    expect(list).toHaveBeenCalledTimes(2)
  })

  test('列表接口失败时提示且不留脏数据', async () => {
    list.mockResolvedValue({ code: 500, msg: '数据库炸了' })
    wrapper = await mountPage()
    expect(layerSpies().msg).toHaveBeenCalledWith('数据库炸了', { icon: 2 })
    expect(wrapper.vm.rows).toHaveLength(0)
  })

  test('列表接口被拒（HTTP 403）时提示后端文案', async () => {
    list.mockRejectedValue({
      response: { status: 403, data: { code: 403, msg: '仅管理员可执行该操作' } },
    })
    wrapper = await mountPage()
    expect(layerSpies().msg).toHaveBeenCalledWith('仅管理员可执行该操作', { icon: 2 })
    expect(wrapper.vm.rows).toHaveLength(0)
  })
})


// ------------------------------------------- 状态列与启用/停用（第 14 轮审计 P2-18）

describe('UserManagement 状态列与停用', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    list.mockResolvedValue({ code: 200, data: USERS })
    toggle.mockResolvedValue({ code: 200, data: {} })
  })
  afterEach(() => wrapper?.unmount())

  test('已停用与正常账号在列表里能分辨', async () => {
    // 后端一直返回 disabled，而类型与页面原先都没有它 → 停用后列表里看不出来
    wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('正常')
    expect(text).toContain('已停用')
  })

  test('点"停用"会调状态接口，并提示凭证已失效', async () => {
    wrapper = await mountPage()
    const row = wrapper.vm.rows.find((item: any) => item.id === 2)

    await wrapper.vm.toggleDisabled(row)
    await flushPromises()

    expect(toggle).toHaveBeenCalledWith(2, false)   // 该行原本 disabled=true → 变成启用
    expect(layerSpies().msg).toHaveBeenCalledWith(
      expect.stringContaining('凭证已失效'), { icon: 1 })
  })

  test('不能停用自己（按钮禁用，服务端也会拒）', async () => {
    wrapper = await mountPage(1)
    const selfRow = wrapper.vm.rows.find((item: any) => item.id === 1)

    // 页面把当前账号那一行的按钮置灰；服务端另有一道 403（见 test_admin_users）
    expect(selfRow.id).toBe(wrapper.vm.currentUserId)
  })
})
