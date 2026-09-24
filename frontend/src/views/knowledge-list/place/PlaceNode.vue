<template>
  <lay-row :space="10">
    <lay-col :md="24">
      <lay-card>
        <lay-form style="margin-top: 20px">
          <lay-row>
            <lay-col :md="6">
              <lay-form-item label="地点名称：" label-width="100">
                <lay-input v-model="searchName" placeholder="请输入名称搜索" style="width: 90%" />
              </lay-form-item>
            </lay-col>
            <lay-col :md="6">
              <lay-form-item label-width="0">
                <lay-button type="primary" @click="toSearch"><lay-icon type="layui-icon-search" />查询</lay-button>
                <lay-button @click="toReset"><lay-icon type="layui-icon-refresh" />重置</lay-button>
              </lay-form-item>
            </lay-col>
          </lay-row>
        </lay-form>
      </lay-card>
    </lay-col>

    <lay-col :md="24">
      <lay-card>
        <lay-table :page="page" :columns="columns" :dataSource="dataSource" @change="change">
          <template #index="{ rowIndex }">
            {{ (page.current - 1) * page.limit + rowIndex + 1 }}
          </template>
          <template #type>
            <lay-tag color="#C29B6B" class="type_tag">战争地点</lay-tag>
          </template>
          <template #toolbar>
            <lay-button size="sm" type="primary" @click="add"><lay-icon type="layui-icon-add-1" />新增地点</lay-button>
          </template>
          <template #operator="{ row }">
            <lay-button size="xs" @click="openEntityDetail(row)">详情</lay-button>
            <lay-button size="xs" type="primary" @click="viewDetail(row)"><lay-icon type="layui-icon-edit" />编辑</lay-button>
            <lay-button size="xs" type="danger" @click="deleteNode(row)"><lay-icon type="layui-icon-delete" />删除</lay-button>
          </template>
        </lay-table>
      </lay-card>
    </lay-col>
  </lay-row>

  <lay-layer v-model="visible" :title="formData.id ? '编辑战争地点' : '新增战争地点'" :shade="true" :area="['600px', 'auto']">
    <lay-form ref="formRef" :model="formData" :rules="formRules" style="width: 560px; padding: 20px">
      <div class="form-section">
        <div class="section-title"><lay-icon type="layui-icon-form" />基本信息</div>
        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="地点名称" prop="geo_name" required>
              <lay-input v-model="formData.geo_name" placeholder="请输入地点名称" :maxlength="100" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="所属朝代" prop="DynastyName">
              <lay-input v-model="formData.DynastyName" placeholder="请输入所属朝代" />
            </lay-form-item>
          </lay-col>
        </lay-row>
      </div>

      <div class="form-section">
        <div class="section-title"><lay-icon type="layui-icon-location" />地点信息</div>
        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="现代名称" prop="modern_name">
              <lay-input v-model="formData.modern_name" placeholder="请输入现代名称" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="具体位置" prop="Specific_location">
              <lay-input v-model="formData.Specific_location" placeholder="请输入具体位置" />
            </lay-form-item>
          </lay-col>
        </lay-row>
        <lay-row>
          <lay-col :md="8">
            <lay-form-item label="所属省份" prop="Province">
              <lay-input v-model="formData.Province" placeholder="省/直辖市" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="8">
            <lay-form-item label="所属城市" prop="City">
              <lay-input v-model="formData.City" placeholder="市/地区" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="8">
            <lay-form-item label="所属区县" prop="District_County">
              <lay-input v-model="formData.District_County" placeholder="区/县" />
            </lay-form-item>
          </lay-col>
        </lay-row>
      </div>

      <lay-form-item style="text-align: right; margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee;">
        <lay-button type="primary" style="width: 120px" @click="submit" :loading="isSubmitting">
          <lay-icon type="layui-icon-ok" />
          {{ formData.id ? '保存修改' : '立即创建' }}
        </lay-button>
        <lay-button style="width: 100px; margin-left: 10px" @click="close">
          <lay-icon type="layui-icon-close" />
          取消
        </lay-button>
      </lay-form-item>
    </lay-form>
  </lay-layer>
</template>

<script setup lang="ts">
import { nodeColumns, useNodeCrudPage } from '@/composables/useNodeCrudPage'

// 列表拉取、分页查询、增删改提交与详情跳转都在组合式函数里（四个管理页共用一份），
// 本页只声明：节点类型、字段集合与列配置。
const {
  searchName, page, dataSource, visible, formRef, isSubmitting, formData, formRules,
  add, submit, close, viewDetail, openEntityDetail, deleteNode,
  change, toSearch, toReset,
} = useNodeCrudPage({
  nodeType: 'Place',
  displayType: '战争地点',
  nameField: 'geo_name',
  nameLabel: '地点名称',
  // 字段顺序与原实现一致：提交 payload 与编辑回填都按它走
  emptyForm: () => ({
    id: null,
    type: 'Place',
    geo_name: "",
    DynastyName: "",
    modern_name: "",
    Province: "",
    City: "",
    District_County: "",
    Specific_location: "",
  }),
})

const columns = nodeColumns(
  { title: '战争地点名称', key: 'geo_name', minWidth: 200 },
  [
    { title: '所属朝代', key: 'DynastyName', width: 120, align: 'center' },
    { title: '现代名称', key: 'modern_name', width: 150 },
    { title: '所属省份', key: 'Province', width: 100 },
  ],
  260,
)
</script>

<style scoped>
.type_tag {
  width: 100px;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.form-section {
  margin-bottom: 20px;
  padding: 15px;
  background-color: #f8f9fa;
  border-radius: 6px;
}

.section-title {
  font-weight: bold;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e4e7ed;
  color: #303133;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 5px;
}

:deep(.lay-form-item) {
  margin-bottom: 15px;
}

:deep(.lay-input),
:deep(.lay-select),
:deep(.lay-textarea) {
  width: 100%;
}
</style>
