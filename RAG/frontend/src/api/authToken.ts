/** 登录凭证（JWT）的进程内保管处。
 *
 * 主应用以 iframe 嵌入 RAG 时，把当前账号的 token 用 postMessage 下发；本模块只负责
 * **保管 + 拼请求头**，不做任何验签——验签在服务端（见 RAG/server/auth.py）。
 *
 * 三条刻意的设计选择：
 *
 * 1. **不放 localStorage**。token 只留在内存：刷新页面就没了，但主应用会在 iframe
 *    每次 load 后重发（见 ../../docs 与 frontend/src/views/knowledge/RagAssistant.vue），
 *    因此功能不受影响。放进 localStorage 会让"同源任意脚本都能读到长期凭证"，
 *    而我们并不需要它跨会话存活。
 *
 * 2. **严格清洗**。token 会被塞进 HTTP 头，头值里出现换行/空白会被 fetch 直接拒绝
 *    （甚至可能被用来注入额外头）。所以只接受 base64url/JWT 字符集，见 normalizeToken。
 *
 * 3. **空 token 等于不带这个头**。独立访问 :8000（没有主应用）或服务端未开启校验时，
 *    请求不带任何身份头。
 */

/** JWT 只由 `[A-Za-z0-9_-]` 与两个点组成；额外允许 `=`（部分实现会带 padding）。 */
const TOKEN_PATTERN = /^[A-Za-z0-9._=-]{8,4096}$/

/** 携带身份用的请求头名，与旧后端一致（同一个 token，同一种排障方式）。 */
export const AUTH_HEADER = 'Token'

/** 归一化 token：非法/空值返回空串（=没有身份），绝不抛错、更不原样透传。 */
export function normalizeToken(raw: unknown): string {
  if (raw === null || raw === undefined) return ''
  const text = String(raw).trim()
  return TOKEN_PATTERN.test(text) ? text : ''
}

let currentToken = ''

/** 设置当前 token（非法值视为清空）。 */
export function setAuthToken(raw: unknown): void {
  currentToken = normalizeToken(raw)
}

/** 当前 token（未登录/独立访问时为空串）。 */
export function getAuthToken(): string {
  return currentToken
}

/** 需要带上身份的请求该加的请求头；没有 token 时返回空对象。 */
export function authHeaders(): Record<string, string> {
  return currentToken ? { [AUTH_HEADER]: currentToken } : {}
}
