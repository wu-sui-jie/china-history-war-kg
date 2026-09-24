/** 从问答页抽出的三块逻辑（第 7 轮 W2）。
 *
 * 拆分的另一半价值在这里：以前这些逻辑锁在 1189 行的页面里，只能挂载整页才测得到；
 * 现在它们是纯函数/组合式函数，可以直接断言——尤其是"导出成什么样"与"逐帧怎么落到消息上"。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import { applyInferenceFrame, type InferenceStreamFrame } from '@/api/module/inference'
import { buildConversationMarkdown, exportFileName } from '@/utils/inference-export'
import { copyText } from '@/utils/clipboard'
import { useChatHistory, CHAT_HISTORY_KEY } from '@/composables/useChatHistory'
import type { ChatMessage, ChatSession } from '@/types/inference'

const userMsg = (content: string, time = 1_700_000_000_000): ChatMessage =>
  ({ role: 'user', content, time, fromKg: true })

const aiMsg = (extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ role: 'assistant', content: '答案', time: 1_700_000_000_000, fromKg: true, ...extra })

const session = (messages: ChatMessage[], title = '赤壁之战'): ChatSession =>
  ({ id: 1, title, messages, lastTime: 1_700_000_000_000 })

describe('buildConversationMarkdown 会话导出', () => {
  test('逐条输出角色与时间，AI 的思考过程按引用块输出', () => {
    const md = buildConversationMarkdown(session([
      userMsg('赤壁之战在哪？'),
      aiMsg({ content: '在湖北。', thinking: ['第一步\n第二步'], kgContext: '赤壁—发生地→赤壁市' }),
    ]), 1_700_000_000_000)

    expect(md).toContain('# 赤壁之战')
    expect(md).toContain('## 用户')
    expect(md).toContain('## AI助手')
    expect(md).toContain('### 思考过程')
    expect(md).toContain('> 第一步\n> 第二步')
    expect(md).toContain('### 知识图谱参考信息')
    expect(md).toContain('```\n赤壁—发生地→赤壁市\n```')
  })

  test('标题为空时用占位，最后一条消息后不加分隔线', () => {
    const md = buildConversationMarkdown(session([userMsg('一'), userMsg('二')], ''))
    expect(md).toContain('# 对话记录')
    expect(md.trimEnd().endsWith('二')).toBe(true)
    expect(md.match(/^---$/gm)).toHaveLength(1)
  })

  test('用户消息不输出思考过程与图谱段落（只有 AI 有）', () => {
    const md = buildConversationMarkdown(session([
      userMsg('提问'), aiMsg({ content: '回答', thinking: ['想到了'] }),
    ]))
    expect(md.match(/### 思考过程/g)).toHaveLength(1)
  })

  test('导出文件名带标题与日期', () => {
    expect(exportFileName('赤壁之战', new Date('2026-09-25T10:00:00Z'))).toBe('赤壁之战_2026-09-25.md')
    expect(exportFileName('', new Date('2026-09-25T10:00:00Z'))).toBe('对话记录_2026-09-25.md')
  })
})

describe('applyInferenceFrame 帧 → 消息', () => {
  test('thinking 逐条累积，answer 逐字追加', () => {
    const msg = aiMsg({ content: '', thinking: [] })
    applyInferenceFrame(msg, { status: 'thinking', content: '先定位' })
    applyInferenceFrame(msg, { status: 'thinking', content: '再判断' })
    applyInferenceFrame(msg, { status: 'answer', content: '赤壁' })
    applyInferenceFrame(msg, { status: 'answer', content: '之战' })

    expect(msg.thinking).toEqual(['先定位', '再判断'])
    expect(msg.content).toBe('赤壁之战')
  })

  test('extracting 覆盖正文为阶段提示，entities 记下实体', () => {
    const msg = aiMsg({ content: '' })
    applyInferenceFrame(msg, { status: 'extracting', message: '正在识别实体' })
    expect(msg.content).toBe('⏳ 正在识别实体')

    applyInferenceFrame(msg, { status: 'entities', entities: ['赤壁', '曹操'] })
    expect(msg.entities).toEqual(['赤壁', '曹操'])
  })

  test('kg_data 带节点时 fromKg=true，空图时 false；complete 更新消息时间', () => {
    const msg = aiMsg({ content: '', kg_data: { nodes: [], lines: [] } })
    applyInferenceFrame(msg, { status: 'kg_data', kg_data: { nodes: [{ id: 'n1' }], lines: [] }, relations_text: '关系' })
    expect(msg.fromKg).toBe(true)
    expect(msg.kgContext).toBe('关系')

    applyInferenceFrame(msg, { status: 'kg_data', kg_data: { nodes: [], lines: [] } })
    expect(msg.fromKg).toBe(false)

    const before = msg.time
    applyInferenceFrame(msg, { status: 'complete', kg_data: { nodes: [{ id: 'n1' }], lines: [] } })
    expect(msg.time).toBeGreaterThanOrEqual(before)
  })

  test('error 帧把正文换成错误文案并标记不来自图谱', () => {
    const msg = aiMsg()
    applyInferenceFrame(msg, { status: 'error', message: '模型超时' })
    expect(msg.content).toBe('错误: 模型超时')
    expect(msg.fromKg).toBe(false)
  })

  test('未知/缺字段的帧不抛错', () => {
    const msg = aiMsg({ thinking: undefined })
    expect(() => applyInferenceFrame(msg, { status: 'start' } as InferenceStreamFrame)).not.toThrow()
    expect(() => applyInferenceFrame(msg, { status: 'answer' })).not.toThrow()
    expect(msg.thinking).toBeUndefined()
  })
})

describe('copyText 复制', () => {
  afterEach(() => vi.restoreAllMocks())

  test('优先走剪贴板 API', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    expect(await copyText('内容')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('内容')
    vi.unstubAllGlobals()
  })

  test('剪贴板 API 不存在时走回退方案', async () => {
    vi.stubGlobal('navigator', {})
    const execCommand = vi.fn().mockReturnValue(true)
    document.execCommand = execCommand

    expect(await copyText('内容')).toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
    vi.unstubAllGlobals()
  })

  test('剪贴板 API 拒绝时也回退，且不抛给调用方', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    document.execCommand = vi.fn().mockReturnValue(true)

    expect(await copyText('内容')).toBe(true)
    vi.unstubAllGlobals()
  })
})

describe('useChatHistory 问答记录', () => {
  /** 组合式函数用到 onBeforeUnmount，必须在组件上下文里调用 */
  function withSetup(uid: string | undefined, token: string) {
    let api: ReturnType<typeof useChatHistory> | undefined
    const Comp = defineComponent({
      setup() {
        api = useChatHistory({ uid: () => uid, isAccessBlocked: () => !uid && !!token })
        return () => h('div')
      },
    })
    const wrapper = mount(Comp)
    return { api: api!, wrapper }
  }

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  test('按账号分 key 读写：换个 uid 读不到别人的记录', () => {
    const a = withSetup('3', 'tk')
    a.api.create()
    a.api.flush()
    expect(localStorage.getItem(`${CHAT_HISTORY_KEY}:u3`)).toBeTruthy()
    a.wrapper.unmount()

    const b = withSetup('4', 'tk')
    b.api.load()
    expect(b.api.chats.value).toHaveLength(1)
    expect(b.api.chats.value[0].title).toBe('新对话')
    b.wrapper.unmount()
  })

  test('已登录但 uid 未知：既不读也不写公共桶', () => {
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify([session([userMsg('别人的问题')], '他人记录')]))

    const h1 = withSetup(undefined, 'tk')
    h1.api.load()
    expect(h1.api.chats.value[0].title).toBe('新对话')   // 没读到公共桶
    h1.api.create()
    h1.api.flush()
    expect(localStorage.getItem(CHAT_HISTORY_KEY)).toContain('他人记录')  // 也没覆盖公共桶
    h1.wrapper.unmount()
  })

  test('未登录（独立使用）：沿用全局 key', () => {
    const h1 = withSetup(undefined, '')
    h1.api.create()
    h1.api.flush()
    expect(localStorage.getItem(CHAT_HISTORY_KEY)).toBeTruthy()
    h1.wrapper.unmount()
  })

  test('删除只剩一个会话时清空而不是删掉；删前面的会话时当前下标跟着挪', () => {
    const { api, wrapper } = withSetup('3', 'tk')
    api.create()            // 两个会话：新建的 + 初始的
    expect(api.chats.value).toHaveLength(2)

    api.remove(0)
    expect(api.chats.value).toHaveLength(1)

    api.remove(0)           // 只剩一个：清空它
    expect(api.chats.value).toHaveLength(1)
    expect(api.chats.value[0].title).toBe('新对话')
    expect(api.chats.value[0].messages).toEqual([])
    wrapper.unmount()
  })

  test('标题取首条提问并截断到 20 字', () => {
    const { api, wrapper } = withSetup('3', 'tk')
    api.updateTitle(0, '这是一个非常长的提问'.repeat(4))
    expect(api.chats.value[0].title).toHaveLength(23)   // 20 字 + '...'
    expect(api.chats.value[0].title.endsWith('...')).toBe(true)
    wrapper.unmount()
  })

  test('卸载时把防抖中的改动落盘（不丢最后一次写入）', async () => {
    const { api, wrapper } = withSetup('3', 'tk')
    api.create()
    expect(localStorage.getItem(`${CHAT_HISTORY_KEY}:u3`)).toBeTruthy()

    api.chats.value[0].title = '改完就卸载'
    wrapper.unmount()   // 应当触发 flush
    expect(localStorage.getItem(`${CHAT_HISTORY_KEY}:u3`)).toContain('改完就卸载')
  })
})
