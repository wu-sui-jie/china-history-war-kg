<template>
  <div class="workspace-page">
    <div class="page-header">
      <div>
        <h1>数据集中心</h1>
      </div>
      <div class="header-actions">
        <lay-button @click="goDashboard">返回首页仪表盘</lay-button>
        <lay-button type="primary" @click="loadData">刷新数据</lay-button>
      </div>
    </div>

    <div class="overview-grid">
      <div class="overview-card" v-for="item in summaryCards" :key="item.title">
        <span>{{ item.title }}</span>
        <strong>{{ item.value }}</strong>
      </div>
    </div>

    <div class="panel-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>关系结构</h3>
          <span>当前主关系表分布</span>
        </div>
        <div class="relation-list">
          <div v-for="item in dataInfo.relation_breakdown || []" :key="item.name" class="relation-item">
            <span>{{ item.name }}</span>
            <strong>{{ item.value }}</strong>
          </div>
          <div v-if="!(dataInfo.relation_breakdown || []).length" class="relation-item empty-row">
            暂无关系结构数据
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>数据可用性概览</h3>
          <span>面向图谱浏览、时间轴和问答使用</span>
        </div>
        <div class="relation-list">
          <div v-for="item in availabilityCards" :key="item.label" class="relation-item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <p>{{ item.description }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { getDatasetOverview } from '../../api/module/workspace'

const router = useRouter()

const dataInfo = ref<any>({
  counts: {},
  relation_breakdown: [],
  quality_report: {},
})

const summaryCards = computed(() => [
  { title: '实体总量', value: dataInfo.value.counts?.entities || 0 },
  { title: '事件数量', value: dataInfo.value.counts?.events || 0 },
  { title: '组织数量', value: dataInfo.value.counts?.organizations || 0 },
  { title: '人物数量', value: dataInfo.value.counts?.persons || 0 },
  { title: '关系数量', value: dataInfo.value.counts?.relations || 0 },
  { title: '地点数量', value: dataInfo.value.counts?.places || 0 },
])

const availabilityCards = computed(() => {
  const events = Number(dataInfo.value.counts?.events || 0)
  const places = Number(dataInfo.value.counts?.places || 0)
  const relations = Number(dataInfo.value.counts?.relations || 0)
  const missingSource = sumObject(dataInfo.value.quality_report?.missing_source_text)
  const missingEvidence = sumObject(dataInfo.value.quality_report?.missing_evidence)

  return [
    {
      label: '来源文本覆盖率',
      value: percent(events - missingSource, events),
      description: '表示事件数据中有多少条保留了原始来源文本，可用于回溯抽取依据与人工核验。'
    },
    {
      label: '问答证据可用率',
      value: percent(events - missingEvidence, events),
      description: '表示当前数据中可直接为问答或推理提供证据支撑的比例，比例越高越适合知识问答。'
    },
    {
      label: '平均事件关系数',
      value: events ? (relations / events).toFixed(1) : '0.0',
      description: '表示每个战争事件平均连接了多少条关系，用于判断事件之间及事件与实体之间的关联丰富度。'
    },
    {
      label: '地点事件支撑比',
      value: places ? `${(events / places).toFixed(1)} : 1` : '0 : 1',
      description: '表示平均每个地点可支撑多少条战争事件，数值越高通常说明地点与事件的关联更集中、更可用。'
    },
  ]
})

const sumObject = (source: Record<string, number> | undefined) => {
  if (!source) return 0
  return Object.values(source).reduce((sum, value) => sum + Number(value || 0), 0)
}

const percent = (part: number, total: number) => {
  if (!total) return '0%'
  return `${Math.max(0, Math.min(100, Math.round((part / total) * 100)))}%`
}

const loadData = async () => {
  try {
    const res = await getDatasetOverview()
    if (res.code === 200) {
      dataInfo.value = res.data || {}
      return
    }
    layer.msg(res.msg || '加载数据集信息失败', { icon: 2 })
  } catch (error) {
    console.error('加载数据集信息失败', error)
    layer.msg('加载数据集信息失败', { icon: 2 })
  }
}

const goDashboard = () => {
  router.push('/workspace/dashboard')
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.workspace-page {
  padding: 20px;
  min-height: 100%;
  background: linear-gradient(180deg, #f7f6f2 0%, #eef4fb 100%);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.page-header h1 {
  margin: 0 0 8px;
  font-size: 30px;
  color: #2f3542;
}

.page-header p {
  margin: 0;
  color: #6b7280;
}

.overview-grid,
.panel-grid {
  display: grid;
  gap: 16px;
}

.overview-grid {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin-bottom: 16px;
}

.overview-card,
.panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(74, 54, 24, 0.08);
}

.overview-card {
  padding: 18px;
}

.overview-card span {
  display: block;
  color: #8c6d3b;
  font-size: 13px;
  margin-bottom: 10px;
}

.overview-card strong {
  font-size: 32px;
  color: #111827;
}

.panel-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.panel {
  padding: 18px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.panel-header h3 {
  margin: 0;
  color: #2f3542;
}

.panel-header span {
  color: #8c6d3b;
  font-size: 12px;
}

.relation-list {
  display: grid;
  gap: 12px;
}

.relation-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #f8fafc;
  border-radius: 12px;
  padding: 14px;
}

.relation-item span {
  color: #6b7280;
}

.relation-item strong {
  color: #111827;
  font-size: 16px;
  word-break: break-all;
}

.relation-item p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.empty-row {
  color: #9ca3af;
  justify-content: center;
}

@media (max-width: 1200px) {
  .overview-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .panel-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 992px) {
  .overview-grid,
  .panel-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
  }

  .overview-grid,
  .panel-grid {
    grid-template-columns: 1fr;
  }
}
</style>
