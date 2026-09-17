<script setup lang="ts">
import type { TimelineGroup } from '@/types/contract'

defineProps<{ groups: TimelineGroup[] }>()
</script>

<template>
  <div v-if="groups.length" class="timeline-wrap">
    <section v-for="group in groups" :key="group.label" class="timeline-group">
      <h4>{{ group.label }}</h4>
      <ol v-if="group.items.length" class="timeline-list">
        <li v-for="item in group.items" :key="item.event_id" class="timeline-item">
          <span class="timeline-dot" aria-hidden="true"></span>
          <div>
            <div class="timeline-name">{{ item.name }}</div>
            <div v-if="item.start_date || item.dynasty" class="timeline-date">
              {{ item.start_date || item.dynasty || '' }}
            </div>
          </div>
        </li>
      </ol>
      <p v-else class="timeline-empty">本组暂无条目</p>
    </section>
  </div>
  <p v-else class="soft-empty">当前问题暂无时间线数据。</p>
</template>
