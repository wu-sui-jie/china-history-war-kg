#!/usr/bin/env node
/**
 * 首屏产物体积门禁。
 *
 * `chunkSizeWarningLimit` 只打印警告，不能阻止有人在入口里静态 import ECharts，
 * 使 1MB 的重资源被 modulepreload 拖回首屏。这里在 CI/发布前做硬检查：
 *   1. index.html 不得预加载 ECharts / 地图 chunk；
 *   2. 入口 + vendor 的 gzip 体积不得超过预算；
 *   3. 首屏 JS 总量（entry + vendor + 预加载 chunk）不得超过预算。
 *
 * ## 为什么必须按产物自己写的前缀解析
 *
 * 写死 `/assets/` 前缀时，`npm run build:integration` 的产物引用的是
 * `/rag/assets/...`，正则一条都匹配不上，于是三个体积全是 0、"体积门禁通过"——
 * 一个永远为真的门禁等于没有门禁，而且它会掩盖真实回归。
 * 因此这里从 index.html 里出现的 `src`/`href` 里**自己推断** assets 目录，
 * 并且**找不到入口或 vendor 时直接失败**，不允许出现"0 kB 通过"。
 *
 * 用法：node scripts/check-bundle.mjs [--dist dist] [--json]
 */

import { existsSync, readFileSync, statSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { basename, dirname, join, resolve } from 'node:path'

/** 首屏预算（gzip 后的字节数）。调整预算需要同步改这里与 CI 文档。 */
export const BUDGET = {
  entryGzip: 80 * 1024,      // 应用入口（不含 vendor）
  vendorGzip: 110 * 1024,    // vue/pinia/markdown-it 等运行时
  firstPaintGzip: 190 * 1024, // 首屏实际要下载的 JS 总量
}

/** 必须按需加载、不得出现在 index.html 里的 chunk 关键字。 */
const LAZY_ONLY = ['echarts', 'china-map', 'zrender']

function gzipSize(path) {
  try {
    return gzipSync(readFileSync(path), { level: 9 }).length
  } catch {
    return 0
  }
}

function kb(bytes) {
  return `${(bytes / 1024).toFixed(1)} kB`
}

/** 抽出 index.html 里所有本站脚本引用（跳过 data: 与外部 URL）。 */
function collectScriptRefs(html) {
  const refs = []
  for (const m of html.matchAll(/(?:src|href)="([^"]+\.js)"/g)) {
    const url = m[1]
    if (url.startsWith('data:') || /^[a-z][a-z0-9+.-]*:\/\//i.test(url)) continue
    refs.push(url.startsWith('/') ? url : `/${url}`)
  }
  return refs
}

/** modulepreload 的引用（与上面同口径）。 */
function collectPreloadRefs(html) {
  const refs = []
  for (const m of html.matchAll(/rel="modulepreload"[^>]*href="([^"]+\.js)"/g)) {
    const url = m[1]
    if (url.startsWith('data:') || /^[a-z][a-z0-9+.-]*:\/\//i.test(url)) continue
    refs.push(url.startsWith('/') ? url : `/${url}`)
  }
  return refs
}

export function checkBundle(distDir = 'dist') {
  const dist = resolve(distDir)
  const failures = []
  const warnings = []
  let html
  try {
    html = readFileSync(join(dist, 'index.html'), 'utf8')
  } catch {
    return { ok: false, failures: [`找不到构建产物：${join(dist, 'index.html')}（先 npm run build）`],
             warnings, sizes: {} }
  }

  const scriptRefs = collectScriptRefs(html)
  // 前缀从产物自身推断：取第一条引用的目录部分（/assets/ 或 /rag/assets/）
  const assetsPrefix = scriptRefs.length ? dirname(scriptRefs[0]) + '/' : ''
  const dirOf = (url) => url.replace(/\/[^/]*$/, '/')
  const nameOf = (url) => basename(url)

  const referenced = scriptRefs.filter((url) => dirOf(url) === assetsPrefix)
  const preloaded = collectPreloadRefs(html).filter((url) => dirOf(url) === assetsPrefix)

  /**
   * URL → 产物文件路径。
   *
   * 不能简单去掉开头的 `/`：base 是 `/rag/` 时（build:integration），页面引用
   * `/rag/assets/x.js`，但文件在 `dist/assets/x.js`——base 只出现在 URL 里，不进 dist 目录结构。
   * 所以按 `/assets/` 这个锚点切，与 base 前缀无关。
   */
  const fileOf = (url) => {
    const idx = url.indexOf('/assets/')
    return idx >= 0 ? join(dist, url.slice(idx + 1)) : join(dist, url.replace(/^\//, ''))
  }

  const modeMarker = join(dist, 'build-mode.txt')
  const buildMode = existsSync(modeMarker)
    ? readFileSync(modeMarker, 'utf8').split('\n')[0].trim()
    : '（无标记）'

  const firstPaint = new Set([...referenced, ...preloaded])

  for (const url of firstPaint) {
    const lower = nameOf(url).toLowerCase()
    const hit = LAZY_ONLY.find((kw) => lower.includes(kw))
    if (hit) {
      failures.push(`index.html 预加载了按需 chunk「${url}」（匹配 ${hit}）：` +
        `重资源必须保持懒加载`)
    }
  }

  const sizes = {}
  let firstPaintTotal = 0
  for (const url of firstPaint) {
    const file = fileOf(url)
    if (!existsSync(file)) {
      failures.push(`index.html 引用了 ${url}，但产物里没有 ${file.slice(dist.length + 1)}`)
      continue
    }
    const gz = gzipSize(file)
    sizes[nameOf(url)] = gz
    firstPaintTotal += gz
  }
  const entryUrl = [...firstPaint].find((n) => nameOf(n).startsWith('index-'))
  const vendorUrl = [...firstPaint].find((n) => nameOf(n).startsWith('vendor-'))
  const entryGz = entryUrl ? sizes[nameOf(entryUrl)] : 0
  const vendorGz = vendorUrl ? sizes[nameOf(vendorUrl)] : 0

  // 关键：拿不到入口/vendor 时**不能**按 0 通过。
  // 前缀对不上就会匹配为空 → 三个 0 kB → "通过"，必须判失败。
  if (!entryUrl) {
    failures.push(
      `在 index.html 里没找到入口 chunk（预期 ${assetsPrefix || '任意前缀'}index-*.js）：` +
      `实际引用 ${scriptRefs.length} 条。门禁前缀可能与产物不符，请先修解析再放行`)
  }
  if (!vendorUrl) {
    // vendor 缺失只可能是构建配置变了（拆分规则被改掉）；同样不能静默按 0 计
    failures.push(
      `在 index.html 里没找到 vendor chunk（预期 ${assetsPrefix || '任意前缀'}vendor-*.js）：` +
      '若确实改了 chunk 拆分规则，请同步更新本门禁与预算口径')
  }

  if (entryGz > BUDGET.entryGzip) {
    failures.push(`入口 chunk ${entryUrl} gzip ${kb(entryGz)} 超过预算 ${kb(BUDGET.entryGzip)}`)
  }
  if (vendorGz > BUDGET.vendorGzip) {
    failures.push(`vendor chunk ${vendorUrl} gzip ${kb(vendorGz)} 超过预算 ${kb(BUDGET.vendorGzip)}`)
  }
  if (firstPaintTotal > BUDGET.firstPaintGzip) {
    failures.push(`首屏 JS gzip 合计 ${kb(firstPaintTotal)} 超过预算 ${kb(BUDGET.firstPaintGzip)}`)
  }
  // 兜底提醒：按需 chunk 原始体积很大不算失败，但值得看一眼
  try {
    for (const url of firstPaint) {
      const file = fileOf(url)
      if (statSync(file).size > 1.5 * 1024 * 1024) {
        warnings.push(`chunk ${nameOf(url)} 原始体积 ` +
          `${(statSync(file).size / 1024 / 1024).toFixed(2)} MB（按需加载，可接受）`)
      }
    }
  } catch {
    // 忽略：stat 失败不影响门禁结论
  }

  return {
    ok: failures.length === 0,
    failures,
    warnings,
    sizes: { entryGzip: entryGz, vendorGzip: vendorGz, firstPaintGzip: firstPaintTotal,
             assetsPrefix, buildMode, entry: entryUrl, vendor: vendorUrl, preloaded,
             budget: BUDGET },
  }
}

const invokedDirectly = process.argv[1] && import.meta.url.endsWith(
  process.argv[1].replace(/\\/g, '/').split('/').pop())
if (invokedDirectly) {
  const args = process.argv.slice(2)
  const distArg = args.indexOf('--dist')
  const result = checkBundle(distArg >= 0 ? args[distArg + 1] : 'dist')
  if (args.includes('--json')) {
    console.log(JSON.stringify(result, null, 2))
  } else {
    console.log(`构建模式 ${result.sizes.buildMode || '（未解析）'}`
      + ` / 资源前缀 ${result.sizes.assetsPrefix || '（未解析到）'}`
      + ` / 入口 ${result.sizes.entry || '（未找到）'} gzip ${kb(result.sizes.entryGzip || 0)}`
      + ` / vendor ${result.sizes.vendor || '（未找到）'} gzip ${kb(result.sizes.vendorGzip || 0)}`
      + ` / 首屏合计 gzip ${kb(result.sizes.firstPaintGzip || 0)}`)
    for (const w of result.warnings) console.log(`提示：${w}`)
    for (const f of result.failures) console.error(`失败：${f}`)
    console.log(result.ok ? '体积门禁通过' : '体积门禁未通过')
  }
  process.exit(result.ok ? 0 : 1)
}
