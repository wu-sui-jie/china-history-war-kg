<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

import chinaMap from '@/assets/china-map.json'
import type { MapPoint } from '@/types/contract'

const props = defineProps({
  points: { type: Array as PropType<MapPoint[]>, default: () => [] },
})

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null
let registered = false

function validPoints(): MapPoint[] {
  return (props.points || []).filter(
    (p) => typeof p.longitude === 'number' && typeof p.latitude === 'number',
  )
}

function render(): void {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  if (!registered) {
    echarts.registerMap('china', chinaMap as unknown as Parameters<typeof echarts.registerMap>[1])
    registered = true
  }

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
  render()
  if (el.value) {
    ro = new ResizeObserver(() => chart?.resize())
    ro.observe(el.value)
  }
})

watch(() => props.points, render, { deep: true })

onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="places-map-wrap">
    <div ref="el" class="places-map" />
    <p class="places-map-note">
      历史地名按其现代位置标注（区县/市级为主，属近似点位）；可滚轮缩放、拖拽平移。
    </p>
  </div>
</template>
