<template>
  <div class="versions-page">
    <div class="page-header">
      <div>
        <h1>数据版本管理</h1>
      </div>
      <lay-button type="primary" @click="loadData">刷新</lay-button>
    </div>

    <div class="version-list">
      <div v-for="item in versions" :key="item.id" class="version-card">
        <div>
          <strong>{{ item.version }}</strong>
          <span>{{ item.status }} · {{ item.extracted_at || '未记录时间' }}</span>
          <p>{{ item.source_path || '暂无来源路径' }}</p>
        </div>
        <div class="metrics">
          <div>
            <span>实体增量</span>
            <strong>{{ item.entity_delta || 0 }}</strong>
          </div>
          <div>
            <span>关系增量</span>
            <strong>{{ item.relation_delta || 0 }}</strong>
          </div>
        </div>
      </div>
      <div v-if="!versions.length" class="empty-state">暂无数据版本记录</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getDatasetVersions } from '../../api/module/workspace'

const versions = ref<any[]>([])

const loadData = async () => {
  const res = await getDatasetVersions()
  versions.value = res.code === 200 ? res.data || [] : []
}

onMounted(loadData)
</script>

<style scoped>
.versions-page {
  padding: 20px;
  min-height: 100%;
  background: linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header,
.version-card {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 28px rgba(74, 54, 24, 0.08);
}

.page-header {
  display: flex;
  justify-content: space-between;
  padding: 22px;
  margin-bottom: 16px;
}

.page-header h1 {
  margin: 0 0 8px;
}

.page-header p,
.version-card span,
.version-card p {
  color: #6b7280;
}

.version-list {
  display: grid;
  gap: 12px;
}

.version-card {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
}

.version-card strong {
  color: #111827;
  font-size: 18px;
}

.metrics {
  display: flex;
  gap: 12px;
}

.metrics div {
  min-width: 120px;
  background: #f8fafc;
  border-radius: 12px;
  padding: 12px;
}

.metrics span,
.metrics strong {
  display: block;
}

.empty-state {
  color: #9ca3af;
}
</style>
