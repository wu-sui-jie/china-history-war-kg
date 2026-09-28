<script setup lang="ts">
/**
 * 提问历史侧栏（桌面常驻左栏；移动端作为左侧抽屉内容）。
 *
 * 两级结构：
 * - 上层「会话」：会话索引（标题/时间/条数）+ 新建/切换/重命名/删除，可折叠；
 *   改名与删除用页内控件（内联输入框 + 二次确认），不用 window.prompt / window.confirm
 *   ——嵌入主应用的 iframe 没开 allow-modals，原生弹窗在那里会被静默忽略（见 startRename 注释）；
 * - 下层「提问历史」：当前会话的轮次列表，点条目回看该轮面板。
 * 轮次是会话内的维度，两者共存而不是二选一。
 *
 * 数据来自 store.turnHistory（由 messages 派生）：每轮问答一条，含失败/取消/
 * 被重查取代的轮次——回看某一轮的知识面板不应被可用性过滤挡住。
 * 点击条目由外层（App）决定后续动作（切换轮次、移动端收起抽屉）。
 */
import { computed, ref } from 'vue'
import type { Directive } from 'vue'

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
/** 页内单行控件（改名输入 / 删除确认），同一时刻只允许一处。 */
const inline = ref<{ id: string; mode: 'rename' | 'remove' } | null>(null)
const draftTitle = ref('')

/** 挂载即聚焦并全选（改名输入框）：原生 autofocus 对动态插入的元素不可靠。
 *
 * 用指令而不是"共享 ref + nextTick"：v-for 里新元素挂载与旧元素卸载的先后顺序
 * 由列表位置决定，共享 ref 会被后发生的卸载覆盖成 null，聚焦就落空。
 * 指令各自在挂载时生效，互不干扰。
 */
const vFocusSelect: Directive<HTMLInputElement> = {
  mounted(el) {
    el.focus()
    el.select()      // 预选原标题：改名多为重打，直接覆盖
  },
}

/** 挂载即聚焦（不选中），用于删除确认里的「取消」。 */
const vFocus: Directive<HTMLElement> = {
  mounted(el) {
    el.focus()
  },
}

function closeInline(): void {
  inline.value = null
  draftTitle.value = ''
}

function pickSession(id: string): void {
  if (id === store.sessionId) return
  closeInline()          // 条目换了，页内的改名输入/删除确认不该跟着漂过去
  store.switchSession(id)
  emit('session-change')
}

function createSession(): void {
  closeInline()
  store.createSession()
  emit('session-change')
}

/** 改名与删除的确认都做在页面内，不用 window.prompt / window.confirm。
 *
 * 本页的常规入口是以 iframe 嵌入主应用（frontend/src/views/knowledge/RagAssistant.vue），
 * 那个 iframe 的 sandbox 只开 allow-scripts/allow-same-origin/allow-forms/allow-downloads、
 * 没开 allow-modals：按 HTML 规范，confirm 一律返回 false、prompt 一律返回 null，
 * 原生弹窗静默失效——表现为"改名、删除点了没反应"，而"新建"照常能用。
 * 把输入与确认放进页面后，嵌入形态与独立打开（在新窗口打开）行为一致。
 */
function startRename(id: string, current: string): void {
  inline.value = { id, mode: 'rename' }
  draftTitle.value = current
}

function commitRename(id: string): void {
  if (inline.value?.id !== id || inline.value.mode !== 'rename') return
  store.renameSession(id, draftTitle.value)   // 空标题由 store 回退默认名
  closeInline()
}

function startRemove(id: string): void {
  inline.value = { id, mode: 'remove' }
  // 焦点原本在已被换掉的「删除」按钮上，不接管就会落到 body、键盘用户要重新 Tab 一圈；
  // 靠 v-focus 落在「取消」而不是「确认删除」：破坏性操作不该由一次回车完成。
}

function confirmRemove(id: string): void {
  if (inline.value?.id !== id || inline.value.mode !== 'remove') return
  closeInline()
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
          <!-- 改名态：输入框 + 保存/取消（Enter 提交、Esc 取消） -->
          <form
            v-if="inline?.id === s.id && inline.mode === 'rename'"
            class="session-inline"
            @submit.prevent="commitRename(s.id)"
          >
            <input
              v-focus-select
              v-model="draftTitle"
              class="session-edit-input"
              type="text"
              maxlength="60"
              :aria-label="`重命名会话「${s.title}」`"
              @keydown.esc="closeInline"
            />
            <span class="session-inline-actions">
              <button class="session-action" type="submit">保存</button>
              <button class="session-action" type="button" @click="closeInline">取消</button>
            </span>
          </form>

          <!-- 删除态：二次确认做在页面内（原生 confirm 在嵌入 iframe 里被 sandbox 吞掉） -->
          <div
            v-else-if="inline?.id === s.id && inline.mode === 'remove'"
            class="session-inline"
          >
            <p class="session-confirm-text">
              删除「{{ s.title }}」？消息会从本机移除，不可恢复。
            </p>
            <span class="session-inline-actions">
              <button
                v-focus
                class="session-action"
                type="button"
                @click="closeInline"
              >
                取消
              </button>
              <button
                class="session-action danger"
                type="button"
                @click="confirmRemove(s.id)"
              >
                确认删除
              </button>
            </span>
          </div>

          <template v-else>
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
                @click.stop="startRename(s.id, s.title)"
              >
                改名
              </button>
              <button
                class="session-action danger"
                type="button"
                :aria-label="`删除会话「${s.title}」`"
                @click.stop="startRemove(s.id)"
              >
                删除
              </button>
            </span>
          </template>
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
