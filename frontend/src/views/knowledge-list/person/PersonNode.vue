<template>
  <lay-row :space="10">
    <lay-col :md="24">
      <lay-card>
        <lay-form style="margin-top: 20px">
          <lay-row>
            <lay-col :md="6">
              <lay-form-item label="人物名称：" label-width="100">
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
            <lay-tag color="#3A5FCD" class="type_tag">历史人物</lay-tag>
          </template>
          <template #toolbar>
            <lay-button size="sm" type="primary" @click="add"><lay-icon type="layui-icon-add-1" />新增人物</lay-button>
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

  <lay-layer v-model="visible" :title="formData.id ? '编辑历史人物' : '新增历史人物'" shade="true" :area="['600px', 'auto']">
    <lay-form ref="formRef" :model="formData" :rules="formRules" style="width: 560px; padding: 20px">
      <div class="form-section">
        <div class="section-title"><lay-icon type="layui-icon-form" />基本信息</div>
        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="人物名称" prop="PersonName" required>
              <lay-input v-model="formData.PersonName" placeholder="请输入人物名称" :maxlength="100" />
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
        <div class="section-title"><lay-icon type="layui-icon-username" />人物信息</div>
        <lay-row>
          <lay-col :md="12">
            <lay-form-item label="所属组织" prop="OrgName">
              <lay-input v-model="formData.OrgName" placeholder="请输入所属组织" />
            </lay-form-item>
          </lay-col>
          <lay-col :md="12">
            <lay-form-item label="担任角色" prop="Role">
              <lay-input v-model="formData.Role" placeholder="请输入担任角色" />
            </lay-form-item>
          </lay-col>
        </lay-row>
        <lay-form-item label="备注" prop="Remark">
          <lay-textarea v-model="formData.Remark" placeholder="请输入人物备注" :rows="4" />
        </lay-form-item>
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

<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import Http from "@/api/http";
import { layer } from "@layui/layui-vue";

const router = useRouter();
const searchName = ref("");
const page = ref({ total: 0, limit: 10, current: 1 });
const dataSource = ref([]);
const visible = ref(false);
const formRef = ref(null);
const isSubmitting = ref(false);

const columns = ref([
  { title: "序号", width: 80, align: "center", customSlot: "index", key: "index" },
  { title: "历史人物名称", key: "PersonName", minWidth: 200 },
  { title: "类型", width: 120, customSlot: "type", key: "type", align: "center" },
  { title: "所属朝代", key: "DynastyName", width: 120, align: "center" },
  { title: "所属组织", key: "OrgName", width: 150 },
  { title: "担任角色", key: "Role", width: 120 },
  { title: "操作", width: 260, customSlot: "operator", key: "operator", align: "center" },
]);

const emptyForm = () => ({
  id: null,
  type: "Person",
  PersonName: "",
  DynastyName: "",
  OrgName: "",
  Role: "",
  Remark: "",
});

const formData = ref(emptyForm());

const formRules = {
  PersonName: {
    required: true,
    message: "请输入人物名称",
    min: 1,
    max: 100,
  },
};

function add() {
  resetForm();
  visible.value = true;
}

function resetForm() {
  formData.value = emptyForm();
  formRef.value?.clearValidate();
}

function submit() {
  formRef.value.validate((isValidate) => {
    if (!isValidate) {
      layer.msg("请填写必填项", { icon: 2 });
      return;
    }
    isSubmitting.value = true;
    const submitData = {
      type: "Person",
      PersonName: formData.value.PersonName,
      DynastyName: formData.value.DynastyName || null,
      OrgName: formData.value.OrgName || null,
      Role: formData.value.Role || null,
      Remark: formData.value.Remark || null,
    };
    if (formData.value.id) submitData.id = formData.value.id;
    const url = formData.value.id ? "/update_node" : "/create_node";
    Http.post(url, submitData)
      .then((res) => {
        isSubmitting.value = false;
        if (res.code === 200) {
          layer.msg(formData.value.id ? "修改成功" : "创建成功", { icon: 1 });
          close();
          query();
        } else {
          layer.msg(res.msg || "操作失败", { icon: 2 });
        }
      })
      .catch(() => {
        isSubmitting.value = false;
        layer.msg("网络错误，请重试", { icon: 2 });
      });
  });
}

function close() {
  visible.value = false;
  resetForm();
}

function viewDetail(row) {
  formData.value = {
    id: row.id,
    type: "Person",
    PersonName: row.PersonName || "",
    DynastyName: row.DynastyName || "",
    OrgName: row.OrgName || "",
    Role: row.Role || "",
    Remark: row.Remark || "",
  };
  visible.value = true;
}

function openEntityDetail(row) {
  router.push(`/knowledge/entity-detail?type=Person&id=${row.id}&back=${encodeURIComponent('/knowledge-list/person')}`);
}

function deleteNode(row) {
  layer.confirm(`确定要删除“${row.PersonName}”吗？`, {
    icon: 3,
    title: "确认删除",
    yes(index) {
      layer.close(index);
      Http.post("/delete_node", { type: "Person", id: row.id }).then((res) => {
        if (res.code === 200) {
          layer.msg("删除成功", { icon: 1 });
          query();
        } else {
          layer.msg(res.msg || "删除失败", { icon: 2 });
        }
      });
    },
    btn2(index) {
      layer.close(index);
    },
  });
}

function change({ current, limit }) {
  page.value.current = current;
  page.value.limit = limit;
  query();
}

function toSearch() {
  page.value.current = 1;
  query();
}

function toReset() {
  searchName.value = "";
  page.value.current = 1;
  query();
}

function query() {
  Http.post("/api/find_node_page", {
    pageNum: page.value.current,
    pageSize: page.value.limit,
    name: searchName.value,
    node_type: "Person",
  }).then((res) => {
    if (res.code === 200) {
      dataSource.value = (res.data.records || []).map((item) => ({ ...item, type: "历史人物" }));
      page.value.total = res.data.total || 0;
    } else {
      layer.msg(res.msg || "查询失败", { icon: 2 });
    }
  });
}

onMounted(() => query());
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
