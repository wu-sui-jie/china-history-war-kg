# RAG 规则推理移植 —— 需求与设计

> 2026-09-20。本文档是 [RAG借鉴旧问答系统-需求分析](RAG借鉴旧问答系统-需求分析.md) 第四节 4.4
> （P2 规则推理移植）的落地设计。**落点已拍板**：方案 A 离线固化，且 20 条规则进公开仓库
> （见该文档第八节待决项 #1/#2）。

## 一、目标与边界

**目标**：把旧系统 20 条推理规则移植到 RAG，让需要多跳才能回答的问题（战役隶属、因果链、
战争阶段）在**不改在线检索架构**的前提下可答，且答案引用能区分"原始关系"与"推理得出"。

**做法**：在离线构建期应用规则，把推理结果固化成与 `relations.json` 同构的独立制品
`inferred_relations.json`；在线侧只多加载一份制品，检索、融合、引用、面板全链路无新分支。

**边界**：

1. 不动 `relations.json`（原始关系是事实层，推理结果单独成文件，便于比对与回滚）；
2. 不做在线展开（在线不加载规则、不做多跳搜索）；
3. 只做关系推理，不改实体、卡片、时间线、地图；
4. 推理关系不参与"新的事实断言"以外的用途（不出现在冲突判定的事实侧，见 §6.4）。

## 二、规则移植

素材：旧系统 `backend/rules/rule_base.json`（20 条）与 `backend/inference/rule_llm_integration.py`
（`apply_inference_rules` / `_apply_composite_rules`）。

移植后的规则文件：`config/rule_base.json`（原样保留 20 条的结构：`condition` / `inference` /
`description`，不增删字段，便于与私有侧继续对照）。

| 类别 | 条数 | 规则 | 产出规模（快照 20260915_v1） |
| --- | --- | --- | --- |
| 反向推理（1 跳反转） | 17 | war_001–013、016–019 | 11,559 条 |
| 复合推理（链式） | 3 | war_014 因果链（2 步）、war_015 顺承链（2 步）、war_020 战争阶段（3 步） | 271 + 3 + 0 条 |

**反向规则**：`事件 --[原始关系]--> X` ⇒ `X --[推理关系]--> 事件`（`direction: reverse` 时交换两端）。
例：`赤壁之战 --主战场--> 赤壁` ⇒ `赤壁 --发生于--> 赤壁之战`。

**复合规则**：沿同一关系类型走 `path_length` 步（`A --r--> B --r--> C`），产出跨越中间节点的新关系
（`A --连续演进--> C`），方向与规则 `direction` 一致。

### 2.1 两处与旧实现不同的地方（必须记录）

1. **3 步路径被实现**。旧实现遇到 `path_length != 2` 直接跳过，所以 war_020 从未产出。本次实现
   通用 N 步（含 3 步）。
2. **war_020 在当前快照上产出为 0**（不是缺陷，是数据事实）：`包含关系` 只有 28 条边，
   实测 2 步链 3 条、**3 步链 0 条**。因此"战争阶段"类问题的可答性实际由两条规则共同承担：
   - war_016（`包含关系` 反向 ⇒ `隶属于战役`，1 跳推理）：支撑"某战役包含哪些阶段/子战役"
     （检索大战役的邻居即可拿到 `子战役 --隶属于战役--> 大战役` 的推理边）；
   - war_020（3 步链 ⇒ `战争阶段`）：方向正确但当前数据无命中，产物为空，构建报告如实记录。

   把这一条写进文档的目的：不要让后续读者以为"产物里没有 war_020"是 bug。

## 三、产物设计

每次构建产出两个文件，写在快照目录内（与 `relations.json` 同级、同版本）：

### 3.1 `inferred_relations.json`

结构与 `relations.json` 的 `RelationEdge` **同构**（同名字段），额外带推理标记，使在线加载器
可以"一份代码读两种行"：

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

### 3.2 `inference_report.json`

构建报告（供治理与审计，不进在线链路）：规则文件哈希、参与推理的关系条数、每条规则的产出条数、
跳过原因统计（`pending_review` / 3 步链无命中 / 自环 / 去重）、产物条数与 SHA256。

## 四、离线构建

新增 `scripts/build_inferred_relations.py`：

```bash
python scripts/build_inferred_relations.py --version 20260915_v1 [--rules config/rule_base.json]
```

- 输入：`data/snapshot/<version>/{entities.json, relations.json}` + 规则文件；
- 输出：写回同目录的 `inferred_relations.json` 与 `inference_report.json`（覆盖式，幂等）；
- 过滤：跳过 `pending_review=True` 的关系（与在线加载口径一致）；
- 去重：`(source, relation, target, rule_id)` 唯一；复合规则同一 `(start, end)` 只产一条；
- 规模保护：单规则产出上限（默认 20000 条，超出截断并写入报告）——防止规则改动后路径爆炸；
- 确定性：输出按 `(rule_id, source_entity_id, relation, target_entity_id)` 稳定排序，重复构建字节一致。

接入 `scripts/run_pipeline.py`：顺序改为 `治理(F09) → 规则推理(F09 延伸) → 索引(F11)`，
使新版本快照自动携带推理产物。推理步骤失败不阻断索引构建，但会在流水线日志与报告中标红。

## 五、契约

新增 `contracts/inference.py`（并导出到 `contracts/__init__.py`）：

| 类型 | 作用 |
| --- | --- |
| `InferenceRule` | 规则结构（`rule_id` / `name` / `condition` / `inference` / `description`） |
| `InferredRelation` | 推理关系行（`RelationEdge` 全字段 + §3.1 的推理标记），提供 `to_dict()` |
| `InferenceReport` | 构建报告结构 |

`RelationEdge` **不加字段**：原始关系契约保持"事实层"纯净；两者靠 `source_type`/`inferred` 在
加载侧区分（`RelationEdge` 的行 `inferred` 缺失即视为 `false`）。

## 六、在线消费

### 6.1 加载（`server/graph/graph_index.py`）

`_load()` 在读完 `relations.json` 后，若 `inferred_relations.json` 存在则一并灌入同一张邻接表，
并记录 `self.inferred_edge_count`（供 health/日志观测）。**不新增检索路径**：F03 的
`single_entity` / `relation` / `event_event` / `timeline` / `background` / `comparison` 六种策略
因为共享邻接表而自动能看到推理边。

### 6.2 检索与证据（`server/graph/search.py`）

- `_triple_from_row()`：行带推理标记时，`content` 追加 `inferred / rule_id / rule_name /
  derived_from / derived_from_rows`（原始关系不写入，保持既有证据结构不变）；
- 事件-事件白名单（`server/graph/query_strategies.py` 的 `EVENT_EVENT_RELATIONS`）：追加四个
  推理关系名 `间接因果 / 连续演进 / 战争阶段 / 隶属于战役`，否则事件类问题会把推理边过滤掉。

### 6.3 引用（`server/generate/__init__.py::build_citations`）

推理边的引用标题追加规则标注，让"原始 vs 推理"在引用清单里可辨认：

```
赤壁 —发生于→ 赤壁之战（推理·主战场反向推理规则）
```

`content` 里的 `derived_from_rows` 同时随 panel 事件下发，证据详情页可继续追到原始行。

### 6.4 不做的接线（明确记录，避免下一轮被当遗漏）

1. **不参与冲突判定**：`server/fusion/conflict.py` 的事实比对只看原始三元组，推理边不进事实侧
   （推理结果与原始事实比较会产生假冲突）；
2. **不进 `relation_card_field_map` 一致性校验**：该表按原始关系×目标类型构建，推理关系不在其中；
3. **不影响拒答阈值**：推理边是合法依据（来自真实原始关系），但不额外提高证据分。

## 七、验收

1. **可复现构建**：对 `20260915_v1` 跑两次构建，产物 SHA256 一致；报告里 17 条反向规则各有产出、
   war_014/015 有产出、war_020 为 0 并注明"3 步链无命中"。
2. **三个多跳问题的证据可检索**（检索层断言，不依赖 LLM）：
   - 战役隶属：命中 `X --隶属于战役--> 大战役` 的推理边（war_016）；
   - 因果链：命中 `A --间接因果--> C` 的推理边（war_014）；
   - 战争阶段：命中大战役的 `隶属于战役` 推理边集合（war_016），并确认 war_020 产物为空。
3. **引用可区分**：`build_citations` 对推理边给出带规则名的标题；原始三元组标题不变（回归断言）。
4. **溯源可回指**：任一推理边的 `derived_from_rows` 能在 `relations.json` 里定位到真实原始行，
   且该原始行的 `relation` 与 `derived_from` 一致。
5. **不回归**：既有后端测试全通过；`relations.json` 字节不变；未产出推理产物时在线行为与移植前一致
   （缺文件即降级为纯原始图谱）。

## 八、风险

| 风险 | 处置 |
| --- | --- |
| 推理边被当成事实回答 | 引用标题强制带"推理"标注；`content.inferred` 随证据下发，下游（前端/评测）可据此区分 |
| 产物膨胀 | 单规则 2 万条上限 + 报告记录截断；当前实测总量 1.19 万条，与原始 1.77 万条同量级 |
| 规则改动需要重建 | 这是方案 A 的固有代价；规则文件哈希写进报告，快照版本与规则版本可对账 |
| 复合推理的语义外推 | 只按规则声明实现（同关系类型的链），不外推其它关系组合 |
| 3 步链 0 命中被误读为缺陷 | §2.1 已写明；报告里单列该规则的跳过原因 |
