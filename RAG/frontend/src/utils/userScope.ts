/** 主应用身份桥：让 RAG 前端知道"现在是谁在问"（问答记录隔离，问题二方案 A）。
 *
 * 背景：RAG 是独立服务、没有用户体系，会话历史存在本页 localStorage 里且 key 全局唯一
 * （`ragv5-session-v3`）。主应用以同源 iframe 嵌入 RAG 时共享同一个 localStorage，
 * 于是同一浏览器上任何账号打开 RAG 看到的都是同一份记录。
 *
 * 这里只做两件事：
 * 1. 记住主应用通过 postMessage 告知的账号 id（`{type: 'cw-user', uid}`）；
 * 2. 按账号组装存储 key——有 uid 用 `ragv5-session-v3:u{uid}`，没有 uid（独立访问
 *    :8000、或消息还没到）沿用原 key，行为与改造前完全一致。
 *
 * 安全边界（如实）：同源之下懂控制台的账号能改 uid 去看别人的记录，本方案只解决"串记录"。
 * 要防冒充得走方案 B（主应用传 JWT + RAG 服务端验签），见
 * docs/方案分析-账户提权与问答隔离-20260925.md。
 */

/** 会话存储的基础 key（与 stores/session.ts 的历史 key 保持一致）。 */
export const SESSION_STORAGE_KEY = 'ragv5-session-v3'

let activeUid: string | null = null

/** 当前账号 id；独立访问时为 null。 */
export function getActiveUid(): string | null {
  return activeUid
}

/** 设置当前账号 id（空串/null 视为未指定）。 */
export function setActiveUid(uid: string | number | null | undefined): void {
  const next = uid === null || uid === undefined ? '' : String(uid).trim()
  activeUid = next ? next : null
}

/**
 * 按账号组装存储 key。
 * 没有账号时不加后缀——独立访问 :8000 的场景与改造前一致，老记录也留在默认 key 里。
 */
export function storageKeyFor(uid: string | number | null | undefined, base = SESSION_STORAGE_KEY): string {
  const id = uid === null || uid === undefined ? '' : String(uid).trim()
  return id ? `${base}:u${id}` : base
}

/** 当前该读写的存储 key。 */
export function activeStorageKey(base = SESSION_STORAGE_KEY): string {
  return storageKeyFor(activeUid, base)
}

/** 收到的消息是否是本应用认的身份消息（校验 origin 与形状，形状不对一律忽略）。 */
export function parseUserScopeMessage(event: { origin?: string; data?: unknown }, expectedOrigin: string): string | null | undefined {
  if (event.origin !== expectedOrigin) return undefined
  const data = event.data as { type?: unknown; uid?: unknown } | null | undefined
  if (!data || typeof data !== 'object' || data.type !== 'cw-user') return undefined
  const uid = data.uid
  if (uid === null || uid === undefined || uid === '') return null
  return String(uid).trim() || null
}

/**
 * 安装身份桥：主应用发来账号变化时回调。
 *
 * 只接受同源消息（`expectedOrigin` 默认取当前 origin），其它来源一律忽略——
 * 否则任意被嵌入的第三方页面都能改我们的存储 key。
 * 返回解绑函数，便于测试与热更新时清理。
 */
export function installHostUserBridge(
  onScopeChange: (uid: string | null) => void,
  expectedOrigin: string = window.location.origin,
): () => void {
  const handler = (event: MessageEvent) => {
    const uid = parseUserScopeMessage(event, expectedOrigin)
    if (uid === undefined) return
    if (uid === activeUid) return
    setActiveUid(uid)
    onScopeChange(uid)
  }
  window.addEventListener('message', handler)
  return () => window.removeEventListener('message', handler)
}
