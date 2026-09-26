<script setup lang="ts">
import { computed, nextTick, watch } from 'vue'

import EntityCardsView from '@/components/panel/EntityCardsView.vue'
import EvidenceView from '@/components/panel/EvidenceView.vue'
import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import PlacesView from '@/components/panel/PlacesView.vue'
import SubGraphView from '@/components/panel/SubGraphView.vue'
import TimelineView from '@/components/panel/TimelineView.vue'
import { useSessionStore, type PanelTab } from '@/stores/session'
import type { AssistantMessage } from '@/stores/session'

const store = useSessionStore()
const tab = computed({
  get: () => store.panelTab,
  set: (value: PanelTab) => store.setPanelTab(value),
})

const TABS: Array<{ id: PanelTab; label: string }> = [
  { id: 'evidence', label: '引用证据' },
  { id: 'cards', label: '实体卡' },
  { id: 'graph', label: '图谱子图' },
  { id: 'timeline', label: '时间线' },
  { id: 'places', label: '地点' },
]

/** 左右方向键在 tabs 之间移动焦点并切换视图（标准 tablist 键盘模型）。 */
function focusTab(step: number): void {
  const idx = TABS.findIndex((t) => t.id === tab.value)
  const next = TABS[(idx + step + TABS.length) % TABS.length]
  tab.value = next.id
  void nextTick(() => {
    document.getElementById(`panel-tab-${next.id}`)?.focus()
  })
}

const msg = computed<AssistantMessage | null>(() => store.panelMessage)

/** 选中轮次的时间（历史提示条用）。 */
const historyTime = computed(() => {
  const at = msg.value?.createdAt
  if (!at) return ''
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

/** 面板生命周期空状态：
 * "还没提问 / 正在识别实体 / 正在检索 / 正在生成 / 已完成但该 tab 无数据 / 失败 / 已取消"要分开说，
 * 统一显示"暂无数据"时用户分不清是没数据还是还在跑。
 * cancelled 不能归进 ready，否则"本轮已取消"的文案永远不可达。 */
type PanelPhase = 'idle' | 'running' | 'cancelled' | 'failed' | 'ready'
const phase = computed<PanelPhase>(() => {
  const m = msg.value
  if (!m) return 'idle'
  if (m.turnStatus === 'connecting' || m.turnStatus === 'streaming') return 'running'
  if (m.turnStatus === 'cancelled') return 'cancelled'
  if (m.turnStatus === 'failed' || m.turnStatus === 'interrupted') return 'failed'
  return 'ready'
})

const progressText = computed(() => {
  const m = msg.value
  if (!m) return ''
  const stage = store.latestStage(m)
  return stage ? `正在${stage}…` : '正在处理…'
})

const hasFilter = computed(() => {
  const f = msg.value?.requestFilters
  return !!f && (f.dynasty.length > 0 || f.event_type.length > 0)
})

const emptyText = computed(() => {
  switch (phase.value) {
    case 'idle':
      return '尚未提问。输入问题后，回答的引用与知识面板会显示在这里。'
    case 'running':
      return `${progressText.value}证据与图谱会边生成边出现。`
    case 'cancelled':
      return '本轮已取消，没有可展示的证据。可在回答处点击"重试本轮"。'
    case 'failed':
      return msg.value?.turnStatus === 'interrupted'
        ? '本轮连接中断，证据可能不完整。可在回答处点击"重试本轮"。'
        : '本轮未正常完成，没有可展示的证据。可在回答处点击"重试本轮"。'
    default:
      return hasFilter.value
        ? '当前筛选条件下没有可展示的内容（筛选为硬过滤，可尝试清除朝代/类型筛选）。'
        : '本轮回答没有额外的实体卡、图谱或时间线内容。'
  }
})

function askFromGraph(question: string): void {
  void store.sendQuestion(question)
}

watch(
  () => msg.value?.id,
  () => {
    // 新的轮次默认回到证据 tab，避免停留在上一轮的视图
    store.setPanelTab('evidence')
  },
)
</script>

<template>
  <div class="panel-pane">
    <header class="panel-head">
      <div>
        <h2>知识面板</h2>
        <p v-if="msg?.question">{{ msg.question }}</p>
        <p v-else>等待提问</p>
      </div>
      <div v-if="msg?.panel" class="panel-count">
        {{ msg.panel.entity_cards.length }} 实体 · {{ msg.panel.subgraph.nodes.length }} 节点
      </div>
    </header>

    <!-- 历史轮次提示：让用户知道面板不是最新一轮，并提供一键返回 -->
    <div v-if="store.isViewingHistory" class="panel-history-bar">
      <span>
        正在查看历史轮次<template v-if="historyTime">（{{ historyTime }} 的提问）</template>
      </span>
      <button class="ghost-btn" type="button" @click="store.returnToLatest()">返回最新</button>
    </div>

    <!-- 标准 tabs 语义：role=tablist/tab + aria-selected/aria-controls，
         键盘用户与读屏可以知道"当前在哪个视图、有哪些视图" -->
    <div class="panel-tabs" role="tablist" aria-label="知识面板视图">
      <button
        v-for="item in TABS"
        :id="`panel-tab-${item.id}`"
        :key="item.id"
        type="button"
        role="tab"
        :aria-selected="tab === item.id"
        :aria-controls="`panel-tabpanel-${item.id}`"
        :tabindex="tab === item.id ? 0 : -1"
        :class="{ active: tab === item.id }"
        @click="tab = item.id"
        @keydown.arrow-right.prevent="focusTab(1)"
        @keydown.arrow-left.prevent="focusTab(-1)"
      >
        {{ item.label }}
      </button>
    </div>

    <div
      v-if="!msg || (phase === 'running' && !msg.citations.length)"
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-empty"
      aria-live="polite"
    >
      <PanelEmpty :text="emptyText" />
    </div>
    <div
      v-else-if="tab === 'evidence'"
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-evidence"
      :aria-labelledby="`panel-tab-evidence`"
    >
      <!-- 没有引用时也要走"生命周期文案"（取消/失败/检索中），
           否则 EvidenceView 自己的"暂无引用证据"会盖住更有信息量的状态说明 -->
      <EvidenceView v-if="msg.citations.length" :citations="msg.citations"
                    :conflicts="msg.conflicts" />
      <PanelEmpty v-else :text="emptyText" />
    </div>
    <div
      v-else-if="tab === 'cards'"
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-cards"
      :aria-labelledby="`panel-tab-cards`"
    >
      <EntityCardsView
        v-if="(msg.panel?.entity_cards || msg.entityCards || []).length"
        :cards="msg.panel?.entity_cards || msg.entityCards || []"
      />
      <PanelEmpty v-else :text="emptyText" />
    </div>
    <div
      v-else-if="tab === 'graph'"
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-graph"
      :aria-labelledby="`panel-tab-graph`"
    >
      <SubGraphView
        v-if="msg.panel?.subgraph?.nodes?.length"
        :graph="msg.panel.subgraph"
        @ask="askFromGraph"
      />
      <PanelEmpty v-else :text="emptyText" />
    </div>
    <div
      v-else-if="tab === 'timeline'"
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-timeline"
      :aria-labelledby="`panel-tab-timeline`"
    >
      <TimelineView
        v-if="msg.panel?.timeline?.groups?.length"
        :groups="msg.panel.timeline.groups"
      />
      <PanelEmpty v-else :text="emptyText" />
    </div>
    <div
      v-else
      class="panel-body"
      role="tabpanel"
      id="panel-tabpanel-places"
      aria-labelledby="panel-tab-places"
    >
      <PlacesView
        v-if="(msg.panel?.map_points || []).length || (msg.panel?.entity_cards || []).length"
        :cards="msg.panel?.entity_cards || []"
        :points="msg.panel?.map_points || []"
      />
      <PanelEmpty v-else :text="emptyText" />
    </div>
  </div>
</template>
