# data/index

**归属功能：F11 文本切分与索引构建（离线）。**

读取 F09 治理快照的文本语料（原始正文、事件卡片、关系证据），统一切分成可检索片段，
构建 **关键词索引（SQLite FTS5）** 与**向量索引**，输出带版本号的索引目录，
供 F04 在线文本检索读取。

## 三类语料（F11 全部切分入库）

| 语料 | 快照来源 | chunk_type | 回答场景 |
| --- | --- | --- | --- |
| 原始正文 | `data/raw/source_texts/*.txt`（旧原文） | `raw` | 背景、过程、细节 |
| 事件卡片 | `snapshot/<v>/event_cards.json` | `event_card` | 事件结构化描述 |
| 关系证据 | `snapshot/<v>/evidence_corpus.json` | `evidence` | 精确引用/溯源 |

## 索引结构

```text
data/index/<YYYYMMDD_vN>/
├── manifest.json           # 版本 + 来源快照版本 + 切分参数 + 各语料片段数
├── chunks.jsonl            # 全部切分片段（TextChunk，见 contracts/index.py）
├── chunks_fts.db           # SQLite FTS5 关键词索引（external content 指向 chunks）
└── vectors/
    ├── ids.json            # 片段 id 列表（与矩阵行对齐）
    └── embeddings.npy      # float32 矩阵 [N, dim]（云端模型；无密钥时为空占位）
```

`chunks_fts.db` 的 `chunks` 表保存 chunk_id/chunk_type/doc_id/source_version、
event_id/event_name/event_type/dynasty 等元数据列，供 F04 按朝代/战争类型筛选；
其中 event_type 目前只对事件卡片片段有值。

## 切分规则（对应 F11 需求）

1. **事件卡片**以事件为最小单元（一条卡片→一条/多条片段）。
2. **原文**按 段落→句子 边界切分，超过 `max_chars` 再切。
3. 相邻片段保留少量重叠（`overlap_chars`），降低跨片段信息丢失。
4. 每条片段保留：chunk_type、doc_id、event_id/name、event_type、dynasty、start_date、
   source、related_entities，保证可回溯（验收：没有无法回溯来源的片段）。
5. 向量模型经 `EMBEDDING_*` 配置；无密钥时 `INDEX_BUILD_EMBEDDINGS=false` 只建 FTS5。

## 中文关键词检索

FTS5 官方分词对中文按整句/标点切，检索词无法命中子串。本项目做法：
**用 jieba 对片段与查询分词，把分词结果以空格拼接存入 FTS5 的 keywords 列**，
检索时查询词同样分词后匹配（初版 `query_fts` 用 OR + BM25 排序，召回优先；
AND/加权等精确策略属 F04 融合层，按 F10 评测结果再定）。词典 + 索引均保存在
索引目录，随版本走。

## 版本一致性

- 索引版本号 = 其来源快照版本号（`build_index` 默认取最近快照，也可 `--version` 指定）。
- `manifest.source_snapshot` 记录来源快照版本；F04 运行时核对两者一致。

## 运行

```bash
python scripts/build_index.py --version 20260904_v2        # 默认取最新快照版本
python scripts/build_index.py --no-embeddings               # 无向量模型密钥时只建 FTS5
```

## 验收对照

- F11 验收：无无法回溯片段 ✓（每条含 doc_id/chunk_type）；FTS5 与向量索引可生成 ✓；
  报告记录切分参数与数量 ✓；版本号与快照一致 ✓。

## 边界 / 不做什么

- 不做检索（F04 的事）；本层只构建。
- 云端向量不可用时：降级为只建 FTS5（不改变索引目录结构）。
