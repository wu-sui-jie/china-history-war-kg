/** 接口前缀与路径拼接的唯一来源。
 *
 * 独立部署（RAGv5 同源托管）时前缀是 `/api`，与后端 `/api/*` 直接对齐；
 * 并入旧知识库系统 Web 入口时（子路径反代，见 china-war/docs/集成与入口约定.md），
 * 构建期用 `VITE_API_BASE=/rag/api VITE_BASE_PATH=/rag/`（即 .env.integration）指到反代前缀。
 *
 * 之所以必须参数化：写死根绝对路径 `/api/health` 时，一旦 SPA 被挂在 `/rag/` 下，
 * 请求会落到旧 Flask 的 `/api/*` 上——两边都用 `/api` 前缀，必然串台。
 */

const RAW_BASE = import.meta.env.VITE_API_BASE ?? '/api'

/** 去掉尾部斜杠，保证拼接结果是 `/api/health` 而不是 `/api//health`。 */
export const API_BASE = RAW_BASE.replace(/\/+$/, '')

/** 拼接接口路径：`apiUrl('/query')` → `/api/query`（并入模式下为 `/rag/api/query`）。 */
export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}
