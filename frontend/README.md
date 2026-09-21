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

访问 **http://localhost:3001**。端口在 `vite.config.ts` 中固定为 3001（不是 Vite 默认的 5173），
并已把 `/api`、`/rag` 两条代理配好——所以浏览器只需要这一个地址。

```bash
pnpm build        # 生产构建，产物到 dist/
```

> 若用镜像源更快的场景：`npm config set registry https://mirrors.huaweicloud.com/repository/npm/`。

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
| `/knowledge/text-extract` | `views/knowledge/TextEntityExtract.vue` | 文本实体识别 |
| `/knowledge/inference` | `views/inference/index.vue` | **历史问答助手**（旧版智能问答，SSE 流式） |
| `/knowledge/rag` | `views/knowledge/RagAssistant.vue` | **RAG 智能问答**（iframe 承载 RAG 服务页面） |
| `/error/{401,403,404,500}` | `views/error/*.vue` | 错误页 |

## 目录结构

```text
frontend/src/
├── api/
│   ├── http.ts            # 唯一的 axios 封装：注入 token、统一解包 response.data
│   └── module/            # 按业务分组的接口函数（commone / user / workspace）
├── config/index.ts        # baseURL（按当前 host 推导 :5000）、ragBase（/rag/）、timeout
├── layouts/               # BasicLayout + global/ 下的头部、菜单、标签页、设置
├── library/               # 通用工具（treeUtil 等）
├── mockjs/                # Mock 数据（**菜单与权限实际来源**，见下）
├── router/                # index.ts（守卫）+ module/base-routes.ts（全部路由）
├── store/                 # Pinia：app（标签页主题）、user（token/菜单/权限）
├── styles/                # 全局样式
├── types/                 # TS 类型（result / user）
├── utils/
│   ├── knowledge.ts       # 实体类型/字段标签映射、关系属性分组等业务工具
│   └── date.ts            # 对话时间展示
└── views/                 # 页面（inference / knowledge / knowledge-list / workspace / login / error）
```

## 两条必须知道的约定

### 1. 加菜单项要改三处

`src/main.ts` 无条件 `import './mockjs'`，mockjs 在 XHR 层拦下了 `/user/menu`、`/user/permission`，
所以**侧边栏菜单实际渲染的是 `src/mockjs/user.ts` 里的硬编码数据**，不是 `backend/app.py` 的
`get_menu()`（那份接口至今未被这个前端调用过）。只改后端的菜单项不会出现在界面上。

1. `src/mockjs/user.ts` —— 菜单数据（实际生效的源）；
2. `src/store/user.ts` 的 `mergeWorkspaceMenus` —— id 白名单，不在清单里的项会被 `.filter(Boolean)` **静默丢弃**；
3. `src/router/module/base-routes.ts` —— 路由，否则点进去是 404。

（若哪天真去掉了 mockjs，`backend/app.py` 的 `get_menu()` 才成为菜单源，届时同样要满足第 2、3 条。）

### 2. `axios` 封装只有一处

全部请求走 `src/api/http.ts`（`Http`）：请求拦截注入 token、无 token 时跳登录页，
响应拦截统一返回 `response.data`。**判断接口成败要看响应体里的 `code` 字段**——
旧后端在未鉴权时返回 `{"code": 403, ...}` 但 **HTTP 状态码是 200**，只看 HTTP 层会误判。

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
