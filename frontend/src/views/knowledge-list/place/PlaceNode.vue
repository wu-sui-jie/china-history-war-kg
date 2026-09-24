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
  { title: "战争地点名称", key: "geo_name", minWidth: 200 },
  { title: "类型", width: 120, customSlot: "type", key: "type", align: "center" },
  { title: "所属朝代", key: "DynastyName", width: 120, align: "center" },
  { title: "现代名称", key: "modern_name", width: 150 },
  { title: "所属省份", key: "Province", width: 100 },
  { title: "操作", width: 260, customSlot: "operator", key: "operator", align: "center" },
]);

const emptyForm = () => ({
  id: null,
  type: "Place",
  geo_name: "",
  DynastyName: "",
  modern_name: "",
  Province: "",
  City: "",
  District_County: "",
  Specific_location: "",
});

const formData = ref(emptyForm());

const formRules = {
  geo_name: {
    required: true,
    message: "请输入地点名称",
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
      type: "Place",
      geo_name: formData.value.geo_name,
      DynastyName: formData.value.DynastyName || null,
      modern_name: formData.value.modern_name || null,
      Province: formData.value.Province || null,
      City: formData.value.City || null,
      District_County: formData.value.District_County || null,
      Specific_location: formData.value.Specific_location || null,
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
    type: "Place",
    geo_name: row.geo_name || "",
    DynastyName: row.DynastyName || "",
    modern_name: row.modern_name || "",
    Province: row.Province || "",
    City: row.City || "",
    District_County: row.District_County || "",
    Specific_location: row.Specific_location || "",
  };
  visible.value = true;
}

function openEntityDetail(row) {
  router.push(`/knowledge/entity-detail?type=Place&id=${row.id}&back=${encodeURIComponent('/knowledge-list/place')}`);
}

function deleteNode(row) {
  layer.confirm(`确定要删除“${row.geo_name}”吗？`, {
    icon: 3,
    title: "确认删除",
    yes(index) {
      layer.close(index);
      Http.post("/delete_node", { type: "Place", id: row.id }).then((res) => {
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
    node_type: "Place",
  }).then((res) => {
    if (res.code === 200) {
      dataSource.value = (res.data.records || []).map((item) => ({ ...item, type: "战争地点" }));
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
