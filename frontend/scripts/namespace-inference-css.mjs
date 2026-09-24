/** 把 index.css 从"Vue scoped"改成"命名空间全局"（第 7 轮 W2 的一次性迁移脚本）。
 *
 * 为什么必须改：模板拆成子组件后，父组件 `<style scoped>` 只会给**父模板**里的元素打上
 * data-v 属性，子组件内部的元素不再匹配——页面会直接掉样式。而直接去掉 scoped 又会让
 * `.clear-button` / `.hint` / `.sidebar-header` 这些与 TextEntityExtract 等页面重名的规则
 * 溢出去（本项目实测有 9 个同名 class）。
 *
 * 做法：给每条规则的选择器加 `.inference-container ` 前缀（页面根元素），等价于"手写版
 * scoped"——只在问答页的 DOM 子树里生效，且**规则内容与先后顺序一字不动**，因此层叠关系
 * 与改动前完全一致。已经提到 `.inference-container` 的根规则保持原样（否则会自己嵌自己）。
 *
 * 安全性由脚本自带：写回前把加过的前缀去掉，与输入逐字比对，不一致就中止（fail-closed），
 * 并打印三类计数（前缀 / 根规则 / keyframes 内跳过）。幂等：对已加前缀的文件再跑一次会
 * 因"去前缀后不一致"而拒绝写回，不会二次加前缀。
 *
 * 用法：node scripts/namespace-inference-css.mjs
 */

import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import postcss from 'postcss'

const target = path.resolve('src/views/inference/index.css')
const ROOT = '.inference-container'

const css = readFileSync(target, 'utf-8')
const root = postcss.parse(css)

let prefixed = 0
let keptRoot = 0
let skippedKeyframes = 0

root.walkRules((rule) => {
  // @keyframes 里的 from/to/50% 不是选择器，跳过
  if (rule.parent?.type === 'atrule' && /keyframes/i.test(rule.parent.name)) {
    skippedKeyframes += 1
    return
  }
  if (rule.selectors.some((sel) => sel.includes(ROOT))) {
    keptRoot += 1
    return
  }
  rule.selectors = rule.selectors.map((sel) => `${ROOT} ${sel}`)
  prefixed += 1
})

// 写回前的等价校验：去掉刚加的前缀后必须与输入逐字相同
const stripped = root.clone()
stripped.walkRules((rule) => {
  if (rule.parent?.type === 'atrule' && /keyframes/i.test(rule.parent.name)) return
  rule.selectors = rule.selectors.map((sel) => sel.replace(`${ROOT} `, ''))
})

if (stripped.toString() !== css) {
  console.error('✗ 去前缀后与原文不一致，已中止（没有写回）')
  process.exit(1)
}

writeFileSync(target, root.toString(), 'utf-8')
console.log(`✓ 已写回 ${target}`)
console.log(`  前缀 ${prefixed} 条规则、根规则 ${keptRoot} 条保持原样、keyframes 内跳过 ${skippedKeyframes} 条；等价校验通过`)
