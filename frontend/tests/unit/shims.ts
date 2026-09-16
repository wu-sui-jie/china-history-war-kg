/** Node 环境下的最小浏览器 API 替身（localStorage / window / 定时器）。
 *
 * 只补齐被测代码真正用到的部分，不引入 jsdom：目标是让 store 与 SSE 客户端的
 * 状态机能在 Node 里跑真实断言（第四轮复核 P1-12）。
 */

class MemoryStorage {
  private store = new Map<string, string>()
  quotaBytes = Number.POSITIVE_INFINITY

  get length(): number {
    return this.store.size
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null
  }

  setItem(key: string, value: string): void {
    const size = [...this.store.entries()].reduce(
      (n, [k, v]) => n + k.length + v.length, 0)
    if (size + key.length + value.length > this.quotaBytes) {
      const err = new Error('QuotaExceededError')
      err.name = 'QuotaExceededError'
      throw err
    }
    this.store.set(key, value)
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  clear(): void {
    this.store.clear()
  }

  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null
  }
}

export function installBrowserShims(): MemoryStorage {
  const storage = new MemoryStorage()
  const g = globalThis as any
  g.localStorage = storage
  // store 里用 window.setTimeout / window.clearTimeout 做节流，Node 下补到 globalThis
  if (!g.window) g.window = g
  g.window.setTimeout = setTimeout
  g.window.clearTimeout = clearTimeout
  g.window.confirm = () => true
  if (!g.crypto?.randomUUID) {
    g.crypto = { randomUUID: () => `id-${Math.random().toString(16).slice(2)}` }
  }
  return storage
}

/** 构造一个可控的 SSE 响应体（按给定分块顺序喂给客户端）。 */
export function sseResponse(chunks: string[], init: ResponseInit = {}): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
    ...init,
  })
}

/** 把事件对象序列化成 SSE 帧。 */
export function frame(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`
}

/** 一个"永不主动结束"的响应体，用于超时用例。
 *
 * 关键点：浏览器里 abort fetch signal 会让 body 流以 AbortError 失败，
 * 假响应必须复刻这个行为，否则读流会永远挂住、超时逻辑根本走不到。
 */
export function hangingResponse(): {
  resp: Response
  push: (text: string) => void
  close: () => void
  attachAbort: (signal: AbortSignal | null | undefined) => void
} {
  const encoder = new TextEncoder()
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  return {
    resp: new Response(stream, { status: 200 }),
    push(text: string) {
      controller?.enqueue(encoder.encode(text))
    },
    close() {
      try {
        controller?.close()
      } catch {
        // 已关闭
      }
    },
    attachAbort(signal) {
      signal?.addEventListener('abort', () => {
        const err = new Error('The operation was aborted.')
        err.name = 'AbortError'
        try {
          controller?.error(err)
        } catch {
          // 流已关闭时忽略
        }
      })
    },
  }
}

export const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
