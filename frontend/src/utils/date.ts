/** 对话/历史列表用的时间展示：当天只给时分，跨天再带上月日。
 *  inference/index.vue 与 knowledge/TextEntityExtract.vue 共用这一份，不要各写一份。 */
export const formatChatTime = (timestamp: number): string => {
  const date = new Date(timestamp)
  const now = new Date()
  const time = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (date.toDateString() === now.toDateString()) {
    return time
  }
  return `${date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })} ${time}`
}
