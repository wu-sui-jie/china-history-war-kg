/** RAG 嵌入页的跨应用身份契约（账号分桶 + 服务端验签凭证）。
 *
 * 契约的另一半在 RAG 前端：它只认 `{type:'cw-user', uid, role, token}` 且只认同源
 * （见 RAG 仓库 tests/unit/user-scope.test.ts）。这里钉住主应用发出的这一半——载荷形状、
 * targetOrigin、以及"在 iframe load 之后才发"（早于 load 发会丢消息）。
 *
 * token 由 RAG 服务端验签：uid/role 只用于分 localStorage 桶（防串记录），
 * 防冒充靠 RAG 服务端验签这份 token。少发它不会导致前端报错，只会让开启
 * RAG_REQUIRE_AUTH 的部署里所有问答 401——所以必须在契约层钉住。
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
    // token 是登录凭证：放进 URL 会进日志，因此只能走 postMessage
    const store = useUserStore()
    store.token = 'aaa.bbb.ccc'
    await flushPromises()
    postMessage.mockClear()
    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    expect(postMessage).toHaveBeenCalledWith(
      { type: 'cw-user', uid: '7', role: 'viewer', token: 'aaa.bbb.ccc' }, '/')
    expect(postMessage).toHaveBeenCalledTimes(1)
  })

  test('账号未知时先拉取再发（不能把空 uid 发出去）', async () => {
    const store = useUserStore()
    store.userInfo = {}
    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    await flushPromises()   // 等 ensureUserInfo 拉到账号（首次解析出账号不再重建 iframe，见下一条用例）

    const postMessage = await stubFrameWindow(wrapper)
    expect(store.userInfo.id).toBe(7)

    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    // 没登录（无 token）时不带 token 字段值——RAG 侧按"没有身份"处理，
    // 这与服务端未开启校验的行为一致
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'cw-user', uid: '7', role: 'viewer', token: '' }, '/')
  })

  test('首次解析出账号不重建 iframe（否则进页面会白加载两遍）', async () => {
    const store = useUserStore()
    store.userInfo = {}
    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    const before = wrapper.vm.frameKey

    await flushPromises()   // ensureUserInfo 拉到 id=7：这是"首次解析出账号"，不是换号

    expect(store.userInfo.id).toBe(7)
    expect(wrapper.vm.frameKey).toBe(before)
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
    // userInfo 接口始终返回空：无论拉几次（onMounted 与 iframe load 各一次）都拿不到 id
    const api = await import('@/api/module/user')
    ;(api.userInfo as any).mockResolvedValue({ code: 200, data: null })

    wrapper = mount(RagAssistant, { global: { plugins: [Layui] } })
    const postMessage = await stubFrameWindow(wrapper)
    await wrapper.find('iframe').trigger('load')
    await flushPromises()

    expect(store.userInfo.id).toBeUndefined()
    expect(postMessage).not.toHaveBeenCalled()
  })
})
