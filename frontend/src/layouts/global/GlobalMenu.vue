<template>
  <lay-menu
    :tree="true"
    :collapse="collapse"
    :level="appStore.level"
    :inverted="appStore.inverted"
    :theme="appStore.sideTheme"
    :openKeys="openKeys"
    :selectedKey="selectedKey"
    @changeOpenKeys="changeOpenKeys"
    @changeSelectedKey="changeSelectedKey"
  >
    <GlobalMenuItem :menus="menus" :teleportProps="{disabled: false}"></GlobalMenuItem>
  </lay-menu>
</template>

<script lang="ts">
export default {
  name: "GlobalMenu",
};
</script>

<script lang="ts" setup>
import { useAppStore } from "../../store/app";
import GlobalMenuItem from "./GlobalMenuItem.vue";

const appStore = useAppStore();

interface MenuProps {
  collapse: boolean;
  selectedKey: string;
  openKeys: string[];
  menus: any[];
}

const props = withDefaults(defineProps<MenuProps>(), {
  collapse: false,
});

const emits = defineEmits(['changeOpenKeys', 'changeSelectedKey'])

const changeOpenKeys = (keys: string[]) => {
  emits("changeOpenKeys", keys);
}

const changeSelectedKey = (key: string) => {
  emits("changeSelectedKey", key);
};

// 移除了 onMounted 中的强制展开逻辑，现在由 useMenu.ts 统一管理
// 如需保留后备方案，可取消下面注释：
/*
import { onMounted } from 'vue';
onMounted(() => {
  setTimeout(() => {
    if (props.openKeys.length === 0) {
      changeOpenKeys(['/knowledge/graph']);
    }
  }, 100);
});
*/
</script>

<style>
.layui-nav-tree * {
  font-size: 14px;
}

.layui-nav-tree .layui-nav-item > a,
.layui-nav-tree.inverted .layui-nav-item > a {
  padding: 8px 18px;
}

.layui-nav-tree.inverted .layui-this > a {
  padding: 8px 16px;
}

.layui-nav-tree .layui-nav-item > a > span {
  padding-left: 12px;
}

.layui-nav-tree .layui-nav-item > a .layui-nav-more {
  font-size: 12px!important;
  padding: 3px 0px;
}
</style>
