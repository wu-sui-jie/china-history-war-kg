/** SSE POST 流式客户端：fetch + ReadableStream 解析 `data:` 行。
 *
 * EventSource 只支持 GET，而 /api/query 需要 POST body（问题/历史/筛选），
 * 因此这里用 fetch 流逐行解析；支持 AbortController 中途取消。
 */

import type { QueryRequest, SSEEnvelope } from '@/types/contract'

export interface StreamOptions {
  onEvent: (event: SSEEnvelope) => void
  onError: (message: string) => void
  signal?: AbortSignal
}

function decodeLine(line: Uint8Array): string {
  return new TextDecoder('utf-8').decode(line)
}

async function parseSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SSEEnvelope) => void,
  onError: (message: string) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatchBuffer = (text: string) => {
    buffer += text
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const lines = block.split(/\r?\n/)
      const dataLines = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
      if (!dataLines.length) continue
      const raw = dataLines.join('\n')
      try {
        const parsed = JSON.parse(raw) as SSEEnvelope
        onEvent(parsed)
      } catch (err) {
        onError(`SSE 数据解析失败: ${String(err)}`)
      }
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      dispatchBuffer(decoder.decode(value, { stream: true }))
    }
    // 流尾残留
    dispatchBuffer(decoder.decode())
  } finally {
    reader.releaseLock()
  }
}

export async function streamQuery(
  request: QueryRequest,
  options: StreamOptions,
): Promise<void> {
  const controller = new AbortController()
  const external = options.signal
  const abortFromExternal = () => controller.abort()
  if (external) {
    if (external.aborted) controller.abort()
    else external.addEventListener('abort', abortFromExternal)
  }
  try {
    const resp = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(request),
      signal: controller.signal,
    })
    if (!resp.ok || !resp.body) {
      options.onError(`请求失败（HTTP ${resp.status}）`)
      return
    }
    await parseSseStream(resp.body, options.onEvent, options.onError)
  } catch (err) {
    if ((err as Error).name === 'AbortError') {
      options.onError('已取消')
    } else {
      options.onError(`网络错误: ${String(err)}`)
    }
  } finally {
    if (external) external.removeEventListener('abort', abortFromExternal)
  }
}
