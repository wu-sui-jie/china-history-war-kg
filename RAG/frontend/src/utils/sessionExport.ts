/** 会话导出为 Markdown（F01）。
 *
 * 设计口径（docs/RAG_v2/RAG借鉴旧问答系统-需求分析.md §4.2）：
 * - 导出内容：轮次时间、问题、回答正文、**引用编号到来源的对照清单**。
 *   最后一项是关键——正文里是 `[n]` 引用，脱离界面后不附证据清单引用会悬空。
 * - 范围先只做"会话正文 + 引用来源"；实体卡/子图/时间线是否入文档列为待决项，不导出。
 * - 失败/取消/中断的轮次连同状态如实导出（不静默丢弃），但不产生空正文段落。
 */

import type { AssistantMessage, ChatMessage } from '@/stores/session'
import type { Citation, TurnStatus } from '@/types/contract'

export interface SessionExportMeta {
  /** 会话标题（多会话管理引入；缺省用"中国历代战争史问答"） */
  title?: string
  sessionId?: string
  exportedAt?: number
}

/** 非正常终态的中文说明（completed 不标注）。 */
const STATUS_NOTES: Partial<Record<TurnStatus, string>> = {
  cancelled: '本轮已取消，未生成完整回答。',
  failed: '本轮生成失败，没有回答。',
  interrupted: '本轮连接中断，回答可能不完整。',
  refused: '本轮因检索证据不足而拒答（本系统不使用模型通用知识兜底）。',
  degraded: '本轮为降级生成（未经完整检索链路）。',
  connecting: '本轮未完成（页面刷新或请求中断）。',
  streaming: '本轮未完成（页面刷新或请求中断）。',
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 本地时间 `YYYY-MM-DD HH:mm`（导出文档给人看，不做时区换算）。 */
export function formatStamp(at: number): string {
  const d = new Date(at)
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  )
}

/** 文件名安全化：去掉路径分隔符与控制字符，限长。 */
export function sanitizeFilename(name: string): string {
  const cleaned = name
    .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
  return cleaned || '会话'
}

/** 引用摘要单行化并截断（摘要是证据原文片段，过长会淹没清单）。 */
function oneLine(text: string | undefined, limit = 120): string {
  const flat = String(text || '').replace(/\s+/g, ' ').trim()
  if (flat.length <= limit) return flat
  return `${flat.slice(0, limit)}…`
}

function citationLines(citations: Citation[]): string[] {
  if (!citations.length) return []
  const lines = ['**引用来源**', '']
  for (const c of [...citations].sort((a, b) => a.index - b.index)) {
    const kind = c.kind ? ` · ${c.kind}` : ''
    lines.push(`- **[${c.index}]** ${c.title || c.evidence_id}${kind}`)
    const snippet = oneLine(c.snippet)
    if (snippet) lines.push(`  > ${snippet}`)
  }
  return lines
}

/** 把整段会话装配成 Markdown 文档。 */
export function buildSessionMarkdown(
  messages: ChatMessage[],
  meta: SessionExportMeta = {},
): string {
  const exportedAt = meta.exportedAt ?? Date.now()
  const assistantTurns = messages.filter(
    (m): m is AssistantMessage => m.role === 'assistant',
  )
  const head = [
    `# ${meta.title || '中国历代战争史问答'} · 会话导出`,
    '',
    `- 导出时间：${formatStamp(exportedAt)}`,
  ]
  if (meta.sessionId) head.push(`- 会话 ID：\`${meta.sessionId}\``)
  head.push(`- 轮次：${assistantTurns.length} 轮`)
  head.push('')

  const body: string[] = []
  assistantTurns.forEach((turn, i) => {
    const question = (turn.question || '').trim()
    body.push('---', '')
    body.push(`## 第 ${i + 1} 轮 · ${formatStamp(turn.createdAt)}`, '')
    body.push('**问题**', '', question || '（空问题）', '')
    const answer = (turn.answer || '').trim()
    body.push('**回答**', '')
    if (answer) body.push(answer, '')
    const note = STATUS_NOTES[turn.turnStatus]
    if (!answer) {
      body.push(note || '（本轮没有生成回答。）', '')
    } else if (note) {
      body.push(`> ${note}`, '')
    }
    if (turn.supersededBy) {
      body.push('> 本轮回答已被后续的纠正重查取代，以上为原回答。', '')
    }
    body.push(...citationLines(turn.citations))
    if (body[body.length - 1] !== '') body.push('')
  })

  return [...head, ...body].join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n'
}

/** 触发浏览器下载（导出逻辑与 DOM 分离，便于在测试里只断言 Markdown 文本）。 */
export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/** 默认文件名：`<标题>-<时间戳>.md`。 */
export function exportFilename(title: string | undefined, at: number): string {
  const d = new Date(at)
  const stamp =
    `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-` +
    `${pad2(d.getHours())}${pad2(d.getMinutes())}`
  return `${sanitizeFilename(title || '中国历代战争史问答')}-${stamp}.md`
}
