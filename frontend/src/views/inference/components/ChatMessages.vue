<!--
  消息列表（旧问答助手的聊天区）。

  从 views/inference/index.vue 的模板里整块搬出来（第 7 轮 W2），class 名与结构保持不变。
  子组件自己持有"每条消息的展开状态"（图谱可视化、引用信息、实体卡片）——它们是纯展示状态，
  与页面无关；对外只派发 copy / ask / node-click 三类动作。

  滚动也归它管：父页面原先通过 ref 直接改 `.message-container` 的 scrollTop，
  现在改用 defineExpose 暴露的 scrollToBottom / scrollToTop，避免跨组件操作 DOM。
-->
<template>
  <div class="message-container" ref="container">
    <div v-if="isFirstLoad && messages.length === 0" class="empty-chat">
      <div class="empty-icon">
        <lay-icon type="layui-icon-loading" size="60px" color="#dcdcdc"></lay-icon>
      </div>
      <div class="empty-text">正在加载聊天记录...</div>
    </div>
    <div v-else-if="messages.length === 0" class="empty-chat">
      <div class="empty-icon">
        <lay-icon type="layui-icon-dialogue" size="60px" color="#dcdcdc"></lay-icon>
      </div>
      <div class="empty-text">开始新的对话</div>
      <!-- 添加快捷提示 -->
      <div class="quick-prompts">
        <div class="prompt-title">常用提示：</div>
        <div class="prompt-items">
          <div class="prompt-item" v-for="(prompt, idx) in quickPrompts" :key="idx" @click="emit('ask', prompt)">
            {{ prompt }}
          </div>
        </div>
      </div>
    </div>
    <template v-else>
      <div
        v-for="(message, msgIndex) in messages"
        :key="msgIndex"
        class="message-item"
        :class="message.role === 'user' ? 'user-message' : 'ai-message'"
      >
        <div class="message-avatar">
          <lay-avatar v-if="message.role === 'user'">用户</lay-avatar>
          <lay-avatar v-else bg-color="#009688">AI</lay-avatar>
        </div>
        <div class="message-content">
          <!-- 用户消息 -->
          <div class="message-text" v-if="message.role === 'user'">{{ message.content }}</div>

          <!-- AI消息部分 -->
          <template v-else>
            <!-- 思考过程显示 - 移到答案上面 -->
            <div
              v-if="message.thinking && message.thinking.length > 0"
              class="thinking-section"
            >
              <div class="thinking-header">
                <div class="thinking-header-left">
                  <span class="iconfont icon-thinking"></span>
                  <span>思考过程</span>
                </div>
                <div class="thinking-header-right">
                  <div class="thinking-tag">模型思考推理</div>
                </div>
              </div>
              <div class="thinking-content">
                <div v-for="(item, index) in message.thinking" :key="index" v-html="renderThinkingContent(item)"></div>
              </div>
            </div>

            <!-- AI消息使用Markdown渲染 -->
            <div class="message-text markdown-body" v-html="renderMarkdown(message.content)"></div>

            <!-- 知识图谱可视化 -->
            <div v-if="message.role === 'assistant' && message.kgContext" class="kg-visualization" @click.stop>
              <div class="kg-header" @click.stop="toggleKgVisualization(msgIndex, $event)">
                <lay-icon :type="showKgVisualization[msgIndex] ? 'layui-icon-up' : 'layui-icon-down'" color="#009688"></lay-icon>
                <span>{{ showKgVisualization[msgIndex] ? '隐藏知识图谱' : '显示知识图谱' }}</span>
              </div>

              <div v-if="showKgVisualization[msgIndex]" class="kg-graph-wrapper" @click.stop @mousedown.stop @touchstart.stop>
                <div class="kg-graph-container" @click.stop @mousedown.stop @touchstart.stop>
                  <!-- 知识图谱视图，可以通过引入外部图谱库如ECharts完善 -->
                  <div class="kg-graph-placeholder" @click.stop @mousedown.stop @touchstart.stop>
                    <div v-if="getKgData(message).nodes.length === 0" class="kg-empty" @click.stop>
                      <lay-icon type="layui-icon-about" color="#FF9800"></lay-icon>
                      <span>没有检索到相关的知识图谱数据</span>
                    </div>
                    <kg-graph v-else :data="getKgData(message)" class="kg-graph" @node-click="emit('node-click', $event)"></kg-graph>
                  </div>
                </div>
              </div>
            </div>

            <!-- 添加知识图谱未找到信息的提示 -->
            <div v-if="message.role === 'assistant' && message.fromKg === false" class="no-kg-info-notice">
              <lay-icon type="layui-icon-tips" color="#FF9800"></lay-icon>
              <span>知识图谱中未找到相关信息，此为大模型基于通用知识的回答</span>
            </div>
          </template>

          <div class="message-footer">
            <div class="message-actions">
              <lay-icon
                type="layui-icon-file"
                size="14px"
                color="#888"
                class="action-icon"
                title="复制消息"
                @click="emit('copy', message.content)"
              ></lay-icon>
            </div>
            <div class="message-time">{{ formatChatTime(message.time) }}</div>

            <!-- 知识图谱引用信息 -->
            <div v-if="message.role === 'assistant' && message.kgContext" class="kg-reference">
              <lay-button
                type="normal"
                size="sm"
                @click="toggleKgInfo(msgIndex)"
              >
                {{ showKgInfo[msgIndex] ? '隐藏知识图谱信息' : '查看引用图谱信息' }}
              </lay-button>

              <div v-if="showKgInfo[msgIndex]" class="kg-context">
                <lay-line theme="green">知识图谱参考信息</lay-line>
                <div class="ai-notice">
                  <lay-icon type="layui-icon-about" color="#FF9800"></lay-icon>
                  <span>{{ message.fromKg === false ? '知识图谱中未找到相关信息，内容由AI基于通用知识生成' : '内容由AI基于下方知识图谱信息生成，请注意辨别信息的准确性' }}</span>
                </div>
                <div class="kg-content-formatted markdown-body" v-html="renderKgContextMarkdown(message.kgContext)"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
    <div v-if="loading" class="ai-message">
      <div class="message-avatar">
        <lay-avatar bg-color="#009688">AI</lay-avatar>
      </div>
      <div class="message-content thinking-bubble">
        <div class="message-text thinking-animation">思考中<span class="dot-animation"></span></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'

import KgGraph from './KgGraph.vue'
import { formatChatTime } from '@/utils/date'
import { renderKgContextMarkdown, renderMarkdown, renderThinkingContent } from '@/utils/inference-render'
import type { ChatMessage, KgGraphData } from '@/types/inference'

const props = defineProps<{
  messages: ChatMessage[]
  /** 正在生成回答（显示"思考中"气泡） */
  loading: boolean
  /** 首次加载中（显示"正在加载聊天记录"） */
  isFirstLoad: boolean
  /** 空会话时的常用提示 */
  quickPrompts: string[]
}>()

const emit = defineEmits<{
  (e: 'copy', content: string): void
  /** 点快捷提示或实体标签时，把问题交回页面去发 */
  (e: 'ask', question: string): void
  /** 点击图谱节点：页面负责打开详情抽屉 */
  (e: 'node-click', node: Record<string, unknown>): void
  /** 图谱展开/收起（展开时会触发 resize，页面据此做侧边栏保护，见 index.vue 的 onKgToggle） */
  (e: 'kg-toggle', expanded: boolean): void
}>()

const container = ref<HTMLElement | null>(null)

/** 控制知识图谱可视化的显示状态 - 默认展开 */
const showKgVisualization = ref<Record<number, boolean>>({})
const showKgInfo = reactive<Record<number, boolean>>({})

// 监听消息变化，为新消息默认展开知识图谱
watch(() => props.messages.length, () => {
  const lastIndex = props.messages.length - 1
  if (lastIndex >= 0 && props.messages[lastIndex].role === 'assistant') {
    if (showKgVisualization.value[lastIndex] === undefined) {
      showKgVisualization.value[lastIndex] = true
    }
  }
}, { immediate: true })

// 切换知识图谱可视化显示
function toggleKgVisualization(index: number, event: Event) {
  // 阻止事件冒泡
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }

  // 保存当前图谱显示状态
  const currentState = showKgVisualization.value[index];

  // 切换图表显示状态
  showKgVisualization.value[index] = !currentState;

  // 把"是否展开"交给页面：展开会触发 resize，页面要做侧边栏保护并让图表重新量尺寸
  emit('kg-toggle', !currentState)
}

// 切换"引用图谱信息"的展开状态
function toggleKgInfo(msgIndex: number) {
  showKgInfo[msgIndex] = !showKgInfo[msgIndex];
}

/** 取消息里的图谱数据：优先用后端原样返回的 kg_data，其次把 kgContext 当 JSON 试一次。 */
function getKgData(message: ChatMessage): KgGraphData {
  try {
    // 检查消息中是否直接包含kg_data属性
    if (message.kg_data) {
      return message.kg_data;
    }

    // 检查消息的kgContext是否是JSON字符串
    if (message.kgContext) {
      // 尝试解析JSON，如果失败则认为是格式化文本而非JSON
      try {
        const parsed = JSON.parse(message.kgContext);
        if (parsed && typeof parsed === 'object' && (parsed.nodes || parsed.lines)) {
          return parsed as KgGraphData;
        }
      } catch (jsonError) {
        // 不是 JSON：按格式化文本处理，走空图谱分支
      }
    }
  } catch (e) {
    // 任何解析异常都不该影响消息渲染
  }

  // 返回空数据结构
  return { nodes: [], lines: [] };
}

function scrollToBottom() {
  nextTick(() => {
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight;
    }
  })
}

function scrollToTop() {
  nextTick(() => {
    container.value?.scrollTo({ top: 0 })
  })
}

defineExpose({ scrollToBottom, scrollToTop })
</script>
