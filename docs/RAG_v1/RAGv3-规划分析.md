# RAGv3 规划分析：前端问答页 F01 + 知识面板 F07

- 阶段：RAGv3（前端单页）
- 状态：📋 规划中（本文件为任务分析，非完成记录）
- 前置：RAGv2 已完成（SSE 问答链路，见 [RAGv2-在线问答链路.md](RAGv2-在线问答链路.md)）
- 目标：实现**唯一用户可见页面**——智能问答页（F01 主界面 + F07 知识面板同页），
  消费 RAGv2 的 SSE 事件流，支持多轮追问、朝代/战争类型筛选、实体纠正重查、
  引用溯源与知识面板（图谱子图/时间线/地图/证据）。

## 一、范围一句话

新建**独立前端**（Vue 3 + TS + Vite），单页问答：
左侧对话区（流式回答 + 过程状态 + 引用 + 实体纠正）＋右侧知识面板区（F07），
顶部筛选；与 RAG 后端（server/api.py）同源或跨域联调，数据全部走 SSE
（data-contract.md）。**不复用旧 frontend 的后台模板壳**（登录/菜单/mock/多页后台），
但可复用其成熟依赖选型。

## 二、为什么必须新建前端（而非改造旧 frontend）

旧 `frontend/` 是 `layui-vue-admin` 后台模板（axios 封装含登录态、mockjs、
layouts 布局/路由守卫/多页后台），与 F01 验收冲突：

| F01 验收 | 旧后台模板 | 结论 |
| --- | --- | --- |
| 页面无旧版后台菜单/模板内容 | 含 login/layouts/router 后台壳 | 冲突 → 不套壳 |
| 打开即问答、无需登录 | axios http.ts 带 token/401 逻辑 | 冲突 → 独立 API 层 |
| 单页问答 + 面板 | 多页后台 + mock | 不匹配 → 独立 SPA |

RAGv3 新建 `RAG/frontend/`（与旧 frontend 完全隔离），技术选型沿用旧项目已验证的
Vue 3 + TS + Vite + pinia + echarts + relation-graph-vue3（RAG 前端依赖独立声明，
不读旧 package.json）。

## 三、页面形态与信息架构

```text
┌─────────────────────────────────────────────────────────────┐
│ 顶栏：RAG 标题 | 朝代筛选(多选) | 战争类型筛选(多选) | 清空会话  │
├───────────────────────────────┬─────────────────────────────┤
│ 左：对话区（F01）              │ 右：知识面板（F07）          │
│  · 消息流（user/assistant）    │  · 实体信息卡                │
│  · 过程状态条（识别→图谱→文本→  │  · 图谱子图(可点节点追问)     │
│    融合→生成 实时反馈）          │  · 时间线                   │
│  · 流式回答 + 引用 [n] 可点击   │  · 地图/地点列表(降级)        │
│  · 实体识别卡 + 纠正按钮        │  · 引用与证据(冲突提示)       │
│  · 候选实体下拉纠正            │                             │
└───────────────────────────────┴─────────────────────────────┘
```

- 窄屏（移动端）降级：面板收起为标签/抽屉，满足"尽可能适配移动端"。
- 组件不互相遮挡；数据全部来自 SSE panel/事件，前端不自行拼第二套 subgraph。

## 四、后端已就绪的对接面（真实负载核对 2026-09-04）

RAGv2 SSE 实际推送（`server/sse.py`，字段与 data-contract 一致）：

| 事件 | 负载（data 字段） | 前端用途 |
| --- | --- | --- |
| `session_start` | 无 data，stage=start | 建立本次流 |
| `status` | stage ∈ entity_linking/graph_search/text_search/fusion/generating/cache_hit | 过程状态条 |
| `entities` | {entities[], candidates[], question_type, rewritten_question, elapsed_ms} | 实体卡 + 纠正候选 |
| `graph_results` | {evidence[] (graph_triple), hit_entities[]} | （内部/可折叠调试） |
| `text_results` | {evidence[], mode} | （内部） |
| `fusion` | {evidence_count, conflicts[], citation_index[]} | 冲突提示（可选） |
| `answer` | {delta}（多段增量） | 流式正文 |
| `citations` | {citations[], conflicts[]}；citation={index,evidence_id,kind,title,snippet} | 引用列表 + "存在不同说法" |
| `panel` | {entity_cards[], subgraph{nodes[],edges[]}, timeline{groups[]}, map_points[]} | F07 直接消费 |
| `done` | {finish_reason, model_used?, cache_hit?} | 结束当前流 |
| `error` | {error_code, message} | 错误提示 |

细节：
- entities 项含 name/type/standard_name/confidence/entity_id/dynasty（前端纠正用 standard_name 回传）；
- candidates = [{mention, entity_type, options:[{name, standard_name, confidence, dynasty, event_type, entity_id}]}]；
- 引用 answer 中的 `[n]` ↔ citations[n-1].index ↔ 点击展开 snippet（snippet 现为事件卡
  description 片段；graph_triple 的 snippet 为空 → 前端对 graph 类用 title 展示）；
- map_points 现为 0（RAGv1 坐标覆盖率为 0）→ 地图模块**必须降级为地点列表**（规划已约定）；
- panel.timeline.groups 已按朝代分组，末组"时间不详/仅知朝代"由后端生成，前端直接渲染。
- `contracts/sse.py` 保留了 `thinking` 事件枚举，但 RAGv2 后端当前不发射该事件；
  前端状态机无需处理，避免误以为事件缺失。

## 五、功能模块拆解（任务级）

### 1. 工程初始化
- 建 `RAG/frontend/`（Vite + Vue3 + TS + pinia），独立 package.json；
- vite dev proxy `/api → http://127.0.0.1:8000`（联调期免 CORS）；build 产物可被后端静态托管或独立部署；
- 依赖：vue/pinia/vue-router(可不要，单页)/echarts/relation-graph-vue3(或自绘 SVG)/markdown-it/axios(仅 health)。

### 2. SSE 客户端与状态机（核心难点）
- `api/sse.ts`：`fetch` + `ReadableStream` 解析 `data: ` 行（或 EventSource——但 POST+body 需 fetch 流，
  因携带 question/history/filters 用 POST，**选 fetch 流**，EventSource 只支持 GET）；
- 事件分发：按 type 路由到 store；多段 answer 增量**追加**到当前消息；
- 流取消：AbortController 中断 fetch → 通知后端断连（done 收不到属预期）；
- 错误/重连：网络中断给用户提示，不阻塞历史。

### 3. 会话与状态（stores/session.ts）
- 本地持久化（localStorage/pinia-plugin-persistedstate），刷新保留本轮历史；
- 每轮消息结构：{id, role, question, filters, answer 全文, citations[], conflicts[],
  entities[], candidates[], panel?, status 过程记录, createdAt}；
- 请求体组装：session_id（首次生成 uuid）+ question + history（近期 ≤4 轮转
  {role,content}）+ filters + corrected_entities（纠正时带）；
- "清空会话"：清本地 + 新 session_id（后端不存会话，靠前端携带）。

### 4. 对话区组件（components/chat/）
- `ChatInput.vue`：输入框 + 发送（Enter）、禁用态（流进行中）、取消按钮；
- `MessageBubble.vue`：用户/助手气泡；助手正文 markdown 渲染（markdown-it）；
- `AnswerDelta` 处理：answer 事件累积；引用 `[n]` 转可点 span；
- `StatusBar.vue`：接收 status 事件 → 显示当前阶段（识别中/图谱检索/文本检索/融合/生成/缓存命中）；
- `Citations.vue`：引用列表（citations 事件）；点击引用 → 右侧/弹层显示 snippet 原文 + source_version；
- 冲突提示：citations.conflicts / fusion.conflicts 非空 → 该轮气泡显示"存在不同说法"徽标。

### 5. 实体识别与纠正交互（components/chat/ 内）
- entities 事件 → 消息头部实体 chips（standard_name + type + dynasty）；
- 点击实体 chip → "纠正"菜单：候选替换（candidates.options 下拉）／移除／新增实体；
- 纠正动作构造 corrected_entities（add/replace/remove，字段按 data-contract）；
- **纠正流程**：AbortController 取消当前进行中的流 → 携带 corrected_entities + 上一轮
  history 重新 POST（不修改已结束历史回答）。

### 6. 筛选条件（顶栏 FiltersBar.vue）
- 朝代/战争类型多选：**已拍板走后端 `GET /api/dicts`**（RAGv1 dicts.json 有
  dynasty_aliases/event_type_standard，但 RAGv2 未暴露词典接口；不采用前端内置清单）；
- 选中 filters 随每个新请求发送，图谱+文本都过滤。RAGv2 复核修复后已具备：
  图谱按节点 dynasty/event_type 过滤，文本检索接收 filters 并按索引中的
  dynasty/event_type 元数据过滤（raw/evidence 暂无事件元数据时不精确参与事件筛选）。

### 7. 知识面板（components/panel/，F07）
- `EntityCards.vue`：panel.entity_cards 卡片（事件/人物/组织/地点字段）；
- `SubGraph.vue`：panel.subgraph → 用 relation-graph-vue3 或 echarts graph 绘制；
  节点点击 → 生成"介绍一下 XX"或"XX 参与了哪些战争"追问（F07 需求）；
- `Timeline.vue`：panel.timeline.groups → 竖排时间线（按朝代分组展示；无年份条目入"不详"组，
  不强行排时间轴）；
- `MapView.vue`：panel.map_points 有坐标 → 地图标记（现代底图）；
  **当前 map_points=0 → 渲染地点列表降级**（从 entity_cards 地点 / graph 地点节点取名字列表）；
- `EvidencePanel.vue`：引用证据原文列表 + 展开；
- 面板策略：默认显示 实体卡 + 图谱子图 + 引用证据；时间线/地图按 panel 数据有无条件显示。

### 8. 后端小增强（RAGv3 联调前置，改动小）
- `GET /api/dicts`（已定，不再二选一）：返回朝代/事件类型标准清单，并附 `version`
  字段（读快照 dicts.json，runtime 缓存）；前端用于筛选下拉与数据版本展示；
- （可选）`GET /api/health` 已存在，前端启动探测用。

### 9. 质量与验收准备
- 冒烟脚本（Node/curl）跑通完整 SSE 序列渲染；
- 手动验收清单映射 F01/F07 验收标准。

## 六、实施顺序（建议）

1. 工程初始化 + Vite proxy + health 探测 → 空页可跑。
2. SSE 客户端（fetch 流解析 + 事件分发 + Abort）→ 用 curl/后端日志对照。
3. 会话 store + 消息流渲染（answer 增量/status 状态条/citations 引用）。
4. 对话区（输入/取消/多轮/筛选随请求发送）。
5. 实体识别卡 + candidates 下拉 + 纠正重查（取消当前流 → 重发）。
6. 知识面板：实体卡 → 图谱子图(可点) → 时间线 → 引用证据 → 地图(降级地点列表)。
7. 后端补 `GET /api/dicts`（决策已定）→ 筛选下拉接真数据。
8. 验收：F01/F07 功能文档验收标准逐条过 + 移动端基本可用。

## 七、依赖与前置

1. RAG 后端可启动（RAGv2 已完成）；无 LLM key 时 F06 走离线摘要回答器，前端不影响验收。
2. Node ≥ 18（本机 v22.19 / npm 10.9.3 ✓）；npm registry 可访问（需装 vite/vue 等）。
3. 坐标系/地图：现代底图需要外部 tile（如高德/OSM）或纯 SVG 占位；**初版建议用"地点列表+坐标点"
   不引重型地图库**，避免 key/网络依赖（RAGv1 坐标覆盖为 0，地图非重点）。
4. 图谱绘制：relation-graph-vue3（旧项目已用）或 echarts graph——若前者体积大，可自绘 SVG；
   待实施时定，列为可配置项。
5. 项目约定：不修改旧 backend/frontend/entity-event-relation 代码；RAG/frontend 全新。

## 八、风险与对策

1. **SSE POST 流解析**（EventSource 不支持 POST body）：fetch + ReadableStream 手动解析
   `data:` 行。风险中 → 先做最小解析器冒烟再铺 UI。
2. **引用与增量对齐**：answer 增量按句切分，引用 `[n]` 可能跨 delta → 前端在**整段累积后**
   统一做 `[n]` 高亮，避免流式过程中引用断裂。
3. **后端 snippet 为空（graph 类）**：引用点击展示兜底 title/三元组文本。
4. **图谱节点多（24+）**：SubGraph 布局需限制/缩放；点击命中中心实体优先。
5. **dicts 无接口**：后端补 `GET /api/dicts`（低风险，已拍板；返回清单 + version，
   保证与治理词典一致）。
6. **跨域**：dev proxy 解决；生产同源部署或 CORS（后端已开 allow_origins=*）。
7. 移动端：面板改抽屉/标签，防遮挡。

## 九、不在 RAGv3 范围

- F08 演示模式、F10 评测体系（更后阶段）；
- 历史地图底图素材与授权（后续评估）；
- 账号/权限/多用户隔离（页面本地保存，无账号体系）。

## 十、验收对照（来自 F01/F07 功能文档）

- F01：打开即问答无登录；回答带引用；关键检索步骤可见；无旧后台内容；筛选影响图谱+文本。
- F07：演示问题知识面板不空白；各模块互不遮挡；无坐标不报错（降级地点列表）；
  点击图谱节点可发起新追问；时间/坐标缺失有明确降级展示。
- 非功能：本地检索链路首 Token 已达标（RAGv2 实测 30~50ms 无 LLM）；前端渲染不阻塞流。
