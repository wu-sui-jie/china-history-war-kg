/** 主应用身份桥：让 RAG 前端知道"现在是谁在问"（问答记录隔离，问题二方案 A）。
 *
 * 背景：RAG 是独立服务、没有用户体系，会话历史存在本页 localStorage 里且 key 全局唯一
 * （`ragv5-session-v3`）。主应用以同源 iframe 嵌入 RAG 时共享同一个 localStorage，
 * 于是同一浏览器上任何账号打开 RAG 看到的都是同一份记录。
 *
 * 这里只做两件事：
 * 1. 把主应用 postMessage 来的身份消息解析成 `{uid, role}` 并交给回调；
 * 2. 按账号组装存储 key——有 uid 用 `ragv5-session-v3:u{uid}`，没有 uid（独立访问
 *    :8000、或消息还没到）沿用原 key，行为与改造前完全一致。
 *
 * **换桶（setActiveUid）由 store 负责，这里不做**。原因是要保证"先把当前状态写回旧桶、
 * 再换 uid"这个顺序——如果桥先把 uid 改了，store 再写就会把上一个账号的会话写进新账号的
 * 桶里（串数据）。曾经踩过：桥改成传对象后 store 仍按字符串处理，activeUid 变成
 * `"[object Object]"`，所有账号落进同一个桶，症状就是"两个账号记录一模一样"。
 * `tests/unit/user-scope-wiring.test.ts` 照 main.ts 的接线方式专门钉这一处。
 *
 * 安全边界（如实）：同源之下懂控制台的账号能改 uid 去看别人的记录，本方案只解决"串记录"。
 * 要防冒充得走方案 B（主应用传 JWT + RAG 服务端验签），见
 * docs/方案分析-账户提权与问答隔离-20260925.md。
 */

/** 会话存储的基础 key（与 stores/session.ts 的历史 key 保持一致）。 */
export const SESSION_STORAGE_KEY = 'ragv5-session-v3'

/** uid 的形状约束：只接受数字/字母/下划线/连字符，且有长度上限。
 *
 * uid 会被直接拼进 localStorage 的 key，因此不能什么字符串都收（第 6 轮审核低危项）：
 * 桥曾经把对象当字符串处理，拼出 `ragv5-session-v3:u[object Object]` 这种桶；
 * 超长 uid 也会把 key 长度和配额一起吃掉。主应用的账号 id 是数字，将来若换成
 * 字符串/UUID 也落在 `[A-Za-z0-9_-]` 之内。
 * 不合法一律按"没有账号"处理——退到独立访问语义，是这里最保守的一侧。
 */
const UID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

/** 归一化 uid：合法返回字符串；空值/对象/超长/含分隔符返回 null。 */
export function normalizeUid(raw: unknown): string | null {
  if (raw === null || raw === undefined) return null
  const text = String(raw).trim()
  return UID_PATTERN.test(text) ? text : null
}

let activeUid: string | null = null
let activeRole = ''
/** 最近一次已经通知过的 uid：桥的去重依据**不能**用 activeUid——换桶由 store 负责，
 *  桥若拿 activeUid 当依据，在上游没换桶时会重复通知（或反过来漏掉）。 */
let lastNotifiedUid: string | null = null

/** 当前账号 id；独立访问时为 null。 */
export function getActiveUid(): string | null {
  return activeUid
}

/** 当前账号角色（主应用给的 admin/editor/viewer；老版本主应用不发则空串）。 */
export function getActiveRole(): string {
  return activeRole
}

/** 设置当前账号 id（空串/null/非法值视为未指定）。换桶时机由 store 决定，见文件头说明。 */
export function setActiveUid(uid: string | number | null | undefined): void {
  activeUid = normalizeUid(uid)
  // store 刚换到这个桶，同一个 uid 再来的消息本就无事可做，顺手同步去重位
  lastNotifiedUid = activeUid
}

/** 记录当前账号角色（不参与存储 key 组装，供后续按角色做界面收敛时取用）。 */
export function setActiveRole(role: string | null | undefined): void {
  activeRole = role ? String(role).trim() : ''
}

/**
 * 按账号组装存储 key。
 * 没有账号（含 uid 非法）时不加后缀——独立访问 :8000 的场景与改造前一致，老记录也留在默认 key 里。
 */
export function storageKeyFor(uid: string | number | null | undefined, base = SESSION_STORAGE_KEY): string {
  const id = normalizeUid(uid)
  return id ? `${base}:u${id}` : base
}

/** 当前该读写的存储 key。 */
export function activeStorageKey(base = SESSION_STORAGE_KEY): string {
  return storageKeyFor(activeUid, base)
}

/** 身份消息的解构结果：uid 为空表示"退出到无账号"。 */
export interface UserScopeMessage {
  uid: string | null
  /** 主应用给出的角色（admin/editor/viewer 或空串）。老版本主应用不发这个字段，按空串处理。 */
  role: string
}

/** 收到的消息是否是本应用认的身份消息（校验 origin 与形状，形状不对一律忽略）。 */
export function parseUserScopeMessage(
  event: { origin?: string; data?: unknown },
  expectedOrigin: string,
): UserScopeMessage | undefined {
  if (event.origin !== expectedOrigin) return undefined
  const data = event.data as { type?: unknown; uid?: unknown; role?: unknown } | null | undefined
  if (!data || typeof data !== 'object' || data.type !== 'cw-user') return undefined
  const role = typeof data.role === 'string' ? data.role.trim() : ''
  // 形状/取值不合法（对象、超长、含分隔符）一律当"没有账号"：宁可退回独立访问语义，
  // 也不能拼出一个谁都不是的桶
  return { uid: normalizeUid(data.uid), role }
}

/**
 * 安装身份桥：主应用发来账号变化时回调（**只回调，不改 activeUid**，见文件头）。
 *
 * 只接受同源消息（`expectedOrigin` 默认取当前 origin），其它来源一律忽略——
 * 否则任意被嵌入的第三方页面都能改我们的存储 key。
 * 同一个 uid 重复到达不重复通知（换桶有副作用：会重读存储、中断在途流）。
 * 返回解绑函数，便于测试与热更新时清理。
 */
export function installHostUserBridge(
  onScopeChange: (scope: UserScopeMessage) => void,
  expectedOrigin: string = window.location.origin,
): () => void {
  const handler = (event: MessageEvent) => {
    const scope = parseUserScopeMessage(event, expectedOrigin)
    if (scope === undefined) return
    if (scope.uid === lastNotifiedUid) return
    // 先回调、成功后才推进去重位：回调里若抛错（换桶要读存储，存储异常会炸），
    // 去重位已经推进的话这个 uid 就再也不会被通知，store 永远停在上一个桶且没有自愈机会。
    // 不在这里 setActiveUid：store 必须先写回旧桶再换，见文件头。
    onScopeChange(scope)
    lastNotifiedUid = scope.uid
  }
  window.addEventListener('message', handler)
  return () => window.removeEventListener('message', handler)
}
