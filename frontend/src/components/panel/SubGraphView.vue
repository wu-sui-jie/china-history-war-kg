<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import { ENTITY_TYPE_COLORS, type SubGraph } from '@/types/contract'

const props = defineProps({
  graph: { type: Object as PropType<SubGraph>, default: () => ({ nodes: [], edges: [] }) },
})

const emit = defineEmits<{ (e: 'ask', question: string): void }>()
const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null

function cleanName(name: string): string {
  return name.replace(/(之战|之变|之役|之围|大战|起义|战争|会战)$/, '')
}

function render(): void {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const nodes = (props.graph.nodes || []).map((n) => ({
    id: n.id,
    name: n.name,
    category: n.type || '其他',
    symbolSize: n.type === '事件' ? 34 : 28,
    itemStyle: { color: ENTITY_TYPE_COLORS[n.type || ''] || '#64748b' },
  }))
  const links = (props.graph.edges || []).map((e) => ({
    source: e.source,
    target: e.target,
    relation: e.relation || '',
  }))
  chart.setOption({
    tooltip: { show: true },
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: nodes,
        links,
        roam: true,
        draggable: true,
        force: { repulsion: 340, edgeLength: 140, gravity: 0.12 },
        label: { show: true, position: 'right', fontSize: 11, color: '#334155' },
        emphasis: { focus: 'adjacency' },
        lineStyle: { color: '#94a3b8', width: 1.5, curveness: 0.12 },
        edgeLabel: {
          show: true,
          fontSize: 10,
          color: '#64748b',
          formatter: (p: unknown) => {
            const data = (p as { data?: { relation?: string } }).data
            return data?.relation || ''
          },
        },
      },
    ],
  })
}

function askNode(name: string): void {
  if (name) emit('ask', `介绍一下${cleanName(name)}`)
}

onMounted(() => {
  render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
})

watch(() => props.graph, render, { deep: true })
onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div v-if="props.graph.nodes.length" class="graph-wrap">
    <div ref="el" class="subgraph-canvas" aria-label="知识图谱子图"></div>
    <p class="graph-hint">图中节点可拖动缩放查看；点击下方实体可继续提问。</p>
    <div class="graph-followups">
      <button
        v-for="n in props.graph.nodes"
        :key="n.id"
        type="button"
        :title="`${n.type || '实体'} · ${n.dynasty || ''}`"
        @click="askNode(n.name)"
      >
        {{ n.name }}
      </button>
    </div>
  </div>
  <PanelEmpty v-else text="当前问题暂无图谱子图。" />
</template>
