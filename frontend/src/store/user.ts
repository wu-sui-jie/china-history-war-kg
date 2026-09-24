import { defineStore } from 'pinia'
import { menu, permission, userInfo } from '../api/module/user'

export interface MenuItem {
  id?: string
  icon?: string
  title?: string
  children?: MenuItem[]
}

const DASHBOARD_MENU: MenuItem = {
  id: '/workspace/dashboard',
  icon: 'layui-icon-home',
  title: '首页仪表盘'
}

const TIMELINE_MENU: MenuItem = {
  id: '/knowledge/timeline',
  icon: 'layui-icon-date',
  title: '历史时间轴'
}

const MAP_MENU: MenuItem = {
  id: '/knowledge/map',
  icon: 'layui-icon-location',
  title: '历史地图视图'
}

// 仅管理员下发的菜单（后端 get_menu 按角色裁剪）。
// 注意它必须**取自后端下发的内容**（menuMap.get），不能像上面三个内置项那样写成常量：
// 常量会无条件进导航栏，等于把后端对 editor/viewer 的裁剪撤销掉。

// 不在侧边栏展示的菜单项：路由、页面与后端接口都保留，只是不进导航栏。
// 「历史问答助手」的入口已由「RAG 智能问答」承接，暂从导航栏下线；
// 需要恢复入口时，把对应 id 从这里删掉即可。
const HIDDEN_MENU_IDS = new Set<string>(['/knowledge/inference'])

function hideMenus(items: MenuItem[]): MenuItem[] {
  return items
    .filter((item) => item?.id && !HIDDEN_MENU_IDS.has(item.id))
    .map((item) => (item.children ? { ...item, children: hideMenus(item.children) } : item))
}

// 注意：下面两份 id 清单是 backend/app.py `get_menu()` 的白名单，
// 后端加了菜单项但没同步加进来，会因为 `.filter(Boolean)` 被静默丢弃（页面上就是不出现）。
function mergeWorkspaceMenus(source: MenuItem[] = []) {
  const menus: MenuItem[] = Array.isArray(source) ? [...source] : []
  const menuMap = new Map(menus.map((item) => [item?.id, item]))

  const knowledgeGroup = menuMap.get('/knowledge')
  if (knowledgeGroup?.children) {
    const childMap = new Map((knowledgeGroup.children || []).map((item) => [item?.id, item]))
    knowledgeGroup.children = [
      childMap.get('/knowledge/graph'),
      childMap.get('/knowledge/graph/event'),
      childMap.get('/knowledge/graph/organization'),
      childMap.get('/knowledge/graph/person'),
      childMap.get('/knowledge/graph/place'),
      childMap.get('/knowledge/inference'),
      childMap.get('/knowledge/rag'),
      childMap.get('/knowledge/text-extract'),
      childMap.get('/knowledge/relation-analysis'),
      childMap.get('/knowledge/search'),
      childMap.get('/knowledge/entity-detail'),
    ].filter(Boolean) as MenuItem[]
  }

  // 修正 2026-09-25：不再用 WORKSPACE_GROUP 兜底。菜单唯一事实源是后端
  // get_menu()，viewer 的数据运营组被角色裁剪后本就不该出现——原先的
  // `|| WORKSPACE_GROUP` 保底会把内置默认组（数据集中心+图谱质检）硬塞
  // 回给 viewer，等于部分撤销后端的裁剪。
  const workspaceGroup = menuMap.get('/workspace/manage')
  if (workspaceGroup?.children) {
    const childMap = new Map((workspaceGroup.children || []).map((item) => [item?.id, item]))
    workspaceGroup.children = [
      childMap.get('/workspace/dataset'),
      childMap.get('/knowledge-list'),
      childMap.get('/workspace/quality'),
      childMap.get('/workspace/repair'),
      childMap.get('/workspace/dataset-versions'),
    ].filter(Boolean) as MenuItem[]
  }

  const orderedMenus = [
    DASHBOARD_MENU,
    TIMELINE_MENU,
    MAP_MENU,
    knowledgeGroup,
    workspaceGroup,
    // 只有 admin 的后端响应里才有这一项，取自 menuMap 而不是内置常量
    menuMap.get('/admin/users'),
  ].filter(Boolean) as MenuItem[]

  const usedIds = new Set(orderedMenus.map((item) => item?.id))
  const remainingMenus = menus.filter((item) => item?.id && !usedIds.has(item.id))

  return hideMenus([...orderedMenus, ...remainingMenus])
}

export const useUserStore = defineStore({
  id: 'user',
  state: () => {
    return {
      token: '',
      userInfo: {} as Record<string, any>,
      permissions: [] as string[],
      menus: [] as MenuItem[],
    }
  },
  actions: {
    async loadMenus() {
      const { data, code } = await menu()
      if (code == 200) {
        this.menus = mergeWorkspaceMenus(data)
      }
    },
    async loadPermissions() {
      const { data, code } = await permission()
      if (code == 200) {
        this.permissions = Array.isArray(data) ? data : []
      }
    },
    /** 拉取当前账号信息（account/name/role）：界面显示角色徽标用。
     *  未登录或接口失败时静默保持空对象——角色相关 UI 按"只读"兜底。 */
    async loadUserInfo() {
      try {
        const { data, code } = await userInfo()
        if (code == 200 && data) {
          this.userInfo = data
        }
      } catch {
        // token 失效等场景：拦截器已处理跳登录，这里不必再提示
      }
    },
    /** 清空登录态（token 与后端下发的菜单/权限一并清掉，避免换账号后残留） */
    clearSession() {
      this.token = ''
      this.userInfo = {}
      this.permissions = []
      this.menus = []
    },
    /**
     * 确保 userInfo 已加载，并返回它。拿账号 id 做事的地方（问答记录按账号隔离、
     * 给 RAG 传身份）必须走这个而不是直接读 userInfo：
     * BasicLayout 的 onMounted 里 loadUserInfo() 是不 await 的，而子组件的 mounted
     * 先于父组件执行——直接读会拿到空对象，隔离就退化成"共享 key"。
     */
    async ensureUserInfo() {
      if (!this.userInfo?.id) {
        await this.loadUserInfo()
      }
      return this.userInfo
    }
  },
  persist: {
    storage: localStorage,
    paths: ['token', 'userInfo', 'permissions', 'menus'],
    // 本地缓存里可能存着改动前下发的菜单，恢复时先过一遍隐藏清单，
    // 否则等 loadMenus() 返回前会短暂闪出已下线的入口。
    afterRestore: (ctx) => {
      const store = ctx.store as unknown as { menus?: MenuItem[] }
      if (Array.isArray(store.menus)) {
        store.menus = hideMenus(store.menus)
      }
    },
  }
})
