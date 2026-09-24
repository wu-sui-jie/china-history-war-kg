<!--
  智能问答页面

  功能: 基于大模型的历史战争知识问答
    - 自然语言提问
    - 展示AI回答和相关知识图谱
    - 显示推理耗时和识别实体
    - 支持多轮对话历史

  API: POST/GET /api/ai/inference
  参数: question(用户问题)

  处理流程: 实体提取 → Neo4j查询 → 规则推理 → 大模型生成回答
-->
<template>
  <div class="inference-container">
    <!-- 左侧聊天列表 -->
    <div class="chat-sidebar" :class="{ 'sidebar-collapsed': !showSidebar }">
      <div class="sidebar-header">
        <button type="button" class="new-chat-button" @mousedown.stop @click.stop="createNewChat">
          新建聊天
        </button>
      </div>
      <div class="chat-history">
        <div
          v-for="(chat, index) in chatHistory"
          :key="index"
          class="chat-item"
          :class="{ 'active': currentChatIndex === index }"
        >
          <div class="chat-item-content" @click="switchChat(index)">
            <div class="chat-title">{{ chat.title || '新对话' }}</div>
            <div class="chat-time">{{ formatChatTime(chat.lastTime) }}</div>
          </div>
          <div class="chat-actions">
            <lay-icon
              type="layui-icon-delete"
              color="#ff5722"
              @click.stop="deleteChat(index)"
              class="delete-icon"
            ></lay-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- 右侧聊天内容 -->
    <div class="chat-content">
      <!-- 添加覆盖层，用于移动设备上阻止事件传播 -->
      <div class="sidebar-overlay" v-if="isMobileView && showSidebar" @click="toggleSidebar"></div>

      <!-- 移除lay-card，使用flex布局替代 -->
      <div class="chat-header">
        <div class="header-left">
          <div class="fold-button" @click="toggleSidebar">
            <lay-icon :type="showSidebar ? 'layui-icon-spread-left' : 'layui-icon-shrink-right'" color="#009688" size="16px"></lay-icon>
          </div>
          <div class="header-title">{{ currentChat.title || '新对话' }}</div>
        </div>
        <div class="header-actions">
          <!-- 添加导出按钮 -->
          <lay-icon
            type="layui-icon-export"
            color="#009688"
            size="20px"
            class="export-button"
            title="导出对话到Markdown"
            @click="exportToMarkdown"
          ></lay-icon>
        </div>
      </div>

      <!-- 聊天区域包装器 -->
      <div class="message-wrapper">
        <!-- 消息列表 -->
        <div class="message-container" ref="messageContainer">
          <div v-if="isFirstLoad && currentChat.messages.length === 0" class="empty-chat">
            <div class="empty-icon">
              <lay-icon type="layui-icon-loading" size="60px" color="#dcdcdc"></lay-icon>
            </div>
            <div class="empty-text">正在加载聊天记录...</div>
          </div>
          <div v-else-if="currentChat.messages.length === 0" class="empty-chat">
            <div class="empty-icon">
              <lay-icon type="layui-icon-dialogue" size="60px" color="#dcdcdc"></lay-icon>
            </div>
            <div class="empty-text">开始新的对话</div>
            <!-- 添加快捷提示 -->
            <div class="quick-prompts">
              <div class="prompt-title">常用提示：</div>
              <div class="prompt-items">
                <div class="prompt-item" v-for="(prompt, idx) in quickPrompts" :key="idx" @click="useQuickPrompt(prompt)">
                  {{ prompt }}
                </div>
              </div>
            </div>
          </div>
          <template v-else>
            <div
              v-for="(message, msgIndex) in currentChat.messages"
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
                          <kg-graph v-else :data="getKgData(message)" class="kg-graph" @node-click="handleKgNodeClick"></kg-graph>
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
                      @click="copyMessage(message.content)"
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

        <!-- 实体卡片 - 当识别到实体且当前消息是AI回复时显示 -->
        <div v-if="currentChat.messages.length > 0 &&
                    currentChat.messages[currentChat.messages.length-1].role === 'assistant' &&
                    currentChat.messages[currentChat.messages.length-1].entities &&
                    (currentChat.messages[currentChat.messages.length-1].entities?.length ?? 0) > 0 &&
                    showEntityCard"
             class="entity-card">
          <div class="entity-card-header">
            <div class="entity-card-title">
              <lay-icon type="layui-icon-location" color="#009688" size="14px"></lay-icon>
              <span>问题中的实体</span>
            </div>
            <div class="entity-card-close" @click="hideEntityCard">
              <lay-icon type="layui-icon-close" color="#999" size="12px"></lay-icon>
            </div>
          </div>
          <div class="entity-list">
            <lay-tag
              v-for="(entity, idx) in currentChat.messages[currentChat.messages.length-1].entities"
              :key="idx"
              size="sm"
              theme="primary"
              class="entity-tag"
              @click="askAboutEntity(entity)"
            >{{ entity }}</lay-tag>
          </div>
        </div>
      </div>

      <!-- 输入框和按钮 - 重新样式化为底部固定样式 -->
      <div class="input-container">
        <div class="input-textarea-wrapper">
          <lay-textarea
            v-model="currentQuery"
            placeholder="请输入您的问题，系统将结合知识图谱内容进行回答..."
            :autosize="{minRow: 1, maxRow: 2}"
            @keydown.enter="handleEnterKey"
            class="compact-textarea"
            ref="textareaRef"
          ></lay-textarea>
          <div class="send-button" @click="handleQuery" :class="{ 'disabled': loading || !currentQuery.trim() }">
            <lay-icon type="layui-icon-up" size="16px" :color="loading || !currentQuery.trim() ? '#ccc' : '#009688'"></lay-icon>
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
              @click="clearInput"
            ></lay-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- 消息提示 -->
    <lay-layer v-model="showNodeDetailDrawer" :title="'节点关联信息'" shade="true" :area="['520px', '88vh']">
      <div v-if="selectedKgNode" class="kg-node-detail">
        <div class="kg-node-detail-header">
          <div>
            <div class="kg-node-detail-type">{{ typeLabel(selectedKgNode.type) }}</div>
            <h3>{{ nodeDisplayName(selectedKgNode) }}</h3>
          </div>
          <lay-tag>{{ selectedKgNode.type || '未知类型' }}</lay-tag>
        </div>

        <div v-if="nodeSummaryChips.length" class="kg-node-summary">
          <div v-for="item in nodeSummaryChips" :key="item.label" class="kg-node-chip">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>

        <div v-if="selectedKgNode.source_text" class="kg-node-section emphasis">
          <div class="kg-node-section-title">来源原文</div>
          <div class="kg-node-long-text">{{ selectedKgNode.source_text }}</div>
        </div>

        <div v-if="selectedKgNode.Remark" class="kg-node-section">
          <div class="kg-node-section-title">补充说明</div>
          <div class="kg-node-long-text">{{ selectedKgNode.Remark }}</div>
        </div>

        <div v-if="selectedKgNode.Description" class="kg-node-section">
          <div class="kg-node-section-title">节点说明</div>
          <div class="kg-node-long-text">{{ selectedKgNode.Description }}</div>
        </div>

        <div v-if="selectedKgNode.Result" class="kg-node-section">
          <div class="kg-node-section-title">事件结果</div>
          <div class="kg-node-long-text">{{ selectedKgNode.Result }}</div>
        </div>

        <div v-if="selectedKgNode.Impact" class="kg-node-section">
          <div class="kg-node-section-title">历史影响</div>
          <div class="kg-node-long-text">{{ selectedKgNode.Impact }}</div>
        </div>

        <div v-if="structuredRelationAttributes.length" class="kg-node-section">
          <div class="kg-node-section-header">
            <div class="kg-node-section-title">关联关系</div>
            <span class="kg-node-section-count">{{ structuredRelationAttributes.length }} 组</span>
          </div>
          <div class="kg-structured-relations">
            <div
              v-for="(item, index) in structuredRelationAttributes"
              :key="`${item.relation}-${index}`"
              class="kg-structured-item"
            >
              <div class="kg-structured-head">
                <div class="kg-relation-badge">{{ item.relation }}</div>
                <div class="kg-relation-meta">
                  <span>{{ item.targets.length }} 个对象</span>
                  <span>{{ item.evidences.length }} 条证据</span>
                </div>
              </div>
              <div class="kg-structured-row">
                <label>关系对象</label>
                <div class="kg-target-list">
                  <lay-tag v-for="target in item.targets" :key="target" class="kg-target-tag">{{ target }}</lay-tag>
                </div>
              </div>
              <div class="kg-structured-row" v-if="item.evidences.length">
                <label>证据</label>
                <div class="kg-evidence-list">
                  <div v-for="evidence in item.evidences" :key="evidence" class="kg-evidence-card">{{ evidence }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="nodeDetailEntries.length" class="kg-node-section">
          <div class="kg-node-section-title">节点属性</div>
          <div class="kg-node-field-list">
            <div v-for="item in nodeDetailEntries" :key="item.key" class="kg-node-field">
              <label>{{ item.label }}</label>
              <div>{{ item.value }}</div>
            </div>
          </div>
        </div>
      </div>
    </lay-layer>

    <div v-if="showCopySuccess" class="copy-success-tip">
      复制成功!
    </div>
    <div v-if="showExportSuccess" class="export-success-tip">
      导出成功!
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, reactive, onBeforeUnmount } from 'vue';
import { useUserStore } from '../../store/user';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import DOMPurify from 'dompurify';
// 导入知识图谱组件
import KgGraph from './components/KgGraph.vue';
import { fieldLabel, groupRelationAttributes, nodeDisplayName, normalizeType, typeLabel } from '../../utils/knowledge';
import { formatChatTime } from '../../utils/date';

// 用户store（用于获取token）
const userStore = useUserStore();

// 代码高亮的 HTML 转义（不引用 md 实例，避免初始化器自引用）
function escapeHtml(text: string): string {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function highlightCode(str: string, lang: string): string {
  if (lang && hljs.getLanguage(lang)) {
    try {
      return '<pre class="hljs"><code>' +
             hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
             '</code></pre>';
    } catch (__) {}
  }

  return '<pre class="hljs"><code>' + escapeHtml(str) + '</code></pre>';
}

// 进行中的问答流控制器（见 handleQuery / onBeforeUnmount）
let streamController: AbortController | null = null;

// 创建Markdown渲染器实例
// html: false —— 不直通 raw HTML。模型输出与图谱数据都属于不可信内容，
// 它们拼出的 HTML 若直通 v-html 就是存储型 XSS 的入口。
const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: highlightCode,
});

// 所有进入 v-html 的 HTML 统一在这里净化（DOMPurify 兜底，去掉脚本与事件属性）
function sanitizeHtml(html: string): string {
  if (!html) return '';
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}

// Markdown 文本 -> 可安全交给 v-html 的 HTML
function renderMarkdown(text: string): string {
  if (!text) return '';
  return sanitizeHtml(md.render(text));
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  time: number;
  kgContext?: string; // 知识图谱上下文
  thinking?: string[]; // 思考过程
  fromKg: boolean; // 是否来自知识图谱
  entities?: string[]; // 实体列表
  kg_data?: any; // 单独存储原始的kg_data
}

interface Chat {
  id: number;
  title: string;
  messages: Message[];
  lastTime: number;
}

// 状态变量
const currentQuery = ref('');
const loading = ref(false);
const messageContainer = ref<HTMLElement | null>(null);
const showKgInfo = reactive<{[key: number]: boolean}>({});
const isFirstLoad = ref(true);
const isMobileView = ref(window.innerWidth < 768);
const showSidebar = ref(true);
const initialSidebarState = ref(true); // 添加变量存储初始侧边栏状态
const isSidebarLocked = ref(false); // 添加锁定变量，防止意外改变侧边栏状态
const showCopySuccess = ref(false);
const showExportSuccess = ref(false);

// 快捷提示
const quickPrompts = [
  "淝水之战的主战场是哪里？",
  "谁是赤壁之战的主要指挥者？",
  "长平之战的结果如何？"
];

// 控制知识图谱可视化的显示状态 - 默认展开
const showKgVisualization = ref<Record<number, boolean>>({});

// 添加ref用于获取textarea元素
const textareaRef = ref(null);

// 控制实体卡片是否显示
const showEntityCard = ref(true);
const showNodeDetailDrawer = ref(false);
const selectedKgNode = ref<Record<string, any> | null>(null);
const nodeLongTextKeys = new Set(['source_text', 'Remark', 'Description', 'Result', 'Impact']);

const nodeSummaryChips = computed(() => {
  const node = selectedKgNode.value || {};
  const chips = [];
  if (node.DynastyName) chips.push({ label: '朝代', value: node.DynastyName });
  if (node.StartDate || node.EndDate) chips.push({ label: '时间', value: [node.StartDate, node.EndDate].filter(Boolean).join(' 至 ') });
  if (node.Place || node.modern_name || node.geo_name) chips.push({ label: '地点', value: node.Place || node.modern_name || node.geo_name });
  if (node.EventType || node.OrgType || node.Role) chips.push({ label: '类别', value: node.EventType || node.OrgType || node.Role });
  return chips;
});

const structuredRelationAttributes = computed(() => groupRelationAttributes(selectedKgNode.value?.relations))

const nodeDetailEntries = computed(() => {
  const node = selectedKgNode.value || {};
  const hiddenKeys = new Set([
    'id', 'name', 'type', 'EventName', 'PersonName', 'OrgName', 'geo_name',
    'relations', 'created', 'category', 'label', 'value', 'symbolSize'
  ]);
  return Object.entries(node)
    .filter(([key, value]) => !hiddenKeys.has(key) && !nodeLongTextKeys.has(key) && String(value ?? '').trim())
    .filter(([key]) => fieldLabel(key) !== key)
    .map(([key, value]) => ({ key, label: fieldLabel(key), value: String(value) }));
});

// 切换侧边栏显示，适用于所有设备
function toggleSidebar() {
  // 解锁侧边栏以允许状态改变
  isSidebarLocked.value = false;
  showSidebar.value = !showSidebar.value;
  initialSidebarState.value = showSidebar.value; // 保存用户选择的状态
  // 给一段时间后重新锁定侧边栏状态
  setTimeout(() => {
    isSidebarLocked.value = true;
  }, 300);
}

// 切换知识图谱可视化显示
function toggleKgVisualization(index: number, event: Event) {
  // 阻止事件冒泡
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }

  // 锁定侧边栏，防止状态被改变
  isSidebarLocked.value = true;

  // 保存当前图谱显示状态
  const currentState = showKgVisualization.value[index];

  // 切换图表显示状态
  showKgVisualization.value[index] = !currentState;

  // 延迟触发resize事件，等待DOM更新
  if (!currentState) { // 仅在展开图谱时执行
    nextTick(() => {
      // 确保侧边栏状态不变
      if (isSidebarLocked.value) {
        showSidebar.value = initialSidebarState.value;
      }

      // 用setTimeout给DOM更新留出时间
      setTimeout(() => {
        if (showKgVisualization.value[index]) { // 确保仍然是展开状态
          // 确保侧边栏状态不变
          if (isSidebarLocked.value) {
            showSidebar.value = initialSidebarState.value;
          }

          // 不改变侧边栏状态，仅调整图表大小
          window.dispatchEvent(new Event('resize'));
        }
      }, 100);
    });
  }

  // 300ms后解除侧边栏锁定
  setTimeout(() => {
    isSidebarLocked.value = false;
  }, 300);
}

// 使用快捷提示
function useQuickPrompt(prompt: string) {
  currentQuery.value = prompt;
}

// 复制消息
function copyMessage(content: string) {
  try {
    // 使用更可靠的剪贴板API，并添加错误处理
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(content)
        .then(() => {
          showCopySuccess.value = true;
          setTimeout(() => {
            showCopySuccess.value = false;
          }, 2000);
        })
        .catch(err => {
          // 回退方案：创建临时文本域元素
          fallbackCopy(content);
        });
    } else {
      // 浏览器不支持clipboard API，使用回退方案
      fallbackCopy(content);
    }
  } catch (error) {
    fallbackCopy(content);
  }
}

// 回退复制方法（创建临时文本域元素）
function fallbackCopy(text: string) {
  const textArea = document.createElement('textarea');
  textArea.value = text;

  // 确保元素不可见
  textArea.style.position = 'fixed';
  textArea.style.left = '-999999px';
  textArea.style.top = '-999999px';
  document.body.appendChild(textArea);

  // 保存用户的选择范围
  const selected = document.getSelection()?.rangeCount ?? 0 > 0
    ? document.getSelection()?.getRangeAt(0)
    : false;

  // 选择文本
  textArea.select();
  textArea.setSelectionRange(0, textArea.value.length);

  // 执行复制命令
  try {
    document.execCommand('copy');
    showCopySuccess.value = true;
    setTimeout(() => {
      showCopySuccess.value = false;
    }, 2000);
  } catch (err) {
  }

  // 移除元素
  document.body.removeChild(textArea);

  // 恢复用户的选择
  if (selected && document.getSelection()) {
    document.getSelection()?.removeAllRanges();
    document.getSelection()?.addRange(selected);
  }
}

// 清空输入框
function clearInput() {
  currentQuery.value = '';
}

// 切换知识图谱信息显示
function toggleKgInfo(msgIndex: number) {
  showKgInfo[msgIndex] = !showKgInfo[msgIndex];
}

// 聊天历史
const chatHistory = ref<Chat[]>([
  {
    id: 1,
    title: '新对话',
    messages: [],
    lastTime: Date.now()
  }
]);

const currentChatIndex = ref(0);
const currentChat = computed(() => chatHistory.value[currentChatIndex.value]);

// 监听消息变化，为新消息默认展开知识图谱
watch(() => currentChat.value.messages.length, () => {
  const lastIndex = currentChat.value.messages.length - 1;
  if (lastIndex >= 0 && currentChat.value.messages[lastIndex].role === 'assistant') {
    if (showKgVisualization.value[lastIndex] === undefined) {
      showKgVisualization.value[lastIndex] = true;
    }
  }
}, { immediate: true });

// 从localStorage加载聊天记录
function loadChatHistory() {
  try {
    const storedHistory = localStorage.getItem('chatHistory');
    if (storedHistory) {
      chatHistory.value = JSON.parse(storedHistory);
    }
    // 设置一个短暂的加载延迟，以显示加载状态
    setTimeout(() => {
      isFirstLoad.value = false;
    }, 500);
  } catch (error) {
    isFirstLoad.value = false;
  }
}

// 聊天历史容量上限：localStorage 一般只有 ~5MB，而流式回答很长，
// 不设上限会在若干轮对话后直接写不进去（QuotaExceededError 原先被空 catch 吞掉）。
const MAX_CHATS = 20;
const MAX_MESSAGES_PER_CHAT = 200;

// 收缩聊天历史到上限内：单会话保留最近的消息，会话数按最近使用保留
function trimChatHistory() {
  const chats = chatHistory.value;
  if (!Array.isArray(chats)) return;
  for (const chat of chats) {
    if (Array.isArray(chat.messages) && chat.messages.length > MAX_MESSAGES_PER_CHAT) {
      chat.messages = chat.messages.slice(-MAX_MESSAGES_PER_CHAT);
    }
  }
  if (chats.length > MAX_CHATS) {
    chatHistory.value = [...chats]
      .sort((a, b) => (b.lastTime || 0) - (a.lastTime || 0))
      .slice(0, MAX_CHATS);
  }
}

// 保存聊天记录到localStorage
function saveChatHistory() {
  try {
    trimChatHistory();
    localStorage.setItem('chatHistory', JSON.stringify(chatHistory.value));
  } catch (error) {
    // 配额仍不够：丢掉一半会话再试一次；还失败就只告警，不阻塞对话
    try {
      trimChatHistory();
      chatHistory.value = chatHistory.value.slice(0, Math.max(1, Math.floor(MAX_CHATS / 2)));
      localStorage.setItem('chatHistory', JSON.stringify(chatHistory.value));
      console.warn('聊天记录超出 localStorage 配额，已丢弃较旧的会话');
    } catch (retryError) {
      console.warn('聊天记录写入 localStorage 失败，本次不保存历史:', retryError);
    }
  }
}

// 创建新聊天
function createNewChat() {
  streamController?.abort();
  const now = Date.now();
  const newChat: Chat = {
    id: now,
    title: `新对话 ${formatChatTime(now)}`,
    messages: [],
    lastTime: now
  };
  chatHistory.value.unshift(newChat);
  currentChatIndex.value = 0;
  currentQuery.value = '';
  showEntityCard.value = false;
  saveChatHistory();
  nextTick(() => {
    messageContainer.value?.scrollTo({ top: 0 });
  });
}

// 切换聊天
function switchChat(index: number) {
  streamController?.abort();
  currentChatIndex.value = index;
}

// 更新聊天标题
function updateChatTitle(chatIndex: number, firstMessage: string) {
  const chat = chatHistory.value[chatIndex];
  if (!chat.title || chat.title === '新对话') {
    // 使用第一条消息的前15个字符作为标题
    chat.title = firstMessage.slice(0, 15) + (firstMessage.length > 15 ? '...' : '');
  }
}

// 格式化时间

// 滚动到底部
function scrollToBottom() {
  nextTick(() => {
    if (messageContainer.value) {
      messageContainer.value.scrollTop = messageContainer.value.scrollHeight;
    }
  });
}

// 提交查询
async function handleQuery() {
  if (!currentQuery.value.trim() || loading.value) {
    return;
  }

  // 添加用户消息
  const userMessage: Message = {
    role: 'user',
    content: currentQuery.value,
    time: Date.now(),
    fromKg: true
  };

  currentChat.value.messages.push(userMessage);
  currentChat.value.lastTime = Date.now();

  // 如果是第一条消息，更新聊天标题
  if (currentChat.value.messages.length === 1) {
    updateChatTitle(currentChatIndex.value, userMessage.content);
  }

  const queryText = currentQuery.value;
  currentQuery.value = '';
  loading.value = true;

  scrollToBottom();

  // 创建空的AI消息，用于逐步填充内容
  const aiMessage: Message = {
    role: 'assistant',
    content: '',
    time: Date.now(),
    fromKg: true,
    entities: [],
    kg_data: { nodes: [], lines: [] },
    kgContext: '',
    thinking: []
  };
  currentChat.value.messages.push(aiMessage);

  try {
    // 使用SSE流式接收
    const streamUrl = '/api/ai/inference/stream';
    // 每次提问前中断上一次未结束的流：否则切换会话/离开页面后，
    // 旧流仍会继续往已经没人看的 aiMessage 上写内容
    streamController?.abort();
    streamController = new AbortController();
    const response = await fetch(streamUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Token': userStore.token || ''
      },
      body: JSON.stringify({ question: queryText }),
      signal: streamController.signal
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('无法获取响应流');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let chunkCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      chunkCount++;

      if (done) {
        break;
      }

      const decoded = decoder.decode(value, { stream: true });
      buffer += decoded;
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';  // 保留不完整的行

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));

            switch (data.status) {
              case 'start':
                break;

              case 'extracting':
                // 显示提取状态
                aiMessage.content = `⏳ ${data.message}`;
                scrollToBottom();
                break;

              case 'entities':
                // 保存实体信息
                aiMessage.entities = data.entities || [];
                break;

              case 'querying':
                // 显示查询状态
                aiMessage.content = `⏳ ${data.message}`;
                scrollToBottom();
                break;

              case 'queried':
                // 查询完成
                break;

              case 'generating':
                // 开始生成回答，清空状态信息
                aiMessage.content = '';
                scrollToBottom();
                break;

              case 'content':
                // 逐步追加内容
                aiMessage.content += data.content;
                scrollToBottom();
                break;

              case 'done':
                // 完成，保存知识图谱数据
                if (data.answer && !aiMessage.content) {
                  aiMessage.content = data.answer;
                }
                aiMessage.kg_data = data.kg_data || { nodes: [], lines: [] };
                aiMessage.kgContext = data.relations_text || '';
                aiMessage.entities = data.entities || [];
                aiMessage.fromKg = data.kg_data && (data.kg_data.nodes.length > 0 || data.kg_data.lines.length > 0);
                break;

              case 'error':
                // 错误处理
                aiMessage.content = `❌ ${data.message}`;
                aiMessage.fromKg = false;
                break;
            }
          } catch (parseErr) {
          }
        }
      }
    }

    // 处理buffer中剩余的数据
    if (buffer.startsWith('data: ')) {
      try {
        const data = JSON.parse(buffer.slice(6));
        if (data.status === 'done') {
          aiMessage.kg_data = data.kg_data || { nodes: [], lines: [] };
          aiMessage.kgContext = data.relations_text || '';
          aiMessage.entities = data.entities || [];
          aiMessage.fromKg = data.kg_data && (data.kg_data.nodes.length > 0 || data.kg_data.lines.length > 0);
        }
      } catch (e) {
        // 忽略解析错误
      }
    }

    currentChat.value.lastTime = Date.now();
    loading.value = false;
    scrollToBottom();

  } catch (error) {

    // 主动中断（切换会话、发起新提问、离开页面）不是故障：
    // 保留已生成的内容，也不要覆盖成错误提示
    if ((error as any)?.name === 'AbortError') {
      loading.value = false;
      return;
    }

    // 更新AI消息为错误信息
    aiMessage.content = '推理请求发生错误，请稍后再试';
    aiMessage.fromKg = false;
    currentChat.value.lastTime = Date.now();

    loading.value = false;
    scrollToBottom();
  }
}

// 监听消息变化，自动保存聊天历史。
// 流式回答是逐字追加的，直接写会把整份历史 JSON.stringify 跑上千次，因此合并到 500ms 一次。
let saveHistoryTimer: number | undefined;
function scheduleSaveChatHistory() {
  if (saveHistoryTimer) window.clearTimeout(saveHistoryTimer);
  saveHistoryTimer = window.setTimeout(() => {
    saveHistoryTimer = undefined;
    saveChatHistory();
  }, 500);
}
watch(() => chatHistory.value, () => {
  scheduleSaveChatHistory();
}, { deep: true });

// 监听消息变化，自动滚动到底部
watch(() => currentChat.value.messages.length, () => {
  scrollToBottom();
});

// 监听聊天切换
watch(currentChatIndex, () => {
  scrollToBottom();
});

// 监听窗口大小变化
function handleResize() {
  isMobileView.value = window.innerWidth < 768;

  // 如果侧边栏已锁定，则不改变其状态
  if (!isSidebarLocked.value) {
    // 移除自动折叠侧边栏的逻辑，改为使用初始状态
    if (window.innerWidth < 768) {
      // 在小屏幕上根据初始状态决定是否显示侧边栏
      showSidebar.value = initialSidebarState.value && isMobileView.value;
    } else {
      // 在大屏幕上保持侧边栏状态
      showSidebar.value = initialSidebarState.value;
    }
  }
}

// 组件挂载时添加窗口大小监听
onMounted(() => {
  loadChatHistory();
  scrollToBottom();
  window.addEventListener('resize', handleResize);

  // 设置初始侧边栏状态
  initialSidebarState.value = true;
  showSidebar.value = window.innerWidth >= 768 ? true : false;
});

// 组件卸载时移除窗口大小监听
onBeforeUnmount(() => {
  // 中断进行中的问答流，并把防抖中的历史立即落盘
  streamController?.abort();
  if (saveHistoryTimer) {
    window.clearTimeout(saveHistoryTimer);
    saveHistoryTimer = undefined;
  }
  saveChatHistory();
  window.removeEventListener('resize', handleResize);
});

// 删除聊天
function deleteChat(index: number) {
  if (chatHistory.value.length <= 1) {
    // 如果只有一个聊天，清空它而不是删除
    chatHistory.value[0].messages = [];
    chatHistory.value[0].title = '新对话';
    chatHistory.value[0].lastTime = Date.now();
  } else {
    // 删除指定的聊天
    chatHistory.value.splice(index, 1);

    // 如果删除的是当前聊天，则切换到第一个聊天
    if (index === currentChatIndex.value) {
      currentChatIndex.value = 0;
    }
    // 如果删除的聊天在当前聊天之前，需要调整索引
    else if (index < currentChatIndex.value) {
      currentChatIndex.value--;
    }
  }

  saveChatHistory();
}

// 导出对话内容到Markdown
function getGraphNodeDisplayName(node: any) {
  if (!node) return '-';
  return node.EventName || node.PersonName || node.OrgName || node.geo_name || node.name || node.label || '-';
}

function exportToMarkdown() {
  if (currentChat.value.messages.length === 0) {
    alert('当前对话为空，无法导出');
    return;
  }

  try {
    // 构建Markdown内容
    let markdownContent = `# ${currentChat.value.title || '对话记录'}\n\n`;
    markdownContent += `导出时间: ${formatChatTime(Date.now())}\n\n`;

    // 添加每条消息
    currentChat.value.messages.forEach((message, index) => {
      const role = message.role === 'user' ? '用户' : 'AI助手';
      const time = formatChatTime(message.time);

      markdownContent += `## ${role} (${time})\n\n`;

      // 如果是AI消息且有思考过程，添加思考过程
      if (message.role === 'assistant' && message.thinking && message.thinking.length > 0) {
        markdownContent += '### 思考过程\n\n';
        message.thinking.forEach((think, i) => {
          // 将思考过程转换为Markdown引用格式
          // 处理每一行，在每行前添加>符号
          const thinkLines = think.split('\n');
          const quotedThink = thinkLines.map(line => `> ${line}`).join('\n');
          markdownContent += `${quotedThink}\n\n`;
        });
      }

      // 添加消息内容
      markdownContent += `${message.content}\n\n`;

      // 如果是AI消息且有知识图谱上下文，添加知识图谱信息
      if (message.role === 'assistant' && message.kgContext) {
        markdownContent += '### 知识图谱参考信息\n\n';
        markdownContent += '```\n';
        markdownContent += message.kgContext;
        markdownContent += '\n```\n\n';
      }

      // 添加分隔线（除了最后一条消息）
      if (index < currentChat.value.messages.length - 1) {
        markdownContent += '---\n\n';
      }
    });


    // 创建Blob对象
    const blob = new Blob([markdownContent], { type: 'text/markdown;charset=utf-8' });

    // 创建下载链接
    const url = URL.createObjectURL(blob);

    // 设置文件名 (使用对话标题和日期时间)
    const now = new Date();
    const dateStr = now.toISOString().split('T')[0]; // 格式: YYYY-MM-DD
    const fileName = `${currentChat.value.title || '对话记录'}_${dateStr}.md`;

    // 使用更可靠的下载方法
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', fileName);
    link.style.display = 'none';
    document.body.appendChild(link);

    // 点击并移除
    link.click();

    // 延迟移除元素和URL，确保浏览器有足够时间处理下载
    setTimeout(() => {
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      // 显示成功消息
      showExportSuccess.value = true;
      setTimeout(() => {
        showExportSuccess.value = false;
      }, 2000);
    }, 100);
  } catch (error) {
    alert(`导出失败: ${error instanceof Error ? error.message : String(error)}`);
  }
}

// 点击实体标签时询问关于该实体的问题
function askAboutEntity(entity: string) {
  // 构建关于实体的问题
  currentQuery.value = `请告诉我关于${entity}的历史相关信息`;

  // 自动提交查询
  nextTick(() => {
    handleQuery();
  });
}

// 从消息中获取知识图谱数据
function normalizeKgNodeDetail(node: Record<string, any>) {
  return {
    ...node,
    type: normalizeType(String(node.type || '').trim()) || node.type || ''
  };
}

function handleKgNodeClick(node: Record<string, any>) {
  selectedKgNode.value = normalizeKgNodeDetail(node);
  showNodeDetailDrawer.value = true;
}

function getKgData(message: Message) {
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
          return parsed;
        }
      } catch (jsonError) {
      }
    }
  } catch (e) {
  }

  // 返回空数据结构
  return { nodes: [], lines: [] };
}

// 渲染知识图谱上下文为Markdown
// 图谱数据来自接口，属不可信内容：统一在出口做 HTML 净化
function renderKgContextMarkdown(context: string): string {
  return sanitizeHtml(buildKgContextHtml(context));
}

function buildKgContextHtml(context: string): string {
  if (!context) return '';

  // 尝试解析为JSON并转换为表格
  try {
    const jsonData = JSON.parse(context);
    if (typeof jsonData === 'object') {
      // 检查是否包含nodes和lines（知识图谱数据结构）
      if (jsonData.nodes && Array.isArray(jsonData.nodes)) {
        return renderJsonTableMarkdown(jsonData);
      }

      // 一般JSON对象转表格
      return jsonObjectToMarkdownTable(jsonData);
    }
  } catch (e) {
    // 解析失败，作为普通文本处理
  }

  // 处理新的文本格式 - 使用更美观的渲染
  return renderFormattedKgContext(context);
}

// 渲染格式化的知识图谱上下文
function renderFormattedKgContext(context: string): string {
  if (!context) return '';
  
  
  // 检查是否是旧格式的简单文本（不包含【】章节标记）
  if (!context.includes('【') || !context.includes('】')) {
    // 使用 pre 标签保持格式，或者直接用 markdown 渲染
    return `<pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.6;">${context}</pre>`;
  }
  
  // 按行分割
  const lines = context.split('\n');
  let html = '<div class="kg-context-formatted">';
  
  let currentSection = '';
  let sectionContent: string[] = [];
  
  const flushSection = () => {
    if (!currentSection) return;
    
    
    if (currentSection === '涉及实体') {
      html += '<div class="kg-section kg-entities">';
      html += '<div class="kg-section-title">📚 涉及实体</div>';
      html += '<div class="kg-entity-list">';
      sectionContent.forEach((line, idx) => {
        // 解析实体行: "⚔️ 实体名 (类型)" 
        // 使用简单的字符串处理代替正则
        const parenIdx = line.lastIndexOf('(');
        if (parenIdx > 0 && line.endsWith(')')) {
          const beforeParen = line.substring(0, parenIdx).trim();
          const type = line.substring(parenIdx + 1, line.length - 1);
          // 提取图标（第一个字符/emoji）和名称
          const firstSpaceIdx = beforeParen.indexOf(' ');
          if (firstSpaceIdx > 0) {
            const icon = beforeParen.substring(0, firstSpaceIdx);
            const name = beforeParen.substring(firstSpaceIdx + 1).trim();
            html += `<span class="kg-entity-tag" data-type="${type}">${icon} ${name}</span>`;
          }
        } else {
        }
      });
      html += '</div></div>';
    } else if (currentSection.includes('关系')) {
      const isInferred = currentSection.includes('推理');
      html += `<div class="kg-section kg-relations ${isInferred ? 'inferred' : 'direct'}">`;
      html += `<div class="kg-section-title">${isInferred ? '🔍 推理关系' : '🔗 直接关系'}</div>`;
      html += '<div class="kg-relation-list">';
      sectionContent.forEach((line, idx) => {
        // 解析关系行: "1. 源 →【关系】→ 目标"
        // 使用字符串分割代替正则，更健壮
        const arrow1Idx = line.indexOf('→【');
        const arrow2Idx = line.indexOf('】→');
        if (arrow1Idx > 0 && arrow2Idx > arrow1Idx) {
          // 提取序号和源实体
          const beforeFirstArrow = line.substring(0, arrow1Idx).trim();
          const dotIdx = beforeFirstArrow.indexOf('.');
          if (dotIdx > 0) {
            const num = beforeFirstArrow.substring(0, dotIdx).trim();
            const source = beforeFirstArrow.substring(dotIdx + 1).trim();
            // 提取关系类型
            const relation = line.substring(arrow1Idx + 2, arrow2Idx).trim();
            // 提取目标实体
            const target = line.substring(arrow2Idx + 2).trim();
            
            html += `
              <div class="kg-relation-item">
                <span class="kg-relation-num">${num}</span>
                <span class="kg-relation-source">${source}</span>
                <span class="kg-relation-arrow">→</span>
                <span class="kg-relation-type">${relation}</span>
                <span class="kg-relation-arrow">→</span>
                <span class="kg-relation-target">${target}</span>
              </div>
            `;
          }
        } else if (line.startsWith('(') && line.endsWith(')')) {
          // 基于关系的说明
          html += `<div class="kg-relation-note">${line}</div>`;
        } else if (line.includes('...')) {
          // 省略说明
          html += `<div class="kg-relation-more">${line}</div>`;
        } else {
        }
      });
      html += '</div></div>';
    }
    
    sectionContent = [];
  };
  
  lines.forEach(line => {
    const trimmedLine = line.trim();
    // 匹配章节标题: 【章节名】 或 【章节名】(说明)
    const sectionMatch = trimmedLine.match(/^【(.+?)】(.*)$/);
    if (sectionMatch) {
      flushSection();
      currentSection = sectionMatch[1];
    } else if (trimmedLine && !trimmedLine.startsWith('📚')) {
      sectionContent.push(trimmedLine);
    }
  });
  
  flushSection();
  html += '</div>';
  
  
  // 如果生成的HTML只有外壳（没有实际内容），回退到原始文本
  if (html.length < 50 || !html.includes('kg-section')) {
    return `<pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.6;">${context}</pre>`;
  }
  
  return html;
}

// 将JSON对象转换为Markdown表格
function jsonObjectToMarkdownTable(json: any): string {
  // 处理非对象或空对象
  if (!json || typeof json !== 'object' || Array.isArray(json) && json.length === 0) {
    return renderMarkdown('*无有效数据*');
  }

  // 处理数组
  if (Array.isArray(json)) {
    // 提取所有可能的键
    const allKeys = new Set<string>();
    json.forEach(item => {
      if (item && typeof item === 'object') {
        Object.keys(item).forEach(key => allKeys.add(key));
      }
    });

    const keys = Array.from(allKeys);
    if (keys.length === 0) {
      // 数组包含的不是对象
      let tableContent = '数组内容：\n\n';
      json.forEach((item, index) => {
        tableContent += `${index+1}. ${String(item)}\n`;
      });
      return renderMarkdown(tableContent);
    }

    // 构建表头
    let table = '| # | ' + keys.join(' | ') + ' |\n';
    table += '|' + '---|'.repeat(keys.length + 1) + '\n';

    // 构建表行
    json.forEach((item, index) => {
      table += `| ${index+1} |`;
      keys.forEach(key => {
        const value = item[key];
        if (value === undefined || value === null) {
          table += ' - |';
        } else if (typeof value === 'object') {
          table += ` ${JSON.stringify(value).substr(0, 20)}... |`;
        } else {
          table += ` ${String(value)} |`;
        }
      });
      table += '\n';
    });

    return renderMarkdown(table);
  }

  // 处理普通对象
  let table = '| 属性 | 值 |\n|---|---|\n';

  Object.entries(json).forEach(([key, value]) => {
    if (value === null || value === undefined) {
      table += `| ${key} | - |\n`;
    } else if (typeof value === 'object') {
      if (Array.isArray(value) && value.length > 0) {
        table += `| ${key} | ${value.length}个项目 |\n`;
      } else {
        table += `| ${key} | ${JSON.stringify(value).substr(0, 30)}... |\n`;
      }
    } else {
      table += `| ${key} | ${String(value)} |\n`;
    }
  });

  return renderMarkdown(table);
}

// 渲染知识图谱数据为Markdown表格
function renderJsonTableMarkdown(jsonData: any): string {
  let result = '';

  // 添加节点表格
  if (jsonData.nodes && jsonData.nodes.length > 0) {
    result += '## 实体节点\n\n';
    result += '| # | ID | 名称 | 类型 |\n';
    result += '|---|---|---|---|\n';

    jsonData.nodes.forEach((node: any, index: number) => {
      const id = node.id || '-';
      const name = getGraphNodeDisplayName(node);
      const type = node.type || node.category || '-';
      result += `| ${index+1} | ${id} | ${name} | ${type} |\n`;
    });

    result += '\n\n';
  }

  // 添加关系表格
  if (jsonData.lines && jsonData.lines.length > 0) {
    result += '## 实体关系\n\n';
    result += '| # | 源实体 | 关系 | 目标实体 | 类型 |\n';
    result += '|---|---|---|---|---|\n';

    // 创建节点ID到名称的映射
    const nodeMap = new Map();
    if (jsonData.nodes) {
      jsonData.nodes.forEach((node: any) => {
        if (node.id !== undefined) {
          nodeMap.set(String(node.id), getGraphNodeDisplayName(node));
        }
      });
    }

    // 添加调试信息

    jsonData.lines.forEach((line: any, index: number) => {
      // 源和目标节点ID
      const fromId = line.from !== undefined ? line.from :
                    (line.source !== undefined ? line.source : '-');
      const toId = line.to !== undefined ? line.to :
                  (line.target !== undefined ? line.target : '-');

      // 尝试获取关系文本
      let relationText = '-';
      if (line.text !== undefined && line.text !== '') {
        relationText = line.text;
      } else if (line.relation !== undefined && line.relation !== '') {
        relationText = line.relation;
      } else if (line.label !== undefined && line.label !== '') {
        relationText = line.label;
      } else if (line.name !== undefined && line.name !== '') {
        relationText = line.name;
      }

      // 关系类型（是否为推理关系）
      let relationType = '数据库关系';
      if (line.inferred === true) {
        relationType = '推理关系';
      } else if (line.derived_from) {
        relationType = '推理关系';
      } else if (line.rule_id) {
        relationType = '推理关系';
      }

      // 获取实体名称
      const sourceName = nodeMap.get(String(fromId)) || String(fromId);
      const targetName = nodeMap.get(String(toId)) || String(toId);

      result += `| ${index+1} | ${sourceName} | ${relationText} | ${targetName} | ${relationType} |\n`;
    });

    // 添加完整属性信息表格
    result += '\n\n## 关系详细属性\n\n';
    result += '| # | 关系 | 源 → 目标 | 属性信息 |\n';
    result += '|---|---|---|---|\n';

    jsonData.lines.forEach((line: any, index: number) => {
      // 获取源和目标ID
      const fromId = line.from !== undefined ? line.from :
                   (line.source !== undefined ? line.source : '-');
      const toId = line.to !== undefined ? line.to :
                 (line.target !== undefined ? line.target : '-');

      // 获取关系文本
      let relationText = line.text || line.relation || line.label || '-';

      // 获取实体名称
      const sourceName = nodeMap.get(String(fromId)) || String(fromId);
      const targetName = nodeMap.get(String(toId)) || String(toId);

      // 收集所有属性
      const propPairs = [];
      for (const [key, value] of Object.entries(line)) {
        // 跳过基本属性
        if (['from', 'to', 'source', 'target', 'text', 'relation', 'label'].includes(key)) {
          continue;
        }

        // 格式化值
        let formattedValue = value;
        if (typeof value === 'object') {
          formattedValue = JSON.stringify(value).substring(0, 50);
          if (JSON.stringify(value).length > 50) {
            formattedValue += '...';
          }
        }

        propPairs.push(`${key}: ${formattedValue}`);
      }

      const props = propPairs.length > 0 ? propPairs.join('<br>') : '-';
      result += `| ${index+1} | ${relationText} | ${sourceName} → ${targetName} | ${props} |\n`;
    });
  } else {
    result += '## 实体关系\n\n没有关系数据\n\n';
  }

  return renderMarkdown(result);
}

// 思考过程渲染函数
function renderThinkingContent(thinkingText: string): string {
  if (!thinkingText) return '';

  // 先转义原始文本再套格式化标签：思考过程同样是模型输出，
  // 其中出现 <script> / onerror 之类内容时不能当 HTML 执行。
  let formattedText = md.utils.escapeHtml(thinkingText);

  // 处理标题格式（例如：1. **问题理解**:）
  formattedText = formattedText.replace(/(\d+\.\s*\*\*[^*]+\*\*:)/g, '<h4>$1</h4>');

  // 处理子标题和实体标记（例如：- **平江府**:）
  formattedText = formattedText.replace(/(-\s*\*\*[^*]+\*\*:)/g, '<h5>$1</h5>');

  // 处理普通加粗文本
  formattedText = formattedText.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 处理列表项（以 - 开头的行）
  formattedText = formattedText.replace(/^-\s+([^<].*)/gm, '<div class="list-item">• $1</div>');

  // 将换行符转换为<br>标签
  formattedText = formattedText.replace(/\n/g, '<br>');

  // 修复在替换后可能出现的多余<br>标签
  formattedText = formattedText.replace(/<\/h4><br>/g, '</h4>');
  formattedText = formattedText.replace(/<\/h5><br>/g, '</h5>');
  formattedText = formattedText.replace(/<\/div><br>/g, '</div>');

  // 为代码块添加样式
  formattedText = formattedText.replace(/```([\s\S]*?)```/g, '<pre class="thinking-code"><code>$1</code></pre>');

  return sanitizeHtml(formattedText);
}

// 处理回车键事件
function handleEnterKey(event: KeyboardEvent) {
  // 如果按下了Shift键，则允许换行
  if (event.shiftKey) {
    return;
  }

  // 否则，阻止默认行为并发送消息
  event.preventDefault();
  handleQuery();
}

// 隐藏实体卡片
function hideEntityCard() {
  showEntityCard.value = false;
}

// 在新消息到达时重置实体卡片显示状态
watch(() => currentChat.value.messages.length, () => {
  if (currentChat.value.messages.length > 0 &&
      currentChat.value.messages[currentChat.value.messages.length-1].role === 'assistant' &&
      (currentChat.value.messages[currentChat.value.messages.length-1].entities?.length ?? 0) > 0) {
    showEntityCard.value = true;
  }
});
</script>

<style scoped>
.inference-container {
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  position: relative;
  background-color: #f5f7fa;
}

/* 左侧边栏样式 */
.chat-sidebar {
  width: 230px;
  flex: 0 0 230px;
  border-right: 1px solid #eee;
  display: flex;
  flex-direction: column;
  background-color: #fff;
  transition: all 0.3s ease;
  z-index: 100;
  height: 100%;
  box-shadow: 0 0 10px rgba(0,0,0,0.05);
  isolation: isolate; /* 创建新的堆叠上下文 */
  pointer-events: auto !important; /* 强制确保点击事件独立 */
}

/* 侧边栏折叠状态 */
.sidebar-collapsed {
  width: 0;
  flex-basis: 0;
  padding: 0;
  overflow: hidden;
  border-right: none;
}

/* 移动设备适配 */
@media (max-width: 767px) {
  .chat-sidebar {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    box-shadow: 2px 0 10px rgba(0,0,0,0.1);
    z-index: 1000; /* 增加z-index值确保在最上层 */
  }

  /* 添加覆盖层，阻挡其他元素的点击事件影响侧边栏 */
  .sidebar-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: rgba(0, 0, 0, 0.15);
    z-index: 999; /* 低于侧边栏但高于其他内容 */
    display: none; /* 默认隐藏 */
  }

  /* 当侧边栏显示时显示覆盖层 */
  .chat-sidebar:not(.sidebar-collapsed) + .chat-content .sidebar-overlay {
    display: block;
  }

  .chat-content {
    width: 100%;
    position: relative;
    z-index: 1;
  }

  /* 确保知识图谱容器不会影响侧边栏 */
  .kg-graph-container {
    transition: none; /* 移除过渡效果，避免与侧边栏冲突 */
  }

  /* 知识图谱展开时确保内容不超出右侧边界 */
  .kg-graph-placeholder {
    max-width: 100%;
    overflow-x: auto;
  }
}

/* 添加头部左侧区域样式 */
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 折叠按钮样式 */
.fold-button {
  cursor: pointer;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.2s;
}

.fold-button:hover {
  background-color: #f0f9f6;
}

/* 修改右侧聊天内容区域 */
.chat-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background-color: #fff;
}

/* 添加聊天头部 */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 1px solid #eee;
  background-color: #fff;
  height: 42px;
  flex-shrink: 0;
}

.header-title {
  font-size: 15px;
  font-weight: 500;
  color: #333;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #eee;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 10px 0;
}

.chat-item {
  padding: 12px 16px;
  cursor: pointer;
  border-radius: 8px;
  margin: 0 8px 8px 8px;
  background-color: #fff;
  border: 1px solid #eee;
  transition: all 0.3s;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.chat-item:hover {
  background-color: #f9f9f9;
  transform: translateY(-2px);
}

.chat-item.active {
  background-color: #e6f7f5;
  border-color: #009688;
  box-shadow: 0 2px 8px rgba(0,150,136,0.1);
}

.chat-item-content {
  flex: 1;
  overflow: hidden;
}

.chat-title {
  font-size: 14px;
  color: #333;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-time {
  font-size: 12px;
  color: #999;
}

.chat-actions {
  opacity: 0;
  transition: opacity 0.3s;
}

.chat-item:hover .chat-actions {
  opacity: 1;
}

.delete-icon {
  cursor: pointer;
}

/* 修改消息包装器，使其占满剩余空间 */
.message-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

/* 修改消息容器样式，增加聊天区域空间 */
.message-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 20px 10px 20px;
  display: flex;
  flex-direction: column;
  position: relative;
}

.message-item {
  display: flex;
  margin-bottom: 24px;
  position: relative;
  max-width: 100%;
}

/* 修改输入容器，固定在底部 */
.input-container {
  padding: 8px 16px;
  background-color: #fff;
  border-top: 1px solid #eee;
  position: relative;
  flex-shrink: 0;
  z-index: 5;
  max-height: 90px;
  display: flex;
  flex-direction: column;
}

.input-textarea-wrapper {
  width: 100%;
  position: relative;
}

/* 恢复按钮组样式 */
.button-group {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 2px;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hint {
  color: #999;
  font-size: 12px;
}

/* 恢复消息样式 */
.user-message {
  flex-direction: row-reverse;
}

.message-avatar {
  margin: 0 12px;
}

.message-content {
  max-width: 95%;
  position: relative;
}

.user-message .message-content {
  background-color: #e6f7f5;
  border-radius: 18px 4px 18px 18px;
  padding: 16px 20px;
  box-shadow: 0 1px 2px rgba(0,150,136,0.1);
  max-width: 95%;
}

.ai-message .message-content {
  background-color: #f5f5f5;
  border-radius: 4px 18px 18px 18px;
  padding: 16px 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  max-width: 70%;
  width: 100%;
  box-sizing: border-box;
}

/* 消息动画 */
.message-item {
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 思考过程样式 */
.thinking-section {
  margin: 12px 0 16px 0;
  border-left: 3px solid #009688;
  position: relative;
}

.thinking-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background-color: #edf7f5;
  border-top: 1px solid #d1e7dd;
  border-right: 1px solid #d1e7dd;
  border-bottom: 1px solid #d1e7dd;
  margin-bottom: 0;
}

.thinking-header-left {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  color: #009688;
}

.thinking-header-right {
  display: flex;
  align-items: center;
}

.thinking-tag {
  background-color: #009688;
  color: white;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
}

.thinking-content {
  background-color: #f8fbfa;
  padding: 12px 16px;
  margin-top: 0;
  border-right: 1px solid #d1e7dd;
  border-bottom: 1px solid #d1e7dd;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.thinking-content h4 {
  margin: 12px 0 8px 0;
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.thinking-content h4:first-child {
  margin-top: 0;
}

.thinking-content h5 {
  margin: 10px 0 6px 0;
  font-size: 14px;
  font-weight: 500;
  color: #444;
}

.thinking-content .list-item {
  margin: 4px 0 4px 12px;
  position: relative;
}

.thinking-content strong {
  font-weight: 600;
  color: #0a5d52;
}

.thinking-code {
  background-color: #f0f0f0;
  padding: 12px;
  border-radius: 4px;
  margin: 10px 0;
  overflow-x: auto;
  font-family: Consolas, Monaco, 'Andale Mono', monospace;
  font-size: 13px;
  line-height: 1.4;
}

/* 思考中动画 */
.thinking-bubble {
  background-color: #f5f5f5;
  border-radius: 4px 18px 18px 18px;
  padding: 12px 16px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.thinking-animation {
  position: relative;
  display: inline-block;
}

.dot-animation::after {
  content: "...";
  display: inline-block;
  overflow: hidden;
  vertical-align: bottom;
  animation: dotAnimation 1.5s infinite steps(4, end);
  width: 0;
}

@keyframes dotAnimation {
  0% { width: 0; }
  25% { width: 0.25em; }
  50% { width: 0.5em; }
  75% { width: 0.75em; }
  100% { width: 1em; }
}

/* 消息底部样式 */
.message-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.message-actions {
  display: flex;
  gap: 8px;
}

.action-icon {
  cursor: pointer;
  opacity: 0.6;
  transition: all 0.3s;
}

.action-icon:hover {
  opacity: 1;
  transform: scale(1.2);
}

.message-time {
  font-size: 12px;
  color: #999;
}

/* 知识图谱相关样式 */
.kg-reference {
  margin-left: 0 !important;
  width: 100%;
  margin-top: 12px;
}

.kg-context {
  margin-top: 10px;
  padding: 16px;
  background-color: #f0f9f6;
  border-radius: 12px;
  border: 1px solid #e0e0e0;
  animation: fadeIn 0.3s ease;
  width: 100%;  /* 确保占满宽度 */
  box-sizing: border-box;
}

.ai-notice {
  margin: 10px 0;
  padding: 8px 12px;
  background-color: #fff3e0;
  border-radius: 4px;
  border-left: 3px solid #FF9800;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #E65100;
}

/* 知识图谱可视化样式 */
.kg-visualization {
  margin-top: 15px;
  border-radius: 8px;
  background-color: #f7f9fa;
  overflow: hidden;
  position: relative;
  z-index: 1;
  pointer-events: auto; /* 确保图谱区域有自己的点击事件 */
  isolation: isolate; /* 创建新的堆叠上下文 */
  width: 100%;
}

.kg-header {
  display: flex;
  align-items: center;
  padding: 10px 15px;
  background-color: #e8f4f4;
  cursor: pointer;
  font-weight: 500;
  color: #009688;
  pointer-events: auto; /* 确保点击事件独立 */
}

.kg-header span {
  margin-left: 8px;
}

.kg-graph-wrapper {
  padding: 15px;
  transition: all 0.3s ease;
  pointer-events: auto; /* 确保点击事件独立 */
  min-height: 400px; /* 确保最小高度 */
  height: 400px; /* 固定高度 */
  width: 100%;
  box-sizing: border-box;
}

.kg-graph-container {
  min-height: 300px;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto; /* 确保点击事件独立 */
}

.kg-graph-placeholder {
  min-height: 370px;
  height: 370px;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #fff;
  border-radius: 4px;
  border: 1px solid #e0e0e0;
  position: relative;
  z-index: 1;
  pointer-events: auto; /* 确保点击事件独立 */
}

.kg-graph {
  width: 100%;
  height: 100%;
  min-height: 370px;
  pointer-events: auto; /* 确保点击事件独立 */
}

@media screen and (min-width: 768px) {
  .kg-graph-wrapper {
    height: 340px;
    min-height: 340px;
    width: 100%;
  }

  .kg-graph-placeholder {
    min-height: 420px;
    height: 420px;
  }

  .kg-graph {
    min-height: 420px;
  }
}

@media screen and (min-width: 1200px) {
  .kg-graph-wrapper {
    height: 550px;
    min-height: 550px;
    width: 100%;
  }

  .kg-graph-placeholder {
    min-height: 520px;
    height: 520px;
  }

  .kg-graph {
    min-height: 520px;
  }
}

/* 确保移动设备上知识图谱不会影响侧边栏 */
@media (max-width: 767px) {
  .kg-visualization {
    overflow: visible;
  }

  .kg-graph-wrapper {
    padding: 10px;
  }

  .kg-graph-placeholder {
    max-width: 100%;
    overflow-x: auto;
  }
}

/* 实体卡片样式 */
.entity-card {
  background-color: #f7f9fa;
  border-radius: 6px;
  margin: 8px 0;
  padding: 8px 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  border: 1px solid #eee;
}

.entity-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.entity-card-title {
  display: flex;
  align-items: center;
  font-weight: 500;
  color: #009688;
  font-size: 13px;
}

.entity-card-title span {
  margin-left: 4px;
}

.entity-card-close {
  cursor: pointer;
  opacity: 0.6;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: all 0.2s;
}

.entity-card-close:hover {
  opacity: 1;
  background-color: #eee;
}

.entity-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.entity-tag {
  cursor: pointer;
  transition: transform 0.2s;
  font-size: 12px;
  padding: 1px 8px;
}

.entity-tag:hover {
  transform: scale(1.05);
}

/* 空聊天状态 */
.empty-chat {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}

.empty-icon {
  margin-bottom: 16px;
}

.empty-text {
  font-size: 16px;
  margin-bottom: 24px;
}

/* 快捷提示样式 */
.quick-prompts {
  width: 100%;
  max-width: 500px;
  padding: 16px;
  margin-top: 16px;
}

.prompt-title {
  margin-bottom: 12px;
  color: #666;
  font-size: 14px;
  text-align: center;
}

.prompt-items {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

.prompt-item {
  padding: 8px 16px;
  background-color: #f0f9f6;
  color: #009688;
  border-radius: 20px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.3s;
  border: 1px dashed #009688;
}

.prompt-item:hover {
  background-color: #009688;
  color: white;
  border-style: solid;
  transform: translateY(-2px);
}

/* 复制成功提示 */
.copy-success-tip {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background-color: rgba(0, 150, 136, 0.8);
  color: white;
  padding: 10px 20px;
  border-radius: 4px;
  font-size: 16px;
  animation: fadeInOut 2s ease;
  z-index: 1000;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

@keyframes fadeInOut {
  0% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
  15% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  80% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  100% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
}

/* Markdown样式 */
.markdown-body {
  line-height: 1.6;
  word-break: break-word;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin-bottom: 10px;
  width: 100%;
  overflow: auto;
}

.markdown-body :deep(table th),
.markdown-body :deep(table td) {
  padding: 6px 13px;
  border: 1px solid #dfe2e5;
  vertical-align: top;
  word-break: break-word;
}

.markdown-body :deep(table th) {
  background-color: #f2f8f6;
  font-weight: 600;
  text-align: left;
  color: #009688;
}

.markdown-body :deep(table tr) {
  background-color: #fff;
  border-top: 1px solid #c6cbd1;
}

.markdown-body :deep(table tr:nth-child(2n)) {
  background-color: #f6faf9;
}

/* 输入框相关样式 */
.clear-button {
  cursor: pointer;
  opacity: 0.6;
  transition: opacity 0.3s;
}

.clear-button:hover {
  opacity: 1;
}

/* 特定于表格的样式 */
.markdown-body :deep(table td:nth-child(5)) {
  font-weight: 500;
}

.markdown-body :deep(table td:last-child) {
  max-width: 350px;
}

/* 添加紧凑型输入框样式 */
.compact-textarea {
  font-size: 14px;
  line-height: 1.5;
}

.compact-textarea :deep(.layui-textarea) {
  padding: 6px 8px;
  padding-right: 45px; /* 为发送按钮留出空间 */
  min-height: 22px !important;
  line-height: 1.5;
  resize: none;
  border-radius: 20px;
  box-shadow: 0 1px 6px rgba(0,0,0,0.05);
  transition: all 0.3s;
}

.compact-textarea :deep(.layui-textarea:focus) {
  box-shadow: 0 2px 8px rgba(0,150,136,0.15);
}

/* 发送按钮样式 */
.send-button {
  position: absolute;
  right: 10px;
  bottom: 6px;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #f0f9f6;
  border-radius: 50%;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  z-index: 10;
}

.send-button:hover {
  background-color: #e0f2f1;
  transform: translateY(-2px);
  box-shadow: 0 3px 5px rgba(0,0,0,0.15);
}

.send-button.disabled {
  background-color: #f5f5f5;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.export-button {
  cursor: pointer;
  transition: all 0.3s;
}

.export-button:hover {
  transform: scale(1.2);
  color: #00b5a0;
}

/* ========== 页面整体美化 ========== */

/* 整体容器添加渐变背景 */
.inference-container {
  background: linear-gradient(135deg, #f5f7fa 0%, #e8f4f8 50%, #f0f7f4 100%);
}

/* 左侧边栏美化 */
.chat-sidebar {
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  border-right: 1px solid rgba(0, 150, 136, 0.1);
  box-shadow: 4px 0 20px rgba(0, 0, 0, 0.05);
}

/* 侧边栏头部 */
.sidebar-header {
  background: linear-gradient(135deg, #ffffff 0%, #f5f7fa 100%);
  padding: 20px 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}

.sidebar-header .layui-btn {
  background: #009688 !important;
  border: none;
  color: white !important;
  box-shadow: 0 2px 8px rgba(0, 150, 136, 0.3);
  transition: all 0.3s ease;
}

.sidebar-header .layui-btn:hover {
  background: #00796b !important;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 150, 136, 0.4);
}

/* 聊天项美化 */
.chat-item {
  background: #ffffff;
  border: 1px solid transparent;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  margin: 8px 12px;
  border-radius: 12px;
}

.chat-item:hover {
  background: #ffffff;
  border-color: rgba(0, 150, 136, 0.2);
  box-shadow: 0 4px 16px rgba(0, 150, 136, 0.12);
  transform: translateY(-2px);
}

.chat-item.active {
  background: linear-gradient(135deg, #e0f2f1 0%, #e8f5e9 100%);
  border-color: #009688;
  box-shadow: 0 4px 16px rgba(0, 150, 136, 0.2);
}

.chat-title {
  font-weight: 500;
  color: #2c3e50;
}

.chat-time {
  color: #90a4ae;
  font-size: 11px;
}

/* 右侧聊天区域 */
.chat-content {
  background: transparent;
}

/* 头部美化 */
.chat-header {
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(0, 150, 136, 0.1);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.03);
}

.header-title {
  font-weight: 600;
  color: #2c3e50;
  font-size: 16px;
}

/* 折叠按钮美化 */
.fold-button {
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  border-radius: 8px;
  width: 32px;
  height: 32px;
  transition: all 0.3s ease;
}

.fold-button:hover {
  background: linear-gradient(135deg, #b2dfdb 0%, #80cbc4 100%);
  transform: scale(1.1);
}

/* 消息容器 */
.message-container {
  padding: 24px 28px;
}

/* 用户消息气泡美化 */
.user-message .message-content {
  background: #ffffff;
  color: #333;
  border-radius: 20px 20px 4px 20px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1), 0 0 0 1px rgba(0, 150, 136, 0.15);
  padding: 14px 20px;
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(0, 150, 136, 0.2);
}

.user-message .message-content::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(0,150,136,0.03) 0%, transparent 50%);
  pointer-events: none;
}

.user-message .message-text {
  color: #333;
}

/* AI消息气泡美化 */
.ai-message .message-content {
  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
  border: 1px solid rgba(0, 150, 136, 0.1);
  border-radius: 20px 20px 20px 4px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
  padding: 16px 20px;
}

/* 头像美化（尺寸用 CSS 控制：组件的 size 只接受 xs/sm/md/lg 预设值） */
.message-avatar .layui-avatar {
  width: 40px;
  height: 40px;
  line-height: 36px;
  font-size: 14px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2), 0 0 0 3px rgba(255, 255, 255, 0.8), 0 0 0 5px rgba(0, 150, 136, 0.3);
  border: 2px solid white;
  transition: all 0.3s ease;
  font-weight: 600;
}

.message-avatar .layui-avatar:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25), 0 0 0 3px rgba(255, 255, 255, 0.9), 0 0 0 6px rgba(0, 150, 136, 0.4);
}

/* 用户头像特殊样式 */
.user-message .message-avatar .layui-avatar {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3), 0 0 0 3px rgba(255, 255, 255, 0.8), 0 0 0 5px rgba(102, 126, 234, 0.3);
}

.user-message .message-avatar .layui-avatar:hover {
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4), 0 0 0 3px rgba(255, 255, 255, 0.9), 0 0 0 6px rgba(102, 126, 234, 0.4);
}

/* AI头像特殊样式 */
.ai-message .message-avatar .layui-avatar {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%) !important;
  box-shadow: 0 4px 12px rgba(0, 150, 136, 0.3), 0 0 0 3px rgba(255, 255, 255, 0.8), 0 0 0 5px rgba(0, 150, 136, 0.3);
}

.ai-message .message-avatar .layui-avatar:hover {
  box-shadow: 0 6px 20px rgba(0, 150, 136, 0.4), 0 0 0 3px rgba(255, 255, 255, 0.9), 0 0 0 6px rgba(0, 150, 136, 0.4);
}

/* 输入区域美化 */
.input-container {
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(20px);
  border-top: 1px solid rgba(0, 150, 136, 0.1);
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.05);
  padding: 16px 24px;
}

/* 输入框美化 */
.compact-textarea :deep(.layui-textarea) {
  background: #f8fafc;
  border: 2px solid transparent;
  border-radius: 24px;
  padding: 12px 50px 12px 20px;
  font-size: 15px;
  transition: all 0.3s ease;
}

.compact-textarea :deep(.layui-textarea:focus) {
  background: #ffffff;
  border-color: #009688;
  box-shadow: 0 4px 20px rgba(0, 150, 136, 0.15);
}

/* 发送按钮美化 */
.send-button {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  width: 36px;
  height: 36px;
  right: 8px;
  bottom: 8px;
  box-shadow: 0 4px 12px rgba(0, 150, 136, 0.4);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.send-button:hover {
  background: linear-gradient(135deg, #00897b 0%, #1de9b6 100%);
  transform: translateY(-2px) scale(1.05);
  box-shadow: 0 6px 20px rgba(0, 150, 136, 0.5);
}

.send-button :deep(.layui-icon) {
  color: white !important;
}

/* 清空按钮美化 */
.clear-button {
  background: linear-gradient(135deg, #f5f5f5 0%, #eeeeee 100%);
  padding: 6px;
  border-radius: 50%;
  transition: all 0.3s ease;
}

.clear-button:hover {
  background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
  color: #e53935 !important;
  transform: rotate(90deg);
}

/* 导出按钮美化 */
.export-button {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  padding: 8px;
  border-radius: 50%;
  transition: all 0.3s ease;
}

.export-button:hover {
  background: linear-gradient(135deg, #2196f3 0%, #64b5f6 100%);
  color: white !important;
  transform: scale(1.1);
}

/* 实体卡片美化 */
.entity-card {
  background: linear-gradient(135deg, #ffffff 0%, #f5f7fa 100%);
  border: 1px solid rgba(0, 150, 136, 0.15);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  padding: 16px;
  margin: 16px 20px;
  animation: slideUp 0.4s ease;
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.entity-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #009688;
}

.entity-tag {
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  border: 1px solid rgba(0, 150, 136, 0.2);
  border-radius: 20px;
  padding: 6px 14px;
  font-size: 13px;
  transition: all 0.3s ease;
}

.entity-tag:hover {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  color: white;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 150, 136, 0.3);
}

/* 思考过程美化 */
.thinking-section {
  border-left: none;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
  margin-bottom: 20px;
}

.thinking-header {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none;
  padding: 12px 16px;
}

.thinking-header-left {
  color: #1565c0;
  font-weight: 600;
}

.thinking-tag {
  background: linear-gradient(135deg, #2196f3 0%, #64b5f6 100%);
  box-shadow: 0 2px 8px rgba(33, 150, 243, 0.3);
}

.thinking-content {
  background: linear-gradient(180deg, #fafafa 0%, #f5f5f5 100%);
  border: 1px solid rgba(0, 0, 0, 0.05);
  border-top: none;
  padding: 16px 20px;
}

/* 知识图谱可视化区域美化 */
.kg-visualization {
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(0, 150, 136, 0.1);
  margin-top: 20px;
}

.kg-header {
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  padding: 14px 20px;
  font-weight: 600;
  transition: all 0.3s ease;
}

.kg-header:hover {
  background: linear-gradient(135deg, #b2dfdb 0%, #80cbc4 100%);
}

.kg-graph-wrapper {
  background: linear-gradient(135deg, #fafafa 0%, #f5f5f5 100%);
  padding: 20px;
}

.kg-graph-placeholder {
  border-radius: 12px;
  border: 2px dashed rgba(0, 150, 136, 0.2);
  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
}

/* 知识图谱参考信息按钮美化 */
.kg-reference .layui-btn {
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  border: 1px solid rgba(0, 150, 136, 0.2);
  border-radius: 20px;
  padding: 8px 16px;
  font-size: 13px;
  transition: all 0.3s ease;
  color: #009688;
}

.kg-reference .layui-btn:hover {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  border-color: transparent;
  color: white;
}

/* 空状态美化 */
.empty-chat {
  background: linear-gradient(135deg, rgba(255,255,255,0.8) 0%, rgba(248,250,252,0.8) 100%);
  border-radius: 24px;
  padding: 60px 40px;
  margin: 40px;
  backdrop-filter: blur(10px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
}

.empty-icon {
  background: linear-gradient(135deg, #e0f2f1 0%, #b2dfdb 100%);
  width: 100px;
  height: 100px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 24px;
  box-shadow: 0 8px 24px rgba(0, 150, 136, 0.2);
}

.empty-text {
  font-size: 20px;
  font-weight: 600;
  color: #2c3e50;
  margin-bottom: 32px;
}

/* 快捷提示美化 */
.quick-prompts {
  background: linear-gradient(135deg, rgba(255,255,255,0.9) 0%, rgba(248,250,252,0.9) 100%);
  border-radius: 20px;
  padding: 28px 32px;
  backdrop-filter: blur(10px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
}

.prompt-title {
  font-size: 15px;
  font-weight: 600;
  color: #546e7a;
  margin-bottom: 16px;
}

.prompt-item {
  background: linear-gradient(135deg, #ffffff 0%, #f5f7fa 100%);
  border: 1px solid rgba(0, 150, 136, 0.15);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  padding: 12px 20px;
  font-size: 14px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.prompt-item:hover {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  border-color: transparent;
  color: white;
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(0, 150, 136, 0.3);
}

/* 无知识图谱提示美化 */
.no-kg-info-notice {
  background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%);
  border: 1px solid rgba(255, 183, 77, 0.3);
  border-radius: 12px;
  padding: 12px 16px;
  margin-top: 16px;
  font-size: 13px;
  color: #e65100;
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: 0 4px 12px rgba(255, 183, 77, 0.15);
}

/* AI通知美化 */
.ai-notice {
  margin: 12px 0;
  padding: 12px 16px;
  background-color: #fff3e0;
  border-radius: 8px;
  border-left: 4px solid #FF9800;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #E65100;
  width: 100%;
  box-sizing: border-box;
}

/* 消息底部操作区美化 */
.message-footer {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  width: 100%;
}

.message-footer > .message-actions,
.message-footer > .message-time {
  align-self: flex-end;
}

.message-time {
  background: rgba(0, 0, 0, 0.04);
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  color: #78909c;
}

/* 复制成功提示美化 */
.copy-success-tip,
.export-success-tip {
  background: linear-gradient(135deg, #009688 0%, #26a69a 100%);
  border-radius: 12px;
  padding: 14px 28px;
  font-size: 15px;
  font-weight: 500;
  box-shadow: 0 8px 24px rgba(0, 150, 136, 0.4);
}

/* 滚动条美化 */
.message-container::-webkit-scrollbar,
.chat-history::-webkit-scrollbar {
  width: 6px;
}

.message-container::-webkit-scrollbar-track,
.chat-history::-webkit-scrollbar-track {
  background: transparent;
}

.message-container::-webkit-scrollbar-thumb,
.chat-history::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, #b2dfdb 0%, #80cbc4 100%);
  border-radius: 3px;
}

.message-container::-webkit-scrollbar-thumb:hover,
.chat-history::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, #80cbc4 0%, #4db6ac 100%);
}

/* Markdown内容美化 */
.markdown-body {
  color: #37474f;
  line-height: 1.8;
}

.markdown-body :deep(p) {
  margin-bottom: 12px;
}

.markdown-body :deep(code) {
  background: linear-gradient(135deg, #f5f5f5 0%, #eeeeee 100%);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 0.9em;
  color: #e53935;
}

.markdown-body :deep(pre) {
  background: linear-gradient(135deg, #263238 0%, #1c2529 100%);
  border-radius: 12px;
  padding: 16px;
  overflow-x: auto;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

.markdown-body :deep(pre code) {
  background: transparent;
  color: #aed581;
  padding: 0;
}

.markdown-body :deep(blockquote) {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border-left: 4px solid #2196f3;
  border-radius: 0 8px 8px 0;
  padding: 12px 16px;
  margin: 16px 0;
}

.markdown-body :deep(ul), 
.markdown-body :deep(ol) {
  padding-left: 24px;
  margin: 12px 0;
}

.markdown-body :deep(li) {
  margin: 8px 0;
}

.markdown-body :deep(a) {
  color: #009688;
  text-decoration: none;
  border-bottom: 1px dashed #009688;
  transition: all 0.3s ease;
}

.markdown-body :deep(a:hover) {
  color: #00796b;
  border-bottom-style: solid;
}

/* 响应式优化 */
@media (max-width: 768px) {
  .chat-sidebar {
    box-shadow: 4px 0 30px rgba(0, 0, 0, 0.15);
  }
  
  .message-content {
    max-width: 90% !important;
  }
  
  .entity-card {
    margin: 12px;
  }
  
  .empty-chat {
    margin: 20px;
    padding: 40px 24px;
  }
}

/* 加载动画美化 */
.thinking-bubble {
  background: linear-gradient(135deg, #f5f7fa 0%, #e3f2fd 100%);
  border: 1px solid rgba(33, 150, 243, 0.15);
}

.dot-animation::after {
  color: #2196f3;
}

/* 思考中文字样式 */
.thinking-animation {
  color: #546e7a;
  font-weight: 500;
}

/* 固定在主内容区内布局，避免整页滚动，只保留消息列表内部滚动。 */
.inference-container {
  height: 100%;
  min-height: 0;
}

.chat-sidebar,
.chat-content,
.message-wrapper {
  min-height: 0;
}

.chat-sidebar {
  width: 230px;
  flex: 0 0 230px;
}

.chat-sidebar.sidebar-collapsed {
  width: 0 !important;
  flex: 0 0 0 !important;
  padding: 0 !important;
  border-right: none !important;
  overflow: hidden !important;
}

.chat-header {
  height: 44px;
  padding: 6px 14px;
}

.sidebar-header {
  padding: 12px 14px;
}

.new-chat-button {
  width: 100%;
  height: 42px;
  border: none;
  border-radius: 8px;
  background: #009688;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 150, 136, 0.28);
  transition: all 0.2s ease;
}

.new-chat-button:hover {
  background: #00796b;
  transform: translateY(-1px);
}

.chat-history {
  padding: 8px 0;
}

.chat-item {
  margin: 6px 10px;
  padding: 10px 12px;
}

.message-container {
  padding: 14px 20px 8px;
}

.message-item {
  margin-bottom: 16px;
}

.message-avatar {
  margin: 0 8px;
}

.ai-message .message-content,
.user-message .message-content {
  padding: 12px 16px;
}

.empty-chat {
  margin: 16px;
  padding: 28px 30px;
}

.empty-icon {
  width: 72px;
  height: 72px;
  margin-bottom: 16px;
}

.empty-text {
  font-size: 17px;
  margin-bottom: 18px;
}

.quick-prompts {
  padding: 16px 20px;
}

.prompt-items {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.prompt-item {
  padding: 10px 12px;
}

.input-container {
  padding: 10px 18px;
}

@media (max-width: 1100px) {
  .prompt-items {
    grid-template-columns: 1fr;
  }
}

.kg-node-detail {
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.kg-node-detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.kg-node-detail-header h3 {
  margin: 6px 0 0;
  font-size: 22px;
  color: #1f2937;
}

.kg-node-detail-type {
  font-size: 12px;
  color: #009688;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.kg-node-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.kg-node-chip {
  padding: 12px 14px;
  border-radius: 12px;
  background: linear-gradient(135deg, #f8fafc 0%, #eefaf8 100%);
  border: 1px solid rgba(0, 150, 136, 0.12);
}

.kg-node-chip span {
  display: block;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}

.kg-node-chip strong {
  color: #1f2937;
  line-height: 1.5;
}

.kg-node-section {
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  padding: 14px 16px;
  background: #fff;
}

.kg-node-section.emphasis {
  background: linear-gradient(135deg, #fffaf0 0%, #f4fffd 100%);
  border-color: rgba(0, 150, 136, 0.18);
}

.kg-node-section-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f766e;
  margin-bottom: 10px;
}

.kg-node-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.kg-node-section-count {
  font-size: 13px;
  color: #b7791f;
}

.kg-node-long-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: #374151;
}

.kg-node-field-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg-node-field {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  align-items: start;
}

.kg-node-field label {
  color: #6b7280;
  font-size: 13px;
}

.kg-node-field div {
  color: #1f2937;
  line-height: 1.7;
  white-space: pre-wrap;
}

.kg-structured-relations {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kg-structured-item {
  padding: 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, #fafafa 0%, #f5f8fb 100%);
  border: 1px solid rgba(194, 155, 107, 0.18);
}

.kg-structured-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.kg-relation-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 68px;
  padding: 10px 16px;
  border-radius: 999px;
  background: linear-gradient(135deg, #b86d36 0%, #9f4e1e 100%);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.kg-relation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.kg-relation-meta span {
  padding: 6px 12px;
  border-radius: 999px;
  background: #f5e8cf;
  color: #a16207;
  font-size: 12px;
}

.kg-structured-row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 12px;
  align-items: start;
  margin-top: 10px;
}

.kg-structured-row label {
  color: #7c8798;
  font-size: 13px;
}

.kg-target-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.kg-target-tag {
  margin-right: 0;
  border-radius: 10px;
}

.kg-evidence-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg-evidence-card {
  padding: 14px 16px;
  border-left: 4px solid #d29a3a;
  border-radius: 12px;
  background: #fff;
  color: #1f2937;
  line-height: 1.8;
  white-space: pre-wrap;
}

@media (max-width: 768px) {
  .kg-node-summary {
    grid-template-columns: 1fr;
  }

  .kg-node-field {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .kg-structured-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .kg-structured-head {
    flex-direction: column;
    align-items: flex-start;
  }
}

</style>
