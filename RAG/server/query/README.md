# server/query（F02）

**归属功能：F02 问题理解、实体识别与消歧（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `dictionary_matcher.py` | 词典/规则实体识别：实体标准名+别名贪心最长匹配；事件"XX之战"式简称兜底（如"牧野之战"→"周武王灭商牧野之战"）；返回命中（含同名多实体）。 |
| `classifier.py` | 问题类型判定规则、朝代过滤器抽取、指代词检测。 |
| `prompts.py` | LLM 兜底的抽取提示词与容错解析（markdown/散文混杂也能取 JSON；类型过滤、去重）；`should_fallback()` 判定"词典零命中且开关打开"。 |
| `llm_fallback.py` | `EntityFallbackClient`：同步 OpenAI 兼容客户端，独立超时 `LLM_ENTITY_TIMEOUT_SECONDS`（默认 8 s）、`max_tokens=1024`（推理模型的思考会吃满小值，导致正文为空）。 |
| `understand.py` | 编排：词典识别 → 歧义降级（同名全部进 candidates）→ 指代消解 → corrected_entities 纠正 → 类型判定 → rewritten_question 生成；词典零命中且开关打开时走 LLM 兜底。 |

## 设计要点

1. **词典命中即跳过 LLM**（默认路径）：`load_understanding` 建 DictionaryMatcher，
   控制首 Token 预算。
2. **LLM 兜底（RAGv5 落地，开关默认关闭）**：`ENABLE_LLM_ENTITY_FALLBACK=true` 且
   **词典零命中**才触发；失败/超时/解析失败一律降级回词典路径，结果按问句进程内缓存
   （失败不缓存）。兜底实体 `confidence="low"`、`entity_id=None`（未对齐知识库），
   可观测字段 `F02Output.llm_entity_used` 进 entities 事件与评测 trace。
3. **同名多实体：朝代偏好 + 候选纠正**：同一 mention 命中多实体（如井陉之战战国/西汉、洛阳 20+ 条）→
   问句里提到的朝代若与某候选相符，该候选**前置**成为 `entities`（可观测字段 `dynasty_disambiguated=True`）；
   **只做偏好、不做硬过滤**（v4 教训：硬过滤会把"所属朝代与问句不同"的事件整题剔除、直接拒答）；
   全部候选仍进 `candidates`（带 dynasty/event_type/entity_id）供前端点选纠正。
4. **简称兜底**：标准名不含"牧野之战"这类口语写法时，用事件全名包含匹配（置信度 medium）。
5. 指代消解用历史轮重匹配；corrected_entities 支持 add/replace/remove。

## 输入 / 输出

- 输入：QueryRequest（question/history/filters/corrected_entities）
- 输出：F02Output（rewritten_question/question_type/entities/candidates/filters/llm_entity_used）

## 边界

- 兜底开关默认关闭：开启前用评测确认不引入错误实体（词典未命中是低频但可见的场景）。
- 词法与规则无法覆盖的口语复杂句，最终仍以 F10 评测口径决定开关档位。
