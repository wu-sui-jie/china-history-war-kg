<template>
  <lay-container class="graph-page">
    <div class="graph-header">
      <div>
        <h1>{{ focusName ? '实体关系验证图' : '中国历史战争事件总览图' }}</h1>
        <p class="graph-subtitle">
          {{ focusName
            ? '以该实体为中心的一阶子图。'
            : '总览按战争事件 / 战争地点 / 历史人物 / 参战势力四类均衡取样，只取已有关联的节点。' }}
        </p>
      </div>
      <lay-button v-if="focusName" size="sm" @click="loadOverview">返回总览</lay-button>
    </div>

    <p v-if="limitHint" class="graph-limit-hint">{{ limitHint }}</p>

    <div class="graph-wrapper">
      <EChartsGraph :data="datasource" @node-expanded="loadNodeRelations" />
      <div v-if="loading" class="graph-loading-mask">加载图谱中...</div>
    </div>
  </lay-container>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import EChartsGraph from './EChartsGraph.vue'
import { getGraphNodeContext, searchNameKg } from '@/api/module/graph'
import { getNodeRelations } from '@/api/module/node'
import { mergeNodeRelations } from '@/utils/graph'

const loading = ref(false)
const datasource = ref<any>({ nodes: [], lines: [] })
const graphPageSummary = inject<any>('graphPageSummary', null)
const route = useRoute()
const focusName = ref('')

// 后端总览是"四类均衡取样"，取满配额就说明库里还有更多（本库 9184 个节点，总览只画 100）。
// 不说明的话用户会以为"这张图就这么大"——这句提示与四个子页同一措辞。
// 这里**没有**"加载全部节点"按钮：全量 9184 个节点力导向布局渲染不动
// （四个子页实测 200 个节点就卡），所以后端也不再提供全量分支；
// 要看某一块就用名称/关系筛选聚焦，或从实体详情页跳进来。
const limitHint = computed(() =>
  !focusName.value && datasource.value?.truncated
    ? `默认视图按四类均衡取样，最多展示前 ${datasource.value.node_limit || 100} 个实体节点；`
      + '搜索名称或按关系筛选可查看其余节点。'
    : '',
)

function syncPageSummary() {
  graphPageSummary?.setGraphPageSummary(
    datasource.value.nodes?.length || 0,
    datasource.value.lines?.length || 0,
  )
}

async function loadNodeRelations(nodeId: string) {
  loading.value = true
  try {
    const response = await getNodeRelations(nodeId)
    if (response.code === 200) {
      // 合并去重的实现统一在 utils/graph.ts，不要在本页另写一份
      datasource.value = mergeNodeRelations(datasource.value, response.data || {})
      syncPageSummary()
    }
  } catch (error) {
    console.error('获取节点关系失败:', error)
  } finally {
    loading.value = false
  }
}

/** 退出聚焦、回到四类均衡的总览。 */
async function loadOverview() {
  focusName.value = ''
  sessionStorage.removeItem('graphFocus')
  await getGraph(false)
}

async function getGraph(allowFocus = true) {
  loading.value = true
  try {
    const focus = allowFocus ? getGraphFocus() : null
    const response = focus
      ? await getGraphNodeContext(focus)
      : await searchNameKg({})
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
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.graph-subtitle {
  text-align: center;
}

.graph-limit-hint {
  flex-shrink: 0;
  margin: 0 0 8px;
  padding: 0 4px;
  color: #8c6d3b;
  font-size: 13px;
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
