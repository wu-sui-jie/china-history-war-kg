<!--
  RAG 智能问答页

  功能: 以 iframe 嵌入独立的 RAG 问答服务（图谱 + 文本双通道检索、答案带引用证据）。
  并入方式: 同源子路径反代 /rag/ → RAG 服务（FastAPI），页面与接口都挂在该前缀下，
  所以这里不需要跨域配置；开发由 vite.config.ts 的 /rag 代理承担，生产由 nginx 分流。
  详见 docs/集成与入口约定.md。
-->
<template>
  <div class="rag-page">
    <div class="page-header">
      <div>
        <h1>RAG 智能问答</h1>
        <p>图谱与文本双通道检索，回答带引用证据与知识面板。由独立的 RAG 服务提供，本页通过 <code>/rag</code> 反代嵌入。</p>
      </div>
      <div class="header-actions">
        <lay-button @click="openInNewTab">在新窗口打开</lay-button>
        <lay-button type="primary" @click="reloadFrame">重新加载</lay-button>
      </div>
    </div>

    <div class="frame-card">
      <!-- sandbox：限制弹窗、顶层导航、下载等越权动作。
           注意这**不是安全隔离**：RAG 页面与主应用同源，`allow-scripts` + `allow-same-origin`
           组合下 iframe 内脚本可以直接访问父页面（也能移除自身 sandbox 属性，浏览器规范明示）。
           真正的隔离要靠把 RAG 部署到独立域名，或至少在反代层给 /rag/ 加认证。 -->
      <iframe
        :key="frameKey"
        ref="frameRef"
        class="rag-frame"
        :src="ragBase"
        title="RAG 智能问答"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
        referrerpolicy="no-referrer"
        @load="onFrameLoad"
      ></iframe>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import config from '../../config'
import { useUserStore } from '../../store/user'

const ragBase = config.ragBase
const frameKey = ref(0)
const frameRef = ref<HTMLIFrameElement | null>(null)
const userStore = useUserStore()

/** 换 key 重建 iframe 来触发重新加载：不碰 contentWindow，避免同源判定带来的限制。 */
const reloadFrame = () => {
  frameKey.value += 1
}

/**
 * 把当前账号告知 iframe 里的 RAG 前端（问题二方案 A）。
 *
 * 为什么需要：RAG 是独立服务、没有用户体系，会话历史存在它自己的 localStorage 里，
 * key 全局唯一——同一浏览器上任何账号打开 RAG 看到的都是同一份记录。
 * 主应用这边知道"现在是谁"，就把账号 id 传进去，RAG 按 uid 分 key 存。
 *
 * 用 postMessage 而不是拼在 iframe URL 上：URL 会进浏览器历史与各级访问日志。
 * targetOrigin 用 `/`（= 只发给同源文档，见 HTML 规范）——RAG 与主应用同源，
 * 换成独立域名部署时这里要同步改成那一个源。
 *
 * 边界（如实说）：这只解决"串记录"，不防"冒充"——同源之下，懂控制台的账号可以改 uid
 * 去看别人的记录。公网部署需要方案 B（传 JWT + RAG 服务端验签）。
 */
const postUserScope = () => {
  const frame = frameRef.value
  const uid = userStore.userInfo?.id
  if (!frame?.contentWindow || uid === undefined || uid === null) return
  frame.contentWindow.postMessage({ type: 'cw-user', uid: String(uid) }, '/')
}

/** iframe 每次 load 后重发：RAG 可能比本页晚拿到账号，或自身刚被重建。 */
const onFrameLoad = async () => {
  await userStore.ensureUserInfo()
  postUserScope()
}

// 账号变化（换号登录）时重建 iframe 并重发：RAG 前端按新 uid 重新读它自己的存储
watch(() => userStore.userInfo?.id, () => {
  reloadFrame()
})

onMounted(async () => {
  await userStore.ensureUserInfo()
})

const openInNewTab = () => {
  window.open(ragBase, '_blank', 'noopener')
}
</script>

<style scoped>
.rag-page {
  padding: 20px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at top left, rgba(139, 30, 35, 0.12), transparent 24%),
    linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 18px;
  margin-bottom: 16px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(74, 54, 24, 0.08);
}

.page-header h1 {
  margin: 0 0 6px;
  font-size: 20px;
  color: #3c2f1c;
}

.page-header p {
  margin: 0;
  color: #7a6a52;
  font-size: 13px;
  line-height: 1.6;
}

.page-header code {
  padding: 1px 5px;
  border-radius: 6px;
  background: rgba(191, 160, 106, 0.16);
  font-size: 12px;
}

.header-actions {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.frame-card {
  flex: 1;
  /* 兜底：父级高度意外解析为 auto 时，iframe 的 100% 会塌成 0，给一个可视高度下限 */
  min-height: 520px;
  padding: 0;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(74, 54, 24, 0.08);
}

.rag-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}
</style>
