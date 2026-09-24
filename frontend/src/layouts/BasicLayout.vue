<!--
  基础布局组件
  
  功能: 应用整体布局框架
    - 顶部导航栏
    - 侧边菜单
    - 内容区域
    - 全局设置、消息通知
-->
<template>
  <lay-config-provider
      :themeVariable="appStore.themeVariable"
      :theme="appStore.theme"
      :locales="locales"
      :locale="appStore.locale"
  >
    <lay-layout
        :class="[
        appStore.tab ? 'has-tab' : '',
        appStore.collapse ? 'collapse' : '',
        appStore.greyMode ? 'grey-mode' : ''
      ]"
    >
      <!-- 遮盖层 -->
      <div
          v-if="!appStore.collapse"
          class="layui-layer-shade hidden-sm-and-up"
          @click="collapse"
      ></div>
      <!-- 核心菜单  -->
      <lay-side
          :width="sideWidth"
          :class="appStore.sideTheme == 'dark' ? 'dark changeBgc history-side' : 'light history-side'"
      >
        <div v-if="appStore.logo" class="side-brand-card">
          <div
            class="side-brand-main"
            title="点击返回首页仪表盘"
            @click="goDashboard"
          >
            <div class="side-brand-title">干戈纪略</div>
          </div>
        </div>
        <div class="side-menu-wrapper">
          <div
              class="side-menu1"
              v-if="appStore.subfield && appStore.subfieldPosition == 'side'"
          >
            <global-main-menu
                :collapse="true"
                :menus="mainMenus"
                :selectedKey="mainSelectedKey"
                @changeSelectedKey="changeMainSelectedKey"
            ></global-main-menu>
          </div>
          <div class="side-menu2">
            <global-menu
                :collapse="appStore.collapse"
                :menus="menus"
                :openKeys="openKeys"
                :selectedKey="selectedKey"
                @changeOpenKeys="changeOpenKeys"
                @changeSelectedKey="changeSelectedKey"
            ></global-menu>
          </div>
        </div>
        <div class="side-bottom-actions">
          <div class="side-user-chip">
            <span class="side-user-name" :title="displayName">{{ displayName || '未登录' }}</span>
            <span :class="['side-user-role', `role-${roleKey}`]">{{ roleLabel }}</span>
          </div>
          <lay-tooltip content="退出登录">
            <div class="side-brand-icon" @click="logOut">
              <img src="/icon/logout.svg" style="width: 16px;"/>
            </div>
          </lay-tooltip>
        </div>
      </lay-side>
      <lay-layout style="width: 0px">
        <div v-if="showGlobalSearchBar" class="global-search-bar">
          <lay-input
            v-model="globalKeyword"
            placeholder="全局搜索事件、人物、地点、组织"
            @keyup.enter="goGlobalSearch"
          />
          <lay-button type="primary" @click="goGlobalSearch">搜索</lay-button>
        </div>
        <lay-body>
          <global-content></global-content>
        </lay-body>
        <lay-footer></lay-footer>
      </lay-layout>
    </lay-layout>
    <global-setup v-model="visible"></global-setup>
  </lay-config-provider>
</template>

<script lang="ts">
import {computed, onMounted, ref} from 'vue'
import {useAppStore} from '../store/app'
import {useUserStore} from '../store/user'
import GlobalSetup from './global/GlobalSetup.vue'
import GlobalContent from './global/GlobalContent.vue'
import GlobalBreadcrumb from './global/GlobalBreadcrumb.vue'
import GlobalTab from './global/GlobalTab.vue'
import GlobalMenu from './global/GlobalMenu.vue'
import GlobalMainMenu from './global/GlobalMainMenu.vue'
import GlobalMessageTab from './global/GlobalMessageTab.vue'
import {useRoute, useRouter} from 'vue-router'
import {useMenu} from './composable/useMenu'
import zh_CN from '../lang/zh_CN'
import en_US from '../lang/en_US'

export default {
  components: {
    GlobalSetup,
    GlobalContent,
    GlobalTab,
    GlobalMenu,
    GlobalBreadcrumb,
    GlobalMainMenu,
    GlobalMessageTab
  },
  setup() {
    const appStore = useAppStore()
    const userInfoStore = useUserStore()
    const fullscreenRef = ref()
    const visible = ref(false)
    const sideWidth = computed(() =>
        appStore.collapse
            ? '60px'
            : appStore.subfield && appStore.subfieldPosition == 'side'
                ? '280px'
                : '220px'
    )
    const router = useRouter()
    const route = useRoute()
    const globalKeyword = ref('')
    const showGlobalSearchBar = computed(() => {
      return !route.path.startsWith('/knowledge/graph') && !route.path.startsWith('/knowledge-list')
    })

    const {
      selectedKey,
      openKeys,
      menus,
      mainMenus,
      mainSelectedKey,
      changeMainSelectedKey,
      changeSelectedKey,
      changeOpenKeys
    } = useMenu()
    
    onMounted(() => {
      if (document.body.clientWidth < 768) {
        appStore.collapse = true
      }
      // 刷新页面后 token 从 localStorage 恢复、但 userInfo 不走持久化恢复，
      // 这里兜底拉一次；登录/注册成功后也会主动拉。
      userInfoStore.loadUserInfo()
      userInfoStore.loadMenus()
      userInfoStore.loadPermissions()
    })

    const changeVisible = () => {
      visible.value = !visible.value
    }

    const currentIndex = ref('1')

    const collapse = () => {
      appStore.collapse = !appStore.collapse
    }

    const refresh = () => {
      appStore.routerAlive = false
      setTimeout(function () {
        appStore.routerAlive = true
      }, 500)
    }

    const logOut = () => {
      const userInfoStore = useUserStore()
      // 一次性清空 token/用户信息/菜单/权限，避免换账号后残留上一账号的菜单
      userInfoStore.clearSession()
      router.push('/login')
    }

    // 当前账号与角色展示：admin 管理员 / editor 编辑者 / viewer 只读
    const displayName = computed(() =>
        userInfoStore.userInfo?.name || userInfoStore.userInfo?.account || ''
    )
    const roleKey = computed(() => {
      const role = userInfoStore.userInfo?.role
      return role === 'admin' || role === 'editor' ? role : 'viewer'
    })
    const roleLabel = computed(() =>
        ({admin: '管理员', editor: '编辑者', viewer: '只读'})[roleKey.value]
    )

    const goDashboard = () => {
      router.push('/workspace/dashboard')
    }

    const goGlobalSearch = () => {
      const keyword = globalKeyword.value.trim()
      router.push(keyword ? `/knowledge/search?keyword=${encodeURIComponent(keyword)}` : '/knowledge/search')
    }

    const locales = [
      {name: 'zh_CN', locale: zh_CN, merge: true},
      {name: 'en_US', locale: en_US, merge: true}
    ]

    const flag = ref(false)

    function changeDropdown() {
      flag.value = !flag.value
    }

    return {
      sideWidth,
      mainSelectedKey,
      fullscreenRef,
      appStore,
      visible,
      menus,
      mainMenus,
      userInfoStore,
      currentIndex,
      selectedKey,
      openKeys,
      collapse,
      changeOpenKeys,
      changeSelectedKey,
      changeMainSelectedKey,
      changeVisible,
      refresh,
      logOut,
      displayName,
      roleKey,
      roleLabel,
      goDashboard,
      globalKeyword,
      showGlobalSearchBar,
      goGlobalSearch,
      locales,
      changeDropdown,
      flag
    }
  }
}
</script>

<style lang="less">
@media screen and (max-width: 767px) {
  .layui-side {
    position: absolute;
    height: 100vh;
  }
}

/*鼠标经过背景色，增加了improtant，否则设置无效*/
.layui-header .layui-nav-item .layui-icon:hover {
  background: whitesmoke !important;
}

/*面包屑颜色兼容*/
.layui-header .layui-nav-item .layui-breadcrumb a {
  color: #999 !important;
}

.layui-header .layui-nav-item .layui-breadcrumb a:nth-last-child(2) {
  color: #666 !important;
}

/*图标默认颜色修复，指定 .layui-icon 去掉improtant，否则无法设置图标其他颜色*/
.layui-header .layui-nav-item .layui-icon {
  color: #666;
}

/*取消默认a标签的padding:0 20px，否则扩大图标后容器变形*/
.layui-header .layui-nav-item > a {
  padding: 0 !important;
}

/*扩大图标尺寸与所在容器大小一致，默认大小导致鼠标必须点击图标才能触发事件效果*/
.layui-header .layui-nav-item .layui-icon {
  height: 50px;
  padding: 20px;
}

/*增加鼠标经过图标时改变图标颜色，颜色为当前系统主题色*/
.layui-header .layui-nav-item .layui-icon:hover {
  color: #BFA06A !important;
}

.grey-mode {
  filter: grayscale(1);
}

.history-side {
  padding: 14px 10px 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}

.side-brand-card {
  margin: 4px 6px 12px;
  padding: 18px 18px 20px;
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(255, 249, 236, 0.58), rgba(255, 255, 255, 0.18));
  border: 1px solid rgba(255, 255, 255, 0.3);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.28), 0 10px 24px rgba(101, 74, 25, 0.12);
}

.side-brand-main {
  cursor: pointer;
}

.side-brand-title {
  color: #2c2c2c;
  font-size: clamp(20px, 2.4vw, 28px);
  font-weight: 700;
  line-height: 1.3;
  letter-spacing: 0.04em;
  word-break: break-all;
  text-align: center;
}

.side-brand-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: rgba(255,255,255,0.38);
  cursor: pointer;
  transition: transform 0.2s ease, background 0.2s ease;
}

.side-brand-icon:hover {
  transform: translateY(-1px);
  background: rgba(255,255,255,0.58);
}

.side-menu-wrapper {
  width: 100%;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  display: flex;
  padding: 0 4px 10px;
}

.side-bottom-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 8px 6px 4px;
}

/* 当前账号与角色徽标：让"谁在用、什么权限"在界面上可见 */
.side-user-chip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  max-width: 100%;
}

.side-user-name {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #6b5b3e;
  font-size: 12px;
  font-weight: 600;
}

.side-user-role {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  color: #fff;
}

.side-user-role.role-admin {
  background: #b45309;
}

.side-user-role.role-editor {
  background: #0f766e;
}

.side-user-role.role-viewer {
  background: #9ca3af;
}

.side-menu-wrapper::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.side-menu-wrapper::-webkit-scrollbar-thumb {
  border-radius: 10px;
  background-color: rgb(40, 51, 62);
}

.light .side-menu-wrapper::-webkit-scrollbar-thumb {
  background-color: #e2e2e2;
}

.side-menu1 {
  width: 60px;
  flex: 0 0 60px;
  border-right: 1px solid rgba(0, 0, 0, 0.12);
}

.light .side-menu1 {
  border-right: 1px solid whitesmoke;
}

.side-menu2 {
  flex: 1;
}

.changeBgc {
  background: linear-gradient(
    180deg,
    #D8C7A3 0%,
    #C9B48C 100%
  ) !important;
}

/* ===== 侧边栏文字颜色 ===== */
.dark {
  color: #2C2C2C !important;
}

.dark .layui-menu-item,
.dark .layui-menu-body-title,
.dark .layui-menu-body-title a {
  color: #2C2C2C !important;
}


/* ===== 菜单选中颜色 ===== */
.dark .layui-this,
.dark .layui-menu-itemed > .layui-menu-body-title {
  background: linear-gradient(135deg, #9c8455, #c4aa76) !important;
  color: #ffffff !important;
  border-radius: 12px;
  box-shadow: 0 8px 18px rgba(109, 82, 33, 0.16);
}

/* ===== 鼠标悬停 ===== */
.dark .layui-menu-item:hover,
.dark .layui-menu-body-title:hover {
  background-color: rgba(255, 248, 232, 0.72) !important;
  color: #2C2C2C !important;
  transition: all 0.25s ease;
}

.history-side .layui-menu {
  background: transparent !important;
}

.history-side .layui-menu-item,
.history-side .layui-sub-menu > .layui-menu-body-title {
  margin: 6px 0;
  border-radius: 12px;
}

.history-side .layui-menu-body-title {
  min-height: 42px;
  border-radius: 12px;
}

.history-side .layui-menu-item:focus,
.history-side .layui-menu-item:focus-visible,
.history-side .layui-menu-body-title:focus,
.history-side .layui-menu-body-title:focus-visible,
.history-side .layui-menu-body-title a:focus,
.history-side .layui-menu-body-title a:focus-visible {
  outline: none !important;
  box-shadow: none !important;
}

.history-side .layui-menu-body-title-content {
  font-weight: 600;
  letter-spacing: 0.01em;
}

.history-side .layui-menu-item .layui-icon,
.history-side .layui-sub-menu .layui-icon {
  opacity: 0.92;
}

.underpainting {
  .layui-tab-title {
    .layui-this {
      color: #A88442 !important;              /* Tab文字颜色 */
      border-bottom: 2px solid #BFA06A !important; /* 底部高亮线 */
      background-color: #F5EFE6 !important;   /* 轻宣纸色背景 */

      .layui-icon {
        color: #A88442 !important;
      }
    }
  }
}
.layui-body
> .global-tab
> .layui-tab
> .layui-tab-head
> .layui-tab-title
> li {
  height: 38px;
  line-height: 38px;
}

.global-search-bar {
  height: 54px;
  display: grid;
  grid-template-columns: minmax(260px, 520px) auto;
  gap: 10px;
  align-items: center;
  justify-content: flex-end;
  padding: 8px 24px;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.88);
  border-bottom: 1px solid rgba(191, 160, 106, 0.18);
}

.designer {
  padding-left: 5px;
  box-sizing: border-box;

  .layui-tab-head {
    background-color: unset !important;
  }

  .layui-tab-title {
    background-color: unset !important;

    > li {
      background-color: #fff;
      margin: 5px 0 0 5px;
      border-radius: 4px;
      height: 32px !important;
      line-height: 32px !important;
    }
  }
}
</style>
