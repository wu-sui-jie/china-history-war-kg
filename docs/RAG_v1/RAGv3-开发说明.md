# RAGv3 开发说明：前端问答页 F01 + 知识面板 F07

- 阶段：RAGv3（前端单页）
- 状态：已完成 ✅
- 完成时间：2026-09-04
- 范围：`docs/README.md` 建议开发顺序第 1 步最后一段——新建 `RAG/frontend/`（Vue 3 + TS + Vite）
  单页问答（F01 主界面 + F07 知识面板同页），消费 RAGv2 SSE 事件流；后端补 `GET /api/dicts`
- 前置：RAGv2 已完成（SSE 问答链路 + 缓存/降级，数据版本 `20260904_v2`）
- 需求文档：[F01](../features/01-qa-main.md)、[F07](../features/07-knowledge-panel.md)、
  规划分析见 [RAGv3-规划分析.md](RAGv3-规划分析.md)

## 一、本阶段做了什么

交付 RAG 系统**唯一用户可见页面**：智能问答页。左侧对话区（流式回答 + 过程状态 + 引用 +
实体识别与纠正），右侧知识面板（实体卡 / 图谱子图 / 时间线 / 地点降级 / 引用证据），顶部
朝代与战争类型筛选；窄屏下面板收成可开关的抽屉。

不复用旧 `china-war/frontend/` 的 layui-vue-admin 后台壳（登录/菜单/mock 与 F01 验收冲突），
依赖选型沿用旧项目已验证的 Vue 3 + TS + Vite + pinia + echarts，图谱用 ECharts graph（避免
relation-graph 的体积与版本风险）。数据只来自 SSE 事件，前端不自行拼第二套 subgraph。

### 完成的功能点

1. **工程（`package.json` / `vite.config.ts` / `tsconfig*.json` / `index.html`）**
   - dev proxy `/api → http://127.0.0.1:8000`，联调免 CORS；`npm run build` 可产物化静态托管；
   - 依赖：vue/pinia/echarts/markdown-it/@vueuse/core，无 vue-router（单页）。
2. **类型契约（`src/types/contract.ts`）**
   - 镜像 data-contract：QueryRequest/history/filters/corrected_entities、SSE 各事件负载
     （entities/candidates/citations/conflicts/panel）、dicts/health 响应、SubGraph/Timeline/
     MapPoint/EntityCard；STAGE_LABELS 与实体类型配色。
3. **SSE 客户端（`src/api/sse.ts`）**
   - EventSource 只支持 GET，而 `/api/query` 需 POST body → `fetch` + `ReadableStream` 按
     `\n\n` 分块、解析 `data:` 行；AbortController 取消（取消时后端收不到 done 属预期）；
   - 网络/解析异常走 onError，与后端 `error` 事件区分。
4. **会话状态（`src/stores/session.ts`）**
   - localStorage（`ragv3-session-v1`）持久化消息流，刷新保留本轮历史与 session_id；
   - `boot()`：/api/health（服务在线/离线模式）+/api/dicts（筛选词典）；
   - 消息模型 user/assistant；assistant 保存完整回答、引用、冲突、实体、候选、panel、
     过程状态、纠正指令、错误；
   - `history` 由“已结束且未取消”的轮次组装，请求携带最近 8 条（后端再按 4 轮裁剪）；
   - 发起/取消/停止流共用单一 active + AbortController；流异常/完成都会解除占用；
   - 事件处理：status 记录阶段（含 cache_hit 正常路径）、entities/candidates 更新、answer
     增量累积、citations+conflicts、panel 规范化、error、done（置 finished 并清 active）。
5. **主页面（`src/App.vue`）**
   - 顶栏（品牌 + FiltersBar + 清空会话）＋ 左对话列 + 右面板列；
   - `useMediaQuery(max-width: 980px)`：窄屏下面板改为抽屉 + 遮罩，可开关、不遮挡消息。
6. **对话区（`src/components/chat/`）**
   - `ChatInput.vue`：Enter 发送（Shift+Enter 换行、IME 组合不误触）、自动增高、流进行中切换
     “停止”按钮；
   - `ChatPane.vue`：消息流 + 欢迎页（示例问题可点）+ 自动滚动（贴近底部才跟随）；
   - `MessageBubble.vue`：用户气泡（含该轮筛选 chips）；助手气泡含——
     - 纠正说明、实体 chips（标准名/类型/朝代），**纠正菜单只在最新一轮回答可操作**：
       替换候选（下拉）、移除、新增实体（手输标准名重查）；较早回答的 chips 只读展示；
     - 过程状态条（正在识别实体/检索图谱/检索文本/融合重排/生成回答/缓存命中）；
     - 流式回答 + 错误/异常提示、冲突徽标（存在不同说法）、引用 chips `[n]` 可点、复制回答、
       结束态（已生成/已取消/依据不足/降级生成/异常中断）；
   - `MarkdownContent.vue`：markdown-it 渲染；**整段累积后**扫描纯文本节点，把 `[n]`
     替换为可点引用按钮、把实体标准名包成高亮（引用跨 delta 不会断）；
   - 引用点击 → window 事件 `rag:citation` → 面板切到“引用证据”并滚动定位到该条。
7. **知识面板（`src/components/panel/`，F07）**
   - 标签页：引用证据（默认）/ 实体卡 / 图谱子图 / 时间线 / 地点；
   - `EntityCardsView.vue`：实体信息卡（朝代/时间/类型/身份/所属/位置/别名/描述/来源）；
   - `SubGraphView.vue`：ECharts graph 力导布局（类型配色、边标签显示关系、可拖动缩放）；
     节点下方提供“继续提问”chips（点节点名生成 `介绍一下XX` 追问）——设计取舍：画布节点
     保留拖动/缩放，追问入口放图下方按钮，避免拖拽与点击冲突；
   - `TimelineView.vue`：直接渲染后端 `panel.timeline.groups`（已按朝代分组，“时间不详/
     仅知朝代”归末组由后端生成）；组为空/无数据有文字降级；
   - `PlacesView.vue`：`map_points` 有坐标本可上底图，当前坐标覆盖率为 0 → 与地点实体卡
     合并成**地点列表降级**（现代地名/省市区/暂无坐标均展示），不引重型地图库；
   - `EvidenceView.vue`：引用按 index 列表，展开原文 snippet、冲突内联提示、evidence id；
     接受 `rag:citation` 事件定位高亮；
   - `PanelEmpty.vue`：各模块缺失统一“暂无可展示”占位，面板不空白。
8. **筛选（`src/components/ui/FiltersBar.vue`）**
   - 朝代/战争类型多选下拉，选项来自 `GET /api/dicts`（不内置清单）；
   - 选中 filters 随请求发送，图谱与文本检索均参与过滤（RAGv2 复核修复后已验证）；
   - “清除筛选”一键清空两个筛选集合。
9. **后端小增强（`server/api.py`）**
   - `GET /api/dicts`：读 `runtime.snapshot_dir/dicts.json`，朝代由 `dynasty_aliases` 键生成
     （剔除“不详/未知/无”），战争类型取 `event_type_standard`；返回
     `{status, version, data_version, generated_at, dynasty[], event_type[], sources{counts}}`；
     runtime/词典缺失时 HTTP 503（`status=error`）；
   - 实际返回（20260904_v2）：102 个朝代、27 类战争，与 RAGv1 dicts.json 一致。

## 二、功能 ↔ 文件/文件夹映射

| 功能 | 目录/文件 | 说明 |
| --- | --- | --- |
| 工程配置 | `frontend/package.json`、`vite.config.ts`、`tsconfig*.json`、`index.html` | Vite dev proxy `/api` |
| 类型契约 | `frontend/src/types/contract.ts` | 镜像 data-contract |
| SSE 客户端 | `frontend/src/api/sse.ts` | fetch 流解析 + Abort |
| 后端 API | `frontend/src/api/http.ts` | /api/health、/api/dicts |
| 会话/状态机 | `frontend/src/stores/session.ts` | 持久化/发起/取消/纠正/历史 |
| 主页面/布局 | `frontend/src/App.vue` | 顶栏+对话+面板+窄屏抽屉 |
| F01 对话 | `frontend/src/components/chat/` | Input/Pane/MessageBubble/MarkdownContent |
| F07 面板 | `frontend/src/components/panel/` | EntityCards/SubGraph/Timeline/Places/Evidence/Empty/Pane |
| 筛选/提示 | `frontend/src/components/ui/` | FiltersBar/ToastView |
| 样式 | `frontend/src/styles.css` | 单页问答工具风、移动端抽屉 |
| 后端词典 | `server/api.py` | `GET /api/dicts`（RAGv3） |

## 三、跑通结果（验收对照）

后端启动与接口（Python 3.11，无 LLM key，数据版本 20260904_v2）：

| 验证项 | 结果 |
| --- | --- |
| `GET /api/health` | status=ok、version=20260904_v2 ✅ |
| `GET /api/dicts` | 102 朝代 / 27 战争类型；version/data_version 齐全 ✅ |
| 全量检索 SSE（“赤壁之战的主帅是谁？”） | session_start→status×5→entities→graph_results→
  text_results→fusion→answer×13（增量）→citations→panel→done ✅ |
| 缓存命中 SSE（同问二次） | status(entity_linking)→entities→status(cache_hit)→answer→
  citations→panel→done；命中路径无 graph/text/fusion 属设计行为 ✅ |
| `corrected_entities`（add 人物“曹操”） | entities 追加曹操（entity_id=person_0307），
  面板正常 ✅ |
| panel 负载形状 | entity_cards/subgraph(nodes/edges)/timeline(groups=1)/map_points=0，
  与前端类型一致 ✅ |
| `npm run build`（vue-tsc + vite） | 类型检查通过、产物生成（echarts 单独 chunk）✅ |

前端验收对照（F01/F07 功能文档逐条）：

| F01 验收 | 结果 |
| --- | --- |
| 打开即问答、无需登录 | 欢迎页可直接提问，无路由守卫/登录壳 ✅ |
| 回答带引用 | 正文 `[n]` 可点 + 引用 chips；点击定位证据原文 ✅ |
| 关键检索步骤可见 | 状态条实时展示识别/图谱/文本/融合/生成/缓存命中 ✅ |
| 页面无旧后台内容 | 全新独立 frontend，未复用旧后台模板 ✅ |
| 筛选同时影响图谱+文本 | 下拉来自 /api/dicts，随请求传 filters，RAGv2 双通道已验证 ✅ |
| 会话历史刷新保留 | localStorage 持久化 ✅ |
| 纠正实体后重查 | 替换/移除/新增（含手输标准名）→ 取消进行中流（如有）→ 追加一轮纠正重查，
  不修改已结束历史 ✅ |

| F07 验收 | 结果 |
| --- | --- |
| 演示问题面板不空白 | 各标签页有占位/降级文案 ✅ |
| 模块互不遮挡 | 面板标签页单模块展示；窄屏抽屉可关 ✅ |
| 无坐标不报错 | map_points=0 → 地点列表降级（现代地名/省市区/暂无坐标）✅ |
| 点击图谱节点可追问 | 节点名 chips 生成“介绍一下 XX”追问 ✅ |
| 时间/坐标缺失有降级 | 时间线空组/无数据显示提示；地点无坐标有 fallback 文案 ✅ |

## 四、契约与设计要点（对接时留下的约定）

1. **两条 SSE 结束路径**：全量检索与缓存命中。缓存命中不发射 graph/text/fusion 及其结果
   事件是设计行为，前端把 `status(cache_hit)`/`done.cache_hit` 视为正常路径，不当作丢事件。
2. **answer 增量切句**：引用 `[n]` 可能跨 delta → 正文整段累积后统一做 `[n]` 高亮与实体高亮，
   不在增量渲染中逐个处理。
3. **图谱类引用 snippet 为空**：EvidenceView 展开时以 title 为准并提示“以标题为准”。
4. **时间线结构**：`panel.timeline.groups` 已按朝代分组，末组“时间不详/仅知朝代”由后端
   生成，前端直接渲染；不做二次时间轴排序。
5. **地点降级**：坐标覆盖率为 0，前端不引重型地图库；后续坐标数据到位再评估底图。
6. **实体纠正对象化**：纠正/按实体重查以**显式传入的 assistant 消息**为源（不依赖全局
   正在流消息），因此“刚结束的一轮回答”也可在查看后纠正；消息列表里较早回答的实体 chips
   只读展示（避免误以为可纠正）。这是本阶段补齐交互时的一处修正。

## 五、边界与未做事项（诚实说明）

1. **真实 LLM / 云端 embed 未在本机验证**：F06 走离线摘要回答器、F04 向量模式关闭属
   RAGv2 既有边界；前端不受影响，配 key 后链路上层无改动。
2. **图谱画布节点未直接绑定点击追问**：拖拽/缩放优先，追问以图下节点 chips 提供（见一.7），
   若需画布内点击需后续加“非拖拽位移判定”。
3. **未做自动化前端测试**（vitest/playwright 未引入）：以 build 类型检查 + 页面/接口冒烟
   代替，`tests/` 仍为 Python 层预留。
4. **生产部署形态未落地**：dev proxy 只服务联调；build 产物可静态托管或由后端同源托管，
   未搭 CI/容器（按需求文档边界）。
5. **纠正只作用于最新一轮回答**：历史多轮消息的实体 chips 只读；需要“改历史某轮后全链
   重放”属更深交互，不在本阶段。
6. **无账号体系**：会话仅存本页 localStorage，清空会话即新建 session_id（后端不存会话）。
7. **F08 演示模式 / F10 评测**未在本阶段（后续阶段）。

## 六、如何复现

```bash
# 1) 启动后端（无 LLM key 也能跑检索链 + 离线回答器）
cd RAG
python scripts/run_server.py --port 8000          # Python 3.11（本机 E:/anaconda/envs/AI_Agent）

# 2) 前端依赖与启动（Vite dev proxy 转发 /api → 8000）
cd RAG/frontend
npm install
npm run dev                                        # 打开 http://127.0.0.1:5173

# 3) 生产构建（类型检查 + 产物）
npm run build                                      # dist/ 可静态托管

# 4) 接口冒烟
curl http://127.0.0.1:8000/api/dicts
curl http://127.0.0.1:8000/api/health
# SSE 冒烟：POST /api/query（question 必须 UTF-8；Windows 下用 --data-binary @file 方式避免 GBK 乱码）
```

演示问题：赤壁之战的主帅是谁？/ 介绍长平之战。/ 井陉之战发生在什么时候？/
牧野之战与武王伐纣有什么关系？同问一次可观察缓存命中路径与正常面板展示。

## 七、下一阶段（RAGv4 及以后）

- F10 问答效果评测与问题题库（质量闭环），F08 演示模式；
- 真实 LLM key 联调（F06 真流式）、云端 embed 重建索引后接 F04 向量模式；
- 依据 F10 结果再评估：实体纠正放宽到历史轮、图谱画布节点点击追问、历史地图底图素材。
