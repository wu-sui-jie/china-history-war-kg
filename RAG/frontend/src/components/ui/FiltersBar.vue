<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const dynastyOpen = ref(false)
const typeOpen = ref(false)

// 两个弹层互斥 + 点击外部/Escape 关闭：
// 弹层可同时打开时桌面端会互相遮挡，且只能再次点击触发按钮才关闭。
watch(dynastyOpen, (open) => {
  if (open) typeOpen.value = false
})
watch(typeOpen, (open) => {
  if (open) dynastyOpen.value = false
})

function closeAll(): void {
  dynastyOpen.value = false
  typeOpen.value = false
}

function onDocumentClick(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  if (!target || target.closest('.filter-control')) return
  closeAll()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeAll()
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onKeydown)
})

const dynastyText = computed(() =>
  store.filters.dynasty.length ? `${store.filters.dynasty.length} 个朝代` : '全部朝代',
)
const typeText = computed(() =>
  store.filters.event_type.length
    ? `${store.filters.event_type.length} 类战争`
    : '全部战争类型',
)

function toggleDynasty(name: string): void {
  store.toggleFilter('dynasty', name)
}

function toggleType(name: string): void {
  store.toggleFilter('event_type', name)
}
</script>

<template>
  <div class="filters-bar">
    <div class="filter-control">
      <button
        class="filter-trigger"
        type="button"
        :aria-expanded="dynastyOpen"
        @click="dynastyOpen = !dynastyOpen"
      >
        <span class="filter-label">朝代</span>
        <span class="filter-value">{{ dynastyText }}</span>
        <span class="caret" aria-hidden="true"></span>
      </button>
      <div v-if="dynastyOpen" class="filter-pop" data-static>
        <div class="filter-pop-title">朝代筛选（空 = 不过滤）</div>
        <button
          v-for="item in store.dynastyOptions"
          :key="item.standard"
          class="filter-option"
          :class="{ checked: store.filters.dynasty.includes(item.standard) }"
          type="button"
          @click="toggleDynasty(item.standard)"
        >
          <span class="check-box" aria-hidden="true"></span>
          {{ item.standard }}
        </button>
        <p v-if="store.dictError" class="filter-pop-hint">词典加载失败：{{ store.dictError }}</p>
      </div>
    </div>

    <div class="filter-control">
      <button
        class="filter-trigger"
        type="button"
        :aria-expanded="typeOpen"
        @click="typeOpen = !typeOpen"
      >
        <span class="filter-label">战争类型</span>
        <span class="filter-value">{{ typeText }}</span>
        <span class="caret" aria-hidden="true"></span>
      </button>
      <div v-if="typeOpen" class="filter-pop filter-pop-wide" data-static>
        <div class="filter-pop-title">战争类型筛选（空 = 不过滤）</div>
        <button
          v-for="name in store.eventTypeOptions"
          :key="name"
          class="filter-option"
          :class="{ checked: store.filters.event_type.includes(name) }"
          type="button"
          @click="toggleType(name)"
        >
          <span class="check-box" aria-hidden="true"></span>
          {{ name }}
        </button>
        <p v-if="store.dictError" class="filter-pop-hint">词典加载失败：{{ store.dictError }}</p>
      </div>
    </div>

    <button
      v-if="store.filters.dynasty.length || store.filters.event_type.length"
      class="ghost-btn filter-clear"
      type="button"
      @click="store.clearFilters()"
    >
      清除筛选
    </button>
  </div>
</template>
