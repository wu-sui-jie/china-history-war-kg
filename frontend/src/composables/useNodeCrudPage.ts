/** 知识库管理页的公共 CRUD 逻辑（FE-4，2026-09-25）。
 *
 * 四个管理页（战争事件 / 参战组织 / 历史人物 / 战争地点）原先各有一份 ~250 行的
 * 复制粘贴：列表拉取、分页与查询、新增/编辑提交、删除确认、详情跳转。四份的差异只有
 * 节点类型、字段集合、列配置与文案，逻辑一字不差——改一处要改四遍。
 *
 * 这里把逻辑收成一份，页面只声明"自己长什么样"：
 *
 *     const { dataSource, submit, ... } = useNodeCrudPage({
 *       nodeType: 'Event',
 *       displayType: '战争事件',
 *       nameField: 'EventName',
 *       nameLabel: '事件名称',
 *       emptyForm: () => ({ id: null, type: 'Event', EventName: '', ... }),
 *     })
 *
 * 行为与抽之前的四份实现逐句对应（含提示文案、空值送 null、删除前确认），
 * tests/component/crud-pages.test.ts 是这四页的回归底线。
 */

import { onMounted, ref, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { layer } from '@layui/layui-vue'

import { createNode, deleteNode as deleteNodeApi, findNodePage, updateNode, type NodePayload } from '@/api/module/node'

export type NodeRow = Record<string, any>

/** 列表列定义（lay-table 的 columns 项）。 */
export interface NodeColumn {
  title: string
  key?: string
  width?: number
  minWidth?: number
  align?: 'left' | 'center' | 'right'
  customSlot?: string
}

export interface NodeCrudPageOptions {
  /** 后端节点类型：Event / Organization / Person / Place */
  nodeType: string
  /** 列表"类型"列显示的文案（各页不同：战争事件 / 参战组织 / …） */
  displayType: string
  /** 主名字段：表单必填、列表首列、提示文案都用它 */
  nameField: string
  /** 主名字段的中文名，用于"请输入 XX""确定要删除 XX"等提示 */
  nameLabel: string
  /** 新增时的空表单，必须含 id/type 与全部可编辑字段 */
  emptyForm: () => NodeRow
}

export interface NodeCrudPage {
  searchName: Ref<string>
  page: Ref<{ total: number; limit: number; current: number }>
  dataSource: Ref<NodeRow[]>
  visible: Ref<boolean>
  formRef: Ref<any>
  isSubmitting: Ref<boolean>
  formData: Ref<NodeRow>
  formRules: Record<string, any>
  add: () => void
  resetForm: () => void
  submit: () => void
  close: () => void
  viewDetail: (row: NodeRow) => void
  openEntityDetail: (row: NodeRow) => void
  deleteNode: (row: NodeRow) => void
  change: (payload: { current: number; limit: number }) => void
  toSearch: () => void
  toReset: () => void
  query: () => void
}

/** 按各页的列差异拼出列定义：序号、名字列、类型列、若干业务列、操作列。 */
export function nodeColumns(
  nameColumn: NodeColumn,
  extraColumns: NodeColumn[] = [],
  operatorWidth = 260,
): NodeColumn[] {
  return [
    { title: '序号', width: 80, align: 'center', customSlot: 'index', key: 'index' },
    nameColumn,
    { title: '类型', width: 120, customSlot: 'type', key: 'type', align: 'center' },
    ...extraColumns,
    { title: '操作', width: operatorWidth, customSlot: 'operator', key: 'operator', align: 'center' },
  ]
}

export function useNodeCrudPage(options: NodeCrudPageOptions): NodeCrudPage {
  const { nodeType, displayType, nameField, nameLabel, emptyForm } = options

  const router = useRouter()
  const route = useRoute()

  const searchName = ref('')
  const page = ref({ total: 0, limit: 10, current: 1 })
  const dataSource = ref<NodeRow[]>([])
  const visible = ref(false)
  const formRef = ref<any>(null)
  const isSubmitting = ref(false)
  const formData = ref<NodeRow>(emptyForm())

  const formRules = {
    [nameField]: {
      required: true,
      message: `请输入${nameLabel}`,
      min: 1,
      max: 100,
    },
  }

  // 参与提交的字段 = 空表单里除 id/type 之外的键，顺序也沿用空表单（后端不关心顺序，
  // 但保持与抽之前逐字段一致，便于对照 payload）
  const submitFields = Object.keys(emptyForm()).filter((key) => key !== 'id' && key !== 'type')

  function add() {
    resetForm()
    visible.value = true
  }

  function resetForm() {
    formData.value = emptyForm()
    formRef.value?.clearValidate()
  }

  function submit() {
    formRef.value.validate((isValidate: boolean) => {
      if (!isValidate) {
        layer.msg('请填写必填项', { icon: 2 })
        return
      }

      isSubmitting.value = true
      // 主名字段原样送（必填），其余空值统一送 null：后端按 null 落库为空，不能送空串
      const submitData: NodePayload = { type: nodeType }
      for (const field of submitFields) {
        submitData[field] = field === nameField ? formData.value[field] : formData.value[field] || null
      }

      if (formData.value.id) submitData.id = formData.value.id

      // 走向由 api 模块决定（/create_node 与 /update_node 的 URL 都在那里，不散在组件里）；
      // 行首的分号不能省：上一行以 `)` 结尾、这一行以 `(` 开头，会被解析成函数调用
      ;(formData.value.id ? updateNode : createNode)(submitData)
        .then((res) => {
          isSubmitting.value = false
          if (res.code === 200) {
            layer.msg(formData.value.id ? '修改成功' : '创建成功', { icon: 1 })
            close()
            query()
          } else {
            layer.msg(res.msg || '操作失败', { icon: 2 })
          }
        })
        .catch((error: any) => {
          isSubmitting.value = false
          // HTTP 非 2xx（如 viewer 被 403 拒绝）走 axios 异常分支，后端的
          // msg 在 error.response.data 里——直接弹它，别笼统说"网络错误"
          layer.msg(error?.response?.data?.msg || '网络错误，请重试', { icon: 2 })
        })
    })
  }

  function close() {
    visible.value = false
    resetForm()
  }

  function viewDetail(row: NodeRow) {
    const next: NodeRow = { id: row.id, type: nodeType }
    for (const field of submitFields) {
      next[field] = row[field] || ''
    }
    formData.value = next
    visible.value = true
  }

  function openEntityDetail(row: NodeRow) {
    // back 用当前列表页路径，详情页的返回按钮据此回到本页
    router.push(`/knowledge/entity-detail?type=${nodeType}&id=${row.id}&back=${encodeURIComponent(route.path)}`)
  }

  function deleteNode(row: NodeRow) {
    layer.confirm(`确定要删除 "${row[nameField]}" 吗？`, {
      icon: 3,
      title: '确认删除',
      yes(index: number) {
        layer.close(index)
        deleteNodeApi({ type: nodeType, id: row.id }).then((res) => {
          if (res.code === 200) {
            layer.msg('删除成功', { icon: 1 })
            query()
          } else {
            layer.msg(res.msg || '删除失败', { icon: 2 })
          }
        }).catch((error: any) => {
          // 修正 2026-09-25：原先没有 catch，viewer 被 403 拒绝时确认框关了
          // 却毫无反馈（"删除没反应"）。与提交路径一致，弹后端的失败原因。
          layer.msg(error?.response?.data?.msg || '网络错误，请重试', { icon: 2 })
        })
      },
      btn2(index: number) {
        layer.close(index)
      },
      // layui-vue 的 LayerProps 类型没写出 btn2（取消按钮），运行时支持；这里只压类型，
      // 行为与四个页面原来的写法一致
    } as any)
  }

  function change({ current, limit }: { current: number; limit: number }) {
    page.value.current = current
    page.value.limit = limit
    query()
  }

  function toSearch() {
    page.value.current = 1
    query()
  }

  function toReset() {
    searchName.value = ''
    page.value.current = 1
    query()
  }

  function query() {
    findNodePage({
      pageNum: page.value.current,
      pageSize: page.value.limit,
      name: searchName.value,
      node_type: nodeType,
    }).then((res) => {
      if (res.code === 200) {
        dataSource.value = (res.data.records || []).map((item: NodeRow) => ({ ...item, type: displayType }))
        page.value.total = res.data.total || 0
      } else {
        layer.msg(res.msg || '查询失败', { icon: 2 })
      }
    })
  }

  onMounted(() => query())

  return {
    searchName, page, dataSource, visible, formRef, isSubmitting, formData, formRules,
    add, resetForm, submit, close, viewDetail, openEntityDetail, deleteNode,
    change, toSearch, toReset, query,
  }
}
