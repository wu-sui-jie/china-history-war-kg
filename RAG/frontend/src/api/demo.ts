/** F08 演示示例题客户端：读取后端由题库生成的示例清单（前端不再硬编码问题）。 */

import { apiUrl } from '@/api/base'
import { ApiError, DEFAULT_GET_TIMEOUT_MS } from '@/api/http'
import type { ApiErrorBody, DemoExamplesResponse } from '@/types/contract'

async function getJson<T>(path: string, timeoutMs = DEFAULT_GET_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const resp = await fetch(path, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
    if (!resp.ok) {
      let message = `HTTP ${resp.status}: ${path}`
      try {
        const body = (await resp.json()) as ApiErrorBody
        if (body?.message) message = body.message
      } catch {
        // 非 JSON 错误体：保留状态码描述
      }
      throw new ApiError(message, resp.status)
    }
    return (await resp.json()) as T
  } finally {
    window.clearTimeout(timer)
  }
}

export function fetchDemoExamples(): Promise<DemoExamplesResponse> {
  return getJson<DemoExamplesResponse>(apiUrl('/demo/examples'))
}
