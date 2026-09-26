<template>
  <div class="admin-users-page">
    <div class="page-header">
      <div>
        <h1>用户管理</h1>
        <p>
          注册账号一律是「只读」。需要写数据或数据运营入口时，在这里把角色提升为编辑；
          需要用户管理入口时提升为管理员。
          <strong>角色实时生效，但菜单是登录时下发的——被改的人需要重新登录才能看到新菜单。</strong>
          「停用」会立刻失效该账号在其它设备上的登录凭证（RAG 侧最迟在撤销查询的 TTL 内失效）。
        </p>
      </div>
      <lay-button type="primary" :loading="loading" @click="loadData">刷新</lay-button>
    </div>

    <lay-card>
      <lay-table :columns="columns" :data-source="rows" :loading="loading">
        <template #role="{ row }">
          <lay-select
            v-model="row.role"
            size="sm"
            style="width: 150px"
            :disabled="row.id === currentUserId"
          >
            <lay-select-option value="viewer">只读（viewer）</lay-select-option>
            <lay-select-option value="editor">编辑（editor）</lay-select-option>
            <lay-select-option value="admin">管理员（admin）</lay-select-option>
          </lay-select>
        </template>

        <template #status="{ row }">
          <span v-if="row.disabled" class="status-tag status-tag--disabled">已停用</span>
          <span v-else class="status-tag status-tag--active">正常</span>
        </template>

        <template #operator="{ row }">
          <lay-button
            size="xs"
            type="primary"
            :disabled="row.id === currentUserId || !isDirty(row)"
            :loading="savingId === row.id"
            @click="save(row)"
          >
            保存
          </lay-button>
          <lay-button
            size="xs"
            :type="row.disabled ? 'normal' : 'danger'"
            :disabled="row.id === currentUserId"
            :loading="togglingId === row.id"
            @click="toggleDisabled(row)"
          >
            {{ row.disabled ? '启用' : '停用' }}
          </lay-button>
          <span v-if="row.id === currentUserId" class="self-hint">当前登录账号</span>
        </template>
      </lay-table>
      <div v-if="!rows.length && !loading" class="empty-state">还没有账号</div>
    </lay-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { layer } from '@layui/layui-vue'

import { listUsers, setUserDisabled, updateUserRole, type AdminUser } from '../../api/module/admin'
import { apiErrorMessage } from '../../utils/apiError'
import { useUserStore } from '../../store/user'

const userStore = useUserStore()
const loading = ref(false)
const savingId = ref<number | null>(null)
const togglingId = ref<number | null>(null)
const rows = ref<AdminUser[]>([])
/** 最近一次从服务端读回的原始角色：用来判断某行是否被改过（只有改过才允许保存） */
const originalRoles = ref<Record<number, string>>({})

const currentUserId = computed(() => Number(userStore.userInfo?.id) || null)

const columns = [
  { title: '账号', key: 'account', minWidth: 160 },
  { title: '昵称', key: 'name', minWidth: 140 },
  { title: '状态', width: 90, customSlot: 'status', key: 'status' },
  { title: '角色', width: 200, customSlot: 'role', key: 'role' },
  { title: '操作', width: 260, customSlot: 'operator', key: 'operator' },
]

const isDirty = (row: AdminUser) => originalRoles.value[row.id] !== row.role

async function loadData() {
  loading.value = true
  try {
    const res = await listUsers()
    if (res.code === 200) {
      rows.value = (res.data || []) as AdminUser[]
      originalRoles.value = Object.fromEntries(rows.value.map((item) => [item.id, item.role]))
    } else {
      layer.msg(res.msg || '加载用户列表失败', { icon: 2 })
    }
  } catch (error) {
    layer.msg(apiErrorMessage(error, '加载用户列表失败，请稍后重试'), { icon: 2 })
  } finally {
    loading.value = false
  }
}

/**
 * 停用 / 启用账号。
 *
 * 为什么值得一个按钮：接口在 `POST /api/admin/users/<id>/status`，
 * 页面上没有入口时，管理员"停用某人"只能靠改角色或手写 SQL——而改角色并不阻止登录。
 * 停用是**独立于角色**的一档：角色决定能做什么，停用决定能不能进来。
 */
async function toggleDisabled(row: AdminUser) {
  togglingId.value = row.id
  try {
    const res = await setUserDisabled(row.id, !row.disabled)
    if (res.code === 200) {
      layer.msg(
        `已${row.disabled ? '启用' : '停用'}账号 ${row.account}；` +
        '该账号在其它设备上的凭证已失效（RAG 侧最迟在一个撤销查询周期内失效）',
        { icon: 1 })
      await loadData()
    } else {
      layer.msg(res.msg || '操作失败', { icon: 2 })
      await loadData()
    }
  } catch (error) {
    layer.msg(apiErrorMessage(error, '操作失败，请稍后重试'), { icon: 2 })
    await loadData()
  } finally {
    togglingId.value = null
  }
}

async function save(row: AdminUser) {
  savingId.value = row.id
  try {
    const res = await updateUserRole(row.id, row.role)
    if (res.code === 200) {
      // 菜单是登录时下发的，角色改了不代表对方界面立刻变——提示写清楚，免得管理员以为没生效
      layer.msg(`已把 ${row.account} 设为「${roleLabel(row.role)}」，该用户下次登录生效`, { icon: 1 })
      await loadData()
    } else {
      layer.msg(res.msg || '保存失败', { icon: 2 })
      await loadData()  // 失败即回滚显示：以服务端为准
    }
  } catch (error: any) {
    // 403 的文案来自后端（"不能修改自己的角色…" / "仅管理员可执行该操作"），
    // 它藏在 error.response.data.msg 里，不是 error.message
    layer.msg(apiErrorMessage(error, '保存失败，请稍后重试'), { icon: 2 })
    await loadData()
  } finally {
    savingId.value = null
  }
}

const roleLabel = (role: string) =>
  ({ admin: '管理员', editor: '编辑', viewer: '只读' } as Record<string, string>)[role] || role

onMounted(loadData)
</script>

<style scoped>
.admin-users-page {
  padding: 20px;
  min-height: 100%;
  background: linear-gradient(180deg, #f7f2ea 0%, #eef4fb 100%);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
  padding: 18px;
  margin-bottom: 16px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(191, 160, 106, 0.18);
  border-radius: 18px;
  box-shadow: 0 12px 28px rgba(74, 54, 24, 0.08);
}

.page-header h1 {
  margin: 0 0 6px;
  font-size: 20px;
  color: #3c2f1c;
}

.page-header p {
  margin: 0;
  max-width: 780px;
  color: #7a6a52;
  font-size: 13px;
  line-height: 1.7;
}

.empty-state {
  padding: 24px;
  text-align: center;
  color: #9a8b74;
}

.status-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 18px;
}

.status-tag--active {
  color: #2b7a3d;
  background: rgba(43, 122, 61, 0.12);
}

.status-tag--disabled {
  color: #a33;
  background: rgba(170, 51, 51, 0.12);
}

.self-hint {
  margin-left: 8px;
  color: #9a8b74;
  font-size: 12px;
}
</style>
