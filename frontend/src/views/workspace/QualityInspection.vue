<template>
  <div class="workspace-page">
    <div class="page-header">
      <div>
        <h1>数据修复工作台</h1>
      </div>
      <div class="header-actions">
        <lay-button @click="router.push('/workspace/dashboard')">回到仪表盘</lay-button>
        <lay-button type="primary" @click="loadData">刷新工作台</lay-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span>重复节点</span>
        <strong>{{ workbench.summary?.duplicate_names || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>孤立节点</span>
        <strong>{{ workbench.summary?.isolated_nodes || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>缺失字段</span>
        <strong>{{ workbench.summary?.missing_required_fields || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>时间异常</span>
        <strong>{{ workbench.summary?.timeline_issues || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>坐标缺失</span>
        <strong>{{ workbench.summary?.coordinate_missing || 0 }}</strong>
      </div>
      <div class="summary-card">
        <span>坐标低置信</span>
        <strong>{{ workbench.summary?.coordinate_low_confidence || 0 }}</strong>
      </div>
    </div>

    <div class="main-grid">
      <div class="panel queue-panel" :style="queuePanelStyle">
        <div class="panel-header">
          <h3>修复队列</h3>
          <span>{{ filteredIssues.length }} 条</span>
        </div>
        <div class="filter-tabs">
          <button v-for="item in issueTabs" :key="item.value" :class="{ active: filters.issueType === item.value }" @click="filters.issueType = item.value">
            {{ item.label }}
          </button>
        </div>
        <div class="issue-list">
          <div
            v-for="item in filteredIssues"
            :key="item.issue_key"
            class="issue-item"
            :class="{ active: selectedIssue?.issue_key === item.issue_key }"
            @click="selectIssue(item)"
          >
            <div class="issue-top">
              <strong>{{ item.title }}</strong>
              <span :class="`severity-${item.severity}`">{{ severityLabel(item.severity) }}</span>
            </div>
            <p>{{ item.description }}</p>
            <div class="issue-meta">{{ typeLabel(item.node_type) }} · {{ item.node_name || '待人工判断' }}</div>
          </div>
          <div v-if="!filteredIssues.length" class="empty-state">当前筛选下没有问题项</div>
        </div>
      </div>

      <div ref="editorPanelRef" class="panel editor-panel">
        <div class="panel-header">
          <h3>{{ selectedIssue?.title || '请选择一个问题项' }}</h3>
          <div class="editor-actions">
            <lay-button size="sm" :disabled="!selectedNodeId" @click="goEntityDetail">实体详情</lay-button>
            <lay-button size="sm" :disabled="selectedNodeType !== 'Place'" @click="goMapFocus">查看地图定位</lay-button>
          </div>
        </div>

        <div v-if="selectedIssue && !selectedIssue.editable" class="readonly-box">
          <p>该问题需要人工判断是否合并或重命名，当前工作台不自动执行高风险合并。</p>
        </div>

        <div v-else-if="selectedNode">
          <div class="field-grid">
            <div v-for="[key, value] in editableFields" :key="key" class="field-item">
              <label>{{ fieldLabel(key) }}</label>
              <div v-if="isReadonlyDisplayField(key)" class="readonly-value">
                {{ formatDisplayValue(key, value) }}
              </div>
              <lay-textarea
                v-else-if="isLongField(key)"
                v-model="formState[key]"
                :rows="3"
              />
              <lay-select v-else-if="key === 'coord_source'" v-model="formState[key]">
                <lay-select-option value="">未标注</lay-select-option>
                <lay-select-option value="manual">人工确认</lay-select-option>
                <lay-select-option value="literature">文献考证</lay-select-option>
                <lay-select-option value="map_tool">地图工具</lay-select-option>
                <lay-select-option value="city_centroid">城市中心点</lay-select-option>
                <lay-select-option value="province_centroid">省级中心点</lay-select-option>
                <lay-select-option value="historical_region">历史区域估算</lay-select-option>
              </lay-select>
              <lay-select v-else-if="key === 'coord_confidence'" v-model="formState[key]">
                <lay-select-option value="">未标注</lay-select-option>
                <lay-select-option value="high">高</lay-select-option>
                <lay-select-option value="medium">中</lay-select-option>
                <lay-select-option value="low">低</lay-select-option>
              </lay-select>
              <lay-input v-else v-model="formState[key]" />
            </div>
          </div>
          <div class="save-bar">
            <lay-button type="primary" :loading="saving" @click="saveChanges">保存修复</lay-button>
          </div>
        </div>

        <div v-else class="empty-state">选择左侧问题项后，在这里直接编辑实体字段。</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { updateNodeProperties } from '../../api/module/node'
import { getEntityDetail, getQualityWorkbench } from '../../api/module/workspace'
import { fieldLabel, fieldValueLabel, toEditableFields, typeLabel } from '../../utils/knowledge'

const router = useRouter()
const route = useRoute()

const workbench = ref<any>({
  summary: {},
  issue_queue: [],
})
const selectedIssue = ref<any>(null)
const selectedNode = ref<Record<string, any> | null>(null)
const formState = reactive<Record<string, any>>({})
const saving = ref(false)
const editorPanelRef = ref<HTMLElement | null>(null)
const queuePanelHeight = ref<number | null>(null)
let editorPanelObserver: ResizeObserver | null = null

const filters = reactive({
  issueType: 'all',
})

const issueTabs = [
  { label: '全部', value: 'all' },
  { label: '缺失字段', value: 'missing_fields' },
  { label: '时间异常', value: 'timeline_issue' },
  { label: '坐标缺失', value: 'coordinate_missing' },
  { label: '坐标低置信', value: 'coordinate_low_confidence' },
  { label: '孤立节点', value: 'isolated_node' },
  { label: '重复节点', value: 'duplicate_name' },
]

const filteredIssues = computed(() => {
  const items = workbench.value.issue_queue || []
  if (filters.issueType === 'all') return items
  return items.filter((item: any) => item.issue_type === filters.issueType)
})

const editableFields = computed(() => {
  const fields = toEditableFields(selectedNode.value || {})
  const priority = selectedIssue.value?.fields || []
  if (!priority.length) return fields
  const order = new Map(priority.map((key: string, index: number) => [key, index]))
  return [...fields].sort(([left], [right]) => {
    const leftOrder = order.has(left) ? Number(order.get(left)) : 999
    const rightOrder = order.has(right) ? Number(order.get(right)) : 999
    if (leftOrder !== rightOrder) return leftOrder - rightOrder
    return left.localeCompare(right, 'zh-Hans-CN')
  })
})
const selectedNodeId = computed(() => selectedIssue.value?.node_id)
const selectedNodeType = computed(() => selectedIssue.value?.node_type)
const selectedNodeName = computed(() => selectedIssue.value?.node_name)
const readonlyDisplayKeys = new Set(['relations', 'quality_flags', 'type_label'])
const queuePanelStyle = computed(() => {
  if (!queuePanelHeight.value) return {}
  return { height: `${queuePanelHeight.value}px` }
})

const shouldSyncPanelHeight = () => window.innerWidth > 1200

const syncQueuePanelHeight = () => {
  if (!shouldSyncPanelHeight()) {
    queuePanelHeight.value = null
    return
  }

  queuePanelHeight.value = editorPanelRef.value?.offsetHeight || null
}

const scheduleQueuePanelHeightSync = async () => {
  await nextTick()
  syncQueuePanelHeight()
}

const bindEditorPanelObserver = async () => {
  await nextTick()
  if (!editorPanelRef.value || typeof ResizeObserver === 'undefined') {
    syncQueuePanelHeight()
    return
  }

  editorPanelObserver?.disconnect()
  editorPanelObserver = new ResizeObserver(() => {
    syncQueuePanelHeight()
  })
  editorPanelObserver.observe(editorPanelRef.value)
  syncQueuePanelHeight()
}

const loadData = async (selection?: { issueType?: string; previousKey?: string; previousIndex?: number }) => {
  const res = await getQualityWorkbench()
  if (res.code === 200) {
    workbench.value = res.data || {}
    await initSelection(selection)
    await scheduleQueuePanelHeightSync()
    return workbench.value
  }
  layer.msg(res.msg || '加载工作台失败', { icon: 2 })
  return null
}

const initSelection = async (selection?: { issueType?: string; previousKey?: string; previousIndex?: number }) => {
  const routeId = route.query.id ? Number(route.query.id) : null
  const routeType = String(route.query.type || '')
  const items = workbench.value.issue_queue || []
  if (selection?.previousKey) {
    const scopedItems = selection.issueType && selection.issueType !== 'all'
      ? items.filter((item: any) => item.issue_type === selection.issueType)
      : items
    const sameIssue = scopedItems.find((item: any) => item.issue_key === selection.previousKey)
    const nextIssue = scopedItems[Math.min(Math.max(selection.previousIndex ?? 0, 0), Math.max(scopedItems.length - 1, 0))]
    const target = sameIssue || nextIssue || items[0]
    if (target) {
      await selectIssue(target)
      return
    }
    selectedIssue.value = null
    selectedNode.value = null
    return
  }
  const matched = items.find((item: any) => item.node_id === routeId && item.node_type === routeType)
  const firstItem = matched || items[0]
  if (firstItem) {
    await selectIssue(firstItem)
  }
}

const selectIssue = async (item: any) => {
  selectedIssue.value = item
  selectedNode.value = null
  Object.keys(formState).forEach((key) => delete formState[key])

  if (!item.node_id || !item.node_type) {
    return
  }

  const res = await getEntityDetail({ id: item.node_id, type: item.node_type })
  if (res.code !== 200) {
    layer.msg(res.msg || '加载实体失败', { icon: 2 })
    return
  }

  selectedNode.value = res.data?.node || {}
  Object.assign(formState, selectedNode.value)
  await scheduleQueuePanelHeightSync()
}

const saveChanges = async () => {
  if (!selectedNodeId.value || !selectedNodeType.value) return

  const previousKey = selectedIssue.value?.issue_key
  const previousIndex = filteredIssues.value.findIndex((item: any) => item.issue_key === previousKey)
  const previousIssueType = filters.issueType
  const wasCoordinateIssue = ['coordinate_missing', 'coordinate_low_confidence'].includes(selectedIssue.value?.issue_type)
  saving.value = true
  try {
    const res = await updateNodeProperties({
      id: selectedNodeId.value,
      type: selectedNodeType.value,
      properties: editableProperties(),
    })
    if (res.code === 200) {
      const nextWorkbench = await loadData({ issueType: previousIssueType, previousKey, previousIndex })
      const stillExists = (nextWorkbench?.issue_queue || []).some((item: any) => item.issue_key === previousKey)
      layer.msg(wasCoordinateIssue && !stillExists ? '坐标修复已保存，该地点已可在地图中按新坐标显示' : '修复已保存，已切换到下一条问题', { icon: 1 })
      return
    }
    layer.msg(res.msg || '保存失败', { icon: 2 })
  } catch (error) {
    console.error(error)
    layer.msg('保存失败', { icon: 2 })
  } finally {
    saving.value = false
  }
}

const severityLabel = (severity: string) => {
  const labels: Record<string, string> = {
    high: '高优先级',
    medium: '中优先级',
    low: '低优先级',
  }
  return labels[severity] || severity
}

const isLongField = (key: string) => ['Impact', 'Remark', 'Result', 'Description', 'source_text', 'coord_note'].includes(key)

const isReadonlyDisplayField = (key: string) => readonlyDisplayKeys.has(key)

const editableProperties = () => {
  const properties: Record<string, any> = {}
  Object.entries(formState).forEach(([key, value]) => {
    if (!readonlyDisplayKeys.has(key)) {
      properties[key] = value
    }
  })
  return properties
}

const formatDisplayValue = (key: string, value: any) => {
  if (key === 'relations') return formatRelations(value)
  if (key === 'quality_flags') return formatQualityFlags(value)
  if (key === 'type_label') return String(value || '-')
  return formatPlainValue(fieldValueLabel(key, value))
}

const formatRelations = (relations: any) => {
  if (!Array.isArray(relations) || relations.length === 0) return '暂无关联关系'
  return relations
    .map((item: any) => {
      const source = item.source_name || item.source || selectedNodeName.value || '当前实体'
      const relation = item.relation_type || item.type || '关联'
      const target = item.target_name || item.to || item.target || '未知实体'
      const targetType = item.target_type_label || typeLabel(item.target_type)
      const evidence = item.evidence ? `，证据：${item.evidence}` : ''
      return `${source} ${relation} ${target}（${targetType}）${evidence}`
    })
    .join('\n')
}

const formatQualityFlags = (flags: any) => {
  if (!flags || typeof flags !== 'object') return '暂无质检标记'
  const parts: string[] = []
  if (Array.isArray(flags.missing_fields) && flags.missing_fields.length) {
    parts.push(`缺失字段：${flags.missing_fields.map((field: string) => fieldLabel(field)).join('、')}`)
  }
  if (Array.isArray(flags.timeline_problems) && flags.timeline_problems.length) {
    parts.push(`时间问题：${flags.timeline_problems.join('、')}`)
  }
  if (flags.coordinate_quality) {
    const coord = flags.coordinate_quality
    parts.push(`地图展示：${coord.is_mappable ? '可显示' : '不可显示'}`)
    parts.push(`坐标来源：${coord.source_label || '未解析'}`)
    if (coord.confidence) parts.push(`坐标置信度：${fieldValueLabel('coord_confidence', coord.confidence)}`)
    if (coord.note) parts.push(`坐标说明：${coord.note}`)
  }
  if (flags.is_isolated) parts.push('孤立节点：是')
  return parts.length ? parts.join('\n') : '暂无质检标记'
}

const formatPlainValue = (value: any): string => {
  if (value === null || value === undefined || value === '') return '-'
  if (Array.isArray(value)) return value.map(formatPlainValue).join('、')
  if (typeof value === 'object') return Object.values(value).map(formatPlainValue).join('、')
  return String(value)
}

const goEntityDetail = () => {
  router.push(`/knowledge/entity-detail?type=${selectedNodeType.value}&id=${selectedNodeId.value}`)
}

const goMapFocus = () => {
  if (selectedNodeType.value !== 'Place') return
  router.push(`/knowledge/map?place=${encodeURIComponent(selectedNodeName.value || '')}`)
}

watch(
  () => route.query,
  () => {
    if (workbench.value.issue_queue?.length) {
      initSelection()
    }
  },
)

onMounted(async () => {
  window.addEventListener('resize', syncQueuePanelHeight)
  await loadData()
  await bindEditorPanelObserver()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncQueuePanelHeight)
  editorPanelObserver?.disconnect()
})
</script>

<style scoped>
.workspace-page {
  padding: 20px;
  min-height: 100%;
  background:
    radial-gradient(circle at top right, rgba(139, 30, 35, 0.08), transparent 24%),
    linear-gradient(180deg, #f8f3ea 0%, #eef4fb 100%);
}

.page-header,
.summary-card,
.panel {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(74, 54, 24, 0.08);
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 24px;
  margin-bottom: 16px;
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

.header-actions,
.editor-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
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
  color: #111827;
  font-size: 30px;
}

.main-grid {
  display: grid;
  grid-template-columns: 420px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.panel {
  padding: 18px;
}

.queue-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.editor-panel {
  align-self: start;
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

.filter-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.filter-tabs button {
  border: 0;
  border-radius: 999px;
  padding: 8px 14px;
  background: #f1f5f9;
  color: #475569;
  cursor: pointer;
}

.filter-tabs button.active {
  background: linear-gradient(135deg, #8b1e23, #c59b58);
  color: #fff;
}

.issue-list,
.field-grid {
  display: grid;
  gap: 12px;
}

.issue-list {
  flex: 1;
  overflow-y: auto;
  padding-right: 6px;
}

.issue-item,
.field-item,
.readonly-box {
  padding: 14px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid transparent;
}

.issue-item {
  cursor: pointer;
}

.issue-item.active {
  border-color: #c59b58;
  background: linear-gradient(135deg, #fffaf2, #f8fbff);
}

.issue-top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.issue-item strong {
  color: #111827;
}

.issue-item p {
  margin: 0 0 8px;
  color: #6b7280;
}

.issue-meta {
  color: #8c6d3b;
  font-size: 12px;
}

.severity-high {
  color: #b91c1c;
}

.severity-medium {
  color: #c2410c;
}

.severity-low {
  color: #15803d;
}

.field-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.field-item label {
  display: block;
  margin-bottom: 8px;
  color: #8c6d3b;
  font-size: 12px;
}

.readonly-value {
  min-height: 38px;
  padding: 10px 12px;
  border: 1px solid #eef2f7;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.save-bar {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.empty-state {
  color: #9ca3af;
  padding: 16px 0;
}

.issue-list::-webkit-scrollbar {
  width: 8px;
}

.issue-list::-webkit-scrollbar-thumb {
  border-radius: 10px;
  background-color: rgba(191, 160, 106, 0.45);
}

@media (max-width: 1200px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .main-grid {
    grid-template-columns: 1fr;
  }

  .queue-panel {
    height: auto !important;
  }

  .issue-list {
    overflow: visible;
    padding-right: 0;
  }
}

@media (max-width: 768px) {
  .workspace-page {
    padding: 14px;
  }

  .page-header,
  .summary-grid,
  .field-grid {
    grid-template-columns: 1fr;
    display: grid;
  }
}
</style>
