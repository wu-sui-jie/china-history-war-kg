<template>
  <div class="search-page">
    <div class="page-header">
      <h1>全局搜索</h1>
    </div>

    <div class="search-box">
      <lay-input v-model="keyword" placeholder="输入关键词" @keyup.enter="search" />
      <lay-button type="primary" @click="search">搜索</lay-button>
    </div>

    <div class="result-list">
      <div v-for="item in results" :key="`${item.type}-${item.id}`" class="result-item">
        <div>
          <strong>{{ item.name }}</strong>
          <span>{{ item.type_label }} · {{ item.subtitle || '无补充信息' }}</span>
        </div>
        <div class="actions">
          <lay-button size="xs" @click="router.push(item.entity_route)">详情</lay-button>
          <lay-button size="xs" @click="router.push(item.graph_route)">看图谱</lay-button>
          <lay-button size="xs" @click="router.push(item.timeline_route)">看时间轴</lay-button>
        </div>
      </div>
      <div v-if="searched && !results.length" class="empty-state">没有找到匹配实体</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'
import { globalSearch } from '../../api/module/graph'
import { apiErrorMessage } from '../../utils/apiError'

const router = useRouter()
const route = useRoute()
const keyword = ref('')
const searched = ref(false)
const results = ref<any[]>([])

const search = async () => {
  if (!keyword.value.trim()) {
    layer.msg('请输入关键词', { icon: 2 })
    return
  }
  // 后端失败是 HTTP 5xx：失败时既要把结果清空，也要给出提示，
  // 否则"搜索了但没反应"和"确实没有匹配"在界面上长得一模一样。
  try {
    const res = await globalSearch(keyword.value.trim())
    searched.value = true
    results.value = res.code === 200 ? res.data || [] : []
  } catch (error) {
    console.error('全局搜索失败:', error)
    searched.value = true
    results.value = []
    layer.msg(apiErrorMessage(error, '搜索失败，请稍后重试'), { icon: 2 })
  }
}

onMounted(() => {
  if (route.query.keyword) {
    keyword.value = String(route.query.keyword)
    search()
  }
})
</script>

<style scoped>
.search-page {
  padding: 20px;
  min-height: 100%;
  background: linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header,
.search-box,
.result-item {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 10px 28px rgba(74, 54, 24, 0.08);
}

.page-header {
  padding: 22px;
  margin-bottom: 16px;
}

.page-header h1 {
  margin: 0 0 8px;
}

.page-header p {
  margin: 0;
  color: #6b7280;
}

.search-box {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  padding: 16px;
  margin-bottom: 16px;
}

.result-list {
  display: grid;
  gap: 12px;
}

.result-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 14px;
}

.result-item strong,
.result-item span {
  display: block;
}

.result-item span {
  color: #6b7280;
  margin-top: 6px;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.empty-state {
  color: #9ca3af;
  text-align: center;
  padding: 18px;
}
</style>
