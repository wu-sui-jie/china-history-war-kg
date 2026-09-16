# frontend（RAGv3）

**归属功能：F01 智能问答主界面 + F07 可视化知识面板（单页）。**

RAG 系统唯一用户可见页面：左侧对话区（流式回答 + 过程状态 + 引用 + 实体识别与纠正）、
右侧知识面板（实体卡 / 图谱子图 / 时间线 / 地点列表降级 / 引用证据）、顶部朝代与战争类型
筛选；数据全部来自 RAG 后端 SSE（`POST /api/query`）与普通接口（`/api/health`、`/api/dicts`），
不依赖旧 `china-war/frontend/` 后台模板。

> 需求与验收：`../docs/features/01-qa-main.md`、`../docs/features/07-knowledge-panel.md`；
> 阶段说明与对接细节：`../docs/RAG_v1/RAGv3-开发说明.md`、`../docs/RAG_v1/RAGv3-规划分析.md`；
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

## 目录结构

```text
frontend/
├── index.html / vite.config.ts / tsconfig*.json
├── package.json
└── src/
    ├── main.ts                  # 入口（pinia + App）
    ├── App.vue                  # 顶栏 + 对话列 + 知识面板（窄屏抽屉）
    ├── styles.css               # 全局样式
    ├── types/contract.ts        # 前后端契约 TS 类型（镜像 data-contract）
    ├── api/
    │   ├── sse.ts               # fetch 流解析 data: 行 + AbortController
    │   └── http.ts              # /api/health、/api/dicts
    ├── stores/session.ts        # 会话持久化 + 问答状态机 + 纠正/取消/筛选
    └── components/
        ├── chat/                # F01：ChatInput / ChatPane / MessageBubble / MarkdownContent
        ├── panel/               # F07：EntityCards / SubGraph / Timeline / Places / Evidence / Empty / Pane
        └── ui/                  # FiltersBar（朝代/战争类型多选）、ToastView
```

## 关键交互规则

- 会话保存在 localStorage（`ragv5-session-v2`，带 `schemaVersion`；旧键 `ragv3-session-v1` 会自动迁移，
  更高版本或损坏的数据隔离到 `ragv5-session-quarantine`），刷新保留历史与已收到的正文（中断轮恢复为
  `interrupted`）；流式正文按 ~800ms 节流落盘；清空会话新建 session_id。
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
- [x] F07 知识面板（实体卡/图谱子图/时间线/地图或地点降级/引用证据，均有空态降级）
- [x] SSE 流式 + 纠正交互（替换/移除/新增实体、按实体重查）+ 朝代/战争类型筛选
- [x] 移动端降级（面板抽屉）+ 后端 /api/dicts 联调
