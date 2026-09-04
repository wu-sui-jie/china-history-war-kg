# server/query（F02）

**归属功能：F02 问题理解、实体识别与消歧（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `dictionary_matcher.py` | 词典/规则实体识别：实体标准名+别名贪心最长匹配；事件"XX之战"式简称兜底（如"牧野之战"→"周武王灭商牧野之战"）；返回命中（含同名多实体）。 |
| `classifier.py` | 问题类型判定规则、朝代过滤器抽取、指代词检测。 |
| `understand.py` | 编排：词典识别 → 歧义降级（同名全部进 candidates）→ 指代消解 → corrected_entities 纠正 → 类型判定 → rewritten_question 生成。 |

## 设计要点（RAGv2 规划第 2 节落地）

1. **词典命中即跳过 LLM**（默认路径）：`load_understanding` 建 DictionaryMatcher，
   `enable_llm=False` 兜底关闭，控制首 Token 预算。
2. **同名歧义降级**：同一 mention 命中多实体（如井陉之战战国/西汉）→ entities 取首选，
   全部进 candidates（带 dynasty/event_type/entity_id 供前端纠正）。
3. **简称兜底**：标准名不含"牧野之战"这类口语写法时，用事件全名包含匹配（置信度 medium）。
4. 指代消解用历史轮重匹配；corrected_entities 支持 add/replace/remove。

## 输入 / 输出

- 输入：QueryRequest（question/history/filters/corrected_entities）
- 输出：F02Output（rewritten_question/question_type/entities/candidates/filters）

## 边界

- 不含 LLM 实现；`llm_client` 传入 + `enable_llm=True` 可扩展词典未命中路径。
- 词法与规则无法覆盖的口语复杂句，待 F10 评测后决定是否开 LLM。
