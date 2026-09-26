#!/usr/bin/env node
/**
 * 静态资源路径检查（构建后的回归门禁）。
 *
 * 背景：vite 的 base 是 `/static/`，而 `public/` 下的资源**原样拷贝、不改名、也不会被
 * 构建器补上前缀**。于是写错路径的三种形态都不会让构建失败，只有上线后肉眼可见：
 *
 *   形态一（漏前缀）`src="/login.jpg"`：不带 /static/ 前缀，生产 nginx 按 `location /`
 *       转给旧后端 Flask，后端没有这条静态路由 → 登录主图 401/404。
 *   形态二（相对路径）CSS 里的 `url(background.jpg)`：相对 `dist/assets/*.css` 解析成
 *       `/static/assets/background.jpg`；更糟的是 vite 解析不到这个文件时会**把整条
 *       background 声明从产物里删掉**（实测 dist 的 .login-wrap 规则里
 *       完全没有 background-image），页面上背景直接消失。
 *   形态三（同一形态一）`src="/icon/logout.svg"`：退出按钮图标 404。
 *
 * 因此分两段检查，缺一不可：
 *   A. **源码规则**：`src/` 下所有绝对根路径资源引用必须带 base，相对裸文件名必须没有
 *      ——错误就是在这里写进去的，这一段能在构建前就拦住。
 *   B. **产物规则**：dist 里 `/static/...` 的每条引用都要真有其文件，且解析必须命中引用
 *      （一条都没命中说明检查已与产物脱节，此时"零失败"是假通过）。
 *
 * 用法：node scripts/check-dist-assets.mjs [--dist dist] [--src src]
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

/** vite base，须与 vite.config.ts 的 `base` 一致。 */
const BASE = '/static/'

/** 允许出现在产物里、但不由 dist 承载的资源路径（各有各的兜底）。 */
const ALLOW = new Set(['/favicon.ico', '/'])

/** 资源类扩展名：只有这类"裸文件名"才按错误处理（`url(x.png)`）。 */
const ASSET_EXT = /\.(png|jpe?g|gif|svg|webp|avif|ico|bmp|woff2?|ttf|eot|mp4|webm)$/i

const SOURCE_SUFFIX = /\.(vue|ts|tsx|js|mjs|css|less|scss)$/i

/** 去掉注释再扫：注释里出现的示例路径不该被判成缺陷（也不能靠"别在注释里写"来维持门禁）。 */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

function walk(dir, out = []) {
  if (!existsSync(dir)) return out
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (SOURCE_SUFFIX.test(entry.name)) out.push(full)
  }
  return out
}

/** 从一段源码里抽出所有资源引用（含出现在哪个文件哪一行，便于直接定位）。 */
function collectSourceRefs(text) {
  const clean = stripComments(text)
  const refs = []
  for (const m of clean.matchAll(/(?:src|href)\s*=\s*["']([^"']+)["']/g)) refs.push(m[1])
  for (const m of clean.matchAll(/url\(\s*["']?([^"')]*)["']?\s*\)/g)) refs.push(m[1])
  return refs
}

/**
 * A. 源码规则：返回违规清单。
 *
 * 判据刻意收得很窄，避免误伤合法写法（`url(../../assets/x.png)`、`url(@/assets/x.png)`、
 * 完整 URL、`data:`）：
 *   - 绝对根路径 `/x` 且不带 BASE、不在 ALLOW → 一定会落到旧后端；
 *   - **裸文件名**（不含 `/`、不以 `.`/`~`/`@` 开头）+ 资源扩展名 → vite 会相对当前文件解析
 *     而找不到，产物里这条声明会被删掉。
 */
export function checkSource(srcDir = 'src') {
  const src = resolve(srcDir)
  const failures = []
  let refCount = 0

  for (const file of walk(src)) {
    const rel = relative(src, file).replace(/\\/g, '/')
    const text = readFileSync(file, 'utf8')
    for (const ref of collectSourceRefs(text)) {
      const clean = ref.split('?')[0].split('#')[0].trim()
      if (!clean) continue
      if (/^[a-z][a-z0-9+.-]*:/i.test(clean) || clean.startsWith('//')) continue  // 外部/data:
      if (clean.startsWith('${') || clean.includes('${')) continue                 // 模板拼接
      refCount += 1

      if (clean.startsWith('/')) {
        if (clean.startsWith(BASE) || ALLOW.has(clean)) continue
        failures.push(
          `${rel}: 引用了 ${clean}——不以 ${BASE} 开头，生产会被 nginx 转给旧后端；` +
          `静态资源要么放进 src/ 用 import 引入，要么带上 import.meta.env.BASE_URL`)
        continue
      }
      // 相对引用：只有在"看起来像 public 资源"时才判错
      const bare = !clean.includes('/') && !/^[.~@]/.test(clean)
      if (bare && ASSET_EXT.test(clean)) {
        failures.push(
          `${rel}: 引用了裸文件名 ${clean}——相对本文件解析，vite 找不到会把这条声明从产物里删掉；` +
          `public 资源请用 import.meta.env.BASE_URL 前缀`)
      }
    }
  }

  if (refCount === 0) {
    failures.push(`在 ${src} 下没找到任何资源引用：源码规则已失效，请先确认解析规则再放行`)
  }
  return { ok: failures.length === 0, failures, refCount }
}

/** B. 产物规则：dist 里的每条 /static/ 引用都要真有其文件。 */
export function checkDist(distDir = 'dist') {
  const dist = resolve(distDir)
  const failures = []
  const checked = []

  const htmlPath = join(dist, 'index.html')
  if (!existsSync(htmlPath)) {
    return { ok: false, failures: [`找不到构建产物：${htmlPath}（先 npm run build）`], checked }
  }

  const targets = [htmlPath]
  const assetsDir = join(dist, 'assets')
  if (existsSync(assetsDir)) {
    for (const name of readdirSync(assetsDir)) {
      if (name.endsWith('.css')) targets.push(join(assetsDir, name))
    }
  }

  let refCount = 0
  for (const file of targets) {
    const rel = relative(dist, file).replace(/\\/g, '/')
    for (const ref of collectSourceRefs(readFileSync(file, 'utf8'))) {
      const clean = ref.split('?')[0].split('#')[0]
      if (!clean.startsWith('/') || clean.startsWith('//')) continue
      refCount += 1
      if (ALLOW.has(clean)) continue
      if (!clean.startsWith(BASE)) {
        failures.push(`${rel} 引用了 ${clean}：不以 ${BASE} 开头，生产会被转给旧后端`)
        continue
      }
      const onDisk = clean.slice(BASE.length)
      if (!existsSync(join(dist, onDisk))) {
        failures.push(`${rel} 引用了 ${clean}，但产物里没有 ${onDisk}`)
        continue
      }
      checked.push(clean)
    }
  }

  if (refCount === 0) {
    failures.push(`在 ${dist} 的 index.html 与 assets/*.css 里没找到任何站内资源引用：` +
      '产物规则已失效，请先确认解析规则再放行')
  }
  return { ok: failures.length === 0, failures, checked }
}

const invokedDirectly = process.argv[1] && import.meta.url.endsWith(
  process.argv[1].replace(/\\/g, '/').split('/').pop())
if (invokedDirectly) {
  const args = process.argv.slice(2)
  const argOf = (flag, fallback) => {
    const i = args.indexOf(flag)
    return i >= 0 ? args[i + 1] : fallback
  }
  const only = argOf('--only', '')

  const results = []
  if (only !== 'dist') results.push(['源码', checkSource(argOf('--src', 'src'))])
  if (only !== 'src') results.push(['产物', checkDist(argOf('--dist', 'dist'))])

  let ok = true
  for (const [label, result] of results) {
    for (const f of result.failures) console.error(`失败（${label}）：${f}`)
    if (!result.ok) ok = false
    else console.log(`${label}资源路径检查通过`)
  }
  process.exit(ok ? 0 : 1)
}
