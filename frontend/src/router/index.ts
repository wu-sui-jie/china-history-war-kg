import { createRouter, createWebHashHistory, NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import routes from './module/base-routes'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false })

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

/**
 * Router 前置拦截
 * 
 * 1.验证 token 存在, 并且有效, 否则 -> login.vue
 * 2.验证 permission 存在, 否则 -> 403.vue
 * 3.验证 router 是否存在, 否则 -> 404.vue
 * 
 * @param to 目标
 * @param from 来自
 */
router.beforeEach((to: RouteLocationNormalized, from: RouteLocationNormalized, next: NavigationGuardNext) => {
  NProgress.start();

  // 无匹配路由 -> 404（实际鉴权在 api/http.ts 的请求拦截器里做）
  if (to.matched.length == 0) {
    next({path: '/error/404'})
  } else {
    next();
  }
})

router.afterEach(() => {
  NProgress.done();
})

export default router