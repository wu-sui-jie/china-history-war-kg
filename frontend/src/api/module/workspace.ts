import Http from '../http'

// 工作台相关接口：仪表盘、数据集、质检、修复、实体详情。
export const getDashboardOverview = () => Http.get('/api/dashboard/overview')

export const getDatasetOverview = () => Http.get('/api/dataset/overview')

export const getQualityReport = () => Http.get('/api/quality/report')

export const getQualityWorkbench = () => Http.get('/api/quality/workbench')

export const getEntityDetail = (params: { id: string | number; type: string }) => Http.get('/api/entity/detail', params)

export const getTimelineOverview = (params?: { keyword?: string; dynasty?: string; participant?: string }) => Http.get('/api/timeline/overview', params)

export const getTimelineEvents = (params?: { keyword?: string; dynasty?: string; participant?: string; event_type?: string; only_issues?: string }) =>
  Http.get('/api/timeline/events', params)

export const getEventMap = (params?: { keyword?: string; dynasty?: string }) => Http.get('/api/map/events', params)

export const getRepairIssues = (params?: { type?: string }) => Http.get('/api/repair/issues', params)

export const getDatasetVersions = () => Http.get('/api/dataset/versions')
