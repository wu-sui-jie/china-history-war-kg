/** 复制到剪贴板（第 7 轮 W2 从 index.vue 抽出）。
 *
 * 两条路径：正规的 `navigator.clipboard` 与旧浏览器的临时文本域回退。
 * 返回是否成功，由调用方决定提示什么。
 */

/** 回退方案：临时文本域 + `document.execCommand('copy')`，并恢复用户原有的选择范围。 */
function fallbackCopy(text: string): boolean {
  const textArea = document.createElement('textarea')
  textArea.value = text

  // 确保元素不可见
  textArea.style.position = 'fixed'
  textArea.style.left = '-999999px'
  textArea.style.top = '-999999px'
  document.body.appendChild(textArea)

  // 保存用户的选择范围
  const selection = document.getSelection()
  const selected = (selection?.rangeCount ?? 0) > 0 ? selection?.getRangeAt(0) : null

  // 选择文本
  textArea.select()
  textArea.setSelectionRange(0, textArea.value.length)

  let succeeded = false
  try {
    document.execCommand('copy')
    succeeded = true
  } catch (err) {
    succeeded = false
  }

  // 移除元素
  document.body.removeChild(textArea)

  // 恢复用户的选择
  if (selected && selection) {
    selection.removeAllRanges()
    selection.addRange(selected)
  }

  return succeeded
}

/** 复制文本：优先剪贴板 API，失败或不可用时走回退方案。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
    return fallbackCopy(text)
  } catch (error) {
    return fallbackCopy(text)
  }
}
