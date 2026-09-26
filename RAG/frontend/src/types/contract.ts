/** RAG 前后端数据契约（镜像 RAG/docs/data-contract.md 与后端 contracts/）。 */

export interface HistoryTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface Filters {
  dynasty: string[]
  event_type: string[]
}

/** 一次实体纠正指令（与后端 contracts/request.py 的 CorrectedEntity 一一对应）。
 *
 * 源与目标必须是**两个**字段：同名不同朝代的实体共享
 * standard_name，一个含混的 entity_id 无法表达"把 A 换成 B"。
 *
 * | 动作 | 源实体 | 目标实体 | 其他 |
 * | --- | --- | --- | --- |
 * | add | — | `replacement_entity_id` 可选 | `name` + `entity_type` 必填 |
 * | replace | `source_entity_id` 或 `original` | `replacement_entity_id` 或 `replacement` | — |
 * | remove | `source_entity_id` 或 `original` | — | — |
 */
export interface CorrectedEntity {
  action: 'add' | 'replace' | 'remove'
  source_entity_id?: string
  replacement_entity_id?: string
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
  /** 事件叙事字段（由 F05 从 event_cards 快照装配，仅事件卡有值） */
  aggressor?: string | null
  defender?: string | null
  action?: string | null
  impact?: string | null
  place?: string | null
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
  | 'thinking' // 仅在 EXPOSE_THINKING=true 时后端才外发原始推理，默认不发
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

/** done 事件载荷（后端 FinishReason 枚举的前端镜像）。 */
export interface DoneData {
  finish_reason?: string
  model_used?: string
  cache_hit?: boolean
  /** 首思考时延与推理增量条数：只报度量，不含推理内容 */
  first_thinking_ms?: number | null
  thinking_frames?: number
  truncated?: boolean
}

/** 一轮问答的终态（与后端 FinishReason 对齐，另加前端侧派生态）。 */
export type TurnStatus =
  | 'connecting' // 已发出请求，尚未收到事件
  | 'streaming' // 正在接收增量
  | 'completed' // normal/未知但无错误
  | 'refused' // 依据不足
  | 'degraded' // 降级生成
  | 'cancelled' // 用户取消
  | 'failed' // 服务异常 / 网络错误 / 超时
  | 'interrupted' // 流意外中断（EOF 无 done / 刷新后恢复）

/** 可作为后续多轮上下文的历史终态。 */
export const HISTORY_SAFE_STATUSES: TurnStatus[] = ['completed', 'refused', 'degraded']

export interface ApiErrorBody {
  status?: string
  error_code?: string
  message?: string
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
