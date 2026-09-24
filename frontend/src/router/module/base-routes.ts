import BasicLayout from '../../layouts/BasicLayout.vue'
import Login from '../../views/login/index.vue'
import GraphLayout from '../../views/knowledge/graph/GraphLayout.vue'
import NodeLayout from '../../views/knowledge-list/NodeLayout.vue'

export default [
  { path: '/', redirect: '/workspace/dashboard' },
  { path: '/login', component: Login, meta: { title: '登录页面' } },
  {
    path: '/knowledge',
    component: BasicLayout,
    meta: { title: '历史战争知识图谱' },
    children: [
      {
        path: '/knowledge/graph',
        component: GraphLayout,
        meta: { title: '战争关系图', requireAuth: true },
        children: [
          { path: '', component: () => import('../../views/knowledge/graph/OverviewGraph.vue'), meta: { title: '战争关系图总览', requireAuth: true } },
          { path: 'event', component: () => import('../../views/knowledge/graph/EntityGraph.vue'), meta: { title: '历史战争', graphKind: 'event', requireAuth: true } },
          { path: 'organization', component: () => import('../../views/knowledge/graph/EntityGraph.vue'), meta: { title: '参战势力', graphKind: 'organization', requireAuth: true } },
          { path: 'person', component: () => import('../../views/knowledge/graph/EntityGraph.vue'), meta: { title: '历史人物', graphKind: 'person', requireAuth: true } },
          { path: 'place', component: () => import('../../views/knowledge/graph/EntityGraph.vue'), meta: { title: '战争地点', graphKind: 'place', requireAuth: true } },
        ],
      },
      {
        path: '/knowledge-list',
        component: NodeLayout,
        meta: { title: '数据维护', requireAuth: true, requiresRole: 'editor' },
        children: [
          { path: '', redirect: '/knowledge-list/event' },
          { path: 'event', component: () => import('../../views/knowledge-list/event/EventNode.vue'), meta: { title: '战争事件', requireAuth: true, requiresRole: 'editor' } },
          { path: 'organization', component: () => import('../../views/knowledge-list/organization/OrgNode.vue'), meta: { title: '参战组织', requireAuth: true, requiresRole: 'editor' } },
          { path: 'person', component: () => import('../../views/knowledge-list/person/PersonNode.vue'), meta: { title: '历史人物', requireAuth: true, requiresRole: 'editor' } },
          { path: 'place', component: () => import('../../views/knowledge-list/place/PlaceNode.vue'), meta: { title: '战争地点', requireAuth: true, requiresRole: 'editor' } },
        ],
      },
      { path: '/knowledge/entity-detail', component: () => import('../../views/knowledge/EntityDetail.vue'), meta: { title: '实体详情', requireAuth: true } },
      { path: '/knowledge/map', component: () => import('../../views/knowledge/HistoricalMapView.vue'), meta: { title: '历史地图视图', requireAuth: true } },
      { path: '/knowledge/timeline', component: () => import('../../views/knowledge/TimelineView.vue'), meta: { title: '战争时间轴', requireAuth: true } },
      { path: '/knowledge/relation-analysis', component: () => import('../../views/knowledge/RelationAnalysis.vue'), meta: { title: '关系分析', requireAuth: true } },
      { path: '/knowledge/search', component: () => import('../../views/knowledge/GlobalSearch.vue'), meta: { title: '全局搜索', requireAuth: true } },
      // 历史问答助手同样会调大模型（消耗同一份配额），与文本实体识别一个口径：
      // 路由 meta + 后端 require_write_role 一起限 editor。它的菜单入口已在
      // store/user.ts 的 HIDDEN_MENU_IDS 里下线（由 RAG 智能问答承接），因此不需要动菜单裁剪。
      { path: '/knowledge/inference', component: () => import('../../views/inference/index.vue'), meta: { title: '历史问答助手', requireAuth: true, requiresRole: 'editor' } },
      { path: '/knowledge/rag', component: () => import('../../views/knowledge/RagAssistant.vue'), meta: { title: 'RAG 智能问答', requireAuth: true } },
      // 文本实体识别会调大模型（消耗配额），只读账号不给入口：菜单裁剪、路由 meta、
      // 后端 require_write_role 三处同口径。要放开只改这三处。
      { path: '/knowledge/text-extract', component: () => import('../../views/knowledge/TextEntityExtract.vue'), meta: { title: '文本实体识别', requireAuth: true, requiresRole: 'editor' } },
    ],
  },
  {
    path: '/workspace',
    component: BasicLayout,
    meta: { title: '数据运营' },
    children: [
      { path: '/workspace/dashboard', component: () => import('../../views/workspace/Dashboard.vue'), meta: { title: '首页仪表盘', requireAuth: true } },
      { path: '/workspace/dataset', component: () => import('../../views/workspace/DatasetCenter.vue'), meta: { title: '数据集中心', requireAuth: true, requiresRole: 'editor' } },
      { path: '/workspace/dataset-versions', component: () => import('../../views/workspace/DatasetVersions.vue'), meta: { title: '数据版本管理', requireAuth: true, requiresRole: 'editor' } },
      { path: '/workspace/quality', component: () => import('../../views/workspace/QualityInspection.vue'), meta: { title: '图谱质检', requireAuth: true, requiresRole: 'editor' } },
      { path: '/workspace/repair', component: () => import('../../views/workspace/QualityInspection.vue'), meta: { title: '数据修复工作台', requireAuth: true, requiresRole: 'editor' } },
    ],
  },
  {
    path: '/admin',
    component: BasicLayout,
    meta: { title: '系统管理' },
    children: [
      {
        path: '/admin/users',
        component: () => import('../../views/admin/UserManagement.vue'),
        // 仅管理员：改角色是系统管理动作，editor 若能进就等于能把权限体系打散
        // （把自己升成 admin）。后端 /api/admin/* 另有 require_admin 兜底，
        // 这里只是不给入口。
        meta: { title: '用户管理', requireAuth: true, requiresRole: 'admin' },
      },
    ],
  },
  {
    path: '/error',
    component: BasicLayout,
    meta: { title: '错误页面' },
    children: [
      { path: '/error/401', component: () => import('../../views/error/401.vue'), meta: { title: '401' } },
      { path: '/error/403', component: () => import('../../views/error/403.vue'), meta: { title: '403' } },
      { path: '/error/404', component: () => import('../../views/error/404.vue'), meta: { title: '404' } },
      { path: '/error/500', component: () => import('../../views/error/500.vue'), meta: { title: '500' } },
    ],
  },
]
