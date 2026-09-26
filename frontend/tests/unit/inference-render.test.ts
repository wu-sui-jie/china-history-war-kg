/** 问答页渲染工具的用例。
 *
 * 这些函数是 XSS 防线的落点（模型输出与图谱数据经 v-html 上屏），
 * 所以这里重点钉两件事：
 *  1) 该渲染的能渲染（Markdown、图谱上下文两套格式、JSON 转表格、思考过程）；
 *  2) 不该执行的绝不执行（<script> / onerror / javascript: 一律净掉）。
 */

import { describe, expect, test } from 'vitest'

import {
  buildKgContextHtml,
  escapeHtml,
  jsonObjectToMarkdownTable,
  renderFormattedKgContext,
  renderJsonTableMarkdown,
  renderKgContextMarkdown,
  renderMarkdown,
  renderThinkingContent,
  sanitizeHtml,
} from '@/utils/inference-render'

describe('escapeHtml / sanitizeHtml', () => {
  test('转义四个 HTML 元字符', () => {
    expect(escapeHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;')
  })

  test('净化去掉脚本与事件属性', () => {
    const cleaned = sanitizeHtml('<div><script>alert(1)</script><img src=x onerror=alert(2)></div>')
    expect(cleaned).not.toContain('<script')
    expect(cleaned).not.toContain('onerror')
    expect(sanitizeHtml('')).toBe('')
  })
})

describe('renderMarkdown', () => {
  test('渲染常规 Markdown', () => {
    expect(renderMarkdown('**粗体**')).toContain('<strong>粗体</strong>')
    expect(renderMarkdown('# 标题')).toContain('<h1')
    expect(renderMarkdown('')).toBe('')
  })

  test('代码块走高亮分支', () => {
    const html = renderMarkdown('```js\nconst a = 1 < 2;\n```')
    expect(html).toContain('<pre class="hljs">')
    expect(html).toContain('code')
  })

  test('原始 HTML 不直通（html: false + 净化）', () => {
    // md 开 html: false，标签被转义成文本（&lt;img ...&gt;），因此不构成标签
    const html = renderMarkdown('<img src=x onerror=alert(1)>')
    expect(html).not.toContain('<img')
    expect(renderMarkdown('<script>alert(1)</script>')).not.toContain('<script')
  })
})

describe('renderThinkingContent', () => {
  test('转义后套样式，脚本不执行', () => {
    const html = renderThinkingContent('先看 <script>alert(1)</script> 再判断')
    expect(html).not.toContain('<script')
    expect(html).toContain('&lt;script&gt;')
    expect(renderThinkingContent('')).toBe('')
  })

  test('代码块包成 thinking-code', () => {
    expect(renderThinkingContent('```\nx\n```')).toContain('thinking-code')
  })
})

describe('buildKgContextHtml（【】文本格式）', () => {
  const context = [
    '【涉及实体】',
    '⚔️ 巨鹿之战 (Event)',
    '👤 项羽 (Person)',
    '【直接关系】',
    '1. 巨鹿之战 →【参战】→ 项羽',
    '【推理关系】',
    '2. 项羽 →【统领】→ 楚军',
  ].join('\n')

  test('实体与关系都渲染成结构化 HTML', () => {
    const html = buildKgContextHtml(context)
    expect(html).toContain('kg-section')
    expect(html).toContain('kg-entity-tag')
    expect(html).toContain('data-type="Event"')
    expect(html).toContain('巨鹿之战')
    expect(html).toContain('kg-relation-item')
    expect(html).toContain('kg-relations inferred')
  })

  test('没有【】标记的旧格式回退成 pre', () => {
    const html = buildKgContextHtml('一段没有任何章节标记的说明')
    expect(html).toContain('<pre')
  })

  test('空值返回空串', () => {
    expect(buildKgContextHtml('')).toBe('')
    expect(renderKgContextMarkdown('')).toBe('')
  })
})

describe('JSON 转换', () => {
  test('普通对象转表格', () => {
    const html = jsonObjectToMarkdownTable({ 朝代: '秦', 地点: '巨鹿' })
    expect(html).toContain('<table')
    expect(html).toContain('朝代')
    expect(html).toContain('巨鹿')
  })

  test('空数组与空值走提示分支；空对象仍出表头', () => {
    // 原实现的判据是 `!json || typeof json !== 'object' || (Array.isArray(json) && json.length === 0)`，
    // 所以 [] 与 null 提示"无有效数据"，{} 会渲染成只有表头的表——照旧保留
    expect(jsonObjectToMarkdownTable([])).toContain('无有效数据')
    expect(jsonObjectToMarkdownTable(null)).toContain('无有效数据')
    expect(jsonObjectToMarkdownTable({})).toContain('<table')
  })

  test('图谱结构（nodes/lines）转实体表', () => {
    const html = renderJsonTableMarkdown({
      nodes: [{ id: 1, name: '巨鹿之战', type: 'Event' }],
      lines: [],
    })
    expect(html).toContain('<table')
    expect(html).toContain('巨鹿之战')
  })

  test('JSON 字符串走 buildKgContextHtml 的 JSON 分支', () => {
    const html = buildKgContextHtml(JSON.stringify({ nodes: [{ id: 1, name: '项羽', type: 'Person' }], lines: [] }))
    expect(html).toContain('<table')
    expect(html).toContain('项羽')
  })

  test('JSON 里的脚本内容也会被净化', () => {
    const html = sanitizeHtml(jsonObjectToMarkdownTable({ name: '<script>alert(1)</script>' }))
    expect(html).not.toContain('<script')
  })
})

describe('renderFormattedKgContext 兜底', () => {
  test('只有外壳没有内容时回退成 pre（避免空白区域）', () => {
    expect(renderFormattedKgContext('【空章节】')).toContain('<pre')
  })
})
