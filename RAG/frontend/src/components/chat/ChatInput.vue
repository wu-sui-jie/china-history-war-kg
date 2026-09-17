<script setup lang="ts">
import { nextTick, ref } from 'vue'

import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const text = ref('')
const inputEl = ref<HTMLTextAreaElement | null>(null)

function focusInput(): void {
  inputEl.value?.focus()
}

defineExpose({ focusInput })

async function submit(): Promise<void> {
  const question = text.value.trim()
  if (!question) return
  if (store.activeMessage) store.cancelStream()
  text.value = ''
  await store.sendQuestion(question)
  await nextTick()
  autoResize()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void submit()
  }
}

function autoResize(): void {
  if (!inputEl.value) return
  inputEl.value.style.height = 'auto'
  inputEl.value.style.height = `${Math.min(inputEl.value.scrollHeight, 160)}px`
}
</script>

<template>
  <div class="chat-input-wrap">
    <label class="sr-only" for="chat-question-input">输入历史战争相关问题</label>
    <textarea
      id="chat-question-input"
      ref="inputEl"
      v-model="text"
      class="chat-input"
      rows="1"
      placeholder="输入历史战争相关问题，例如：赤壁之战的主帅是谁？"
      @input="autoResize"
      @keydown="onKeydown"
    ></textarea>
    <button
      v-if="store.activeMessage"
      class="chat-action stop"
      type="button"
      title="停止本次回答"
      @click="store.cancelStream"
    >
      <span class="stop-square" aria-hidden="true"></span>
      停止
    </button>
    <button
      v-else
      class="chat-action send"
      type="button"
      :disabled="!text.trim()"
      title="发送（Enter）"
      @click="submit"
    >
      <span class="send-arrow" aria-hidden="true"></span>
      发送
    </button>
  </div>
</template>
