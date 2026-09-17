<script setup lang="ts">
import type { ECharts } from 'echarts/core'
import { onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

import type { MapPoint } from '@/types/contract'

const props = defineProps({
  points: { type: Array as PropType<MapPoint[]>, default: () => [] },
})

const el = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let ro: ResizeObserver | null = null
let registered = false
let echartsCore: typeof import('echarts/core') | null = null
let disposed = false

/** 懒加载 ECharts 与中国地图 JSON（P1-15）：
 * 地图源文件约 570 KB，只有真正打开"地点"页并画图时才需要下载。 */
async function ensureEcharts(): Promise<typeof import('echarts/core')> {
  if (echartsCore) return echartsCore
  const [core, charts, components, renderers, mapModule] = await Promise.all([
    import('echarts/core'),
    import('echarts/charts'),
    import('echarts/components'),
    import('echarts/renderers'),
    import('@/assets/china-map.json'),
  ])
  core.use([
    charts.ScatterChart,
    components.GeoComponent,
    components.TooltipComponent,
    renderers.CanvasRenderer,
  ])
  if (!registered) {
    core.registerMap('china', (mapModule.default ?? mapModule) as never)
    registered = true
  }
  echartsCore = core
  return core
}

function validPoints(): MapPoint[] {
  return (props.points || []).filter(
    (p) => typeof p.longitude === 'number' && typeof p.latitude === 'number',
  )
}

// 动态 chunk（echarts + 地图 JSON）加载失败要可见、可重试（第四轮复核 P2-8）
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
      ? '当前处于离线状态，地图组件无法加载'
      : `地图组件加载失败：${String(err)}`
    return null
  } finally {
    loading.value = false
  }
}

async function render(): Promise<void> {
  if (!el.value) return
  const echarts = await ensureEchartsSafe()
  if (!echarts || disposed || !el.value) return
  if (!chart || chart.isDisposed?.()) chart = echarts.init(el.value)

  const pts = validPoints()
  chart.setOption(
    {
      tooltip: {
        trigger: 'item',
        formatter: (item: unknown) => {
          const p = item as { name: string; value: [number, number, number]; data: { modern?: string } }
          const n = p.value?.[2] || 0
          const modern = p.data?.modern ? `（今 ${p.data.modern}）` : ''
          return `${p.name}${modern}<br/>相关事件 ${n} 个`
        },
      },
      geo: {
        map: 'china',
        roam: true,
        zoom: 1.15,
        top: 8,
        bottom: 8,
        label: { show: false },
        itemStyle: { areaColor: '#eef2f7', borderColor: '#cbd5e1' },
        emphasis: { itemStyle: { areaColor: '#e2e8f0' }, label: { show: false } },
      },
      series: [
        {
          type: 'scatter',
          coordinateSystem: 'geo',
          symbolSize: (val: number[]) => 9 + Math.min(7, (val?.[2] || 0) * 0.6),
          itemStyle: { color: '#dc2626', opacity: 0.85 },
          label: {
            show: true,
            position: 'right',
            formatter: '{b}',
            fontSize: 11,
            color: '#334155',
          },
          emphasis: { scale: 1.3 },
          data: pts.map((p) => ({
            name: p.name,
            value: [p.longitude, p.latitude, (p.events || []).length],
            modern: p.modern_name || '',
          })),
        },
      ],
    },
    true,
  )
}

onMounted(() => {
  void render()
  if (el.value) {
    ro = new ResizeObserver(() => chart?.resize())
    ro.observe(el.value)
  }
})

watch(() => props.points, () => void render(), { deep: true })

onBeforeUnmount(() => {
  disposed = true
  ro?.disconnect()
  ro = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="places-map-wrap">
    <div v-if="loadError" class="panel-loading" role="alert">
      <p>{{ loadError }}</p>
      <button type="button" class="ghost-btn" @click="() => void render()">重试地图加载</button>
    </div>
    <template v-else>
      <div ref="el" class="places-map" />
      <p v-if="loading" class="places-map-note" role="status">正在加载地图组件…</p>
      <p v-else class="places-map-note">
        历史地名按其现代位置标注（区县/市级为主，属近似点位）；可滚轮缩放、拖拽平移。
      </p>
    </template>
  </div>
</template>
