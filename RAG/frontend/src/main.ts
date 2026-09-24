import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from '@/App.vue'
import '@/styles.css'
import { useSessionStore } from '@/stores/session'
import { installHostUserBridge } from '@/utils/userScope'

const app = createApp(App)
app.use(createPinia())

// 主应用以 iframe 嵌入时会把当前账号 postMessage 进来（问题二方案 A：会话记录按账号隔离）。
// 装在这里而不是组件里：会话存储在 store 初始化时就要读到正确的 key，而 store 可能在
// 任何组件挂载前就被创建（App.vue 就会用到）。独立访问 :8000 时收不到消息，
// 行为与改造前一致。
//
// 换桶（setActiveUid）由 store 的 applyUserScope 负责——它必须先把当前状态写回旧桶、
// 再换 uid（顺序反了会串数据），所以这里只把解析好的身份原样转交。
const sessionStore = useSessionStore()
installHostUserBridge((scope) => sessionStore.applyUserScope(scope))

app.mount('#app')
