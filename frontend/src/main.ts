import { createApp } from 'vue'
import Router from './router'
import Store from './store'
import App from './App.vue'
import { permission } from "./directives/permission";
import Layui from '@layui/layui-vue'
import '@layui/layui-vue/lib/index.css'

async function bootstrap() {
  // mockjs 只在开发环境启用：它在 XHR 层拦截 /user/menu、/user/permission 等请求，
  // 无条件 import 会被打进生产包，让后端同名接口永远不生效。
  // 需要在开发时直接连后端（例如调试菜单接口）时设 VITE_ENABLE_MOCK=false。
  if (import.meta.env.DEV && import.meta.env.VITE_ENABLE_MOCK !== 'false') {
    await import('./mockjs')
  }

  const app = createApp(App)

  app.use(Store);
  app.use(Router);
  app.use(Layui);
  app.directive("permission",permission);
  app.mount('#app');
}

bootstrap()
