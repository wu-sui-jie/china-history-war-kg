<!--
  战争关系图的四个子页（历史战争 / 参战势力 / 历史人物 / 战争地点）。

  四者共用一份实现、由路由 meta.graphKind 驱动，差异集中在 GRAPH_CONFIGS 的「名称标签、
  搜索提示、关系筛选枚举、接口路径」四项。新增关系维度时只需在 GRAPH_CONFIGS 加一项
  并在 base-routes.ts 注册路由。
-->
<template>
  <lay-container fluid="true" class="graph-subpage">
    <lay-card class="graph-filter-card">
      <lay-form style="margin-top: 10px">
        <lay-row>
          <lay-col :md="8">
            <lay-form-item :label="config.label" label-width="80">
              <lay-input
                v-model="searchQuery.name"
                :placeholder="config.placeholder"
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
                  v-for="type in config.relTypes"
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
      <p v-if="limitHint" class="graph-limit-hint">{{ limitHint }}</p>
    </lay-card>
    <div class="graph-canvas">
      <EChartsGraph :data="datasource" @node-expanded="loadNodeRelations" />
      <div v-if="loading || expanding" class="graph-loading-mask">加载图谱中...</div>
    </div>
  </lay-container>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { layer } from '@layui/layui-vue'
import EChartsGraph from './EChartsGraph.vue'
import { getEventGraph, type EventGraphEndpoint } from '@/api/module/graph'
import { getNodeRelations } from '@/api/module/node'
import { apiErrorMessage } from '@/utils/apiError'
import { mergeNodeRelations } from '@/utils/graph'

/** 四个子页的全部差异集中在这里，模板与逻辑共享。 */
const GRAPH_CONFIGS: Record<string, { label: string; placeholder: string; relTypes: string[]; endpoint: EventGraphEndpoint }> = {
  event: {
    label: '战争名称',
    placeholder: '请输入战争名称，如：淝水之战',
    relTypes: ['因果关系', '顺承关系', '并列关系', '包含关系', '条件关系'],
    endpoint: '/api/graph/event_event'
  },
  organization: {
    label: '名称',
    placeholder: '请输入战争或势力名称',
    relTypes: ['发起方', '防守方', '支援方', '同盟方', '投降方', '被俘方', '议和方', '调停方'],
    endpoint: '/api/graph/event_organization'
  },
  person: {
    label: '名称',
    placeholder: '请输入战争或人物名称',
    relTypes: ['统帅', '将领', '谋士', '使者', '君主', '参与者', '俘虏', '阵亡', '投降', '叛变', '可汗'],
    endpoint: '/api/graph/event_person'
  },
  place: {
    label: '名称',
    placeholder: '请输入战争或地点名称',
    relTypes: ['主战场', '次要战场', '出发地', '目的地', '途经地', '驻防地', '指挥所', '补给地', '战略要地', '议和地点'],
    endpoint: '/api/graph/event_place'
  }
}

const route = useRoute()
const config = computed(() => GRAPH_CONFIGS[route.meta.graphKind as string] || GRAPH_CONFIGS.event)

const datasource = ref<any>({ nodes: [], lines: [] })
const loading = ref(false)
const expanding = ref(false)
const graphPageSummary = inject<any>('graphPageSummary', null)

const searchQuery = ref({
  name: '',
  rel_type: ''
})

// 后端默认视图只返回前 100 个实体节点（见 model_search.DEFAULT_VIEW_NODE_LIMIT）。
// 不说明的话用户会以为"这张图就这么大"，而不是"还有更多、搜索才出来"。
const limitHint = computed(() =>
  datasource.value?.truncated
    ? `默认视图只展示前 ${datasource.value.node_limit || 100} 个实体节点，搜索名称或按关系筛选可查看其余节点。`
    : '',
)

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
    const response = await getNodeRelations(nodeId)
    if (response.code === 200) {
      // 合并去重的实现统一在 utils/graph.ts，不要在本页另写一份
      datasource.value = mergeNodeRelations(datasource.value, response.data || {})
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
    const response = await getEventGraph(config.value.endpoint, {
      name: searchQuery.value.name?.trim() || '',
      rel_type: searchQuery.value.rel_type || '',
    })
    if (response.code === 200) {
      datasource.value = response.data
    } else {
      datasource.value = { nodes: [], lines: [] }
    }
  } catch (error) {
    // 后端失败是 HTTP 5xx：清空画布之外还要给一句提示，
    // 否则用户只看到"图没了"，分不清是查询失败还是本来就没有数据。
    console.error('请求图谱数据失败:', error)
    datasource.value = { nodes: [], lines: [] }
    layer.msg(apiErrorMessage(error, '加载图谱数据失败，请稍后重试'), { icon: 2 })
  } finally {
    syncPageSummary()
    loading.value = false
  }
}

// 四个子页共用同一组件实例（路由切换不重建），切换维度时必须重置筛选并重新取数
watch(() => route.meta.graphKind, () => {
  searchQuery.value.name = ''
  searchQuery.value.rel_type = ''
  getGraph()
})

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

.graph-limit-hint {
  margin: 0 0 4px 20px;
  color: #8c6d3b;
  font-size: 13px;
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
