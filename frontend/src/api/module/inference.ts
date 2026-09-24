/** 旧问答助手的流式问答（SSE）客户端（第 7 轮 W2 从 index.vue 抽出）。
 *
 * 页面原先把 fetch + 分块解析 + 逐帧 switch 全写在自己身上（约 170 行），
 * 抽出来之后：**传输与协议解析归这里**（含帧类型定义），**把帧应用到消息状态归页面**。
 * 这样帧协议有类型可依，页面也不再出现 `data` 之类的 any。
 */

import type { ChatMessage, KgGraphData } from '@/types/inference'

/** 服务端逐帧推送的协议（`data: {...}\n\n`）。字段按 status 分支解释，未用到的就是 undefined。 */
export interface InferenceStreamFrame {
  status: 'start' | 'extracting' | 'entities' | 'thinking' | 'answer' | 'kg_data' | 'complete' | 'error'
  /** extracting / error：给人看的文案 */
  message?: string
  /** entities：问题里识别到的实体名 */
  entities?: string[]
  /** thinking / answer：增量正文 */
  content?: string
  /** kg_data / complete：图谱数据与关系文本 */
  kg_data?: KgGraphData
  relations_text?: string
}

export interface StreamInferenceOptions {
  /** 每解析出一帧就回调一次（调用方负责把它应用到消息上） */
  onFrame: (frame: InferenceStreamFrame) => void
  /** 中断用的 signal：页面离开或再次提问时会 abort */
  signal?: AbortSignal
}

/**
 * 发起一次流式问答，逐帧回调，直到流结束。
 *
 * 分块解析与页面原先的实现一致：按 `\n` 切行、把最后一段不完整的行留在 buffer 里，
 * 只处理 `data: ` 前缀的行；单行 JSON 解析失败不影响后续行。
 */
export async function streamInference(
  question: string,
  token: string,
  { onFrame, signal }: StreamInferenceOptions,
): Promise<void> {
  const response = await fetch('/api/ai/inference/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Token': token,
    },
    body: JSON.stringify({ question }),
    signal,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('无法获取响应流')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''   // 保留不完整的行

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        onFrame(JSON.parse(line.slice(6)) as InferenceStreamFrame)
      } catch (parseError) {
        // 忽略单行解析错误，继续处理其他行
      }
    }
  }
}

/**
 * 把一帧数据应用到"正在生成的这条消息"上。
 *
 * 放在这里而不是页面里：帧的含义与协议在同一处定义，页面只负责编排（谁收流、什么时候中断）。
 * 它是纯状态改写（不改别的状态、不发请求），因此可以直接单测。
 */
export function applyInferenceFrame(aiMessage: ChatMessage, frame: InferenceStreamFrame): void {
  switch (frame.status) {
    case 'start':
      break

    case 'extracting':
      // 后端给的阶段提示（"正在识别实体…"）直接铺在回答位置
      aiMessage.content = `⏳ ${frame.message}`
      break

    case 'entities':
      aiMessage.entities = frame.entities || []
      break

    case 'thinking':
      if (!aiMessage.thinking) aiMessage.thinking = []
      aiMessage.thinking.push(frame.content || '')
      break

    case 'answer':
      aiMessage.content += frame.content || ''
      break

    case 'kg_data':
      aiMessage.kg_data = frame.kg_data || { nodes: [], lines: [] }
      aiMessage.kgContext = frame.relations_text || ''
      aiMessage.fromKg = !!(frame.kg_data && (frame.kg_data.nodes.length > 0 || frame.kg_data.lines.length > 0))
      break

    case 'complete':
      if (frame.kg_data) {
        aiMessage.kg_data = frame.kg_data
        aiMessage.kgContext = frame.relations_text || ''
        aiMessage.fromKg = frame.kg_data.nodes.length > 0 || frame.kg_data.lines.length > 0
      }
      aiMessage.time = Date.now()
      break

    case 'error':
      aiMessage.content = `错误: ${frame.message}`
      aiMessage.fromKg = false
      break
  }
}
