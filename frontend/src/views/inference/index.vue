<!--
  智能问答页面（历史问答助手）

  结构：模板按 UI 块拆成四个子组件，逻辑分给三个模块，页面只留状态与编排——
    - components/SessionSidebar.vue   会话列表（新建/切换/删除）
    - components/ChatMessages.vue     消息列表、思考过程、图谱展开、引用信息（自带滚动）
    - components/ChatInput.vue        底部输入区（Enter 发送）
    - components/KgNodeDrawer.vue     图谱节点详情抽屉（lay-layer 会 teleport，故自带样式）
    - composables/useChatHistory.ts   问答记录与按账号隔离的持久化（含自动合并落盘）
    - composables/useInferenceSidebar.ts 侧边栏状态与图谱展开保护
    - api/module/inference.ts         SSE 客户端与帧协议（含把帧应用到消息）
    - utils/inference-export.ts       导出 Markdown（纯函数，可直接单测）

  样式在 index.css，按 `.inference-container` 命名空间生效（不是 scoped）：scoped 只会给本模板
  的元素打 data-v 属性，子组件内部匹配不到。迁移脚本见 scripts/namespace-inference-css.mjs。
--><template>
  <div class="inference-container">
    <!-- 左侧聊天列表 -->
    <session-sidebar
      :sessions="chatHistory"
      :active-index="currentChatIndex"
      :collapsed="!showSidebar"
      @create="createNewChat"
      @select="switchChat"
      @remove="deleteChat"
    />

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
        <!-- 消息列表（滚动由子组件负责，见 ChatMessages 的 defineExpose） -->
        <chat-messages
          ref="chatMessagesRef"
          :messages="currentChat.messages"
          :loading="loading"
          :is-first-load="isFirstLoad"
          :quick-prompts="quickPrompts"
          @copy="copyMessage"
          @ask="useQuickPrompt"
          @node-click="handleKgNodeClick"
          @kg-toggle="onKgToggle"
        />

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

      <!-- 输入框和按钮 -->
      <chat-input
        v-model="currentQuery"
        :loading="loading"
        @send="handleQuery"
        @clear="clearInput"
      />
    </div>

    <!-- 节点关联信息抽屉 -->
    <kg-node-drawer v-model="showNodeDetailDrawer" :node="selectedKgNode" />

    <div v-if="showCopySuccess" class="copy-success-tip">
      复制成功!
    </div>
    <div v-if="showExportSuccess" class="export-success-tip">
      导出成功!
    </div>
  </div>
</template>


<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { useUserStore } from '../../store/user';
import { layer } from '@layui/layui-vue';
import { normalizeType } from '../../utils/knowledge';
import { copyText } from '../../utils/clipboard';
import { buildConversationMarkdown, downloadMarkdown, exportFileName } from '../../utils/inference-export';
import { applyInferenceFrame, streamInference } from '../../api/module/inference';
import { useChatHistory } from '../../composables/useChatHistory';
import { useInferenceSidebar } from '../../composables/useInferenceSidebar';
import type { ChatMessage, KgNodeDetail } from '../../types/inference';
// 按 UI 块拆出的子组件（见文件头）
import SessionSidebar from './components/SessionSidebar.vue';
import ChatMessages from './components/ChatMessages.vue';
import ChatInput from './components/ChatInput.vue';
import KgNodeDrawer from './components/KgNodeDrawer.vue';

// 用户store（用于获取token与账号 id）
const userStore = useUserStore();

// 本页面所属的账号 id：挂载时钉住，之后**不跟随 userInfo 变化**。
// 登出（BasicLayout.logOut）与 token 过期（api/http.ts 的 handleUnauthorized）都是先
// clearSession() 清空 userInfo、再跳登录页，随后本组件卸载并 flush 历史——若此刻才去读
// userInfo.id，拿到的是 undefined，key 会退回全局 'chatHistory'，把这个账号的整份记录
// 复制进公共桶，之后任何账号都能读到。钉住 uid 后，"落到自己的桶"与清凭据的先后顺序无关。
const scopedUid = ref<string | number | undefined>(userStore.userInfo?.id);

/** 已登录但拿不到账号 id（token 在、userinfo 未返回或失败）时，既不读也不写公共桶：
 *  那份数据不知道属于谁，读它会显示别人的记录，写它会污染别人的桶。只有未登录
 *  （独立访问该页面）才沿用全局 key——那是改造前的语义。 */
function isScopedAccessBlocked() {
  return !scopedUid.value && !!userStore.token;
}

// 会话列表与按账号隔离的持久化（含自动合并落盘与卸载落盘），见 composables/useChatHistory
const history = useChatHistory({ uid: () => scopedUid.value, isAccessBlocked: isScopedAccessBlocked });
const chatHistory = history.chats;
const currentChatIndex = history.currentIndex;
const currentChat = history.current;

// 进行中的问答流控制器（见 handleQuery / onBeforeUnmount）
let streamController: AbortController | null = null;

// 侧边栏（展开/收起、resize 保持、图谱展开保护），见 composables/useInferenceSidebar
const sidebar = useInferenceSidebar();
const isMobileView = sidebar.isMobileView;
const showSidebar = sidebar.showSidebar;
const toggleSidebar = sidebar.toggle;
const onKgToggle = sidebar.onKgToggle;

// 状态变量
const currentQuery = ref('');
const loading = ref(false);
const isFirstLoad = ref(true);
const showCopySuccess = ref(false);
const showExportSuccess = ref(false);

// 快捷提示
const quickPrompts = [
  "淝水之战的主战场是哪里？",
  "谁是赤壁之战的主要指挥者？",
  "长平之战的结果如何？"
];

// 控制实体卡片是否显示
const showEntityCard = ref(true);
const showNodeDetailDrawer = ref(false);
const selectedKgNode = ref<KgNodeDetail | null>(null);

// 消息列表子组件的引用：滚动由它自己管（页面不再直接操作 DOM）
const chatMessagesRef = ref<InstanceType<typeof ChatMessages> | null>(null);

// 使用快捷提示（只填进输入框，不直接发送）
function useQuickPrompt(prompt: string) {
  currentQuery.value = prompt;
}

// 复制消息
async function copyMessage(content: string) {
  if (await copyText(content)) {
    showCopySuccess.value = true;
    setTimeout(() => {
      showCopySuccess.value = false;
    }, 2000);
  }
}

// 清空输入框
function clearInput() {
  currentQuery.value = '';
}

// 创建新聊天
function createNewChat() {
  streamController?.abort();
  history.create();
  currentQuery.value = '';
  showEntityCard.value = false;
  chatMessagesRef.value?.scrollToTop();
}

// 切换聊天
function switchChat(index: number) {
  history.switchTo(index);
}

// 删除聊天
function deleteChat(index: number) {
  history.remove(index);
}

// 滚动到底部（交给消息列表子组件）
function scrollToBottom() {
  chatMessagesRef.value?.scrollToBottom();
}

// 提交查询
async function handleQuery() {
  if (!currentQuery.value.trim() || loading.value) {
    return;
  }

  const userMessage: ChatMessage = {
    role: 'user',
    content: currentQuery.value,
    time: Date.now(),
    fromKg: true
  };
  currentChat.value.messages.push(userMessage);
  currentChat.value.lastTime = Date.now();

  // 如果是第一条消息，更新聊天标题
  if (currentChat.value.messages.length === 1) {
    history.updateTitle(currentChatIndex.value, userMessage.content);
  }

  const queryText = currentQuery.value;
  currentQuery.value = '';
  loading.value = true;

  scrollToBottom();

  // 创建空的AI消息，用于逐步填充内容
  const aiMessage: ChatMessage = {
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
    // 每次提问前中断上一次未结束的流：否则切换会话/离开页面后，
    // 旧流仍会继续往已经没人看的 aiMessage 上写内容
    streamController?.abort();
    streamController = new AbortController();

    await streamInference(queryText, userStore.token || '', {
      // 每来一帧就应用到这条消息上（滚动由监听消息长度的 watch 负责）
      onFrame: (frame) => {
        applyInferenceFrame(aiMessage, frame);
        scrollToBottom();
      },
      signal: streamController.signal,
    });

    // 流结束后的清理工作
    if (loading.value) {
      loading.value = false;
      // 确保消息时间戳更新
      aiMessage.time = Date.now();
      currentChat.value.lastTime = Date.now();
    }
  } catch (error) {
    if ((error as { name?: string })?.name === 'AbortError') {
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

// 监听消息变化，自动滚动到底部
watch(() => currentChat.value.messages.length, () => {
  scrollToBottom();
});

// 监听聊天切换
watch(currentChatIndex, () => {
  scrollToBottom();
});

// 组件挂载：拿到账号 id、读本账号的历史、复位首屏加载态
onMounted(async () => {
  // 先确保拿到账号 id 再读历史：否则会退回共享 key，隔离失效（见 store.ensureUserInfo）
  await userStore.ensureUserInfo();
  // 钉住本次挂载所属的账号：此后 userInfo 被 clearSession() 清空也不影响落盘去向
  scopedUid.value = userStore.userInfo?.id;
  history.load();
  // 读本地存储是同步的：保留一个短延时，让"正在加载聊天记录"有机会显示（与原实现一致）。
  // 这个标志不清会一直停在"正在加载"，看不到"开始新的对话"与常用提示。
  window.setTimeout(() => { isFirstLoad.value = false; }, 500);
  scrollToBottom();
});

// 组件卸载时中断进行中的问答流
// （历史落盘由 useChatHistory 的卸载钩子负责，侧边栏的 resize 监听由 useInferenceSidebar 负责）
onBeforeUnmount(() => {
  streamController?.abort();
});

// 导出对话内容到Markdown
function exportToMarkdown() {
  if (currentChat.value.messages.length === 0) {
    layer.msg('当前对话为空，无法导出', { icon: 0 });
    return;
  }

  try {
    const markdownContent = buildConversationMarkdown(currentChat.value);
    downloadMarkdown(markdownContent, exportFileName(currentChat.value.title));

    // 显示成功消息
    showExportSuccess.value = true;
    setTimeout(() => {
      showExportSuccess.value = false;
    }, 2000);
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

// 图谱节点详情：统一类型写法（图谱数据里的 type 可能是别名）
function normalizeKgNodeDetail(node: Record<string, unknown>): KgNodeDetail {
  const rawType = String(node.type ?? '');
  return {
    ...node,
    type: normalizeType(rawType.trim()) || rawType
  };
}

function handleKgNodeClick(node: Record<string, unknown>) {
  selectedKgNode.value = normalizeKgNodeDetail(node);
  showNodeDetailDrawer.value = true;
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

<style src="./index.css"></style>
