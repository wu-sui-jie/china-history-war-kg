<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import ChatInput from '@/components/chat/ChatInput.vue'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const listEl = ref<HTMLElement | null>(null)

const EXAMPLES = [
  '赤壁之战的主帅是谁？',
  '介绍长平之战。',
  '井陉之战发生在什么时候？',
  '牧野之战与武王伐纣有什么关系？',
]

function scrollToBottom(force = false): void {
  const el = listEl.value
  if (!el) return
  const nearBottom =
    el.scrollHeight - el.scrollTop - el.clientHeight < 260
  if (force || nearBottom) {
    void nextTick(() => {
      el.scrollTop = el.scrollHeight
    })
  }
}

watch(
  () => store.messages.length,
  () => scrollToBottom(true),
)

watch(
  () =>
    store.activeMessage
      ? store.activeMessage.answer.length +
        store.activeMessage.status.length
      : 0,
  () => scrollToBottom(),
)

function openCitation(index: number): void {
  window.dispatchEvent(
    new CustomEvent('rag:citation', {
      detail: { index, openPanel: true },
    }),
  )
}

function ask(question: string): void {
  void store.sendQuestion(question)
}
</script>

<template>
  <div class="chat-pane">
    <div ref="listEl" class="message-list">
      <div v-if="!store.messages.length" class="welcome-box">
        <div class="welcome-mark" aria-hidden="true">史</div>
        <h2>中国历代战争史知识问答</h2>
        <p>可连续追问同一场战争、人物或地点；回答会标注引用，右侧展示图谱与证据。</p>
        <div class="example-list">
          <button
            v-for="q in EXAMPLES"
            :key="q"
            type="button"
            class="example-btn"
            @click="ask(q)"
          >
            {{ q }}
          </button>
        </div>
        <div v-if="store.backendError" class="backend-warn">
          <strong>后端提示：</strong>{{ store.backendError }}
        </div>
      </div>
      <template v-for="m in store.messages" :key="m.id">
        <MessageBubble :message="m" @citation="openCitation" />
      </template>
    </div>
    <ChatInput />
  </div>
</template>
