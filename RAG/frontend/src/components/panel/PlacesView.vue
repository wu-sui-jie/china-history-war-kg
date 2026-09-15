<script setup lang="ts">
import { computed } from 'vue'

import MapView from '@/components/panel/MapView.vue'
import type { EntityCard, MapPoint } from '@/types/contract'

const props = defineProps<{ cards: EntityCard[]; points: MapPoint[] }>()

const hasMapPoints = computed(
  () => (props.points || []).some(
    (p) => typeof p.longitude === 'number' && typeof p.latitude === 'number',
  ),
)

const places = computed(() => {
  const seen = new Set<string>()
  const out: Array<{ name: string; modern?: string; province?: string; city?: string }> = []
  for (const p of props.points) {
    if (!p.name || seen.has(p.name)) continue
    seen.add(p.name)
    out.push({
      name: p.name,
      modern: p.modern_name,
    })
  }
  for (const card of props.cards) {
    if (card.type !== '地点') continue
    if (seen.has(card.name)) continue
    seen.add(card.name)
    out.push({
      name: card.name,
      modern: card.modern_name,
      province: card.province,
      city: card.city,
    })
  }
  return out
})
</script>

<template>
  <div class="places-wrap">
    <MapView v-if="hasMapPoints" :points="points" />
    <div v-if="places.length" class="places-grid">
      <div v-for="place in places" :key="place.name" class="place-card">
        <strong>{{ place.name }}</strong>
        <span v-if="place.modern" class="place-modern">{{ place.modern }}</span>
        <span v-else-if="place.province || place.city" class="place-modern">
          {{ place.province }}{{ place.city }}
        </span>
        <span v-else class="place-fallback">暂无坐标/现代地名</span>
      </div>
    </div>
    <p v-else class="soft-empty">当前问题暂无可展示的地点信息。</p>
  </div>
</template>
