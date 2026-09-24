import { createRouter, createWebHashHistory, NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import routes from './module/base-routes'
import { useUserStore } from '../store/user'
import { layer } from '@layui/layui-vue'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false })

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 免登录页面前缀：登录页与错误页
const PUBLIC_PATH_PREFIXES = ['/login', '/error']

// 拥有写权限的角色：admin / editor。viewer（普通用户）访问
// requiresRole 页面（数据运营组）会被守卫拦下，不管入口是快捷卡片、
// 页面按钮还是手输 URL。
const WRITE_ROLES = ['admin', 'editor']

/**
 * Router 前置拦截
 *
 * 1.无匹配路由 -> 404
 * 2.未登录访问业务页 -> 跳登录页（带 redirect 回跳）
 * 3.普通用户访问 requiresRole 页面（数据运营组）-> 提示并回仪表盘
 *
 * token 是否**有效**由后端判定：过期/伪造的 token 会被接口拒为 401，
 * 由 api/http.ts 的响应拦截器清凭据并回到登录页。
 */
router.beforeEach(async (to: RouteLocationNormalized, _from: RouteLocationNormalized, next: NavigationGuardNext) => {
  NProgress.start();

  // 无匹配路由 -> 404
  if (to.matched.length == 0) {
    next({path: '/error/404'})
    return
  }

  const isPublic = PUBLIC_PATH_PREFIXES.some(prefix => to.path.startsWith(prefix))
  const userStore = useUserStore()
  if (!isPublic) {
    if (!userStore.token) {
      next({ path: '/login', query: { redirect: to.fullPath } })
      return
    }

    // 角色拦截。userInfo 不随 token 持久化，刷新后可能还没加载——先拉取再判。
    if (to.meta.requiresRole && !userStore.userInfo?.role) {
      await userStore.loadUserInfo()
    }
    if (to.meta.requiresRole && !WRITE_ROLES.includes(userStore.userInfo?.role)) {
      layer.msg('当前账号为普通用户，无法访问数据运营页面', { icon: 2 })
      next({ path: '/workspace/dashboard' })
      return
    }
  }

  next();
})

router.afterEach(() => {
  NProgress.done();
})

export default router
