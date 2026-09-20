<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'

import ChatPane from '@/components/chat/ChatPane.vue'
import HistoryPane from '@/components/history/HistoryPane.vue'
import PanelPane from '@/components/panel/PanelPane.vue'
import FiltersBar from '@/components/ui/FiltersBar.vue'
import ToastView from '@/components/ui/ToastView.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const isNarrow = useMediaQuery('(max-width: 980px)')
// 面板开关放在 store 里（点击引用要能"打开抽屉再定位"，见 store.requestCitation）
const panelOpen = computed({
  get: () => store.panelOpen,
  set: (value: boolean) => {
    store.panelOpen = value
  },
})

// 桌面默认展开面板；移动端收起为抽屉
store.panelOpen = !window.matchMedia('(max-width: 980px)').matches

// 提问历史：桌面是常驻左栏（显隐持久化），移动端是左侧抽屉
const HISTORY_VISIBLE_KEY = 'ragv5-ui-history-visible'

function readHistoryVisible(): boolean {
  try {
    return localStorage.getItem(HISTORY_VISIBLE_KEY) !== '0'
  } catch {
    return true // 隐私模式：默认显示
  }
}

const historyVisible = ref(readHistoryVisible())
const historyOpen = ref(false)

watch(isNarrow, (narrow) => {
  store.panelOpen = !narrow
  historyOpen.value = false
})

function toggleHistory(): void {
  if (isNarrow.value) {
    const next = !historyOpen.value
    historyOpen.value = next
    if (next) store.panelOpen = false // 两个抽屉不同时展开
    return
  }
  historyVisible.value = !historyVisible.value
  try {
    localStorage.setItem(HISTORY_VISIBLE_KEY, historyVisible.value ? '1' : '0')
  } catch {
    // 存储不可用时仅本次会话生效
  }
}

/** 点历史记录：切换面板到该轮；移动端收起历史抽屉，让位给知识面板。 */
function onPickTurn(id: string): void {
  store.selectTurn(id)
  if (isNarrow.value) historyOpen.value = false
}

/** 移动端切知识面板时收起历史抽屉（两个抽屉互斥）。 */
function togglePanelDrawer(): void {
  const next = !store.panelOpen
  store.panelOpen = next
  if (next) historyOpen.value = false
}

function clearAsk(): void {
  if (
    window.confirm('清空本轮会话？历史记录将从本页移除，并新建会话 ID。')
  ) {
    store.clearConversation()
  }
}

// 移动端抽屉的对话框语义（第四轮复核 P1-13）：
// Escape 关闭、打开时把焦点移入抽屉、关闭后焦点回到触发按钮。
const drawerEl = ref<HTMLElement | null>(null)
const panelTriggerEl = ref<HTMLButtonElement | null>(null)
const historyDrawerEl = ref<HTMLElement | null>(null)
const historyTriggerEl = ref<HTMLButtonElement | null>(null)

function closePanel(): void {
  store.panelOpen = false
  void nextTick(() => panelTriggerEl.value?.focus())
}

function closeHistory(): void {
  historyOpen.value = false
  void nextTick(() => historyTriggerEl.value?.focus())
}

/** 真正可 Tab 到的元素：排除 disabled / 隐藏 / aria-hidden / roving tabindex(-1)。
 *
 * 第五轮审核 P1-13：旧实现直接 querySelectorAll('button, ...')，把 tablist 里
 * `:tabindex="tab === item.id ? 0 : -1"` 的**非当前 tab** 也算成"可聚焦"，
 * 于是"最后一个元素"往往是一个根本 Tab 不到的元素，`active === last` 永远不成立，
 * 焦点就从中途逃出抽屉（浏览器实测：第 2 次 Tab 即逃逸）。
 *
 * `tabIndex >= 0` 这一条是关键：`button:not([disabled])` 这类选择器仍会匹配
 * `tabindex="-1"` 的按钮，而浏览器在 Tab 序列里会跳过它们。
 */
function tabbableIn(root: HTMLElement): HTMLElement[] {
  const selector =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  return Array.from(root.querySelectorAll<HTMLElement>(selector)).filter((el) => {
    if (el.tabIndex < 0) return false
    if (el.getAttribute('aria-hidden') === 'true') return false
    const style = window.getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden') return false
    // getClientRects 对 position: fixed 元素同样有效（offsetParent 会误判为 null）
    return el.getClientRects().length > 0
  })
}

/** 抽屉的对话框键盘语义（知识面板 / 提问历史两个抽屉共用）：
 * Escape 关闭、Tab 在抽屉内部循环（避免焦点落到被遮挡的聊天区）。 */
function trapFocus(event: KeyboardEvent, root: HTMLElement | null, close: () => void): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
    return
  }
  if (event.key !== 'Tab') return
  if (!root) return
  const focusables = tabbableIn(root)
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement as HTMLElement | null
  const index = active ? focusables.indexOf(active) : -1
  if (index === -1) {
    // 焦点在抽屉容器自身（tabindex=-1）或已跑到外面：无论方向都拉回抽屉内
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
    return
  }
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

onMounted(() => {
  void store.boot()
})

watch(
  () => isNarrow.value && store.panelOpen,
  (open) => {
    if (!open) return
    void nextTick(() => {
      drawerEl.value
        ?.querySelector<HTMLElement>('button:not([disabled]), [href], input, select, textarea')
        ?.focus()
    })
  },
)

watch(
  () => isNarrow.value && historyOpen.value,
  (open) => {
    if (!open) return
    void nextTick(() => {
      historyDrawerEl.value
        ?.querySelector<HTMLElement>('button:not([disabled]), [href], input, select, textarea')
        ?.focus()
    })
  },
)

onBeforeUnmount(() => {
  store.cancelStream()
})
</script>

<template>
  <div class="qa-shell">
    <header class="qa-topbar">
      <div class="qa-brand">
        <div class="qa-brand-mark" aria-hidden="true">史</div>
        <div class="qa-brand-text">
          <h1>中国历代战争史问答</h1>
          <p>
            <template v-if="store.backendReady">服务在线</template>
            <template v-else>服务未连接</template>
            <template v-if="store.backendVersion"> · {{ store.backendVersion }}</template>
          </p>
        </div>
      </div>

      <FiltersBar />

      <div class="qa-topbar-actions">
        <button
          ref="historyTriggerEl"
          class="ghost-btn"
          type="button"
          :aria-expanded="isNarrow ? historyOpen : historyVisible"
          aria-controls="qa-history-drawer"
          @click="toggleHistory"
        >
          历史记录
        </button>
        <button
          v-if="isNarrow"
          ref="panelTriggerEl"
          class="ghost-btn"
          type="button"
          :aria-expanded="panelOpen"
          aria-controls="qa-panel-drawer"
          @click="togglePanelDrawer"
        >
          {{ panelOpen ? '收起面板' : '知识面板' }}
        </button>
        <button class="ghost-btn" type="button" @click="clearAsk">清空会话</button>
      </div>
    </header>

    <main class="qa-main">
      <aside v-if="!isNarrow && historyVisible" class="qa-history-col" aria-label="提问历史">
        <HistoryPane @pick="onPickTurn" />
      </aside>
      <aside
        v-else-if="isNarrow && historyOpen"
        id="qa-history-drawer"
        ref="historyDrawerEl"
        class="qa-history-col qa-history-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="提问历史"
        @keydown="trapFocus($event, historyDrawerEl, closeHistory)"
      >
        <button class="ghost-btn drawer-close" type="button" @click="closeHistory">
          关闭历史
        </button>
        <HistoryPane @pick="onPickTurn" />
      </aside>
      <div
        v-if="isNarrow && historyOpen"
        class="panel-drawer-mask"
        role="presentation"
        @click="closeHistory"
      ></div>

      <section class="qa-chat-col">
        <ChatPane />
      </section>
      <aside v-if="panelOpen && !isNarrow" class="qa-panel-col">
        <PanelPane />
      </aside>
      <aside
        v-else-if="isNarrow && panelOpen"
        id="qa-panel-drawer"
        ref="drawerEl"
        class="qa-panel-col qa-panel-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="知识面板"
        @keydown="trapFocus($event, drawerEl, closePanel)"
      >
        <button class="ghost-btn drawer-close" type="button" @click="closePanel">
          关闭面板
        </button>
        <PanelPane />
      </aside>
      <div
        v-if="isNarrow && panelOpen"
        class="panel-drawer-mask"
        role="presentation"
        @click="closePanel"
      ></div>
    </main>

    <ToastView />
  </div>
</template>
