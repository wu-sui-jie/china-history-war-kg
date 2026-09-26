<template>
  <div class="entity-detail-page">
    <div class="hero-card">
      <div>
        <div class="eyebrow">实体详情页</div>
        <h1>{{ displayName }}</h1>
      </div>
      <div class="hero-actions" @click.capture="rememberGraphFocus">
        <lay-button @click="goBackToSource">返回原页面</lay-button>
        <lay-button @click="goGraphPage">查看图谱</lay-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span>实体类型</span>
        <strong>{{ typeLabel(entity.node?.type) }}</strong>
      </div>
      <div class="summary-card">
        <span>关联关系</span>
        <strong>{{ entity.quality_flags?.relation_count || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>缺失字段</span>
        <strong>{{ (entity.quality_flags?.missing_fields || []).length }}</strong>
      </div>
      <div class="summary-card">
        <span>重复风险</span>
        <strong>{{ Math.max((entity.quality_flags?.duplicate_count || 1) - 1, 0) }}</strong>
      </div>
    </div>

    <div class="content-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>核心属性</h3>
          <!-- 普通用户不显示；且实体数据未就绪时不带 undefined 参数跳转 -->
          <lay-button v-if="hasWriteRole && entity.node?.type" size="sm" @click="goRepair">进入修复工作台</lay-button>
        </div>
        <div class="property-list">
          <div v-for="[key, value] in visibleFields" :key="key" class="property-item">
            <label>{{ fieldLabel(key) }}</label>
            <div>{{ fieldValueLabel(key, value) }}</div>
          </div>
        </div>
        <div v-if="structuredRelationAttributes.length" class="structured-relations">
          <div class="structured-title">
            <span>关联关系</span>
            <small>{{ structuredRelationAttributes.length }} 组</small>
          </div>
          <div v-for="(item, index) in structuredRelationAttributes" :key="`${item.relation}-${index}`" class="structured-item">
            <div class="structured-head">
              <div class="relation-badge">{{ item.relation }}</div>
              <div class="relation-meta">
                <span>{{ item.targets.length }} 个对象</span>
                <span>{{ item.evidences.length }} 条证据</span>
              </div>
            </div>
            <div class="structured-row">
              <label>关系对象</label>
              <div class="tag-list">
                <lay-tag v-for="target in item.targets" :key="target">{{ target }}</lay-tag>
                <span v-if="!item.targets.length" class="muted-text">暂无关系对象</span>
              </div>
            </div>
            <div class="structured-row">
              <label>证据</label>
              <div class="evidence-list">
                <div v-for="evidence in item.evidences" :key="evidence" class="evidence-card">{{ evidence }}</div>
                <span v-if="!item.evidences.length" class="muted-text">暂无证据</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>质量提示</h3>
          <span>优先修复会影响问答和时间轴的字段</span>
        </div>
        <div class="flag-list">
          <div class="flag-item">
            <label>缺失字段</label>
            <div>{{ formatList(entity.quality_flags?.missing_fields, fieldLabel) }}</div>
          </div>
          <div class="flag-item">
            <label>时间问题</label>
            <div>{{ formatList(entity.quality_flags?.timeline_problems, problemLabel) }}</div>
          </div>
          <div class="flag-item">
            <label>孤立节点</label>
            <div>{{ entity.quality_flags?.is_isolated ? '是' : '否' }}</div>
          </div>
          <div v-if="entity.quality_flags?.coordinate_quality" class="flag-item">
            <label>地图展示</label>
            <div>{{ coordinateQualityText }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="content-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>关联实体</h3>
          <span>{{ entity.relations?.length || 0 }} 条</span>
        </div>
        <div class="relation-list">
          <div v-for="item in entity.relations || []" :key="`${item.target_type}-${item.target_id}-${item.relation_type}`" class="relation-item">
            <div>
              <strong>{{ item.target_name }}</strong>
              <span>{{ item.relation_type }} · {{ item.target_type_label }}</span>
            </div>
            <lay-button size="xs" @click="openDetail(item.target_type, item.target_id)">详情</lay-button>
          </div>
          <div v-if="!(entity.relations || []).length" class="empty-state">暂无结构化关系</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>时间邻近事件</h3>
          <span>用于补足时间维度</span>
        </div>
        <div class="timeline-list">
          <div v-for="item in entity.timeline_context || []" :key="item.id" class="timeline-item">
            <div>
              <strong>{{ item.name }}</strong>
              <span>{{ item.start_date || item.end_date || '时间待补全' }}</span>
            </div>
            <lay-button size="xs" @click="openDetail('Event', item.id)">查看</lay-button>
          </div>
          <div v-if="!(entity.timeline_context || []).length" class="empty-state">当前实体没有可展示的时间邻近事件</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { getEntityDetail } from '../../api/module/workspace'
import { apiErrorMessage } from '../../utils/apiError'
import { fieldLabel, fieldValueLabel, groupRelationAttributes, problemLabel, toEditableFields, typeLabel } from '@/utils/knowledge'
import { useHasWriteRole } from '@/utils/auth'

const route = useRoute()
const router = useRouter()
const hasWriteRole = useHasWriteRole()

const entity = ref<any>({
  node: {},
  quality_flags: {},
  relations: [],
  timeline_context: [],
})

const displayName = computed(() => {
  const node = entity.value.node || {}
  return node.EventName || node.PersonName || node.OrgName || node.geo_name || node.name || '未命名实体'
})

const visibleFields = computed(() => toEditableFields(entity.value.node || {}).filter(([key]) => key !== 'relations'))
const coordinateQualityText = computed(() => {
  const coord = entity.value.quality_flags?.coordinate_quality
  if (!coord) return '暂无坐标质检信息'
  const parts = [
    coord.is_mappable ? '可在地图显示' : '暂不可在地图显示',
    `来源：${coord.source_label || '未解析'}`,
  ]
  if (coord.confidence) parts.push(`置信度：${fieldValueLabel('coord_confidence', coord.confidence)}`)
  if (coord.note) parts.push(coord.note)
  return parts.join('；')
})
const structuredRelationAttributes = computed(() => groupRelationAttributes(entity.value.node?.relations))
const isAllowedBackPath = (path: string) => {
  if (!path.startsWith('/')) return false
  if (path.startsWith('/knowledge-list/')) return true
  if (path === '/knowledge/graph') return true
  if (path.startsWith('/knowledge/graph/')) return true
  if (path.startsWith('/knowledge/map')) return true
  if (path.startsWith('/knowledge/timeline')) return true
  if (path.startsWith('/knowledge/search')) return true
  if (path.startsWith('/knowledge/relation-analysis')) return true
  if (path.startsWith('/workspace/quality')) return true
  return false
}

const sourceBackPath = computed(() => {
  const back = String(route.query.back || '')
  if (isAllowedBackPath(back)) return back
  const cached = sessionStorage.getItem('entityDetailBackPath') || ''
  if (isAllowedBackPath(cached)) return cached
  return '/knowledge-list/event'
})

const loadData = async () => {
  const id = String(route.params.id || route.query.id || '')
  const type = String(route.params.type || route.query.type || '')
  if (!id || !type) {
    layer.msg('缺少实体参数', { icon: 2 })
    return
  }

  // 后端失败是 HTTP 4xx/5xx：参数缺失是 400、实体不存在是 404，
  // 两者都走 axios 异常分支，因此必须 catch 之后再取值——只判断 code 会让失败静默。
  try {
    const res = await getEntityDetail({ id, type })
    if (res.code === 200) {
      entity.value = res.data || {}
      const back = String(route.query.back || '')
      if (isAllowedBackPath(back)) {
        sessionStorage.setItem('entityDetailBackPath', back)
      }
    } else {
      layer.msg(res.msg || '加载实体详情失败', { icon: 2 })
    }
  } catch (error) {
    console.error('加载实体详情失败:', error)
    layer.msg(apiErrorMessage(error, '加载实体详情失败，请稍后重试'), { icon: 2 })
  }
}

const goBackToSource = () => {
  router.push(sourceBackPath.value)
}

const goGraphPage = () => {
  if (sourceBackPath.value === '/knowledge/graph' || sourceBackPath.value.startsWith('/knowledge/graph/')) {
    router.push(sourceBackPath.value)
    return
  }
  router.push('/knowledge/graph')
}

const rememberGraphFocus = (event: Event) => {
  const target = event.target as HTMLElement
  if (!target?.textContent?.includes('图') && !target?.textContent?.includes('鍥')) return

  const node = entity.value.node || {}
  sessionStorage.setItem('graphFocus', JSON.stringify({
    name: displayName.value,
    type: node.type || '',
    graph_id: node.neo4j_id || '',
  }))
}

const goRepair = () => {
  router.push(`/workspace/repair?type=${entity.value.node?.type}&id=${entity.value.node?.id}`)
}

const openDetail = (type: string, id: string | number) => {
  router.push(`/knowledge/entity-detail?type=${type}&id=${id}&back=${encodeURIComponent(sourceBackPath.value)}`)
}

const formatList = (items: string[] = [], formatter: (value?: string) => string) => {
  if (!items.length) return '无'
  return items.map((item) => formatter(item)).join('、')
}

watch(
  () => [route.query.id, route.query.type],
  () => loadData(),
)

onMounted(loadData)
</script>

<style scoped>
.entity-detail-page {
  padding: 20px;
  min-height: 100%;
  background:
    radial-gradient(circle at top left, rgba(196, 155, 88, 0.18), transparent 28%),
    linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.hero-card,
.summary-card,
.panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 20px;
  box-shadow: 0 14px 34px rgba(74, 54, 24, 0.08);
}

.hero-card {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 24px;
  margin-bottom: 18px;
}

.eyebrow {
  color: #9a7441;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.hero-card h1 {
  margin: 10px 0 8px;
  font-size: 34px;
  color: #2f3542;
}

.hero-card p {
  margin: 0;
  color: #6b7280;
}

.hero-actions {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  flex-wrap: wrap;
}

.summary-grid,
.content-grid {
  display: grid;
  gap: 16px;
}

.summary-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 16px;
}

.summary-card {
  padding: 18px;
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

.content-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-bottom: 16px;
}

.panel {
  padding: 18px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
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

.property-list,
.flag-list,
.relation-list,
.timeline-list,
.structured-relations,
.evidence-list {
  display: grid;
  gap: 12px;
}

.property-item,
.flag-item,
.relation-item,
.timeline-item,
.structured-item {
  padding: 14px;
  border-radius: 14px;
  background: #f8fafc;
}

.property-item label,
.flag-item label,
.structured-row label {
  display: block;
  margin-bottom: 6px;
  color: #8c6d3b;
  font-size: 12px;
}

.structured-relations {
  margin-top: 16px;
}

.structured-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #8c6d3b;
  font-size: 14px;
  font-weight: 600;
}

.structured-title small {
  font-size: 12px;
  color: #9a7441;
  font-weight: 500;
}

.structured-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.relation-badge {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 14px;
  border-radius: 999px;
  background: linear-gradient(135deg, #8b1e23, #b88945);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.relation-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.relation-meta span {
  padding: 4px 10px;
  border-radius: 999px;
  background: #f4ead8;
  color: #8c6d3b;
  font-size: 12px;
}

.structured-row {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 12px;
  align-items: start;
}

.tag-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.evidence-card {
  padding: 12px 14px;
  border-radius: 12px;
  background: linear-gradient(180deg, #ffffff 0%, #f3f7fb 100%);
  border-left: 3px solid #c59b58;
  color: #374151;
  line-height: 1.75;
}

.muted-text {
  color: #9ca3af;
  font-size: 13px;
}

.relation-item,
.timeline-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.relation-item strong,
.timeline-item strong {
  display: block;
  color: #111827;
}

.relation-item span,
.timeline-item span {
  color: #6b7280;
  font-size: 13px;
}

.empty-state {
  color: #9ca3af;
  padding: 12px 0;
}

@media (max-width: 1100px) {
  .summary-grid,
  .content-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 768px) {
  .entity-detail-page {
    padding: 14px;
  }

  .hero-card,
  .summary-grid,
  .content-grid {
    grid-template-columns: 1fr;
    display: grid;
  }

  .structured-row {
    grid-template-columns: 1fr;
  }

  .structured-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .hero-actions {
    margin-top: 8px;
  }
}
</style>
