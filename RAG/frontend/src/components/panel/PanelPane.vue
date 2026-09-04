<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import EntityCardsView from '@/components/panel/EntityCardsView.vue'
import EvidenceView from '@/components/panel/EvidenceView.vue'
import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import PlacesView from '@/components/panel/PlacesView.vue'
import SubGraphView from '@/components/panel/SubGraphView.vue'
import TimelineView from '@/components/panel/TimelineView.vue'
import { useSessionStore } from '@/stores/session'
import type { AssistantMessage } from '@/stores/session'

const store = useSessionStore()
const tab = ref<'cards' | 'graph' | 'timeline' | 'places' | 'evidence'>('evidence')

const current = computed<AssistantMessage | null>(() => store.activeMessage)
const lastFinished = computed<AssistantMessage | null>(() => {
  if (current.value) return current.value
  for (let i = store.messages.length - 1; i >= 0; i -= 1) {
    const m = store.messages[i]
    if (m.role === 'assistant' && (m as AssistantMessage).finished) {
      return m as AssistantMessage
    }
  }
  return null
})

const msg = computed(() => current.value || lastFinished.value)

function askFromGraph(question: string): void {
  void store.sendQuestion(question)
}

watch(
  () => msg.value?.id,
  () => {
    tab.value = 'evidence'
  },
)
</script>

<template>
  <div class="panel-pane">
    <header class="panel-head">
      <div>
        <h2>知识面板</h2>
        <p v-if="msg">{{ msg.question }}</p>
        <p v-else>等待提问</p>
      </div>
      <div v-if="msg?.panel" class="panel-count">
        {{ msg.panel.entity_cards.length }} 实体 · {{ msg.panel.subgraph.nodes.length }} 节点
      </div>
    </header>

    <nav class="panel-tabs" aria-label="知识面板视图">
      <button
        type="button"
        :class="{ active: tab === 'evidence' }"
        @click="tab = 'evidence'"
      >
        引用证据
      </button>
      <button type="button" :class="{ active: tab === 'cards' }" @click="tab = 'cards'">
        实体卡
      </button>
      <button type="button" :class="{ active: tab === 'graph' }" @click="tab = 'graph'">
        图谱子图
      </button>
      <button type="button" :class="{ active: tab === 'timeline' }" @click="tab = 'timeline'">
        时间线
      </button>
      <button type="button" :class="{ active: tab === 'places' }" @click="tab = 'places'">
        地点
      </button>
    </nav>

    <div v-if="!msg" class="panel-body">
      <PanelEmpty text="尚未提问。输入问题后，回答的引用与知识面板会显示在这里。" />
    </div>
    <div v-else-if="tab === 'evidence'" class="panel-body">
      <EvidenceView :citations="msg.citations" :conflicts="msg.conflicts" />
    </div>
    <div v-else-if="tab === 'cards'" class="panel-body">
      <EntityCardsView :cards="msg.panel?.entity_cards || msg.entityCards || []" />
    </div>
    <div v-else-if="tab === 'graph'" class="panel-body">
      <SubGraphView
        :graph="msg.panel?.subgraph || { nodes: [], edges: [] }"
        @ask="askFromGraph"
      />
    </div>
    <div v-else-if="tab === 'timeline'" class="panel-body">
      <TimelineView :groups="msg.panel?.timeline?.groups || []" />
    </div>
    <div v-else class="panel-body">
      <PlacesView
        :cards="msg.panel?.entity_cards || []"
        :points="msg.panel?.map_points || []"
      />
    </div>
  </div>
</template>
