/** RAG 嵌入页的跨应用身份契约（问题二方案 A）。
 *
 * 契约的另一半在 RAG 前端：它只认 `{type:'cw-user', uid}` 且只认同源（见 RAG 仓库
 * tests/unit/user-scope.test.ts）。这里钉住主应用发出的这一半——载荷形状、
 * targetOrigin、以及"在 iframe load 之后才发"（早于 load 发会丢消息）。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import RagAssistant from '@/views/knowledge/RagAssistant.vue'
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

/** 把 iframe 的 contentWindow 换成一个可控替身，并取到 postMessage 间谍。 */
async function stubFrameWindow(wrapper: VueWrapper<any>) {
  const iframe = wrapper.find('iframe').element as HTMLIFrameElement
  const postMessage = vi.fn()
  Object.defineProperty(iframe, 'contentWindow', { value: { postMessage }, configurable: true })
  return postMessage
}

describe('RagAssistant 身份传递', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => wrapper?.unmount())

  test('iframe load 后把当前账号发给 RAG 前端（同源 targetOrigin）', async () => {
    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    await flushPromises()
    const postMessage = await stubFrameWindow(wrapper)

    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    expect(postMessage).toHaveBeenCalledTimes(1)
    // targetOrigin 用 '/'：只发给同源文档。改成 '*' 会让消息投给任意嵌入方
    // role 一起带上（RAG 侧只存不用；老版本主应用不发时 RAG 按空串处理）
    expect(postMessage).toHaveBeenCalledWith({ type: 'cw-user', uid: '7', role: 'viewer' }, '/')
  })

  test('账号未知时先拉取再发（不能把空 uid 发出去）', async () => {
    const store = useUserStore()
    store.userInfo = {}
    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    await flushPromises()   // 等 ensureUserInfo 拉到账号（此刻 iframe 会因 key 变化重建）

    // 账号变化会重建 iframe，所以间谍要挂在重建之后的那一个元素上
    const postMessage = await stubFrameWindow(wrapper)
    expect(store.userInfo.id).toBe(7)

    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    expect(postMessage).toHaveBeenCalledWith({ type: 'cw-user', uid: '7', role: 'viewer' }, '/')
  })

  test('账号切换时重建 iframe（RAG 按新 uid 重读它自己的存储）', async () => {
    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    await flushPromises()
    const keyBefore = wrapper.find('iframe').attributes('key') ?? wrapper.vm.frameKey

    const store = useUserStore()
    store.userInfo = { id: 9, role: 'viewer' }
    await flushPromises()

    expect(wrapper.vm.frameKey).toBeGreaterThan(Number(keyBefore) || 0)
  })

  test('未拿到账号时不发消息（宁可保持独立访问行为，也不发空身份）', async () => {
    const store = useUserStore()
    store.userInfo = {}
    // userInfo 接口返回空：ensureUserInfo 之后仍没有 id
    const api = await import('@/api/module/user')
    ;(api.userInfo as any).mockResolvedValueOnce({ code: 200, data: null })

    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    const postMessage = await stubFrameWindow(wrapper)
    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    expect(postMessage).not.toHaveBeenCalled()
  })
})
