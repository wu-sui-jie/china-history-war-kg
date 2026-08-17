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
          { path: 'event', component: () => import('../../views/knowledge/graph/event/EventGraph.vue'), meta: { title: '历史战争', requireAuth: true } },
          { path: 'organization', component: () => import('../../views/knowledge/graph/organization/OrganizationGraph.vue'), meta: { title: '参战势力', requireAuth: true } },
          { path: 'person', component: () => import('../../views/knowledge/graph/person/PersonGraph.vue'), meta: { title: '历史人物', requireAuth: true } },
          { path: 'place', component: () => import('../../views/knowledge/graph/place/PlaceGraph.vue'), meta: { title: '战争地点', requireAuth: true } },
        ],
      },
      {
        path: '/knowledge-list',
        component: NodeLayout,
        meta: { title: '数据维护', requireAuth: true },
        children: [
          { path: '', redirect: '/knowledge-list/event' },
          { path: 'event', component: () => import('../../views/knowledge-list/event/EventNode.vue'), meta: { title: '战争事件', requireAuth: true } },
          { path: 'organization', component: () => import('../../views/knowledge-list/organization/OrgNode.vue'), meta: { title: '参战组织', requireAuth: true } },
          { path: 'person', component: () => import('../../views/knowledge-list/person/PersonNode.vue'), meta: { title: '历史人物', requireAuth: true } },
          { path: 'place', component: () => import('../../views/knowledge-list/place/PlaceNode.vue'), meta: { title: '战争地点', requireAuth: true } },
        ],
      },
      { path: '/knowledge/entity/:type/:id', component: () => import('../../views/knowledge/EntityDetail.vue'), meta: { title: '实体详情', requireAuth: true } },
      { path: '/knowledge/entity-detail', component: () => import('../../views/knowledge/EntityDetail.vue'), meta: { title: '实体详情', requireAuth: true } },
      { path: '/knowledge/map', component: () => import('../../views/knowledge/HistoricalMapView.vue'), meta: { title: '历史地图视图', requireAuth: true } },
      { path: '/knowledge/timeline', component: () => import('../../views/knowledge/TimelineView.vue'), meta: { title: '战争时间轴', requireAuth: true } },
      { path: '/knowledge/relation-analysis', component: () => import('../../views/knowledge/RelationAnalysis.vue'), meta: { title: '关系分析', requireAuth: true } },
      { path: '/knowledge/search', component: () => import('../../views/knowledge/GlobalSearch.vue'), meta: { title: '全局搜索', requireAuth: true } },
      { path: '/knowledge/inference', component: () => import('../../views/inference/index.vue'), meta: { title: '历史问答助手', requireAuth: true } },
      { path: '/knowledge/text-extract', component: () => import('../../views/knowledge/TextEntityExtract.vue'), meta: { title: '文本实体识别', requireAuth: true } },
    ],
  },
  {
    path: '/workspace',
    component: BasicLayout,
    meta: { title: '数据运营' },
    children: [
      { path: '/workspace/dashboard', component: () => import('../../views/workspace/Dashboard.vue'), meta: { title: '首页仪表盘', requireAuth: true } },
      { path: '/workspace/dataset', component: () => import('../../views/workspace/DatasetCenter.vue'), meta: { title: '数据集中心', requireAuth: true } },
      { path: '/workspace/dataset-versions', component: () => import('../../views/workspace/DatasetVersions.vue'), meta: { title: '数据版本管理', requireAuth: true } },
      { path: '/workspace/quality', component: () => import('../../views/workspace/QualityInspection.vue'), meta: { title: '图谱质检', requireAuth: true } },
      { path: '/workspace/repair', component: () => import('../../views/workspace/RepairWorkbench.vue'), meta: { title: '数据修复工作台', requireAuth: true } },
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
