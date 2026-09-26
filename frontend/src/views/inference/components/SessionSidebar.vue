<!--
  会话列表（旧问答助手左侧栏）。

  class 名与结构沿用 views/inference/index.vue 的页面级样式，不要改动——
  样式在页面级的 index.css 里按 `.inference-container` 命名空间生效，子组件只负责渲染与派发事件。
-->
<template>
  <div class="chat-sidebar" :class="{ 'sidebar-collapsed': collapsed }">
    <div class="sidebar-header">
      <button type="button" class="new-chat-button" @mousedown.stop @click.stop="emit('create')">
        新建聊天
      </button>
    </div>
    <div class="chat-history">
      <div
        v-for="(chat, index) in sessions"
        :key="index"
        class="chat-item"
        :class="{ 'active': activeIndex === index }"
      >
        <div class="chat-item-content" @click="emit('select', index)">
          <div class="chat-title">{{ chat.title || '新对话' }}</div>
          <div class="chat-time">{{ formatChatTime(chat.lastTime) }}</div>
        </div>
        <div class="chat-actions">
          <lay-icon
            type="layui-icon-delete"
            color="#ff5722"
            @click.stop="emit('remove', index)"
            class="delete-icon"
          ></lay-icon>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { formatChatTime } from '@/utils/date'
import type { ChatSession } from '@/types/inference'

defineProps<{
  sessions: ChatSession[]
  /** 当前选中的会话下标（与 chatHistory 数组下标一致） */
  activeIndex: number
  /** 折叠态：收起时只留新建按钮 */
  collapsed: boolean
}>()

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'select', index: number): void
  (e: 'remove', index: number): void
}>()
</script>
