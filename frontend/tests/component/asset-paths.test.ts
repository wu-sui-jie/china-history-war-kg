/** 登录页与主框架的资源路径（第 12 轮审查 P1-4）。
 *
 * public/ 下的资源原样拷贝、不改名，也不会被构建器补上 vite base（本仓库是 /static/）。
 * 修复前这里有两处错误写法，构建与挂载都不会报错，只有上线后肉眼可见：
 *   - `src="/login.jpg"`：不带前缀，生产被 nginx 转给旧后端 Flask → 登录主图 404；
 *   - CSS 里的 `url(background.jpg)`：相对 dist/assets/*.css 解析成
 *     /static/assets/background.jpg，且 vite 找不到时会**把整条 background 声明从产物里删掉**。
 *
 * 这里断言的是"渲染出来的地址确实带 /static/ 前缀"。vitest.config.ts 里把 `base` 也设成
 * /static/，与 vite.config.ts 保持一致——否则测试环境拿到 BASE_URL='/'，
 * 断言会与生产路径脱节，等于没测。
 */

import { describe, expect, test, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Layui from '@layui/layui-vue'

import Login from '@/views/login/index.vue'
import BasicLayout from '@/layouts/BasicLayout.vue'
import { makeTestRouter } from './helpers'

vi.mock('@/api/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

// 主框架挂载时会拉菜单与权限（user store）；不 mock 的话请求落到空的 Http 桩上，
// 会以"未处理的 rejection"形式报出来，把资源路径断言淹掉。
vi.mock('@/api/module/user', () => ({
  menu: vi.fn(async () => ({ code: 200, data: [] })),
  permission: vi.fn(async () => ({ code: 200, data: [] })),
  userInfo: vi.fn(async () => ({ code: 200, data: {} })),
  login: vi.fn(),
  signIn: vi.fn(),
}))

vi.mock('@layui/layui-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@layui/layui-vue')>()
  return {
    ...actual,
    layer: { msg: vi.fn(), confirm: vi.fn(), close: vi.fn() },
  }
})

describe('登录页资源路径', () => {
  test('主图与背景图都带 vite base 前缀', () => {
    setActivePinia(createPinia())
    const wrapper = mount(Login, { global: { plugins: [Layui, makeTestRouter()] } })

    const img = wrapper.find('img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('/static/login.jpg')

    const style = wrapper.find('.login-wrap').attributes('style') || ''
    expect(style).toContain('/static/background.jpg')

    wrapper.unmount()
  })
})

describe('主框架资源路径', () => {
  test('退出按钮图标带 vite base 前缀', () => {
    setActivePinia(createPinia())
    const wrapper = mount(BasicLayout, {
      global: {
        plugins: [Layui, makeTestRouter()],
        stubs: {
          'global-content': true,
          'global-menu': true,
          'global-tab': true,
          'global-breadcrumb': true,
          'global-setup': true,
        },
      },
    })

    const icons = wrapper.findAll('img').map((node) => node.attributes('src'))
    expect(icons).toContain('/static/icon/logout.svg')
    // 不允许出现"不带前缀的绝对路径"——那正是这次的缺陷形态
    expect(icons.filter((src) => src && src.startsWith('/') && !src.startsWith('/static/')))
      .toEqual([])

    wrapper.unmount()
  })
})
