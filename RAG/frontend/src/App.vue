<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'

import ChatPane from '@/components/chat/ChatPane.vue'
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
watch(isNarrow, (narrow) => {
  store.panelOpen = !narrow
})

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

function closePanel(): void {
  store.panelOpen = false
  void nextTick(() => panelTriggerEl.value?.focus())
}

function onDrawerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    closePanel()
    return
  }
  if (event.key !== 'Tab') return
  // 焦点陷阱：Tab 在抽屉内部循环，避免焦点落到被遮挡的聊天区
  const root = drawerEl.value
  if (!root) return
  const focusables = root.querySelectorAll<HTMLElement>(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (event.shiftKey && (active === first || !root.contains(active))) {
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
        ?.querySelector<HTMLElement>('button, [href], input, select, textarea')
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
          v-if="isNarrow"
          ref="panelTriggerEl"
          class="ghost-btn"
          type="button"
          :aria-expanded="panelOpen"
          aria-controls="qa-panel-drawer"
          @click="panelOpen = !panelOpen"
        >
          {{ panelOpen ? '收起面板' : '知识面板' }}
        </button>
        <button class="ghost-btn" type="button" @click="clearAsk">清空会话</button>
      </div>
    </header>

    <main class="qa-main">
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
        @keydown="onDrawerKeydown"
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
