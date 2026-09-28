/** 「在新窗口打开」也必须能提问：主应用要把身份发给**新开的窗口**，不只是 iframe。

背景（线上真实故障）：RAG 的问答接口要求主应用签发的 token（`RAG_AUTH_MODE=jwt`），
而 token 只能由主应用 postMessage 下发。主应用原先用
`window.open(url, '_blank', 'noopener')`——`noopener` 下 `window.open` 返回 null，
身份根本发不出去，新窗口于是"页面能看、提问全是 401 未认证"。
修法：不带 noopener 开窗口，并在它**每次** load 后下发身份（token 只在内存里，
用户在新窗口按 F5 也得能拿到）；反向引用由 RAG 页自己在收到身份时切断
（utils/userScope.ts 的 severOpenerIfTopLevel），不给宿主留下反控入口。

这条用例用真实浏览器跑完整链路：宿主页（复刻主应用的新实现）开新窗口 →
新窗口收到身份 → 提问请求真的带上 `Token` 头 → 且 `window.opener` 已被清空。
*/

import assert from 'node:assert/strict'

import { expect, test } from '@playwright/test'

/** 宿主页：逐字复刻 frontend/src/views/knowledge/RagAssistant.vue 里 openInNewTab 的新实现。 */
const HOST_HTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>host</title></head>
<body>
<button id="open-new">在新窗口打开</button>
<script>
function postUserScopeTo(target) {
  target.postMessage({ type: 'cw-user', uid: '9', role: 'viewer', token: 'aaa.bbb.ccc' }, '/');
}
document.getElementById('open-new').addEventListener('click', function () {
  var opened = window.open('/', '_blank');
  if (!opened) return;                 // 被弹窗拦截时不报错，用户再点一次即可
  opened.addEventListener('load', function () { postUserScopeTo(opened); });
});
</script>
</body></html>`

test.describe('新窗口打开', () => {
  test('身份下发到新窗口，提问带上 Token 头', async ({ page, context }) => {
    const tokenSeen: Array<string | undefined> = []
    await context.route('**/api/query', async (route) => {
      tokenSeen.push(route.request().headers()['token'])
      // 只关心请求头：回一个最简 SSE 结束帧，避免前端一直等
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: 'data: {"type":"done","data":{}}\n\n',
      })
    })
    await page.route('**/__host.html', (route) =>
      route.fulfill({ contentType: 'text/html; charset=utf-8', body: HOST_HTML }),
    )

    await page.goto('/__host.html')
    const [opened] = await Promise.all([
      context.waitForEvent('page'),
      page.getByRole('button', { name: '在新窗口打开' }).click(),
    ])
    await opened.waitForLoadState('domcontentloaded')

    // 身份到达后新窗口自己切断反向引用（反向标签劫持的收口点）
    await expect
      .poll(async () => opened.evaluate(() => window.opener === null), { timeout: 10_000 })
      .toBe(true)

    await opened.getByRole('textbox', { name: '输入历史战争相关问题' }).fill('介绍一下长平之战')
    await opened.getByRole('textbox', { name: '输入历史战争相关问题' }).press('Enter')

    await expect.poll(() => tokenSeen.length, { timeout: 10_000 }).toBeGreaterThan(0)
    assert.equal(tokenSeen[0], 'aaa.bbb.ccc', '新窗口的提问必须带上主应用下发的凭证')
  })
})
