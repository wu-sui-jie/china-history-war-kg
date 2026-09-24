<template>
  <lay-row :space="10">
    <lay-col :md="24">
      <lay-card>
        <lay-form style="margin-top: 20px">
          <lay-row>
            <lay-col :md="6">
              <lay-form-item label="战争事件名称：" label-width="120">
                <lay-input v-model="searchName" placeholder="请输入名称搜索" style="width: 90%" />
              </lay-form-item>
            </lay-col>
            <lay-col :md="6">
              <lay-form-item label-width="0">
                <lay-button type="primary" @click="toSearch">
                  <lay-icon type="layui-icon-search" />
                  查询
                </lay-button>
                <lay-button @click="toReset">
                  <lay-icon type="layui-icon-refresh" />
                  重置
                </lay-button>
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
            <lay-tag color="#8B1E23" class="type_tag">战争事件</lay-tag>
          </template>

          <template #toolbar>
            <lay-button size="sm" type="primary" @click="add">
              <lay-icon type="layui-icon-add-1" />
              新增事件
            </lay-button>
          </template>

          <template #operator="{ row }">
            <lay-button size="xs" @click="openEntityDetail(row)">
              详情
            </lay-button>
            <lay-button size="xs" type="primary" @click="viewDetail(row)">
              <lay-icon type="layui-icon-edit" />
              编辑
            </lay-button>
            <lay-button size="xs" type="danger" @click="deleteNode(row)">
              <lay-icon type="layui-icon-delete" />
              删除
            </lay-button>
          </template>
        </lay-table>
      </lay-card>
    </lay-col>
  </lay-row>

  <lay-layer v-model="visible" :title="formData.id ? '编辑战争事件' : '新增战争事件'" :shade="true" :area="['760px', '86vh']">
    <div class="layer-scroll-body">
      <lay-form ref="formRef" :model="formData" :rules="formRules" class="event-form">
      <div class="form-section">
        <div class="section-title">
          <lay-icon type="layui-icon-form" />
          基本信息
        </div>

        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="事件名称" prop="EventName" required>
              <lay-input v-model="formData.EventName" placeholder="请输入事件名称" :maxlength="100" />
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
        <div class="section-title">
          <lay-icon type="layui-icon-time" />
          事件信息
        </div>

        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="事件类型" prop="EventType">
              <lay-input v-model="formData.EventType" placeholder="请输入事件类型" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="发生地点" prop="Place">
              <lay-input v-model="formData.Place" placeholder="请输入发生地点" />
            </lay-form-item>
          </lay-col>
        </lay-row>

        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="开始时间" prop="StartDate">
              <lay-input v-model="formData.StartDate" placeholder="如：公元前1046年" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="结束时间" prop="EndDate">
              <lay-input v-model="formData.EndDate" placeholder="如：公元前1046年" />
            </lay-form-item>
          </lay-col>
        </lay-row>

        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="发起方" prop="Aggressor">
              <lay-input v-model="formData.Aggressor" placeholder="请输入发起方" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="防守方" prop="Defender">
              <lay-input v-model="formData.Defender" placeholder="请输入防守方" />
            </lay-form-item>
          </lay-col>
        </lay-row>

        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="关键人物" prop="KeyPersons">
              <lay-input v-model="formData.KeyPersons" placeholder="请输入关键人物" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="兵力规模" prop="TroopSize">
              <lay-input v-model="formData.TroopSize" placeholder="请输入兵力规模" />
            </lay-form-item>
          </lay-col>
        </lay-row>

        <lay-form-item label="主要行动" prop="Action">
          <lay-input v-model="formData.Action" placeholder="请输入主要行动" />
        </lay-form-item>

        <lay-form-item label="事件结果" prop="Result">
          <lay-input v-model="formData.Result" placeholder="请输入事件结果" />
        </lay-form-item>

        <lay-form-item label="历史影响" prop="Impact">
          <lay-textarea v-model="formData.Impact" placeholder="请输入历史影响" :rows="3" />
        </lay-form-item>

        <lay-form-item label="来源原文" prop="source_text">
          <lay-textarea v-model="formData.source_text" placeholder="请输入来源原文" :rows="3" />
        </lay-form-item>

        <lay-form-item label="备注" prop="Remark">
          <lay-textarea v-model="formData.Remark" placeholder="请输入备注" :rows="2" />
        </lay-form-item>
      </div>

      <lay-form-item class="form-actions">
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
    </div>
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
  nodeType: 'Event',
  displayType: 'Event',
  nameField: 'EventName',
  nameLabel: '事件名称',
  // 字段顺序与原实现一致：提交 payload 与编辑回填都按它走
  emptyForm: () => ({
    id: null,
    type: 'Event',
    EventName: "",
    DynastyName: "",
    EventType: "",
    StartDate: "",
    EndDate: "",
    Place: "",
    Aggressor: "",
    Defender: "",
    KeyPersons: "",
    Action: "",
    Result: "",
    TroopSize: "",
    Impact: "",
    source_text: "",
    Remark: "",
  }),
})

const columns = nodeColumns(
  { title: '战争事件名称', key: 'EventName', minWidth: 220 },
  [
    { title: '所属朝代', key: 'DynastyName', width: 120, align: 'center' },
    { title: '发生地点', key: 'Place', width: 180 },
    { title: '开始时间', key: 'StartDate', width: 120 },
  ],
  200,
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

.layer-scroll-body {
  height: calc(86vh - 54px);
  overflow-y: auto;
}

.event-form {
  width: 100%;
  padding: 20px;
  box-sizing: border-box;
}

.form-actions {
  text-align: right;
  margin-top: 20px;
  margin-bottom: 0;
  padding-top: 16px;
  padding-bottom: 4px;
  border-top: 1px solid #eee;
  background: #fff;
  position: sticky;
  bottom: 0;
  z-index: 2;
}

:deep(.lay-form-item) {
  margin-bottom: 15px;
}

:deep(.lay-input),
:deep(.lay-select),
:deep(.lay-textarea) {
  width: 100%;
}

@media (max-width: 768px) {
  .layer-scroll-body {
    height: calc(86vh - 50px);
  }

  .event-form {
    padding: 16px;
  }
}
</style>
