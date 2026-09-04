<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import type { Citation, Conflict } from '@/types/contract'

const props = defineProps<{
  citations: Citation[]
  conflicts: Conflict[]
}>()

const active = ref<number | null>(null)
const expanded = ref<Set<number>>(new Set())
const listEl = ref<HTMLElement | null>(null)

const KIND_LABEL: Record<string, string> = {
  graph_triple: '图谱',
  raw_text: '原文',
  event_card: '事件卡',
  evidence: '关系证据',
}

const indexes = computed<number[]>(() => {
  const seen = new Set<number>()
  return props.citations
    .map((c) => c.index)
    .filter((i) => {
      if (seen.has(i)) return false
      seen.add(i)
      return true
    })
    .sort((a, b) => a - b)
})

const byIndex = computed<Record<number, Citation>>(() => {
  const map: Record<number, Citation> = {}
  for (const c of props.citations) map[c.index] = c
  return map
})

function onCitationEvent(event: Event): void {
  const detail = (event as CustomEvent<{ index: number }>).detail
  if (!detail || typeof detail.index !== 'number') return
  active.value = detail.index
  expanded.value.add(detail.index)
  void nextTick(() => {
    const row = listEl.value?.querySelector(`[data-index="${detail.index}"]`)
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}

function conflictFor(index: number): Conflict | undefined {
  const evidenceId = byIndex.value[index]?.evidence_id
  if (!evidenceId) return undefined
  return props.conflicts.find((c) => c.evidence_ids.includes(evidenceId))
}

function toggle(i: number): void {
  if (expanded.value.has(i)) expanded.value.delete(i)
  else expanded.value.add(i)
}

onMounted(() => window.addEventListener('rag:citation', onCitationEvent))
onBeforeUnmount(() => window.removeEventListener('rag:citation', onCitationEvent))
</script>

<template>
  <div v-if="indexes.length" ref="listEl" class="evidence-list">
    <article
      v-for="i in indexes"
      :key="i"
      :data-index="i"
      class="evidence-row"
      :class="{ active: active === i }"
      @click="toggle(i)"
    >
      <header class="evidence-head">
        <span class="cite-no">[{{ i }}]</span>
        <span class="cite-kind">{{ KIND_LABEL[byIndex[i]?.kind || ''] || '证据' }}</span>
        <h4>{{ byIndex[i]?.title || '引用' }}</h4>
        <span class="cite-arrow" aria-hidden="true"></span>
      </header>
      <div v-if="expanded.has(i)" class="evidence-body">
        <p>{{ byIndex[i]?.snippet || '（图谱类引用无原文片段，以标题为准）' }}</p>
        <div v-if="conflictFor(i)" class="conflict-inline">
          与{{ conflictFor(i)?.description || '其他资料' }}存在不同说法
        </div>
        <span class="evidence-id">evidence: {{ byIndex[i]?.evidence_id }}</span>
      </div>
    </article>
  </div>
  <PanelEmpty v-else text="回答暂无可展开的引用证据。" />
</template>
