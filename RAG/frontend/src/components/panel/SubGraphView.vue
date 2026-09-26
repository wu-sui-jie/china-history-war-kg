<script setup lang="ts">
import type { ECharts } from 'echarts/core'
import { onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

import PanelEmpty from '@/components/panel/PanelEmpty.vue'
import { ENTITY_TYPE_COLORS, type SubGraph } from '@/types/contract'

const props = defineProps({
  graph: { type: Object as PropType<SubGraph>, default: () => ({ nodes: [], edges: [] }) },
})

const emit = defineEmits<{ (e: 'ask', question: string): void }>()
const el = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let ro: ResizeObserver | null = null
let echartsCore: typeof import('echarts/core') | null = null
let disposed = false

/** 懒加载 ECharts：整包按需引入，且只在真正要画图时才下载。 */
async function ensureEcharts(): Promise<typeof import('echarts/core')> {
  if (echartsCore) return echartsCore
  const [core, charts, components, renderers] = await Promise.all([
    import('echarts/core'),
    import('echarts/charts'),
    import('echarts/components'),
    import('echarts/renderers'),
  ])
  core.use([charts.GraphChart, components.TooltipComponent, renderers.CanvasRenderer])
  echartsCore = core
  return core
}

// 动态 chunk 加载失败（离线/发布后文件名变化）要有可见降级与重试，
// 不能变成未处理的 Promise rejection。
const loadError = ref('')
const loading = ref(false)

async function ensureEchartsSafe(): Promise<typeof import('echarts/core') | null> {
  loading.value = true
  try {
    const core = await ensureEcharts()
    loadError.value = ''
    return core
  } catch (err) {
    loadError.value = navigator.onLine === false
      ? '当前处于离线状态，图谱组件无法加载'
      : `图谱组件加载失败：${String(err)}`
    return null
  } finally {
    loading.value = false
  }
}

function cleanName(name: string): string {
  return name.replace(/(之战|之变|之役|之围|大战|起义|战争|会战)$/, '')
}

function onChartClick(params: unknown): void {
  // 只响应节点点击（echarts graph 的边点击也会派发 click，需按 dataType 过滤）
  const p = params as { dataType?: string; name?: string } | null
  if (p?.dataType === 'node' && p.name) askNode(p.name)
}

async function render(): Promise<void> {
  if (!el.value) return
  const echarts = await ensureEchartsSafe()
  if (!echarts || disposed || !el.value) return
  if (!chart || chart.isDisposed?.()) {
    chart = echarts.init(el.value)
    chart.on('click', onChartClick)
  }
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
  void render()
  ro = new ResizeObserver(() => chart?.resize())
  if (el.value) ro.observe(el.value)
})

watch(() => props.graph, () => void render(), { deep: true })
onBeforeUnmount(() => {
  disposed = true
  ro?.disconnect()
  ro = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div v-if="loadError" class="graph-wrap">
    <PanelEmpty :text="loadError" />
    <p class="graph-hint">
      <button type="button" class="ghost-btn" @click="() => void render()">重试图谱加载</button>
    </p>
  </div>
  <div v-else-if="loading && !props.graph.nodes.length" class="panel-loading" role="status">
    正在加载图谱组件…
  </div>
  <div v-else-if="props.graph.nodes.length" class="graph-wrap">
    <div ref="el" class="subgraph-canvas" aria-label="知识图谱子图"></div>
    <p class="graph-hint">图中节点可拖动缩放查看；点击图中节点或下方实体可继续提问。</p>
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
