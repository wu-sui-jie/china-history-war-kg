# RAG 借鉴旧问答系统：需求分析与规则推理移植设计

- 文档类型：需求分析 + 落地设计
- 状态：生效（四项借鉴均已落地，待决项均已拍板）
- 路径约定：`RAG/...` 指本仓库内文件；`china-war/...` 指旧知识库系统（私有仓库）内的文件。

> 本文档回答两个问题：旧「历史问答助手」有哪些能力值得搬进 RAG、哪些不该搬；
> 以及其中的规则推理能力如何移植。**本文档不包含"删除旧页面"的动作**——旧页保留，
> 待借鉴完成后再单独判断去留。

## 一、结论摘要

四项借鉴，按性价比排序：

| 顺序 | 借鉴项 | 性质 | 工作量 | 落点 |
| --- | --- | --- | --- | --- |
| 1 | 事件实体卡补齐叙事字段（攻方/守方/作战行动/历史影响/地点） | 数据已有、只是没接出来 | 小 | 已完成 |
| 2 | 会话级导出 Markdown | 纯新增功能 | 小 | 已完成 |
| 3 | 多会话管理（新建/切换/删除/自动命名） | 新增功能，会触碰已过审的持久化层 | 中 | 已完成 |
| 4 | 规则推理能力移植（20 条规则、含多跳） | 唯一真正的能力缺口，需单独设计 | 大 | 已完成（离线固化） |

三项明确**不借鉴**：思考过程展示（旧页的是死代码）、无依据时用通用知识回答
（RAG 故意不这么做）、点击实体追问与内嵌图谱（RAG 已具备且更完整）。

## 二、现状盘点：旧问答系统有什么

以下均为核实过的事实，依据见第八节核对表。

### 2.1 前端能力（`china-war/frontend/src/views/inference/index.vue`）

| 能力 | 说明 |
| --- | --- |
| 左侧会话栏 | 会话列表（标题/时间/删除）、新建聊天、折叠；localStorage `chatHistory` 持久化 |
| 每条 AI 消息内嵌可交互图谱 | ECharts 力导向图，可拖拽缩放、图例过滤、邻接高亮；可折叠展开 |
| 节点详情抽屉 | 类型标签、朝代/时间/地点 chips、**来源原文/事件结果/历史影响**、按关系类型分组的关联对象与证据、属性表 |
| 实体标签点击追问 | 点实体标签自动构造「请告诉我关于 X 的历史相关信息」并提交 |
| 会话级导出 Markdown | 整段会话导出为 .md：标题、导出时间、每轮的思考过程（引用块）、正文、知识图谱上下文（代码块） |
| 空态快捷问题 | 3 条固定问题 |
| SSE 阶段状态 | 正在识别实体 / 正在查询知识图谱 / 正在生成回答 |
| 逐条复制消息 | 带剪贴板降级方案 |
| 无依据免责提示 | 图谱未命中时显示「此为大模型基于通用知识的回答」 |

### 2.2 后端能力（旧系统独有，前端体现不完整）

**规则推理引擎**：`china-war/backend/rules/rule_base.json` 共 **20 条**规则：

- **16 条反向推理**：主战场、出发地、目的地、指挥所、补给地、发起方、防守方、同盟方、
  统帅、将领、谋士、阵亡、投降、战略要地、议和地点、议和方
- **4 条复合/多跳推理**：事件因果链、事件顺承链、包含关系、战争阶段

推理结果带 `inferred` / `rule_id` / `rule_name` / `derived_from` 标记，可与原始关系区分。
实现在 `china-war/backend/inference/rule_llm_integration.py`
（`apply_inference_rules`、`_apply_composite_rules`）。

**其他**：实体间最短路径搜索（深度可配）、实体相关性打分（只保留前 4 个）、
问题类型路由（朝代战争枚举/事件详情/参与事件列表各配专属提示词）、
朝代范围识别与二级回退、实体抽取（规则优先、不足才调 LLM、带 LRU 缓存）。

### 2.3 三个"看着有、其实没有"的能力（避免误判）

| 项 | 实际情况 | 依据 |
| --- | --- | --- |
| 思考过程展示 | 页面有该区块，但后端 SSE **从不发送** `thinking` 事件，只对 localStorage 遗留数据生效，属准死代码 | `china-war/backend/app.py:2544-2872`（全文 `thinking` 出现 0 次） |
| 真正的多轮对话 | 「多轮」只在 UI 保存层面；请求体只有当前问题，不带任何历史上下文 | `index.vue:844`（`body: JSON.stringify({ question: queryText })`） |
| 引用标注 | 没有 `[1][2]` 式引用；只有消息底部可折叠的「知识图谱参考信息」文本块 | `index.vue:178-196` |

### 2.4 RAG 现状：已有 / 缺失对照

| 能力 | 旧页 | RAG 现状 |
| --- | --- | --- |
| 流式回答 + 阶段状态 | 有 | **已有**（SSE status 事件 + 推理 thinking 真实接线） |
| 引用标注与点击定位 | 无 | **已有**（`[n]` 统一转可点引用，跨 delta 不断） |
| 实体识别展示 | 有 | **已有** |
| 点击实体追问 | 有 | **已有**（实体纠正/按实体重查/图谱节点追问 chips） |
| 内嵌可交互图谱 | 每条消息内 | **已有**（右栏子图常驻，节点可拖拽缩放 + 追问 chips） |
| 历史回看 | 会话级 | **已有**（轮次级，且能恢复该轮知识面板，比旧页更细） |
| 空态示例题 | 3 条固定问题 | **已有且更强**（F08 按类别分组、能力标签、一键提问） |
| 失败/取消/重试 | 无 | **已有** |
| 事件叙事字段（攻方/守方/历史影响） | 节点抽屉里有 | **缺**（数据在快照里，装配时被丢弃） |
| 会话级导出 | 有 | **缺**（只有逐条复制） |
| 多会话管理 | 有 | **缺**（单会话 + 轮次历史 + 清空） |
| 规则推理 / 多跳 | 有（20 条规则） | **完全缺**（检索层只有原始一跳关系） |

## 三、借鉴项一：事件实体卡补齐叙事字段

**为什么值**：这是性价比最高的一项——**数据已经在快照里，只是没接出来**。

快照 `data/snapshot/<版本>/event_cards.json` 的 1050 条事件记录含
`aggressor`、`defender`、`action`、`impact`、`place` 等字段；但在装配实体卡时，
`server/fusion/panel_builder.py` 的事件分支只映射了 9 个字段，上述字段全部被丢弃。
结果是 RAG 的实体卡能显示朝代/时间/类型/身份/所属/位置/别名/来源，
但答完「赤壁之战」却不显示谁攻谁守、也不显示历史影响——而这正是战争史最该有的信息。
对照旧页节点详情抽屉，它最有价值的恰恰也是「事件结果／历史影响」这两个叙事字段。

**改造点（全部在 RAG 侧，不动数据链路、不重建索引）**：

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `contracts/panel.py` | `EntityCard` 增加字段：`aggressor` / `defender` / `action` / `impact` / `place`（均 `Optional[str] = None`） |
| 2 | `frontend/src/types/contract.ts` | 同步 TS 镜像类型 |
| 3 | `server/fusion/panel_builder.py` | 事件分支补字段映射（从 `event_cards` 取，与既有 `card.get(...)` 写法一致） |
| 4 | `frontend/src/components/panel/EntityCardsView.vue` | 在实体卡元信息区渲染「攻方/守方/作战行动/地点」，`impact` 用独立段落（语气上是叙述而非属性） |
| 5 | [data-contract.md](data-contract.md)（`entity_cards` 段）+ [features.md](features.md) 的 F07 节 | 同步字段说明——本仓库硬规则：契约字段调整必须同步这两处 |
| 6 | `tests/test_panel_event_card_fields.py` | 守护用例：含 `impact` 的事件卡经 panel 装配后字段不丢 |

**边界**：

1. 只补事件类型；人物/组织/地点的快照字段（`role` / `org` / `org_type` / `province` /
   `city` / `modern_name` / 经纬度）已经全部暴露，无需改动。
2. 不要顺手改其他实体类型的字段语义，避免牵连已过审的 panel 装配与测试。
3. 字段口径：只对事件卡有值，取自快照 `event_cards.json`；快照缺失或空白时为 `null`，
   F07 按缺失不渲染。

**验收**：提问「赤壁之战」→ 事件卡的实体卡区显示攻方/守方/作战行动/历史影响/地点；
[data-contract.md](data-contract.md) 与 `contracts/` 定义一致。

## 四、借鉴项二：会话级导出 Markdown

**原状**：RAG 只有逐条复制回答（`frontend/src/components/chat/MessageBubble.vue`）。

**设计要点**：

1. 导出内容至少含：轮次时间、问题、回答正文、**引用编号到来源的对照清单**。
   最后一项是关键——RAG 的回答正文里是 `[n]` 引用，脱离界面后若不附证据清单，引用会悬空。
   旧页导出的「知识图谱上下文」代码块正是在补这个，但形态可读性差，改成结构化清单。
2. 导出范围取**最小集合**：会话正文 + 引用编号到来源的对照清单。
   实体卡/子图/时间线是否入文档仍待决，未实现。
3. 落点：`stores/session.ts` 暴露的轮次列表已足够，导出逻辑放前端即可，不需要后端接口。
   实现为 `frontend/src/utils/sessionExport.ts` + 顶栏「导出」按钮（`App.vue`）。

**验收**：任意一段会话导出为 .md，Markdown 渲染正常，引用编号能与来源清单对上。

## 五、借鉴项三：多会话管理

**原状**：RAG 是单会话——一个 `session_id` + 轮次历史 + 「清空会话」新建 id；
存储键 `ragv5-session-v2`，带 `schemaVersion`、旧键迁移与隔离（`quarantine`）。

**设计要点**：

1. 需要会话索引（列表 + 标题 + 时间）+ 每会话独立消息体；存储结构升级，
   触发 `STORAGE_SCHEMA_VERSION` 提升，并按既有约定补迁移用例
   （迁移/隔离/上限裁剪逻辑都有对应测试，改动必须同步）。
2. 会话标题按首条问题自动生成（旧页是前 15 字），并允许后续重命名。
3. 左侧现有的「提问历史」栏是**轮次**维度，多会话是**会话**维度，两者需要共存——
   层级为：会话列表（可折叠）→ 选中会话的轮次列表，而不是二选一。
4. 顶栏原「清空会话」按钮改为「新建会话」：多会话下旧会话自动留在列表里，
   原「清空即丢历史」的语义在列表里已由每会话的删除按钮承担。
5. 落地结果：`schemaVersion 3`（会话索引 + 每会话消息体；旧键自动迁移；
   会话数 20 / 每会话 60 条上限，超限按最近使用裁剪）、`HistoryPane.vue`
   （会话列表 + 新建/切换/重命名/删除）、`tests/unit/session-store.test.ts`、
   `tests/component/history-pane.test.ts`。

**风险（已处置）**：这是四项里唯一会动到已过审持久化层的改动，需认真处理迁移与
「超长会话裁剪上限」的交互——多会话会放大 localStorage 占用。

**验收**：新建/切换/删除会话，刷新后全部保持；旧格式数据能自动迁移或隔离不报错。

## 六、借鉴项四：规则推理移植

### 6.1 目标与边界

**为什么是唯一的能力缺口**：旧系统有 20 条规则（16 反向 + 4 复合），能推出
「战役隶属」「因果链」「顺承链」「战争阶段」这类**需要多跳**的关系。
RAG 侧原本完全没有对应实现——检索层只有 `relations.json` 里的原始一跳关系。
后果可验证：需要多跳才能回答的问题，RAG 答不了。

**目标**：把 20 条推理规则移植到 RAG，让需要多跳才能回答的问题在**不改在线检索架构**
的前提下可答，且答案引用能区分"原始关系"与"推理得出"。

**做法（离线固化）**：在离线构建期应用规则，把推理结果固化成与 `relations.json`
同构的独立制品 `inferred_relations.json`；在线侧只多加载一份制品，
检索、融合、引用、面板全链路无新分支。

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| **离线固化（已选）** | 在 F09/F11 构建期应用规则，把推理结果固化成独立的 `inferred_relations.json`，在线检索层直接用 | 对在线链路零侵入；与既有"离线制品 + 在线只读"架构一致；可复现、可版本化。代价是推理结果随快照版本固定，改规则要重建快照 |
| 在线展开 | 在线检索命中子图后做规则展开（等价于旧系统做法） | 规则可调、不依赖重建；但在线引入规则加载与多跳展开，性能与复杂度上升，且要处理"推理关系 vs 原始关系"的在线区分 |

**边界**：

1. 不动 `relations.json`（原始关系是事实层，推理结果单独成文件，便于比对与回滚）；
2. 不做在线展开（在线不加载规则、不做多跳搜索）；
3. 只做关系推理，不改实体、卡片、时间线、地图；
4. 推理关系不参与"新的事实断言"以外的用途（不出现在冲突判定的事实侧）。

**必须保持的性质**：推理关系要能与原始关系区分（旧系统用
`inferred` / `rule_id` / `rule_name` / `derived_from`），否则引用与证据无法溯源，
会破坏 RAG 现有的引用制。

### 6.2 规则移植

素材：旧系统 `backend/rules/rule_base.json`（20 条）与 `backend/inference/rule_llm_integration.py`。
移植后的规则文件：`data/rules/rule_base.json`（原样保留 20 条的结构：`condition` /
`inference` / `description`，不增删字段，便于与私有侧继续对照）。

| 类别 | 条数 | 规则 | 产出规模（快照 `20260915_v1`） |
| --- | --- | --- | --- |
| 反向推理（1 跳反转） | 17 | war_001–013、016–019 | 11,559 条 |
| 复合推理（链式） | 3 | war_014 因果链（2 步）、war_015 顺承链（2 步）、war_020 战争阶段（3 步） | 271 + 3 + 0 条 |

- **反向规则**：`事件 --[原始关系]--> X` ⇒ `X --[推理关系]--> 事件`
  （`direction: reverse` 时交换两端）。
  例：`赤壁之战 --主战场--> 赤壁` ⇒ `赤壁 --发生于--> 赤壁之战`。
- **复合规则**：沿同一关系类型走 `path_length` 步（`A --r--> B --r--> C`），
  产出跨越中间节点的新关系（`A --连续演进--> C`），方向与规则 `direction` 一致。

**两处与旧实现不同的地方（必须记录）**：

1. **3 步路径被实现**。旧实现遇到 `path_length != 2` 直接跳过，所以 `war_020` 从未产出；
   本次实现通用 N 步（含 3 步）。
2. **`war_020` 在当前快照上产出为 0**（不是缺陷，是数据事实）：`包含关系` 只有 28 条边，
   实测 2 步链 3 条、**3 步链 0 条**。因此"战争阶段"类问题的可答性实际由两条规则共同承担：
   - `war_016`（`包含关系` 反向 ⇒ `隶属于战役`，1 跳推理）：支撑"某战役包含哪些阶段/子战役"
     （检索大战役的邻居即可拿到 `子战役 --隶属于战役--> 大战役` 的推理边）；
   - `war_020`（3 步链 ⇒ `战争阶段`）：方向正确但当前数据无命中，产物为空，构建报告如实记录。

   把这一条写进文档的目的：**不要让后续读者以为"产物里没有 war_020"是 bug**。

### 6.3 产物设计

每次构建产出两个文件，写在快照目录内（与 `relations.json` 同级、同版本）。

**`inferred_relations.json`**：结构与 `relations.json` 的 `RelationEdge` 同构（同名字段），
额外带推理标记，使在线加载器可以"一份代码读两种行"：

```json
{
  "source_entity_id": "place_0008",
  "source_name": "斧隧",
  "relation": "发生于",
  "target_entity_id": "event_0001",
  "target_name": "神农斧隧之战",
  "confidence": "medium",
  "source_type": "inference",
  "legacy_table": "inference",
  "inferred": true,
  "rule_id": "war_001",
  "rule_name": "主战场反向推理规则",
  "derived_from": "主战场",
  "derived_from_rows": [[1, "event_place_relations"]],
  "composite": false,
  "path": [],
  "source_version": "20260915_v1"
}
```

| 字段 | 说明 |
| --- | --- |
| `source_type` | 恒为 `inference`（原始关系是 `kg_relation`），在线据此与事实层区分 |
| `legacy_table` | 恒为 `inference`：证据 ID 形如 `graph_inference_h<哈希>`，与原始行号空间天然隔离 |
| `inferred` | 恒为 `true`（与旧系统的 `properties.inferred` 口径一致） |
| `rule_id` / `rule_name` | 产出该关系的规则（引用可溯源到规则） |
| `derived_from` | 反向规则 = 原始关系名；复合规则 = `"<关系>链"`（与旧系统一致） |
| `derived_from_rows` | **溯源链**：`[行号, 旧表名]` 列表。反向规则 1 项；复合规则为路径上每段各 1 项。这是"引用能回指到原始证据"的关键（旧系统只写了关系名，本次补上行号） |
| `composite` / `path` | 复合规则为 `true` 且 `path` 记录中间节点 `{entity_id, name, type}`；反向规则为空 |
| `confidence` | 继承来源原始关系（复合规则取路径上最低的一档），不做放大 |

**`inference_report.json`**：构建报告（供治理与审计，不进在线链路）——规则文件哈希、
参与推理的关系条数、每条规则的产出条数、跳过原因统计（`pending_review` / 3 步链无命中 /
自环 / 去重）、产物条数与 SHA256。

### 6.4 离线构建

`scripts/build_inferred_relations.py`：

```bash
python scripts/build_inferred_relations.py --version 20260915_v1 [--rules data/rules/rule_base.json]
```

- 输入：`data/snapshot/<version>/{entities.json, relations.json}` + 规则文件；
- 输出：写回同目录的 `inferred_relations.json` 与 `inference_report.json`（覆盖式，幂等）；
- 过滤：跳过 `pending_review=True` 的关系（与在线加载口径一致）；
- 去重：`(source, relation, target, rule_id)` 唯一；复合规则同一 `(start, end)` 只产一条；
- 规模保护：单规则产出上限（默认 20000 条，超出截断并写入报告）——防止规则改动后路径爆炸；
- 确定性：输出按 `(rule_id, source_entity_id, relation, target_entity_id)` 稳定排序，
  重复构建字节一致。

接入 `scripts/run_pipeline.py`：顺序为 `治理(F09) → 规则推理 → 索引(F11)`，
使新版本快照自动携带推理产物。推理步骤失败不阻断索引构建，
但会在流水线日志与报告中标红。

### 6.5 契约

`contracts/inference.py`（并导出到 `contracts/__init__.py`）：

| 类型 | 作用 |
| --- | --- |
| `InferenceRule` | 规则结构（`rule_id` / `name` / `condition` / `inference` / `description`） |
| `InferredRelation` | 推理关系行（`RelationEdge` 全字段 + 6.3 的推理标记），提供 `to_dict()` |
| `InferenceReport` | 构建报告结构 |

`RelationEdge` **不加字段**：原始关系契约保持"事实层"纯净；两者靠 `source_type` / `inferred`
在加载侧区分（`RelationEdge` 的行 `inferred` 缺失即视为 `false`）。

### 6.6 在线消费

| 环节 | 落点 | 口径 |
| --- | --- | --- |
| 加载 | `server/graph/graph_index.py` | `_load()` 读完 `relations.json` 后，若 `inferred_relations.json` 存在则一并灌入同一张邻接表，并记录 `inferred_edge_count`（供 health/日志观测）。**不新增检索路径**：F03 的六种策略因共享邻接表而自动能看到推理边 |
| 检索与证据 | `server/graph/search.py` | `_triple_from_row()` 在行带推理标记时，`content` 追加 `inferred / rule_id / rule_name / derived_from / derived_from_rows`（原始关系不写入，保持既有证据结构不变） |
| 事件-事件白名单 | `server/graph/query_strategies.py` 的 `EVENT_EVENT_RELATIONS` | 追加四个推理关系名 `间接因果 / 连续演进 / 战争阶段 / 隶属于战役`，否则事件类问题会把推理边过滤掉 |
| 引用 | `server/generate/__init__.py::build_citations` | 推理边的引用标题追加规则标注，让"原始 vs 推理"在引用清单里可辨认：`赤壁 —发生于→ 赤壁之战（推理·主战场反向推理规则）`；`content` 里的 `derived_from_rows` 同时随 panel 事件下发，证据详情可继续追到原始行 |

**明确不做的接线**（避免下一轮被当遗漏）：

1. **不参与冲突判定**：`server/fusion/conflict.py` 的事实比对只看原始三元组，
   推理边不进事实侧（推理结果与原始事实比较会产生假冲突）；
2. **不进 `relation_card_field_map` 一致性校验**：该表按原始关系×目标类型构建，
   推理关系不在其中；
3. **不影响拒答阈值**：推理边是合法依据（来自真实原始关系），但不额外提高证据分。

### 6.7 验收

1. **可复现构建**：对 `20260915_v1` 跑两次构建，产物 SHA256 一致；报告里 17 条反向规则各有产出、
   `war_014` / `war_015` 有产出、`war_020` 为 0 并注明"3 步链无命中"。
2. **三个多跳问题的证据可检索**（检索层断言，不依赖 LLM）：
   - 战役隶属：命中 `X --隶属于战役--> 大战役` 的推理边（`war_016`）；
   - 因果链：命中 `A --间接因果--> C` 的推理边（`war_014`）；
   - 战争阶段：命中大战役的 `隶属于战役` 推理边集合（`war_016`），并确认 `war_020` 产物为空。
3. **引用可区分**：`build_citations` 对推理边给出带规则名的标题；原始三元组标题不变（回归断言）。
4. **溯源可回指**：任一推理边的 `derived_from_rows` 能在 `relations.json` 里定位到真实原始行，
   且该原始行的 `relation` 与 `derived_from` 一致。
5. **不回归**：既有后端测试全通过；`relations.json` 字节不变；未产出推理产物时在线行为
   与移植前一致（缺文件即降级为纯原始图谱）。

### 6.8 风险

| 风险 | 处置 |
| --- | --- |
| 推理边被当成事实回答 | 引用标题强制带"推理"标注；`content.inferred` 随证据下发，下游（前端/评测）可据此区分 |
| 产物膨胀 | 单规则 2 万条上限 + 报告记录截断；当前实测总量 1.19 万条，与原始 1.77 万条同量级 |
| 规则改动需要重建 | 离线固化方案的固有代价；规则文件哈希写进报告，快照版本与规则版本可对账 |
| 复合推理的语义外推 | 只按规则声明实现（同关系类型的链），不外推其它关系组合 |
| 3 步链 0 命中被误读为缺陷 | 6.2 已写明；报告里单列该规则的跳过原因 |

### 6.9 实测数字

- 对 `20260915_v1` 产出 **11,833 条**推理边
  （反向 11,559 + 因果链 3 + 顺承链 271 + 战争阶段 0）；
- `inferred_relations.json` 6.9 MB + `inference_report.json`；重复构建字节一致。

## 七、明确不借鉴的项

| 项 | 旧系统做法 | 为什么不搬 |
| --- | --- | --- |
| 思考过程展示 | 页面有区块，但后端从不发送 `thinking` 事件（死代码） | 没有可借鉴的实现；RAG 的推理 thinking 是真实接线的，不需要动 |
| 无依据时用大模型通用知识回答 + 免责提示 | 检索未命中时用 LLM 通用知识作答并提示 | 与 RAG 的核心策略相反。RAG 是引用制 + 拒答，**故意不做**这种兜底；搬它是倒退 |
| 点击实体追问 / 内嵌可交互图谱 | 有 | RAG 已具备（实体纠正、按实体重查、图谱节点追问 chips、右栏常驻子图） |
| 空态快捷问题（3 条固定） | 有 | RAG 的 F08 示例区更强（按类别分组、能力标签、一键提问） |

## 八、风险与注意事项

1. **契约字段改动必须同步文档**：本仓库硬规则——涉及 [data-contract.md](data-contract.md)
   的字段调整，必须同步更新该文件与 `contracts/` 中的定义，再同步受影响的功能节。
2. **多会话会触碰已过审的持久化层**：`stores/session.ts` 有 `schemaVersion`、旧键迁移、
   损坏数据隔离、超长会话裁剪上限等机制且都有测试；改动需同步补用例。
3. **规则推理结果的出处可追溯性**：必须保留"原始关系 / 推理关系"的区分标记，
   否则会破坏引用制（引用要能回指到证据）。
4. **公开仓库的边界**：本仓库是公开仓库，而 20 条规则源自旧系统的私有仓库。
   把 `rule_base.json` 移植进来等于把它公开——这是一个显式拍板的决定（见第九节）。
5. **只补事件类型**：不要顺手改其他实体类型的字段语义，避免牵连已过审的 panel 装配与测试。

## 九、待决项与拍板结果

| # | 待决项 | 拍板结果 |
| --- | --- | --- |
| 1 | 规则推理落点选**离线固化**还是**在线展开** | **离线固化**（快照构建期应用规则，产物独立于 `relations.json`，对在线链路零侵入） |
| 2 | 20 条规则是否愿意进公开仓库 | **进公开仓库**（`data/rules/rule_base.json`） |
| 3 | 多会话是否真的需要 | **需要**（轮次历史保留为会话内的二级列表，不是二选一） |
| 4 | 导出的内容范围 | 取**最小集合**——会话正文 + 引用编号到来源的对照清单；实体卡/子图/时间线是否入文档仍待决，未实现 |
| 5 | 旧页入口现在就摘，还是等借鉴完成后再摘 | **暂时保留**（它仍是唯一的答案质量对照基线） |

关于旧页去留：**在借鉴完成前不要删旧页**。理由是它目前是唯一的答案质量对照基线——
RAGv4 评测曾暴露问题（F02 朝代子串误判导致拒答、F04 长改写 AND 失效、应拒答缺口），
而「是否过度拒答」这类判断需要同题并行对照才能判。若确定不再需要对照，
建议顺序仍是「先摘菜单入口 → 观察一段 → 最后删代码」。

## 十、附录：事实核对表

| 事实 | 依据 |
| --- | --- |
| 旧页导出 Markdown（含思考过程引用块、正文、KG 上下文） | `china-war/frontend/src/views/inference/index.vue:1073-1160` |
| 旧页多会话（新建/切换/删除/自动命名） | `index.vue:687-766`、`index.vue:1044-1065` |
| 旧页节点详情抽屉（来源原文/事件结果/历史影响/关系分组） | `index.vue:271-357`、`index.vue:1214-1364` |
| 旧页实体标签点击追问 | `index.vue:1163-1171` |
| 旧页请求体不含历史（多轮仅 UI 层面） | `index.vue:844` |
| 旧页无依据免责提示 | `index.vue:158-162`、`index.vue:190-193` |
| 旧页思考过程为死代码 | `china-war/backend/app.py:2544-2872` 内 `thinking` 出现 0 次 |
| 20 条规则（16 反向 + 4 复合） | `china-war/backend/rules/rule_base.json`（逐条列出 war_001–war_020） |
| 推理实现与标记 | `china-war/backend/inference/rule_llm_integration.py`（`apply_inference_rules`、`_apply_composite_rules`） |
| 快照事件字段含 aggressor/defender/action/impact/place | `data/snapshot/<版本>/event_cards.json`（1050 条） |
| RAG 侧原先无规则推理 | 在 `server/`、`contracts/`、`config/`、`scripts/` 搜 `rule_base` / `inferred` / `rule_id` / `derived_from` 零命中 |
| RAG 会话存储结构与迁移机制 | `frontend/src/stores/session.ts`（`ragv5-session-v2` → `v3`、`schemaVersion`、quarantine、裁剪上限） |
| 旧页删除边界：`backend/entity_extract/` 仅服务问答 | `china-war/backend/app.py:1627`、`2453`、`2572` |
| 「文本实体识别」用的是另一个模块（勿删 `entity-event-relation/`） | `china-war/backend/app.py:3409+` 用 `src.extractors.*`；`entity-event-relation/` 另被全局搜索使用 |

## 十一、落地结果对照

| 借鉴项 | 状态 | 落点 |
| --- | --- | --- |
| 事件实体卡补齐叙事字段 | 已完成 | `contracts/panel.py`、`server/fusion/panel_builder.py`、`frontend/src/types/contract.ts`、`EntityCardsView.vue`、[data-contract.md](data-contract.md)、[features.md](features.md)、`tests/test_panel_event_card_fields.py` |
| 会话级导出 Markdown | 已完成 | `frontend/src/utils/sessionExport.ts`、顶栏「导出」按钮（`App.vue`）、`tests/unit/session-export.test.ts`、`tests/e2e/sessions.spec.ts` |
| 多会话管理 | 已完成 | `frontend/src/stores/session.ts`（schemaVersion 3）、`HistoryPane.vue`、`tests/unit/session-store.test.ts`、`tests/component/history-pane.test.ts` |
| 规则推理移植 | 已完成（离线固化） | 规则库 `data/rules/rule_base.json`（20 条）；离线固化 `data/snapshot/inference.py` + `scripts/build_inferred_relations.py`（产物 `inferred_relations.json` / `inference_report.json`，接入 `run_pipeline.py`）；契约 `contracts/inference.py`；在线消费 `server/graph/graph_index.py`、`search.py`、`query_strategies.py`、`server/generate/prompts.py`、`server/generate/__init__.py`；测试 `tests/test_inference_rules.py`、`tests/test_graph_inferred_edges.py` |
