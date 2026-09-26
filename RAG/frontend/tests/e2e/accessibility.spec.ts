/**
 * 浏览器级可访问性验收。
 *
 * 只测"行为"，不测"属性是否存在"：键盘能否完成提问、抽屉焦点是否困住、
 * Escape 是否关闭并把焦点还回去、tabs 左右键是否切换。
 * 需要后端已启动（默认 http://127.0.0.1:8125，可用 RAG_BASE_URL 覆盖）。
 */

import { expect, test } from '@playwright/test'

test.describe('桌面端键盘路径', () => {
  test('键盘完成提问 → 得到回答 → 引用可点开', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面键盘路径只在桌面 project 跑')
    await page.goto('/')
    const input = page.getByRole('textbox', { name: '输入历史战争相关问题' })
    await expect(input).toBeVisible()
    await input.fill('介绍一下长平之战。')
    await input.press('Enter')

    // 有回答即视为链路走通（离线模式也会给出摘要回答）
    const answer = page.locator('.assistant-answer').first()
    await expect(answer).toBeVisible({ timeout: 45_000 })
    await expect(page.locator('.msg-meta').first()).toContainText(/已生成|依据不足|降级生成/)

    // 正文里的引用必须带可访问名称，并可点击
    const cite = page.locator('.answer-cite').first()
    await expect(cite).toBeVisible()
    await cite.click()
    await expect(page.locator('.evidence-row.active')).toBeVisible()
  })

  test('面板 tabs 支持左右键切换并保持 ARIA 状态', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '桌面面板常驻')
    await page.goto('/')
    const tablist = page.getByRole('tablist', { name: '知识面板视图' })
    await expect(tablist).toBeVisible()
    const firstTab = page.getByRole('tab', { name: '引用证据' })
    await firstTab.click()
    await expect(firstTab).toHaveAttribute('aria-selected', 'true')
    await firstTab.press('ArrowRight')
    await expect(page.getByRole('tab', { name: '实体卡' }))
      .toHaveAttribute('aria-selected', 'true')
  })
})

test.describe('移动端抽屉', () => {
  test.use({ viewport: { width: 420, height: 820 } })

  test('开关抽屉：焦点进入、Escape 关闭并回到触发按钮', async ({ page }) => {
    await page.goto('/')
    const trigger = page.getByRole('button', { name: /知识面板|收起面板/ })
    await expect(trigger).toBeVisible()
    // 确保从关闭状态开始
    if ((await trigger.textContent())?.includes('收起')) {
      await trigger.click()
    }
    await trigger.click()

    const dialog = page.getByRole('dialog', { name: '知识面板' })
    await expect(dialog).toBeVisible()
    // 焦点应落入抽屉内部
    await expect
      .poll(async () => dialog.evaluate((el) => el.contains(document.activeElement)))
      .toBe(true)
    // 抽屉内应该始终有一个可见的关闭按钮
    await expect(dialog.getByRole('button', { name: '关闭面板' })).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(trigger).toBeFocused()
  })

  test('抽屉焦点陷阱：Tab / Shift+Tab 都不会把焦点带到抽屉外', async ({ page }) => {
    // 焦点陷阱必须真正按 Tab 循环才算被证明：只验证"焦点进入抽屉"与 Escape 回焦，
    // 无法证明焦点不会逃逸。
    await page.goto('/')
    const trigger = page.getByRole('button', { name: /知识面板|收起面板/ })
    await expect(trigger).toBeVisible()
    if ((await trigger.textContent())?.includes('收起')) {
      await trigger.click()
    }
    await trigger.click()

    const dialog = page.getByRole('dialog', { name: '知识面板' })
    await expect(dialog).toBeVisible()

    const insideCount = async () =>
      dialog.evaluate((el) => el.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ).length)
    const focusables = await insideCount()
    expect(focusables).toBeGreaterThan(0)

    // 正向循环：按满一圈再多按两次，焦点必须始终留在抽屉内
    for (let i = 0; i < focusables + 2; i += 1) {
      await page.keyboard.press('Tab')
      const trapped = await dialog.evaluate((el) => el.contains(document.activeElement))
      expect(trapped, `第 ${i + 1} 次 Tab 后焦点逃出抽屉`).toBe(true)
    }

    // 反向循环：Shift+Tab 同样不得逃逸（从第一个元素往回绕到最后一个）
    for (let i = 0; i < focusables + 2; i += 1) {
      await page.keyboard.press('Shift+Tab')
      const trapped = await dialog.evaluate((el) => el.contains(document.activeElement))
      expect(trapped, `第 ${i + 1} 次 Shift+Tab 后焦点逃出抽屉`).toBe(true)
    }

    // 焦点确实在动（不是"所有按键都被吞掉"的假通过）
    const moved = await dialog.evaluate(() => document.activeElement?.textContent?.trim() || '')
    expect(moved.length).toBeGreaterThan(0)
  })

  test('移动端点击引用会自动打开抽屉并定位证据', async ({ page }) => {
    await page.goto('/')
    const input = page.getByRole('textbox', { name: '输入历史战争相关问题' })
    await input.fill('介绍一下赤壁之战。')
    await input.press('Enter')
    const cite = page.locator('.cite-chip').first()
    await expect(cite).toBeVisible({ timeout: 45_000 })
    await cite.click()
    const dialog = page.getByRole('dialog', { name: '知识面板' })
    await expect(dialog).toBeVisible()
    await expect(page.locator('.evidence-row.active')).toBeVisible()
  })
})

test.describe('动态内容播报与减少动效', () => {
  // 抽屉只在窄视口存在，而"减少动效"要断言的正是抽屉/蒙层的过渡，故用窄视口
  test.use({ viewport: { width: 420, height: 820 } })

  test('toast 使用常驻 live region', async ({ page }) => {
    await page.goto('/')
    const region = page.locator('.toast-region')
    await expect(region).toHaveAttribute('aria-live', 'polite')
  })

  test('prefers-reduced-motion 下动画被压制', async ({ page }) => {
    // 断言必须落在**真的有过渡**的元素上，并且同时测两种状态——
    // 只测 reduce 态会空转：元素本来就没有 transition 时任何断言都通过。
    await page.goto('/')
    const trigger = page.getByRole('button', { name: /知识面板|收起面板/ })
    await expect(trigger).toBeVisible()
    if ((await trigger.textContent())?.includes('收起')) {
      await trigger.click()
    }
    await trigger.click()
    await expect(page.getByRole('dialog', { name: '知识面板' })).toBeVisible()

    const durationSeconds = async () =>
      page.evaluate(() => {
        const el = document.querySelector('.panel-drawer-mask')
          || document.querySelector('.qa-panel-drawer')
        if (!el) return Number.NaN
        // 浏览器会把 0.001ms 规范化成 1e-06s 之类的科学计数法字符串，
        // 因此按"秒"解析数值再比较，而不是匹配固定字符串形态
        const raw = getComputedStyle(el).transitionDuration
        const value = parseFloat(raw)
        return raw.trim().endsWith('ms') ? value / 1000 : value
      })

    // 正常态：过渡确实存在（否则下面的压制断言毫无意义）
    const normal = await durationSeconds()
    expect(Number.isFinite(normal)).toBe(true)
    expect(normal).toBeGreaterThan(0.05)

    // reduce 态：被压到 0.001ms 量级（对应 1e-06 s）
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const reduced = await durationSeconds()
    expect(Number.isFinite(reduced)).toBe(true)
    expect(reduced).toBeLessThanOrEqual(0.001)
    expect(reduced).toBeLessThan(normal)
  })
})
