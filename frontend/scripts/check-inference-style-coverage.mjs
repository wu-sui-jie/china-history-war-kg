/** 检查"模板里用到的 class 都有样式兜底"（第 7 轮 W2 的拆分护栏）。
 *
 * 背景：inference 页的样式从"父组件 scoped"改成"index.css 按 `.inference-container` 命名空间
 * 生效"，模板又拆进了四个子组件。这类改法最容易出的事故是**某个 class 没人管了**——元素还在，
 * 样式悄悄掉了，类型检查与单测都发现不了。
 *
 * 做法：把页面与四个子组件模板里的 class 名都收集起来，逐个到 index.css（去掉命名空间前缀后）
 * 与各组件的 scoped 样式里找；找不到的列出来，由人判断是漏了还是本来就该由 layui 提供。
 * 退出码非 0 表示有未覆盖项（CI 可挂）。
 *
 * 用法：node scripts/check-inference-style-coverage.mjs
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'

const PAGE_DIR = path.resolve('src/views/inference')
const FILES = [
  'index.vue',
  'components/SessionSidebar.vue',
  'components/ChatMessages.vue',
  'components/ChatInput.vue',
  'components/KgNodeDrawer.vue',
]

/** 第三方/框架自带的 class：不在本页 CSS 里定义，跳过 */
const EXTERNAL = /^(lay-|iconfont$|icon-|layui-)/

/** 模板里用到、但**从来没有过**样式的 class（逐条与迁移前的 index.css 核对过，不是本次拆分丢的）：
 *  - kg-empty / kg-content-formatted：只是结构标记，样式一直由 layui 与 .markdown-body 承担。 */
const NEVER_STYLED = new Set(['kg-empty', 'kg-content-formatted'])

function collectClasses(source) {
  const names = new Set()
  // 静态 class="a b"
  for (const m of source.matchAll(/\sclass="([^"]*)"/g)) {
    m[1].split(/\s+/).filter(Boolean).forEach((c) => names.add(c))
  }
  // 动态 :class="{ 'a-b': cond }" 与 :class="['a', cond && 'b']"
  for (const m of source.matchAll(/:class="([^"]*)"/g)) {
    // 先把比较用的字符串字面量去掉（`role === 'user'` 里的 'user' 不是 class）
    const expr = m[1].replace(/[!=]==?\s*(['"])[^'"]*\1/g, '')
    for (const q of expr.matchAll(/'([^']+)'|"([^"]+)"/g)) {
      const value = q[1] || q[2]
      if (value && !/[{}()?]/.test(value)) names.add(value)
    }
  }
  return names
}

function collectSelectors(cssSource) {
  const selectors = new Set()
  // 去掉注释后抓选择器块（本页 CSS 全是 class 选择器，够用）
  const cleaned = cssSource.replace(/\/\*[\s\S]*?\*\//g, '')
  for (const m of cleaned.matchAll(/(^|\})([^{}@]+)\{/g)) {
    for (const c of m[2].matchAll(/\.([A-Za-z0-9_-]+)/g)) selectors.add(c[1])
  }
  return selectors
}

const pageCss = collectSelectors(readFileSync(path.join(PAGE_DIR, 'index.css'), 'utf-8'))

let missing = 0
for (const file of FILES) {
  const source = readFileSync(path.join(PAGE_DIR, file), 'utf-8')
  // 组件自带的 scoped 样式（抽屉那类 teleport 出去的）
  const ownStyle = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
    .map((m) => collectSelectors(m[1]))
    .reduce((acc, set) => new Set([...acc, ...set]), new Set())

  const gaps = [...collectClasses(source)]
    .filter((name) => !EXTERNAL.test(name) && !NEVER_STYLED.has(name))
    .filter((name) => !pageCss.has(name) && !ownStyle.has(name))

  if (gaps.length) {
    missing += gaps.length
    console.error(`✗ ${file}：${gaps.length} 个 class 在 index.css 与自身 scoped 样式里都没有规则`)
    gaps.forEach((name) => console.error(`    .${name}`))
  } else {
    console.log(`✓ ${file}：模板 class 全部有样式兜底`)
  }
}

process.exit(missing ? 1 : 0)
