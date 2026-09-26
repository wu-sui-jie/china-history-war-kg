<template>
  <div class="timeline-page">
    <div class="page-header">
      <div class="hero-copy">
        <div class="eyebrow">Chronicle</div>
        <h1>历史时间轴</h1>
        <p>按朝代、人物、组织与事件类型浏览战争进程，快速定位时间异常与关键节点。</p>
      </div>
      <div class="header-actions">
        <lay-button @click="router.push('/workspace/dashboard')">返回首页仪表盘</lay-button>
        <lay-button @click="router.push('/knowledge/map')">历史地图</lay-button>
        <lay-button type="primary" @click="loadData">刷新</lay-button>
      </div>
    </div>

    <div class="filter-bar">
      <div class="filter-grid">
        <lay-input v-model="filters.keyword" placeholder="搜索事件名称" />
        <lay-select v-model="filters.dynasty">
          <lay-select-option value="">全部朝代</lay-select-option>
          <lay-select-option v-for="item in dynasties" :key="item" :value="item">{{ item }}</lay-select-option>
        </lay-select>
        <lay-input v-model="filters.participant" placeholder="人物/组织参与时间线" />
        <lay-input v-model="filters.event_type" placeholder="事件类型" />
      </div>
      <div class="filter-actions">
        <lay-checkbox v-model="onlyIssues" value="onlyIssues">只看时间异常</lay-checkbox>
        <lay-button type="primary" @click="loadData">筛选</lay-button>
        <lay-button @click="resetFilters">重置</lay-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span>事件总数</span>
        <strong>{{ timeline.summary?.total_events || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>可排序事件</span>
        <strong>{{ timeline.summary?.with_year || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>待补时间</span>
        <strong>{{ timeline.summary?.without_year || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>时间异常</span>
        <strong>{{ timeline.summary?.timeline_issues || 0 }}</strong>
      </div>
    </div>

    <div class="timeline-list">
      <div v-for="item in timeline.events || []" :key="item.id" class="timeline-card" @click="openDetail(item.id)">
        <div class="timeline-rail"></div>
        <div class="timeline-year">
          {{ item.parsed_year ?? '待补全' }}
        </div>
        <div class="timeline-content">
          <div class="title-row">
            <h3>{{ item.name }}</h3>
            <span class="dynasty-badge">{{ item.dynasty || '朝代待补全' }}</span>
          </div>
          <p>{{ item.event_type || '类型待补全' }} · {{ item.place || '地点待补全' }}</p>
          <div class="meta-row">
            <span>开始：{{ item.start_date || '-' }}</span>
            <span>结束：{{ item.end_date || '-' }}</span>
            <span v-if="item.quality_flags?.timeline_problems?.length" class="warning">
              {{ item.quality_flags.timeline_problems.map(problemLabel).join('、') }}
            </span>
          </div>
        </div>
      </div>
      <div v-if="!(timeline.events || []).length" class="empty-state">暂无符合条件的事件</div>
    </div>

    <div class="content-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>朝代分层时间线</h3>
          <span>{{ (timeline.dynasty_groups || []).length }} 个朝代</span>
        </div>
        <div class="dynasty-group-list">
          <div v-for="group in timeline.dynasty_groups || []" :key="group.dynasty" class="dynasty-group">
            <div class="group-title">
              <strong>{{ group.dynasty }}</strong>
              <span>{{ group.count }} 场</span>
            </div>
            <div class="group-events">
              <lay-tag v-for="event in group.events || []" :key="event.id" @click="openDetail(event.id)">
                {{ event.name }} · {{ event.start_date || event.end_date || '待补时点' }}
              </lay-tag>
            </div>
          </div>
          <div v-if="!(timeline.dynasty_groups || []).length" class="empty-state">暂无朝代分层数据</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>同期战争对比</h3>
          <span>{{ (timeline.comparisons || []).length }} 组</span>
        </div>
        <div class="compare-list">
          <div v-for="item in timeline.comparisons || []" :key="item.year" class="compare-item">
            <strong>{{ item.year }}</strong>
            <div class="compare-tags">
              <lay-tag v-for="event in item.events || []" :key="event.id" @click="openDetail(event.id)">
                {{ event.name }}
              </lay-tag>
            </div>
          </div>
          <div v-if="!(timeline.comparisons || []).length" class="empty-state">暂无同期战争数据</div>
        </div>
      </div>
    </div>

    <div class="panel" v-if="filters.participant && (timeline.participant_timeline || []).length">
      <div class="panel-header">
        <h3>参与者战争时间线</h3>
        <span>{{ filters.participant }} · {{ (timeline.participant_timeline || []).length }} 场</span>
      </div>
      <div class="participant-line">
        <div v-for="item in timeline.participant_timeline || []" :key="item.id" class="participant-item" @click="openDetail(item.id)">
          <strong>{{ item.name }}</strong>
          <span>{{ item.start_date || item.end_date || '时间待补全' }}</span>
          <small>{{ item.dynasty || '朝代待补全' }}</small>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useRoute } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { getTimelineEvents } from '../../api/module/workspace'
import { apiErrorMessage } from '../../utils/apiError'
import { problemLabel } from '../../utils/knowledge'

const router = useRouter()
const route = useRoute()

const filters = reactive({
  keyword: '',
  dynasty: '',
  participant: '',
  event_type: '',
})
const onlyIssues = ref(false)

const timeline = ref<any>({
  summary: {},
  dynasties: [],
  events: [],
})

const dynasties = computed(() => timeline.value.dynasties || [])

const loadData = async () => {
  // 后端失败是 HTTP 5xx（失败不再伪装成 200），
  // 因此必须走 catch —— 只判断 `res.code === 200` 的话，失败时页面会静默不动、
  // 用户看不到任何原因（axios 异常分支里才有后端的 msg）。
  try {
    const res = await getTimelineEvents({
      ...filters,
      only_issues: onlyIssues.value ? '1' : '0',
    })
    if (res.code === 200) {
      timeline.value = res.data || {}
      return
    }
    layer.msg(res.msg || '加载时间轴失败', { icon: 2 })
  } catch (error) {
    console.error('加载时间轴失败:', error)
    layer.msg(apiErrorMessage(error, '加载时间轴失败，请稍后重试'), { icon: 2 })
  }
}

const resetFilters = () => {
  filters.keyword = ''
  filters.dynasty = ''
  filters.participant = ''
  filters.event_type = ''
  onlyIssues.value = false
  loadData()
}

const openDetail = (id: number) => {
  // 实体详情是 query 传参的单一路由（/knowledge/entity-detail），没有 /knowledge/entity/:type/:id
  router.push(`/knowledge/entity-detail?type=Event&id=${id}&back=${encodeURIComponent('/knowledge/timeline')}`)
}

onMounted(() => {
  if (route.query.dynasty) filters.dynasty = String(route.query.dynasty)
  if (route.query.keyword) filters.keyword = String(route.query.keyword)
  if (route.query.participant) filters.participant = String(route.query.participant)
  loadData()
})
</script>

<style scoped>
.timeline-page {
  padding: 20px;
  min-height: 100%;
  background:
    radial-gradient(circle at top left, rgba(139, 30, 35, 0.1), transparent 20%),
    linear-gradient(180deg, #f6f0e6 0%, #eff5fc 100%);
}

.page-header,
.filter-bar,
.summary-card,
.timeline-card,
.panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(74, 54, 24, 0.08);
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 26px;
  margin-bottom: 18px;
}

.eyebrow {
  color: #9a7441;
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.page-header h1 {
  margin: 10px 0 8px;
  font-size: 38px;
  color: #2f3542;
}

.page-header p {
  margin: 0;
  color: #6b7280;
  max-width: 760px;
  line-height: 1.7;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: flex-start;
}

.filter-bar {
  padding: 18px;
  margin-bottom: 18px;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.filter-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.summary-card {
  padding: 20px;
}

.summary-card span {
  display: block;
  margin-bottom: 10px;
  color: #8c6d3b;
  font-size: 13px;
}

.summary-card strong {
  font-size: 30px;
  color: #111827;
}

.timeline-list {
  display: grid;
  gap: 14px;
  margin-bottom: 16px;
}

.timeline-card {
  position: relative;
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 20px;
  padding: 20px 20px 20px 28px;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.timeline-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 16px 34px rgba(74, 54, 24, 0.12);
}

.timeline-rail {
  position: absolute;
  left: 14px;
  top: 18px;
  bottom: 18px;
  width: 3px;
  border-radius: 999px;
  background: linear-gradient(180deg, #8b1e23, rgba(197, 155, 88, 0.24));
}

.timeline-year {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  background: linear-gradient(135deg, #8b1e23, #c59b58);
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  min-height: 90px;
}

.title-row,
.meta-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.title-row h3 {
  margin: 0;
  color: #2f3542;
}

.title-row span,
.meta-row span,
.timeline-content p {
  color: #6b7280;
}

.dynasty-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  background: #f4ead8;
  color: #8c6d3b !important;
  font-size: 12px;
}

.timeline-content p {
  margin: 8px 0 12px;
  font-size: 14px;
}

.warning {
  color: #c2410c !important;
}

.empty-state {
  color: #9ca3af;
  padding: 18px 0;
  text-align: center;
}

.content-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.panel {
  padding: 20px;
}

.panel-header,
.group-title,
.participant-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-header {
  margin-bottom: 12px;
}

.panel-header h3 {
  margin: 0;
  color: #2f3542;
}

.dynasty-group-list,
.compare-list,
.participant-line {
  display: grid;
  gap: 12px;
}

.dynasty-group,
.compare-item,
.participant-item {
  padding: 16px;
  border-radius: 14px;
  background: #f8fafc;
}

.group-events,
.compare-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.participant-item {
  cursor: pointer;
}

.participant-item strong {
  color: #111827;
}

.participant-item span,
.participant-item small,
.group-title span,
.panel-header span {
  color: #6b7280;
}

:deep(.layui-tag) {
  cursor: pointer;
}

@media (max-width: 900px) {
  .filter-grid,
  .summary-grid {
    grid-template-columns: 1fr 1fr;
  }

  .timeline-card,
  .content-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .timeline-page {
    padding: 14px;
  }

  .page-header {
    display: block;
  }

  .header-actions {
    margin-top: 12px;
  }

  .filter-grid,
  .summary-grid {
    grid-template-columns: 1fr;
  }

  .page-header h1 {
    font-size: 32px;
  }
}
</style>
