<!--
  图谱节点详情抽屉（点击问答子图里的节点后弹出）。

  class 名与结构沿用 views/inference/index.vue 的页面级样式，不要改动。
  它必须自成一个组件：lay-layer 会把内容 teleport 到 body，页面里按 `.inference-container`
  命名空间生效的样式够不到它（teleport 之后不在那个子树里），所以抽屉的样式放在
  本组件的 scoped 块里（见文件尾）。
-->
<template>
  <lay-layer :model-value="modelValue" :title="'节点关联信息'" :shade="true" :area="['520px', '88vh']"
             @update:model-value="emit('update:modelValue', $event)">
    <div v-if="node" class="kg-node-detail">
      <div class="kg-node-detail-header">
        <div>
          <div class="kg-node-detail-type">{{ typeLabel(node.type) }}</div>
          <h3>{{ nodeDisplayName(node) }}</h3>
        </div>
        <lay-tag>{{ node.type || '未知类型' }}</lay-tag>
      </div>

      <div v-if="nodeSummaryChips.length" class="kg-node-summary">
        <div v-for="item in nodeSummaryChips" :key="item.label" class="kg-node-chip">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>

      <div v-if="node.source_text" class="kg-node-section emphasis">
        <div class="kg-node-section-title">来源原文</div>
        <div class="kg-node-long-text">{{ node.source_text }}</div>
      </div>

      <div v-if="node.Remark" class="kg-node-section">
        <div class="kg-node-section-title">补充说明</div>
        <div class="kg-node-long-text">{{ node.Remark }}</div>
      </div>

      <div v-if="node.Description" class="kg-node-section">
        <div class="kg-node-section-title">节点说明</div>
        <div class="kg-node-long-text">{{ node.Description }}</div>
      </div>

      <div v-if="node.Result" class="kg-node-section">
        <div class="kg-node-section-title">事件结果</div>
        <div class="kg-node-long-text">{{ node.Result }}</div>
      </div>

      <div v-if="node.Impact" class="kg-node-section">
        <div class="kg-node-section-title">历史影响</div>
        <div class="kg-node-long-text">{{ node.Impact }}</div>
      </div>

      <div v-if="structuredRelationAttributes.length" class="kg-node-section">
        <div class="kg-node-section-header">
          <div class="kg-node-section-title">关联关系</div>
          <span class="kg-node-section-count">{{ structuredRelationAttributes.length }} 组</span>
        </div>
        <div class="kg-structured-relations">
          <div
            v-for="(item, index) in structuredRelationAttributes"
            :key="`${item.relation}-${index}`"
            class="kg-structured-item"
          >
            <div class="kg-structured-head">
              <div class="kg-relation-badge">{{ item.relation }}</div>
              <div class="kg-relation-meta">
                <span>{{ item.targets.length }} 个对象</span>
                <span>{{ item.evidences.length }} 条证据</span>
              </div>
            </div>
            <div class="kg-structured-row">
              <label>关系对象</label>
              <div class="kg-target-list">
                <lay-tag v-for="target in item.targets" :key="target" class="kg-target-tag">{{ target }}</lay-tag>
              </div>
            </div>
            <div class="kg-structured-row" v-if="item.evidences.length">
              <label>证据</label>
              <div class="kg-evidence-list">
                <div v-for="evidence in item.evidences" :key="evidence" class="kg-evidence-card">{{ evidence }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="nodeDetailEntries.length" class="kg-node-section">
        <div class="kg-node-section-title">节点属性</div>
        <div class="kg-node-field-list">
          <div v-for="item in nodeDetailEntries" :key="item.key" class="kg-node-field">
            <label>{{ item.label }}</label>
            <div>{{ item.value }}</div>
          </div>
        </div>
      </div>
    </div>
  </lay-layer>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { fieldLabel, groupRelationAttributes, nodeDisplayName, typeLabel } from '@/utils/knowledge'
import type { KgNodeDetail } from '@/types/inference'

const props = defineProps<{
  modelValue: boolean
  /** 当前选中的节点；为 null 时不渲染内容 */
  node: KgNodeDetail | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

/** 抽屉里单独成段展示的长文本字段（其余字段进"节点属性"列表） */
const nodeLongTextKeys = new Set(['source_text', 'Remark', 'Description', 'Result', 'Impact'])

const nodeSummaryChips = computed(() => {
  const node = props.node || {};
  const chips: { label: string; value: unknown }[] = [];
  if (node.DynastyName) chips.push({ label: '朝代', value: node.DynastyName });
  if (node.StartDate || node.EndDate) chips.push({ label: '时间', value: [node.StartDate, node.EndDate].filter(Boolean).join(' 至 ') });
  if (node.Place || node.modern_name || node.geo_name) chips.push({ label: '地点', value: node.Place || node.modern_name || node.geo_name });
  if (node.EventType || node.OrgType || node.Role) chips.push({ label: '类别', value: node.EventType || node.OrgType || node.Role });
  return chips;
});

const structuredRelationAttributes = computed(() => groupRelationAttributes(props.node?.relations))

const nodeDetailEntries = computed(() => {
  const node = props.node || {};
  const hiddenKeys = new Set([
    'id', 'name', 'type', 'EventName', 'PersonName', 'OrgName', 'geo_name',
    'relations', 'created', 'category', 'label', 'value', 'symbolSize'
  ]);
  return Object.entries(node)
    .filter(([key, value]) => !hiddenKeys.has(key) && !nodeLongTextKeys.has(key) && String(value ?? '').trim())
    .filter(([key]) => fieldLabel(key) !== key)
    .map(([key, value]) => ({ key, label: fieldLabel(key), value: String(value) }));
});
</script>

<style scoped>
/* 规则与 index.css 里的 .kg-node-detail 保持一致，不要各改一份。
   放在这里的原因见文件头——teleport 出来的内容不在页面命名空间的子树里。 */
.kg-node-detail {
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.kg-node-detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.kg-node-detail-header h3 {
  margin: 6px 0 0;
  font-size: 22px;
  color: #1f2937;
}

.kg-node-detail-type {
  font-size: 12px;
  color: #009688;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.kg-node-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.kg-node-chip {
  padding: 12px 14px;
  border-radius: 12px;
  background: linear-gradient(135deg, #f8fafc 0%, #eefaf8 100%);
  border: 1px solid rgba(0, 150, 136, 0.12);
}

.kg-node-chip span {
  display: block;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}

.kg-node-chip strong {
  color: #1f2937;
  line-height: 1.5;
}

.kg-node-section {
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  padding: 14px 16px;
  background: #fff;
}

.kg-node-section.emphasis {
  background: linear-gradient(135deg, #fffaf0 0%, #f4fffd 100%);
  border-color: rgba(0, 150, 136, 0.18);
}

.kg-node-section-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f766e;
  margin-bottom: 10px;
}

.kg-node-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.kg-node-section-count {
  font-size: 13px;
  color: #b7791f;
}

.kg-node-long-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: #374151;
}

.kg-node-field-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg-node-field {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  align-items: start;
}

.kg-node-field label {
  color: #6b7280;
  font-size: 13px;
}

.kg-node-field div {
  color: #1f2937;
  line-height: 1.7;
  white-space: pre-wrap;
}

.kg-structured-relations {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kg-structured-item {
  padding: 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, #fafafa 0%, #f5f8fb 100%);
  border: 1px solid rgba(194, 155, 107, 0.18);
}

.kg-structured-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.kg-relation-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 68px;
  padding: 10px 16px;
  border-radius: 999px;
  background: linear-gradient(135deg, #b86d36 0%, #9f4e1e 100%);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.kg-relation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.kg-relation-meta span {
  padding: 6px 12px;
  border-radius: 999px;
  background: #f5e8cf;
  color: #a16207;
  font-size: 12px;
}

.kg-structured-row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 12px;
  align-items: start;
  margin-top: 10px;
}

.kg-structured-row label {
  color: #7c8798;
  font-size: 13px;
}

.kg-target-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.kg-target-tag {
  margin-right: 0;
  border-radius: 10px;
}

.kg-evidence-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kg-evidence-card {
  padding: 14px 16px;
  border-left: 4px solid #d29a3a;
  border-radius: 12px;
  background: #fff;
  color: #1f2937;
  line-height: 1.8;
  white-space: pre-wrap;
}

@media (max-width: 768px) {
  .kg-node-summary {
    grid-template-columns: 1fr;
  }

  .kg-node-field {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .kg-structured-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .kg-structured-head {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
