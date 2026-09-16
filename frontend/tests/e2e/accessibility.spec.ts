/**
 * 浏览器级可访问性验收（2026-09-16 工作单 P1-13）。
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
  test('toast 使用常驻 live region', async ({ page }) => {
    await page.goto('/')
    const region = page.locator('.toast-region')
    await expect(region).toHaveAttribute('aria-live', 'polite')
  })

  test('prefers-reduced-motion 下动画被压制', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')
    const duration = await page.evaluate(() => {
      const el = document.querySelector('.qa-topbar')
      if (!el) return ''
      return getComputedStyle(el).transitionDuration
    })
    // 规则把过渡压到 0.001ms；只断言"不是正常量级"
    expect(duration).toMatch(/0\.001ms|0s/)
  })
})
