/** 旧前端组件测试的公共夹具。
 *
 * `vi.mock` 的工厂是提升的，因此这里只放"不依赖 mock"的东西：真实的内存路由与
 * 断言辅助。layui 的 `layer` 各测试文件自己 mock（见各文件顶部的工厂），
 * 再用 `layerSpies()` 取到间谍对象断言提示文案。
 */

import type { Mock } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { layer } from '@layui/layui-vue'

/** 真实内存路由：页面里的 useRouter()/useRoute() 需要它，比打桩更接近运行态。 */
export function makeTestRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/knowledge/timeline', component: { template: '<div />' } },
      { path: '/knowledge/entity-detail', component: { template: '<div />' } },
      { path: '/workspace/dashboard', component: { template: '<div />' } },
      { path: '/workspace/dataset', component: { template: '<div />' } },
      { path: '/workspace/quality', component: { template: '<div />' } },
    ],
  })
}

/** 取到被 mock 的 layer.msg / layer.confirm / layer.close 间谍。 */
export function layerSpies(): { msg: Mock; confirm: Mock; close: Mock } {
  return layer as unknown as { msg: Mock; confirm: Mock; close: Mock }
}
