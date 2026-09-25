/** 首屏体积门禁的用例（第 12 轮审查 P2-3）。
 *
 * 修的是什么：`scripts/check-bundle.mjs` 原先把资源前缀写死成 `/assets/`，而
 * `npm run build:integration` 的产物引用 `/rag/assets/...`——正则一条都匹配不上，
 * 于是打出"入口 0.0 kB / vendor 0.0 kB / 首屏合计 0.0 kB，体积门禁通过"。
 * 一个永远为真的门禁比没有门禁更糟：它会让真实回归也显示为通过。
 *
 * 这里对**两种构建模式的合成产物**分别断言，并专门钉住"解析不到入口时不许按 0 通过"。
 *
 * 用子进程跑脚本本身（`node scripts/check-bundle.mjs --json`），不 import 它的内部函数：
 *   1. CI 执行的就是这条命令，测命令行契约比测内部函数更贴近真实门禁；
 *   2. 该文件带 `#!/usr/bin/env node`，import 进 vitest 的内联转换会让 shebang 落在
 *      模块中间，直接 SyntaxError——想 import 就得为了测试删掉 shebang，得不偿失。
 * 合成产物而不是真跑两次构建：构建耗时且与解析逻辑无关，被测对象是解析与判定。
 */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'vitest'

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts',
                    'check-bundle.mjs')

/** 跑一次门禁，返回 { ok, result }；ok 取退出码（CI 就是看这个）。 */
function runGate(dist) {
  try {
    const out = execFileSync(process.execPath, [SCRIPT, '--dist', dist, '--json'],
                             { encoding: 'utf8' })
    return { ok: true, result: JSON.parse(out) }
  } catch (error) {
    // 非零退出：stdout 里仍是完整 JSON（脚本先打印结果再 exit）
    const out = (error.stdout || '').trim()
    return { ok: false, result: out ? JSON.parse(out) : { failures: [] } }
  }
}

/** 造一个 dist：assetsPrefix 为 '/assets/'（独立）或 '/rag/assets/'（并入）。 */
function makeDist({ assetsPrefix = '/assets/', files = {}, preload = [], includeEntry = true,
                    includeVendor = true, declared = null } = {}) {
  const dist = mkdtempSync(join(tmpdir(), 'rag-bundle-'))
  mkdirSync(join(dist, 'assets'), { recursive: true })

  // 默认文件先铺，再让 files 覆盖——顺序反了的话"替换 vendor 体积"这类用例会被默认值盖掉
  const all = {}
  if (includeEntry) all['index-abc123.js'] = 'x'.repeat(2048)
  if (includeVendor) all['vendor-def456.js'] = 'y'.repeat(4096)
  Object.assign(all, files)
  for (const [name, content] of Object.entries(all)) {
    writeFileSync(join(dist, 'assets', name), content)
  }

  const refs = declared ?? Object.keys(all).map((name) => `${assetsPrefix}${name}`)
  const scripts = refs.filter((url) => !preload.includes(url))
  const html = [
    '<!doctype html><html><head>',
    ...preload.map((url) => `<link rel="modulepreload" href="${url}">`),
    '</head><body><div id="app"></div>',
    ...scripts.map((url) => `<script type="module" src="${url}"></script>`),
    '</body></html>',
  ].join('\n')
  writeFileSync(join(dist, 'index.html'), html, 'utf8')
  if (assetsPrefix !== '/assets/') {
    writeFileSync(join(dist, 'build-mode.txt'), 'integration\n', 'utf8')
  }
  return dist
}

test('并入模式（/rag/assets/）能解析出真实体积而不是 0', () => {
  const { ok, result } = runGate(makeDist({ assetsPrefix: '/rag/assets/' }))

  assert.equal(result.sizes.assetsPrefix, '/rag/assets/')
  assert.equal(result.sizes.buildMode, 'integration')
  assert.ok(result.sizes.entryGzip > 0, `入口 gzip 不该是 0：${JSON.stringify(result.sizes)}`)
  assert.ok(result.sizes.vendorGzip > 0, `vendor gzip 不该是 0：${JSON.stringify(result.sizes)}`)
  assert.ok(result.sizes.firstPaintGzip > 0)
  assert.deepEqual(result.failures, [])
  assert.equal(ok, true)
})

test('独立模式（/assets/）与并入模式同样有效', () => {
  const { ok, result } = runGate(makeDist({ assetsPrefix: '/assets/' }))

  assert.equal(result.sizes.assetsPrefix, '/assets/')
  assert.ok(result.sizes.entryGzip > 0)
  assert.ok(result.sizes.vendorGzip > 0)
  assert.equal(ok, true)
})

test('引用不存在于产物的路径时失败（前缀对得上、文件不在）', () => {
  const dist = makeDist({ assetsPrefix: '/rag/assets/',
                          declared: ['/rag/assets/index-ghost.js', '/rag/assets/vendor-ghost.js'] })
  const { ok, result } = runGate(dist)

  assert.equal(ok, false)
  assert.ok(result.failures.some((f) => f.includes('产物里没有')), result.failures.join('；'))
})

test('index.html 里一条脚本引用都没有时失败（门禁与产物脱节的另一形态）', () => {
  const dist = mkdtempSync(join(tmpdir(), 'rag-bundle-empty-'))
  mkdirSync(join(dist, 'assets'), { recursive: true })
  writeFileSync(join(dist, 'index.html'), '<!doctype html><html><body></body></html>', 'utf8')

  const { ok, result } = runGate(dist)

  assert.equal(ok, false)
  assert.ok(result.failures.some((f) => f.includes('没找到入口 chunk')), result.failures.join('；'))
})

test('缺少 vendor chunk 时失败（拆分规则被改掉不能静默按 0 计）', () => {
  const { ok, result } = runGate(makeDist({ includeVendor: false }))

  assert.equal(ok, false)
  assert.ok(result.failures.some((f) => f.includes('没找到 vendor chunk')), result.failures.join('；'))
})

test('index.html 预加载按需 chunk（echarts）时失败', () => {
  const { ok, result } = runGate(makeDist({
    files: { 'echarts-hash.js': 'z'.repeat(1024) },
    preload: ['/assets/echarts-hash.js'],
  }))

  assert.equal(ok, false)
  assert.ok(result.failures.some((f) => f.includes('echarts')), result.failures.join('；'))
})

test('超过 vendor 预算时失败（门禁真的在卡体积，不是只做解析）', () => {
  // 用随机字节而不是重复字符：`'a'.repeat(400*1024)` 的 gzip 只有几百字节，
  // 门禁（按 gzip 计）不会超预算，用例会假通过。随机数据不可压缩，才真的顶穿预算。
  const huge = randomBytes(400 * 1024)
  const { ok, result } = runGate(makeDist({ files: { 'vendor-def456.js': huge } }))

  assert.equal(ok, false)
  assert.ok(result.sizes.vendorGzip > result.sizes.budget.vendorGzip,
            `vendor gzip ${result.sizes.vendorGzip} 应超过预算 ${result.sizes.budget.vendorGzip}`)
  assert.ok(result.failures.some((f) => f.includes('vendor chunk')), result.failures.join('；'))
})

test('找不到 index.html 时给出明确失败', () => {
  const { ok, result } = runGate(join(tmpdir(), 'rag-bundle-not-exist'))

  assert.equal(ok, false)
  assert.ok(result.failures.some((f) => f.includes('找不到构建产物')))
})
