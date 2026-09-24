/** 会话与问答状态（localStorage 持久化）。
 *
 * 状态机（2026-09-15 审核 P0-4/P0-5/P0-6）：
 * - 每轮回答只有一个终态 `turnStatus`，`streaming/finished/cancelled` 由它派生，
 *   不再各自为政（历史实现里 done 会把 error 轮标成 finished，进而污染下一轮上下文）；
 * - 只有 completed/refused/degraded 进入多轮历史，且被纠正结果替代的轮次会被排除；
 * - 流自然结束但没收到 done → interrupted（半截回答不进历史，也不再永久转圈）；
 * - 刷新恢复时把所有未收敛的瞬态状态迁移为 interrupted（幽灵流式消息没有 AbortController）。
 *
 * 多会话（2026-09-20 借鉴项 P1）：
 * - 存储结构升到 v3：会话索引（id/标题/时间）+ 每个会话各自的消息体；
 * - 活动会话的消息体就是 `messages`，非活动会话的消息体在 `sessionBodies` 里，
 *   二者在切换/持久化时显式交换，避免"数组引用脱节"这类静默错位；
 * - 旧键（v2 单会话 / v1）读取时自动迁移成"单会话"；
 * - 会话数上限与每会话消息上限共同约束 localStorage 占用，超限时按最近更新裁剪。
 */

import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import { activeStorageKey, getActiveUid, setActiveRole, setActiveUid, type UserScopeMessage } from '@/utils/userScope'
import { fetchDicts, fetchHealth } from '@/api/http'
import { streamQuery } from '@/api/sse'
import {
  HISTORY_SAFE_STATUSES,
  STAGE_LABELS,
  type CandidateOption,
  type Citation,
  type Conflict,
  type CorrectedEntity,
  type DictEntry,
  type DoneData,
  type EntityCandidate,
  type EntityCard,
  type EntityInfo,
  type Filters,
  type HistoryTurn,
  type PanelData,
  type SSEEnvelope,
  type TurnStatus,
} from '@/types/contract'

/** 存储键带 schema 版本：字段结构变化时可以并存而不是把旧数据读坏。
 *  实际 key 由 utils/userScope 按账号组装（主应用 iframe 嵌入时是 `...:u{uid}`，
 *  独立访问 :8000 时就是下面这个基础 key，行为与改造前一致）。 */
const STORAGE_KEY_BASE = 'ragv5-session-v3'
const LEGACY_STORAGE_KEYS = ['ragv5-session-v2', 'ragv3-session-v1', 'ragv5-session-v1']
const STORAGE_SCHEMA_VERSION = 3
/** 无法安全解析/版本过新的原值隔离位置（不删除，便于排查） */
const STORAGE_QUARANTINE_KEY = 'ragv5-session-quarantine'
/** 持久化上限：每会话只保留最近若干条，避免把 localStorage 写爆导致恢复整体失效。 */
const MAX_PERSISTED_MESSAGES = 60
/** 会话数上限：多会话会放大 localStorage 占用，超限时按"最近更新"裁剪（活动会话必留） */
const MAX_SESSIONS = 20
/** 读写配额不足时的降级档位（会话数 / 每会话消息数） */
const DEGRADED_SESSIONS = 8
const DEGRADED_MESSAGES = 20
const DEFAULT_SESSION_TITLE = '新会话'
/** 自动命名取首条提问的前 N 字（沿用旧问答系统的 15 字口径） */
const TITLE_MAX_CHARS = 15
const DEFAULT_FILTERS: Filters = { dynasty: [], event_type: [] }

const EMPTY_PANEL: PanelData = {
  entity_cards: [],
  subgraph: { nodes: [], edges: [] },
  timeline: { groups: [] },
  map_points: [],
}

export type PanelTab = 'evidence' | 'cards' | 'graph' | 'timeline' | 'places'

export interface StatusRecord {
  stage: string
  label: string
  at: number
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  question: string
  answer: string
  citations: Citation[]
  conflicts: Conflict[]
  entities: EntityInfo[]
  candidates: EntityCandidate[]
  entityCards: EntityCard[]
  panel: PanelData | null
  status: StatusRecord[]
  /** 唯一状态源；streaming/finished/cancelled 由它派生 */
  turnStatus: TurnStatus
  streaming: boolean
  cancelled: boolean
  finished: boolean
  finishReason?: string
  /** 回答可能不完整（流中断 / 触及 max_tokens） */
  partial?: boolean
  truncated?: boolean
  error?: string
  /** 已收到 done（重复 done 幂等判定用） */
  doneSeen?: boolean
  /** done 之后仍收到业务帧的计数（协议错误观测，不阻断界面） */
  protocolErrors?: number
  correctedEntities: CorrectedEntity[]
  /** 本轮实际发出的筛选快照（重试时沿用，而不是读当前全局筛选） */
  requestFilters?: Filters
  /** 被哪一轮取代（纠正重查/重试）：被取代的轮次不进入历史 */
  supersededBy?: string
  /** 纠正/重试的来源轮次 id */
  sourceTurnId?: string
  createdAt: number
}

export interface UserMessage {
  id: string
  role: 'user'
  question: string
  filters: Filters
  createdAt: number
}

export type ChatMessage = UserMessage | AssistantMessage

/** 历史提问记录条目（由 messages 派生，供历史侧栏渲染）。
 *
 * 与 `history`（发给后端的多轮上下文）不同：这里保留失败/取消/被重查取代的轮次，
 * 因为"回看某一轮的知识面板"不应被可用性过滤挡住。 */
export interface HistoryEntry {
  id: string
  /** 第几轮（按 assistant 消息出现顺序编号，含重试/纠正轮） */
  index: number
  question: string
  createdAt: number
  turnStatus: TurnStatus
  live: boolean
  /** 被纠正/重试的新轮取代（仍可回看） */
  superseded: boolean
  /** 是否带有可展示的面板数据（配额降级持久化后可能为 false） */
  hasPanel: boolean
}

function newId(prefix: string): string {
  const tail =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${tail}`
}

function stageLabel(stage?: string): string {
  return STAGE_LABELS[stage || ''] || stage || ''
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function isLive(status: TurnStatus): boolean {
  return status === 'connecting' || status === 'streaming'
}

function isHistorySafe(msg: AssistantMessage): boolean {
  return HISTORY_SAFE_STATUSES.includes(msg.turnStatus)
}

/** 后端 finish_reason（或缺失时的上下文）→ 前端终态。 */
function turnStatusFromFinishReason(reason: string, hasError: boolean): TurnStatus {
  switch ((reason || '').toLowerCase()) {
    case 'normal':
      return 'completed'
    case 'refused':
      return 'refused'
    case 'degraded':
      return 'degraded'
    case 'cancelled':
      return 'cancelled'
    case 'failed':
      return 'failed'
    case 'interrupted':
      return 'interrupted'
    default:
      return hasError ? 'failed' : 'completed'
  }
}

/** 读取持久化时把旧结构补齐为当前结构（缺失字段给安全默认）。 */
function normalizeStoredMessage(raw: any): ChatMessage | null {
  if (!raw || (raw.role !== 'user' && raw.role !== 'assistant')) return null
  if (raw.role === 'user') {
    return {
      id: String(raw.id || newId('user')),
      role: 'user',
      question: String(raw.question || ''),
      filters: {
        dynasty: Array.isArray(raw.filters?.dynasty) ? raw.filters.dynasty : [],
        event_type: Array.isArray(raw.filters?.event_type) ? raw.filters.event_type : [],
      },
      createdAt: Number(raw.createdAt) || Date.now(),
    }
  }
  let turnStatus: TurnStatus = raw.turnStatus
  if (!turnStatus) {
    // 旧结构（streaming/cancelled/finished 三个布尔）迁移：未收敛的一律 interrupted
    if (raw.cancelled) turnStatus = 'cancelled'
    else if (raw.finished) {
      turnStatus = turnStatusFromFinishReason(String(raw.finishReason || ''), !!raw.error)
    } else turnStatus = 'interrupted'
  }
  // 刷新后没有活的流：瞬态状态一律收敛为 interrupted（P0-6 幽灵流）
  if (isLive(turnStatus)) turnStatus = 'interrupted'
  const msg: AssistantMessage = {
    id: String(raw.id || newId('assistant')),
    role: 'assistant',
    question: String(raw.question || ''),
    answer: String(raw.answer || ''),
    citations: Array.isArray(raw.citations) ? raw.citations : [],
    conflicts: Array.isArray(raw.conflicts) ? raw.conflicts : [],
    entities: Array.isArray(raw.entities) ? raw.entities : [],
    candidates: Array.isArray(raw.candidates) ? raw.candidates : [],
    entityCards: Array.isArray(raw.entityCards) ? raw.entityCards : [],
    panel: raw.panel || null,
    status: Array.isArray(raw.status) ? raw.status : [],
    turnStatus,
    streaming: false,
    cancelled: turnStatus === 'cancelled',
    finished: HISTORY_SAFE_STATUSES.includes(turnStatus),
    finishReason: raw.finishReason,
    partial: raw.partial || turnStatus === 'interrupted',
    truncated: raw.truncated,
    error: raw.error,
    correctedEntities: Array.isArray(raw.correctedEntities) ? raw.correctedEntities : [],
    requestFilters: raw.requestFilters,
    supersededBy: raw.supersededBy,
    sourceTurnId: raw.sourceTurnId,
    createdAt: Number(raw.createdAt) || Date.now(),
  }
  return asReactiveMessage(msg)
}

/** 把消息对象变成响应式代理。
 *
 * 必须做这一步（2026-09-15 复核发现的 P0 前端缺陷）：流式回答靠"改对象属性"驱动重渲染，
 * 而 `messages.value.push(obj)` 存进数组的是**原始对象**——之后直接改原始对象的属性
 * 不会触发任何依赖（DOM 停留在"正在生成"），同时 `obj !== active.value`（后者是代理）
 * 会让事件守卫把所有 SSE 事件全部丢弃。实测：只有通过代理写入才会更新界面。
 */
function asReactiveMessage<T extends ChatMessage>(msg: T): T {
  return reactive(msg) as T
}

/** 裁剪：只留最近 N 条，且不以助手消息开头（避免恢复出"没有问题的回答"）。 */
function cropMessages(
  messages: ChatMessage[],
  limit = MAX_PERSISTED_MESSAGES,
): ChatMessage[] {
  let kept = messages.slice(-limit)
  while (kept.length && kept[0].role === 'assistant') kept = kept.slice(1)
  return kept
}

/** 会话索引条目（消息体不在这里，见 store 内的 messages / sessionBodies）。 */
export interface SessionMeta {
  id: string
  title: string
  createdAt: number
  updatedAt: number
}

/** 会话列表渲染项。 */
export interface SessionEntry extends SessionMeta {
  active: boolean
  /** 该会话已有多少条消息（活动会话实时取自 messages） */
  messageCount: number
}

/** 会话自动命名：首条提问的前 15 字（旧问答系统口径），空提问保持默认标题。 */
function deriveTitle(question: string): string {
  const text = (question || '').trim()
  if (!text) return DEFAULT_SESSION_TITLE
  return text.length > TITLE_MAX_CHARS ? `${text.slice(0, TITLE_MAX_CHARS)}...` : text
}

/** 一段消息里的首个提问（迁移旧数据时用来给会话命名）。 */
function firstQuestion(messages: ChatMessage[]): string {
  for (const m of messages) {
    if (m.role === 'user' && m.question) return m.question
    if (m.role === 'assistant' && m.question) return m.question
  }
  return ''
}

function newSessionMeta(id?: string): SessionMeta {
  const at = Date.now()
  return { id: id || newId('session'), title: DEFAULT_SESSION_TITLE, createdAt: at, updatedAt: at }
}

/** 去掉体积最大的面板与过程记录（配额不足时的降级持久化）。 */
function stripHeavy(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((m) => {
    if (m.role !== 'assistant') return m
    return { ...m, panel: null, candidates: [], status: [] }
  })
}

export interface PersistNotice {
  kind: 'info' | 'warn'
  text: string
}

/** 从持久化结构得到的运行时视图：会话索引 + 各自的全部消息体。 */
interface PersistedSessions {
  sessions: SessionMeta[]
  messages: ChatMessage[]
  /** 非活动会话的消息体（活动会话的消息体就是 `messages`） */
  bodies: Map<string, ChatMessage[]>
  activeSessionId: string
}

/** 首启 / 隔离后的空会话。 */
function emptyPersisted(): PersistedSessions {
  const meta = newSessionMeta()
  return { sessions: [meta], messages: [], bodies: new Map(), activeSessionId: meta.id }
}

/** 数据损坏或版本过新时的统一出口：隔离原值 + 从空会话开始（不静默丢弃）。 */
function quarantined(
  key: string,
  raw: string,
  reason: string,
  text: string,
): PersistedSessions & { notice: PersistNotice | null } {
  quarantine(key, raw, reason)
  return { ...emptyPersisted(), notice: { kind: 'warn', text } as PersistNotice }
}

/** 超会话上限时按"最近更新"裁剪（活动会话必留），并回收对应消息体。 */
function trimSessions(
  sessions: SessionMeta[],
  bodies: Map<string, ChatMessage[]>,
  activeIdRaw: unknown,
): { sessions: SessionMeta[]; bodies: Map<string, ChatMessage[]>; activeSessionId: string } {
  const wanted = typeof activeIdRaw === 'string' ? activeIdRaw : ''
  let kept = sessions
  if (sessions.length > MAX_SESSIONS) {
    const byRecent = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)
    const top = byRecent.slice(0, MAX_SESSIONS)
    if (wanted && !top.some((s) => s.id === wanted)) {
      const active = byRecent.find((s) => s.id === wanted)
      if (active) top[top.length - 1] = active
    }
    const keepIds = new Set(top.map((s) => s.id))
    for (const id of [...bodies.keys()]) {
      if (!keepIds.has(id)) bodies.delete(id)
    }
    kept = sessions.filter((s) => keepIds.has(s.id))
  }
  const activeSessionId = kept.some((s) => s.id === wanted) ? wanted : kept[0].id
  return { sessions: kept, bodies, activeSessionId }
}

/** v3 多会话结构 → 会话索引 + 消息体。 */
function readMultiSession(parsed: Record<string, unknown>): PersistedSessions | null {
  const rawList = parsed.sessions as unknown[]
  const sessions: SessionMeta[] = []
  const bodies = new Map<string, ChatMessage[]>()
  for (const item of rawList) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const rec = item as Record<string, unknown>
    const id = typeof rec.id === 'string' && rec.id ? rec.id : newId('session')
    const messages = (Array.isArray(rec.messages) ? rec.messages : [])
      .map(normalizeStoredMessage)
      .filter((m): m is ChatMessage => !!m)
    const createdAt = Number(rec.createdAt) || messages[0]?.createdAt || Date.now()
    const title = typeof rec.title === 'string' && rec.title.trim()
      ? rec.title.trim().slice(0, 60)
      : deriveTitle(firstQuestion(messages))
    sessions.push({ id, title, createdAt, updatedAt: Number(rec.updatedAt) || createdAt })
    bodies.set(id, cropMessages(messages))
  }
  if (!sessions.length) return null
  const trimmed = trimSessions(sessions, bodies, parsed.activeSessionId)
  return { ...trimmed, messages: messagesOf(trimmed.bodies, trimmed.activeSessionId) }
}

/** v2（或更早）单会话结构 → 迁移为"第一条会话"。 */
function readLegacySingleSession(parsed: Record<string, unknown>): PersistedSessions {
  const messages = (Array.isArray(parsed.messages) ? parsed.messages : [])
    .map(normalizeStoredMessage)
    .filter((m): m is ChatMessage => !!m)
  const id = typeof parsed.sessionId === 'string' && parsed.sessionId
    ? parsed.sessionId
    : newId('session')
  const createdAt = messages[0]?.createdAt || Date.now()
  const meta: SessionMeta = {
    id,
    title: deriveTitle(firstQuestion(messages)),
    createdAt,
    updatedAt: messages[messages.length - 1]?.createdAt || createdAt,
  }
  const bodies = new Map<string, ChatMessage[]>([[id, cropMessages(messages)]])
  return { sessions: [meta], messages: bodies.get(id) as ChatMessage[], bodies, activeSessionId: id }
}

/** 取某会话的消息体（活动会话的消息体由 store 的 messages 持有，这里只处理非活动）。 */
function messagesOf(bodies: Map<string, ChatMessage[]>, id: string): ChatMessage[] {
  return bodies.get(id) || []
}

/** 把身份消息归一成 uid（兼容三种入参：`{uid,role}` 对象、裸 uid 字符串、null）。 */
function normalizeScopeUid(scope: UserScopeMessage | string | null | undefined): string | null {
  const raw = scope !== null && typeof scope === 'object' ? scope.uid : scope
  const text = raw === null || raw === undefined ? '' : String(raw).trim()
  return text ? text : null
}

/** schema 迁移（第四轮复核 P2-11 + 2026-09-20 多会话 P1）：
 * - 当前版本（v3 多会话）：正常解析；
 * - v2/v1（单会话）：迁移为"第一条会话"，消息逐条升级；
 * - 更新版本：不猜结构，原值隔离到 quarantined 并向用户提示；
 * - 结构非法：同样隔离原值后从空会话开始（不再静默丢弃）。
 */
function readPersisted(): PersistedSessions & { notice: PersistNotice | null } {
  const keys = [activeStorageKey(STORAGE_KEY_BASE), ...LEGACY_STORAGE_KEYS]
  for (const key of keys) {
    let raw: string | null = null
    try {
      raw = localStorage.getItem(key)
    } catch {
      return { ...emptyPersisted(), notice: null }
    }
    if (!raw) continue
    let parsed: Record<string, unknown> | null = null
    try {
      const candidate = JSON.parse(raw) as Record<string, unknown>
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
        throw new Error('根节点不是对象')
      }
      parsed = candidate
    } catch {
      return quarantined(key, raw, '本地会话数据无法解析',
                         '本地会话数据已损坏，已备份并新建会话')
    }
    const version = typeof parsed.schemaVersion === 'number' ? parsed.schemaVersion : 1
    if (version > STORAGE_SCHEMA_VERSION) {
      return quarantined(
        key, raw,
        `schemaVersion=${version} 高于当前支持的 ${STORAGE_SCHEMA_VERSION}`,
        '本地会话来自更新版本的页面，已隔离保存并新建会话（旧数据未被删除）',
      )
    }
    if (version >= 3 && Array.isArray(parsed.sessions)) {
      const read = readMultiSession(parsed)
      if (!read) {
        return quarantined(key, raw, 'sessions 结构非法',
                           '本地会话结构异常，已备份并新建会话')
      }
      return { ...read, notice: null }
    }
    const migrated = readLegacySingleSession(parsed)
    return {
      ...migrated,
      notice: { kind: 'info', text: '已从旧版本本地会话迁移到当前结构' },
    }
  }
  return { ...emptyPersisted(), notice: null }
}

/** 把无法安全解析的原始值挪到隔离键（不删除，便于用户/我们排查）。 */
function quarantine(sourceKey: string, raw: string, reason: string): void {
  try {
    localStorage.setItem(STORAGE_QUARANTINE_KEY, JSON.stringify({
      from: sourceKey,
      reason,
      at: new Date().toISOString(),
      raw: raw.slice(0, 20000),
    }))
    localStorage.removeItem(sourceKey)
  } catch {
    // 隔离也失败（配额/隐私模式）：保留原键，至少不丢数据
  }
}

export const useSessionStore = defineStore('session', () => {
  /** 当前内存状态来自哪个桶（换桶的唯一依据，见 applyUserScope 的说明）。 */
  let loadedScope: string | null = getActiveUid()
  const persisted = readPersisted()
  // 会话索引（标题/时间）与活动会话：活动会话的消息体就是下面的 messages
  const sessions = ref<SessionMeta[]>(persisted.sessions)
  const activeSessionId = ref<string>(persisted.activeSessionId)
  const messages = ref<ChatMessage[]>(persisted.messages)
  /** 非活动会话的消息体：只在切换/持久化时与 messages 显式交换，避免数组引用脱节 */
  const sessionBodies = new Map<string, ChatMessage[]>(persisted.bodies)
  const filters = reactive<Filters>(clone(DEFAULT_FILTERS))

  const backendReady = ref(false)
  const backendInfo = ref<string>('')
  const backendError = ref<string>('')
  const backendVersion = ref<string>('')

  const dynastyOptions = ref<DictEntry[]>([])
  const eventTypeOptions = ref<string[]>([])
  const dictError = ref<string>('')

  const active = ref<AssistantMessage | null>(null)
  const abort = ref<AbortController | null>(null)
  const toast = ref<{ kind: 'info' | 'warn' | 'error'; text: string } | null>(null)

  // 知识面板状态（从组件内提升到 store）：移动端点击引用需要在面板未挂载时也能"打开+定位"
  const panelOpen = ref(true)
  const panelTab = ref<PanelTab>('evidence')
  const citationFocus = ref<{ index: number; nonce: number } | null>(null)
  // 历史视图：selectedTurnId 为 null 表示跟随最新轮；focusMessage 是"滚动定位到某一轮"的信号
  const selectedTurnId = ref<string | null>(null)
  const focusMessage = ref<{ id: string; nonce: number } | null>(null)

  /** 当前会话 ID（发往后端；后端只用它做 SSE 事件关联）。 */
  const sessionId = computed(() => activeSessionId.value)

  const activeSessionMeta = computed<SessionMeta | null>(
    () => sessions.value.find((s) => s.id === activeSessionId.value) || null,
  )

  /** 当前会话标题（列表高亮与导出文件名用）。 */
  const activeSessionTitle = computed(
    () => activeSessionMeta.value?.title || DEFAULT_SESSION_TITLE,
  )

  /** 左侧会话列表（保持索引顺序：新会话在前，不随活动状态重排）。 */
  const sessionList = computed<SessionEntry[]>(() =>
    sessions.value.map((s) => ({
      ...s,
      active: s.id === activeSessionId.value,
      messageCount: s.id === activeSessionId.value
        ? messages.value.length
        : (sessionBodies.get(s.id)?.length ?? 0),
    })),
  )

  const activeMessage = computed<AssistantMessage | null>(() => active.value)

  /** 多轮历史：只取"可用的终态"，被取代的轮次排除，纠正结果作为该轮的问题-回答。 */
  const history = computed<HistoryTurn[]>(() => {
    const out: HistoryTurn[] = []
    let pendingQuestion: string | null = null
    for (const m of messages.value) {
      if (m.role === 'user') {
        pendingQuestion = m.question
        continue
      }
      const question = m.question || pendingQuestion || ''
      if (!question) continue
      pendingQuestion = null
      if (!isHistorySafe(m) || m.supersededBy) continue
      const answer = (m.answer || '').trim()
      if (!answer) continue
      out.push({ role: 'user', content: question })
      out.push({ role: 'assistant', content: m.answer })
    }
    return out
  })

  /** 最新一轮：活动流优先，否则最近一条已收敛的回答（判断"是否偏离最新"的基准）。 */
  const latestTurn = computed<AssistantMessage | null>(() => {
    if (active.value) return active.value
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
      const m = messages.value[i]
      if (m.role === 'assistant' && !isLive((m as AssistantMessage).turnStatus)) {
        return m as AssistantMessage
      }
    }
    // 全部在流式中的极端情况：退回最后一条助手消息
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
      const m = messages.value[i]
      if (m.role === 'assistant') return m as AssistantMessage
    }
    return null
  })

  /** 历史记录里选中的轮次；该轮已被清空/裁剪时返回 null（面板自动回退到最新）。 */
  const selectedTurn = computed<AssistantMessage | null>(() => {
    const id = selectedTurnId.value
    if (!id) return null
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
      const m = messages.value[i]
      if (m.role === 'assistant' && m.id === id) return m as AssistantMessage
    }
    return null
  })

  /** 面板当前展示的轮次：手选的历史轮 > 活动轮 > 最近一条已收敛的回答。 */
  const panelMessage = computed<AssistantMessage | null>(() => {
    if (selectedTurn.value) return selectedTurn.value
    return latestTurn.value
  })

  /** 面板是否停留在历史轮次（提示条与"返回最新"入口据此显示）。 */
  const isViewingHistory = computed(
    () => !!selectedTurn.value && selectedTurn.value.id !== latestTurn.value?.id,
  )

  /** 历史提问记录（倒序，最新在前）：含失败/取消/被重查取代的轮次，均可点回查看。 */
  const turnHistory = computed<HistoryEntry[]>(() => {
    const out: HistoryEntry[] = []
    let pendingQuestion = ''
    let index = 0
    for (const m of messages.value) {
      if (m.role === 'user') {
        pendingQuestion = m.question
        continue
      }
      const msg = m as AssistantMessage
      index += 1
      const question = msg.question || pendingQuestion
      pendingQuestion = ''
      out.push({
        id: msg.id,
        index,
        question,
        createdAt: msg.createdAt,
        turnStatus: msg.turnStatus,
        live: isLive(msg.turnStatus),
        superseded: !!msg.supersededBy,
        hasPanel: !!msg.panel,
      })
    }
    out.reverse()
    return out
  })

  let quotaWarned = false

  /** 某会话的消息体：活动会话取 messages，其余取 sessionBodies。 */
  function messagesFor(id: string): ChatMessage[] {
    return id === activeSessionId.value ? messages.value : (sessionBodies.get(id) || [])
  }

  /** 组装持久化载荷（会话数/每会话条数/是否剥离重字段由降级档位决定）。 */
  function buildPayload(options: {
    maxSessions: number
    perSession: number
    stripPanel: boolean
  }): Record<string, unknown> {
    const keep = [...sessions.value]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, options.maxSessions)
    const active = activeSessionMeta.value
    if (active && !keep.some((s) => s.id === active.id)) {
      keep[keep.length - 1] = active      // 活动会话必留（哪怕它是刚建的空会话）
    }
    return {
      schemaVersion: STORAGE_SCHEMA_VERSION,
      activeSessionId: activeSessionId.value,
      sessions: keep.map((s) => {
        let list = cropMessages(messagesFor(s.id), options.perSession)
        if (options.stripPanel) list = stripHeavy(list)
        return {
          id: s.id, title: s.title, createdAt: s.createdAt, updatedAt: s.updatedAt,
          messages: list,
        }
      }),
    }
  }

  function persist(): void {
    try {
      localStorage.setItem(activeStorageKey(STORAGE_KEY_BASE), JSON.stringify(buildPayload({
        maxSessions: MAX_SESSIONS,
        perSession: MAX_PERSISTED_MESSAGES,
        stripPanel: false,
      })))
      return
    } catch {
      // 配额/隐私模式：先降级（减会话数、去掉面板与过程记录、每会话只留 20 条）再试一次
    }
    try {
      localStorage.setItem(activeStorageKey(STORAGE_KEY_BASE), JSON.stringify(buildPayload({
        maxSessions: DEGRADED_SESSIONS,
        perSession: DEGRADED_MESSAGES,
        stripPanel: true,
      })))
      if (!quotaWarned) {
        quotaWarned = true
        showToast('warn', '本地存储接近上限：已裁剪历史附件与较早会话，仅影响刷新恢复')
      }
    } catch {
      if (!quotaWarned) {
        quotaWarned = true
        showToast('warn', '本地存储不可用（隐私模式或配额已满）：本次会话刷新后不会保留')
      }
    }
  }

  /**
   * 切换账号作用域（主应用 iframe 嵌入时通过 postMessage 告知，见 utils/userScope）。
   *
   * **本函数是换桶的唯一入口，顺序在这里保证**：
   *   ① 先把当前内存状态写回**旧**桶（此刻 activeUid 还是旧值，persist() 才对得上）；
   *   ② 再 setActiveUid 换桶；
   *   ③ 最后读新桶，替换内存状态。
   * 顺序反了会把上一个账号的会话写进新账号的桶里（串数据）。
   *
   * 用 `loadedScope`（而不是 getActiveUid()）判断"是否同一个桶"：桥只回调、不改 activeUid，
   * 但即使将来有人改了桥的实现，这里的判断也仍然对齐"当前内存状态来自哪个桶"这个事实。
   *
   * 没收到过消息时（独立访问 :8000）不会调用本函数，存储 key 不带后缀，行为与改造前一致。
   */
  function applyUserScope(scope: UserScopeMessage | string | null): void {
    const nextUid = normalizeScopeUid(scope)
    if (nextUid === loadedScope) return

    persist()                    // ① 写回旧桶（activeUid 尚未变）
    setActiveUid(nextUid)        // ② 换桶
    loadedScope = nextUid
    setActiveRole(typeof scope === 'object' && scope !== null ? scope.role : '')

    const reloaded = readPersisted()   // ③ 读新桶
    sessions.value = reloaded.sessions
    activeSessionId.value = reloaded.activeSessionId
    messages.value = reloaded.messages
    sessionBodies.clear()
    for (const [id, list] of reloaded.bodies) sessionBodies.set(id, list)

    // 上一账号的瞬态状态不能带过来：在途回答、选中轮、当前回答对象
    cancelStream()
    active.value = null
    selectedTurnId.value = null
    citationFocus.value = null

    if (reloaded.notice) showToast(reloaded.notice.kind, reloaded.notice.text)
  }

  // 流式期间的持久化节流（第四轮复核 P1-11）：每个 delta 都写 localStorage 会明显掉帧，
  // 但完全不写又会在刷新时丢掉 user 消息与半截正文。用 800ms 合并窗口，终态一律立即落盘。
  let persistTimer: number | undefined

  function schedulePersist(delayMs = 800): void {
    if (persistTimer !== undefined) return
    persistTimer = window.setTimeout(() => {
      persistTimer = undefined
      persist()
    }, delayMs)
  }

  function flushPersist(): void {
    if (persistTimer !== undefined) {
      window.clearTimeout(persistTimer)
      persistTimer = undefined
    }
    persist()
  }

  async function boot(): Promise<void> {
    if (persisted.notice) showToast(persisted.notice.kind, persisted.notice.text)
    try {
      const health = await fetchHealth()
      backendReady.value = health.status === 'ok'
      backendVersion.value = health.version || ''
      backendInfo.value = health.llm_available
        ? `数据版本 ${health.version || '-'} · 大模型已配置`
        : `数据版本 ${health.version || '-'} · 离线回答模式`
      if (health.load_error) backendError.value = health.load_error
    } catch (err) {
      backendReady.value = false
      backendError.value =
        err instanceof Error && err.message
          ? err.message
          : '无法连接 RAG 后端（请确认已启动 http://127.0.0.1:8000）'
    }
    try {
      const dicts = await fetchDicts()
      if (dicts.status === 'ok') {
        dynastyOptions.value = dicts.dynasty || []
        eventTypeOptions.value = dicts.event_type || []
        if (dicts.data_version) backendVersion.value = dicts.data_version
        dictError.value = ''
      } else {
        dictError.value = dicts.message || '词典接口返回错误'
      }
    } catch (err) {
      dictError.value = err instanceof Error && err.message ? err.message : '筛选词典加载失败'
    }
  }

  function showToast(kind: 'info' | 'warn' | 'error', text: string): void {
    toast.value = { kind, text }
    window.setTimeout(() => {
      if (toast.value?.text === text) toast.value = null
    }, 4200)
  }

  function toggleFilter(kind: keyof Filters, value: string): void {
    const list = filters[kind] as string[]
    const idx = list.indexOf(value)
    if (idx >= 0) list.splice(idx, 1)
    else list.push(value)
  }

  function setFilter(kind: keyof Filters, value: string, on: boolean): void {
    const list = filters[kind] as string[]
    const idx = list.indexOf(value)
    if (on && idx < 0) list.push(value)
    if (!on && idx >= 0) list.splice(idx, 1)
  }

  function clearFilters(): void {
    filters.dynasty.splice(0)
    filters.event_type.splice(0)
  }

  // ---- 多会话管理（2026-09-20 借鉴项 P1）----
  /** 视图类状态复位：切换/新建会话后，面板与历史选择不能停留在旧会话上。 */
  function resetViewState(): void {
    active.value = null
    citationFocus.value = null
    selectedTurnId.value = null
    focusMessage.value = null
  }

  /** 把当前会话的消息体挪进非活动区（切换/新建前调用）。 */
  function stashActiveBody(): void {
    sessionBodies.set(activeSessionId.value, messages.value)
  }

  function touchSession(id: string): void {
    const meta = sessions.value.find((s) => s.id === id)
    if (meta) meta.updatedAt = Date.now()
  }

  /** 会话索引超上限时按"最近更新"裁剪（活动会话必留），并回收对应消息体。 */
  function trimSessionIndex(): void {
    if (sessions.value.length <= MAX_SESSIONS) return
    const keep = [...sessions.value]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_SESSIONS)
    const active = activeSessionMeta.value
    if (active && !keep.some((s) => s.id === active.id)) {
      keep[keep.length - 1] = active
    }
    const keepIds = new Set(keep.map((s) => s.id))
    for (const meta of sessions.value) {
      if (!keepIds.has(meta.id)) sessionBodies.delete(meta.id)
    }
    sessions.value = sessions.value.filter((s) => keepIds.has(s.id))
  }

  /** 首条提问自动命名会话（旧页口径：前 15 字）；已自动命名或被重命名的不覆盖。 */
  function autoTitleSession(question: string): void {
    const meta = activeSessionMeta.value
    if (!meta) return
    if (meta.title && meta.title !== DEFAULT_SESSION_TITLE) return
    meta.title = deriveTitle(question)
  }

  /** 切换会话：保存当前会话的消息体，装载目标会话。 */
  function switchSession(id: string): void {
    if (!id || id === activeSessionId.value) return
    const target = sessions.value.find((s) => s.id === id)
    if (!target) return
    cancelStream()
    stashActiveBody()
    const body = sessionBodies.get(id) || []
    sessionBodies.delete(id)        // 目标成为活动会话：消息体改由 messages 持有
    activeSessionId.value = id
    messages.value = body
    resetViewState()
    touchSession(id)
    persist()
  }

  /** 新建会话；当前会话还没有任何消息时不重复新建（避免堆一堆空会话）。 */
  function createSession(): void {
    if (!messages.value.length && !active.value) {
      showToast('info', '当前已经是新会话')
      return
    }
    cancelStream()
    stashActiveBody()
    const meta = newSessionMeta()
    sessions.value.unshift(meta)
    trimSessionIndex()
    activeSessionId.value = meta.id
    messages.value = []
    resetViewState()
    persist()
  }

  /** 删除会话；删掉活动会话时切到最近更新的其它会话，删空后补一条空会话。 */
  function deleteSession(id: string): void {
    const index = sessions.value.findIndex((s) => s.id === id)
    if (index < 0) return
    const wasActive = id === activeSessionId.value
    if (wasActive) cancelStream()
    sessionBodies.delete(id)
    sessions.value.splice(index, 1)

    if (!sessions.value.length) {
      const meta = newSessionMeta()
      sessions.value.push(meta)
      if (wasActive) {
        activeSessionId.value = meta.id
        messages.value = []
        resetViewState()
      }
      persist()
      return
    }
    if (wasActive) {
      const next = [...sessions.value].sort((a, b) => b.updatedAt - a.updatedAt)[0]
      activeSessionId.value = next.id
      messages.value = sessionBodies.get(next.id) || []
      sessionBodies.delete(next.id)
      resetViewState()
    }
    persist()
  }

  /** 重命名会话（空标题回退默认名；不改 updatedAt——重命名不是"使用"）。 */
  function renameSession(id: string, title: string): void {
    const meta = sessions.value.find((s) => s.id === id)
    if (!meta) return
    const text = (title || '').trim().slice(0, 60)
    meta.title = text || DEFAULT_SESSION_TITLE
    persist()
  }

  /** 设置终态并同步派生布尔（唯一入口，避免三个布尔互相矛盾）。 */
  function setTurnStatus(
    msg: AssistantMessage,
    turnStatus: TurnStatus,
    errorText?: string,
  ): void {
    msg.turnStatus = turnStatus
    msg.streaming = isLive(turnStatus)
    msg.finished = isHistorySafe(msg)
    msg.cancelled = turnStatus === 'cancelled'
    if (turnStatus === 'interrupted') msg.partial = true
    if (errorText !== undefined) msg.error = errorText || undefined
  }

  function createTurn(question: string): AssistantMessage {
    const assistant: AssistantMessage = {
      id: newId('assistant'),
      role: 'assistant',
      question,
      answer: '',
      citations: [],
      conflicts: [],
      entities: [],
      candidates: [],
      entityCards: [],
      panel: null,
      status: [{ stage: 'start', label: stageLabel('start'), at: Date.now() }],
      turnStatus: 'connecting',
      streaming: true,
      cancelled: false,
      finished: false,
      correctedEntities: [],
      createdAt: Date.now(),
    }
    return asReactiveMessage(assistant)
  }

  /** 串行化所有提问入口：任何时刻只允许一条活动流（第四轮复核 P1-10）。
   *
   * 旧实现里 retry/correct 直接在活动流上启动新请求：全局 active 与 AbortController 被覆盖，
   * 旧请求继续占用后端资源，而它的事件因 id 守卫被静默丢弃——表现为"重试没反应、还更慢"。
   * 现在统一走这里：先 abort 并**等前一条流真正收尾**，再启动新的一条。
   */
  let runChain: Promise<void> = Promise.resolve()
  /** 递增的请求序号：判断"排队中的这一轮是否已被更新的请求取代" */
  let turnSeq = 0

  /** 创建并启动一轮。**必须同步建消息**，这样并发调用时后一个请求
   * 能立刻取消前一个（前一个可能还没真正发出）。 */
  function beginTurn(
    question: string,
    options: {
      filters: Filters
      corrected?: CorrectedEntity[]
      source?: AssistantMessage
      pushUser: boolean
    },
  ): AssistantMessage {
    cancelStream()          // 立刻中断当前活动流（若有）
    selectedTurnId.value = null   // 新提问回到最新视图：历史面板不停留在被点开的旧轮上

    const assistant = createTurn(question)
    assistant.requestFilters = options.filters
    assistant.correctedEntities = options.corrected || []
    if (options.source) {
      assistant.sourceTurnId = options.source.id
      assistant.entities = options.source.entities.map((e) => ({ ...e }))
      options.source.supersededBy = assistant.id
    }
    if (options.pushUser) {
      autoTitleSession(question)     // 首条提问即给会话命名（列表里可辨认）
      messages.value.push({
        id: newId('user'),
        role: 'user',
        question,
        filters: options.filters,
        createdAt: Date.now(),
      } as UserMessage)
    }
    messages.value.push(assistant)
    active.value = assistant
    touchSession(activeSessionId.value)   // 会话"最近使用"时间（删除后回退与容量裁剪依据）
    flushPersist()          // 入队即落盘：刷新后至少能看到问题与"已中断"的空回答

    const mySeq = ++turnSeq
    const job = runChain.then(async () => {
      // 排队期间又来了更新的提问：这一轮直接收敛，不再发请求（严格单流）
      if (mySeq !== turnSeq) {
        if (isLive(assistant.turnStatus)) {
          setTurnStatus(assistant, 'cancelled', '已被新的提问取代')
        }
        if (active.value && active.value.id === assistant.id) active.value = null
        flushPersist()
        return
      }
      await launch(assistant, question, options.filters, assistant.correctedEntities)
    })
    runChain = job.catch(() => undefined)
    return assistant
  }

  async function sendQuestion(raw: string): Promise<void> {
    const question = raw.trim()
    if (!question) return
    beginTurn(question, { filters: clone(filters), pushUser: true })
  }

  async function launch(
    assistant: AssistantMessage,
    question: string,
    requestFilters: Filters,
    corrected: CorrectedEntity[],
  ): Promise<void> {
    assistant.error = undefined
    const controller = new AbortController()
    abort.value = controller
    const historyForRequest: HistoryTurn[] = history.value.slice(-8)
    const outcome = await streamQuery(
      {
        session_id: sessionId.value,
        question,
        history: historyForRequest,
        filters: requestFilters,
        corrected_entities: corrected,
      },
      {
        signal: controller.signal,
        onEvent: (event: SSEEnvelope) => {
          // 按 id 比较而不是对象引用：assistant 与 active.value 可能分别是原始对象/代理，
          // 引用比较会在两者不等时静默丢掉全部事件（2026-09-15 复核发现的浏览器端卡死）。
          if (!active.value || assistant.id !== active.value.id) {
            // 已经收到 done 却还在来的业务帧属于协议错误：计数但不改动状态（幂等保护）
            if (assistant.doneSeen && event.type !== 'done') {
              assistant.protocolErrors = (assistant.protocolErrors || 0) + 1
            }
            return
          }
          handleEvent(assistant, event)
        },
        onError: (message: string) => {
          // 先记录原因；终态统一在流结束后收敛，避免"error 后 done"或"无 done"两种时序各写一套
          if (message !== '已取消') assistant.error = message
        },
      },
    )

    if (isLive(assistant.turnStatus)) {
      switch (outcome) {
        case 'aborted':
          setTurnStatus(assistant, 'cancelled', '已取消本次回答')
          break
        case 'connect_timeout':
        case 'idle_timeout':
        case 'total_timeout':
          // 已有正文说明回答可能不完整，按 interrupted 保留；否则是彻底失败
          if (assistant.answer) {
            setTurnStatus(assistant, 'interrupted',
                          assistant.error || '等待超时，回答可能不完整')
          } else {
            setTurnStatus(assistant, 'failed', assistant.error || '等待超时')
          }
          break
        case 'http_error':
        case 'network_error':
        case 'protocol_error':
        case 'parse_error':
          setTurnStatus(assistant, assistant.answer ? 'interrupted' : 'failed',
                        assistant.error || '请求失败')
          break
        default:
          // 字节流结束但没收到 done：半截回答既不能当完成，也不能一直转圈
          if (assistant.error) {
            setTurnStatus(assistant, assistant.answer ? 'interrupted' : 'failed',
                          assistant.error)
          } else {
            setTurnStatus(assistant, 'interrupted', '连接中断，回答可能不完整')
          }
      }
    }
    if (active.value && active.value.id === assistant.id) active.value = null
    if (abort.value === controller) abort.value = null
    flushPersist()
  }

  /** 是否已进入终态（done 之后到达的业务帧一律忽略，避免"完成后又被改回"）。 */
  function isTerminal(msg: AssistantMessage): boolean {
    return !isLive(msg.turnStatus)
  }

  function handleEvent(assistant: AssistantMessage, event: SSEEnvelope): void {
    const data = event.data || {}
    // 协议守卫（第四轮复核 P1-9）：done 之后再来的业务帧属于协议错误，忽略并计数，
    // 不能让它把已完成/已失败的轮次改回"流式中"。
    if (isTerminal(assistant) && event.type !== 'done' && event.type !== 'error') {
      assistant.protocolErrors = (assistant.protocolErrors || 0) + 1
      return
    }
    switch (event.type) {
      case 'status': {
        const stage = data.stage || event.stage
        if (stage && stage !== 'start') {
          assistant.status.push({ stage, label: stageLabel(stage), at: Date.now() })
          if (assistant.turnStatus === 'connecting') setTurnStatus(assistant, 'streaming')
        }
        break
      }
      case 'entities': {
        assistant.entities = (data.entities || []).map((e: EntityInfo) => ({ ...e }))
        assistant.candidates = (data.candidates || []).map((c: EntityCandidate) => ({
          ...c,
          options: (c.options || []).map((o) => ({ ...o })),
        }))
        break
      }
      case 'answer': {
        const delta = String(data.delta || '')
        if (delta) {
          assistant.answer += delta
          if (assistant.turnStatus === 'connecting') setTurnStatus(assistant, 'streaming')
          schedulePersist()      // 节流落盘：刷新时至少保留到最近 800ms 的正文（P1-11）
        }
        break
      }
      case 'citations': {
        assistant.citations = (data.citations || []).map((c: Citation) => ({ ...c }))
        if (Array.isArray(data.conflicts)) {
          assistant.conflicts = (data.conflicts || []).map((c: Conflict) => ({ ...c }))
        }
        break
      }
      case 'panel': {
        const panel = normalizePanel(data)
        assistant.panel = panel
        assistant.entityCards = panel.entity_cards || []
        break
      }
      case 'error': {
        assistant.error = data.message || '服务端错误'
        break
      }
      case 'done': {
        // 重复 done 幂等：已经进入终态就不再改写（协议允许重发，但不能覆盖既有结论）
        if (isTerminal(assistant) && assistant.finishReason && event.type === 'done'
            && assistant.doneSeen) {
          return
        }
        const done = (data || {}) as DoneData
        const reason = String(done.finish_reason || '')
        assistant.finishReason = reason || 'normal'
        assistant.doneSeen = true
        if (done.truncated) assistant.truncated = true
        if (done.cache_hit) {
          assistant.status.push({ stage: 'cache_hit', label: '缓存命中', at: Date.now() })
        }
        let next = turnStatusFromFinishReason(reason, !!assistant.error)
        // 收到过 error 事件时，normal done 不得把这一轮标成"完成"（第四轮复核 P1-9）：
        // 否则页面同时显示错误与"已生成"，而且这轮还会被写进下一轮历史。
        // 只有明确的 refused / degraded 仍按后端语义进入可用终态。
        if (assistant.error && next === 'completed') {
          next = assistant.answer ? 'interrupted' : 'failed'
        }
        setTurnStatus(assistant, next,
                      next === 'failed' || next === 'interrupted' ? assistant.error : undefined)
        active.value = null
        abort.value = null
        flushPersist()
        break
      }
      default:
        break
    }
  }

  function normalizePanel(data: any): PanelData {
    const panel: PanelData = clone(EMPTY_PANEL)
    if (!data) return panel
    panel.entity_cards = Array.isArray(data.entity_cards)
      ? data.entity_cards.map((c: EntityCard) => ({ ...c }))
      : []
    panel.subgraph = data.subgraph || { nodes: [], edges: [] }
    panel.subgraph = {
      nodes: Array.isArray(panel.subgraph.nodes) ? panel.subgraph.nodes : [],
      edges: Array.isArray(panel.subgraph.edges) ? panel.subgraph.edges : [],
    }
    panel.timeline = data.timeline || { groups: [] }
    panel.timeline.groups = Array.isArray(panel.timeline.groups) ? panel.timeline.groups : []
    panel.map_points = Array.isArray(data.map_points) ? data.map_points : []
    return panel
  }

  function cancelStream(): void {
    if (abort.value) abort.value.abort()
    abort.value = null
    const msg = active.value
    if (msg) {
      setTurnStatus(msg, 'cancelled', '已取消本次回答')
      active.value = null
      flushPersist()
    }
  }

  /** 纠正重查：以某条 assistant 消息为源，按该消息的问题 + 纠正指令追加一轮新回答。
   * 源轮次标记 supersededBy —— 后续追问使用纠正后的答案，而不是被替换的旧答案（P0-4）。 */
  function correctEntity(
    source: AssistantMessage,
    payload: {
      option?: CandidateOption
      entity?: EntityInfo
      candidate?: EntityCandidate
      action: 'add' | 'replace' | 'remove'
    },
  ): void {
    if (!source || source.role !== 'assistant') return
    // 契约口径（P1-3）：源与目标分别用 source_entity_id / replacement_entity_id 表达。
    // 同名不同朝代共享 standard_name，只传名字时后端无法判断用户选的是哪一个。
    const corrections: CorrectedEntity[] = []
    if (payload.action === 'add' && payload.option) {
      corrections.push({
        action: 'add',
        replacement_entity_id: payload.option.entity_id,
        entity_type:
          payload.option.entity_type ||
          payload.candidate?.entity_type ||
          payload.entity?.type,
        name: payload.option.standard_name,
      })
    } else if (payload.action === 'replace' && payload.option && payload.entity) {
      const opt = payload.option
      const sourceId = payload.entity.entity_id
      const relatedCandidate = source.candidates.find((cand) =>
        cand.options.some((o) =>
          sourceId && o.entity_id
            ? o.entity_id === sourceId
            : o.standard_name === opt.standard_name,
        ),
      )
      if (relatedCandidate) {
        corrections.push({
          action: 'replace',
          source_entity_id: sourceId,
          replacement_entity_id: opt.entity_id,
          entity_type: opt.entity_type || relatedCandidate.entity_type || payload.entity.type,
          original: payload.entity.standard_name || payload.entity.name,
          replacement: opt.standard_name,
        })
      } else {
        // 该替换目标不在同名候选组内：删旧 + 加新，后端按两条指令顺序执行
        corrections.push({
          action: 'remove',
          source_entity_id: sourceId,
          entity_type: payload.entity.type,
          original: payload.entity.standard_name || payload.entity.name,
        })
        corrections.push({
          action: 'add',
          replacement_entity_id: opt.entity_id,
          entity_type: opt.entity_type || payload.entity.type,
          name: opt.standard_name,
        })
      }
    } else if (payload.action === 'remove' && payload.entity) {
      corrections.push({
        action: 'remove',
        source_entity_id: payload.entity.entity_id,
        entity_type: payload.entity.type,
        original: payload.entity.standard_name || payload.entity.name,
      })
    }
    if (!corrections.length) return
    const question = source.question
    if (!question) return
    void beginTurn(question, {
      filters: clone(source.requestFilters || filters),
      corrected: corrections,
      source,
      pushUser: false,
    })
  }

  /** 手动"按实体重查"：以某条 assistant 消息为源，补齐一个标准实体后重查。 */
  function addEntityManual(
    source: AssistantMessage,
    standardName: string,
    entityType: string,
  ): void {
    if (!source || source.role !== 'assistant' || !standardName.trim()) return
    const question = source.question
    if (!question) return
    void beginTurn(question, {
      filters: clone(source.requestFilters || filters),
      corrected: [{
        action: 'add',
        entity_type: entityType,
        name: standardName.trim(),
      }],
      source,
      pushUser: false,
    })
  }

  /** 失败/中断轮的重试：沿用原问题、原筛选与原纠正项（P1-20）。
   * 走统一的 beginTurn：先取消并等待前一条流，保证任何时刻只有一条活动流（P1-10）。 */
  function retryTurn(source: AssistantMessage): void {
    if (!source || source.role !== 'assistant') return
    if (isLive(source.turnStatus)) return
    const question = source.question
    if (!question) return
    void beginTurn(question, {
      filters: clone(source.requestFilters || filters),
      corrected: clone(source.correctedEntities || []),
      source,
      pushUser: false,
    })
  }

  function latestStage(msg: AssistantMessage | null): string {
    if (!msg || !msg.status.length) return ''
    const last = msg.status[msg.status.length - 1]
    return last.label
  }

  // ---- 知识面板导航（移动端可从引用直达证据）----
  function requestCitation(index: number, messageId?: string): void {
    if (typeof index !== 'number' || Number.isNaN(index)) return
    // 带轮次 id 时先切到该轮：否则点历史消息的引用会把高亮打到最新一轮上
    if (messageId) selectedTurnId.value = messageId
    panelTab.value = 'evidence'
    panelOpen.value = true
    citationFocus.value = { index, nonce: (citationFocus.value?.nonce ?? 0) + 1 }
  }

  /** 跳转到某一轮问答（点历史记录）：面板展示该轮数据，聊天区滚动定位到该轮。 */
  function selectTurn(id: string): void {
    if (!id) return
    selectedTurnId.value = id
    panelOpen.value = true
    citationFocus.value = null
    focusMessage.value = { id, nonce: (focusMessage.value?.nonce ?? 0) + 1 }
  }

  /** 从历史轮次返回最新一轮（面板恢复跟随最新）。 */
  function returnToLatest(): void {
    selectedTurnId.value = null
  }

  function setPanelTab(tab: PanelTab): void {
    panelTab.value = tab
  }

  function togglePanel(): void {
    panelOpen.value = !panelOpen.value
  }

  return {
    sessionId,
    messages,
    // 多会话：索引、活动会话标题、切换/新建/删除/重命名
    activeSessionId,
    activeSessionTitle,
    sessionList,
    switchSession,
    createSession,
    deleteSession,
    renameSession,
    // 暴露给组件/测试观察：实际发给后端的多轮上下文（只含可用终态、排除被取代轮次）
    history,
    filters,
    backendReady,
    backendInfo,
    backendError,
    backendVersion,
    dynastyOptions,
    eventTypeOptions,
    dictError,
    activeMessage,
    toast,
    panelOpen,
    panelTab,
    citationFocus,
    panelMessage,
    showToast,
    boot,
    /** 切换账号作用域（主应用 iframe 通过 postMessage 告知身份后调用；独立访问不会调用） */
    applyUserScope,
    /** 等待当前活动流收尾（严格单流约束的可观测入口，测试也用它同步） */
    whenIdle: () => runChain,
    toggleFilter,
    setFilter,
    clearFilters,
    sendQuestion,
    cancelStream,
    correctEntity,
    addEntityManual,
    retryTurn,
    latestStage,
    requestCitation,
    setPanelTab,
    togglePanel,
    // 历史提问记录：选中的轮次、派生列表与"返回最新"
    selectedTurnId,
    selectedTurn,
    latestTurn,
    isViewingHistory,
    turnHistory,
    focusMessage,
    selectTurn,
    returnToLatest,
  }
})
