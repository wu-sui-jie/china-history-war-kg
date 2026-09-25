/** 按账号隔离的 localStorage 读写（问答记录隔离，2026-09-25）。
 *
 * 问题：聊天记录原先存在全局 key（`chatHistory` / `extractHistory`）下，同一台浏览器上
 * 任何账号打开都是同一份记录。根因是存储 key 没有账号维度，而不是读取时机问题。
 *
 * 做法：key 拼上账号 id（`chatHistory:u3`）。账号未加载时（未登录、或 store 还没拉到
 * userInfo）退回原 key——与改造前行为一致，不会因为拿不到 uid 就把记录写丢。
 *
 * 老数据的处置：升级前那份全局记录不属于任何账号，谁先登录都能看到。这里**不做归属**
 * （把别人的提问记录交给第一个登录的人是不合适的），而是整体挪到
 * `<base>:legacy-archived` 归档——不再自动加载，需要时可在控制台手动找回。
 * 归档只做一次：搬完即删原 key。
 */

const ARCHIVE_SUFFIX = ':legacy-archived'

/** localStorage 在隐私模式/配额满时会抛异常，这里统一吞掉（读写都当"没有"处理）。 */
function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/**
 * 写 localStorage，返回是否真的写进去了（第 14 轮审计 P2-14）。
 *
 * 原先返回 void 且吞掉一切异常，于是**调用方的 catch 永远不会执行**：
 * `useChatHistory.save()` 里那套"配额不够就丢掉一半会话重试、还失败就告警"的降级逻辑
 * 成了死代码——localStorage 写满后聊天记录静默停止保存，刷新即全丢，界面上没有任何提示。
 *
 * 现在把成败交回给调用方：**能不能写**是调用方要决定的事（重试、丢弃旧数据、还是提示用户），
 * 不该在这一层替它静默吞掉。读路径仍按"没有"处理（读失败确实等价于没有数据）。
 */
function safeSet(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value)
    return true
  } catch {
    // 配额满或隐私模式：本次不持久化，但不影响内存里的使用
    return false
  }
}

function safeRemove(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* 忽略 */
  }
}

/** 组装存储 key：有账号 → `base:u{uid}`；没有账号 → 退回 `base`（保持独立使用时的行为）。 */
export function scopedStorageKey(base: string, uid?: string | number | null): string {
  const id = uid === null || uid === undefined ? '' : String(uid).trim()
  return id ? `${base}:u${id}` : base
}

/** 归档 key（老全局记录的去处，只留档不加载）。 */
export function archivedStorageKey(base: string): string {
  return `${base}${ARCHIVE_SUFFIX}`
}

/**
 * 读某账号的记录。该账号还没有自己的记录时，把老全局记录归档并返回 `fallback`。
 * 解析失败同样返回 `fallback`（不抛给调用方，页面不该因为一条坏记录白屏）。
 */
export function readScoped<T>(base: string, uid: string | number | null | undefined, fallback: T): T {
  const key = scopedStorageKey(base, uid)
  const raw = safeGet(key)
  if (raw) {
    try {
      return JSON.parse(raw) as T
    } catch {
      return fallback
    }
  }

  // 只有"有账号"时才处理老数据：没账号说明就是独立使用，直接沿用原 key 的语义
  if (key !== base) {
    const legacy = safeGet(base)
    if (legacy) {
      // 归档失败（配额满）不阻断读取：旧数据留在原 key 上，下次换账号时还会再试一次
      if (!safeSet(archivedStorageKey(base), legacy)) {
        console.warn('旧账号记录归档失败（localStorage 配额可能已满），本次仅完成读取')
      }
      safeRemove(base)
    }
  }
  return fallback
}

/** 写某账号的记录；返回是否写成功（调用方据此决定重试还是提示）。 */
export function writeScoped(base: string, uid: string | number | null | undefined, value: unknown): boolean {
  return safeSet(scopedStorageKey(base, uid), JSON.stringify(value))
}
