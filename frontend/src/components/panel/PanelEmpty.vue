<script setup lang="ts">
defineProps<{ text?: string }>()
</script>

<template>
  <div class="panel-empty">
    <div class="panel-empty-mark" aria-hidden="true">面</div>
    <p>{{ text || '提问后，这里将展示实体卡、图谱子图、时间线与引用证据。' }}</p>
  </div>
</template>
