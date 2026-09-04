<script setup lang="ts">
/**
 * 流式回答正文渲染：
 * - 整段累积后交给 markdown-it 渲染（引用跨 delta 不会断）；
 * - 渲染完成后扫描纯文本节点，把 `[n]` 替换为引用按钮、把实体名包成高亮。
 */
import MarkdownIt from 'markdown-it'
import { onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

import type { Citation, EntityInfo } from '@/types/contract'

const md = new MarkdownIt({ breaks: true, html: false, linkify: true })

const props = defineProps({
  text: { type: String, default: '' },
  citations: { type: Array as PropType<Citation[]>, default: () => [] },
  entities: { type: Array as PropType<EntityInfo[]>, default: () => [] },
})

const emit = defineEmits<{ (e: 'citation', index: number): void }>()

function renderedHtml(): string {
  return props.text ? md.render(props.text) : ''
}

const rootEl = ref<HTMLElement | null>(null)

function decorate(el: HTMLElement | null): void {
  if (!el) return
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text)
  const names = [
    ...new Set(props.entities.map((e) => e.standard_name || e.name).filter(Boolean)),
  ].sort((a, b) => b.length - a.length)

  for (const node of textNodes) {
    const original = node.nodeValue || ''
    if (!original.trim()) continue
    const pieces: Array<{ type: 'text' | 'cite' | 'mention'; value: string }> = []
    let rest = original
    const pattern = /(\[\d+\])/g
    let m: RegExpExecArray | null
    let pos = 0
    while ((m = pattern.exec(rest)) !== null) {
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
        const idx = Number(piece.value.replace(/\D/g, ''))
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

function schedule(): void {
  // v-html 更新发生在组件更新阶段之后，故用双帧确保 DOM 已替换
  requestAnimationFrame(() => requestAnimationFrame(() => decorate(rootEl.value)))
}

onMounted(schedule)
watch(() => [props.text, props.entities], schedule)
onBeforeUnmount(() => {
  // 组件卸载前不保留任何定时任务
})
</script>

<template>
  <div ref="rootEl" class="markdown-body" v-html="renderedHtml()"></div>
</template>
