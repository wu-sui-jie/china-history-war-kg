/** 会话与问答状态（localStorage 持久化）。
 *
 * - 保存本轮所有 user/assistant 消息，刷新后保留；
 * - 发起/取消/纠正均维护一个"当前活动 assistant 消息"；
 * - 发送给后端的 history 由已结束轮次 + 当前问题组装；
 * - corrected_entities 纠正：取消当前流 → 用原问题重发（不修改已结束历史）。
 */

import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchDicts, fetchHealth } from '@/api/http'
import { streamQuery } from '@/api/sse'
import {
  STAGE_LABELS,
  type CandidateOption,
  type Citation,
  type Conflict,
  type CorrectedEntity,
  type DictEntry,
  type EntityCandidate,
  type EntityCard,
  type EntityInfo,
  type Filters,
  type HistoryTurn,
  type PanelData,
  type SSEEnvelope,
} from '@/types/contract'

const STORAGE_KEY = 'ragv3-session-v1'
const DEFAULT_FILTERS: Filters = { dynasty: [], event_type: [] }

const EMPTY_PANEL: PanelData = {
  entity_cards: [],
  subgraph: { nodes: [], edges: [] },
  timeline: { groups: [] },
  map_points: [],
}

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
  streaming: boolean
  cancelled: boolean
  finished: boolean
  finishReason?: string
  error?: string
  correctedEntities: CorrectedEntity[]
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

function readPersisted(): { sessionId: string; messages: ChatMessage[] } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { sessionId: newId('session'), messages: [] }
    const parsed = JSON.parse(raw) as {
      sessionId?: string
      messages?: ChatMessage[]
    }
    return {
      sessionId: parsed.sessionId || newId('session'),
      messages: parsed.messages || [],
    }
  } catch {
    return { sessionId: newId('session'), messages: [] }
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

  const activeMessage = computed<AssistantMessage | null>(() => active.value)
  const history = computed<HistoryTurn[]>(() => {
    const out: HistoryTurn[] = []
    let lastUser: UserMessage | null = null
    for (const m of messages.value) {
      if (m.role === 'user') {
        lastUser = m
      } else if (m.role === 'assistant' && m.finished && !m.cancelled && lastUser) {
        out.push({ role: 'user', content: lastUser.question })
        out.push({ role: 'assistant', content: m.answer })
        lastUser = null
      }
    }
    return out
  })

  function persist(): void {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          sessionId: sessionId.value,
          messages: messages.value,
        }),
      )
    } catch {
      // localStorage 不可用（隐私模式/配额）时仅影响刷新恢复
    }
  }

  async function boot(): Promise<void> {
    try {
      const health = await fetchHealth()
      backendReady.value = health.status === 'ok'
      backendVersion.value = health.version || ''
      backendInfo.value = health.llm_available
        ? `数据版本 ${health.version || '-'} · 大模型已配置`
        : `数据版本 ${health.version || '-'} · 离线回答模式`
      if (health.load_error) backendError.value = health.load_error
    } catch {
      backendReady.value = false
      backendError.value = '无法连接 RAG 后端（请确认已启动 http://127.0.0.1:8000）'
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
    } catch {
      dictError.value = '筛选词典加载失败'
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
    persist()
  }

  async function sendQuestion(raw: string): Promise<void> {
    const question = raw.trim()
    if (!question) return
    if (abort.value) cancelStream()
    const userMsg: UserMessage = {
      id: newId('user'),
      role: 'user',
      question,
      filters: clone(filters),
      createdAt: Date.now(),
    }
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
      streaming: true,
      cancelled: false,
      finished: false,
      correctedEntities: [],
      createdAt: Date.now(),
    }
    messages.value.push(userMsg)
    messages.value.push(assistant)
    active.value = assistant
    persist()
    await launch(assistant, question, clone(filters), [])
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
    await streamQuery(
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
          if (assistant !== active.value) return
          handleEvent(assistant, event)
        },
        onError: (message: string) => {
          if (message === '已取消') {
            assistant.cancelled = true
            assistant.error = '已取消本次回答'
          } else {
            assistant.error = message
            // 网络/解析异常：解除“进行中”占用，允许再次提问（该轮保留错误现场）
            if (assistant === active.value) active.value = null
          }
          assistant.streaming = false
          assistant.finished = false
          persist()
        },
      },
    )
    if (assistant === active.value && !assistant.finished && !assistant.cancelled) {
      // 正常流自然结束由 done 事件处理；此处兜底标记避免永远 spinning
      if (assistant.error) {
        assistant.streaming = false
      }
      persist()
    }
    if (abort.value === controller) abort.value = null
  }

  function handleEvent(assistant: AssistantMessage, event: SSEEnvelope): void {
    const data = event.data || {}
    switch (event.type) {
      case 'status': {
        const stage = data.stage || event.stage
        if (stage && stage !== 'start') {
          assistant.status.push({ stage, label: stageLabel(stage), at: Date.now() })
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
        assistant.answer += String(data.delta || '')
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
        assistant.finished = true
        assistant.streaming = false
        assistant.finishReason = data.finish_reason || 'normal'
        if (data.cache_hit) {
          assistant.status.push({
            stage: 'cache_hit',
            label: '缓存命中',
            at: Date.now(),
          })
        }
        active.value = null
        abort.value = null
        persist()
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
      msg.cancelled = true
      msg.error = '已取消本次回答'
      msg.streaming = false
      active.value = null
      persist()
    }
  }

  function createCorrectionMessage(): AssistantMessage {
    const assistant: AssistantMessage = {
      id: newId('assistant'),
      role: 'assistant',
      question: '',
      answer: '',
      citations: [],
      conflicts: [],
      entities: [],
      candidates: [],
      entityCards: [],
      panel: null,
      status: [],
      streaming: true,
      cancelled: false,
      finished: false,
      correctedEntities: [],
      createdAt: Date.now(),
    }
    messages.value.push(assistant)
    active.value = assistant
    return assistant
  }

  /** 纠正重查：以某条 assistant 消息为源（正在流或已结束均可），
   * 按该消息的问题 + 纠正指令追加一轮新回答，不修改原消息内容。 */
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
    const corrections: CorrectedEntity[] = []
    if (payload.action === 'add' && payload.option) {
      corrections.push({
        action: 'add',
        entity_type:
          payload.option.entity_type ||
          payload.candidate?.entity_type ||
          payload.entity?.type,
        name: payload.option.standard_name,
      })
    } else if (payload.action === 'replace' && payload.option && payload.entity) {
      const opt = payload.option
      const relatedCandidate = source.candidates.find((cand) =>
        cand.options.some((o) => o.standard_name === opt.standard_name),
      )
      if (relatedCandidate) {
        corrections.push({
          action: 'replace',
          entity_type: opt.entity_type || relatedCandidate.entity_type || payload.entity.type,
          original: payload.entity.standard_name || payload.entity.name,
          replacement: opt.standard_name,
        })
      } else {
        // 该替换目标不在同名候选组内：删旧 + 加新，后端按两条指令顺序执行
        corrections.push({
          action: 'remove',
          entity_type: payload.entity.type,
          original: payload.entity.standard_name || payload.entity.name,
        })
        corrections.push({
          action: 'add',
          entity_type: opt.entity_type || payload.entity.type,
          name: opt.standard_name,
        })
      }
    } else if (payload.action === 'remove' && payload.entity) {
      corrections.push({
        action: 'remove',
        entity_type: payload.entity.type,
        original: payload.entity.standard_name || payload.entity.name,
      })
    }
    if (!corrections.length) return
    cancelStream()
    const question = source.question
    const requestFilters = clone(filters)
    const assistant = createCorrectionMessage()
    assistant.question = question
    assistant.correctedEntities = corrections
    assistant.status.push({ stage: 'start', label: stageLabel('start'), at: Date.now() })
    assistant.entities = source.entities.map((e) => ({ ...e }))
    persist()
    void launch(assistant, question, requestFilters, corrections)
  }

  /** 手动“按实体重查”：以某条 assistant 消息为源，补齐一个标准实体后重查。 */
  function addEntityManual(
    source: AssistantMessage,
    standardName: string,
    entityType: string,
  ): void {
    if (!source || source.role !== 'assistant' || !standardName.trim()) return
    cancelStream()
    const question = source.question
    const assistant = createCorrectionMessage()
    assistant.question = question
    assistant.correctedEntities = [
      {
        action: 'add',
        entity_type: entityType,
        name: standardName.trim(),
      },
    ]
    assistant.status.push({ stage: 'start', label: stageLabel('start'), at: Date.now() })
    persist()
    void launch(assistant, question, clone(filters), assistant.correctedEntities)
  }

  function latestStage(msg: AssistantMessage | null): string {
    if (!msg || !msg.status.length) return ''
    const last = msg.status[msg.status.length - 1]
    return last.label
  }

  return {
    sessionId,
    messages,
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
    showToast,
    boot,
    toggleFilter,
    setFilter,
    clearFilters,
    clearConversation,
    sendQuestion,
    cancelStream,
    correctEntity,
    addEntityManual,
    latestStage,
  }
})
