import Http from '../http'

// 节点管理接口：知识库管理页（四个 CRUD 页）、图谱页与质检页共用。
// 这些接口的历史路径不带 /api 前缀（生产由 nginx 的 `location /` 兜底，开发态在
// vite.config.ts 里显式转发），因此 URL 写在这里、不要在组件里各写一份。

export interface NodePageQuery {
  pageNum: number
  pageSize: number
  name?: string
  node_type?: string
}

export interface NodePayload {
  type: string
  id?: number | string | null
  [field: string]: any
}

export const findNodePage = (params: NodePageQuery) => Http.post('/api/find_node_page', params)

export const createNode = (payload: NodePayload) => Http.post('/create_node', payload)

export const updateNode = (payload: NodePayload) => Http.post('/update_node', payload)

export const deleteNode = (payload: { type: string; id: number | string }) =>
  Http.post('/delete_node', payload)

export const updateNodeProperties = (payload: NodePayload) =>
  Http.post('/api/node/update_properties', payload)

/** 节点关系（图谱页展开节点时用） */
export const getNodeRelations = (id: number | string) => Http.get('/api/node/relations', { id })

/** 文本实体与事件识别（长文本走大模型，耗时较长） */
export const extractEntitiesEvents = (payload: { text: string }) =>
  Http.post('/api/extract/entities-events', payload)
