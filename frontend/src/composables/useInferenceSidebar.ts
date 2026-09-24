/** 侧边栏状态与"图谱展开时的保护"（第 7 轮 W2 从 views/inference/index.vue 抽出）。
 *
 * 这块状态原先占页面 ~120 行，且与问答逻辑无关：移动端/桌面端的展开收起、窗口 resize 时的
 * 保持策略，以及展开图谱时那套"300ms 内锁住侧边栏 + 恢复 + 派发 resize"的保护。
 * 抽出来后页面只剩"引用与调用"，这段策略也能单独读。
 */

import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

export function useInferenceSidebar() {
  /** 是否移动端视口（<768px） */
  const isMobileView = ref(window.innerWidth < 768)
  const showSidebar = ref(true)
  /** 用户选择的展开状态：resize 后按它恢复 */
  const initialSidebarState = ref(true)
  /** 暂时锁住侧边栏状态（图谱展开期间不允许 resize 改动它） */
  const isSidebarLocked = ref(false)

  /** 手动切换展开/收起（界面上的折叠按钮与遮罩层用） */
  function toggle() {
    // 解锁侧边栏以允许状态改变
    isSidebarLocked.value = false
    showSidebar.value = !showSidebar.value
    initialSidebarState.value = showSidebar.value   // 保存用户选择的状态
    // 给一段时间后重新锁定侧边栏状态
    setTimeout(() => {
      isSidebarLocked.value = true
    }, 300)
  }

  /** 窗口尺寸变化：按"用户选择的初始状态"恢复，而不是每次自动折叠 */
  function handleResize() {
    isMobileView.value = window.innerWidth < 768

    // 如果侧边栏已锁定，则不改变其状态
    if (!isSidebarLocked.value) {
      if (window.innerWidth < 768) {
        // 在小屏幕上根据初始状态决定是否显示侧边栏
        showSidebar.value = initialSidebarState.value && isMobileView.value
      } else {
        // 在大屏幕上保持侧边栏状态
        showSidebar.value = initialSidebarState.value
      }
    }
  }

  /**
   * 图谱展开/收起时的保护（由消息列表子组件的 kg-toggle 事件触发）。
   *
   * 展开图谱会派发 resize，而 handleResize 会按窗口宽度改侧边栏；这里在 300ms 内锁住它，
   * 并在 DOM 更新后把侧边栏恢复成展开前的状态、再派发一次 resize 让图表重新量尺寸。
   */
  function onKgToggle(expanded: boolean) {
    isSidebarLocked.value = true

    if (expanded) {
      nextTick(() => {
        if (isSidebarLocked.value) {
          showSidebar.value = initialSidebarState.value
        }

        // 用setTimeout给DOM更新留出时间
        setTimeout(() => {
          if (isSidebarLocked.value) {
            showSidebar.value = initialSidebarState.value
          }

          // 不改变侧边栏状态，仅调整图表大小
          window.dispatchEvent(new Event('resize'))
        }, 100)
      })
    }

    // 300ms后解除侧边栏锁定
    setTimeout(() => {
      isSidebarLocked.value = false
    }, 300)
  }

  onMounted(() => {
    window.addEventListener('resize', handleResize)
    // 设置初始侧边栏状态
    initialSidebarState.value = true
    showSidebar.value = window.innerWidth >= 768
  })

  onBeforeUnmount(() => {
    window.removeEventListener('resize', handleResize)
  })

  return { isMobileView, showSidebar, initialSidebarState, isSidebarLocked, toggle, handleResize, onKgToggle }
}
