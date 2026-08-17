import { layer } from '@layui/layui-vue'
import { computed, ComputedRef, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { diff } from '../../library/arrayUtil'
import { getParents, getNode } from '../../library/treeUtil'
import { useAppStore } from '../../store/app'
import { useUserStore } from '../../store/user'

// 默认展开工作台和知识图谱两个菜单分组。
const DEFAULT_OPEN_KEYS = ['/workspace/manage', '/knowledge']

export function useMenu() {
  const route = useRoute()
  const router = useRouter()
  const userStore = useUserStore()
  const appStore = useAppStore()
  const selectedKey = ref(route.path)
  const openKeys = ref<string[]>([...DEFAULT_OPEN_KEYS])
  const isAccordion = computed(() => appStore.accordion)
  const isSubfield = computed(() => appStore.subfield)
  const mainSelectedKey = ref('/workspace/dashboard')

  const menus = computed(() => {
    if (isSubfield.value) {
      const node = getNode(userStore.menus, mainSelectedKey.value)
      if (node) {
        return node.children
      }
      return []
    }
    return userStore.menus
  })

  const mainMenus: ComputedRef<any[]> = computed(() => {
    if (isSubfield.value) {
      return userStore.menus
    }
    return []
  })

  const syncOpenKeys = () => {
    let path = route.path

    if (path.startsWith('/knowledge-list')) {
      path = '/knowledge-list'
    }

    selectedKey.value = path
    const parents = getParents(menus.value, path)

    if (parents && parents.length > 0) {
      const parentKeys = parents.map((item: any) => item.id)
      if (isAccordion.value) {
        openKeys.value = [...new Set([...parentKeys, ...DEFAULT_OPEN_KEYS])]
      } else {
        openKeys.value = [...new Set([...parentKeys, ...openKeys.value, ...DEFAULT_OPEN_KEYS])]
      }
      return
    }

    openKeys.value = [...DEFAULT_OPEN_KEYS]
  }

  watch(
    [() => route.path, menus],
    () => {
      syncOpenKeys()
    },
    { immediate: true }
  )

  const to = (id: string) => {
    router.push(id)
  }

  function changeSelectedKey(key: string) {
    const node = getNode(userStore.menus, key)

    if (node.type && node.type == 'modal') {
      layer.open({
        type: 'iframe',
        content: node.id,
        area: ['80%', '80%'],
        maxmin: true,
      })
      return
    }

    if (node.type && node.type == 'blank') {
      window.open(node.id, '_blank')
      return
    }

    to(key)
  }

  function changeOpenKeys(keys: string[]) {
    const addArr = diff(openKeys.value, keys)
    if (keys.length > openKeys.value.length && isAccordion.value) {
      const arr = getParents(menus.value, addArr[0])
      if (arr && arr.length > 0) {
        openKeys.value = arr.map((item: any) => item.id)
      }
    } else {
      openKeys.value = keys
    }
  }

  function changeMainSelectedKey(key: string) {
    const node = getNode(userStore.menus, key)

    if (node.type && node.type == 'modal') {
      layer.open({
        type: 'iframe',
        content: node.id,
        area: ['80%', '80%'],
        maxmin: true,
      })
      return
    }

    if (node.type && node.type == 'blank') {
      window.open(node.id, '_blank')
      return
    }

    mainSelectedKey.value = key
  }

  return {
    selectedKey,
    openKeys,
    changeOpenKeys,
    changeSelectedKey,
    isAccordion,
    menus,
    mainMenus,
    mainSelectedKey,
    changeMainSelectedKey
  }
}
