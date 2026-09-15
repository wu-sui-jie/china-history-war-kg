# RAG 数据契约

- 文档类型：接口与数据契约
- 状态：初稿
- 创建时间：2026-09-03

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
`docs/changes/20260913-ragv4-review-summary.md`。

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

confidence 取值：

1. high：来源明确且证据支持。
2. medium：来源存在但内容可能不完整。
3. low：只属于候选或共现关系。
4. pending_review：仅存在于 F09 治理阶段，需要人工审核。

pending_review 数据不进入 F03 可查询图谱，也不作为运行链路证据返回；它只保留在治理报告中。

score 取值范围为 0 到 1。F04 文本检索结果必须输出 score；F03 图谱证据的 score 可选。

F04 归一化方法：先取当前查询 Top N 候选的原始分，再按 min-max 归一化。max 等于 min 时，该条候选的归一化 score 记为 0.5。关键词、向量、混合三种模式都在各自模式内部完成该归一化。score 只用于排序和证据融合，不用于拒答阈值。

citation_index 由 F05 统一分配，F06 在回答中使用，F07 展示引用。

## 图谱三元组证据

> evidence_id 格式（F03）：`graph_{legacy表名}_{source_row_id}`，如
> `graph_event_person_relations_772`。`source_row_id` 只在各 legacy 表
> （event_person_relations / event_place_relations / event_event_relations /
> event_organization_rel）内唯一，跨表会重复，因此 ID 必须带表名以保证全局唯一
> （缺行号时用含表名的内容哈希回退）。见 `docs/changes/20260913-ragv4-review-summary.md`。

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
        "source": "来源标识"
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
7. thinking：推理模型的思考增量（**RAGv5 起实际发射**：`data={"delta": "..."}`）。
   仅在生成阶段、模型流式输出推理时发送，可能有多帧；对接方可直接忽略。
   注意：中转 endpoint 的推理字段为 `reasoning`、官方为 `reasoning_content`，后端已同时兼容。
8. answer：最终答案增量。
9. citations：引用与证据对照，可携带 conflicts。
10. panel：知识面板完整数据，F07 直接消费。
11. error：错误。
12. done：回答结束。

每条事件都带 session_id 和 stage。

## SSE 事件 payload

1. session_start：{session_id, stage: "start"}
2. status：{session_id, stage: "entity_linking" 或 "graph_search" 等}
3. entities：{entities: [...], candidates: [...], llm_entity_used: bool, dynasty_disambiguated: bool}
   （`llm_entity_used` 为 RAGv5 起新增：本次实体是否来自 F02 的 LLM 兜底——词典完全未命中时才会触发，
   默认关闭 `ENABLE_LLM_ENTITY_FALLBACK`；对接方可据此提示"实体由模型识别，可能有误"）
   （`dynasty_disambiguated` 为 RAGv5 起新增：同名多实体时是否由**问句里提到的朝代**选定——如问
   "西汉的井陉之战"命中西汉那条；只做偏好不做硬过滤，候选集合不变、页面仍可点选纠正）
4. graph_results：{evidence: [graph_triple]}
5. text_results：{evidence: [raw_text/event_card/evidence], mode: "keyword|vector|hybrid|none"}
   （`mode` 为 **RAGv5 起新增**：本次实际执行的文本检索模式；请求 vector/hybrid 但向量不可用时
   自动降级为 keyword，对接方以此字段为准。`vector_available` 只在内部 TextResult 上，不进 SSE。）
6. fusion：{evidence: [...], citation_index: [...], conflicts: [...]}
7. thinking：{delta: “推理增量片段”}（RAGv5 起实际发射；对接方可忽略）
8. answer：{delta: “回答增量文本”}
9. citations：{citations: [{index, evidence_id, kind, title, snippet}], conflicts: [...]}
10. panel：使用“知识面板数据结构”中的 data。
11. error：{error_code, message}
12. done：{session_id, finish_reason, model_used?}

entities 事件中的候选列表用于前端实体卡展示和手动纠正。

done 事件的 finish_reason 枚举：

1. normal：正常完成。
2. refused：系统判定证据不足并拒答。
3. degraded：使用备用模型完成，model_used 记录实际模型。
4. cancelled：连接断开或请求取消。

SSE 连接断开后服务端无法再向前端推送 done；cancelled 主要用于服务端日志和统计。若后端在连接仍存活时主动终止生成，也可以推送 finish_reason=cancelled。

error_code 枚举：

1. retrieval_empty：无检索结果。
2. llm_timeout：大模型超时。
3. llm_unavailable：大模型不可用且无可用备用模型。
4. invalid_request：请求格式不合法。
5. internal：内部错误。

## 流取消

1. SSE 连接断开视为取消请求。
2. 后端检测到连接断开时立即终止大模型生成，避免继续消耗 token。
3. 取消后如需纠正重查，前端重新建立连接并发送新请求。

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
   - graph_triple 与 graph_triple：同 subject、同 relation、不同 object。
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
