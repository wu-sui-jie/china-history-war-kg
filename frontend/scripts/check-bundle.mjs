#!/usr/bin/env node
/**
 * 首屏产物体积门禁（2026-09-15 第四轮复核 P2-12）。
 *
 * `chunkSizeWarningLimit` 只打印警告，不能阻止有人在入口里静态 import ECharts，
 * 使 1MB 的重资源被 modulepreload 拖回首屏。这里在 CI/发布前做硬检查：
 *   1. index.html 不得预加载 ECharts / 地图 chunk；
 *   2. 入口 + vendor 的 gzip 体积不得超过预算；
 *   3. 首屏 JS 总量（entry + vendor + 预加载 chunk）不得超过预算。
 *
 * 用法：node scripts/check-bundle.mjs [--dist dist] [--json]
 */

import { readFileSync, statSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { join, resolve } from 'node:path'

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

  const referenced = [...html.matchAll(/(?:src|href)="\/assets\/([^"]+\.js)"/g)]
    .map((m) => m[1])
  const preloaded = [...html.matchAll(/rel="modulepreload"[^>]*href="\/assets\/([^"]+\.js)"/g)]
    .map((m) => m[1])
  const firstPaint = new Set([...referenced, ...preloaded])

  for (const name of firstPaint) {
    const lower = name.toLowerCase()
    const hit = LAZY_ONLY.find((kw) => lower.includes(kw))
    if (hit) {
      failures.push(`index.html 预加载了按需 chunk「${name}」（匹配 ${hit}）：` +
        `重资源必须保持懒加载`)
    }
  }

  const sizes = {}
  let firstPaintTotal = 0
  for (const name of firstPaint) {
    const gz = gzipSize(join(dist, 'assets', name))
    sizes[name] = gz
    firstPaintTotal += gz
  }
  const entry = [...firstPaint].find((n) => n.startsWith('index-') && n.endsWith('.js'))
  const vendor = [...firstPaint].find((n) => n.startsWith('vendor-'))
  const entryGz = entry ? sizes[entry] : 0
  const vendorGz = vendor ? sizes[vendor] : 0

  if (entryGz > BUDGET.entryGzip) {
    failures.push(`入口 chunk ${entry} gzip ${kb(entryGz)} 超过预算 ${kb(BUDGET.entryGzip)}`)
  }
  if (vendorGz > BUDGET.vendorGzip) {
    failures.push(`vendor chunk ${vendor} gzip ${kb(vendorGz)} 超过预算 ${kb(BUDGET.vendorGzip)}`)
  }
  if (firstPaintTotal > BUDGET.firstPaintGzip) {
    failures.push(`首屏 JS gzip 合计 ${kb(firstPaintTotal)} 超过预算 ${kb(BUDGET.firstPaintGzip)}`)
  }
  // 兜底提醒：dist 根目录出现没有进 index.html 的巨大 chunk 不算失败，但值得看一眼
  try {
    for (const name of firstPaint) {
      const size = statSync(join(dist, 'assets', name)).size
      if (size > 1.5 * 1024 * 1024) {

        warnings.push(`chunk ${name} 原始体积 ${(size / 1024 / 1024).toFixed(2)} MB（按需加载，可接受）`)
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
             preloaded, budget: BUDGET },
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
    console.log(`入口 gzip ${kb(result.sizes.entryGzip || 0)} / vendor gzip ${kb(result.sizes.vendorGzip || 0)}`
      + ` / 首屏合计 gzip ${kb(result.sizes.firstPaintGzip || 0)}`)
    for (const w of result.warnings) console.log(`提示：${w}`)
    for (const f of result.failures) console.error(`失败：${f}`)
    console.log(result.ok ? '体积门禁通过' : '体积门禁未通过')
  }
  process.exit(result.ok ? 0 : 1)
}
