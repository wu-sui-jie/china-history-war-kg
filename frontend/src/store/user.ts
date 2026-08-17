import { defineStore } from 'pinia'
import { menu, permission } from '../api/module/user'

const DASHBOARD_MENU = {
  id: '/workspace/dashboard',
  icon: 'layui-icon-home',
  title: '首页仪表盘'
}

const TIMELINE_MENU = {
  id: '/knowledge/timeline',
  icon: 'layui-icon-date',
  title: '历史时间轴'
}

const MAP_MENU = {
  id: '/knowledge/map',
  icon: 'layui-icon-location',
  title: '历史地图视图'
}

const WORKSPACE_GROUP = {
  id: '/workspace/manage',
  icon: 'layui-icon-console',
  title: '数据运营',
  children: [
    {
      id: '/workspace/dataset',
      icon: 'layui-icon-template-1',
      title: '数据集中心'
    },
    {
      id: '/workspace/quality',
      icon: 'layui-icon-vercode',
      title: '图谱质检'
    }
  ]
}

function mergeWorkspaceMenus(source: any[] = []) {
  const menus = Array.isArray(source) ? [...source] : []
  const menuMap = new Map(menus.map((item) => [item?.id, item]))

  const knowledgeGroup = menuMap.get('/knowledge')
  if (knowledgeGroup?.children) {
    const childMap = new Map((knowledgeGroup.children || []).map((item: any) => [item?.id, item]))
    knowledgeGroup.children = [
      childMap.get('/knowledge/graph'),
      childMap.get('/knowledge/graph/event'),
      childMap.get('/knowledge/graph/organization'),
      childMap.get('/knowledge/graph/person'),
      childMap.get('/knowledge/graph/place'),
      childMap.get('/knowledge/inference'),
      childMap.get('/knowledge/text-extract'),
      childMap.get('/knowledge/relation-analysis'),
      childMap.get('/knowledge/search'),
      childMap.get('/knowledge/entity-detail'),
    ].filter(Boolean)
  }

  const workspaceGroup = menuMap.get('/workspace/manage') || WORKSPACE_GROUP
  if (workspaceGroup?.children) {
    const childMap = new Map((workspaceGroup.children || []).map((item: any) => [item?.id, item]))
    workspaceGroup.children = [
      childMap.get('/workspace/dataset'),
      childMap.get('/knowledge-list'),
      childMap.get('/workspace/quality'),
      childMap.get('/workspace/repair'),
      childMap.get('/workspace/dataset-versions'),
    ].filter(Boolean)
  }

  const orderedMenus = [
    DASHBOARD_MENU,
    TIMELINE_MENU,
    MAP_MENU,
    knowledgeGroup,
    workspaceGroup,
  ].filter(Boolean)

  const usedIds = new Set(orderedMenus.map((item: any) => item?.id))
  const remainingMenus = menus.filter((item) => item?.id && !usedIds.has(item.id))

  return [...orderedMenus, ...remainingMenus]
}

export const useUserStore = defineStore({
  id: 'user',
  state: () => {
    return {
      token: '',
      userInfo: {},
      permissions: [],
      menus: [],
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
        this.permissions = data
      }
    }
  },
  persist: {
    storage: localStorage,
    paths: ['token', 'userInfo', 'permissions', 'menus'],
  }
})
