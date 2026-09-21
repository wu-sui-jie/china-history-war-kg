# frontend（RAGv3）

**归属功能：F01 智能问答主界面 + F07 可视化知识面板（单页）。**

RAG 系统唯一用户可见页面：左侧提问历史栏（可折叠，点任意一轮回到该轮问答）、中间对话区
（流式回答 + 过程状态 + 引用 + 实体识别与纠正）、右侧知识面板（实体卡 / 图谱子图 / 时间线 /
地点列表降级 / 引用证据）、顶部朝代与战争类型筛选；数据全部来自 RAG 后端 SSE（`POST /api/query`）
与普通接口（`/api/health`、`/api/dicts`），不依赖旧 `china-war/frontend/` 后台模板。

> 需求与验收：`../docs/features/01-qa-main.md`、`../docs/features/07-knowledge-panel.md`；
> 阶段交付与对接细节：`../docs/CHANGELOG.md`；
> 前后端数据契约：`../docs/data-contract.md`。

## 技术栈

- Vue 3 + TypeScript + Vite（build 走 `vue-tsc` 类型检查）+ pinia；
- echarts（图谱子图，graph 力导）+ markdown-it（回答渲染）+ @vueuse/core（媒体查询）；
- 无 vue-router（单页）；SSE 用 `fetch` + `ReadableStream` 解析（EventSource 不支持 POST body）。

## 运行方式

前置：RAG 后端已启动（`python scripts/run_server.py --port 8000`），无 LLM key 也能跑
检索链 + 离线回答器。

```bash
cd RAG/frontend
npm install
npm run dev          # http://127.0.0.1:5173（/api 代理到 127.0.0.1:8000）
npm run build        # 类型检查 + 产物到 dist/（可静态托管）
npm run preview      # 预览构建产物
```

## 并入旧知识库系统 Web 入口（可选）

RAG 也可以挂到旧后台（layui 管理台）的 `/rag/` 子路径下，由反代把 `/rag/*` 转发给本服务，
用户从旧后台菜单「RAG 智能问答」进入——见 `../../docs/集成与入口约定.md`。

```bash
npm run build:integration   # base=/rag/ + 接口前缀=/rag/api（参数在 .env.integration）
```

并入模式只改构建期参数（`VITE_BASE_PATH` / `VITE_API_BASE`），服务端与数据链路不动。
`npm run build` 仍是独立部署口径（base `/`、接口前缀 `/api`）；两个模式的 dist 互斥，
切换后必须重新构建，否则页面与接口前缀对不上。

## 目录结构

```text
frontend/
├── index.html / vite.config.ts / tsconfig*.json
├── package.json
└── src/
    ├── main.ts                  # 入口（pinia + App）
    ├── App.vue                  # 顶栏 + 历史栏 + 对话列 + 知识面板（窄屏抽屉）
    ├── styles.css               # 全局样式
    ├── types/contract.ts        # 前后端契约 TS 类型（镜像 data-contract）
    ├── api/
    │   ├── base.ts              # 接口前缀（默认 /api，并入模式 /rag/api）+ 路径拼接
    │   ├── sse.ts               # fetch 流解析 data: 行 + AbortController
    │   └── http.ts              # /api/health、/api/dicts
    ├── stores/session.ts        # 会话持久化 + 问答状态机 + 纠正/取消/筛选
    └── components/
        ├── chat/                # F01：ChatInput / ChatPane / MessageBubble / MarkdownContent
        ├── history/             # F01：HistoryPane（提问历史列表，点轮次回到该轮问答）
        ├── panel/               # F07：EntityCards / SubGraph / Timeline / Places / Evidence / Empty / Pane
        └── ui/                  # FiltersBar（朝代/战争类型多选）、ToastView
```

## 关键交互规则

- 会话保存在 localStorage（`ragv5-session-v2`，带 `schemaVersion`；旧键 `ragv3-session-v1` 会自动迁移，
  更高版本或损坏的数据隔离到 `ragv5-session-quarantine`），刷新保留历史与已收到的正文（中断轮恢复为
  `interrupted`）；流式正文按 ~800ms 节流落盘；清空会话新建 session_id。
- 提问历史（`HistoryPane`）：左侧栏列出每一轮（含失败/取消/被重查取代的轮次，各带状态徽标），
  点击即把知识面板切到该轮并滚动定位到聊天区对应消息；面板顶部出现"正在查看历史轮次 + 返回最新"。
  新提问自动回到最新视图；选中的轮次被裁剪/清空后自动回退。点历史消息的引用 chips 会先切到该轮
  （避免引用高亮打到最新轮上）。左栏显隐记忆在 localStorage（`ragv5-ui-history-visible`）。
- answer 增量整段累积后渲染，`[n]` 在正文统一转可点引用（跨 delta 不断）；点击定位证据。
- 实体纠正/按实体重查：显式以某条 assistant 消息为源，走统一的 `beginTurn`（先取消并等待
  前一条流，保证任何时刻只有一条活动流），按原问题追加一轮“纠正重查”；被取代的轮次标记
  `supersededBy` 并退出多轮历史。纠正指令携带 `entity_id`，同名不同朝代的实体按 ID 精确匹配。
- 只有 completed / refused / degraded 进入多轮历史；failed / cancelled / interrupted 一律排除。
- 缓存命中与全量检索是两条 SSE 路径：命中路径无 graph/text/fusion 事件属设计行为。
- 图谱子图画布节点可拖动缩放，追问入口在节点下方 chips（生成“介绍一下 XX”）。
- 地点：有坐标时出地图（`MapView.vue`，echarts geo + 省级底图 `src/assets/china-map.json`，
  点位大小随相关事件数、可缩放拖拽），无坐标自动降级为地点卡片列表（现代地名/省市区/暂无坐标提示）。
  坐标覆盖 4,819/5,316（RAGv5 2026-09-15 起），底图随包走、不依赖外网。

## 状态

- [x] F01 主界面 + 会话（流式/状态/引用/多轮/复制/清空）
- [x] F01 提问历史（任意轮次回看：知识面板 + 聊天区定位；桌面左栏 / 窄屏左侧抽屉）
- [x] F07 知识面板（实体卡/图谱子图/时间线/地图或地点降级/引用证据，均有空态降级）
- [x] SSE 流式 + 纠正交互（替换/移除/新增实体、按实体重查）+ 朝代/战争类型筛选
- [x] 移动端降级（面板抽屉）+ 后端 /api/dicts 联调
