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
  //
  // 10 分钟：与 nginx 旧后端段的 proxy_read_timeout / proxy_send_timeout 对齐
  // （deploy/nginx/china-war.conf）。文本实体识别（/api/extract/entities-events）走
  // 4~5 次串行模型调用，是全站最慢的接口；此前前端 5 分钟、nginx 5 分钟，
  // 而实测过一次 382 秒（换云端模型前的本地 7B 模型），即**需求已超过上限**。
  //
  // 超时层次：单次模型调用 60s < 接口最坏耗时 < nginx 600s ≤ 此处 600s。
  // 为什么 axios 不小于 nginx：让反代的 504 先到，页面拿到的才是 HTTP 错误而不是
  // 浏览器侧的"请求超时"。
  //
  // 残余缺口（如实记录，别把它当成"不会再超时"）：单次调用最坏 3 次尝试 × 60s 超时
  // ≈ 183s（含退避），一次识别 4~5 次调用，理论上限约 900s，**仍高于 600s**。
  // 真要收口，得给整次识别设总预算（接口侧）或改成 SSE 推帧，不在本次范围内。
  timeout: 1000 * 60 * 10,
};
