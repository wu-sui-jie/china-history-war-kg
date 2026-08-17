<template>
  <lay-container class="graph-page">
    <div class="graph-header">
      <div>
        <h1>{{ focusName ? '实体关系验证图' : '中国历史战争事件总览图' }}</h1>
      </div>
      <lay-button v-if="focusName" size="sm" @click="loadFullGraph">返回完整图谱</lay-button>
    </div>

    <div class="graph-wrapper">
      <EChartsGraph :data="datasource" @node-expanded="loadNodeRelations" />
      <div v-if="loading" class="graph-loading-mask">加载图谱中...</div>
    </div>
  </lay-container>
</template>

<script setup lang="ts">
import { inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import EChartsGraph from './EChartsGraph.vue'
import Http from '@/api/http'

const loading = ref(false)
const datasource = ref<any>({ nodes: [], lines: [] })
const graphPageSummary = inject<any>('graphPageSummary', null)
const route = useRoute()
const focusName = ref('')

function syncPageSummary() {
  graphPageSummary?.setGraphPageSummary(
    datasource.value.nodes?.length || 0,
    datasource.value.lines?.length || 0,
  )
}

async function loadNodeRelations(nodeId: string) {
  loading.value = true
  try {
    const response = await Http.get(`/api/node/relations?id=${nodeId}`)
    if (response.code === 200) {
      const currentNodes = new Set(datasource.value.nodes.map((node: any) => String(node.id)))
      const currentLines = new Set(datasource.value.lines.map((line: any) => `${line.from || line.source}-${line.to || line.target}-${line.text || ''}`))
      const newNodes = (response.data.nodes || []).filter((node: any) => !currentNodes.has(String(node.id)))
      const newLines = (response.data.lines || []).filter((line: any) => {
        const lineKey = `${line.from || line.source}-${line.to || line.target}-${line.text || ''}`
        return !currentLines.has(lineKey)
      })
      datasource.value = {
        nodes: [...(datasource.value.nodes || []), ...newNodes],
        lines: [...(datasource.value.lines || []), ...newLines],
      }
      syncPageSummary()
    }
  } catch (error) {
    console.error('获取节点关系失败:', error)
  } finally {
    loading.value = false
  }
}

async function loadFullGraph() {
  focusName.value = ''
  sessionStorage.removeItem('graphFocus')
  await getGraph(false)
}

async function getGraph(allowFocus = true) {
  loading.value = true
  try {
    const focus = allowFocus ? getGraphFocus() : null
    const response = focus
      ? await Http.get('/api/graph/node_context', focus)
      : await Http.post('/search_name_kg', { load_all: true })
    datasource.value = response.code === 200 ? response.data || { nodes: [], lines: [] } : { nodes: [], lines: [] }
  } catch (error) {
    console.error('获取图谱数据失败:', error)
    datasource.value = { nodes: [], lines: [] }
  } finally {
    syncPageSummary()
    loading.value = false
  }
}

function getGraphFocus() {
  const hasQueryFocus = route.query.focus || route.query.name || route.query.graph_id
  if (hasQueryFocus) {
    focusName.value = String(route.query.name || '')
    return {
      graph_id: route.query.graph_id ? Number(route.query.graph_id) : undefined,
      name: String(route.query.name || ''),
      type: String(route.query.type || ''),
    }
  }

  const rawFocus = sessionStorage.getItem('graphFocus')
  if (!rawFocus) return null

  sessionStorage.removeItem('graphFocus')
  try {
    const focus = JSON.parse(rawFocus)
    focusName.value = focus.name || ''
    return {
      graph_id: focus.graph_id ? Number(focus.graph_id) : undefined,
      name: focus.name || '',
      type: focus.type || '',
    }
  } catch (error) {
    return null
  }
}

watch(
  () => route.fullPath,
  () => getGraph(),
)

onMounted(() => {
  getGraph()
})

onUnmounted(() => {
  graphPageSummary?.resetGraphPageSummary()
})
</script>

<style scoped>
.graph-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  width: 100%;
  max-width: none !important;
  padding: 12px;
  box-sizing: border-box;
  overflow: hidden;
  background: #eef4fb;
}

.graph-header {
  flex-shrink: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-bottom: 10px;
  padding: 14px 18px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(196, 155, 88, 0.18);
  border-radius: 16px;
}

.graph-header h1 {
  margin: 0;
  font-size: 26px;
  color: #243042;
  text-align: center;
}

.graph-header p {
  margin: 0;
  color: #6b7280;
}

.graph-wrapper {
  position: relative;
  flex: 1;
  min-height: 0;
  width: 100%;
  overflow: hidden;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(74, 54, 24, 0.08);
}

.graph-loading-mask {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  background: rgba(255, 255, 255, 0.62);
  color: #8c6d3b;
  font-weight: 600;
}
</style>
