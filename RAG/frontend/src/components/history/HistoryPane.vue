<script setup lang="ts">
/**
 * 提问历史侧栏（桌面常驻左栏；移动端作为左侧抽屉内容）。
 *
 * 两级结构：
 * - 上层「会话」：会话索引（标题/时间/条数）+ 新建/切换/重命名/删除，可折叠；
 * - 下层「提问历史」：当前会话的轮次列表，点条目回看该轮面板。
 * 轮次是会话内的维度，两者共存而不是二选一。
 *
 * 数据来自 store.turnHistory（由 messages 派生）：每轮问答一条，含失败/取消/
 * 被重查取代的轮次——回看某一轮的知识面板不应被可用性过滤挡住。
 * 点击条目由外层（App）决定后续动作（切换轮次、移动端收起抽屉）。
 */
import { computed, ref } from 'vue'

import { useSessionStore, type HistoryEntry } from '@/stores/session'

const emit = defineEmits<{
  (e: 'pick', id: string): void
  /** 切换/新建/删除会话：外层据此收起移动端抽屉 */
  (e: 'session-change'): void
}>()
const store = useSessionStore()

// 会话列表折叠（默认展开；状态按用户偏好持久化）
const SESSIONS_OPEN_KEY = 'ragv5-ui-sessions-open'

function readSessionsOpen(): boolean {
  try {
    return localStorage.getItem(SESSIONS_OPEN_KEY) !== '0'
  } catch {
    return true // 隐私模式：默认展开
  }
}

const sessionsOpen = ref(readSessionsOpen())

function toggleSessions(): void {
  sessionsOpen.value = !sessionsOpen.value
  try {
    localStorage.setItem(SESSIONS_OPEN_KEY, sessionsOpen.value ? '1' : '0')
  } catch {
    // 存储不可用时仅本次会话生效
  }
}

/** 当前高亮条目：手选的历史轮优先，未手选时即最新一轮。 */
const activeId = computed(() => store.selectedTurn?.id || store.latestTurn?.id || '')

function clockText(at: number): string {
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 会话时间：今天只显示时刻，更早显示"月-日 时刻"。 */
function sessionTime(at: number): string {
  const d = new Date(at)
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  return sameDay
    ? clockText(at)
    : `${d.getMonth() + 1}-${d.getDate()} ${clockText(at)}`
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

// ---- 会话操作 ----
function pickSession(id: string): void {
  if (id === store.sessionId) return
  store.switchSession(id)
  emit('session-change')
}

function createSession(): void {
  store.createSession()
  emit('session-change')
}

function renameSession(id: string, current: string): void {
  const next = window.prompt('重命名会话', current)
  if (next === null) return
  store.renameSession(id, next)
}

function removeSession(id: string, title: string): void {
  if (!window.confirm(`删除会话「${title}」？该会话的消息会从本机移除，且不可恢复。`)) return
  store.deleteSession(id)
  emit('session-change')
}
</script>

<template>
  <div class="history-pane">
    <section class="session-block" aria-label="会话列表">
      <header class="session-head">
        <button
          class="session-toggle"
          type="button"
          :aria-expanded="sessionsOpen"
          aria-controls="session-list"
          @click="toggleSessions"
        >
          <span class="session-caret" aria-hidden="true">{{ sessionsOpen ? '▾' : '▸' }}</span>
          会话（{{ store.sessionList.length }}）
        </button>
        <button class="ghost-btn session-new" type="button" @click="createSession">
          新建
        </button>
      </header>
      <nav v-if="sessionsOpen" id="session-list" class="session-list" aria-label="会话">
        <div
          v-for="s in store.sessionList"
          :key="s.id"
          class="session-item"
          :class="{ active: s.active }"
        >
          <button
            class="session-pick"
            type="button"
            :aria-current="s.active ? 'true' : undefined"
            :title="s.title"
            @click="pickSession(s.id)"
          >
            <span class="session-title">{{ s.title }}</span>
            <span class="session-time">
              {{ sessionTime(s.updatedAt) }} · {{ s.messageCount }} 条
            </span>
          </button>
          <span class="session-actions">
            <button
              class="session-action"
              type="button"
              :aria-label="`重命名会话「${s.title}」`"
              @click.stop="renameSession(s.id, s.title)"
            >
              改名
            </button>
            <button
              class="session-action danger"
              type="button"
              :aria-label="`删除会话「${s.title}」`"
              @click.stop="removeSession(s.id, s.title)"
            >
              删除
            </button>
          </span>
        </div>
      </nav>
    </section>

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
