/** jsdom 缺的浏览器 API 补丁（仅测试环境）。
 *
 * jsdom 不实现 ResizeObserver / scrollIntoView / matchMedia，而 layui-vue 的表格、
 * 弹层与图表组件在挂载时都会碰它们——缺了会以无关错误失败，掩盖真实断言。
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
