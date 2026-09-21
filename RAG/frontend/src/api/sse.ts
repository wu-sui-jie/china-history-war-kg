/** SSE POST 流式客户端：fetch + ReadableStream 解析 `data:` 行。
 *
 * EventSource 只支持 GET，而 /api/query 需要 POST body（问题/历史/筛选），
 * 因此这里用 fetch 流逐行解析；支持 AbortController 中途取消。
 *
 * 解析与超时（2026-09-15 审核 P1-12 / P1-13）：
 * - 按 **行** 累积、空行分发，兼容 CRLF（部分反代会改写换行）与多行 `data:`；
 * - 忽略注释行（`: ping` 心跳）与 `event:`/`id:`/`retry:` 字段；
 * - 流尾若还有未分发的完整块，flush 时补一次解析（EOF 残帧）；
 * - 连接超时 / 空闲超时 / 整体上限三档，避免"永久转圈"。
 */

import { apiUrl } from '@/api/base'
import type { ApiErrorBody, QueryRequest, SSEEnvelope } from '@/types/contract'

export interface StreamOptions {
  onEvent: (event: SSEEnvelope) => void
  onError: (message: string) => void
  signal?: AbortSignal
  /** 建连超时（ms）：从发起请求到收到响应头 */
  connectTimeoutMs?: number
  /** 空闲超时（ms）：两次事件之间的最大间隔；0 表示不限制 */
  idleTimeoutMs?: number
  /** 整体超时（ms）：单次问答的总时长上限；0 表示不限制 */
  totalTimeoutMs?: number
}

/** 流结束的原因：由调用方决定如何收敛 UI 状态（第四轮复核 P2-10 细分为独立取值）。 */
export type StreamOutcome =
  | 'eof' // 字节流自然结束但没收到 done（是否收到 done 由上层判断）
  | 'aborted' // 用户主动 abort
  | 'connect_timeout' // 建连阶段超时（没等到响应头）
  | 'idle_timeout' // 读流期间长时间没有数据
  | 'total_timeout' // 单次问答整体超时
  | 'http_error' // 非 2xx（含 4xx 参数错误与限流）
  | 'network_error' // fetch 抛错（断网、DNS、CORS…）
  | 'protocol_error' // 收到的事件不是合法 JSON
  | 'parse_error' // 读取响应体过程中失败（连接被重置等）

export const DEFAULT_CONNECT_TIMEOUT_MS = 20000
export const DEFAULT_IDLE_TIMEOUT_MS = 120000
export const DEFAULT_TOTAL_TIMEOUT_MS = 330000

export function isAbortError(err: unknown): boolean {
  return (err as Error | undefined)?.name === 'AbortError'
}

/** 从错误响应体里取出后端给的可读信息（4xx 现在返回 JSON 而不是 SSE）。 */
async function readErrorMessage(resp: Response): Promise<string> {
  try {
    const text = await resp.text()
    if (text) {
      try {
        const body = JSON.parse(text) as ApiErrorBody
        if (body?.message) return body.message
      } catch {
        return text.slice(0, 200)
      }
    }
  } catch {
    // 读取失败就退回状态码描述
  }
  return `HTTP ${resp.status}`
}

interface SseSink {
  /** 收到一条完整 data 块 */
  onFrame: (raw: string) => void
  /** 连接仍然活跃（收到任意字节，含注释行） */
  onActivity: () => void
  /** 数据块不是合法 JSON（协议错误计数，不中断流） */
  onProtocolError: (raw: string, err: unknown) => void
}

/** 增量式 SSE 行解析器：喂入任意分块的文本，按空行分发 data 块。 */
function createSseParser(sink: SseSink) {
  let buffer = ''
  let dataLines: string[] = []

  const dispatch = () => {
    if (!dataLines.length) return
    const raw = dataLines.join('\n')
    dataLines = []
    sink.onFrame(raw)
  }

  const handleLine = (line: string) => {
    if (line === '') {
      dispatch()
      return
    }
    if (line.startsWith(':')) return // 注释行（心跳）
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') dataLines.push(value)
    // event/id/retry 字段当前协议未使用，忽略即可
  }

  return {
    feed(text: string) {
      sink.onActivity()
      buffer += text
      let idx: number
      // 同时兼容 \n 与 \r\n：先按 \n 切行，再剥掉行尾 \r
      while ((idx = buffer.indexOf('\n')) >= 0) {
        let line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 1)
        if (line.endsWith('\r')) line = line.slice(0, -1)
        handleLine(line)
      }
    },
    flush() {
      // 流结束时缓冲区里可能还有一行（无换行结尾）或一个未分发的 data 块
      if (buffer) {
        let line = buffer
        if (line.endsWith('\r')) line = line.slice(0, -1)
        buffer = ''
        handleLine(line)
      }
      dispatch()
    },
  }
}

export async function streamQuery(
  request: QueryRequest,
  options: StreamOptions,
): Promise<StreamOutcome> {
  const controller = new AbortController()
  const external = options.signal
  const abortFromExternal = () => controller.abort()
  if (external) {
    if (external.aborted) controller.abort()
    else external.addEventListener('abort', abortFromExternal)
  }

  const connectTimeout = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS
  const idleTimeout = options.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS
  const totalTimeout = options.totalTimeoutMs ?? DEFAULT_TOTAL_TIMEOUT_MS

  const startedAt = Date.now()
  let timer: number | undefined
  // 触发超时的类型（'connect' | 'idle' | 'total' | null），供结果分类使用
  let timedOutBy: 'connect' | 'idle' | 'total' | null = null
  let phase: 'connect' | 'body' = 'connect'
  const clearTimer = () => {
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timer = undefined
    }
  }

  /** 按当前阶段重置超时计时（第四轮复核 P1-8）。
   *
   * 三档互相独立：
   * - connect：等响应头，只受 connectTimeout 约束（后端不返回响应头时就该在这一档结束）；
   * - idle：开始读 body 后，两次数据之间超过 idleTimeout 判为卡死；
   * - total：从请求发出起一直有效，是整轮问答的硬上限。
   * 每次等待取"本阶段剩余时间"与"整体剩余时间"的较小值。
   */
  const armTimer = () => {
    clearTimer()
    const limits: number[] = []
    if (phase === 'connect' && connectTimeout > 0) {
      limits.push(connectTimeout - (Date.now() - startedAt))
    }
    if (phase === 'body' && idleTimeout > 0) limits.push(idleTimeout)
    if (totalTimeout > 0) limits.push(totalTimeout - (Date.now() - startedAt))
    if (!limits.length) return
    const wait = Math.min(...limits)
    if (!Number.isFinite(wait)) return
    // 谁先到点，由**触发那一刻**判定：不能在 arm 阶段就写死，
    // 否则用户手动 abort 也会被误判成超时（实测回归）。
    const totalLeft = totalTimeout > 0 ? totalTimeout - (Date.now() - startedAt) : Infinity
    const isTotalFirst = wait >= totalLeft
    const fire = () => {
      timedOutBy = isTotalFirst ? 'total' : (phase === 'connect' ? 'connect' : 'idle')
      controller.abort()
    }
    if (wait <= 0) {
      fire()
      return
    }
    timer = window.setTimeout(fire, Math.max(1, wait))
  }

  let outcome: StreamOutcome = 'eof'
  let sawAnyByte = false
  let protocolErrors = 0

  try {
    armTimer()
    const resp = await fetch(apiUrl('/query'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(request),
      signal: controller.signal,
    })
    sawAnyByte = true
    if (!resp.ok || !resp.body) {
      outcome = 'http_error'
      options.onError(`请求失败：${await readErrorMessage(resp)}`)
      return outcome
    }

    // 收到响应头：连接阶段结束，切换到空闲看门狗
    phase = 'body'
    armTimer()

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    const parser = createSseParser({
      onFrame: (raw) => {
        try {
          options.onEvent(JSON.parse(raw) as SSEEnvelope)
        } catch (err) {
          protocolErrors += 1
          options.onError(`SSE 数据解析失败: ${String(err)}`)
        }
      },
      onActivity: () => armTimer(),
      onProtocolError: () => {
        protocolErrors += 1
      },
    })
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.feed(decoder.decode(value, { stream: true }))
      }
      parser.feed(decoder.decode())
      parser.flush()
    } finally {
      reader.releaseLock()
    }
    // 纯协议错误（无 done 也无其他错误）单独归类，便于上层与监控区分
    return protocolErrors > 0 && outcome === 'eof' ? 'protocol_error' : outcome
  } catch (err) {
    if (isAbortError(err)) {
      if (timedOutBy) {
        outcome = timedOutBy === 'connect'
          ? 'connect_timeout'
          : timedOutBy === 'idle' ? 'idle_timeout' : 'total_timeout'
        const label = timedOutBy === 'connect'
          ? '后端未在连接超时内响应'
          : timedOutBy === 'idle' ? '长时间没有新内容' : '整轮问答超时'
        options.onError(`等待超时（${label}），已自动停止`)
      } else {
        outcome = 'aborted'
        options.onError('已取消')
      }
      return outcome
    }
    outcome = sawAnyByte ? 'parse_error' : 'network_error'
    options.onError(`网络错误: ${String(err)}`)
    return outcome
  } finally {
    clearTimer()
    if (external) external.removeEventListener('abort', abortFromExternal)
  }
}
