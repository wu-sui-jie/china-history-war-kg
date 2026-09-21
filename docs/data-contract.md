# RAG 数据契约

- 文档类型：接口与数据契约
- 状态：生效（实现与本文不一致时以本文为准反查代码，先改代码或先改契约二者必须同步）
- 创建时间：2026-09-03
- 最近核验：2026-09-15

## 文档目的

统一 F03、F04、F05、F06、F01 之间的数据结构，避免各模块自行定义字段，导致接口对不上。

## 查询请求

```json
{
  "session_id": "会话ID",
  "question": "用户问题原文",
  "history": [
    {
      "role": "user",
      "content": "上一轮问题"
    },
    {
      "role": "assistant",
      "content": "上一轮回答"
    }
  ],
  "filters": {
    "dynasty": [],
    "event_type": []
  },
  "corrected_entities": []
}
```

字段填写说明：

1. session_id：前端创建会话时生成。
2. question：前端必填，用户问题原文。
3. question_type：由 F02 判定，前端不直接指定。
4. history：前端携带当前会话近期历史，初版建议最多 2 到 4 轮。

请求边界（2026-09-15 审核 P0-2）：以下上限由契约层强制，超限在调用检索/模型之前
返回 HTTP 4xx（`{"status":"error","error_code":"invalid_request|payload_too_large",...}`），
不会进入 SSE 流。默认值见 `config/defaults.py`，可用环境变量覆盖。

| 字段 | 上限 | 环境变量 |
| --- | --- | --- |
| 请求体 | 64 KiB | `REQUEST_MAX_BYTES` |
| `question` | 500 字符 | `QUESTION_MAX_CHARS` |
| `session_id` | 128 字符 | `SESSION_ID_MAX_CHARS` |
| `history` | 40 条 | `HISTORY_MAX_ITEMS` |
| `history[].content` | 4000 字符 | `HISTORY_CONTENT_MAX_CHARS` |
| `history[].role` | 仅 user / assistant | — |
| `filters.*` | 每维 20 项、单项 64 字符 | `FILTERS_MAX_ITEMS` / `FILTER_VALUE_MAX_CHARS` |
| `corrected_entities` | 20 条，action 仅 add/replace/remove | `CORRECTIONS_MAX_ITEMS` |

数据版本（2026-09-15 审核 P0-7）：服务的数据版本优先取 `RAG_ACTIVE_VERSION`（显式固定，
版本目录缺失即启动失败），未配置时才扫描"最新一致版本"。`GET /api/health` 返回
`version`、`index_version`、`git_commit`、快照/索引 manifest 与向量 ids 的 SHA-256。
5. corrected_entities：前端在用户手动纠正实体时填写；未纠正时不传。
6. filters：前端可选，朝代和战争类型来自 F09 生成的词典。

filters 同时用于图谱节点过滤和文本索引元数据过滤，不是只注入提示词。

event_type 使用 F09 治理后的标准战争类型词典。旧数据 `events.event_type` 当前已有 27 类、无空值，F09 负责同义合并和标准化；没有归一化战争类型的记录不参与对应筛选。

filter 中数组为空表示不过滤。例如 `"dynasty": []` 表示不按朝代过滤，而不是只查“没有朝代”的记录。

## F02 内部输出

以下字段由 F02 在后端生成，不进入前端原始请求体，仅在 F02 到 F03/F04/F05/F06 的内部链路传递：

```json
{
  "rewritten_question": "改写后问题",
  "question_type": "relation",
  "entities": [
    {
      "name": "长平之战",
      "type": "事件",
      "standard_name": "长平之战",
      "confidence": "high"
    }
  ],
  "candidates": [
    {
      "mention": "涿鹿",
      "entity_type": "地点",
      "options": [
        {
          "name": "涿鹿",
          "standard_name": "涿鹿",
          "confidence": "high"
        },
        {
          "name": "涿鹿县",
          "standard_name": "涿鹿",
          "confidence": "medium"
        }
      ]
    }
  ]
}
```

rewritten_question 由 F02 根据原问题、会话历史和实体纠正结果生成。纠错重查时应使用上一轮会话历史，不能清空 history 后让 F02 重新猜测指代。

dynasty_bias 是 F02 从问句中自动识别到的朝代（如问"商朝"→["商"]），**仅作排序软偏置，
不作为过滤条件**；显式筛选走 filters.dynasty（硬过滤）。生效范围：F03 图谱侧在策略前
重排节点（可经 top_k 影响证据集合）；F04 文本侧只是检索返回序，最终顺序由 F05 按相关分
决定（当前不改变最终排序）——详见 `docs/features/02-entity-linking.md`。
两者分开的原因见 `docs/features/02-entity-linking.md` 与
`docs/CHANGELOG.md`（RAGv4 系统问题 4：证据 ID 跨表重复）。

entities 是 F02 判定并应用纠正后的最终实体，SSE entities 事件中的实体字段与它一致。

candidates 是可选的候选实体列表，用于前端“手动纠正”交互展示；没有歧义时可以为空。

## 实体纠正重查

用户手动纠正实体时，前端发起一次新的查询请求：

```json
{
  "session_id": "会话ID",
  "question": "用户问题原文",
  "corrected_entities": [
    {
      "action": "replace",
      "entity_type": "事件",
      "original": "涿鹿之战",
      "replacement": "阪泉之战"
    }
  ],
  "history": [
    {
      "role": "user",
      "content": "上一轮问题"
    },
    {
      "role": "assistant",
      "content": "上一轮回答"
    }
  ]
}
```

规则：

1. 纠正重查是新请求，不能修改已经结束的历史回答。
2. 后端以 corrected_entities 覆盖 F02 自动实体识别结果。
3. F02 使用原问题加纠正后的实体重新执行改写、检索与回答。
4. 前端取消当前进行中的流后，再发起纠正重查。

corrected_entities 支持三种操作：

1. add：新增实体，字段为 entity_type、name。
2. replace：替换实体，字段为 entity_type、original、replacement。
3. remove：删除实体，字段为 entity_type、original，不传 replacement。

add 的 name 和 replace 的 replacement 必须填写 F02 输出中的 standard_name，避免前端回传“涿鹿县”等别名后 F02 再次做归一化。

纠正重查必须携带上一轮会话历史，便于 F02 对“它”“这场战争”等指代继续做消解。

如果 remove 后问题不再包含任何实体，F02 输出空 entities，并标记为纯文本检索；若文本检索也无有效证据，F06 按拒答路径返回。

## 问题类型

问题类型由 F02 负责判定，初版分类：

1. single_entity：介绍单个实体。
2. relation：查询关系或参与方。
3. event_event：查询事件之间的关系。
4. comparison：比较多个实体或事件。
5. timeline：查询时间顺序或时间线。
6. background：查询过程、原因、背景等文本类问题。
7. unknown：无法确定类型。

## 统一证据对象

```json
{
  "evidence_id": "唯一证据ID",
  "kind": "graph_triple",
  "source_type": "kg_relation",
  "source_version": "20260903_v1",
  "confidence": "high",
  "score": 0.95,
  "content": {},
  "related_entities": [],
  "citation_index": 1
}
```

kind 取值：

1. graph_triple：图谱三元组。
2. raw_text：原始正文片段。
3. event_card：事件卡片。
4. evidence：关系证据短文本。

source_type 取值：

1. kg_relation：旧项目关系表。
2. kg_entity：旧项目实体表。
3. original_text：原始正文。
4. event_card_json：事件卡片。
5. relation_evidence：关系证据。
6. kg_inference：规则推理边（离线固化产物，见“规则推理产物”节）。

confidence 取值：

1. high：来源明确且证据支持。
2. medium：来源存在但内容可能不完整。
3. low：只属于候选或共现关系。
4. pending_review：仅存在于 F09 治理阶段，需要人工审核。

pending_review 数据不进入 F03 可查询图谱，也不作为运行链路证据返回；它只保留在治理报告中。

score 取值范围为 0 到 1。F04 文本检索结果必须输出 score；F03 图谱证据的 score 可选。

F04 归一化方法：先取当前查询 Top N 候选的原始分，再按 min-max 归一化。max 等于 min 时，该条候选的归一化 score 记为 0.5。关键词、向量、混合三种模式都在各自模式内部完成该归一化。score 主要用于排序和证据融合，不单独作为拒答阈值；向量/hybrid 模式下作为低分兜底信号之一与「实体为空且证据无共享词」叠加生效（`VECTOR_REFUSAL_MIN_SCORE`，默认 0.25，见 F06 拒答规则第 3 条）。

citation_index 由 F05 统一分配，F06 在回答中使用，F07 展示引用。

## 图谱三元组证据

> evidence_id 格式（F03）：`graph_{legacy表名}_{source_row_id}`，如
> `graph_event_person_relations_772`。`source_row_id` 只在各 legacy 表
> （event_person_relations / event_place_relations / event_event_relations /
> event_organization_rel）内唯一，跨表会重复，因此 ID 必须带表名以保证全局唯一
> （缺行号时用含表名的内容哈希回退）。见 `docs/CHANGELOG.md`（RAGv4 系统问题 4）。

```json
{
  "evidence_id": "graph_event_person_relations_772",
  "kind": "graph_triple",
  "source_type": "kg_relation",
  "source_version": "20260903_v1",
  "confidence": "high",
  "content": {
    "subject": "长平之战",
    "subject_type": "事件",
    "relation": "发起方",
    "object": "秦军",
    "object_type": "组织"
  },
  "related_entities": ["长平之战", "秦军"]
}
```

## 规则推理产物（inferred_relations.json）

P2 规则推理移植（2026-09-20，设计见 [RAG_v2/RAG规则推理移植-需求与设计.md](RAG_v2/RAG规则推理移植-需求与设计.md)）
的离线固化产物，与 `relations.json` 同级写在快照目录内，由 `scripts/build_inferred_relations.py`
应用 `data/rules/rule_base.json`（20 条规则）生成：

| 文件 | 内容 |
| --- | --- |
| `inferred_relations.json` | 推理关系行（与 relations.json 同构 + 推理标记） |
| `inference_report.json` | 构建报告：规则文件哈希、逐规则产出、跳过原因、产物 SHA256 |

推理行字段（在 RelationEdge 之上追加）：

```json
{
  "source_entity_id": "place_0001",
  "source_name": "涿鹿",
  "relation": "发生于",
  "target_entity_id": "event_0005",
  "target_name": "涿鹿之战",
  "confidence": "medium",
  "source_type": "inference",
  "legacy_table": "inference",
  "inferred": true,
  "rule_id": "war_001",
  "rule_name": "主战场反向推理规则",
  "derived_from": "主战场",
  "derived_from_rows": [[6, "event_place_relations"]],
  "composite": false,
  "path": [],
  "source_version": "20260915_v1"
}
```

- `source_type` 恒为 `inference`、`legacy_table` 恒为 `inference`：推理边的证据 ID 形如
  `graph_inference_h<哈希>`，与原始行的 `graph_<旧表>_<行号>` 天然隔离（不会出现同 ID 的不同证据）；
- `derived_from`：反向规则为原始关系名；复合规则为 `"<关系>链"`；
- `derived_from_rows`：溯源链 `[[source_row_id, legacy_table], ...]`，可回指到 `relations.json` 的真实行；
- `composite` / `path`：复合规则为 `true`，`path` 记录路径上的中间节点 `{entity_id, name, type}`；
- `confidence` 继承来源原始关系（复合规则取路径上最低的一档），不做放大。

在线口径（F03，2026-09-20 起）：

1. `GraphIndex` 把 `inferred_relations.json` 与 `relations.json` 一并灌入同一张邻接表，
   六种检索策略自动可见推理边；**缺文件即降级**为纯原始图谱（未产出推理产物的快照行为与移植前一致）；
2. 推理边仍是 `kind=graph_triple` 证据，但 `source_type=kg_inference`，且 `content` 追加
   `inferred / rule_id / rule_name / derived_from / derived_from_rows`；
3. 事件-事件关系白名单（`server/graph/query_strategies.py::EVENT_EVENT_RELATIONS`）已包含
   推理关系名（间接因果 / 连续演进 / 战争阶段 / 隶属于战役），否则事件类问题会把推理边过滤掉；
4. 引用标题与提示词都对推理边标注来源规则（`（推理·规则名）` / `[推理关系：由「X」按规则推导]`），
   使“原始关系 vs 推理得出”在引用清单与回答里可辨；推理边不参与冲突判定的事实侧。

## 文本证据

```json
{
  "evidence_id": "text_001",
  "kind": "raw_text",
  "source_type": "original_text",
  "source_version": "20260903_v1",
  "confidence": "medium",
  "content": {
    "text": "切分后的正文片段",
    "doc_id": "来源文档ID",
    "chunk_type": "raw"
  },
  "related_entities": ["长平之战"]
}
```

chunk_type 取值：raw、event_card、evidence。

事件卡片证据在 content 中额外包含事件名、朝代、时间、参与方、结果等字段。

## 检索输出

F03 图谱检索输出为 graph_triple 列表和节点邻居数据，供 F05 组装 panel；展示用 subgraph 只由 F05 生成。

F04 文本检索输出为 raw_text、event_card、evidence 列表。

“命中关键词”不是强制字段，只在关键词检索模式下存在。

**文本检索模式（RAGv5）**：`keyword`（FTS5/BM25）/ `vector`（Chroma 余弦）/ `hybrid`（关键词+向量融合，
策略见 `TEXT_HYBRID_STRATEGY`）。模式由部署级开关 `TEXT_MODE` 决定，向量不可用时自动降级 keyword；
实际执行模式随 `text_results` 事件的 `mode` 字段上报。筛选条件的语义（RAGv5 §2.5-3）：
`event_type` 只对**有该元数据的片段**（事件卡片、已关联事件的关系证据）生效，原文片段（无事件归属）放行。

## 知识面板数据结构

F07 知识面板使用独立的 panel 数据结构，推荐随 SSE 的 panel 事件推送：

```json
{
  "session_id": "会话ID",
  "stage": "panel",
  "data": {
    "entity_cards": [
      {
        "entity_id": "event_001",
        "type": "事件",
        "name": "长平之战",
        "event_type": "统一战争",
        "dynasty": "战国",
        "start_date": "前262年",
        "description": "事件简介或摘要",
        "aliases": ["长平大战"],
        "source": "来源标识",
        "aggressor": "秦国",
        "defender": "赵国",
        "action": "决战",
        "impact": "赵国精锐尽失，东方六国再无力单独抗秦",
        "place": "长平"
      }
    ],
    "subgraph": {
      "nodes": [
        {
          "id": "event_001",
          "type": "事件",
          "name": "长平之战"
        }
      ],
      "edges": [
        {
          "source": "event_001",
          "target": "org_001",
          "relation": "发起方"
        }
      ]
    },
    "timeline": {
      "groups": [
        {
          "label": "战国",
          "items": [
            {
              "event_id": "event_001",
              "name": "长平之战",
              "start_date": "前262年",
              "dynasty": "战国"
            }
          ]
        },
        {
          "label": "时间不详/仅知朝代",
          "items": []
        }
      ]
    },
    "map_points": [
      {
        "place_id": "place_001",
        "name": "长平",
        "modern_name": "山西省高平市",
        "longitude": 112.92,
        "latitude": 35.79,
        "events": ["event_001"]
      }
    ]
  }
}
```

没有坐标的地点不进入 map_points，以地点列表文本展示。没有准确时间的事件进入“时间不详/仅知朝代”分组。

`entity_cards` 中事件卡的叙事字段（`aggressor` / `defender` / `action` / `impact` / `place`，2026-09-20 起）：
取自快照 `event_cards.json` 的同名字段，由 `server/fusion/panel_builder.py::_entity_card` 在装配事件卡时映射，
**仅事件卡有值**（人物/组织/地点的卡片为 `null`）；快照缺失时该字段为 `null`，F07 按缺失不渲染。

map_points 选点口径（RAGv5 2026-09-15 起，`server/fusion/panel_builder.py`）：同名地点在快照里有多行
（跨朝代重复），按坐标聚类后**优先取带地址线索（省/今址）的簇**（实测：长平→山西高平市、河内→河南沁阳；
无地址线索的行会被地理编码落到同名村庄），簇内重复行合并、`events` 取并集；点位按相关事件数排序，
最多 8 个。

## 流式事件协议

F01 与 F06 之间建议使用 SSE，事件按顺序推送：

1. session_start：会话开始。
2. status：阶段状态，stage 为 entity_linking、graph_search、text_search、fusion、generating、cache_hit。
3. entities：实体识别结果，结构为 {entities, candidates}。
4. graph_results：图谱证据，SSE 中只携带 evidence，不重复携带展示用 subgraph。
5. text_results：文本证据。
6. fusion：融合结果摘要，可携带 conflicts。
7. thinking：推理模型的思考增量（`data={"delta": "..."}`）。
   **默认不发射**（2026-09-15 审核 P0-3）：推理内容可能包含中间判断与上下文复述，
   公共接口只通过 done 事件的 `first_thinking_ms` / `thinking_frames` 暴露“思考了多久、多少段”，
   不含内容。本地调试设 `EXPOSE_THINKING=true` 后才会收到该事件，对接方可直接忽略。
   开启后 reasoning 字段名两端不同（中转 `reasoning` / 官方 `reasoning_content`），后端同时兼容。
8. answer：最终答案增量。
9. citations：引用与证据对照，可携带 conflicts。
10. panel：知识面板完整数据，F07 直接消费。
11. error：错误。
12. done：回答结束。

每条事件都带 session_id 和 stage。

## SSE 事件 payload

1. session_start：{session_id, stage: "start"}
2. status：{session_id, stage: "entity_linking" 或 "graph_search" 等}
3. entities：{entities: [...], candidates: [...], question_type, rewritten_question,
   dynasty_bias: [...], llm_entity_used: bool, dynasty_disambiguated: bool, elapsed_ms}
   （`llm_entity_used` 为 RAGv5 起新增：本次实体是否来自 F02 的 LLM 兜底——词典完全未命中时才会触发，
   默认关闭 `ENABLE_LLM_ENTITY_FALLBACK`；对接方可据此提示"实体由模型识别，可能有误"）
   （`dynasty_disambiguated` 为 RAGv5 起新增：同名多实体时是否由**问句里提到的朝代**选定——如问
   "西汉的井陉之战"命中西汉那条；只做偏好不做硬过滤，候选集合不变、页面仍可点选纠正）
   （`question_type` / `rewritten_question` / `dynasty_bias` / `elapsed_ms` 同样为超集字段：
   问题类型、F02 改写后问题、朝代软偏置、本阶段耗时；对接方可按需消费，不消费不影响。）
4. graph_results：{evidence: [graph_triple], hit_entities: [...]}
   （`hit_entities` 为超集字段：本次图谱命中的实体名列表，供前端/评测展示。）
5. text_results：{evidence: [raw_text/event_card/evidence], mode: "keyword|vector|hybrid|none"}
   （`mode` 为 **RAGv5 起新增**：本次实际执行的文本检索模式；请求 vector/hybrid 但向量不可用时
   自动降级为 keyword，对接方以此字段为准。`vector_available` 只在内部 TextResult 上，不进 SSE。）
6. fusion：{evidence_count: <条数>, citation_index: [...], conflicts: [...]}
   （**RAGv5 口径**：fusion 事件只携带融合后证据的**条数**，不携带完整证据数组；完整证据由后续
   citations（引用摘要）与 panel 事件承载，对接方不要在 fusion 事件里取 evidence 数组。
   2026-09-15 前的文档曾写为 `evidence: [...]`，以本节为准。）
7. thinking：{delta: “推理增量片段”}（仅 EXPOSE_THINKING=true 时发射，默认不发；对接方可忽略）
8. answer：{delta: “回答增量文本”}
9. citations：{citations: [{index, evidence_id, kind, title, snippet}], conflicts: [...]}
10. panel：使用“知识面板数据结构”中的 data。
11. error：{error_code, message}
12. done：{session_id, finish_reason, model_used?, cache_hit?}
    （`cache_hit` 只在命中回答缓存回放时出现，值为 true；正常路径不发送该字段。）

entities 事件中的候选列表用于前端实体卡展示和手动纠正。

done 事件的 finish_reason 枚举：

1. normal：正常完成。
2. refused：证据不足拒答（含硬规则拒答与模型自拒，见 F06 拒答判定规则第 4 条）。
3. degraded：使用备用模型或离线摘要回答器完成，model_used 记录实际模型。
4. cancelled：用户主动取消（前端 abort）。
5. failed：服务端内部异常收尾（error 事件之后必推 failed，不再复用 cancelled——
   否则前端会把异常轮误判成正常结束并写进下一轮历史）。
6. interrupted：正文已部分送达后中断（重试耗尽/网络中断），回答可能不完整；
   **不得**作为下一轮问答的历史上下文。

SSE 连接断开后服务端无法再向前端推送 done；此时前端把该轮收敛为 interrupted
（EOF 无 done）。服务端在连接存活时主动终止生成，按原因推送 cancelled 或 failed。

终态与历史（前端口径）：只有 normal / refused / degraded 可作为后续多轮的历史上下文；
cancelled / failed / interrupted 一律排除。被实体纠正或重试取代的轮次由 `supersededBy`
标记，同样不进入历史，历史使用纠正后的新回答。

error_code 枚举：

1. retrieval_empty：无检索结果。
2. llm_timeout：大模型超时。
3. llm_unavailable：大模型不可用且无可用备用模型。
4. invalid_request：请求格式不合法（含字段长度/数量超限）。
5. rate_limited：超过限流配额（HTTP 429）。
6. payload_too_large：请求体超过 `REQUEST_MAX_BYTES`（HTTP 413）。
7. internal：内部错误。

非 SSE 的 4xx 返回 JSON：`{"status":"error","error_code":...,"message":...}`；
进入 SSE 之后才发生的内部错误仍走 `error` + `done(finish_reason=failed)`。

## 流取消

1. SSE 连接断开视为取消请求。
2. 后端在生成器 `finally` 中取消并 await 模型任务（`server/sse.py`），
   API 层心跳包装器关闭时同样回收内层生成（`server/api.py`），避免继续消耗 token。
3. 取消后如需纠正重查，前端重新建立连接并发送新请求。
4. 保活与上限：空闲超过 `SSE_HEARTBEAT_SECONDS` 发送 `: ping` 注释行；
   单次问答超过 `SSE_MAX_DURATION_SECONDS` 以 `error` + `done(failed)` 收流
   （默认 15s / 300s）。
5. 正文已开始输出后不再透明重试（`server/generate/llm_client.py`）：
   失败以 `interrupted` 收敛并保留部分正文，避免两次尝试的文本被拼成重复答案。

conflicts 示例：

```json
{
  "subject": "长平之战",
  "field": "发起方",
  "evidence_ids": ["graph_event_person_relations_772", "card_001"],
  "conflict_type": "field_vs_triple",
  "description": "图谱三元组与事件卡片结构化字段存在不同表述"
}
```

前端可据此在回答或知识面板中提示“存在不同说法”。

## 冲突与证据合并

1. 初版不做通用语义矛盾检测。
2. 初版只对结构化数据做冲突判定，范围限定为：
   - graph_triple 与 graph_triple：同 subject、同 relation、不同 object；且**仅对单值关系生效**
     （field_map 中 exact 单值组，如发起方/防守方）——多值关系（主帅/将领等）是并列事实行，
     多个 object 不判冲突，避免误报。
   - graph_triple 与 event_card：图谱关系字段与事件卡片结构化字段不一致。
3. raw_text 和 evidence 不参与自动冲突判定，只作为说明性证据保留。
4. conflict_type 取值：different_object、field_vs_triple。
5. 冲突以 conflicts 字段随 fusion 或 citations 事件推送。
6. 是否支持更细的文本语义冲突判断，由 F10 评测后的真实案例决定。

## field_vs_triple 字段映射

图谱关系与事件卡片字段做自动对比时，只使用下表：

| 图谱 relation | 事件卡片字段 | 判定方式 |
| --- | --- | --- |
| 发起方 | 发起方 | 文本归一化后不一致即产生冲突 |
| 防守方 | 防守方 | 文本归一化后不一致即产生冲突 |
| 主战场/发生地点等地点关系 | 地点 | 事件卡片地点字段不包含三元组 object 地点名即产生冲突 |
| 统帅/将领/人物类关系 | 人物 | 人物名称不在事件卡片人物字段中出现即产生冲突 |

人物类关系只做“是否出现”对比，不做职务语义对比。

该映射表由 F09 随治理快照输出，F05 冲突判定时读取，不在各模块硬编码。
