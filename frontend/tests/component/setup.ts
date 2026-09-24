/** jsdom 缺的浏览器 API 补丁（仅测试环境）。
 *
 * jsdom 不实现 ResizeObserver / scrollIntoView / 元素上的 scrollTo / matchMedia，而
 * layui-vue 的表格、弹层与图表组件在挂载时都会碰它们——缺了会以无关错误失败，掩盖真实断言。
 */

import { vi } from 'vitest'

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  (globalThis as any).ResizeObserver = FakeResizeObserver
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}

// 页面里用 element.scrollTo({top}) 复位滚动位置；jsdom 只给 window 实现了 scrollTo，
// 元素上的那个是 undefined，缺了会以 TypeError 失败。
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {}
}

if (!window.matchMedia) {
  (window as any).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })
}
