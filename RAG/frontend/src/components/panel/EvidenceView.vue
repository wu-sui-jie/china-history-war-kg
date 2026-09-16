<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import { useSessionStore } from '@/stores/session'
import type { Citation, Conflict } from '@/types/contract'

const props = defineProps<{
  citations: Citation[]
  conflicts: Conflict[]
}>()

const store = useSessionStore()

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

/** 定位到某条引用：展开 + 高亮 + 滚动到可视区。 */
function focusCitation(index: number): void {
  if (typeof index !== 'number' || Number.isNaN(index)) return
  active.value = index
  expanded.value.add(index)
  void nextTick(() => {
    const row = listEl.value?.querySelector(`[data-index="${index}"]`)
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

// 引用定位来自 store（而不是 window 事件）：移动端抽屉是条件挂载的，
// 事件先于组件挂载发出就会永久丢失（2026-09-15 审核 P1-10）。
// nonce 保证"连续点击同一条引用"也会重新定位。
watch(
  () => store.citationFocus,
  (focus) => {
    if (focus) focusCitation(focus.index)
  },
  { immediate: true, deep: true },
)
</script>

<template>
  <div v-if="indexes.length" ref="listEl" class="evidence-list">
    <!-- 展开/收起用原生 button（第四轮复核 P1-13）：旧实现是点击 article，
         键盘与读屏都拿不到"可展开、当前是否展开"的信息 -->
    <article
      v-for="i in indexes"
      :key="i"
      :data-index="i"
      class="evidence-row"
      :class="{ active: active === i }"
    >
      <h4 class="evidence-heading">
        <button
          type="button"
          class="evidence-head"
          :aria-expanded="expanded.has(i)"
          :aria-controls="`evidence-body-${i}`"
          @click="toggle(i)"
        >
          <span class="cite-no">[{{ i }}]</span>
          <span class="cite-kind">{{ KIND_LABEL[byIndex[i]?.kind || ''] || '证据' }}</span>
          <span class="evidence-title">{{ byIndex[i]?.title || '引用' }}</span>
          <span class="cite-arrow" aria-hidden="true"></span>
        </button>
      </h4>
      <div v-if="expanded.has(i)" :id="`evidence-body-${i}`" class="evidence-body">
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
