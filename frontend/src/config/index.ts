/**
 * 全局配置文件
 */

export default {
  // API 基础地址：同源相对路径。
  // 开发环境由 vite 代理 /api → 127.0.0.1:5000、/rag → 127.0.0.1:8000；
  // 生产由 nginx 按路径分流（见 docs/集成与入口约定.md）。
  // 不再拼 `http://hostname:5000`：那样 axios 直连后端端口、绕过 vite 代理，
  // HTTPS 部署时还会因混合内容被浏览器拦截。
  baseURL: '/',

  // RAG 智能问答页的挂载路径（并入模式）。
  // 用同源相对路径：开发由 vite 代理 /rag → 8000，生产由 nginx 按路径分流，
  // 详见 docs/集成与入口约定.md。RAG 服务独立部署时改这里即可指向它。
  ragBase: '/rag/',

  // API请求超时时间（毫秒）
  // 注意：问答的 SSE 流式请求走原生 fetch（见 views/inference/index.vue），不受此值影响。
  timeout: 1000 * 60 * 5,
};
