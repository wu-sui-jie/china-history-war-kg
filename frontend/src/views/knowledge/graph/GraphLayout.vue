<template>
  <div class="graph-layout">
    <div class="dataset-bar">
      <div class="dataset-chip">
        <span class="label">当前页面实体</span>
        <span class="value">{{ pageSummary.entities }}</span>
      </div>
      <div class="dataset-chip">
        <span class="label">当前页面关系</span>
        <span class="value">{{ pageSummary.relations }}</span>
      </div>
      <div class="dataset-search">
        <lay-input
          v-model="globalKeyword"
          placeholder="全局搜索事件、人物、地点、组织"
          @keyup.enter="goGlobalSearch"
        />
        <lay-button type="primary" @click="goGlobalSearch">搜索</lay-button>
      </div>
    </div>
    <router-view />
  </div>
</template>

<script setup lang="ts">
import { provide, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const globalKeyword = ref('')

const pageSummary = reactive({
  entities: 0,
  relations: 0
})

function setGraphPageSummary(entities: number, relations: number) {
  pageSummary.entities = entities
  pageSummary.relations = relations
}

function resetGraphPageSummary() {
  pageSummary.entities = 0
  pageSummary.relations = 0
}

provide('graphPageSummary', {
  setGraphPageSummary,
  resetGraphPageSummary
})

function goGlobalSearch() {
  const keyword = globalKeyword.value.trim()
  router.push(keyword ? `/knowledge/search?keyword=${encodeURIComponent(keyword)}` : '/knowledge/search')
}
</script>

<style scoped>
.graph-layout {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.dataset-bar {
  display: grid;
  grid-template-columns: minmax(190px, 0.8fr) minmax(190px, 0.8fr) minmax(360px, 1.6fr);
  gap: 10px;
  padding: 12px 14px 0 14px;
  flex-shrink: 0;
}

.dataset-chip {
  background: linear-gradient(135deg, #fff8ef, #fff);
  border: 1px solid #ead9bf;
  border-radius: 12px;
  padding: 12px 16px;
  box-shadow: 0 3px 10px rgba(105, 72, 33, 0.06);
}

.label {
  display: block;
  font-size: 12px;
  color: #8f6a3d;
  margin-bottom: 4px;
}

.value {
  display: block;
  font-size: 15px;
  font-weight: 600;
  color: #3e2b18;
  word-break: break-word;
}

.dataset-search {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid #ead9bf;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 3px 10px rgba(105, 72, 33, 0.06);
}

@media (max-width: 1200px) {
  .dataset-bar {
    grid-template-columns: 1fr 1fr;
  }

  .dataset-search {
    grid-column: 1 / -1;
  }
}

.graph-layout :deep(.graph-page),
.graph-layout :deep(.graph-subpage) {
  flex: 1;
  min-height: 0;
}

.graph-layout :deep(.graph-toolbar),
.graph-layout :deep(.graph-hint) {
  display: none !important;
}
</style>
