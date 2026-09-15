# server/text（F04）

**归属功能：F04 文本检索通道（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `searcher.py` | TextSearcher：读 F11 FTS5（chunks_fts.db）关键词 AND 优先/OR 兜底（OR 剔除单字虚词控噪），min-max 归一 score；向量通道走 Chroma（`distance = 1 − 余弦` 换算后归一）；`search_hybrid()` 融合关键词与向量；`vector_available` 启动探测（集合可加载且 `count()` 与 `ids.json` 一致才为真）。 |
| `scoring.py` | 模式判定（keyword/vector/hybrid）与融合：`fuse_weighted` / `fuse_rrf` / `fuse_hybrid`（策略由 `TEXT_HYBRID_STRATEGY` 定，实测定档 `rrf`）；向量不可用时自动降级 keyword。 |
| `__init__.py` | load_searcher() / search() → TextResult{evidence, mode, vector_available}；chunk_type → Evidence kind/source_type/confidence 装配；事件卡片 content 携带结构化字段。 |

## 设计要点

1. F04 只做检索，不做切分/索引（那是 F11）。
2. 三种可切换模式（keyword/vector/hybrid）：**部署级开关 `TEXT_MODE`**（仓库默认 keyword，
   演示 `.env` 设 `hybrid`）；**加载校验**用 Chroma `count()` 与 `ids.json` 比对，
   集合缺失/损坏/条数不一致 → `vector_available=False` → 自动降级关键词，不报错。
3. 各模式内部 min-max 归一 score 到 0~1（data-contract 约定：max==min → 0.5）；
   hybrid 在各通道内先归一、融合后对融合分再归一。
4. 过滤条件作为索引元数据过滤（dynasty/event_type/chunk_type）；对没有该元数据的原文片段放宽，
   不整批剔除（RAGv5 §2.5-3 修复）。
5. 通道取回条数由 `QUERY_TOP_K_TEXT` 决定；送入融合的条数另由 `QUERY_FUSION_LIMIT` 裁剪。

## 输入 / 输出

- 输入：改写后问题 + filters + mode + top_k
- 输出：TextResult{evidence: raw_text/event_card/evidence, mode, vector_available}

## 边界

- 检索策略（AND-first vs OR/BM25）是 F04 初检基线，最终以 F10 评测为准；向量/hybrid 的收益已在
  RAGv5 四配置对照中量化（回答覆盖 71.7% → 85.1%，长改写题 AND 0% → vector/hybrid 100%）。
- HNSW 是近似检索：`scripts/check_vector_consistency.py` 抽样比对 Chroma top-k 与暴力余弦 top-k
  的重合率，下限 0.9（实测均值 0.93）。
