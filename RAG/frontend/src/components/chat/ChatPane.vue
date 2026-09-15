<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { fetchDemoExamples } from '@/api/demo'
import ChatInput from '@/components/chat/ChatInput.vue'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import { useSessionStore } from '@/stores/session'
import type { DemoExample } from '@/types/contract'

const store = useSessionStore()
const listEl = ref<HTMLElement | null>(null)

// F08 演示示例题：来自后端（由已审核题库生成），前端不再硬编码问题。
// 加载失败时只显示引导文案，不回退到硬编码（旧硬编码里有 2 条未审核题，是 v4 的教训）。
const examples = ref<DemoExample[]>([])
const examplesNote = ref('')
const CAPABILITY_LABEL: Record<string, string> = {
  graph: '图谱',
  text: '文本',
  both: '图谱+文本',
}

/** 按类别分组渲染（关系型 / 背景型 / 时间线型 / 实体介绍…）。 */
const exampleGroups = computed(() => {
  const groups = new Map<string, DemoExample[]>()
  for (const e of examples.value) {
    const key = e.category_label || e.category || '示例'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(e)
  }
  return Array.from(groups, ([label, items]) => ({ label, items }))
})

function capabilityOf(e: DemoExample): string {
  return CAPABILITY_LABEL[e.capability] || ''
}

/** 实测首正文时延（毫秒）→ 展示用秒数；无实测则不显示。 */
function latencyOf(e: DemoExample): string {
  const ms = e.measured?.first_answer_ms
  return typeof ms === 'number' && ms > 0 ? `${(ms / 1000).toFixed(1)}s` : ''
}

onMounted(async () => {
  try {
    const data = await fetchDemoExamples()
    examples.value = data.examples ?? []
    examplesNote.value = data.status === 'ok' && examples.value.length ? '' : '示例题暂不可用'
  } catch {
    examplesNote.value = '示例题加载失败，可直接在下方输入问题'
  }
})

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
        <div v-if="exampleGroups.length" class="example-groups">
          <div v-for="g in exampleGroups" :key="g.label" class="example-group">
            <div class="example-group-label">{{ g.label }}</div>
            <div class="example-list">
              <button
                v-for="e in g.items"
                :key="e.id"
                type="button"
                class="example-btn"
                :title="e.measured?.first_answer_ms ? `实测首段回答约 ${latencyOf(e)}` : ''"
                @click="ask(e.question)"
              >
                {{ e.question }}
                <span v-if="capabilityOf(e)" class="example-tag">{{ capabilityOf(e) }}</span>
                <span v-if="latencyOf(e)" class="example-latency">{{ latencyOf(e) }}</span>
              </button>
            </div>
          </div>
        </div>
        <p v-else-if="examplesNote" class="example-note">{{ examplesNote }}</p>
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
