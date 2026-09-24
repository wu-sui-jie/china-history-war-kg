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
    <lay-layer v-model="showNodeDetailDrawer" :title="'节点关联信息'" :shade="true" :area="['520px', '88vh']">
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
import { layer } from '@layui/layui-vue';
// 导入知识图谱组件
import KgGraph from './components/KgGraph.vue';
import { fieldLabel, groupRelationAttributes, nodeDisplayName, normalizeType, typeLabel } from '../../utils/knowledge';
import { formatChatTime } from '../../utils/date';
// Markdown / 图谱上下文的渲染与净化（S3-5 第一步已抽成独立模块，见其文件头）
import { renderKgContextMarkdown, renderMarkdown, renderThinkingContent } from '../../utils/inference-render';

// 用户store（用于获取token）
const userStore = useUserStore();

// 进行中的问答流控制器（见 handleQuery / onBeforeUnmount）
let streamController: AbortController | null = null;


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

function exportToMarkdown() {
  if (currentChat.value.messages.length === 0) {
    layer.msg('当前对话为空，无法导出', { icon: 0 });
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
    layer.msg(`导出失败: ${error instanceof Error ? error.message : String(error)}`, { icon: 2 });
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

<style scoped src="./index.css"></style>
