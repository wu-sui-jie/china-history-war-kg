<!--
  节点管理页面布局组件
  
  功能: 提供节点管理页面的标签导航
    - 战争事件: /knowledge-list/event
    - 参战组织: /knowledge-list/organization
    - 历史人物: /knowledge-list/person
    - 战争地点: /knowledge-list/place
-->
<template>
  <lay-container fluid="true" style="padding: 10px; height: 100%;">
    <!-- 子页面导航标签 -->
    <lay-card style="margin-bottom: 10px;">
      <div class="node-nav-row">
        <div class="node-type-tabs">
          <div 
            v-for="tab in tabs" 
            :key="tab.path"
            class="tab-item"
            :class="{ active: currentPath === tab.path }"
            @click="navigateTo(tab.path)"
          >
            <lay-icon :type="tab.icon" :style="{ color: tab.color }"></lay-icon>
            <span class="tab-label">{{ tab.label }}</span>
          </div>
        </div>
        <div class="node-global-search">
          <lay-input
            v-model="globalKeyword"
            placeholder="全局搜索事件、人物、地点、组织"
            @keyup.enter="goGlobalSearch"
          />
          <lay-button type="primary" @click="goGlobalSearch">搜索</lay-button>
        </div>
      </div>
    </lay-card>
    
    <!-- 子页面内容 -->
    <router-view />
  </lay-container>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const currentPath = computed(() => route.path)
const globalKeyword = ref('')

const tabs = [
  { 
    path: '/knowledge-list/event', 
    label: '战争事件', 
    icon: 'layui-icon-fire',
    color: '#8B1E23'
  },
  { 
    path: '/knowledge-list/organization', 
    label: '参战组织', 
    icon: 'layui-icon-group',
    color: '#6A4C93'
  },
  { 
    path: '/knowledge-list/person', 
    label: '历史人物', 
    icon: 'layui-icon-username',
    color: '#3A5FCD'
  },
  { 
    path: '/knowledge-list/place', 
    label: '战争地点', 
    icon: 'layui-icon-location',
    color: '#C29B6B'
  }
]

const navigateTo = (path: string) => {
  router.push(path)
}

const goGlobalSearch = () => {
  const keyword = globalKeyword.value.trim()
  router.push(keyword ? `/knowledge/search?keyword=${encodeURIComponent(keyword)}` : '/knowledge/search')
}
</script>

<style scoped>
.node-nav-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.node-type-tabs {
  display: flex;
  gap: 10px;
  padding: 5px;
  flex-wrap: wrap;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s;
  background-color: #f5f7fa;
  border: 1px solid transparent;
}

.tab-item:hover {
  background-color: #e4e7ed;
}

.tab-item.active {
  background-color: #fff;
  border-color: var(--global-primary-color, #409eff);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.tab-label {
  font-size: 14px;
  font-weight: 500;
}

.node-global-search {
  display: grid;
  grid-template-columns: minmax(220px, 360px) auto;
  gap: 10px;
  align-items: center;
  flex-shrink: 0;
}

@media (max-width: 1100px) {
  .node-nav-row {
    align-items: stretch;
    flex-direction: column;
  }

  .node-global-search {
    grid-template-columns: minmax(0, 1fr) auto;
  }
}
</style>
