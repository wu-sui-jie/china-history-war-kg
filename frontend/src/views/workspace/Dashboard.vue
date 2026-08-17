<template>
  <div class="workspace-page">
    <div class="page-header">
      <div>
        <h1>首页仪表盘</h1>
        <div class="header-meta">
          <span>最近刷新：{{ lastRefreshTime || '尚未刷新' }}</span>
        </div>
      </div>

      <div class="header-actions">
        <lay-button type="primary" :loading="loading" @click="loadData(false)">刷新数据</lay-button>
      </div>
    </div>

    <div class="quick-nav">
      <div class="quick-nav-card active" @click="goDashboard">
        <span class="nav-title">首页仪表盘</span>
        <span class="nav-desc">查看概览统计与最近状态</span>
      </div>
      <div class="quick-nav-card" @click="goDatasetCenter">
        <span class="nav-title">数据集中心</span>
        <span class="nav-desc">查看当前导入批次与版本信息</span>
      </div>
      <div class="quick-nav-card" @click="goQualityPage">
        <span class="nav-title">图谱质检</span>
        <span class="nav-desc">查看缺失字段、孤立节点与时间异常</span>
      </div>
      <div class="quick-nav-card" @click="goTimeline('')">
        <span class="nav-title">历史时间轴</span>
        <span class="nav-desc">查看朝代事件分布与历史时间脉络</span>
      </div>
    </div>

    <div class="card-grid">
      <div v-for="card in cards" :key="card.title" class="metric-card">
        <div class="metric-title">{{ card.title }}</div>
        <div class="metric-value">{{ card.value }}</div>
        <div class="metric-subtitle">{{ card.subtitle }}</div>
      </div>
    </div>

    <div class="panel-grid">
      <div class="panel panel-wide">
        <div class="panel-header">
          <h3>朝代时间分布</h3>
          <span>按朝代序列统计展示</span>
        </div>
        <div class="bar-list dynasty-bar-list">
          <div
            v-for="item in visibleDynastyDistribution"
            :key="item.name"
            class="bar-item"
            @click="goTimeline(item.name)"
          >
            <div class="bar-row">
              <span>{{ item.name }}</span>
              <strong>{{ item.value }}</strong>
            </div>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: calcWidth(item.value) }"></div>
            </div>
          </div>
          <div v-if="!visibleDynastyDistribution.length" class="empty-state">暂无朝代分布数据</div>
        </div>
      </div>

      <div class="panel panel-side">
        <div class="panel-header">
          <h3>质检快照</h3>
          <span>当前导入批次</span>
        </div>
        <div class="snapshot-list">
          <div class="snapshot-item">
            <span>孤立节点</span>
            <strong>{{ dashboard.quality_snapshot?.isolated_nodes || 0 }}</strong>
          </div>
          <div class="snapshot-item">
            <span>时间异常</span>
            <strong>{{ dashboard.quality_snapshot?.timeline_issues || 0 }}</strong>
          </div>
          <div class="snapshot-item">
            <span>缺少来源文本</span>
            <strong>{{ dashboard.quality_snapshot?.missing_source_text || 0 }}</strong>
          </div>
          <div class="snapshot-item">
            <span>缺少证据关系</span>
            <strong>{{ dashboard.quality_snapshot?.missing_evidence || 0 }}</strong>
          </div>
        </div>
        <div class="panel-inline-actions">
          <lay-button size="sm" @click="goQualityPage">进入质检工作台</lay-button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { getDashboardOverview } from '../../api/module/workspace'

// 仪表盘数据结构：与后端接口保持对应，便于后续扩展字段
const dashboard = ref<any>({
  cards: [],
  dynasty_distribution: [],
  quality_snapshot: {}
})

const router = useRouter()
const loading = ref(false)
const lastRefreshTime = ref('')
const cards = computed(() => dashboard.value.cards || [])
const visibleDynastyDistribution = computed(() => {
  const rows = dashboard.value.dynasty_distribution || []
  const withData = rows.filter((item: any) => Number(item.value) > 0)
  return withData.length ? withData : rows
})

// 根据最大值计算横条宽度，增强可读性
const calcWidth = (value: number) => {
  const maxValue = Math.max(...visibleDynastyDistribution.value.map((item: any) => Number(item.value) || 0), 1)
  return `${(value / maxValue) * 100}%`
}

// 统一格式化刷新时间
const formatNow = () => {
  const now = new Date()
  const pad = (num: number) => String(num).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
}

// 加载首页概览数据；首次加载不弹提示，手动刷新给出明确反馈
const loadData = async (silent = true) => {
  loading.value = true
  try {
    const res = await getDashboardOverview()
    if (res.code === 200) {
      dashboard.value = res.data || {}
      lastRefreshTime.value = formatNow()
      if (!silent) {
        layer.msg('仪表盘数据已刷新', { icon: 1 })
      }
    } else {
      layer.msg(res.msg || '加载首页数据失败', { icon: 2 })
    }
  } catch (error) {
    console.error('加载仪表盘数据失败:', error)
    layer.msg('加载首页数据失败', { icon: 2 })
  } finally {
    loading.value = false
  }
}

// 提供显式跳转入口，解决切换到其他页面后返回不直观的问题
const goDashboard = () => router.push('/workspace/dashboard')
const goDatasetCenter = () => router.push('/workspace/dataset')
const goQualityPage = () => router.push('/workspace/quality')
const goTimeline = (dynasty: string) => {
  const target = dynasty ? `/knowledge/timeline?dynasty=${encodeURIComponent(dynasty)}` : '/knowledge/timeline'
  router.push(target)
}

onMounted(() => {
  loadData(true)
})
</script>

<style scoped>
.workspace-page {
  padding: 20px;
  background: linear-gradient(180deg, #f7f3ec 0%, #f3f6fb 100%);
  min-height: 100%;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}

.page-header h1 {
  margin: 0 0 8px;
  font-size: 30px;
  color: #2f3542;
}

.page-header p {
  margin: 0 0 10px;
  color: #6b7280;
}

.header-meta {
  color: #8c6d3b;
  font-size: 12px;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.quick-nav {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.quick-nav-card {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(74, 54, 24, 0.08);
  padding: 16px 18px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.quick-nav-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 32px rgba(74, 54, 24, 0.12);
}

.quick-nav-card.active {
  background: linear-gradient(135deg, #fff8ef, #ffffff);
  border-color: #c59b58;
}

.nav-title {
  display: block;
  color: #2f3542;
  font-weight: 600;
  margin-bottom: 8px;
}

.nav-desc {
  display: block;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.metric-card,
.panel {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(74, 54, 24, 0.08);
}

.metric-card {
  padding: 18px;
}

.metric-title {
  color: #8c6d3b;
  font-size: 13px;
  margin-bottom: 12px;
}

.metric-value {
  font-size: 34px;
  font-weight: 700;
  color: #2e2f33;
  margin-bottom: 6px;
}

.metric-subtitle {
  color: #7b8190;
  font-size: 12px;
  line-height: 1.6;
}

.panel-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.95fr);
  align-items: start;
  gap: 16px;
  margin-bottom: 16px;
}

.panel {
  padding: 18px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.panel-header h3 {
  margin: 0;
  font-size: 18px;
  color: #2f3542;
}

.panel-header span {
  color: #8c6d3b;
  font-size: 12px;
}

.stat-list,
.snapshot-list {
  display: grid;
  gap: 12px;
}

.stat-item,
.snapshot-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  background: #f8fafc;
  border-radius: 12px;
}

.stat-item span,
.snapshot-item span {
  color: #4b5563;
}

.stat-item strong,
.snapshot-item strong {
  color: #111827;
  font-size: 18px;
}

.bar-list {
  display: grid;
  gap: 14px;
}

.bar-item {
  cursor: pointer;
}

.bar-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  color: #4b5563;
}

.bar-track {
  width: 100%;
  height: 10px;
  background: #efe6d8;
  border-radius: 999px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #b88a44, #7f5af0);
  border-radius: 999px;
}

.dynasty-bar-list {
  max-height: 520px;
  overflow-y: auto;
  padding-right: 6px;
}

.panel-inline-actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.empty-state {
  color: #9ca3af;
  padding: 10px 0;
}

@media (max-width: 1200px) {
  .quick-nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .panel-grid {
    grid-template-columns: 1fr;
  }

}

@media (max-width: 900px) {
  .quick-nav {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .workspace-page {
    padding: 14px;
  }

  .page-header,
  .panel-grid,
  .card-grid {
    grid-template-columns: 1fr;
    display: grid;
  }

  .page-header {
    display: block;
  }

  .header-actions {
    margin-top: 12px;
  }
}
</style>
