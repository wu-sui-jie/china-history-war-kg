<template>
  <div class="analysis-page">
    <div class="page-header">
      <div>
        <h1>关系分析</h1>
      </div>
      <lay-button type="primary" @click="goGraph">带入图谱页</lay-button>
    </div>

    <div class="filter-panel">
      <lay-input v-model="query.name" placeholder="输入实体名称" />
      <lay-select v-model="query.type">
        <lay-select-option value="">不限类型</lay-select-option>
        <lay-select-option value="Event">事件</lay-select-option>
        <lay-select-option value="Person">人物</lay-select-option>
        <lay-select-option value="Place">地点</lay-select-option>
        <lay-select-option value="Organization">组织</lay-select-option>
      </lay-select>
      <lay-input v-model="query.rel_type" placeholder="关系类型，如 参与/发生于/指挥/影响" />
      <lay-select v-model="query.depth">
        <lay-select-option :value="1">一跳关系</lay-select-option>
        <lay-select-option :value="2">二跳关系</lay-select-option>
      </lay-select>
      <lay-button type="primary" :loading="loading" @click="loadData">分析</lay-button>
    </div>

    <div class="summary-row">
      <div>实体：{{ graph.nodes?.length || 0 }}</div>
      <div>关系：{{ graph.lines?.length || 0 }}</div>
      <div>深度：{{ query.depth }} 跳</div>
    </div>

    <div class="graph-panel">
      <EChartsGraph :data="graph" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { relationAnalysis } from '../../api/module/graph'
import { apiErrorMessage } from '../../utils/apiError'
import EChartsGraph from './graph/EChartsGraph.vue'

const router = useRouter()
const loading = ref(false)
const graph = ref<any>({ nodes: [], lines: [] })
const query = reactive({
  name: '',
  type: '',
  rel_type: '',
  depth: 1,
})

const loadData = async () => {
  if (!query.name.trim()) {
    layer.msg('请输入实体名称', { icon: 2 })
    return
  }
  loading.value = true
  try {
    const res = await relationAnalysis(query)
    graph.value = res.code === 200 ? res.data : { nodes: [], lines: [] }
  } catch (error) {
    // 失败改为 HTTP 4xx/5xx（第 13 轮复核第七节）：不清空的话，画面上会留着上一次
    // 查询的图，用户会以为那就是本次结果；同时把后端或兜底文案提示出来。
    console.error('关系分析失败:', error)
    graph.value = { nodes: [], lines: [] }
    layer.msg(apiErrorMessage(error, '关系分析失败，请稍后重试'), { icon: 2 })
  } finally {
    loading.value = false
  }
}

const goGraph = () => {
  if (!query.name.trim()) {
    router.push('/knowledge/graph')
    return
  }
  router.push(`/knowledge/graph?focus=1&name=${encodeURIComponent(query.name)}&type=${query.type}`)
}
</script>

<style scoped>
.analysis-page {
  padding: 20px;
  min-height: 100%;
  background: linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header,
.filter-panel,
.summary-row,
.graph-panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 28px rgba(74, 54, 24, 0.08);
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 22px;
  margin-bottom: 16px;
}

.page-header h1 {
  margin: 0 0 8px;
  color: #2f3542;
}

.page-header p {
  margin: 0;
  color: #6b7280;
}

.filter-panel {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1.2fr 1fr auto;
  gap: 10px;
  padding: 16px;
  margin-bottom: 16px;
}

.summary-row {
  display: flex;
  gap: 16px;
  padding: 14px 16px;
  margin-bottom: 16px;
  color: #4b5563;
}

.graph-panel {
  padding: 10px;
  min-height: 780px;
}

@media (max-width: 1000px) {
  .filter-panel {
    grid-template-columns: 1fr;
  }
}
</style>
