/** F08 演示示例题客户端：读取后端由题库生成的示例清单（前端不再硬编码问题）。 */

import type { DemoExamplesResponse } from '@/types/contract'

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`)
  }
  return (await resp.json()) as T
}

export function fetchDemoExamples(): Promise<DemoExamplesResponse> {
  return getJson<DemoExamplesResponse>('/api/demo/examples')
}
