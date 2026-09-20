/** 多会话管理与会话导出的浏览器级验收（2026-09-20 借鉴项 P1）。
 *
 * 只按桌面口径验证交互（移动端左侧栏是抽屉，多会话流程在桌面更直接）；
 * 覆盖两条真实链路：新建 → 刷新保持 → 切回（localStorage v3 持久化），
 * 以及导出按钮 → 浏览器下载 → .md 内容含引用来源清单。
 */

import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

test.describe('多会话管理', () => {
  test('新建会话后切换与刷新都保持', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '会话列表交互按桌面口径验证')
    await page.goto('/')

    const input = page.getByRole('textbox', { name: '输入历史战争相关问题' })
    await input.fill('介绍一下长平之战。')
    await input.press('Enter')
    await expect(page.locator('.assistant-answer').first()).toBeVisible({ timeout: 45_000 })

    // 会话标题按首条提问自动生成（不足 15 字时就是原问题）
    await expect(page.locator('.session-item').first()).toContainText('介绍一下长平之战。')

    await page.getByRole('button', { name: '新建', exact: true }).click()
    await expect(page.locator('.session-item')).toHaveCount(2)
    await expect(page.locator('.assistant-answer')).toHaveCount(0)
    await expect(page.locator('.session-item').first()).toHaveClass(/active/)

    // 刷新后两条会话仍在（v3 结构落盘）
    await page.reload()
    await expect(page.locator('.session-item')).toHaveCount(2)

    // 切回第一个会话：那一轮回答恢复
    await page.locator('.session-item').nth(1).locator('.session-pick').click()
    await expect(page.locator('.assistant-answer').first()).toBeVisible()
  })

  test('导出当前会话下载 Markdown 且含引用来源清单', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '导出入口在顶栏，桌面口径验证')
    await page.goto('/')

    const input = page.getByRole('textbox', { name: '输入历史战争相关问题' })
    await input.fill('介绍一下赤壁之战。')
    await input.press('Enter')
    await expect(page.locator('.assistant-answer').first()).toBeVisible({ timeout: 45_000 })

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: '导出', exact: true }).click(),
    ])
    expect(download.suggestedFilename()).toMatch(/\.md$/)

    const path = await download.path()
    const content = readFileSync(path as string, 'utf-8')
    expect(content).toContain('· 会话导出')
    expect(content).toContain('**问题**')
    expect(content).toContain('**回答**')
    expect(content).toContain('**引用来源**')
    // 正文里的 [n] 引用必须在来源清单里有对应条目（不悬空）
    expect(content).toMatch(/\*\*\[1\]\*\* /)
  })
})
