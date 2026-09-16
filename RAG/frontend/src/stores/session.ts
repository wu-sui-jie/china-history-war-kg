/** 会话与问答状态（localStorage 持久化）。
 *
 * 状态机（2026-09-15 审核 P0-4/P0-5/P0-6）：
 * - 每轮回答只有一个终态 `turnStatus`，`streaming/finished/cancelled` 由它派生，
 *   不再各自为政（历史实现里 done 会把 error 轮标成 finished，进而污染下一轮上下文）；
 * - 只有 completed/refused/degraded 进入多轮历史，且被纠正结果替代的轮次会被排除；
 * - 流自然结束但没收到 done → interrupted（半截回答不进历史，也不再永久转圈）；
 * - 刷新恢复时把所有未收敛的瞬态状态迁移为 interrupted（幽灵流式消息没有 AbortController）。
 */

import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

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

/** 存储键带 schema 版本：字段结构变化时可以并存而不是把旧数据读坏。 */
const STORAGE_KEY = 'ragv5-session-v2'
const LEGACY_STORAGE_KEYS = ['ragv3-session-v1', 'ragv5-session-v1']
const STORAGE_SCHEMA_VERSION = 2
/** 无法安全解析/版本过新的原值隔离位置（不删除，便于排查） */
const STORAGE_QUARANTINE_KEY = 'ragv5-session-quarantine'
/** 持久化上限：超长会话只保留最近若干条，避免把 localStorage 写爆导致恢复整体失效。 */
const MAX_PERSISTED_MESSAGES = 60
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
function cropMessages(messages: ChatMessage[]): ChatMessage[] {
  let kept = messages.slice(-MAX_PERSISTED_MESSAGES)
  while (kept.length && kept[0].role === 'assistant') kept = kept.slice(1)
  return kept
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

/** schema 迁移（第四轮复核 P2-11）：
 * - 当前版本：正常解析；
 * - 更早版本：交给 normalizeStoredMessage 逐条升级；
 * - 更新版本：不猜结构，原值隔离到 quarantined 并向用户提示；
 * - 结构非法：同样隔离原值后从空会话开始（不再静默丢弃）。
 */
function readPersisted(): {
  sessionId: string
  messages: ChatMessage[]
  notice: PersistNotice | null
} {
  const keys = [STORAGE_KEY, ...LEGACY_STORAGE_KEYS]
  for (const key of keys) {
    let raw: string | null = null
    try {
      raw = localStorage.getItem(key)
    } catch {
      return { sessionId: newId('session'), messages: [], notice: null }
    }
    if (!raw) continue
    let parsed: { schemaVersion?: unknown; sessionId?: unknown; messages?: unknown } | null = null
    try {
      const candidate = JSON.parse(raw) as Record<string, unknown>
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
        throw new Error('根节点不是对象')
      }
      parsed = candidate
    } catch {
      quarantine(key, raw, '本地会话数据无法解析')
      return {
        sessionId: newId('session'),
        messages: [],
        notice: { kind: 'warn', text: '本地会话数据已损坏，已备份并新建会话' },
      }
    }
    const version = typeof parsed.schemaVersion === 'number' ? parsed.schemaVersion : 1
    if (version > STORAGE_SCHEMA_VERSION) {
      quarantine(key, raw, `schemaVersion=${version} 高于当前支持的 ${STORAGE_SCHEMA_VERSION}`)
      return {
        sessionId: newId('session'),
        messages: [],
        notice: {
          kind: 'warn',
          text: '本地会话来自更新版本的页面，已隔离保存并新建会话（旧数据未被删除）',
        },
      }
    }
    const rawMessages = Array.isArray(parsed.messages) ? parsed.messages : []
    const messages = rawMessages
      .map(normalizeStoredMessage)
      .filter((m): m is ChatMessage => !!m)
    return {
      sessionId: typeof parsed.sessionId === 'string' && parsed.sessionId
        ? parsed.sessionId
        : newId('session'),
      messages: cropMessages(messages),
      notice: version < STORAGE_SCHEMA_VERSION
        ? { kind: 'info', text: '已从旧版本本地会话迁移到当前结构' }
        : null,
    }
  }
  return { sessionId: newId('session'), messages: [], notice: null }
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
  const persisted = readPersisted()
  const sessionId = ref<string>(persisted.sessionId)
  const messages = ref<ChatMessage[]>(persisted.messages)
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

  /** 面板当前展示的轮次：活动轮优先，否则最近一条已收敛的回答。 */
  const panelMessage = computed<AssistantMessage | null>(() => {
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

  let quotaWarned = false

  function persist(): void {
    const payload = {
      schemaVersion: STORAGE_SCHEMA_VERSION,
      sessionId: sessionId.value,
      messages: cropMessages(messages.value),
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
      return
    } catch {
      // 配额/隐私模式：先降级（去掉面板与过程记录、只留最近 20 条）再试一次
    }
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ ...payload, messages: stripHeavy(payload.messages).slice(-20) }),
      )
      if (!quotaWarned) {
        quotaWarned = true
        showToast('warn', '本地存储接近上限：已裁剪历史附件，仅影响刷新恢复')
      }
    } catch {
      if (!quotaWarned) {
        quotaWarned = true
        showToast('warn', '本地存储不可用（隐私模式或配额已满）：本次会话刷新后不会保留')
      }
    }
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

  function clearConversation(): void {
    cancelStream()
    sessionId.value = newId('session')
    messages.value = []
    active.value = null
    citationFocus.value = null
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

    const assistant = createTurn(question)
    assistant.requestFilters = options.filters
    assistant.correctedEntities = options.corrected || []
    if (options.source) {
      assistant.sourceTurnId = options.source.id
      assistant.entities = options.source.entities.map((e) => ({ ...e }))
      options.source.supersededBy = assistant.id
    }
    if (options.pushUser) {
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
  function requestCitation(index: number): void {
    if (typeof index !== 'number' || Number.isNaN(index)) return
    panelTab.value = 'evidence'
    panelOpen.value = true
    citationFocus.value = { index, nonce: (citationFocus.value?.nonce ?? 0) + 1 }
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
    /** 等待当前活动流收尾（严格单流约束的可观测入口，测试也用它同步） */
    whenIdle: () => runChain,
    toggleFilter,
    setFilter,
    clearFilters,
    clearConversation,
    sendQuestion,
    cancelStream,
    correctEntity,
    addEntityManual,
    retryTurn,
    latestStage,
    requestCitation,
    setPanelTab,
    togglePanel,
  }
})
