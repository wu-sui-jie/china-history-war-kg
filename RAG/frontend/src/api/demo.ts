/** F08 演示示例题客户端：读取后端由题库生成的示例清单（前端不再硬编码问题）。 */

import { apiUrl } from '@/api/base'
import { getJson } from '@/api/http'
import type { DemoExamplesResponse } from '@/types/contract'

export function fetchDemoExamples(): Promise<DemoExamplesResponse> {
  return getJson<DemoExamplesResponse>(apiUrl('/demo/examples'))
}
