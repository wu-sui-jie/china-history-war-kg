/** 后端下发的前端路由契约。
 *
 * **为什么要有这份用例。** 后端的报表构建器（`backend/report_builders.py`）会把
 * "点这个按钮该跳哪里"直接拼成路径下发（`detail_route` / `graph_route` /
 * `timeline_route`），前端拿到就 `router.push`。这条链路上没有任何编译期检查：
 * 后端改了路径、前端路由表没跟上，症状就是**点下去落 404**，而且只有人肉点得出来。
 *
 * 实际发生过一次：`build_global_search` 下发 `/knowledge/entity/{type}/{id}`，
 * 而前端只有 `/knowledge/entity-detail?type=&id=`，全局搜索点"详情"必 404。
 * 组件用例也挡不住——它的路由表是假的，推什么都能过。
 *
 * 这里直接用**真实路由实例**（`@/router/index`，与运行态同一个）去 resolve 后端会下发的
 * 每一种形状，并断言没有落进 404 兜底分支：`router/index.ts` 的守卫就是按
 * `to.matched.length == 0 → /error/404` 判定的，所以 matched 为空即等价于"会 404"。
 */

import { describe, expect, test } from 'vitest'

import router from '@/router'

/** 后端 `report_builders.py` 里所有 `*_route` 的字面形状（占位值换成真值即可）。 */
const BACKEND_ROUTES = [
  '/knowledge/entity-detail?type=Event&id=1',
  '/knowledge/entity-detail?type=Person&id=1',
  '/knowledge/entity-detail?type=Place&id=1',
  '/knowledge/entity-detail?type=Organization&id=1',
  '/knowledge/entity-detail?id=1&type=Event',
  '/knowledge/graph?focus=1&name=%E7%A7%A6&type=Event',
  '/knowledge/timeline?keyword=%E7%A7%A6',
  '/knowledge/map?dynasty=%E6%88%98%E5%9B%BD',
  '/knowledge/search?keyword=%E7%A7%A6',
  '/workspace/quality',
  '/workspace/repair?type=Event&id=1',
]

describe('后端下发的前端路由必须能在真实路由表里解析', () => {
  test.each(BACKEND_ROUTES)('%s 有对应路由且不落 404', (url) => {
    const resolved = router.resolve(url)

    expect(resolved.matched.length, `${url} 没有匹配任何路由（会被守卫送去 /error/404）`).toBeGreaterThan(0)
    expect(resolved.path).not.toBe('/error/404')
  })

  test('曾经的 /knowledge/entity/:type/:id 形状确实不存在（改回去就会挂）', () => {
    // 这条是"错误形状"的钉子：它现在不解析，说明上面的修复没有被无声地撤销。
    // 若将来真要引入这个形状，应连同本用例一起改，而不是让它自己失效。
    expect(router.resolve('/knowledge/entity/Event/1').matched.length).toBe(0)
  })
})
