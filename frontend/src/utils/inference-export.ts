/** 问答记录的 Markdown 导出。
 *
 * `buildConversationMarkdown` 是纯函数（给定会话返回字符串），因此可以直接单测；
 * Blob/下载/提示留在调用方，不混进这个函数里。
 */

import { formatChatTime } from './date'
import type { ChatSession } from '../types/inference'

/** 会话 → Markdown 文本（标题、导出时间、逐条消息、思考过程、图谱参考信息）。 */
export function buildConversationMarkdown(chat: ChatSession, exportedAt: number = Date.now()): string {
  let markdownContent = `# ${chat.title || '对话记录'}\n\n`
  markdownContent += `导出时间: ${formatChatTime(exportedAt)}\n\n`

  chat.messages.forEach((message, index) => {
    const role = message.role === 'user' ? '用户' : 'AI助手'
    const time = formatChatTime(message.time)

    markdownContent += `## ${role} (${time})\n\n`

    // 如果是AI消息且有思考过程，添加思考过程
    if (message.role === 'assistant' && message.thinking && message.thinking.length > 0) {
      markdownContent += '### 思考过程\n\n'
      message.thinking.forEach((think) => {
        // 思考过程按引用格式输出：逐行加 >
        const quotedThink = think.split('\n').map(line => `> ${line}`).join('\n')
        markdownContent += `${quotedThink}\n\n`
      })
    }

    // 添加消息内容
    markdownContent += `${message.content}\n\n`

    // 如果是AI消息且有知识图谱上下文，添加知识图谱信息
    if (message.role === 'assistant' && message.kgContext) {
      markdownContent += '### 知识图谱参考信息\n\n'
      markdownContent += '```\n'
      markdownContent += message.kgContext
      markdownContent += '\n```\n\n'
    }

    // 添加分隔线（除了最后一条消息）
    if (index < chat.messages.length - 1) {
      markdownContent += '---\n\n'
    }
  })

  return markdownContent
}

/** 触发浏览器下载（Blob + 临时链接，用完即回收）。 */
export function downloadMarkdown(content: string, fileName: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)

  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', fileName)
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()

  // 延迟移除元素和URL，确保浏览器有足够时间处理下载
  setTimeout(() => {
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }, 100)
}

/** 导出文件名：`<会话标题或占位>_<YYYY-MM-DD>.md`。 */
export function exportFileName(title: string, now: Date = new Date()): string {
  return `${title || '对话记录'}_${now.toISOString().split('T')[0]}.md`
}
