<script setup lang="ts">
/**
 * 提问历史侧栏（桌面常驻左栏；移动端作为左侧抽屉内容）。
 *
 * 数据来自 store.turnHistory（由 messages 派生）：每轮问答一条，含失败/取消/
 * 被重查取代的轮次——回看某一轮的知识面板不应被可用性过滤挡住。
 * 点击条目由外层（App）决定后续动作（切换轮次、移动端收起抽屉）。
 */
import { computed } from 'vue'

import { useSessionStore, type HistoryEntry } from '@/stores/session'

const emit = defineEmits<{ (e: 'pick', id: string): void }>()
const store = useSessionStore()

/** 当前高亮条目：手选的历史轮优先，未手选时即最新一轮。 */
const activeId = computed(() => store.selectedTurn?.id || store.latestTurn?.id || '')

function clockText(at: number): string {
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 状态徽标：被重查取代优先标注（它同时可能是 completed / cancelled）。 */
function statusOf(entry: HistoryEntry): { key: string; text: string } {
  if (entry.superseded) return { key: 'superseded', text: '已被重查取代' }
  switch (entry.turnStatus) {
    case 'connecting':
    case 'streaming':
      return { key: 'live', text: '进行中' }
    case 'cancelled':
      return { key: 'cancelled', text: '已取消' }
    case 'failed':
      return { key: 'failed', text: '生成失败' }
    case 'interrupted':
      return { key: 'interrupted', text: '连接中断' }
    case 'refused':
      return { key: 'refused', text: '依据不足' }
    case 'degraded':
      return { key: 'degraded', text: '降级生成' }
    default:
      return { key: 'completed', text: '已生成' }
  }
}

function itemTitle(entry: HistoryEntry): string {
  const question = entry.question || '（空问题）'
  return entry.hasPanel ? question : `${question}（该轮无面板数据）`
}

function pick(entry: HistoryEntry): void {
  emit('pick', entry.id)
}
</script>

<template>
  <div class="history-pane">
    <header class="history-head">
      <h2>提问历史</h2>
      <span v-if="store.turnHistory.length" class="history-count">
        {{ store.turnHistory.length }} 轮
      </span>
    </header>

    <nav v-if="store.turnHistory.length" class="history-list" aria-label="提问历史记录">
      <button
        v-for="entry in store.turnHistory"
        :key="entry.id"
        type="button"
        class="history-item"
        :class="{ active: entry.id === activeId }"
        :aria-current="entry.id === activeId ? 'true' : undefined"
        :title="itemTitle(entry)"
        @click="pick(entry)"
      >
        <span class="history-meta">
          <span class="history-index">第 {{ entry.index }} 轮</span>
          <span class="history-time">{{ clockText(entry.createdAt) }}</span>
          <span class="history-status" :data-status="statusOf(entry).key">
            {{ statusOf(entry).text }}
          </span>
        </span>
        <span class="history-question">{{ entry.question || '（空问题）' }}</span>
      </button>
    </nav>

    <p v-else class="history-empty">
      还没有提问记录。<br />输入问题后，每一轮问答都会列在这里，可随时点回查看。
    </p>
  </div>
</template>
