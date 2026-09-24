import { createRouter, createWebHashHistory, NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import routes from './module/base-routes'
import { useUserStore } from '../store/user'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false })

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 免登录页面前缀：登录页与错误页
const PUBLIC_PATH_PREFIXES = ['/login', '/error']

/**
 * Router 前置拦截
 *
 * 1.无匹配路由 -> 404
 * 2.未登录访问业务页 -> 跳登录页（带 redirect 回跳）
 *
 * token 是否**有效**由后端判定：过期/伪造的 token 会被接口拒为 401，
 * 由 api/http.ts 的响应拦截器清凭据并回到登录页。
 */
router.beforeEach((to: RouteLocationNormalized, from: RouteLocationNormalized, next: NavigationGuardNext) => {
  NProgress.start();

  // 无匹配路由 -> 404
  if (to.matched.length == 0) {
    next({path: '/error/404'})
    return
  }

  const isPublic = PUBLIC_PATH_PREFIXES.some(prefix => to.path.startsWith(prefix))
  if (!isPublic) {
    const userStore = useUserStore()
    if (!userStore.token) {
      next({ path: '/login', query: { redirect: to.fullPath } })
      return
    }
  }

  next();
})

router.afterEach(() => {
  NProgress.done();
})

export default router
