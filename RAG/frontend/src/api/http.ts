/** 普通 HTTP 客户端（健康检查 + 词典），不携带登录态。 */

import type { DictsResponse, HealthResponse } from '@/types/contract'

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, {
    headers: { Accept: 'application/json' },
  })
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`)
  }
  return (await resp.json()) as T
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>('/api/health')
}

export function fetchDicts(): Promise<DictsResponse> {
  return getJson<DictsResponse>('/api/dicts')
}
