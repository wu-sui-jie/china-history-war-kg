<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
</script>

<template>
  <Transition name="toast">
    <div v-if="store.toast" class="toast" :class="`toast-${store.toast.kind}`">
      {{ store.toast.text }}
    </div>
  </Transition>
</template>
