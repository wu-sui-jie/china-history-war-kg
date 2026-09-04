# server/text（F04）

**归属功能：F04 文本检索通道（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `searcher.py` | TextSearcher：读 F11 FTS5（chunks_fts.db），关键词 AND 优先/OR 兜底（OR 剔除单字虚词控噪），min-max 归一 score，向量可用性启动探测。 |
| `scoring.py` | 模式开关（keyword/vector/hybrid）与解析：向量不可用时自动降级 keyword。 |
| `__init__.py` | load_searcher() / search() → TextResult{evidence, mode, vector_available}；chunk_type → Evidence kind/source_type/confidence 装配；事件卡片 content 携带结构化字段。 |

## 设计要点（RAGv2 规划第 4 节落地）

1. F04 只做检索，不做切分/索引（那是 F11）。
2. 三种可切换模式（keyword/vector/hybrid）：当前默认 keyword；
   **向量加载校验** `len(ids) == embeddings.shape[0]`（占位态 (0,0) → vector_available=False
   → 自动关键词，代码检查而非仅风险文字）。
3. 各模式内部 min-max 归一 score 到 0~1（data-contract 约定：max==min → 0.5）。
4. 过滤条件作为索引元数据过滤（dynasty/chunk_type 等）。

## 输入 / 输出

- 输入：改写后问题 + filters + mode + top_k
- 输出：TextResult{evidence: raw_text/event_card/evidence, mode, vector_available}

## 边界

- 检索策略（AND-first vs OR/BM25）是 F04 初检基线，最终以 F10 评测为准。
- 向量检索实现点已在 scoring/searcher 留出（vector_available=True 后补余弦 top-k）。
