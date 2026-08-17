<template>
  <lay-container fluid="true" class="graph-subpage">
    <lay-card class="graph-filter-card">
      <lay-form style="margin-top: 10px">
        <lay-row>
          <lay-col :md="8">
            <lay-form-item label="名称" label-width="80">
              <lay-input
                v-model="searchQuery.name"
                placeholder="请输入战争或地点名称"
                size="sm"
                :allow-clear="true"
                style="width: 98%"
              />
            </lay-form-item>
          </lay-col>
          <lay-col :md="6">
            <lay-form-item label="关系筛选" label-width="80">
              <lay-select
                v-model="searchQuery.rel_type"
                placeholder="请选择"
                size="sm"
                :allow-clear="true"
                style="width: 98%"
              >
                <lay-select-option
                  v-for="type in relTypes"
                  :key="type"
                  :value="type"
                  :label="type"
                />
              </lay-select>
            </lay-form-item>
          </lay-col>
          <lay-col :md="4">
            <lay-form-item label-width="20">
              <lay-button style="margin-left: 20px" type="primary" size="sm" @click="getGraph">
                查询
              </lay-button>
              <lay-button size="sm" @click="toReset">重置</lay-button>
            </lay-form-item>
          </lay-col>
        </lay-row>
      </lay-form>
    </lay-card>
    <div class="graph-canvas">
      <EChartsGraph :data="datasource" @node-expanded="loadNodeRelations" />
      <div v-if="loading || expanding" class="graph-loading-mask">加载图谱中...</div>
    </div>
  </lay-container>
</template>

<script setup lang="ts">
import { inject, onMounted, onUnmounted, ref } from 'vue'
import EChartsGraph from '../EChartsGraph.vue'
import Http from '@/api/http'

const datasource = ref<any>({ nodes: [], lines: [] })
const loading = ref(false)
const expanding = ref(false)
const graphPageSummary = inject<any>('graphPageSummary', null)

const relTypes = ['主战场', '次要战场', '出发地', '目的地', '途经地', '驻防地', '指挥所', '补给地', '战略要地', '议和地点']

const searchQuery = ref({
  name: '',
  rel_type: ''
})

function syncPageSummary() {
  graphPageSummary?.setGraphPageSummary(
    datasource.value.nodes?.length || 0,
    datasource.value.lines?.length || 0
  )
}

function toReset() {
  searchQuery.value.name = ''
  searchQuery.value.rel_type = ''
  getGraph()
}

async function loadNodeRelations(nodeId: string) {
  expanding.value = true
  try {
    const response = await Http.get(`/api/node/relations?id=${nodeId}`)
    if (response.code === 200) {
      const currentNodes = new Set(datasource.value.nodes.map((n: any) => n.id))
      const currentLines = new Set(datasource.value.lines.map((l: any) => `${l.from}-${l.to}-${l.text}`))

      const newNodes = response.data.nodes.filter((node: any) => !currentNodes.has(node.id))
      const newLines = response.data.lines.filter((line: any) => {
        const lineKey = `${line.from}-${line.to}-${line.text}`
        return !currentLines.has(lineKey)
      })

      datasource.value = {
        nodes: [...datasource.value.nodes, ...newNodes],
        lines: [...datasource.value.lines, ...newLines]
      }
      syncPageSummary()
    }
  } catch (error) {
    console.error('获取节点关系失败:', error)
  } finally {
    expanding.value = false
  }
}

async function getGraph() {
  loading.value = true
  expanding.value = false
  try {
    const params = new URLSearchParams({
      name: searchQuery.value.name?.trim() || '',
      rel_type: searchQuery.value.rel_type || ''
    })
    const response = await Http.get(`/api/graph/event_place?${params.toString()}`)
    if (response.code === 200) {
      datasource.value = response.data
    } else {
      datasource.value = { nodes: [], lines: [] }
    }
  } catch (error) {
    console.error('请求图谱数据失败:', error)
    datasource.value = { nodes: [], lines: [] }
  } finally {
    syncPageSummary()
    loading.value = false
  }
}

onMounted(() => {
  getGraph()
})

onUnmounted(() => {
  graphPageSummary?.resetGraphPageSummary()
})
</script>

<style scoped>
.graph-subpage {
  height: 100%;
  width: 100%;
  max-width: none !important;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 10px 12px 12px;
  box-sizing: border-box;
  overflow: hidden;
}

.graph-filter-card {
  flex-shrink: 0;
  margin-bottom: 8px;
}

.graph-canvas {
  flex: 1;
  min-height: 520px;
  width: 100%;
  overflow: hidden;
  border-radius: 12px;
  position: relative;
  background: #fff;
}

:deep(.echarts-graph) {
  width: 100%;
  height: 100% !important;
  position: relative;
}

:deep(.chart-container) {
  width: 100%;
  height: 100% !important;
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
