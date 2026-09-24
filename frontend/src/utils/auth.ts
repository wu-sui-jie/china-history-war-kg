import { computed } from 'vue'
import { useUserStore } from '../store/user'

/** 当前账号是否拥有写权限（admin / editor）。
 *
 * viewer 或角色尚未加载时为 false——用于隐藏数据运营类入口
 * （仪表盘快捷卡、质检工作台按钮等）。路由级拦截在 router/index.ts，
 * 这里只管"看不到入口"。 */
export function useHasWriteRole() {
  const store = useUserStore()
  return computed(() => ['admin', 'editor'].includes(store.userInfo?.role))
}
