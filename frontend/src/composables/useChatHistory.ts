/** 问答记录的持有与持久化（第 7 轮 W2 从 views/inference/index.vue 抽出）。
 *
 * 它是"按账号隔离"这条链路的落点，抽出来的好处是：隔离规则（钉住 uid、账号未知时不碰公共桶、
 * 老全局记录归档）能单独测，不必挂载整个页面。
 *
 * 存储规则见 utils/userScopedStorage.ts；本模块只负责"什么时候读、什么时候写"。
 */

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { readScoped, writeScoped } from '../utils/userScopedStorage'
import { formatChatTime } from '../utils/date'
import type { ChatSession } from '../types/inference'

/** 存储 key 前缀（按账号隔离后的实际 key 由 utils/userScopedStorage 拼） */
export const CHAT_HISTORY_KEY = 'chatHistory'

/** 容量上限：localStorage 一般只有 ~5MB，而流式回答很长，不设上限若干轮后必然写不进去 */
const MAX_CHATS = 20
const MAX_MESSAGES_PER_CHAT = 200

/** 自动落盘的合并窗口：流式回答逐字追加，直接写会把整份历史 stringify 上千次 */
const SAVE_DEBOUNCE_MS = 500

export interface UseChatHistoryOptions {
  /** 读本页面所属的账号 id（挂载时钉住的那个，不跟随 store 变化） */
  uid: () => string | number | undefined
  /** 已登录但拿不到账号 id：此时既不读也不写公共桶（见第 6 轮 H1） */
  isAccessBlocked: () => boolean
  /**
   * 落盘彻底失败时通知界面（第 14 轮审计 P2-14）。
   *
   * 可选：不传就只留一条 console.warn。但这**不是可有可无的美化**——
   * 聊天记录写不进去意味着"刷新就全丢"，用户有权知道，而不是等到刷新后
   * 发现对话没了才开始怀疑"是不是这个系统本来就不保存"。
   */
  onPersistFailed?: () => void
}

export function useChatHistory(options: UseChatHistoryOptions) {
  const chats = ref<ChatSession[]>([
    { id: 1, title: '新对话', messages: [], lastTime: Date.now() },
  ])
  const currentIndex = ref(0)
  const current = computed(() => chats.value[currentIndex.value])

  /** 收缩到上限内：单会话保留最近的消息，会话数按最近使用保留 */
  function trim() {
    for (const chat of chats.value) {
      if (Array.isArray(chat.messages) && chat.messages.length > MAX_MESSAGES_PER_CHAT) {
        chat.messages = chat.messages.slice(-MAX_MESSAGES_PER_CHAT)
      }
    }
    if (chats.value.length > MAX_CHATS) {
      chats.value = [...chats.value]
        .sort((a, b) => (b.lastTime || 0) - (a.lastTime || 0))
        .slice(0, MAX_CHATS)
    }
  }

  /** 立即落盘（按账号分 key；账号未知但已登录时跳过） */
  function save() {
    if (options.isAccessBlocked()) return
    // 用**返回值**判断成败，而不是 try/catch（第 14 轮审计 P2-14）：
    // writeScoped 早先吞掉一切异常且不返回任何东西，于是下面这套降级永远不执行——
    // localStorage 写满后聊天记录静默停摆，用户完全看不出来。
    if (writeScoped(CHAT_HISTORY_KEY, options.uid(), chats.value)) return
    // 配额不够：丢掉一半会话再试一次
    trim()
    chats.value = chats.value.slice(0, Math.max(1, Math.floor(MAX_CHATS / 2)))
    if (writeScoped(CHAT_HISTORY_KEY, options.uid(), chats.value)) {
      console.warn('聊天记录超出 localStorage 配额，已丢弃较旧的会话')
      return
    }
    // 仍然写不进去：这是"刷新就会丢"的状态，必须让用户看见，而不是只写控制台
    console.warn('聊天记录写入 localStorage 失败，本次不保存历史')
    options.onPersistFailed?.()
  }

  let saveTimer: number | undefined

  /** 合并写入：500ms 内的多次改动只落盘一次（流式回答期间每帧都会触发） */
  function scheduleSave() {
    if (saveTimer) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(() => {
      saveTimer = undefined
      save()
    }, SAVE_DEBOUNCE_MS)
  }

  /** 立刻把待写的改动落盘（组件卸载/离开页面前调用） */
  function flush() {
    if (saveTimer) {
      window.clearTimeout(saveTimer)
      saveTimer = undefined
    }
    save()
  }

  /** 读本账号的记录；老全局记录由 readScoped 归档，不归属任何账号 */
  function load(): void {
    if (options.isAccessBlocked()) return
    const stored = readScoped<ChatSession[]>(CHAT_HISTORY_KEY, options.uid(), [])
    if (Array.isArray(stored) && stored.length) {
      chats.value = stored
    }
  }

  /** 新建会话并切到它（返回新会话，页面可据此复位输入与实体卡片） */
  function create(): ChatSession {
    const now = Date.now()
    const chat: ChatSession = {
      id: now,
      title: `新对话 ${formatChatTime(now)}`,
      messages: [],
      lastTime: now,
    }
    chats.value.unshift(chat)
    currentIndex.value = 0
    save()
    return chat
  }

  function switchTo(index: number) {
    currentIndex.value = index
  }

  /** 删除会话；只剩一个时清空它而不是删掉（列表不能为空） */
  function remove(index: number) {
    if (chats.value.length <= 1) {
      chats.value[0].messages = []
      chats.value[0].title = '新对话'
      chats.value[0].lastTime = Date.now()
    } else {
      chats.value.splice(index, 1)
      if (index === currentIndex.value) {
        currentIndex.value = 0
      } else if (index < currentIndex.value) {
        currentIndex.value--
      }
    }
    save()
  }

  /** 用首条提问给会话命名（超过 20 字截断） */
  function updateTitle(index: number, firstMessage: string) {
    const chat = chats.value[index]
    if (chat) {
      chat.title = firstMessage.length > 20 ? firstMessage.substring(0, 20) + '...' : firstMessage
    }
  }

  // 任何改动都合并落盘（深度监听：消息内容会被流式逐字改写）
  watch(chats, () => scheduleSave(), { deep: true })

  // 组件卸载时把防抖中的改动落盘：不清掉定时器会丢最后一次写入
  onBeforeUnmount(flush)

  return { chats, currentIndex, current, load, save, scheduleSave, flush, create, switchTo, remove, updateTitle }
}
