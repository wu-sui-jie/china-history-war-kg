<!--
  提问输入区（旧问答助手底部）。

  从 views/inference/index.vue 的模板里整块搬出来（第 7 轮 W2），class 名与结构保持不变。
  输入内容用 v-model（modelValue / update:modelValue）双向绑定，页面仍是值的持有者；
  Enter 发送、Shift+Enter 换行的判断收在这里（原先是页面的 handleEnterKey）。
-->
<template>
  <div class="input-container">
    <div class="input-textarea-wrapper">
      <lay-textarea
        v-model="text"
        placeholder="请输入您的问题，系统将结合知识图谱内容进行回答..."
        :autosize="{minRow: 1, maxRow: 2}"
        @keydown.enter="handleEnterKey"
        class="compact-textarea"
      ></lay-textarea>
      <div class="send-button" @click="emit('send')" :class="{ 'disabled': loading || !text.trim() }">
        <lay-icon type="layui-icon-up" size="16px" :color="loading || !text.trim() ? '#ccc' : '#009688'"></lay-icon>
      </div>
    </div>
    <div class="button-group">
      <div class="input-actions">
        <div class="hint">Enter 发送 / Shift + Enter 换行</div>
        <lay-icon
          type="layui-icon-refresh"
          color="#888"
          class="clear-button"
          title="清空输入"
          @click="emit('clear')"
        ></lay-icon>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  modelValue: string
  /** 正在生成回答：此时发送按钮置灰、Enter 不发 */
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'send'): void
  (e: 'clear'): void
}>()

const text = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

// 处理回车键事件：Shift+Enter 换行，单独 Enter 发送
function handleEnterKey(event: KeyboardEvent) {
  // 如果按下了Shift键，则允许换行
  if (event.shiftKey) {
    return;
  }

  // 否则，阻止默认行为并发送消息
  event.preventDefault();
  emit('send')
}
</script>
