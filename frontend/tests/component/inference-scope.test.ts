/** 问答记录隔离的"离开页面"路径。
 *
 * 隔离本身（key 拼账号、老全局记录归档）在 tests/unit/user-scoped-storage.test.ts 里钉过，
 * 但那只覆盖了存储层的读写规则，覆盖不到页面：**登出与 token 过期都会先清 userInfo、
 * 再跳登录页，随后页面卸载并 flush 历史**。卸载那一刻若还去读 userInfo.id，拿到的是
 * undefined，key 退回全局 'chatHistory'，整份记录被复制进公共桶——任何后续账号都能读到。
 * 这条用例走的就是这条真实顺序（clearSession → unmount），断言"落到原账号的桶、公共桶干净"。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import { makeTestRouter } from './helpers'
import Inference from '@/views/inference/index.vue'
import { userInfo } from '@/api/module/user'
import { useUserStore } from '@/store/user'

vi.mock('@/api/module/user', () => ({
  userInfo: vi.fn(),
  menu: vi.fn(),
  permission: vi.fn(),
}))

// 图谱组件会 import echarts 并在挂载时初始化画布，与本用例无关，打桩掉减少噪音
vi.mock('@/views/inference/components/KgGraph.vue', () => ({
  default: { template: '<div />' },
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return { ...actual, layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() } }
})

const userInfoMock = userInfo as unknown as ReturnType<typeof vi.fn>

const CHAT_KEY = 'chatHistory'
const SCOPED_KEY = 'chatHistory:u3'

/** 挂载页面并等 onMounted 里的 ensureUserInfo + loadChatHistory 收尾。 */
async function mountPage(): Promise<VueWrapper<any>> {
  const wrapper = mount(Inference, { global: { plugins: [Layui, makeTestRouter()] } })
  await flushPromises()
  return wrapper
}

describe('推理页的问答记录按账号落盘', () => {
  let wrapper: VueWrapper<any> | undefined

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    userInfoMock.mockResolvedValue({ code: 200, data: { id: 3, role: 'editor' } })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    localStorage.clear()
  })

  test('登出（先清会话再卸载）时历史落在原账号桶，不写进公共 key', async () => {
    const store = useUserStore()
    store.token = 'tk-3'
    wrapper = await mountPage()

    wrapper.vm.createNewChat()   // 触发一次落盘
    await flushPromises()
    expect(JSON.parse(localStorage.getItem(SCOPED_KEY) as string)).toHaveLength(2)

    // BasicLayout.logOut / handleUnauthorized 的真实顺序：先清凭据，页面随路由切换卸载
    store.clearSession()
    wrapper.unmount()
    wrapper = undefined

    expect(localStorage.getItem(CHAT_KEY), '公共桶不能被写上').toBeNull()
    expect(JSON.parse(localStorage.getItem(SCOPED_KEY) as string)).toHaveLength(2)
    expect(localStorage.getItem(SCOPED_KEY)).not.toBeNull()
  })

  test('已登录但拿不到账号 id 时宁可不落盘，也不写进公共桶', async () => {
    const store = useUserStore()
    store.token = 'tk-unknown'
    userInfoMock.mockRejectedValue(new Error('userinfo 挂了'))   // store 内部吞掉，userInfo 保持空
    wrapper = await mountPage()

    wrapper.vm.createNewChat()
    await flushPromises()

    expect(localStorage.getItem(CHAT_KEY)).toBeNull()
  })

  test('未登录（独立使用该页面）沿用全局 key：与改造前一致', async () => {
    const store = useUserStore()
    store.token = ''
    // 未登录时 /api/userinfo 会 401（拦截器拒掉），store 保持空 userInfo —— 与真实链路同形
    userInfoMock.mockRejectedValue(new Error('未登录'))
    wrapper = await mountPage()

    wrapper.vm.createNewChat()
    await flushPromises()

    expect(JSON.parse(localStorage.getItem(CHAT_KEY) as string)).toHaveLength(2)
  })
})
