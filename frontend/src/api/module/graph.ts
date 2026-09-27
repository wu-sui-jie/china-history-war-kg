import Http from '../http'

// 知识图谱相关接口：全局搜索、关系分析、聚焦子图与自由图谱查询。

export const globalSearch = (keyword: string) => Http.get('/api/search/global', { keyword })

export interface RelationQuery {
  name: string
  depth?: number | string
  direction?: string
  limit?: number | string
}

export const relationAnalysis = (query: RelationQuery) =>
  Http.get('/api/relation-analysis/query', query)

/** 以某个实体为中心的局部图谱（focus 由 utils/graph.ts 的 getGraphFocus() 产出） */
export const getGraphNodeContext = (focus: Record<string, unknown> | null) =>
  Http.get('/api/graph/node_context', focus ?? undefined)

/**
 * 总览 / 按条件聚焦检索。
 * 空 payload = 战争关系图首页：后端取"四类均衡"的总览（见 model_search.get_overview_graph）。
 * 传 name / node_type / rel_type 则按条件聚焦。
 */
export const searchNameKg = (payload: { name?: string; node_type?: string; rel_type?: string }) =>
  Http.post('/search_name_kg', payload)

/** 四个子页的图谱接口（战争事件/参战组织/历史人物/战争地点的关系图） */
export type EventGraphEndpoint =
  | '/api/graph/event_event'
  | '/api/graph/event_organization'
  | '/api/graph/event_person'
  | '/api/graph/event_place'

export const getEventGraph = (
  endpoint: EventGraphEndpoint,
  params: { name?: string; rel_type?: string },
) => Http.get(endpoint, params)
