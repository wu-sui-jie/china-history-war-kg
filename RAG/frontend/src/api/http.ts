/** 普通 HTTP 客户端（健康检查 + 词典）。
 *
 * 2026-09-15 审核 P1-13：此前两个 GET 没有任何超时，后端挂住时首屏会一直等待；
 * 现在统一 8 s 上限，并把后端的 JSON 错误信息（如版本不一致）透出给界面。
 *
 * 2026-09-25（第 12 轮审查 P1-1）：改为**携带身份头**。原先注释写的是"不携带登录态"，
 * 在服务端开启 RAG_REQUIRE_AUTH 后这类请求会直接 401，首屏词典与健康检查全挂。
 * 未登录/独立访问时 authHeaders() 返回空对象，行为与改造前一致。
 */

import { apiUrl } from '@/api/base'
import { authHeaders } from '@/api/authToken'
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
      headers: { Accept: 'application/json', ...authHeaders() },
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
