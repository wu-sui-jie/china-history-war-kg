/** 普通 HTTP 客户端（健康检查 + 词典），不携带登录态。
 *
 * 2026-09-15 审核 P1-13：此前两个 GET 没有任何超时，后端挂住时首屏会一直等待；
 * 现在统一 8 s 上限，并把后端的 JSON 错误信息（如版本不一致）透出给界面。
 */

import { apiUrl } from '@/api/base'
import type { ApiErrorBody, DictsResponse, HealthResponse } from '@/types/contract'

export const DEFAULT_GET_TIMEOUT_MS = 8000

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function getJson<T>(path: string, timeoutMs = DEFAULT_GET_TIMEOUT_MS): Promise<T> {
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

export function fetchHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>(apiUrl('/health'))
}

export function fetchDicts(): Promise<DictsResponse> {
  return getJson<DictsResponse>(apiUrl('/dicts'))
}
