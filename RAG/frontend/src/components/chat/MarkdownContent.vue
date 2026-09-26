<script setup lang="ts">
/**
 * 流式回答正文渲染：
 * - 整段累积后交给 markdown-it 渲染（引用跨 delta 不会断）；
 * - 渲染完成后扫描纯文本节点，把 `[n]` 替换为引用按钮、把实体名包成高亮。
 *
 * 两处要点：
 * - **节流**：流式期间每个 token 都重建 markdown 与 DOM 会把长回答拖成 O(n²)，
 *   这里合并到 ~80ms 一次，并在组件卸载时清掉挂起的定时器与帧回调；
 * - **引用交叉校验**：只有真实存在于 citations 里的编号才渲染成可点引用，
 *   否则正文里的 `[1]`（表格、编号、代码）会被误装饰成引用按钮。
 */
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onBeforeUnmount, ref, watch, type PropType } from 'vue'

import type { Citation, EntityInfo } from '@/types/contract'

const md = new MarkdownIt({ breaks: true, html: false, linkify: true })

const props = defineProps({
  text: { type: String, default: '' },
  citations: { type: Array as PropType<Citation[]>, default: () => [] },
  entities: { type: Array as PropType<EntityInfo[]>, default: () => [] },
})

const emit = defineEmits<{ (e: 'citation', index: number): void }>()

/** 渲染节流窗口：既保留"跟手"的观感，又把重渲染次数压到每秒十余次。 */
const RENDER_THROTTLE_MS = 80

const rootEl = ref<HTMLElement | null>(null)
const renderedText = ref(props.text)
const html = computed(() => (renderedText.value ? md.render(renderedText.value) : ''))

const citationIndexes = computed(() => {
  const set = new Set<number>()
  for (const c of props.citations) {
    if (typeof c.index === 'number') set.add(c.index)
  }
  return set
})

function decorate(el: HTMLElement | null): void {
  if (!el) return
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text)
  const names = [
    ...new Set(props.entities.map((e) => e.standard_name || e.name).filter(Boolean)),
  ].sort((a, b) => b.length - a.length)
  const knownCitations = citationIndexes.value

  for (const node of textNodes) {
    const original = node.nodeValue || ''
    if (!original.trim()) continue
    const pieces: Array<{ type: 'text' | 'cite' | 'mention'; value: string }> = []
    const rest = original
    const pattern = /\[(\d+)\]/g
    let m: RegExpExecArray | null
    let pos = 0
    while ((m = pattern.exec(rest)) !== null) {
      const idx = Number(m[1])
      // 只有 citations 中真实存在的编号才算引用；其余保持原文
      if (!knownCitations.has(idx)) continue
      const pre = rest.slice(pos, m.index)
      if (pre) pieces.push({ type: 'text', value: pre })
      pieces.push({ type: 'cite', value: m[1] })
      pos = m.index + m[0].length
    }
    if (pos < rest.length) pieces.push({ type: 'text', value: rest.slice(pos) })

    // 实体高亮只在普通文本段中切分（引用编号不参与）
    const final: typeof pieces = []
    for (const piece of pieces) {
      if (piece.type !== 'text') {
        final.push(piece)
        continue
      }
      let value = piece.value
      let hit = false
      for (const name of names) {
        if (!name || !value.includes(name)) continue
        hit = true
        const parts = value.split(name)
        value = parts.join(`\u0001${name}\u0001`)
      }
      if (!hit) {
        final.push(piece)
        continue
      }
      for (const token of value.split('\u0001')) {
        if (!token) continue
        final.push(names.includes(token) ? { type: 'mention', value: token } : { type: 'text', value: token })
      }
    }

    const frag = document.createDocumentFragment()
    for (const piece of final) {
      if (piece.type === 'cite') {
        const idx = Number(piece.value)
        const btn = document.createElement('button')
        btn.type = 'button'
        btn.className = 'answer-cite'
        btn.textContent = String(idx)
        btn.title = `查看引用 ${idx}`
        btn.addEventListener('click', () => emit('citation', idx))
        frag.appendChild(btn)
      } else if (piece.type === 'mention') {
        const em = document.createElement('em')
        em.className = 'mention'
        em.textContent = piece.value
        frag.appendChild(em)
      } else {
        frag.appendChild(document.createTextNode(piece.value))
      }
    }
    node.parentNode?.replaceChild(frag, node)
  }
}

let debounceTimer: number | undefined
let dirty = true
let flushSeq = 0

async function flush(): Promise<void> {
  if (dirty) {
    renderedText.value = props.text
    dirty = false
  }
  const seq = ++flushSeq
  // 等 Vue 把 v-html 写进 DOM 再装饰。
  // 不能用 requestAnimationFrame：后台标签页里 rAF 会被浏览器暂停，
  // 正文的引用按钮与实体高亮会一直不出现（实测）。nextTick 是微任务，不依赖页面可见性。
  await nextTick()
  if (seq !== flushSeq) return      // 期间又有新的一帧在排期，交给它去装饰
  decorate(rootEl.value)
}

function schedule(immediate = false): void {
  dirty = true
  if (immediate) {
    if (debounceTimer !== undefined) {
      window.clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    flush()
    return
  }
  if (debounceTimer !== undefined) return
  debounceTimer = window.setTimeout(() => {
    debounceTimer = undefined
    flush()
  }, RENDER_THROTTLE_MS)
}

watch(() => [props.text, props.entities], () => schedule())
watch(() => props.citations, () => schedule(true))

schedule(true)

onBeforeUnmount(() => {
  if (debounceTimer !== undefined) window.clearTimeout(debounceTimer)
  flushSeq += 1        // 让在途的 flush 在 nextTick 之后直接放弃
})
</script>

<template>
  <div ref="rootEl" class="markdown-body" v-html="html"></div>
</template>
