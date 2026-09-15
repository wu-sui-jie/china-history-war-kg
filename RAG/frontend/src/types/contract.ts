/** RAG 前后端数据契约（镜像 RAG/docs/data-contract.md 与后端 contracts/）。 */

export interface HistoryTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface Filters {
  dynasty: string[]
  event_type: string[]
}

export interface CorrectedEntity {
  action: 'add' | 'replace' | 'remove'
  entity_type?: string
  name?: string
  original?: string
  replacement?: string
}

export interface QueryRequest {
  session_id: string
  question: string
  history: HistoryTurn[]
  filters: Filters
  corrected_entities?: CorrectedEntity[]
}

export interface EntityInfo {
  name: string
  type?: string
  standard_name?: string
  confidence?: string
  entity_id?: string
  dynasty?: string
}

export interface CandidateOption {
  name: string
  standard_name: string
  entity_type?: string
  confidence?: string
  dynasty?: string
  event_type?: string
  entity_id?: string
}

export interface EntityCandidate {
  mention: string
  entity_type?: string
  options: CandidateOption[]
}

export interface EntitiesData {
  entities: EntityInfo[]
  candidates: EntityCandidate[]
  question_type?: string
  rewritten_question?: string
  elapsed_ms?: number
}

export interface Citation {
  index: number
  evidence_id: string
  kind: string
  title: string
  snippet: string
}

export interface Conflict {
  subject: string
  field: string
  evidence_ids: string[]
  conflict_type?: string
  description?: string
}

export interface EvidenceRef {
  evidence_id: string
  kind?: string
  title?: string
  snippet?: string
}

export interface SubGraphNode {
  id: string
  type: string
  name: string
  dynasty?: string
}

export interface SubGraphEdge {
  source: string
  target: string
  relation: string
}

export interface SubGraph {
  nodes: SubGraphNode[]
  edges: SubGraphEdge[]
}

export interface TimelineItem {
  event_id: string
  name: string
  start_date?: string
  dynasty?: string
}

export interface TimelineGroup {
  label: string
  items: TimelineItem[]
}

export interface MapPoint {
  place_id: string
  name: string
  modern_name?: string
  longitude?: number | null
  latitude?: number | null
  events?: string[]
}

export interface EntityCard {
  entity_id: string
  type: string
  name: string
  event_type?: string
  dynasty?: string
  start_date?: string
  end_date?: string
  description?: string
  aliases?: string[]
  source?: string
  role?: string
  org?: string
  org_type?: string
  modern_name?: string
  longitude?: number | null
  latitude?: number | null
  province?: string
  city?: string
}

export interface PanelData {
  entity_cards: EntityCard[]
  subgraph: SubGraph
  timeline: { groups: TimelineGroup[] }
  map_points: MapPoint[]
}

export type SSEEventType =
  | 'session_start'
  | 'status'
  | 'entities'
  | 'graph_results'
  | 'text_results'
  | 'fusion'
  | 'thinking' // 保留枚举：后端当前不发射（见 data-contract），前端状态机不依赖
  | 'answer'
  | 'citations'
  | 'panel'
  | 'error'
  | 'done'

export interface SSEEnvelope {
  type: SSEEventType
  session_id: string
  stage?: string
  data?: any
}

export interface DictEntry {
  standard: string
  aliases?: string[]
}

export interface DictsResponse {
  status: string
  version?: string
  data_version?: string
  generated_at?: string
  dynasty?: DictEntry[]
  event_type?: string[]
  sources?: {
    file?: string
    counts?: { dynasty?: number; event_type?: number }
  }
  message?: string
}

export interface HealthResponse {
  status: string
  version?: string
  vector_available?: boolean
  llm_available?: boolean
  load_error?: string
  meta?: Record<string, unknown>
}

/** SSE status 阶段文案（与后端 StatusStage 枚举对应）。 */
export const STAGE_LABELS: Record<string, string> = {
  start: '会话开始',
  entity_linking: '识别实体',
  graph_search: '检索图谱',
  text_search: '检索文本',
  fusion: '融合重排',
  generating: '生成回答',
  cache_hit: '缓存命中',
}

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  事件: '#b4532a',
  人物: '#0f766e',
  组织: '#1d4ed8',
  地点: '#7c3aed',
}

/** F08 演示示例题（后端由已审核题库生成，见 scripts/gen_demo_examples.py）。 */
export interface DemoExample {
  id: string
  question: string
  category: string
  /** 展示用中文类别标签（实体介绍 / 关系型 / 背景型 / 时间线型…） */
  category_label: string
  /** 能力标注：graph / text / both（F08 验收：示例题要覆盖图谱与文本两类能力） */
  capability: string
  expect: { graph_min?: number; text_min?: number }
  baseline_score?: string
  measured?: {
    first_thinking_ms?: number | null
    first_answer_ms?: number | null
    total_ms?: number
    truncated?: boolean
    finish_reason?: string
  }
}

export interface DemoExamplesResponse {
  status: string
  version?: string
  generated_at?: string
  source_run?: string
  counts?: { bank_total?: number; candidates?: number; selected?: number }
  notes?: string
  examples?: DemoExample[]
}
