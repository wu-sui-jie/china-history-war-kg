<script setup lang="ts">
/**
 * 单条消息：用户提问 / 助手回答。
 * 助手消息含实体识别结果、状态条、流式回答、引用与纠正入口。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, type PropType } from 'vue'

import MarkdownContent from '@/components/chat/MarkdownContent.vue'
import type { AssistantMessage, UserMessage } from '@/stores/session'
import { useSessionStore } from '@/stores/session'
import type {
  CandidateOption,
  EntityCandidate,
  EntityInfo,
} from '@/types/contract'

const props = defineProps({
  message: { type: Object as PropType<UserMessage | AssistantMessage>, required: true },
})

const emit = defineEmits<{ (e: 'citation', index: number): void }>()
const store = useSessionStore()

const isAssistant = computed(() => props.message.role === 'assistant')
const assistant = computed(() =>
  isAssistant.value ? (props.message as AssistantMessage) : null,
)
const user = computed(() => (!isAssistant.value ? (props.message as UserMessage) : null))
const stage = computed(() => (assistant.value ? store.latestStage(assistant.value) : ''))

const latestAssistantId = computed(() => {
  const msgs = store.messages
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    if (msgs[i].role === 'assistant') return msgs[i].id
  }
  return ''
})
// 仅最新一轮（正在流或刚结束的最新答复）可发起“纠正重查”；较早回答的实体 chips 仅供查看。
// 另外要求"有可纠正的内容"：失败/取消/中断且没有正文时只给"重试本轮"，
// 不再显示实体纠正菜单与手工补实体输入框（否则空回答旁会多出一个无意义的输入行）。
const actionable = computed(() => {
  const a = assistant.value
  if (!a) return false
  if (!a.streaming && !a.answer) return false
  const activeId = store.activeMessage?.id
  return activeId ? activeId === a.id : a.id === latestAssistantId.value
})

const openMenus = reactive<Set<string>>(new Set())
const showAddPanel = ref(false)
const manualName = ref('')
const manualType = ref('事件')

function toggleMenu(key: string): void {
  if (!actionable.value) return
  if (openMenus.has(key)) openMenus.delete(key)
  else openMenus.add(key)
}

function closeOutside(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  if (!target || !target.closest('.entity-chip')) openMenus.clear()
}

onMounted(() => document.addEventListener('click', closeOutside))
onBeforeUnmount(() => document.removeEventListener('click', closeOutside))

/** 与某个实体同 mention 的候选组。
 *
 * 纠正菜单只能列"这个 mention 的候选"：旧实现把所有候选组的选项汇总后混在一起，
 * 会把别的 mention（甚至别的实体类型）的同名项塞进替换列表（2026-09-15 审核 P1-11）。
 */
function candidatesFor(entity: EntityInfo): EntityCandidate[] {
  const names = new Set([entity.name, entity.standard_name || entity.name])
  return (assistant.value?.candidates || []).filter((c) => names.has(c.mention))
}

/** 候选的稳定标识（第四轮复核 P1-3）。
 *
 * 同名不同朝代的候选项共享 standard_name：用它当 key/value 会出现重复 key，
 * 且 select 永远回落到第一项，用户选了"西汉"却被替换成"战国"。
 * 有 entity_id 时用 id；旧数据没有 id 时退回"标准名|朝代|类型"组合键。
 */
function optionKey(option: CandidateOption): string {
  if (option.entity_id) return option.entity_id
  return `${option.standard_name}|${option.dynasty || ''}|${option.entity_type || ''}`
}

/** 候选展示文案：同名时用朝代/类型区分，避免用户看到两个一模一样的选项。 */
function optionLabel(option: CandidateOption): string {
  const parts = [option.standard_name]
  if (option.dynasty) parts.push(option.dynasty)
  if (option.entity_type) parts.push(option.entity_type)
  return parts.length > 1 ? `${parts[0]}（${parts.slice(1).join(' · ')}）` : parts[0]
}

/** 该实体可替换的标准候选（含自身，避免误删后无候选）。 */
function entityOptions(entity: EntityInfo): CandidateOption[] {
  const current: CandidateOption = {
    name: entity.name,
    standard_name: entity.standard_name || entity.name,
    confidence: entity.confidence,
    dynasty: entity.dynasty,
    entity_id: entity.entity_id,
    entity_type: entity.type,
  }
  const extra: CandidateOption[] = []
  for (const cand of candidatesFor(entity)) {
    for (const opt of cand.options) {
      if (optionKey(opt) === optionKey(current)) continue
      extra.push({ ...opt })
    }
  }
  const seen = new Set<string>()
  return [current, ...extra].filter((o) => {
    const key = optionKey(o)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function copyAnswer(): void {
  const msg = assistant.value
  if (!msg) return
  void navigator.clipboard.writeText(msg.answer).then(() => {
    store.showToast('info', '回答已复制')
  })
}

function replaceEntity(option: CandidateOption, entity: EntityInfo): void {
  const a = assistant.value
  if (!a || !actionable.value) return
  // 同 ID（或都无 ID 时同名同朝代）视为"没有变化"，不发无效纠正
  const sameId = option.entity_id && entity.entity_id
    ? option.entity_id === entity.entity_id
    : option.standard_name === (entity.standard_name || entity.name)
  if (sameId) return
  store.correctEntity(a, { action: 'replace', option, entity })
  openMenus.clear()
}

function removeEntity(entity: EntityInfo): void {
  const a = assistant.value
  if (!a || !actionable.value) return
  store.correctEntity(a, { action: 'remove', entity })
  openMenus.clear()
}

function chooseCandidateByKey(candidate: EntityCandidate, key: string): void {
  if (!key) return
  const option = candidate.options.find((o) => optionKey(o) === key)
  if (!option) return
  const a = assistant.value
  if (!a || !actionable.value) return
  store.correctEntity(a, { action: 'add', option, candidate })
}

function addManual(): void {
  const a = assistant.value
  if (!a || !actionable.value) return
  const name = manualName.value.trim()
  if (!name) {
    store.showToast('warn', '请输入实体标准名')
    return
  }
  store.addEntityManual(a, name, manualType.value)
  manualName.value = ''
  showAddPanel.value = false
}

function openManual(): void {
  showAddPanel.value = !showAddPanel.value
  openMenus.clear()
}

/** 失败/中断/取消轮的重试入口：沿用原问题、原筛选与原纠正项（P1-20）。 */
function retryTurn(): void {
  const a = assistant.value
  if (!a) return
  store.retryTurn(a)
}

const canRetry = computed(() => {
  const s = assistant.value?.turnStatus
  return s === 'failed' || s === 'interrupted' || s === 'cancelled'
})

const statusText = computed(() => {
  const a = assistant.value
  if (!a) return ''
  switch (a.turnStatus) {
    case 'cancelled':
      return '已取消'
    case 'failed':
      return '生成失败'
    case 'interrupted':
      return '连接中断 · 回答可能不完整'
    case 'refused':
      return '依据不足'
    case 'degraded':
      return '降级生成'
    case 'connecting':
    case 'streaming':
      return '进行中'
    default:
      return a.truncated ? '已生成（触及长度上限）' : '已生成'
  }
})
</script>

<template>
  <article class="msg-row" :class="message.role === 'user' ? 'row-user' : 'row-assistant'">
    <div class="msg-avatar" :class="message.role === 'user' ? 'avatar-user' : 'avatar-ai'">
      {{ message.role === 'user' ? '我' : '史' }}
    </div>
    <div class="msg-body">
      <template v-if="user">
        <div class="msg-user-text">{{ user.question }}</div>
        <div
          v-if="user.filters.dynasty.length || user.filters.event_type.length"
          class="msg-filters"
        >
          <span v-for="d in user.filters.dynasty" :key="'d' + d" class="mini-chip">
            朝代 · {{ d }}
          </span>
          <span v-for="t in user.filters.event_type" :key="'t' + t" class="mini-chip">
            类型 · {{ t }}
          </span>
        </div>
      </template>

      <template v-if="assistant">
        <div v-if="assistant.correctedEntities.length" class="msg-correction-note">
          已按纠正实体重新检索：
          {{ assistant.correctedEntities.map((c) => c.replacement || c.name).filter(Boolean).join('、') }}
        </div>

        <div v-if="assistant.entities.length || assistant.candidates.length" class="entity-strip">
          <span
            v-for="e in assistant.entities"
            :key="(e.entity_id || e.name) + '|' + (e.standard_name || '')"
            class="entity-chip"
            :class="{ 'chip-static': !actionable }"
            :data-open="openMenus.has(e.standard_name || e.name)"
            :role="actionable ? 'button' : 'note'"
            :tabindex="actionable ? 0 : -1"
            :title="
              actionable
                ? `纠正“${e.standard_name || e.name}”`
                : `“${e.standard_name || e.name}”是较早回答的识别结果，仅供查看`
            "
            @click.stop="actionable && toggleMenu(e.standard_name || e.name)"
            @keydown.enter.stop="actionable && toggleMenu(e.standard_name || e.name)"
            @keydown.space.prevent.stop="actionable && toggleMenu(e.standard_name || e.name)"
            :aria-expanded="actionable ? openMenus.has(e.standard_name || e.name) : undefined"
          >
            <span class="entity-dot" :data-type="e.type"></span>
            {{ e.standard_name || e.name }}
            <span class="entity-sub">
              {{ e.type || '' }}{{ e.dynasty ? ' · ' + e.dynasty : '' }}
            </span>
            <span v-if="actionable" class="entity-chev" aria-hidden="true"></span>
            <span
              v-if="openMenus.has(e.standard_name || e.name)"
              class="entity-menu"
              @click.stop
            >
              <span class="entity-menu-title">纠正“{{ e.standard_name || e.name }}”</span>
              <button
                v-for="o in entityOptions(e)"
                :key="optionKey(o)"
                type="button"
                :disabled="
                  o.entity_id && e.entity_id
                    ? o.entity_id === e.entity_id
                    : o.standard_name === (e.standard_name || e.name)
                "
                @click="replaceEntity(o, e)"
              >
                替换为 {{ optionLabel(o) }}
              </button>
              <button type="button" class="danger" @click="removeEntity(e)">
                移除该实体
              </button>
            </span>
          </span>
          <span
            v-for="cand in assistant.candidates"
            :key="cand.mention"
            class="candidate-chip"
            :title="`“${cand.mention}”有 ${cand.options.length} 个同名候选，点击选择`"
          >
            {{ cand.mention }}
            <select
              class="candidate-select"
              :disabled="!actionable"
              :aria-label="`“${cand.mention}”的同名候选（${cand.options.length} 项）`"
              :title="actionable ? '同名候选，点击选择替换' : '较早回答的候选，不可纠正'"
              @change="
                chooseCandidateByKey(cand, ($event.target as HTMLSelectElement).value)
              "
            >
              <option value="">同名 {{ cand.options.length }} 项</option>
              <option v-for="o in cand.options" :key="optionKey(o)" :value="optionKey(o)">
                {{ optionLabel(o) }}
              </option>
            </select>
          </span>
          <button
            v-if="actionable"
            class="ghost-btn entity-add-btn"
            type="button"
            @click="openManual"
          >
            + 新增实体
          </button>
        </div>
        <div v-else-if="actionable" class="manual-add-row manual-add-row-alone">
          <input v-model="manualName" type="text" placeholder="知识库中的实体标准名" />
          <select v-model="manualType">
            <option value="事件">事件</option>
            <option value="人物">人物</option>
            <option value="组织">组织</option>
            <option value="地点">地点</option>
          </select>
          <button class="ghost-btn" type="button" @click="addManual">按实体重查</button>
        </div>

        <div v-if="actionable && assistant.entities.length && showAddPanel" class="manual-add-row">
          <input v-model="manualName" type="text" placeholder="知识库中的实体标准名" />
          <select v-model="manualType">
            <option value="事件">事件</option>
            <option value="人物">人物</option>
            <option value="组织">组织</option>
            <option value="地点">地点</option>
          </select>
          <button class="ghost-btn" type="button" @click="addManual">加入并重查</button>
          <button class="ghost-btn" type="button" @click="showAddPanel = false">取消</button>
        </div>

        <div v-if="assistant.streaming && stage" class="status-strip">
          <span class="spinner" aria-hidden="true"></span>
          正在{{ stage }}
        </div>
        <div v-if="assistant.error" class="msg-error">{{ assistant.error }}</div>
        <div v-if="assistant.partial && assistant.answer" class="msg-partial">
          以上内容可能不完整（生成过程中断了）。
        </div>

        <div v-if="assistant.answer" class="assistant-answer">
          <MarkdownContent
            :text="assistant.answer"
            :citations="assistant.citations"
            :entities="assistant.entities"
            @citation="(n) => emit('citation', n)"
          />
        </div>

        <div v-if="assistant.conflicts.length" class="conflict-banner">
          资料中存在不同说法（{{ assistant.conflicts.length }} 处）
        </div>

        <div v-if="assistant.citations.length" class="answer-citations">
          <button
            v-for="c in assistant.citations"
            :key="c.evidence_id"
            class="cite-chip"
            type="button"
            :title="c.title"
            @click="emit('citation', c.index)"
          >
            [{{ c.index }}] {{ c.title }}
          </button>
        </div>

        <div class="msg-tools">
          <button v-if="assistant.answer" type="button" class="ghost-btn" @click="copyAnswer">
            复制回答
          </button>
          <button v-if="canRetry" type="button" class="ghost-btn" @click="retryTurn">
            重试本轮
          </button>
          <span class="msg-meta">{{ statusText }}</span>
        </div>
      </template>
    </div>
  </article>
</template>
