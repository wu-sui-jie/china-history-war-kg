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

// 角色分级：admin ⊃ editor ⊃ viewer。路由 meta.requiresRole 写"最低需要的角色"，
// 守卫按强度比对——admin 天然满足 editor 级页面（数据运营组），反过来不成立。
// 后端对应 backend/app.py 的 ROLE_RANKS 与 require_write_role / require_admin，
// 两处口径要一起改。
const ROLE_RANK: Record<string, number> = { viewer: 0, editor: 1, admin: 2 }

/** 当前角色是否满足页面的最低角色要求；角色未知一律不满足（fail-closed）。 */
function roleSatisfies(userRole: unknown, required: unknown): boolean {
  if (!required) return true
  const need = ROLE_RANK[String(required)]
  if (need === undefined) return false
  const have = ROLE_RANK[String(userRole)]
  return have !== undefined && have >= need
}

/**
 * Router 前置拦截
 *
 * 1.无匹配路由 -> 404
 * 2.未登录访问业务页 -> 跳登录页（带 redirect 回跳）
 * 3.角色不足访问 requiresRole 页面（数据运营组 / 用户管理）-> 提示并回仪表盘
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

    // 角色拦截。userInfo 会持久化（store/user.ts 的 persist.paths 含 userInfo），
    // 但**拿不到角色**这件事在刷新后仍会发生：本地缓存被清过、或换号登录的瞬间
    // userInfo 是空的——所以这里仍要按需拉一次，不能假设它一定在。
    if (to.meta.requiresRole && !userStore.userInfo?.role) {
      await userStore.loadUserInfo()
    }
    if (!roleSatisfies(userStore.userInfo?.role, to.meta.requiresRole)) {
      // 提示按"角色不足"统一措辞，不写死页面归属：原先非 admin 一律说"数据运营页面"，
      // 而文本实体识别（requiresRole='editor'）并不属于数据运营组。
      layer.msg(to.meta.requiresRole === 'admin' ? '该页面仅管理员可访问' : '当前账号权限不足，无法访问该页面',
                { icon: 2 })
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
