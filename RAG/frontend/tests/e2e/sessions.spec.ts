/** 多会话管理与会话导出的浏览器级验收。
 *
 * 只按桌面口径验证交互（移动端左侧栏是抽屉，多会话流程在桌面更直接）；
 * 覆盖三条真实链路：新建 → 刷新保持 → 切回（localStorage v3 持久化）、
 * 导出按钮 → 浏览器下载 → .md 内容含引用来源清单，
 * 以及**主应用嵌入形态**（sandbox iframe，无 allow-modals）下的会话改名与删除。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

/** 主应用嵌入页（frontend/src/views/knowledge/RagAssistant.vue）里那份 sandbox 串。
 *
 * 直接读源文件而不是在这里抄一份：这条用例要验的就是"嵌入形态下的会话操作"，
 * 抄一份就只测了抄写时的那份、源文件改了也不知道。RagAssistant.vue 相对本文件
 * 位于 RAG/frontend/tests/e2e → 上溯四级再进 frontend/src/views/knowledge。
 */
function embedSandbox(): string {
  const file = new URL(
    '../../../../frontend/src/views/knowledge/RagAssistant.vue',
    import.meta.url,
  )
  const source = readFileSync(file, 'utf-8')
  const matched = /sandbox="([^"]+)"/.exec(source)
  expect(matched, 'RagAssistant.vue 里应当有一处 sandbox 声明').not.toBeNull()
  return matched![1]
}

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

  /** 会话标题很长时，操作按钮必须还是"一行两个字"的正常按钮。
   *
   * 这是线上发生过的真实缺陷：`.session-actions` 没禁收缩，而中文可在任意两字之间折行，
   * 于是长标题把「改名」「删除」压成一字宽的两行竖条（线上实测 30×21 → 19×38），
   * 用户点上去多半落在缝里，表现成"按钮点了没反应"。
   * 只能靠真实浏览器量几何——jsdom 不算布局，组件用例拦不住这类回归。
   */
  test('长标题不会把改名/删除挤成竖条', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '布局按桌面口径验证')
    await page.goto('/')

    const item = page.locator('.session-item').first()
    await item.hover()
    await item.getByRole('button', { name: /重命名会话/ }).click()
    await page.locator('.session-edit-input').fill('巨鹿之战的楚军主帅到底是谁这个问题值得好好研究一下')
    await page.locator('form.session-inline').getByRole('button', { name: '保存' }).click()
    await expect(item.locator('.session-title')).toHaveText('巨鹿之战的楚军主帅到底是谁这个问题值得好好研究一下')

    const rename = await item.getByRole('button', { name: /重命名会话/ }).boundingBox()
    const remove = await item.getByRole('button', { name: /删除会话/ }).boundingBox()
    const box = await item.boundingBox()
    assert.ok(rename && remove && box, '按钮与条目都应有几何信息')

    // 一行两个字：宽 ≥ 24px（11px 字号两字 + padding）、高 ≤ 26px（不换行）
    assert.ok(rename!.width >= 24, `改名按钮被挤窄了：${Math.round(rename!.width)}px`)
    assert.ok(rename!.height <= 26, `改名按钮被挤成两行了：${Math.round(rename!.height)}px`)
    assert.ok(remove!.width >= 24, `删除按钮被挤窄了：${Math.round(remove!.width)}px`)
    assert.ok(remove!.height <= 26, `删除按钮被挤成两行了：${Math.round(remove!.height)}px`)
    assert.equal(Math.round(rename!.y), Math.round(remove!.y), '两个按钮应在同一行')
    // 都落在条目范围内：溢出到条目外就会被容器裁掉、点不到
    const right = remove!.x + remove!.width
    assert.ok(right <= box!.x + box!.width + 1, '删除按钮溢出条目右侧，会被裁掉')
    assert.ok(rename!.x >= box!.x - 1, '改名按钮溢出条目左侧')
    // 标题让位（省略号），不该反压按钮
    assert.ok(box!.width <= 320, `会话条目被撑过宽：${Math.round(box!.width)}px`)
  })
})

test.describe('主应用嵌入形态（sandbox iframe）', () => {
  /** 会话改名与删除必须不依赖 window.prompt / window.confirm。
   *
   * 主应用用 iframe 嵌本页（RagAssistant.vue），那份 sandbox 没有 allow-modals：
   * 按 HTML 规范，confirm 恒返回 false、prompt 恒返回 null，原生弹窗被静默忽略，
   * 页面表现就是"改名、删除点了没反应"（而"新建"不弹窗、照常可用）。
   * 这里用真实的 sandbox 属性复刻嵌入形态，锁住"页内控件"这条实现。 */
  test('改名与删除在 sandbox iframe 内可用', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', '嵌入形态按桌面口径验证')

    const sandbox = embedSandbox()
    await page.route('**/embed-host.html', (route) =>
      route.fulfill({
        contentType: 'text/html; charset=utf-8',
        body:
          '<!doctype html><meta charset="utf-8">' +
          `<iframe id="rag-embed" src="/" style="width:1400px;height:900px;border:0" sandbox="${sandbox}"></iframe>`,
      }),
    )
    await page.goto('/embed-host.html')

    const frame = page.frameLocator('#rag-embed')
    const input = frame.getByRole('textbox', { name: '输入历史战争相关问题' })
    await input.fill('介绍一下长平之战。')
    await input.press('Enter')
    await expect(frame.locator('.assistant-answer').first()).toBeVisible({ timeout: 45_000 })

    // 改名：点「改名」出页内输入框，保存后标题更新
    const item = frame.locator('.session-item').first()
    await item.hover()
    await item.getByRole('button', { name: /重命名会话/ }).click()
    await frame.locator('.session-edit-input').fill('我改的会话名')
    await frame.locator('form.session-inline').getByRole('button', { name: '保存' }).click()
    await expect(frame.locator('.session-item').first()).toContainText('我改的会话名')

    // 删除：第一次点击只出确认，取消后会话仍在
    await item.hover()
    await item.getByRole('button', { name: /删除会话/ }).click()
    await expect(frame.locator('.session-confirm-text')).toBeVisible()
    await frame.locator('.session-inline-actions').getByRole('button', { name: '取消' }).click()
    await expect(frame.locator('.session-item')).toHaveCount(1)
    await expect(frame.locator('.session-item').first()).toContainText('我改的会话名')

    // 真删：删掉唯一会话后自动补一条空会话（与会话列表口径一致）
    await item.hover()
    await item.getByRole('button', { name: /删除会话/ }).click()
    await frame.locator('.session-inline-actions').getByRole('button', { name: '确认删除' }).click()
    await expect(frame.locator('.session-item')).toHaveCount(1)
    await expect(frame.locator('.session-item').first()).toContainText('新会话')
    await expect(frame.locator('.assistant-answer')).toHaveCount(0)
  })
})
