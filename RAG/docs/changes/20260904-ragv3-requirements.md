# RAGv3（第三阶段）需求分析文档

- 日期：2026-09-04
- 项目：RAG 智能问答（`RAG/` 子项目）
- 状态：已确认并执行
- 触发来源：RAGv2 已完成、RAGv3 预开发分析（[../RAG_v1/RAGv3-规划分析.md](../RAG_v1/RAGv3-规划分析.md)）

## 一、问题与目标（先全量总结）

第三阶段的目标是交付 RAG 系统唯一用户可见页面：**智能问答页（F01 主界面 + F07 知识面板同页）**，
并补齐联调前置的后端小接口与全套阶段文档。当前现状：

1. **RAGv2 后端已完成**：`POST /api/query` SSE 问答链（F02→F03/F04→F05→F06）与
   `GET /api/health` 已跑通；SSE 事件含 `session_start/status/entities/graph_results/text_results/
   fusion/answer/citations/panel/done/error`，缓存命中与全量检索两条路径均已复测。
2. **前端只有占位 README**（`RAG/frontend/README.md`），尚无 Vue/TS 工程与页面代码；
   旧 `china-war/frontend/` 是 layui-vue-admin 后台模板，与 F01/F07 验收冲突，不得复用。
3. **RAGv3 规划已拍板**：新建 Vue 3 + TypeScript + Vite + pinia 单页，SSE 用
   fetch + ReadableStream 解析（EventSource 不支持 POST body），知识面板只消费
   `panel` 事件数据，地图在当前 `map_points=0` 下降级为地点列表，引用 `[n]` 在
   整段累积后统一高亮，需要后端补 `GET /api/dicts`。
4. **当前快照/索引版本**：`20260904_v2`（`dicts.json` 含 `event_type_standard` 27 项、
   `dynasty_aliases` 100+ 键），RAGv2 文本/图谱均已按 filters 过滤。

本阶段要完成：

- **RAGv3 前端实现**（F01 + F07）：工程初始化、SSE 客户端与状态机、会话持久化、
  对话区（流式/状态/引用/实体纠正/筛选）、知识面板（实体卡/图谱子图/时间线/地图降级/
  引用证据）、移动端降级；
- **后端小增强**：`GET /api/dicts`（朝代/战争类型标准清单 + 数据版本）；
- **开发文档全套**：需求分析（本文件）、阶段开发说明、修改总结、文档索引与状态同步；
- **验证与 Git 固化**：后端启动 + 接口冒烟、前端 build + dev 冒烟、新建分支推送 RAG 仓库。

## 二、修改方案

### 1. 后端：`GET /api/dicts`

- 位置：`RAG/server/api.py`（新增路由），从 `runtime.snapshot_dir/dicts.json` 读取。
- 返回结构（与 RAGv3 规划文档一致）：
  `{version, generated_at, data_version, dynasty: [{standard, aliases}], event_type: [...], sources: {...}}`；
  `dynasty` 由 `dynasty_aliases` 键生成，`event_type` 取 `event_type_standard`。
- runtime 未加载时返回 `status=error`（HTTP 503），正常返回 `status=ok`。
- 不新增依赖；同步更新 `server/README.md`、api.py docstring。

### 2. RAGv3 前端工程（`RAG/frontend/`，独立声明依赖）

技术选型（按规划 + 旧项目验证）：Vue 3、TypeScript、Vite、pinia、echarts、
markdown-it、@vueuse/core（clipboard/媒体查询），不引 vue-router（单页）。
图谱用 ECharts graph（避免 relation-graph 体积与版本风险），节点点击发起追问。

模块清单（对应规划分析"五、功能模块拆解"）：

| 模块 | 文件 | 说明 |
| --- | --- | --- |
| 工程配置 | `package.json`、`vite.config.ts`、`tsconfig*.json`、`index.html`、`src/env.d.ts` | Vite dev proxy `/api → http://127.0.0.1:8000` |
| 类型契约 | `src/types/contract.ts` | QueryRequest/SSE/panel/filters/dicts 等 TS 类型（镜像 data-contract） |
| SSE 客户端 | `src/api/sse.ts` | fetch 流解析 `data:` 行 → 事件分发 → AbortController 取消 |
| 后端 API | `src/api/http.ts` | `GET /api/health`、`GET /api/dicts` |
| 会话状态 | `src/stores/session.ts` | localStorage 持久化、消息模型、发起/取消/纠正、历史组装 |
| 主页面 | `src/App.vue` | 顶栏 + 左对话 + 右知识面板 + 窄屏抽屉 |
| 对话组件 | `src/components/chat/*.vue` | Input/Message/Status/Entities/Citations |
| 面板组件 | `src/components/panel/*.vue` | EntityCard/SubGraph/Timeline/Places/Evidence/Empty |
| 全局样式 | `src/styles.css` | 单页问答工具风、紧凑不遮挡、移动端面板抽屉 |

关键交互规则：

- 每轮消息保存问题/完整回答/引用/实体/候选/panel/过程状态/错误；刷新后从 localStorage 恢复。
- answer 增量在整段累积后渲染，`[n]` 统一转为可点引用。
- 实体纠正：点击实体 chip → replace/remove；候选歧义 chip 下拉选择替换；
  纠正确认时若流进行中先 Abort，再用原问题 + 上一轮 history + corrected_entities 重发，
  不修改已结束历史。
- 窄屏下面板切换为抽屉/标签（不遮挡、可关闭）；图谱/时间线/地点缺失有文字降级。

### 3. 文档更新

- `docs/RAG_v1/README.md`：索引追加 RAGv3 开发说明，状态改已完成。
- `docs/RAG_v1/RAGv3-开发说明.md`（新建）：阶段开发说明（做了什么/文件映射/验证结果/
  边界/复现），风格与 RAGv2 开发说明一致。
- `docs/README.md`、`docs/features/01-qa-main.md`、`docs/features/07-knowledge-panel.md`：
  F01/F07 状态改"已完成（RAGv3）"。
- `docs/changes/20260904-ragv3-summary.md`（新建）：总结分析文档（本阶段）。
- `RAG/frontend/README.md`：由"规划中"改为工程说明 + 运行方式。

### 4. 验证

- Python：`GET /api/dicts` 与健康检查；后端正常启动并加载 `20260904_v2`。
- 前端：`npm install`、`npm run build`（类型检查 + 产物）、`npm run dev` 后
  用页面冒烟（后端在线时发问；无 LLM key 也可验证离线回答器与面板）。
- 阶段文档验收对照：F01/F07 验收标准逐条说明。

## 三、边界与不在本阶段

- 不修改旧 `backend/`、`frontend/`、`entity-event-relation/` 代码。
- 不实现 F08 演示模式、F10 评测体系（后续阶段）。
- 前端生产部署形态（静态托管/后端同源）只写运行方式，不搭 CI/容器。
- 真实 LLM key 不在本机验证范围（无 key 时链路按 RAGv2 既有边界运行）。
