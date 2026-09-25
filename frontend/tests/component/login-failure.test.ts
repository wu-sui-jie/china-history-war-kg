/** 登录页的失败可见性（第 13 轮整改）。
 *
 * 背景：后端第 13 轮加了登录限流，超限返回 **HTTP 429**；而登录页原先只有
 * `.then(...).finally(...)`、没有 `.catch`——axios 对非 2xx 走 reject，
 * 于是用户点完登录**界面上什么都没有**，按钮只是重新亮起来。
 * 现象看起来像"点了没反应"，真实原因却是"尝试过于频繁"，两者差得很远。
 *
 * 这里钉住：拒绝分支必须把服务端文案显示出来；并且口令长度下限要与后端一致。
 */

import { beforeEach, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import { layerSpies, makeTestRouter } from './helpers'
import Login from '@/views/login/index.vue'
import { login, signIn } from '@/api/module/user'

vi.mock('@/api/module/user', () => ({
  login: vi.fn(),
  signIn: vi.fn(),
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return { ...actual, layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() } }
})

const loginMock = login as unknown as ReturnType<typeof vi.fn>
const signInMock = signIn as unknown as ReturnType<typeof vi.fn>

function mountPage() {
  setActivePinia(createPinia())
  return mount(Login, { global: { plugins: [Layui, makeTestRouter()] } })
}

/** 429 的拒绝形态：axios 把后端响应放在 error.response.data 里。 */
function throttled() {
  return Promise.reject({
    message: 'Request failed with status code 429',
    response: { status: 429, data: { code: 429, msg: '尝试过于频繁，请稍后再试' } },
  })
}

describe('登录页失败可见性', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 默认两个接口都"永不返回"：用例只关心"请求发出去了没有"与"拒绝时提示了什么"。
    // 一旦返回成功，页面会去调 store 的 loadUserInfo/loadMenus/loadPermissions ——
    // 那是另一个模块的事，在本文件里跑起来只会让断言跑偏。
    const pending = () => new Promise(() => {})
    loginMock.mockImplementation(pending)
    signInMock.mockImplementation(pending)
  })

  test('被限流（429）时把服务端文案显示出来，而不是"点了没反应"', async () => {
    loginMock.mockImplementation(throttled)
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'somebody'
    wrapper.vm.loginForm.password = 'test-password-01'
    await wrapper.vm.loginSubmit()
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('尝试过于频繁，请稍后再试', { icon: 2 })
  })

  test('网络错误（没有任何 response）时回落到兜底文案', async () => {
    loginMock.mockImplementation(() => Promise.reject(new Error('Network Error')))
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'somebody'
    wrapper.vm.loginForm.password = 'test-password-01'
    await wrapper.vm.loginSubmit()
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith('登录失败，请稍后重试', { icon: 2 })
  })

  test('注册被拒（403 关闭自助注册）时同样有提示', async () => {
    signInMock.mockImplementation(() => Promise.reject({
      response: { status: 403, data: { code: 403, msg: '系统已关闭自助注册，请联系管理员开通账号' } },
    }))
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'newbie'
    wrapper.vm.loginForm.name = '新人'
    wrapper.vm.loginForm.password = 'test-password-01'
    await wrapper.vm.signinSubmit()
    await flushPromises()

    expect(layerSpies().msg).toHaveBeenCalledWith(
      '系统已关闭自助注册，请联系管理员开通账号', { icon: 2 })
  })

  test('注册时口令长度下限与后端一致（10 位），更短的拦在本地', async () => {
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'newbie'
    wrapper.vm.loginForm.name = '新人'
    // 9 位：后端 add_user 会以 400 拒绝，前端必须自己拦住，否则用户要等一次往返才知道
    wrapper.vm.loginForm.password = '123456789'
    await wrapper.vm.signinSubmit()

    expect(signInMock).not.toHaveBeenCalled()
    expect(layerSpies().msg).toHaveBeenCalledWith('密码长度需为 10-64 位', {icon: 2})
  })

  test('登录不做口令长度校验：短口令的老账号必须还能登录', async () => {
    // 口令策略提高之前建的账号可能只有 6~9 位，服务端 authentication 从不检查长度，
    // 这些口令完全合法——前端若照搬策略，等于把老用户锁在门外。
    //
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'legacy-user'
    wrapper.vm.loginForm.password = 'pw-123456'
    await wrapper.vm.loginSubmit()

    expect(loginMock).toHaveBeenCalledWith({ account: 'legacy-user', password: 'pw-123456' })
  })

  test('口令按原值发送，不被 trim（第 13 轮复核整改 §2.2）', async () => {
    // 原先这里 `password: loginForm.password.trim()`：那等于静默改了用户输入。
    // 历史口令或命令行建的口令若带首尾空格，用户照着输入反而登录不上，
    // 而提示只能是"用户名或密码错误"——一个用户永远猜不到的原因。
    // 首尾空格该不该允许由服务端口令策略明确回答，前端不做规范化。
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = '  somebody  '
    wrapper.vm.loginForm.password = '  spaced-password  '
    await wrapper.vm.loginSubmit()

    expect(loginMock).toHaveBeenCalledWith({
      // 账号仍然 trim（复制粘贴带进来的空格是明显的输入失误）
      account: 'somebody',
      // 口令原样发送
      password: '  spaced-password  ',
    })
  })

  test('注册路径同样不 trim 口令', async () => {
    const wrapper = mountPage()

    wrapper.vm.loginForm.account = 'newbie'
    wrapper.vm.loginForm.name = ' 新人 '
    wrapper.vm.loginForm.password = '  spaced-password  '
    await wrapper.vm.signinSubmit()

    expect(signInMock).toHaveBeenCalledWith({
      account: 'newbie',
      name: '新人',
      password: '  spaced-password  ',
    })
  })

})
