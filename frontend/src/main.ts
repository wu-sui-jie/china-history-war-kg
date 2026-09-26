import { createApp } from 'vue'
import Router from './router'
import Store from './store'
import App from './App.vue'
import { permission } from "./directives/permission";
import Layui from '@layui/layui-vue'
import '@layui/layui-vue/lib/index.css'

async function bootstrap() {
  // 不引入 mockjs：它会拦截 /user/menu、/user/permission 返回硬编码菜单，
  // 不感知角色（viewer 也拿到全量管理菜单）——菜单的唯一事实源是后端
  // get_menu()（按角色裁剪）。开发时照常连后端即可。
  const app = createApp(App)

  app.use(Store);
  app.use(Router);
  app.use(Layui);
  app.directive("permission",permission);
  app.mount('#app');
}

bootstrap()
