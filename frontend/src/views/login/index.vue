<!--
  用户登录页面
  
  功能: 用户登录和注册
    - 登录表单: 账号密码输入
    - 登录验证: POST /api/login
    - 用户注册: POST /api/sign_in
    - Token存储: JWT Token保存到本地
-->
<template>
  <div class="login-wrap" :style="wrapStyle">
    <div class="login-root">
      <div class="login-main">
        <div class="login-container">
          <div class="login-side">
            <div class="login-bg-title">
              <h1 style="letter-spacing: 1px;font-weight: 700">干戈纪略</h1>

<!--              <h3 style="margin: 20px auto">-->
<!--                Knowledge Graph Visualization System-->
<!--              </h3>-->

              <div>
                <img :src="loginImage" alt="干戈纪略" style="width: 300px;margin-top: 40px"/>
              </div>
            </div>
          </div>
          <div class="login-ID">
            <lay-tab type="brief" v-model="method">
              <lay-tab-item title="登录" id="1">
                <div style="height: 250px">
                  <lay-form-item :label-width="0">
                    <lay-input :allow-clear="true" prefix-icon="layui-icon-username" placeholder="用户名"
                               v-model="loginForm.account"></lay-input>
                  </lay-form-item>
                  <lay-form-item :label-width="0">
                    <lay-input :allow-clear="true" prefix-icon="layui-icon-password" placeholder="密码" password
                               type="password" v-model="loginForm.password"></lay-input>
                  </lay-form-item>
                  <lay-form-item :label-width="0">
                    <lay-button style="margin-top: 80px" type="primary" :loading="loging" :fluid="true"
                                loadingIcon="layui-icon-loading" @click="loginSubmit">登录
                    </lay-button>
                  </lay-form-item>
                </div>
              </lay-tab-item>
              <lay-tab-item title="注册" id="2">
                <div style="height: 250px">
                  <lay-form-item :label-width="0">
                    <lay-input :allow-clear="true" prefix-icon="layui-icon-username" placeholder="用户名"
                               v-model="loginForm.account"></lay-input>
                  </lay-form-item>
                  <lay-form-item :label-width="0">
                    <lay-input :allow-clear="true" prefix-icon="layui-icon-username" placeholder="昵称"
                               v-model="loginForm.name"></lay-input>
                  </lay-form-item>
                  <lay-form-item :label-width="0">
                    <lay-input :allow-clear="true" prefix-icon="layui-icon-password" placeholder="密码" password
                               type="password" v-model="loginForm.password"></lay-input>
                  </lay-form-item>
                  <lay-form-item :label-width="0">
                    <lay-button style="margin-top: 60px" type="primary" :loading="loging" :fluid="true"
                                loadingIcon="layui-icon-loading" @click="signinSubmit">注册
                    </lay-button>
                  </lay-form-item>
                </div>
              </lay-tab-item>
            </lay-tab>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import {defineComponent, reactive, ref} from 'vue'
import {useRouter} from 'vue-router'
import {useUserStore} from '../../store/user'
import {layer} from "@layui/layui-vue"
import { login, signIn } from "@/api/module/user"
import {apiErrorMessage} from "@/utils/apiError"

// 口令长度必须与后端 `backend/password_policy.py` 一致（第 13 轮复核整改 §2.1 之后，
// 首管命令、注册、改密码、前端提示共用那一份口径；跨语言无法共享常量，改数值要两处一起改）。
// 原先这里是 6~20，而后端第 13 轮整改把下限提到 10：不一致的后果是前端放行、
// 后端 400，用户看到"密码长度需在 10~64 位之间"却不知道自己哪里填错了。
// 上限也从 20 提到 64——前端比后端更严会在用户用长口令时凭空拦住他。
const MIN_PASSWORD_LENGTH = 10
const MAX_PASSWORD_LENGTH = 64

// 登录页的两张图放在 public/ 下：vite 原样拷贝、不做哈希改名，因此**不会**被构建器
// 补上部署前缀，路径必须自己带上 vite 的 base（本仓库是 /static/）。
//
// 原先这里是 `src="/login.jpg"` 与 CSS 里的 `url(background.jpg)`，两者在生产都指向
// 不存在的地址（第 12 轮审查 P1-4）：
//   - `/login.jpg` 不带 /static/ 前缀，nginx 会按 `location /` 转给旧后端 Flask，
//     后端没有这条静态路由 → 401/404；
//   - CSS 里的相对 `background.jpg` 会相对 `dist/assets/*.css` 解析成
//     `/static/assets/background.jpg`，而图片实际在 `/static/background.jpg`。
// 构建本身不会报错（vite 只是留一条未解析警告），组件测试也测不到资源路径，
// 所以另加了 scripts/check-dist-assets.mjs 在构建后按产物核对（CI 里跑）。
//
// 用 BASE_URL 而不是写死 /static/：写死会在改 base 或换部署前缀时再犯同一次错。
const BASE_URL = import.meta.env.BASE_URL
const loginImage = `${BASE_URL}login.jpg`
const wrapStyle = { backgroundImage: `url(${BASE_URL}background.jpg)` }

export default defineComponent({
  setup() {
    const router = useRouter()
    const userStore = useUserStore()
    const method = ref('1')
    const loging = ref(false);
    const loginForm = reactive({
      account: '',
      name: '',
      password: '',
    })

    // 只用 trim 处理**账号与昵称**：它们的首尾空格是明显的输入失误（复制粘贴带进来的）。
    //
    // 密码**按原值发送**（第 13 轮复核整改 §2.2）。原先这里也 trim 密码，等于静默改了
    // 用户输入：历史口令或命令行建的口令若带首尾空格，用户照着输入反而登录不上，
    // 而错误提示只能是"用户名或密码错误"——一个用户永远猜不到的原因。
    // 首尾空格该不该被允许由服务端的统一口令策略决定（`backend/password_policy.py`
    // 现在明确返回"不能以空白字符开头或结尾"），前端不做任何规范化。
    const normalizedForm = () => ({
      account: loginForm.account.trim(),
      name: loginForm.name.trim(),
      password: loginForm.password
    })

    const validateLoginForm = () => {
      const {account, password} = normalizedForm()
      if (!account) {
        layer.msg('请输入用户名', {icon: 2})
        return null
      }
      if (!password) {
        layer.msg('请输入密码', {icon: 2})
        return null
      }
      // 登录**不校验口令长度**（第 13 轮整改修正）。
      //
      // 长度策略的作用是"不许设置弱口令"，只该出现在**设置口令**的路径上
      // （注册 validateSignInForm、改密码的接口）。放在登录路径上会直接锁人：
      // 策略提高之前建的账号口令可能只有 6~9 位，服务端 `DbUtils.authentication`
      // 从不检查长度、这些口令完全合法，而前端在本地就把他们拦在门外了。
      // "老口令继续可用、新口令必须够长"是口令策略该有的迁移语义。
      return {account, password}
    }

    const validateSignInForm = () => {
      const {account, name, password} = normalizedForm()
      if (!account) {
        layer.msg('请输入用户名', {icon: 2})
        return null
      }
      // 与后端 `db_utils.MIN_ACCOUNT_LENGTH / MAX_ACCOUNT_LENGTH / MAX_NAME_LENGTH` 同值：
      // 前端这份的作用只是"本地先拦一次、少一次往返"，事实源在后端（第 14 轮审计 P3-10）
      if (account.length < 3 || account.length > 20) {
        layer.msg('用户名长度需为 3-20 位', {icon: 2})
        return null
      }
      if (!name) {
        layer.msg('请输入昵称', {icon: 2})
        return null
      }
      if (name.length > 20) {
        layer.msg('昵称长度不能超过 20 位', {icon: 2})
        return null
      }
      if (!password) {
        layer.msg('请输入密码', {icon: 2})
        return null
      }
      if (password.length < MIN_PASSWORD_LENGTH || password.length > MAX_PASSWORD_LENGTH) {
        layer.msg(`密码长度需为 ${MIN_PASSWORD_LENGTH}-${MAX_PASSWORD_LENGTH} 位`, {icon: 2})
        return null
      }
      return {account, name, password}
    }

    const loginSubmit = async () => {
      if (loging.value) {
        return
      }
      const payload = validateLoginForm()
      if (!payload) {
        return
      }
      loginForm.account = payload.account
      loginForm.password = payload.password
      loging.value = true;
      login(payload)
          .then(({data, code, msg}) => {
            if (code == 200) {
              userStore.token = data
              userStore.loadUserInfo()
              userStore.loadMenus()
              userStore.loadPermissions()
              router.push('/')
            } else {
              layer.msg(msg, {icon: 2})
            }
          })
          // 失败分支必须有 catch（第 13 轮整改）：后端新引入的 429（登录限流）
          // 走的是 axios 的 reject 分支，没有 catch 时用户点完登录**什么都看不到**，
          // 按钮只是重新亮起来——看起来像"没反应"，而实际原因是"尝试太频繁"。
          .catch((error) => layer.msg(apiErrorMessage(error, '登录失败，请稍后重试'), {icon: 2}))
          .finally(() => (loging.value = false));
    }

    const signinSubmit = async () => {
      if (loging.value) {
        return
      }
      const payload = validateSignInForm()
      if (!payload) {
        return
      }
      loginForm.account = payload.account
      loginForm.name = payload.name
      loginForm.password = payload.password
      loging.value = true;
      signIn(payload)
          .then(({data, code, msg}) => {
            if (code == 200) {
              userStore.token = data
              userStore.loadUserInfo()
              userStore.loadMenus()
              userStore.loadPermissions()
              layer.msg(msg || '注册成功', {icon: 1})
              router.push('/')
            } else {
              layer.msg(msg, {icon: 2})
            }
          })
          // 注册同样会因限流/注册开关关闭而走 reject（403/429），没有 catch 时用户
          // 只会看到"点了没反应"。关闭自助注册的部署下这条提示尤其重要。
          .catch((error) => layer.msg(apiErrorMessage(error, '注册失败，请稍后重试'), {icon: 2}))
          .finally(() => (loging.value = false));
    }

    return {
      loginSubmit,
      signinSubmit,
      loginForm,
      method,
      loging,
      loginImage,
      wrapStyle
    }
  }
})
</script>

<style scoped>

.login-wrap {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  right: 0;
  overflow: auto;
  min-width: 600px;
  z-index: 9;
  /* 背景图地址由模板的 :style 下发（要带 vite base 前缀，见 script 里的说明） */
  background-repeat: no-repeat;
  background-size: cover;
  background-position: center;
  min-height: 100vh;
}

.login-wrap :deep(.layui-input-block) {
  margin-left: 0 !important;
}

.login-root {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  justify-content: center;
  width: 100%;
  min-width: 320px;
  background-color: initial;
}

.login-main {
  position: relative;
  display: block;
}

.logo-container {
  max-width: calc(100vw - 28px);
  margin-bottom: 40px;
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-container .logo {
  display: inline-block;
  height: 30px;
  width: 143px;
  background: url() no-repeat 50%;
  background-size: contain;
  cursor: pointer;
}

.login-container {
  position: relative;
  overflow: hidden;
  width: 940px;
  height: 540px;
  max-width: calc(100vw - 28px);
  border-radius: 4px;
  background: hsla(0, 0%, 100%, 0.5);
  backdrop-filter: blur(30px);
  display: flex;
  box-shadow: 6px 6px 12px 4px rgba(0, 0, 0, 0.1);
}

.login-side {
  padding: 40px 20px 20px;
  background-color: #ffffff;
  flex: 1;
  height: 100%;
}

.login-bg-title {
  flex: 1;
  height: 110%;
  color: #01500c;
  text-align: center;
  background-repeat: no-repeat;
  background-position: bottom;
  text-align: center;
  min-width: 200px;
}

.login-ID {
  padding: 20px 30px;
  min-width: 420px;
}

.login-container .layui-tab-head {
  background: transparent;
}

.login-container .layui-input-wrapper {
  margin-top: 10px;
  margin-bottom: 10px;
}

.login-container .layui-input-wrapper {
  margin-top: 12px;
  margin-bottom: 12px;
}

.login-container .assist {
  margin-top: 5px;
  margin-bottom: 5px;
  letter-spacing: 2px;
}

.login-container .layui-btn {
  margin: 10px 0px 10px 0px;
  letter-spacing: 2px;
  height: 40px;
}

.login-container .layui-line-horizontal {
  letter-spacing: 2px;
  margin-bottom: 34px;
  margin-top: 24px;
}

.other-ways {
  display: flex;
  justify-content: space-between;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 14px;
  font-weight: 400;
}

.other-ways li {
  width: 100%;
}

.line-container {
  justify-content: center;
  align-items: center;
  text-align: center;
  cursor: pointer;
}

.line-container .icon {
  height: 28px;
  width: 28px;
  margin-right: 0;
  vertical-align: middle;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px 0 rgb(9 30 66 / 4%), 0 1px 4px 0 rgb(9 30 66 / 10%),
  0 0 1px 0 rgb(9 30 66 / 10%);
}

.line-container .text {
  display: block;
  margin: 12px 0 0;
  font-size: 12px;
  color: #8592a6;
}

:deep(.layui-tab-title .layui-this) {
  background-color: transparent;
}
</style>
