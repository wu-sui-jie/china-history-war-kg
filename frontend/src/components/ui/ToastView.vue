<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
</script>

<template>
  <!-- 读屏播报（第四轮复核 P1-13）：live region 必须常驻 DOM，
       否则动态插入的节点不会被播报；错误用 alert、其余用 status -->
  <div class="toast-region" aria-live="polite" aria-atomic="true">
    <Transition name="toast">
      <div
        v-if="store.toast"
        class="toast"
        :class="`toast-${store.toast.kind}`"
        :role="store.toast.kind === 'error' ? 'alert' : 'status'"
      >
        {{ store.toast.text }}
      </div>
    </Transition>
  </div>
</template>
