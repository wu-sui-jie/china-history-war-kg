# frontend — 旧知识库管理台

基于 `Vue 3` + `TypeScript` + `layui-vue` 的图谱可视化与管理台，是**浏览器唯一入口**（开发 :3001）。

- **代理**：`/api` → 旧后端（:5000），`/rag` → RAG 服务（:8000）
- **构建 base**：`/static/`（生产由 nginx 按 `location /static/` 发静态文件）
- **后端接口与数据模型**：见 [../backend/README.md](../backend/README.md)
- **跨模块集成与安全边界**：见 [../docs/集成与入口约定.md](../docs/集成与入口约定.md)

## 技术栈

Vue 3（Composition API + `<script setup>`）、TypeScript、layui-vue（UI 组件库）、
ECharts（图谱与地图）、Axios、Pinia（状态）、Vite（构建）、markdown-it + highlight.js（问答渲染）。

## 运行方式

```bash
cd frontend
pnpm install      # 首次（或 npm install）
pnpm dev          # 或 npm run dev
```

访问 **http://localhost:3001**。端口在 `vite.config.ts` 中固定为 3001（不是 Vite 默认的 5173；
`strictPort: true`，被占用时直接报错而不是静默换端口），并已把 `/api`、`/rag` 两条代理配好——
所以浏览器只需要这一个地址。接口 baseURL 是同源相对路径 `/`，与 SSE 走同一条通道。

```bash
pnpm build          # 生产构建，产物到 dist/
pnpm build:check    # 类型检查（vue-tsc --noEmit）+ 生产构建，提 PR 前建议跑
```

> 若用镜像源更快的场景：`npm config set registry https://mirrors.huaweicloud.com/repository/npm/`。
>
> 类型检查基于 TypeScript 5.x（Vue 3.5 的 `.d.ts` 需要 5.x 才认识）；`tsconfig.json` 开了
> `skipLibCheck`（跳过第三方声明）与 `allowJs`（四个无 `lang="ts"` 的维护页需要）。

## 页面与路由

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/login` | `views/login/index.vue` | 登录（走 `/api/login`） |
| `/workspace/dashboard` | `views/workspace/Dashboard.vue` | 首页仪表盘 |
| `/workspace/dataset` | `views/workspace/DatasetCenter.vue` | 数据集中心 |
| `/workspace/dataset-versions` | `views/workspace/DatasetVersions.vue` | 数据版本管理 |
| `/workspace/quality` | `views/workspace/QualityInspection.vue` | 图谱质检 |
| `/workspace/repair` | `views/workspace/QualityInspection.vue` | 数据修复工作台（与质检同组件的另一个入口） |
| `/knowledge/graph` | `views/knowledge/graph/OverviewGraph.vue` | 战争关系图总览 |
| `/knowledge/graph/{event,organization,person,place}` | `views/knowledge/graph/EntityGraph.vue` | 四个关系维度子页（**同一组件按 `meta.graphKind` 参数化**） |
| `/knowledge-list/{event,organization,person,place}` | `views/knowledge-list/*/Node*.vue` | 节点关系管理（增删改查） |
| `/knowledge/entity-detail` | `views/knowledge/EntityDetail.vue` | 实体详情 |
| `/knowledge/timeline` | `views/knowledge/TimelineView.vue` | 战争时间轴 |
| `/knowledge/map` | `views/knowledge/HistoricalMapView.vue` | 历史地图视图 |
| `/knowledge/relation-analysis` | `views/knowledge/RelationAnalysis.vue` | 关系分析 |
| `/knowledge/search` | `views/knowledge/GlobalSearch.vue` | 全局搜索 |
| `/knowledge/text-extract` | `views/knowledge/TextEntityExtract.vue` | 文本实体识别（`requiresRole: 'editor'`，会消耗 LLM 配额） |
| `/knowledge/inference` | `views/inference/index.vue` | **历史问答助手**（旧版智能问答，SSE 流式；`requiresRole: 'editor'`，入口已从导航栏下线，由 RAG 问答承接） |
| `/knowledge/rag` | `views/knowledge/RagAssistant.vue` | **RAG 智能问答**（iframe 承载 RAG 服务页面） |
| `/admin/users` | `views/admin/UserManagement.vue` | **用户管理**（`requiresRole: 'admin'`；改角色，不能改自己） |
| `/error/{401,403,404,500}` | `views/error/*.vue` | 错误页 |

## 目录结构

```text
frontend/src/
├── api/
│   ├── http.ts            # 唯一的 axios 封装：注入 token、统一解包 response.data
│   └── module/            # 按业务分组的接口函数（commone / user / workspace）
├── config/index.ts        # baseURL（同源相对路径 `/`）、ragBase（/rag/）、timeout
├── layouts/               # BasicLayout + global/ 下的头部、菜单、标签页、设置
├── library/               # 通用工具（treeUtil 等）
├── router/                # index.ts（守卫）+ module/base-routes.ts（全部路由）
├── store/                 # Pinia：app（标签页主题）、user（token/菜单/权限）
├── styles/                # 全局样式
├── types/                 # TS 类型（result / user）
├── utils/
│   ├── knowledge.ts       # 实体类型/字段标签映射、关系属性分组等业务工具
│   ├── userScopedStorage.ts # 按账号隔离的 localStorage 读写（问答/识别记录）
│   ├── apiError.ts        # 从 axios 失败响应里取后端 msg（错误提示统一入口）
│   └── date.ts            # 对话时间展示
└── views/                 # 页面（inference / knowledge / knowledge-list / workspace / admin / login / error）
```

## 两条必须知道的约定

### 1. 加菜单项要改三处

菜单的**唯一事实源是后端 `backend/app.py` 的 `get_menu()`**（按角色裁剪：viewer 不下发数据运营组）。
2026-09-25 移除了 mockjs——它此前在开发态拦下 `/user/menu` 返回硬编码菜单、不感知角色，
曾导致"开发态菜单与生产不一致、加菜单要改三处"。

新增或调整菜单项，下面三处都要满足，否则菜单会缺项或点进去 404：

1. 菜单数据：`backend/app.py` 的 `get_menu()`；
2. `src/store/user.ts` 的 `mergeWorkspaceMenus` —— id 白名单，不在清单里的项会被 `.filter(Boolean)` **静默丢弃**；
3. `src/router/module/base-routes.ts` —— 路由，否则点进去是 404。

### 2. `axios` 封装只有一处

全部请求走 `src/api/http.ts`（`Http`）：请求拦截注入 token，响应拦截统一返回 `response.data`，
并在 **HTTP 401**（或响应体 `code === 401`）时清空登录态并跳登录页。

后端鉴权状态码：未登录/Token 过期或伪造 → `401`；Token 有效但无写权限（`viewer` 角色调写接口）→ `403`。

## 受控入口与按账号隔离（2026-09-25）

### 入口的角色拦截是三层的

`/workspace/*`、`/knowledge-list/*`、`/knowledge/text-extract`、`/knowledge/inference`、`/admin/users`
都有 `meta.requiresRole`。三层口径必须一起改（后端鉴权是真正防线，前两层是体验）：

1. 后端 `get_menu()` 不下发菜单项；
2. 路由 `meta.requiresRole` + `router/index.ts` 的 `ROLE_RANK` 比对（URL 直达也拦）；
3. 接口 `require_write_role` / `require_admin` 的 403。

### 问答/识别记录按账号隔离

聊天记录与文本识别记录原先存在全局 key（`chatHistory` / `extractHistory`），同一浏览器上
换账号会看到同一个人的记录。现在 key 拼账号 id（`chatHistory:u3`），由
`utils/userScopedStorage.ts` 统一读写：

- 账号未知但**已登录**（token 在、`userinfo` 没拉到）时，既不读也不写公共桶——那份数据不知道属于谁；
- 页面在挂载时**钉住**本次所属的账号 id（`scopedUid`），不再实时读 `userStore.userInfo`：
  登出与 token 过期都是"先清 userInfo、再跳登录页"，若卸载落盘时才读，key 会退回全局 key，
  把整份记录写进公共桶（第 6 轮审核 H1）；
- 升级前那份全局记录会被归档到 `chatHistory:legacy-archived`，**不归属任何账号**。

RAG 问答页还有一条跨应用的身份通道：`RagAssistant.vue` 在 iframe load 后通过
`postMessage` 把 `{type:'cw-user', uid, role}` 发给 RAG 前端，见
[../docs/集成与入口约定.md](../docs/集成与入口约定.md) 第四节。

## 开发规范

- 组件用 Composition API + `<script setup>`；样式加 `scoped`
- 页面组件放 `views/`，通用组件放 `components/`，接口放 `api/`，类型放 `types/`
- 命名：组件与文件名用 PascalCase，变量用 camelCase，常量用 UPPER_SNAKE_CASE
- 图谱/地图类页面复用现成封装：关系图用 `views/knowledge/graph/EChartsGraph.vue`，
  问答内的子图用 `views/inference/components/KgGraph.vue`；
  **新增关系维度只需在 `EntityGraph.vue` 的 `GRAPH_CONFIGS` 加一项 + 注册路由**，不要再复制页面

## 常见问题

| 现象 | 原因 |
| --- | --- |
| 接口请求失败 | 旧后端未启动，或 `src/config/index.ts` 的 baseURL 推导出的主机/端口不对 |
| 改了菜单页面上不出现 | 见上方「加菜单项要改三处」 |
| 智能问答无响应 | 旧版问答需要 Ollama 常驻且已拉取 `deepseek-r1:7b`（`ollama serve` / `ollama pull`） |
| `/rag/` 页面白屏 | RAG 前端 dist 不是并入模式构建产物，见 [../docs/集成与入口约定.md](../docs/集成与入口约定.md) |
| 打包后资源路径错误 | `vite.config.ts` 的 `base` 为 `/static/`，生产需 nginx 按该前缀发文件 |
