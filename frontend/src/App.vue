<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'

import ChatPane from '@/components/chat/ChatPane.vue'
import PanelPane from '@/components/panel/PanelPane.vue'
import FiltersBar from '@/components/ui/FiltersBar.vue'
import ToastView from '@/components/ui/ToastView.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const isNarrow = useMediaQuery('(max-width: 980px)')
// 桌面默认展开面板；移动端面板作为抽屉由按钮开关
const panelOpen = ref(!window.matchMedia('(max-width: 980px)').matches)

watch(isNarrow, (narrow) => {
  panelOpen.value = !narrow
})

function clearAsk(): void {
  if (
    window.confirm('清空本轮会话？历史记录将从本页移除，并新建会话 ID。')
  ) {
    store.clearConversation()
  }
}

onMounted(() => {
  void store.boot()
})

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
        <button v-if="isNarrow" class="ghost-btn" type="button" @click="panelOpen = !panelOpen">
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
      <aside v-else-if="isNarrow && panelOpen" class="qa-panel-col qa-panel-drawer">
        <PanelPane />
      </aside>
      <div
        v-if="isNarrow && panelOpen"
        class="panel-drawer-mask"
        role="presentation"
        @click="panelOpen = false"
      ></div>
    </main>

    <ToastView />
  </div>
</template>
