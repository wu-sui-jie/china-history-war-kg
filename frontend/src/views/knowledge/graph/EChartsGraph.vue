<template>
  <div class="graph-shell">
    <div ref="chartRef" class="graph-canvas"></div>

    <div v-if="!visibleNodes.length" class="empty-state">
      当前筛选条件下暂无可展示节点，请调整筛选或返回完整图谱。
    </div>

    <div class="graph-legend" aria-label="实体类型图例">
      <div v-for="item in typeOptions" :key="item.value" class="legend-item">
        <span class="legend-dot" :style="{ background: item.color }"></span>
        <span>{{ item.label }}</span>
      </div>
    </div>

    <div
      v-if="menu.visible"
      class="node-menu"
      :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
    >
      <div class="node-menu-title">{{ menu.node?.name }}</div>
      <div class="node-menu-type">{{ typeLabel(menu.node?.backendType) }}</div>
      <button @click="goDetail">查看详情</button>
      <button v-if="['Event', 'Place'].includes(menu.node?.backendType)" @click="goMapRoute">查看行军路线</button>
      <button @click="focusNode">聚焦关系</button>
    </div>

    <div class="graph-actions">
      <div v-if="typePanelVisible" class="type-panel">
        <button
          v-for="item in typeOptions"
          :key="item.value"
          :class="{ active: activeTypes.includes(item.value) }"
          @click="toggleType(item.value)"
        >
          <span :style="{ background: item.color }"></span>
          {{ item.label }}
        </button>
      </div>
      <button
        class="action-button"
        :class="{ active: typePanelVisible }"
        :title="typePanelVisible ? '收起实体选择' : '选择展示实体'"
        @click="typePanelVisible = !typePanelVisible"
      >
        <lay-icon type="layui-icon-slider" />
        <span>{{ typePanelVisible ? '收起' : '筛选' }}</span>
      </button>
      <button
        class="action-button"
        :class="{ active: !showRelations }"
        :title="showRelations ? '隐藏实体关系' : '显示实体关系'"
        @click="toggleRelations"
      >
        <lay-icon :type="showRelations ? 'layui-icon-eye-invisible' : 'layui-icon-eye'" />
        <span>{{ showRelations ? '关系' : '显示' }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { nodeDisplayName, normalizeType, typeLabel } from '@/utils/knowledge'

const props = defineProps<{
  data: {
    nodes?: Array<Record<string, any>>
    lines?: Array<Record<string, any>>
  }
}>()

const emit = defineEmits<{
  (event: 'node-expanded', nodeId: string): void
}>()

const router = useRouter()
const route = useRoute()
const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const typeOptions = [
  { value: 'Event', label: '事件', color: '#8b1e23' },
  { value: 'Person', label: '人物', color: '#315cc9' },
  { value: 'Place', label: '地点', color: '#c49b58' },
  { value: 'Organization', label: '组织', color: '#6f4ba0' },
]

const activeTypes = ref(typeOptions.map((item) => item.value))
const focusedNodeId = ref<string | null>(null)
const typePanelVisible = ref(false)
const showRelations = ref(true)

const menu = reactive<{
  visible: boolean
  x: number
  y: number
  node: any
}>({
  visible: false,
  x: 0,
  y: 0,
  node: null,
})

const categories = typeOptions.map((item) => ({
  name: item.value,
  itemStyle: { color: item.color },
}))

const normalizedNodes = computed(() => {
  const seen = new Set<string>()
  return (props.data?.nodes || [])
    .map((node) => {
      const id = String(node.id ?? node.neo4j_id ?? node.name ?? node.label ?? '')
      if (!id || seen.has(id)) return null
      seen.add(id)
      const backendType = normalizeType(node.type || node.category || node.label_type)
      const typeConfig = typeOptions.find((item) => item.value === backendType) || typeOptions[0]
      return {
        ...node,
        id,
        name: String(nodeDisplayName(node)),
        backendType,
        category: backendType,
        symbolSize: backendType === 'Event' ? 66 : 54,
        itemStyle: {
          color: typeConfig.color,
          borderColor: '#ffffff',
          borderWidth: 2,
          shadowBlur: 14,
          shadowColor: 'rgba(15, 23, 42, 0.24)',
        },
        label: {
          show: true,
          formatter: '{b}',
          color: '#243042',
          fontWeight: 600,
          backgroundColor: 'rgba(255,255,255,0.86)',
          borderRadius: 4,
          padding: [2, 5],
        },
      }
    })
    .filter(Boolean) as any[]
})

const normalizedLinks = computed(() => {
  const nodeIds = new Set(normalizedNodes.value.map((node) => node.id))
  return (props.data?.lines || [])
    .map((line) => {
      const source = String(line.source ?? line.from ?? line.start ?? '')
      const target = String(line.target ?? line.to ?? line.end ?? '')
      if (!nodeIds.has(source) || !nodeIds.has(target)) return null
      return {
        ...line,
        source,
        target,
        value: line.text || line.relation_type || line.type || line.name || '',
        label: {
          show: Boolean(line.text || line.relation_type || line.type || ''),
          formatter: String(line.text || line.relation_type || line.type || ''),
          color: '#5f6f82',
          fontSize: 12,
          fontWeight: 600,
          backgroundColor: 'rgba(255, 255, 255, 0.82)',
          borderColor: 'rgba(255, 255, 255, 0.92)',
          borderWidth: 1,
          borderRadius: 4,
          padding: [2, 4],
        },
        lineStyle: {
          color: 'rgba(92, 105, 122, 0.62)',
          width: 1.8,
          opacity: 0.88,
          curveness: 0.08,
        },
      }
    })
    .filter(Boolean) as any[]
})

const focusedNodeSet = computed(() => {
  if (!focusedNodeId.value) return null
  const ids = new Set<string>([focusedNodeId.value])
  normalizedLinks.value.forEach((link) => {
    if (link.source === focusedNodeId.value) ids.add(link.target)
    if (link.target === focusedNodeId.value) ids.add(link.source)
  })
  return ids
})

const visibleNodes = computed(() => {
  const allowed = new Set(activeTypes.value)
  return normalizedNodes.value.filter((node) => {
    if (!allowed.has(node.backendType)) return false
    return !focusedNodeSet.value || focusedNodeSet.value.has(node.id)
  })
})

const visibleLinks = computed(() => {
  const ids = new Set(visibleNodes.value.map((node) => node.id))
  return normalizedLinks.value.filter((link) => ids.has(link.source) && ids.has(link.target))
})

function toggleType(type: string) {
  if (activeTypes.value.includes(type)) {
    activeTypes.value = activeTypes.value.filter((item) => item !== type)
  } else {
    activeTypes.value = [...activeTypes.value, type]
  }
}

function toggleRelations() {
  showRelations.value = !showRelations.value
}

function setOption() {
  if (!chart) return
  menu.visible = false
  chart.setOption(
    {
      animationDuration: 700,
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          if (params.dataType === 'edge') return params.data.value || '关系'
          return `${params.data.name}<br/>${typeLabel(params.data.backendType)}`
        },
      },
      legend: { show: false },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories,
          data: visibleNodes.value,
          links: showRelations.value ? visibleLinks.value : [],
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 10,
          force: {
            repulsion: 520,
            gravity: 0.08,
            edgeLength: [120, 230],
            friction: 0.34,
          },
          edgeLabel: {
            show: showRelations.value,
            formatter: (params: any) => String(params.data?.value || ''),
            color: '#5f6f82',
            fontSize: 12,
            fontWeight: 600,
            backgroundColor: 'rgba(255, 255, 255, 0.82)',
            borderColor: 'rgba(255, 255, 255, 0.92)',
            borderWidth: 1,
            borderRadius: 4,
            padding: [2, 4],
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 3.2, opacity: 0.95 },
          },
        },
      ],
    },
    true,
  )
  nextTick(() => chart?.resize())
}

function handleNodeClick(params: any) {
  if (params.dataType !== 'node') return
  const event = params.event?.event
  menu.visible = true
  menu.node = params.data
  menu.x = Math.max(12, Math.min(event?.offsetX || 20, (chartRef.value?.clientWidth || 260) - 190))
  menu.y = Math.max(58, Math.min(event?.offsetY || 80, (chartRef.value?.clientHeight || 260) - 190))
}

function goDetail() {
  if (!menu.node) return
  const back = encodeURIComponent(route.fullPath)
  router.push(`/knowledge/entity-detail?type=${menu.node.backendType}&id=${menu.node.id}&back=${back}`)
}

function goMapRoute() {
  if (!menu.node) return
  const targetName = encodeURIComponent(menu.node.name || '')
  const queryKey = menu.node.backendType === 'Place' ? 'place' : 'event'
  router.push(`/knowledge/map?${queryKey}=${targetName}`)
}

function focusNode() {
  if (!menu.node) return
  focusedNodeId.value = focusedNodeId.value === menu.node.id ? null : menu.node.id
  emit('node-expanded', menu.node.id)
  menu.visible = false
}

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)
  chart.on('click', handleNodeClick)
  chart.getZr().on('click', (event: any) => {
    if (!event.target) menu.visible = false
  })
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartRef.value)
  setOption()
}

watch([visibleNodes, visibleLinks, showRelations], setOption, { deep: true })

onMounted(() => nextTick(initChart))

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.graph-shell {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f7f2ea;
  border-radius: 12px;
}

.type-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  width: 190px;
  padding: 10px;
  border: 1px solid rgba(196, 155, 88, 0.24);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(8px);
}

.type-panel button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  justify-content: center;
  border: 1px solid #ead9bf;
  border-radius: 8px;
  padding: 7px 8px;
  background: #fff;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
}

.type-panel button.active {
  border-color: #c49b58;
  color: #111827;
  box-shadow: 0 6px 14px rgba(196, 155, 88, 0.16);
}

.type-panel span {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.graph-canvas {
  flex: 1;
  min-height: 640px;
  height: 100%;
}

.node-menu {
  position: absolute;
  z-index: 30;
  width: 176px;
  padding: 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.18);
  border: 1px solid rgba(196, 155, 88, 0.24);
}

.node-menu-title {
  color: #111827;
  font-weight: 700;
  margin-bottom: 4px;
}

.node-menu-type {
  color: #8c6d3b;
  font-size: 12px;
  margin-bottom: 10px;
}

.node-menu button {
  display: block;
  width: 100%;
  border: 0;
  border-radius: 10px;
  padding: 9px 10px;
  margin-top: 8px;
  background: #f8fafc;
  color: #243042;
  text-align: left;
  cursor: pointer;
}

.node-menu button:hover {
  background: #fff3df;
}

.empty-state {
  position: absolute;
  inset: 56px 0 0;
  display: grid;
  place-items: center;
  color: #94a3b8;
  pointer-events: none;
}

.graph-legend {
  position: absolute;
  left: 18px;
  bottom: 18px;
  z-index: 24;
  display: grid;
  grid-template-columns: repeat(4, auto);
  gap: 8px;
  padding: 9px 12px;
  border: 1px solid rgba(196, 155, 88, 0.24);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(8px);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #3e2b18;
  font-size: 13px;
  white-space: nowrap;
}

.legend-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.86);
}

.graph-actions {
  position: absolute;
  right: 18px;
  bottom: 18px;
  z-index: 25;
  display: grid;
  grid-template-columns: 44px 44px;
  justify-items: end;
  gap: 10px;
}

.type-panel {
  grid-column: 1 / -1;
  justify-self: end;
}

.action-button {
  width: 44px;
  height: 44px;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  border: 1px solid rgba(196, 155, 88, 0.36);
  border-radius: 50%;
  padding: 0;
  background: rgba(255, 255, 255, 0.96);
  color: #3e2b18;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.16);
  cursor: pointer;
  backdrop-filter: blur(8px);
}

.action-button .layui-icon {
  font-size: 16px;
  line-height: 1;
}

.action-button span {
  font-size: 11px;
  line-height: 1;
}

.action-button:hover,
.action-button.active {
  color: #8b1e23;
  background: #fff8ef;
  border-color: #c49b58;
  transform: translateY(-1px);
}

@media (max-width: 768px) {
  .graph-canvas {
    min-height: 520px;
  }

  .graph-actions {
    right: 12px;
    bottom: 12px;
  }

  .graph-legend {
    left: 12px;
    bottom: 12px;
    grid-template-columns: repeat(2, auto);
  }
}
</style>
