# RAGv5 开发说明：F08 演示模式 + 真实 LLM/向量接入与部署打磨

- 文档类型：阶段**开发说明**。本文档为**开工前设计版**：现状核查、文件级改动、设计口径、
  实施步骤、验证方案与部署手册已定；**实现完成后回填实测数据、最终行号与产物路径**，
  并把状态改为"已完成"（回填清单见 §十）。
- 状态：🚧 设计完成，待开工
- 日期：2026-09-13
- 需求与验收标准：见 [RAGv5-规划说明.md](RAGv5-规划说明.md)（本文不重复验收判据，只写"怎么做/改哪里/怎么验"）
- 上游依据：[后续阶段规划.md](后续阶段规划.md) §三、[RAGv4-阶段审核报告.md](RAGv4-阶段审核报告.md)
  §十八（v4 边界）、§二十（现成输入）、§28.3（C1–C3 任务缺口）、§28.4（R1–R2 技术风险）
- 行号说明：§二/§三/附录 C 中的 `文件:行号` 是**2026-09-13 开工前基线快照**，实现过程中会漂移，
  改动前用关键词复查一次。

---

## 一、开发总览与交付判据

v5 的六条主线与最小交付判据（详细验收见规划说明 §二）：

| 线 | 一句话目标 | 触发判据（跑完即知） |
| --- | --- | --- |
| T1 F08 演示模式 | 首页示例题来自已审核题库，带能力标签，点击即问 | `data/eval/<v>/demo_examples.json` 生成 + 冒烟逐题通过 |
| T2 真实 LLM | F06 走真 token 流式、F02 兜底可用、降级不污染缓存 | `answer` 帧为多次 token 增量；断网 `finish_reason=degraded` 且未入缓存 |
| T3 向量检索 | 索引真含向量（百炼 v4 + Chroma），`vector/hybrid` 上线可用且可观测 | `manifest.vectors.mode="chroma"`；`chroma.count()==len(ids)`；一致性抽检 ≥0.9；`meta.text_mode` 随配置变化；四配置对照报告 |
| T8 分块实验 | 用数据回答"800/80 是否合适" | `chunk_exp_report.md` 三组对照 + 切换/保持结论（**先于全量建向量**） |
| T4 部署 | 单进程同源托管，一条冒烟脚本自证可用 | `python scripts/smoke_deploy.py` 退出码 0 |
| T5 质量优化 | 拒答补强、AND 兜底、筛选元数据三项量化回升 | 拒答 3 题转 `unknown_answer`；F01 文本召回回升 |
| T6/T7 对照与收口 | 同题库对照报告 + 契约/索引/功能状态同步 | 报告与基线并排；文档状态一致；`pytest` 全绿 |

**改动性质总览**：T2/T3/T4 都有"看着只需接线、实际要新建"的部分（假流式、构建侧向量接线、
静态托管），这是本阶段工作量的主要来源，逐条见 §二、§三。

---

## 二、开工前现状核查（文档声明 → 代码实况 → 改动性质）

| # | 组件 | 文档/直觉声明 | 代码实况（证据） | 改动性质 |
| --- | --- | --- | --- | --- |
| 1 | 向量构建 | "全量重建索引即可产出向量" | `data/index/build.py:61` **无条件**调用 `build_vector_placeholder`；`data/index/vectors.py:31 build_vectors(embed_fn=...)` 全仓**无调用点**；`scripts/build_index.py:24-26` 的 `--no-embeddings` 仅影响 `build.py:62-63` 一行日志 | **新建**：embed 客户端 + `build.py` 接线 |
| 2 | 向量加载/降级 | "校验在 vectors.py" | 校验在 `server/text/searcher.py:45-60`（`len(ids)==shape[0]`、`allow_pickle=False`），降级在 `server/text/scoring.py:19-26 resolve_mode` | 文档引用已修正，无需改码 |
| 3 | F04 模式分支 | "vector 返回空、hybrid 待实现" | `server/text/__init__.py:50-53` hybrid 走 `search_keyword`（等同 keyword，无融合）；`:54-56` vector 返回 `[]`，且该分支仅在 `vector_available=True` 时可达 | **新建**：向量 top-k + hybrid 融合 |
| 4 | 文本模式开关（C2） | "配置已有 TEXT_MODE" | `config/settings.py` 与 `.env.example` 中**不存在** `TEXT_MODE`；`server/sse.py:160-163` 写死 `mode="keyword"`；`server/runtime.py:108` 写死 `"text_mode": "keyword"` | **新建**：配置项 + 三处接线 |
| 5 | 回答缓存键（C3） | "纳入 mode" | `server/generate/cache.py:24-35 cache_key` 五段（改写问题 / 历史尾 400 字 / filters / source_version / model），无 mode；`dynasty_bias` 是经 `server/sse.py:112-115` 并入 filters 间接进键 | **新建**：mode 进键 |
| 6 | 真实流式（T2） | "token 回调已预留，接上即可" | `server/generate/llm_client.py:52-105` 流式与 `on_delta` **已完整实现**；`server/generate/__init__.py:45-82 generate()` 也已支持 `on_delta`；缺的是 **`server/sse.py:227` 传的是 `lambda _: None`**，且 `run_query` 是"先 `await` 出全文、再 `_chunk_answer_stream` 按句切"（`:223-249`） | **改造**：并发桥接（见 §四.4），非一行接线 |
| 7 | F02 LLM 兜底（C1） | "翻开关即可" | `server/query/understand.py:40-45` 存了 `llm_client/enable_llm`，`understand()`（`:98-187`）**无任何 LLM 分支**；`server/runtime.py:83-84` 恒传 `None/False` | **新建**：兜底分支 + 降级 + 用例 |
| 8 | 静态托管（T4） | "dist 已可产出，托管即可" | `frontend/dist/` 已构建；后端**无** `StaticFiles`/`app.mount`，路由仅 `GET /api/dicts`（`api.py:105`）、`GET /api/health`（`:141`）、`POST /api/query`（`:153`） | **新建**：挂载 + 配置项 |
| 9 | 示例题接口 | "后端加路由或构建期生成" | 无 `/api/demo/examples`；前端 `ChatPane.vue:11-16` 硬编码 4 条（其中"牧野之战…""井陉之战…"不在题库） | **新建**：生成脚本 + 路由 + 前端渲染（✅ 已完成） |
| 10 | `demo_mode` | "配置已定义" | `config/settings.py:65/130` 定义，`server/`、`frontend/` **零引用**（死配置） | ✅ 已删除（§四.7 结论②） |
| 11 | 拒答规则宿主 | "改 refusal.py" | 生效规则在 `server/sse.py:189-216`（`refusal.py:14-22` 只提供判空与文案）；`evaluation/chain.py:230-237` 是**同口径副本** | 改规则**必须两处同步** |
| 12 | 评测对照（B2 余项） | "已有 mode 可直接跑对照" | `EvalConfig.mode`（`evaluation/chain.py:33`）与透传（`:189`）已具备；但 `--configs` 仅认 `dual/text-only/text-only-and/text-only-or`（`chain.py:56-64`），无 vector/hybrid 预设 | **新建**：预设 + CLI 选择 |
| 13 | 契约 | "text_results 只有 evidence" | 实现已带 `mode`（`server/sse.py:174`），`contracts/retrieval.py:67-72` 有 `TextResult.mode/vector_available`，但 `docs/data-contract.md` 未记录 | **补契约**（T7） |
| 14 | 拒绝误伤风险（新发现） | — | 第二条硬规则按"证据与问题是否有 ≥2 字共享词"判定（`server/sse.py:204-216`）；向量召回语义相关但无共同词的片段会被判为"不相关"→ 误拒 | **必须按模式分档**（§四.2） |
| 15 | 缓存污染风险（新发现） | — | `server/sse.py:241` 无条件下写缓存，`degraded`（网络抖动/降级）结果会以 1h TTL 被回放 | **限 normal/refused 入缓存** |
| 16 | 缓存是进程内的（新发现） | "重复提问命中缓存" | `server/generate/cache.py:41-55` 进程内 dict + TTL，`cache_dir` 参数未使用 → **重启即失** | 冒烟脚本须同进程复现；部署必须 `workers=1`（`scripts/run_server.py` 已固定） |

### 离线数据链路全景：清洗 → 分块 → 向量化 → 存储 → 检索

> 本节回答"这些环节分别在哪一层、依据什么做的、哪些还是空白"。**清洗与分块是 RAGv1 的既成交付（F09/F11）**，
> 向量化与存储**此前没有实现**（只有接口与占位），模型、存储、检索算法三项均于 2026-09-13 才拍板。

| 环节 | 归属 | 现状 | 实现位置 | 依据 / 参数 / 规则 | v5 动作 |
| --- | --- | --- | --- | --- | --- |
| 数据清洗与治理 | F09（RAGv1，已完成） | ✅ 已落盘 `data/snapshot/20260904_v2/` | `data/snapshot/export.py`（只读导出旧 SQLite + 原文）、`alias.py`（别名词典）、`normalize.py`（战争类型同义归一、朝代归一）、`field_map.py`（关系-事件卡片字段映射）、`isolate.py`（孤立节点标记）、`governance.py`（编排）、`apply_audit.py`（人工决定回填） | 规则 + 词典；**不做静默自动合并**：同名歧义 1,215 组列 `audit/duplicate_name_groups.json` 待人工审核；孤立节点保留并标 `is_isolated`（不删）；地点坐标覆盖率 0 如实记录（`entities.json` 无经纬度字段值） | 仅可选项：1,215 组同名实体人工回填（规划说明 §2.5-4） |
| 语料归一 | F11 前置 | ✅ | `config/defaults.py` 的 `RAW_DIR` → `data/raw/source_texts/doc01_中国历代战争简史.txt`（892 KB）+ `doc02_中国战争史地图集.txt`（1.06 MB） | 旧 `entity-event-relation/data/*.txt` 的只读归一拷贝 | 不动 |
| 数据分块 | F11（RAGv1，已完成） | ✅ 9,544 片段 | `data/index/chunking.py`（`build_chunks`） | 三类语料：原文 `raw` / 事件卡片 `event_card` / 关系证据 `evidence`。原文按空行切段，段长 > `CHUNK_MAX_CHARS=800` 时按句子边界（`。；;！？!` + 换行）聚合成 ≤800 字片段，相邻片段重叠 `CHUNK_OVERLAP_CHARS=80` 字，单句超长则硬切；事件卡片以事件为最小单元；关系证据保留为短文本（不重叠）。每条片段带 `doc_id/chunk_type/event_id/event_name/event_type/dynasty/start_date/source/related_entities` | **T8**：500/100、800/80、1200/120 三组小规模对比实验（用户已确认执行） |
| 关键词索引 | F11（已完成，F04 在用） | ✅ | `data/index/fts.py` | SQLite FTS5；**jieba 分词**（构建时把快照实体名/别名与标准事件类型加入词典）后空格拼接存入 `keywords` 列；`chunks` 主表存 `chunk_id/chunk_type/doc_id/source_version/event_id/event_name/event_type/dynasty/text`；检索 `query_fts` 为 OR + BM25（生产侧 `search_keyword` 为 AND 优先 / OR 兜底） | 为 raw/evidence 补 `event_type` 元数据（规划说明 §2.5-3），须重建索引 |
| 向量化 | F11（**此前未实现**） | ❌ 仅接口与占位 | `data/index/vectors.py:31 build_vectors(embed_fn=...)` 无调用点；`data/index/build.py:61` 无条件写占位；`EMBEDDING_*` 四项配置全仓零读取 | **模型已定（2026-09-13）：阿里云百炼 `text-embedding-v4`**，OpenAI 兼容接口（`https://dashscope.aliyuncs.com/compatible-mode/v1`）；维度默认 1024（可选 2048/1536/1024/768/512/256/128/64），本阶段取 **1024**；**单请求最多 10 条文本**、单条 ≤ 8,192 token；`encoding_format="float"` | **T3**：新增 embed 客户端 + 构建侧接线（batch ≤10、重试、断点续跑、维度断言） |
| 向量存储 | F11（**此前未实现**） | ❌ 无（原设计只有 `ids.json` + `embeddings.npy`，且为 `(0,0)` 占位） | 待建 | **已定（2026-09-13）：Chroma 持久化库**——`chromadb.PersistentClient(path=data/index/<v>/vectors/chroma)`，`create_collection(name, metadata={"hnsw:space": "cosine"})`；`ids.json` 保留（id 行对齐与校验）；`embeddings.npy` 保留为**审计副本**（不参与检索，见 §四.3） | **T3**：新增 `data/index/chroma_store.py`；`requirements.txt` 增 `chromadb` |
| 向量检索 | F04（**此前未实现**） | ❌ `server/text/__init__.py:54-56` 返回空 | 待建 | **算法已定（2026-09-13）：Chroma 内建 HNSW 近似最近邻 + 余弦距离**；注意 Chroma 返回的是 **distance = 1 − 余弦相似度**（越小越相似），须转成相似度后再 min-max；元数据过滤走 `where`（前置过滤，先筛后 ANN） | **T3**：`search_vector()` 走 Chroma；抽检与暴力余弦的一致性（§六.1） |
| 混合检索 | F04（**此前未实现**） | ❌ `hybrid` 等同纯 keyword（无融合） | 待建 | 融合口径 ⏳ 待定，见 §四.2（默认线性加权 + 可切换档位，由评测决定） | **T3** |

```text
旧项目只读数据（backend/database/*.sqlite + entity-event-relation/data/*.txt）
   │  F09 导出与治理：别名词典 / 战争类型与朝代归一 / 孤立节点标记 / 关系-卡片字段映射
   │                同名歧义 1,215 组列待人工项（不静默合并）
   ▼
data/snapshot/<v>/   entities.json · relations.json · event_cards.json · evidence_corpus.json
                     dicts.json · relation_card_field_map.json · governance_report.json · audit/
   │  F11 分块：原文按「段落→句子边界 + 800 字聚合 + 80 字重叠」；事件卡片以事件为最小单元；证据短文本
   ▼
data/index/<v>/      chunks.jsonl（9,544 片段：raw 934 + event_card 1,107 + evidence 7,503；合计约 314 万字符）
   ├─ chunks_fts.db        SQLite FTS5 + jieba 分词 + BM25          ✅ 已实现，F04 keyword 在用
   └─ vectors/             ids.json ✅ · embeddings.npy ⚠(0,0) 占位 · chroma/ ❌待建   ← T3
   ▼
F04 检索通道：keyword（BM25）✅   /   vector（Chroma HNSW + 余弦）❌   /   hybrid（融合）❌
```

**分块质量实测（2026-09-13 统计，供 T8 实验对照）**：片段 9,544 条、总字符 3,143,645、
平均 329 字、中位 240 字、最长 799 字（符合 800 上限）；**最短仅 2 字，且 <10 字的过短片段
共 105 条、全部为 `evidence` 类型**——向量化后可能成为噪音命中源，T8/T3 需统计披露（是否
排除入向量库由实验决定，不直接丢弃，因为证据片段本身是精确溯源的载体）。

**向量化成本与请求量估算（1024 维、batch=10）**：9,544 片段 → **约 955 次请求**；
文本总量约 314 万字符（中文约 200–300 万 token 量级），按百炼 v4 约 0.0005 元/千 token 估算，
**全量重建成本约数元人民币**；断点续跑按批落盘（§四.3），中断重跑不重复计费已完成的批次。

---

## 三、文件级改动清单

> 记号：🆕 新建文件 / ✏️ 修改 / ⚙️ 配置或数据产物

### T1 F08 演示模式

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `scripts/gen_demo_examples.py` | 🆕 | 读题库 + 最近一次 run 的 `scores.jsonl`，过滤 `reviewed=True` 且 `answer_correctness != "incorrect"`，输出 `data/eval/<v>/demo_examples.json`（结构见 §四.7），打印保留/丢弃计数 |
| `data/eval/20260904_v2/demo_examples.json` | ⚙️ | 入 Git、可人工复核的示例清单（含 `capability` 与证据下限） |
| `server/api.py` | ✏️ | 新增 `GET /api/demo/examples`：读清单文件 → 原样返回；文件缺失返回 503（前端静默降级，不回退硬编码） |
| `frontend/src/api/demo.ts` | 🆕 | `fetchDemoExamples()`（相对路径 `/api/demo/examples`） |
| `frontend/src/types/contract.ts` | ✏️ | 示例题类型（id / question / category / capability / expect） |
| `frontend/src/components/chat/ChatPane.vue` | ✏️ | 删除 `EXAMPLES` 常量（`:11-16`）；改为加载示例 → 按 `category` 分组渲染按钮 + 能力标签；加载失败只显示引导文案 |
| `config/settings.py`、`.env.example`、`config/defaults.py` | ✏️ | ✅ 按 §四.7 结论②**删除** `demo_mode`（示例区恒显示），不留死配置 |

### T2 真实 LLM

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `server/sse.py` | ✏️ | ① `answer` 帧改为 `asyncio.Queue` 桥接的真实增量（§四.4）；② LLM 不可用时保留 `_chunk_answer_stream` 的按句切分（离线演示的打字机效果）；③ 仅 `normal/refused` 写缓存（§四.9） |
| `server/query/understand.py` | ✏️ | 新增 LLM 兜底分支与降级（§四.5）；docstring `:12-16` 的"预留，尚未实现"改为实现后描述 |
| `server/query/prompts.py` | 🆕 | F02 兜底提示词 + 结构化解析（与 `server/generate/prompts.py` 分开，避免 F02/F06 提示词耦合） |
| `server/query/README.md` | ✏️ | ✅ 已同步实现（删去"不含 LLM 实现（预留）"；补 `prompts.py`、`llm_fallback.py` 与兜底口径） |
| `server/runtime.py` | ✏️ | 构造并注入 F02 的 `llm_client`，按 `settings.enable_llm_entity_fallback` 开关；`meta` 增 `llm_entity_fallback` 字段 |
| `server/generate/cache.py` | ✏️ | `cache_key` 纳入 mode（与 T3 同一处改动，见下） |
| `server/generate/llm_client.py` | ✏️ | `create()` 传 `max_tokens=settings.llm_max_tokens`（推理模型过小会导致**正文为空**，§4.11）；流式同时识别 **`delta.reasoning`（中转）与 `delta.reasoning_content`（官方）**并交由 `thinking` 回调、只把 `delta.content` 当答案；开 `stream_options={"include_usage": True}` 记录 token/reasoning 占比；密钥读取用别名链 `LLM_API_KEY → DEEPSEEK_API_KEY → RAG-command → RAG-deepseek-v4` |
| `config/settings.py`、`config/defaults.py`、`.env.example` | ✏️ | 新增 `LLM_MAX_TOKENS`（默认 **3072**；实测 1024/2048 会被推理 token 打满致正文为空）；密钥别名链与"不写进 `.env`"的说明 |
| `contracts/sse.py`、`server/sse.py`、`docs/data-contract.md` | ✏️ | 接上 `thinking` 事件（推理模型的 reasoning 阶段，§4.11）；契约里把"当前不发射"的说明改为实际行为 |
| `tests/test_f02_llm_fallback.py` | 🆕 | ✅ 11 条用例：词典未命中走 LLM / LLM 失败或超时降级回词典 / 开关关闭时不调用 / 解析容错与缓存卫生 |

### T3 向量检索

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `data/index/embeddings.py` | 🆕 | 百炼 `text-embedding-v4` 客户端工厂：`build_embed_fn(settings)`；`dimensions=EMBEDDING_DIM`（1024）、`encoding_format="float"`；**batch ≤ 10**（百炼硬上限）、退避重试、断点续跑、返回维度与 `EMBEDDING_DIM` 不一致即报错（§四.3） |
| `data/index/chroma_store.py` | 🆕 | Chroma 持久化封装：`PersistentClient(path=data/index/<v>/vectors/chroma)`；`create_collection(name, metadata={"hnsw:space":"cosine"}, embedding_function=None)`（显式禁用默认 EF，避免拉 onnxruntime）；分批 `add(ids, embeddings, documents, metadatas)`；`query(query_embeddings, n_results, where)` 并把 **distance 转相似度**；`count()` 一致性校验（§四.3） |
| `data/index/build.py` | ✏️ | ✅ 已接线：新增 `_build_vectors()`——有密钥且开关为真 → 调 `vector_pipeline.build_vector_store`（嵌入 + Chroma）；否则写空占位（`mode=placeholder`，在线侧自动降级 keyword）。manifest 的 `mode/dim/model/count/space` 由 vector_pipeline 写入 |
| `data/index/vectors.py` | ➖ | **设计变更（2026-09-14）**：审计副本写入与断点续跑已由 `vector_pipeline.py` 承担，本文件保持占位时代的空实现（全仓无调用点），不再按原计划改造 |
| `scripts/build_index.py` | ✏️ | 增 `--vectors-only`（复用 `chunks.jsonl` 只重建 `vectors/`，因索引目录已存在时 `build.py:40-41` 会拒绝覆盖）、`--sample N`（小样验证维度与质量）、`--rebuild-chroma`（✅ 已实现：从 `ids.json` + `embeddings.npy` 重建 Chroma，不调云端），三者与前两项互斥 |
| `requirements.txt` | ✏️ | ✅ 已补 `chromadb>=1.3`（**项目环境已装 1.3.4**，与 fastapi/pydantic/numpy 共存已实测，见 §4.3） |
| `server/text/searcher.py` | ✏️ | 新增 Chroma 客户端加载与 `search_vector()`：`query_embeddings` + `where`（dynasty/event_type/chunk_type）+ `n_results`；`distance = 1 − 余弦` 转相似度后 min-max；`search_hybrid()` 按 §四.2 融合；`vector_available` 判定改为"Chroma collection 存在且 `count()` 与 `ids.json` 一致" |
| `server/text/__init__.py` | ✏️ | `:50-56` 三分支实现：keyword / vector / hybrid，均产出统一 `TextResult` |
| `server/text/scoring.py` | ✏️ | 若 hybrid 权重要可配，在此集中读取；`resolve_mode` 逻辑保持 |
| `config/settings.py`、`config/defaults.py`、`.env.example` | ✏️ | 新增 `TEXT_MODE`（默认 `keyword`）、`TEXT_HYBRID_STRATEGY`（`weighted`/`rrf`/`fallback`，默认 `weighted`）、`TEXT_HYBRID_KEYWORD_WEIGHT`（默认 `0.5`）、`EMBEDDING_BATCH_SIZE`（默认 10）、`EMBEDDING_TIMEOUT_SECONDS`、`CHROMA_COLLECTION` |
| `server/sse.py` | ✏️ | `:160-163` 按 `settings.text_mode` 传 mode；`server/runtime.py:108` 的 `meta["text_mode"]` 反映真实配置 |
| `server/generate/cache.py` | ✏️ | `cache_key(...)` 增 mode 段（C3）。生产接线形态若为"部署级全局开关"，仍需进键——同名问题在切换模式后不得复用旧模式的缓存 |
| `docs/data-contract.md` | ✏️ | `text_results.data` 补 `mode`，写明 `vector_available` 探测与降级口径；若请求侧支持按请求切模式，须同步 `contracts/request.py` 与 `QueryRequest` |
| `tests/test_hybrid_scoring.py`、`tests/test_vector_degradation.py`、`tests/test_vector_wiring.py` | 🆕 | ✅ 实际落点（原计划名 `test_text_vector.py`）：余弦距离→相似度换算、min-max 边界（max==min→0.5）、hybrid 三档融合、Chroma 缺失/损坏/条数不一致时 `vector_available=false` 自动降级、构建侧接线三分支、审计副本重建与不一致报错 |
| `scripts/check_vector_consistency.py` | 🆕 | ✅ 校验 `chroma.count() == len(ids) == npy.shape[0]`；抽样 N 条问句比较 Chroma top-k 与暴力余弦 top-k 的重合率（HNSW 近似召回的可观测保障；重建后实测 0.932） |

### T4 部署

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `server/api.py` | ✏️ | 路由注册**之后**挂载 `StaticFiles(directory=settings.frontend_dist, html=True)`；dist 不存在则跳过并打日志（不阻塞 API） |
| `config/settings.py`、`.env.example` | ✏️ | 新增 `FRONTEND_DIST`（默认 `RAG/frontend/dist`） |
| `scripts/run_server.py` | ✏️ | 启动自检打印：快照/索引版本、`text_mode`、`vector_available`、`llm_available`、是否托管前端；保持 `workers=1` |
| `scripts/smoke_deploy.py` | 🆕 | 健康检查 → 示例题逐条（含缓存命中复跑）→ 可选限流检查；输出 JSON 报告到 `logs/`（§六.1） |
| `docs/deploy.md` | 🆕 | 端口、环境变量、数据制品、启动与自检、常见故障（§七） |
| `frontend/vite.config.ts` | ✏️（条件） | 仅"子路径托管"时需要改 `base`；根路径同源托管**不用改**（前端全用相对 `/api` 路径） |

### T5 质量优化

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `server/sse.py` + `evaluation/chain.py` | ✏️✏️ | 拒答规则扩展**必须双处同步**（`:189-216` 与 `:230-237`）；规则名进 trace，便于评测归因 |
| `server/generate/refusal.py` | ✏️ | 新增"属性/信息不存在类"判定（X01–X03 场景），保持 `has_any_evidence` 语义不变 |
| `server/text/searcher.py` | ✏️ | AND 失效兜底：`and` 命中 0 时的放宽策略（取词截断 / 降级 or / 交向量通道），生产默认走 `and_or` 不变 |
| `data/index/chunking.py`、`data/index/fts.py` | ✏️ | raw/evidence 片段补 `event_type` 元数据（按 `event_id` 关联事件卡片），使 `_pass_meta`（`searcher.py:161-172`）不再整批剔除原文；须重建索引 |
| `tests/test_refusal.py`、`tests/test_and_fallback.py` | 🆕 | 规则与兜底的守护用例 |

### T6/T7 评测对照与收口

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `evaluation/chain.py` | ✏️ | 新增 `CONFIG_VECTOR`、`CONFIG_HYBRID` 预设并注册进 `CONFIG_PRESETS`；trace 记录 `text_mode` |
| `evaluation/cli.py` | ✏️ | `--configs` 支持新预设；`--llm` 已有；报告增加"文本模式"列 |
| `docs/data-contract.md`、`docs/README.md`、`docs/features/*`、`docs/RAG_v1/README.md` | ✏️ | T7 收口同步（§十） |

### T8 分块参数对比实验（用户已确认执行）

| 文件 | 性质 | 要点 |
| --- | --- | --- |
| `scripts/compare_chunking.py` | 🆕 | 按给定 `max_chars/overlap` 组合构建**索引变体**（`data/index/<v>_c500o100` 等），逐组跑离线评测并汇总指标 |
| `data/index/build.py`、`server/runtime.py`、`evaluation/cli.py` | ✏️ | **前置：索引变体机制**（`--index-suffix` / `--index-variant`，`resolve_version` 以 `manifest.source_snapshot` 定位快照）——见 §4.10；不做这一步实验无法执行 |
| `config/defaults.py`、`.env.example` | ✏️ | 切分参数保持可配（已有 `CHUNK_MAX_CHARS` / `CHUNK_OVERLAP_CHARS`），补注释说明实验候选值 |
| `data/eval/<v>/chunk_exp_report.md` | ⚙️ | 实验报告：三组参数的片段数/平均长度/片段总数、文本 top-k 召回、融合文本携带、回答覆盖、检索耗时，以及"切换或保持"的结论 |
| `data/index/<v>_c*` | ⚙️ | 实验用索引变体（可清理；**800/80 的既有索引必须保留**，它是 v4 基线的参照） |

实验设计见 §四.10。

---

## 四、关键设计决策（先定口径，再写码）

> 标注 ⏳ 的项需要规划说明 §四 的待确认拍板后才能定稿；其余给出推荐值，除非评审反对即按此实现。

### 4.1 文本模式开关形态与缓存键（C2 / C3）⏳

两种形态，二者都必须让 mode 进缓存键：

| 形态 | 优点 | 代价 |
| --- | --- | --- |
| **A. 部署级全局开关（推荐）** | 改动最小；`TEXT_MODE` 一个配置；请求契约不变 | 演示中不能按问题切模式；对照评测靠评测链路而非生产接口 |
| B. 按请求可切 | 前端可加"检索模式"选择器，便于现场展示三种模式 | `QueryRequest` 增字段（契约变更）+ 前端控件 + 缓存键必须含 mode + 潜在滥用面 |

推荐 A：v5 的目标是"演示稳定"，不是"暴露检索调参"；对照实验走评测链路（`EvalConfig.mode`）。
无论选哪种，`cache_key` 都要加 mode 段——A 形态下也必须在文档写明"改了 `TEXT_MODE` 需重启并清理缓存"，
或直接进键（更省心，推荐后者）。

### 4.2 hybrid 归一化与融合口径（审核 R1）⏳ 口径待评测决定

**先把"融合口径"讲清楚**（这是审核报告 R1 悬着的那个问题）：关键词通道（BM25）与向量通道
（余弦）各有各的结果列表和分数量纲。用户问一个问题时，两条通道各给一批片段，"融合口径"
就是回答**三件事**：

1. 两边的分数怎么放在同一个尺度上比——BM25 是"越小越相关"的负值，余弦是 [−1,1] 的相似度，
   直接相加没有意义；
2. 两边都命中的片段怎么加权、只被一边命中的片段怎么算；
3. 最终按什么顺序送进 F05 融合（F05 会再按问题类型给图谱/文本分配名额）。

它有三种常见做法，各有取舍：

| 策略 | 做法 | 优点 | 代价 |
| --- | --- | --- | --- |
| **线性加权**（默认实现） | 两边各自 min-max 归一化到 0–1，`score = w_kw·s_kw + w_vec·s_vec`（默认各 0.5），融合后再归一化 | 直观、可分通道调权重、与现有 score 契约完全兼容 | 依赖归一化是否稳定；权重需要评测定 |
| **RRF**（倒数排名融合） | 不看分数，只看名次：`score = Σ 1/(k + rank)`（k 常取 60） | 天然回避量纲问题，无需归一化，抗分数异常 | 丢弃分数信息（"很像"与"勉强命中"同等对待）；与 F05 的按分重排口径需额外衔接说明 |
| **仅兜底** | 关键词有结果就用关键词，召回为空时才回落到向量 | 改动最小、风险最低 | 拿不到"向量提升召回"的结论，F04 功能文档要求的 hybrid 模式等于没实现 |

**本阶段的处置（2026-09-13 已定档：`rrf`）**

同一题库、同一流程（`main` 套件 28 题、离线回答器）实测三档：

| 策略 | 文本 top-k 召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- |
| `weighted`（0.5/0.5） | 95.2% | 95.2% | 77.1% | 85.1% | 28/28 |
| **`rrf`（定档）** | **97.0%** | **97.0%** | 77.1% | 85.1% | 28/28 |
| `fallback`（仅兜底） | 94.6% | 94.6% | 66.7% | 71.7% | 28/28 |

结论：**默认 `rrf`**（召回最高，回答覆盖与 weighted 并列；且不依赖两通道分数量纲的可比性）。
`fallback` 的三项指标与关键词基线**完全相同**（94.6%/66.7%/71.7%）——印证了"向量只做兜底等于没做
hybrid"。三档共用同一套归一化与返回契约，切换只是 `TEXT_HYBRID_STRATEGY` 一个环境变量。
`weighted` 仍保留（`TEXT_HYBRID_KEYWORD_WEIGHT` 可调权重），供后续按题型细分时使用。

**可分性前提**（保证 F05 与评分链路零改动）：F04 输出 `score` 契约 ∈ [0,1]
（`docs/data-contract.md` L200-202，max==min 记 0.5），F05 用 `tw * (0.5 + score*0.5)` 在同通道内
排序、再按问题类型分配额（`server/fusion/fusion.py:98-125`）。只要融合分压回 [0,1]，下游不用改。

**两条通道的分数处理**：

```
关键词通道（已实现）：bm25 取反 → min-max → s_kw ∈ [0,1]
向量通道（T3 新建，走 Chroma）：
    建库期：Chroma 存原始向量，collection 的 hnsw:space = cosine
    查询期：res = collection.query(query_embeddings=[q], n_results=k, where=过滤条件)
            Chroma 返回的是 distance = 1 − 余弦相似度（越小越相似）
            sim = 1 − distance                      ← 必须换算，这是最容易写错的一步
            s_vec = minmax(sim)                     # 与关键词同口径；max==min → 0.5
```

**必须一并处理的三点**：

1. **拒答硬规则按模式分档**（§二-14）：第二条规则（"实体为空 + 文本证据与问题无共享词"→ 拒答，
   `server/sse.py:204-216`）在 `vector/hybrid` 下会误伤语义相关但无共同词的证据。推荐：
   `keyword` 保持现状；`vector/hybrid` 改为"证据最高分 < 阈值（如 0.25）**且**无共享词"才拒答，
   阈值进配置并写进风险表（`evaluation/chain.py` 的副本同步）。
2. **`dynasty_bias` 在向量/hybrid 下的语义**：向量无"命中朝代"概念，建议仅按可得的元数据
   对**候选顺序**做稳定前置（与关键词一致），或明确在向量模式下忽略并写入文档。二者都需在
   `meta`/trace 里可见，不能沉默。
3. **先写评测再调权重**：`w_kw` 的取值不许拍脑袋——用 §六.2 的四配置对照，以"文本 top-k 召回
   + 回答覆盖 + 14 条重点题"三项同时不退化为准。

### 4.3 向量构建链（百炼 text-embedding-v4 + Chroma）

**模型与接口（2026-09-13 定）**：

```python
# .env
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1   # 阿里云百炼，北京地域
EMBEDDING_API_KEY=sk-...                                               # 控制台申请
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024                    # v4 默认值，可选 2048/1536/1024/768/512/256/128/64

client.embeddings.create(model="text-embedding-v4", input=batch,
                         dimensions=EMBEDDING_DIM, encoding_format="float")
# 约束：单请求 input 最多 10 条文本；单条 ≤ 8,192 token
```

维度先用 `--sample 8` 跑一次**断言实测维度 == `EMBEDDING_DIM`**（不一致就报错退出，
不允许"配 1024 实际存 2048"这种静默错位）；若后续评测显示召回不足，改 `EMBEDDING_DIM`
重建即可（索引目录随快照版本走，重建是一次命令）。

**密钥来源（2026-09-13 确认）**：百炼密钥已在**系统环境变量 `DASHSCOPE_API_KEY`** 中，
本机实测可读（当前 shell 可直接取到）。因此：

- `config/settings.py` 的读取优先级为 **`EMBEDDING_API_KEY` → `DASHSCOPE_API_KEY`**，
  **不要求把密钥写进 `.env`**；`.env.example` 只写变量名与说明，不写值（`.env` 本就不入库）；
- `config/settings.py:15` 的 `load_dotenv(..., override=False)` 意味着系统环境变量优先于 `.env`，
  与该设计天然一致；
- 构建日志与报错信息**不得回显密钥**（只允许打印"已配置/未配置"与模型名）。

**已实测（2026-09-13，直接调用百炼验证，未经过代码）**：

| 项 | 实测结果 |
| --- | --- |
| 单条文本嵌入 | 成功，耗时 **1.07 s**，返回维度 **1024**（与 `EMBEDDING_DIM=1024` 一致），`usage.prompt_tokens=8`（"赤壁之战的主帅是谁？"10 字 → 8 token） |
| 10 条批量 | 成功（耗时 1.50 s），**batch=10 上限确认可用** |
| 全量规模推算 | 9,544 片段 ÷ 10 = **约 955 批**；按实测单批耗时串行约 **25 分钟**，3–4 并发约 **6–8 分钟**（并发须配退避重试防 429） |
| 成本推算 | 片段文本合计约 314 万字符 ≈ 250 万 token 量级 → **约 1–2 元**（按约 0.0005 元/千 token） |

**查询期成本与延迟（2026-09-13 实测，回答"向量检索会不会很贵"）**：

- 每次提问需要对**问题本身**调一次百炼做向量化：实测 8–13 token、耗时 0.34–1.32 s（中位 0.589 s，含网络往返）。
  按约 0.0005 元/千 token 估算，单次约 **0.000005 元** → 1,000 次约 0.005 元、10,000 次约 0.05 元。
- 对比生成侧同一次提问的 token 用量（实测 prompt 1,398–1,865 + completion 361–2,047）：
  **生成侧 token 量是向量化侧的 176–391 倍**，成本瓶颈在 LLM，不在 embedding。
- **重复提问零调用**：同题在缓存 TTL（默认 1 h）内命中回答缓存时，流程在检索之前就返回
  （`server/sse.py` 缓存检查在 graph/text 检索之前），**既不再调 LLM，也不调 embedding**。
  演示示例题是固定题集 → 第一轮点完，后续点击近乎零成本、0.01 s 返回。
- 因此"演示要控成本"的着力点是：控 LLM 的 token（裁剪证据、控篇幅、优选短答题），
  而不是省 embedding。

**Chroma 环境与行为已实测（2026-09-13，用项目环境 `E:/anaconda/envs/AI_Agent/python.exe`）**：

| 项 | 实测结果 |
| --- | --- |
| 依赖共存 | **`chromadb 1.3.4` 已安装**，与 `fastapi 0.121.0` / `pydantic 2.11.10` / `numpy 2.3.4` / `openai 2.7.1` / `onnxruntime 1.23.2` 同环境共存，导入无冲突 → **M0 的依赖预检项已完成**，`requirements.txt` 记 `chromadb>=1.3` 即可 |
| 建集合写法 | `create_collection(name, metadata={"hnsw:space": "cosine"}, embedding_function=None)` 在 1.3.4 上**有效且无告警**（1.x 也支持新的 `configuration=` 写法，两种都可用） |
| 距离语义 | 实测：同向量 → `distance=0.0`；近似向量 → `0.0061`；正交向量 → `1.0`，**确认为 `distance = 1 − 余弦相似度`**，换算方向正确 |
| 过滤写法 | `where={"dynasty": "东汉"}`（等值）与 `where={"chunk_type": {"$in": ["raw"]}}` 均按预期返回 |
| 缺失元数据 | 按 `event_type` 过滤时，**没有该 key 的片段被排除**（实测返回空）→ 与现有 `_pass_meta` 的"无该元数据视为不匹配"语义一致，无需额外适配 |
| `None` 元数据 | 写入被拒绝：`TypeError`（期望 `Bool / Int / Float / Str / SparseVector`）→ 缺失字段**必须省略 key** |
| list 元数据 | 写入被拒绝：`ValueError ... which is a list` → 列表字段（如 `related_entities`）必须先连接成字符串 |
| 持久化产物 | 目录内含 `chroma.sqlite3` + 一个 UUID 子目录（HNSW 索引）→ 部署打包与版本校验需整体携带该目录 |

**构建流程**：

```
scripts/build_index.py
  --vectors-only：复用既有 chunks.jsonl，仅重算 vectors/（推荐，避免重建 FTS5）
  --sample N：只嵌入前 N 条，先验维度/质量/成本（对应规划说明 §六 风险对策"先小样验证"）
  --no-embeddings：跳过云端调用（保持现状语义：只建 FTS5）
  --rebuild-chroma：删除并重建 collection（幂等重跑用）
  embed_fn = build_embed_fn(settings)        # EMBEDDING_BASE_URL/API_KEY/MODEL/DIM 齐全才返回
  ↓
  分批（batch ≤ 10）调用 embed → 每批落 data/index/<v>/vectors/_parts/<i>.json（断点续跑依据）
  ↓
  ① 写 ids.json（chunk_id 顺序，行对齐的唯一依据）
  ② 写 embeddings.npy（float32，审计副本，不参与检索）
  ③ 写 Chroma：PersistentClient(path=<idx>/vectors/chroma)
              create_collection(CHROMA_COLLECTION, metadata={"hnsw:space":"cosine"},
                                embedding_function=None)   # 显式禁默认 EF，避免拉 onnxruntime
              collection.add(ids=chunk_ids, embeddings=batch_vecs,
                             documents=texts, metadatas=[...])   # 分批，每批 ≤ 数百条
  ↓
  manifest.vectors = {"mode":"chroma","dim":D,"model":"text-embedding-v4",
                      "count":N,"space":"cosine","collection":"...","normalized":false}
```

**`ids.json` / `embeddings.npy` 的定位（决策）**：检索**只走 Chroma 一条路径**，`npy` 不实现
第二条检索路径——避免双实现双维护。保留它们的理由：① 审计与可移植（换机器或换 Chroma
版本时不必重新付费 embedding）；② 一致性校验基准（`chroma.count() == len(ids) == npy.shape[0]`，
不一致 → 记 warning 并降级 keyword）；③ 将来若切回暴力余弦/faiss，无需重新嵌入。
磁盘代价：1024 维 × 9,544 ≈ 39 MB（npy）+ Chroma 自身副本，总计百 MB 量级，可接受。

**元数据设计（Chroma 的约束必须遵守）**：

- Chroma 的 `metadatas` **只接受标量**（str / int / float / bool），不接受 list 与 None：
  `related_entities` 这类列表要 `"|".join(...)` 成字符串（仅供展示/调试，不参与过滤）；
- 缺失的字段**不要写 None**，直接省略 key——Chroma 不接受 None 值；这也与现有
  `_pass_meta` 的语义天然一致（无该元数据的片段不匹配 `event_type` 筛选）。
- 过滤走 `where={"dynasty": {"$in": [...]}, ...}`（Chroma 是**先按元数据筛、再在子集上做 ANN**，
  与现有 `_pass_meta` 的"硬过滤"语义一致）。`event_type` 目前只有 `event_card` 片段有值，
  这正是规划说明 §2.5-3 要修的问题。

**踩坑提醒**：

- **batch 上限是 10**（百炼硬限制，超出直接报 `batch size is invalid`）：9,544 片段 ≈ 955 次请求，
  必须做退避重试 + 断点续跑，否则一次网络抖动就要从头再来。
- **Chroma 的 distance 不是相似度**：`cosine` 空间下返回 `distance = 1 − 余弦`，越小越相似；
  换算成相似度后再 min-max，且注意"距离相同（全 1.0）时"也要落到 0.5 的既有约定。
- **`hnsw:space` 在建 collection 时锁定**，事后改无效，只能重建 collection（所以 `--rebuild-chroma`）。
- **必须传 `embedding_function=None`**：否则 Chroma 会加载默认 embedding 模型（多一份依赖与内存，
  且与我们自己的百炼向量不是同一空间，属于隐蔽的正确性事故）。
- `data/index/build.py:40-41` 索引目录已存在即 `FileExistsError` → 全量重建需**新快照版本目录**；
  同一版本内重建向量走 `--vectors-only`。
- `manifest.vectors` 是启动可见性的唯一来源（`server/runtime.py` 与 `/api/health` 的
  `vector_available` 由此判定），构建结束必须复核它。
- **依赖共存预检**：`chromadb` 依赖树较大，先在一个临时环境里试装，确认与 `fastapi`/`pydantic`/
  `numpy` 现有版本共存并锁定版本；若冲突不可调和，退路是"独立进程/独立环境构建向量库"
  （构建与检索分离，检索侧只读 collection 目录）。
- 密钥缺失时**不得**静默产出"看起来成功"的索引：日志与 manifest 必须明确 `placeholder`。

### 4.4 真实流式接线（`run_query` 是异步生成器）

现状：`server/sse.py:223` `await gen.generate(...)` 拿到全文后按句切（`:246-249`）——用户看到的
"流式"与模型无关。目标：`on_delta` 的每个增量即时发出。

`AnswerGenerator.generate` 是 **coroutine**（返回三元组），不是 async generator，因此需要桥接：

```python
queue: asyncio.Queue[str | None] = asyncio.Queue()

def on_delta(delta: str) -> None:
    queue.put_nowait(delta)

task = asyncio.create_task(gen.generate(..., on_delta=on_delta))

while True:
    if task.done() and queue.empty():
        break
    try:
        delta = await asyncio.wait_for(queue.get(), timeout=0.05)
    except asyncio.TimeoutError:
        continue
    yield sse_format(_event(SSEEventType.ANSWER, sid, stage=..., data={"delta": delta}))

finish_reason, model_used, full_answer = await task   # 异常在此抛出，走既有 error 分支
```

要点：

- **离线路径保持按句切分**：`heuristic-offline` 与拒答文案是一次性 `on_delta` 全量回调，
  若原样转发会变成"一大坨瞬时出现"。按"是否为真实 LLM 增量"分流：LLM 增量直接转发；
  非 LLM 文本仍走 `_chunk_answer_stream` 做打字机效果。
- **事件顺序契约不变**：`answer` 增量之后仍是 `citations` → `panel` → `done`；`status(generating)`
  必须在首个 `answer` 之前（现状如此）。
- **`degraded` 的处理**：主模型失败后降级路径会一次性回调全文（`server/generate/__init__.py:72-75`），
  用户可能"什么都没看到就整段出现"，可接受；重点是 `finish_reason=degraded` 可见且不入缓存。
- **缓存写入时机**：现状在生成之后、发射之前构造 payload（`server/sse.py:230-242`）——流式改造后
  保持"先算缓存 payload、再发增量"，保证缓存内容与流式内容一致。

### 4.5 F02 LLM 兜底（C1）——✅ 已实现（2026-09-13）

实现落点：`server/query/prompts.py`（提示词 + JSON 容错解析）、
`server/query/llm_fallback.py`（`EntityFallbackClient`：同步客户端、独立超时）、
`server/query/understand.py`（兜底分支 + 逐问缓存 + 两层异常保护）、
`runtime.py`（按开关注入）、`contracts/request.py`（`F02Output.llm_entity_used`）、
`sse.py` 与 `evaluation/chain.py`（可观测性）。

| 项 | 口径 |
| --- | --- |
| 触发条件 | 词典匹配结果为空（`raw_hits` 空）；词典命中时**一次都不调用** |
| 调用内容 | 单次结构化请求：问句 → `{"entities":[{"name","type"}]}`；类型限定事件/人物/组织/地点；解析失败即视为未命中 |
| 超时预算 | 独立 8 s（`LLM_ENTITY_TIMEOUT_SECONDS`），不复用 F06 的 60 s；实测抽取通常 3–4 s |
| 开关 | `ENABLE_LLM_ENTITY_FALLBACK` 默认 **false**（演示默认关，避免每问两次串行调用吃首 Token） |
| 失败降级 | 异常/超时/解析失败 → 返回空实体，**不抛错**（两层保护：客户端 + 理解层各兜一次） |
| 可观测 | `entities` 事件与评测 trace 增 `llm_entity_used`；兜底实体 `confidence="low"`、`entity_id=None` |
| 缓存 | 按问句进程内缓存（上限 256 条）；**失败结果不缓存**（避免一次抖动被长期记住） |
| 用例 | `tests/test_f02_llm_fallback.py` 11 条：触发/不触发/关闭/失败降级/缓存/解析容错（markdown 包裹、非法 JSON、类型过滤、去重） |
| 实测坑 | 兜底调用 `max_tokens` 给小（256）会被 reasoning 吃满 → 正文为空、解析出空列表；已提到 1024 |

> 旧的"设计口径"表已由本表取代；`understand.py` 的模块 docstring 也已从"预留，尚未实现"改为实现说明。

### 4.6 拒答规则扩展（X01–X03）

- X01–X03 属"属性/信息不存在类"（如问某实体没有的属性）：现有两条硬规则（无证据；无共享词）
  都不触发，于是照常作答。
- 新增判定建议：**实体命中 + 证据存在 + 证据中不存在问题所问的谓词/属性** → 拒答（`unknown_answer`）。
  实现上以"问题谓词词表（如 出生地/身高/子女/兵力 等）是否在证据文本中出现"做保守判定，
  命中不确定时**不做拒答**（宁可不拒，避免把可答题拒掉）。
- 双处同步（`server/sse.py` 与 `evaluation/chain.py`），并在评测 `refusal` 套件上验证：
  X01–X03 转 `unknown_answer`，且 main 套件不因新规则出现新的拒答。

### 4.7 F08 示例题来源、结构与 `demo_mode` 语义 ✅

**数据流**：题库 + 评分（只读） → `scripts/gen_demo_examples.py` → `demo_examples.json`（入 Git）
→ `GET /api/demo/examples` → 前端分组渲染。**前端不再内置任何问题**（当前硬编码里 2 条未审核，
这是二次踩坑点）。

```json
{
  "version": "20260904_v2",
  "generated_at": "2026-09-13T..",
  "filter": { "reviewed": true, "exclude_answer_correctness": ["incorrect"] },
  "counts": { "total": 39, "kept": 20, "dropped": 19 },
  "source_run": "run_20260913_postaudit",
  "examples": [
    { "id": "M01", "question": "…", "category": "关系型", "capability": "graph",
      "expect": { "graph_min": 1, "text_min": 0 },
      "measured": { "first_thinking_ms": 2160, "first_answer_ms": 6010, "truncated": true } }
  ]
}
```

- `capability` 标注：脚本按最近 run 的 trace 自动推导（有图谱证据 → graph；有文本证据 → text；
  两者兼有 → both），**再由人工复核一遍**（示例题是门面，值得人过一遍）。
- **新增选型维度（2026-09-13 实测驱动，必做）**：还要按**实测响应表现**筛题——首 thinking、首正文时延、
  是否触顶截断（`finish_reason=length`）。实测同一模型下"赤壁之战的主帅是谁？"首正文 4.76 s 不截断，
  而"介绍一下长平之战。"首正文 13.11 s 且被 2048 上限截断（§4.11）→ 演示清单必须逐题实测后再定，
  清单里记录实测值，冒烟脚本顺带产出这些数据。
- 数量与分组：建议 8–12 条，覆盖 4 类 `category`（规划说明 §四-4 待拍板）。
- 接口失败降级：文件缺失返回 503，前端仅显示引导文案——**不回退硬编码**。
- `demo_mode` 语义：✅ **采用②（2026-09-14 定）**——删除配置项，示例区恒显示。
  理由：F08 的形态就是"打开即可提问"，限流/缓存/降级各有独立配置，不引入未接线的开关。
  实现：`config/defaults.py`、`config/settings.py`、`.env.example` 三处定义已删。

### 4.8 部署形态（同源托管）

- 前端全用相对路径（`frontend/src/api/http.ts` 的 `/api/health`、`sse.ts` 的 `/api/query`），
  根路径同源托管**不需要**改 `vite.config.ts`。
- 挂载必须在所有 `/api` 路由注册之后：`app.mount("/", StaticFiles(directory=dist, html=True))`。
- 单进程：内存图谱、回答缓存、限流计数都在进程内，`workers` 必须 1（`scripts/run_server.py` 已固定；
  多 worker 会出现"缓存时有时无""图谱加载 N 份"）。
- 数据制品：`data/snapshot/<v>` + `data/index/<v>`（`.gitignore` 已忽略，需随部署包携带，
  约 80MB 量级，以实际打包为准）；启动时 `server/runtime.py:46-71` 校验快照/索引版本一致，
  校验失败**必须启动失败**（现状如此，不要改成静默降级）。

### 4.9 缓存与降级的卫生（新发现，建议纳入 T2）

| 项 | 现状 | v5 口径 |
| --- | --- | --- |
| 写缓存条件 | 无条件（`server/sse.py:241`） | 仅 `finish_reason ∈ {normal, refused}`；`degraded`/`cancelled` 不写 |
| 缓存范围 | 进程内 dict，重启即失（`cache.py:41-55`） | 保持进程内（演示单实例足够）；部署文档写明"重启后首次提问必然全量检索" |
| 缓存键 | 5 段，无 mode | 增 mode（§4.1） |
| 评测链路 | 不读不写缓存（`evaluation/chain.py` 已如此） | 不变 |

### 4.10 分块参数对比实验（T8，用户已确认执行）

**背景**：现有 `CHUNK_MAX_CHARS=800` / `CHUNK_OVERLAP_CHARS=80` 是 RAGv1 的初版取值，
从未做过对照实验；`docs/features/11-text-indexing.md` 的验收只要求"记录切分参数"，不要求调优。

**前置机制（实现 T8 前必须先做，否则实验跑不起来）**：现有代码把**索引目录名与快照目录名绑死**——
`data/index/build.py:21-31 _resolve_snapshot` 要求 `data/snapshot/<version>` 存在，
`server/runtime.py:46-71 resolve_version` 要求索引目录名等于版本号且 `manifest.source_snapshot == version`。
所以"同一快照、三套分块参数"无法直接表达。两个方案：

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A. 索引变体机制（推荐）** | `build_index.py` 增 `--index-suffix`：索引目录为 `data/index/<v><suffix>/`（如 `20260904_v2_c500o100`），manifest 记 `source_snapshot=<v>` 与 `variant=<suffix>`；`resolve_version` 在显式指定变体时**以 `manifest.source_snapshot` 定位快照目录**（图谱/融合仍读真实快照）；评测 CLI 增 `--index-variant` | 改动集中在 `data/index/build.py` + `server/runtime.py` + `evaluation/cli.py`，约几十行；索引目录天然可清理，快照不受影响 |
| B. 复制出假的快照版本目录 | 复制 `data/snapshot/20260904_v2` 为 `data/snapshot/20260904_v2_c500o100` 再按版本构建 | **不要这么做**：`lib/versions.list_versions` 会把它当成最新版本，服务启动默认取最新 → 可能选到实验快照，属真实事故风险；且快照目录体积不小，复制三次纯浪费 |

**实现顺序**：先做方案 A 的机制 → 再跑三组实验 → 结论写入报告后，把机制保留（后续换 embedding 模型、
调分块、做参数对比都要用），并在 `docs/deploy.md` 注明"生产只部署被选中的那一份索引目录"。

| 项 | 设计 |
| --- | --- |
| 变量 | `CHUNK_MAX_CHARS/OVERLAP`：**500/100**、**800/80（现状，对照组）**、**1200/120** |
| 隔离条件 | 同一快照 `20260904_v2`；索引目录独立（`<v>_c500o100` 等，不覆盖既有索引）；离线回答器（不接 LLM，保证确定性）；两套检索配置各跑一遍：`dual`、`text-only`（关键词通道，先隔离分块这一个变量） |
| 必记指标 | ① 片段统计：总数、平均/中位长度、<10 字的过短片段数、以句末标点结尾的比例；② 效果：文本 top-k 召回、融合文本携带、回答覆盖、ok/n；③ 成本：片段总数变化 → **向量化请求数（batch=10）与 Chroma 体积的同比放大**，以及检索耗时 |
| 判定 | 某组在②上不退且①的片段数不显著膨胀 → 采纳并同步更新 `config/defaults.py`；否则保持 800/80 并把结论写进报告（"保持"也是一个有效结论） |
| 顺序 | **必须在 M1 向量全量构建之前完成**——万一要换参数，避免白建一次向量库、白付一次费用 |
| 回溯要求 | 800/80 的索引目录与基线 `run_20260913_postaudit` 必须保留：换分块会改变全部 `chunk_id`，只有 800/80 下的结果与 v4 基线可比 |
| 已知观察 | 过短片段 105 条（全部为 `evidence`，<10 字）——分块实验顺带统计其比例变化；是否在向量库中排除它们，交给 `vector` 配置的对照结果决定，不直接丢弃（证据片段是精确溯源的载体） |
| 产物 | `data/eval/<v>/chunk_exp_report.md`（含三组数据与"切换/保持"结论） |
| 命令 | `python scripts/compare_chunking.py --sizes 500/100,800/80,1200/120 --configs dual,text-only` |

**实施情况与结论（2026-09-13 已执行）**

前置机制已实现：`scripts/build_index.py --index-suffix/--chunk-max-chars/--chunk-overlap-chars`、
`data/index/build.py`（manifest 记 `index_version`/`source_snapshot`/`variant`）、
`server/runtime.py::_resolve_index`（变体回指真实快照，普通索引仍严格校验版本一致）、
`evaluation/cli.py`（`--version` 可传索引目录名；`meta.json` 增 `index_version`/`chunk_params`）、
新增 `scripts/compare_chunking.py`。

实测（套件 `main`、配置 `dual,text-only`、离线回答器）：

| 参数 | 片段总数 | 平均长度 | 句末标点结尾 | dual 文本召回 / 融合携带 / 引用携带 / 回答覆盖 | text-only 同组 | 平均检索耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 500/100 | 11,641（+22%） | 279.5 | 84.7% | 93.2% / 84.2% / 68.5% / 66.4% | 93.2% / 93.2% / 75.0% / 73.5% | 4 ms |
| **800/80（基线）** | **9,544** | 329.4 | 86.3% | **94.6% / 86.3% / 63.7% / 67.6%** | **94.6% / 94.6% / 66.7% / 71.7%** | 3 ms |
| 1200/120 | 8,740（−8.4%） | 359.4 | 87.5% | 92.9% / 86.3% / 62.8% / 67.6% | 92.9% / 92.9% / 67.6% / 73.5% | 3–4 ms |

**结论：保持 800/80，不切换。** 文本 top-k 召回在基线最好（94.6%）；回答覆盖的差异方向相反、
量级 1.2–2.1 个百分点，无一致优势；500/100 成本 +22% 而收益只在"引用携带"一项；
1200/120 省 8.4% 成本但召回 −1.7 个百分点。完整数据与保留观察见
`data/eval/20260904_v2/chunk_exp_report.md`（含"向量通道接入后复测 500/100 vs 800/80"的建议）。

**实施中发现并修复的既有缺陷**：`evaluation/cli.py` 的 `--suites` 参数被当成字符串逐字符迭代，
文档里写的 `run --suites main` 实际报"未知套件: ['m','a','i','n']"（历史 run 全部走的是"空 = 全部套件"）。
已改为逗号分隔解析（新增 `_split_suites`）并补守护用例 `tests/test_cli_args.py`（用例数 50 → 54）。

### 4.11 真实 LLM 的接入口径（2026-09-13 实测：中转 endpoint 为主，官方 endpoint 为备用）

**为什么有两个 endpoint**：用户决策——**项目期内用中转**（`commandcode` 聚合服务，便宜），
**项目结束后切回官方**（`api.deepseek.com`，贵）。代码侧用"密钥别名链 + 两套 fallback 配置"表达，
不需要为将来切回改代码。

**主用（项目期内）：中转 endpoint**

- base_url：`https://api.commandcode.ai/provider/v1`；密钥在**机器级环境变量 `RAG-command`**
  （本机已设置，长度 92、非 `sk-` 前缀；已实测可用）。
- 模型：**`deepseek/deepseek-v4.1-flash`**（已确认在 `/models` 返回的 69 个模型里；同族可选
  `deepseek/deepseek-v4-flash`、`deepseek/deepseek-v4-flash-fast`、`deepseek/deepseek-v4-pro`、
  `deepseek/deepseek-v4-flash-vision-exp`）。
- 该服务是**多模型聚合**（模型清单里包含 claude / gpt 系列），模型名必须写精确 id，
  写错会 400 `unsupported_model`（实测无静默兜底——这一点比官方 endpoint 可靠，官方会接受别名）。

**备用 / 项目结束后：官方 endpoint**

- base_url：`https://api.deepseek.com/v1`；密钥在环境变量 `RAG-deepseek-v4`（已实测可用）。
- 模型：**`deepseek-flash`**（官方 `/models` 只有 `deepseek-flash` 与 `deepseek-v4-pro`；
  仓库预填的 `deepseek-v4-flash` 能被接受但实际服务的是 `deepseek-flash`，不要依赖该别名）。

**密钥读取优先级（即切换开关）**

```
LLM_API_KEY → DEEPSEEK_API_KEY → RAG-command（中转，项目期优先） → RAG-deepseek-v4（官方）
```

- **项目结束后切回官方 = 把 `RAG-command` 从系统环境变量里删掉**（或在 `.env` 里显式设 `LLM_API_KEY`
  指向官方 key），零代码切换。
- 必须支持这一串别名的两个原因：Windows 新建环境变量要**重启进程**才会被 `os.environ` 继承
  （第一次实测就是当前进程读不到、要从系统环境变量存储取）；**连字符变量名在 Linux 上不合法**，
  部署到 Linux 时根本没法 export。
- **降级链的真实落点**：`FALLBACK_LLM_BASE_URL/API_KEY/MODEL` 是独立三件套，可直接指向**官方 endpoint**
  （不同域名、不同 key、`deepseek-flash`）——这样"中转挂了自动降级到官方"就是既有代码的自然行为。
  反之亦然（官方为主时用中转兜底）。

**实测数据（2026-09-13；SSE 端到端，含检索与提示词构造）**

| 问题 | endpoint / 模型 | 首 thinking 增量 | 首 answer 增量 | 总耗时 | completion / reasoning | 是否触顶 |
| --- | --- | --- | --- | --- | --- | --- |
| 赤壁之战的主帅是谁？ | 中转 `deepseek/deepseek-v4.1-flash` | 未单独测 | 4.76 s | 5.81 s | 361 / 161 | 否 |
| 介绍一下长平之战。 | 中转 `deepseek/deepseek-v4.1-flash` | 4.83 s | **13.11 s** | 16.62 s | 2047 / 1547 | **是（截断）** |
| 介绍一下长平之战。（上限 1024 时） | 同上 | 2.16 s | 6.01 s | 7.19 s | 1024 / 736 | **是（截断）** |
| 长平之战的主要经过和结果是什么？（**hybrid 检索**、上限 2048） | 中转 `deepseek/deepseek-v4.1-flash` | — | 12.99 s | 15.03 s | 2048 / 1510 | **是（截断）** |
| — | 官方 `deepseek-flash`（裸接口对照） | 0.70 s | 1.54 s | 1.85 s | — | 否 |

> 最后一行说明一件重要的事：**hybrid 检索带进更多证据 → prompt 从 1,865 涨到 4,349 token → 推理更长**，
> 于是更容易触顶、首正文更慢。上限因此从 2048 提到 **3072**；演示示例题必须按实测筛"短答快"的题。
> 这条链路（证据规模 ↔ 推理长度 ↔ 截断/延迟）是 v5 剩余调优的主线，归入 M5。

**三条必须按此实现的硬约束**：

1. **推理长度在同模型内波动极大**：同一模型同一流程，赤壁问题的 reasoning 只有 161 token（首正文 4.76 s），
   长平问题却到 1547 token（首正文 13.11 s）。这直接改变了两件事：
   - **首 Token 口径必须写清楚**：`docs/architecture.md` 的"首 Token ≤ 5 s"若指**首个可见输出**
     （中转的 thinking 流 4.83 s 开始）基本达标；若指**首正文**则做不到（4.76–13.11 s）。
     结论：把 thinking 流当作"响应已开始"的正式口径，前端必须有"正在思考"占位，否则演示观感是"点了十几秒没反应"。
   - **F08 示例题选型要新增一个维度**：除"评分非 incorrect"外，还要**实测每题的首 thinking / 首正文 / 是否触顶**，
     演示只放"首正文快且不截断"的题（见 §四.7）。
2. **`max_tokens` 会触顶并截断答案**：实测 1024 与 2048 **都被打满**（1024/1024、2047/2048），
   表现为"答案戛然而止"。已实现：`llm_client.py` 捕获服务端 `finish_reason`，等于 `length` 时
   记 warning（`rag.llm` logger）。默认值设 2048，但仍需按题观察。三个可选对策（实现时按评测选）：
   调大上限（更慢更贵）、**裁剪送模证据**（`FUSION_LIMIT` 现为 18，降到 10–12 观察）、
   提示词要求"先给结论、控制篇幅"。注意推理长度主要由模型决定，提示词只能间接影响。
3. **`max_tokens` 不能设小**：官方 endpoint 上 `max_tokens=64` 会正文全空（token 全被 reasoning 吃掉）；
   中转因为推理更长，风险更高。

**另外两个已实测的能力**：

- **两个 endpoint 都流式输出推理，只是字段名不同**（实测中转 730、1536 个 thinking 增量）：

  | endpoint | 流式推理字段 |
  | --- | --- |
  | 中转 | `delta.reasoning`（另有 `reasoning_details`） |
  | 官方 | `delta.reasoning_content` |

  实现必须同时读两个字段（`LLMClient._reasoning_of`）。只读官方字段会把中转的推理**静默丢弃**——
  当前 v5 实现前的代码只取 `delta.content`，思考内容不会污染答案（安全），但也拿不到 thinking 事件。
- **提示词缓存命中明显**：实测 `prompt_tokens=1865` 中 `cached_tokens=1664`（同题库反复评测时命中率更高）→
  耗时与成本要按"命中/未命中"分别记录；中转另返回 `cache_creation_input_tokens`。
  评测随机性（R2）在推理模型上依然存在（思考长度也会变），取样要求不变。


### 4.12 同名实体的朝代消歧（2026-09-14 复查补齐）

**数据侧不合并**：同名事件分属不同朝代（井陉之战 战国/西汉、潼关之战 南北朝/隋/唐/明、洛阳之战 隋/唐/明），
合并会丢掉其中一个朝代的记载且不可逆 → 1,215 组同名实体**保持原样**（用户 2026-09-14 判定）。

**代码侧消歧**：`server/query/understand.py::_resolve_ambiguity(hits, dynasty_filter, dynasty_bias)`
按三级优先处理同名多实体：

1. **显式筛选**（页面下拉，`filters.dynasty`）→ 硬过滤，唯一命中即选中（既有行为，不变）；
2. **问句提到的朝代**（`dynasty_bias`）→ **偏好**：同名候选中朝代相符者前置（`entities` 取首个），
   候选集合不变（其余仍进 `candidates`，页面可点选纠正）；
3. 都没有 → 保留全部，首个进 `entities`（与改动前逐字一致）。

**为什么是偏好而不是硬过滤**：v4 实测的坑——问"商朝"时鸣条之战属夏，一旦把"商朝"当硬筛选，
图谱/文本/融合全为 0、直接拒答（见 `classifier.extract_dynasty_mentions` 说明）；
这里只在同名候选之间排序，不剔除任何证据。

**朝代名宽松比对**（`_dynasty_hit`）：`唐 ≡ 唐朝 ≡ 唐代`、`明 ≡ 明朝`；仅当两侧长度不同且
长的一侧恰好多出 `朝/代/王朝` 时命中；`五代` 与 `五代十国` **不**互相命中（是不同时期）。

**可观测**：`F02Output.dynasty_disambiguated: bool`（是否因问句朝代改变了选择）→ entities 事件
（`server/sse.py`）+ 评测 trace（`evaluation/chain.py`）+ 契约（`docs/data-contract.md`）。

**实测**（真快照，词典路径，不调模型）：

| 问句 | 改动前 | 改动后 |
| --- | --- | --- |
| 西汉的井陉之战是怎么回事？ | `event_0175`（战国） | **`event_0219`（西汉）** |
| 唐朝的潼关之战是怎么回事？ | `event_0448`（南北朝） | **`event_0612`（唐）** |
| 井陉之战是怎么回事？（不带朝代） | `event_0175` | `event_0175`（不变） |
| 唐朝的洛阳在哪里？ | `place_0518`（秦） | **`place_1878`（唐）** |

对现有 9 道演示题与 28 道评测题影响 **0**（无题同时满足"提朝代 + 命中同名"），故不改动既有评测数字。
用例：`tests/test_f02_dynasty_disambiguation.py`（21 条——宽松比对参数化、排序与标记、
硬筛选优先于偏好、多命中前置、单命中不动、多 mention 互不干扰、真快照端到端三条）。

---

### 4.13 地点坐标获取（2026-09-15，复用旧项目 geocoding 工具链）

**背景**：F07 面板的"地图"模块因快照无坐标一直降级为地点卡片列表（F07 文档明确允许）。
2026-09-14 用户决定复用旧项目已有的高德编码工具补坐标。

**旧工具链核对结果**（`entity-event-relation/src/geocoding/`：export → geocode → review → import）：
2026-06-02 跑过一次，导出 111 条（仅上古/夏/商/西周），批准 73 条，但**从未导入旧库**——
实测 `backend/database` 的 `places` 表 5,316 行，`longitude/latitude/coord_source/coord_confidence`
四列非空数均为 **0**。旧工具自带 698 条"历史地名→今名"映射表（长安→西安、邺城→临漳等）。

**RAG 侧无需改动**：F09 `data/snapshot/export.py:81-82` 已导出 `longitude/latitude`；
F05 `panel_builder._build_map_points` 已读 `ent["longitude"]/latitude` 并按 `entity_id` 去重成
`MapPoint`——坐标一旦进快照，后端即生效。缺的只有前端地图渲染（`PlacesView.vue` 现为卡片列表）。

**新增 `scripts/fetch_place_coords.py`**（旧代码不改，只继承其 `AmapGeocoder` 复用检索名/地址构造）：

| 口径 | 取值 | 依据（实测） |
| --- | --- | --- |
| 去重 | 同一（地名+省份）只调用一次，坐标写回该组所有行 | 面板按地名匹配，同名跨朝代行共用坐标；5,316 行 → **3,595 个目标** |
| 排序 | 按地址线索齐全度（今址/省/市 三项）降序 | 无线索的古地名多返回 `30001`（引擎无结果） |
| 速率 | 基准 0.35 s/次（≈3 QPS）+ QPS 错误码原地退避重试（≤3 次） | 20 QPS 时 22% 请求被 `10021` 拒绝；降到 3 QPS 后 **0 拒绝** |
| 断点 | 每条调用后立即追加 `data/cache/amap/coords_progress.jsonl` | 旧工具整批跑完才落盘，中断即白烧额度 |
| 配额保护 | `10003/10044`（日额度）与 `10001/10002/10005/10009`（鉴权/白名单）出现即停并打印续跑命令 | 不把剩余额度烧在必然失败的请求上 |
| 密钥 | `--api-key` → `AMAP_API_KEY` 环境变量 → `RAG/.env` → 旧项目 `src/geocoding/.env` | 不复制密钥、不打印；两份 `.env` 均不入库 |

**结果去向（待用户确认，见 §11.2）**：坐标最终要进 RAG 快照，两条路径任选——
① 写回旧库 `places` 表（旧项目 `import` 步骤，需先备份库）→ 重跑 F09 导出；chunks 不变，
索引目录可复制并改 `manifest.source_snapshot`，避免重新嵌入（省约 6–8 分钟与 1–2 元）；
② RAG 侧做坐标覆盖文件由 F09/panel 读取，不动旧库。

**配套前端**：坐标到位后需补地图渲染（echarts 已在依赖内，另需中国底图 GeoJSON 或在线瓦片），
并保留"无坐标 → 卡片列表"的降级。

**执行结果（2026-09-15，三批 + 一次网络补跑，共约 3,280 次调用）**：

| 指标 | 数值 |
| --- | --- |
| 编码目标（地名+省份去重） | 3,595 |
| 成功 | **3,193（89%）** |
| 失败 | 402（其中 **398 条无省份线索**，高德返回 `30001` 引擎无结果；4 条为重复尝试仍失败） |
| 覆盖旧库行数 | **4,819 / 5,316（91%）**（同名同省行共享坐标） |
| 批次节奏 | 批 1：1,000/1,000 成功；批 2：932/1,000；批 3：956/1,355；补跑：66/87 |
| 精度层级（高德 `level`） | 区县 1,290、市 622、村庄 523、住宅区 280、乡镇 207、省 140、兴趣点 90，其余 51 |

演示涉及的 9 个地点**全部取到坐标**：长平→高平市 (112.92, 35.80)、上党郡→长治 (113.12, 36.20)、
河内→晋城 (112.85, 35.49)、赤壁→赤壁市 (113.90, 29.73)、江陵→荆州 (112.19, 30.35)、
夏口→汉口 (114.33, 30.70)、淝水→东淝河 (116.83, 32.20)、阪泉→怀来县 (115.52, 40.42)、
涿鹿→涿鹿县 (115.20, 40.38)。

**两点已记录的口径**：① 市级/省级层级（762 条）是"区域代表点"，地图打点可用但属近似，
演示/文档中应说明；② 402 条失败集中在无地址线索的古地名，若需要提高覆盖率，办法是给旧项目的
698 条映射表补充"古地名→今址"条目后重跑（`--retry-failed`），不是换 API。

### 4.14 坐标接入快照与前端地图（2026-09-15，用户选择"两条路都走"）

**① 写回旧库**（旧项目自己的 `import` 步骤，满足"旧项目地图也要用"）：

1. 先备份 `backend/database`（13 MB，留 `database.bak-<时间戳>`）；
2. 由 `coords_progress.jsonl` 展开成"每行一条"的导入文件（3,193 个成功目标 → **4,819 行**），
   字段 `place_id/longitude/latitude/source='amap'/confidence=高德 level/note=检索名+标准地址`；
3. 用旧项目 `import_coordinates.import_coordinates()` 写库：**4,819/4,819 全部成功**，
   旧库 `places` 的 `longitude/latitude/coord_source/coord_confidence` 四列非空由 0 → 4,819。

**② 进 RAG 快照**（重跑 F09，不重新嵌入）：

1. `python scripts/export_snapshot.py` → 新快照 **`20260915_v1`**（实体 9,925 / 关系 17,700 /
   地点 5,316，其中 **4,819 有坐标**）；
2. `python scripts/build_index.py --version 20260915_v1 --no-embeddings` 建 chunks + FTS5（免费）；
3. **复用旧向量**：逐条比对新旧 `chunks.jsonl`（9,544 条 `chunk_id` 与文本**全部一致**，
   仅 `source_version` 元数据不同）→ 复制 `vectors/`（含 chroma）并改写 `manifest.vectors`
   （加 `reused_from: 20260904_v2`），**省去重新嵌入的 6–8 分钟与 1–2 元**；
4. 校验：`check_vector_consistency.py --version 20260915_v1` → 条数一致、
   Chroma 与暴力余弦 top-20 重合率 **0.932**（下限 0.9）。
   （注：若不加 `--version`，脚本会用"随机片段文本"当查询而得到 0.545 的假低值——
   片段查询有大量近似重复邻居，HNSW 近似性被放大；**抽检必须用题库问题**。）
5. 评测与演示的版本化产物随版本前移：`data/eval/20260915_v1/` 放入 `questions.jsonl`（题库内容不变）
   与 `demo_examples.json`（示例题与实测时延沿用 09-13 的测量值，检索链路未变）。

**③ 前端地图**（`frontend/src/components/panel/MapView.vue`）：echarts `geo` + `scatter`，
底图 `src/assets/china-map.json`（省级 GeoJSON，569 KB，随包走、不依赖外网）；
`PlacesView.vue` 有坐标时出地图、无坐标保持卡片列表（F07 的降级口径不变）；
点位大小随相关事件数、悬停显示"古地名（今址）+ 相关事件 N 个"，附"近似点位"说明。

**评测数字随版本前移的验证**：在新快照/索引上离线复跑 `main` 套件（`runs/v5_coords_v1`，不调 LLM），
与旧版本 `v5_vec_fusion10` 报告**十列逐项一致**（实体命中 42.6% / 文本召回 97.0% / 融合携带 97.0% /
引用携带 77.1% / 回答覆盖 85.1% / ok 28/28）——坐标与消歧改动不影响检索链路，旧评测结论继续有效。

**验证**：`smoke_deploy.py --base http://127.0.0.1:8011` → **9/9 示例题通过**（含缓存命中 23 ms），
`/api/query` 端到端 SSE 的 `panel.map_points` 对"赤壁之战"给出 **夏口(汉口)、赤壁(赤壁市)、江陵(荆州市)** 三点；
用例 **134 → 140 全过**（新增 `tests/test_panel_map_points.py` 6 条）。

---

## 五、实施步骤（M0–M5，每步给命令与判据）
> 全部命令从 `RAG/` 根目录执行，解释器用项目环境（如 `E:/anaconda/envs/AI_Agent/python.exe`）。

### M0 开工准备

1. 拍板规划说明 §4.2 的待确认项（至少 LLM 密钥、部署目标、`demo_mode` 语义、文本模式开关形态）；
2. 建分支：`git checkout -b ragv5`（当前 `ragv4-evaluation` 已提交 `50aafa2`）；
3. 基线复查：`python -m pytest tests -q` 应 **50 passed**；`python scripts/run_evaluation.py check-bank`
   应"结构通过 + 7 条词典提示"；确认 `run_20260913_postaudit` 未被重写（`traces.jsonl` 4554881 B）；
4. ~~依赖共存预检~~ **已完成（2026-09-13）**：`chromadb 1.3.4` 已在项目环境、与既有依赖共存（§4.3）；
5. 百炼接口连通与维度 **已完成（2026-09-13）**：单条 1024 维 / batch=10 实测通过（§4.3）；
6. **分块参数实验（T8）**：先做「索引变体机制」（§4.10 前置），再跑
   `python scripts/compare_chunking.py --sizes 500/100,800/80,1200/120 --configs dual,text-only`
   → 结论决定 `CHUNK_*` 取值；**先于 M1 全量建向量**。

### M1 向量线

1. ✅ **构建侧已实现并完成全量构建**（2026-09-13）：
   `data/index/embeddings.py`（百炼客户端：batch ≤10、重试、维度断言）、
   `data/index/vector_pipeline.py`（分批落盘 + 断点续跑 + 并发批 + 拼 npy + 写 Chroma + 更新 manifest）、
   `data/index/chroma_store.py`、`scripts/build_index.py --vectors-only/--sample/--no-resume/--concurrency`；
   实测：**9,544 条 / dim=1024 / 955 批 / 365 s（并发 4）**，`chroma.count()==len(ids)==npy 行数`；
   中途因 `np.save` 会补 `.npy` 后缀导致改名失败中断一次，已修（临时名以 `.npy` 结尾）并抢救了已付费的批次。
2. ✅ **一致性抽检**：`scripts/check_vector_consistency.py --sample 20 --top-k 20` →
   条数一致；Chroma top-20 与暴力余弦 top-20 重合率 **均值 0.935（最低 0.800）** ≥ 下限 0.9（HNSW 近似性已量化）。
3. ✅ **检索侧已实现**：`server/text/searcher.py`（`search_vector`/`search_hybrid`/`_where_of`）、
   `server/text/scoring.py`（`fuse_weighted`/`fuse_rrf`/`fuse_hybrid`）、`server/text/__init__.py` 三分支；
   `server/sse.py` 按 `TEXT_MODE` 传模式、拒答规则按模式分档；`cache_key` 纳入 mode（C3）；
   `server/runtime.py` 注入 embed_fn 与集合名、`meta.text_mode` 反映真实配置。
4. ✅ **对照评测**（`--suites main --configs text-only,text-only-and,vector,hybrid`，离线回答器）：

   | 配置 | 文本 top-k 召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
   | --- | --- | --- | --- | --- | --- |
   | text-only（关键词 and_or） | 94.6% | 94.6% | 66.7% | 71.7% | 28/28 |
   | text-only-and（纯 AND） | 2.4% | 2.4% | 1.2% | 1.2% | 1/28 |
   | vector | 89.9% | 89.9% | 67.9% | **85.1%** | 28/28 |
   | **hybrid（rrf）** | **95.2%** | **95.2%** | **77.1%** | **85.1%** | 28/28 |

   **回答覆盖从关键词基线的 71.7% 提升到 85.1%（+13.4 个百分点）——且这是离线回答器下的结果，
   提升完全来自检索质量**。纯向量 top-k 召回（89.9%）低于关键词是"标注词口径偏词面重合"所致，
   但它的回答覆盖反而更高（85.1% vs 71.7%），说明语义召回命中了对回答有用、但词面不重合的片段。
5. ✅ **对照评测已跑两次取样**（`--configs hybrid --llm`，各 28 题）：

   | 指标 | v4 离线基线（dual，keyword，18 证据） | v5 样本 1 | v5 样本 2 |
   | --- | --- | --- | --- |
   | 文本 top-k 召回 | 94.6% | 97.0% | 97.0% |
   | 融合文本携带 | 86.3% | 97.0% | 97.0% |
   | 引用携带(文本) | 63.7% | 77.1% | 77.1% |
   | **回答覆盖** | **67.6%** | **84.5%** | **90.8%** |
   | ok/n（自动口径） | 28/28 | 24/28 | 26/28 |
   | finish_reason | — | 28/28 normal | 28/28 normal |
   | 生成耗时 | <50 ms（离线） | 中位 10.5 s / 最大 25.5 s | 中位 9.3 s / 最大 20.6 s |

   - **ok/n 从 28/28 降到 24–26/28 不是退步**：`ok` 是"回答里出现标注词"的机械口径，
     离线回答器把证据原文抄进答案所以必中；真实 LLM 用自然语言作答，措辞不同就会漏词。
     v4 的"28/28 ok 但 0 correct"正好是反面 —— 正确性仍以人工评分为准。
   - **14 条"检索到位、回答未用"重点题**（本次套件内 10 条）：样本 1 中 8/10 达 100% 覆盖，
     掉队的是 `M01`(0%)、`M12`(0%)；样本 2 中也是 8/10，掉队的是 `M07`(0%)、`B03`(75%)。
     **两次掉队的题不同** → 随机性已量化（R2 要求），单题读数不可用于结论，需看分布。
   - **截断仍存在但未进 trace**：两次分别触发 5 / 3 次 `finish_reason=length` 告警（约 11–18% 的题），
     而 trace 里的 `finish_reason` 只记 normal/degraded/refused，**不含截断** —— 这是可观测性缺口，
     调优旋钮仍是 `QUERY_FUSION_LIMIT`（→8）与 `LLM_MAX_TOKENS`。
6. ✅ **对照评分已做（AI 代理口径，2026-09-14 用户确认为对外口径）**：三次取样后在
   `runs/v5_llm_3/scores.jsonl` 完成 28 条评分——**25 correct / 3 partial / 0 incorrect**、
   引用 28/28 supported（v4 基线 0/23/16，见 §九 §2.2-4）。

### M2 LLM 线（可与 M1 并行）

1. ✅ `server/sse.py` 流式桥接（§4.4）+ 缓存卫生（§4.9）——**已实现并实测**：
   中转 endpoint 下端到端产出 197–495 个 `answer` 帧、1536 个 `thinking` 帧；
   同题复问 0.01 s 命中缓存；不可达 endpoint 下 `finish_reason=degraded` +
   `model_used=heuristic-offline`，且**再问同题不命中缓存**（缓存卫生生效）；
   降级/启发式路径仍按句切分（13 帧），不会"一大坨瞬时出现"；
2. ✅ 配置层与客户端（§4.11）——**已实现**：密钥别名链（`LLM_API_KEY` → `DEEPSEEK_API_KEY` →
   `RAG-command` → `RAG-deepseek-v4`）、`LLM_MAX_TOKENS`（默认 2048）、
   两端推理字段兼容（`_reasoning_of`）、`stream_options={"include_usage": True}` 带 usage、
   `finish_reason=length` 截断告警；`.env` 已配中转 + 官方备用（密钥不入库）；
   `contracts` 与 `docs/data-contract.md` 的 `thinking` 事件已从"保留枚举"改为"实际发射"；
3. ✅ `server/query/understand.py` F02 LLM 兜底实现 + 开关 + 用例（§4.5：`prompts.py` + `llm_fallback.py` + 兜底分支，11 条用例）；
4. ✅ 提示词/证据裁剪调优：`QUERY_FUSION_LIMIT` 18 → 10（D9 实测定），截断告警 13 → 3 次、可用示例题 5 → 9 条；
5. ✅ **对照评测**：`--llm` 三次取样（`v5_llm_1`、`v5_llm_2`、`v5_llm_3`，各 28 题），回答覆盖 84.5% / 90.8% / 97.0%；
6. ✅ **判据**：14 条重点题（套件内 10 条）8/10 → 8/10 → **10/10**，与 v4 基线并排（§九 §2.2-4）。
   另附 28 条评分（AI 代理口径，用户已确认）：25 correct / 3 partial / 0 incorrect。

> 复现命令（已实测）：`python scripts/run_server.py --host 127.0.0.1 --port 8000` 后
> `curl -N -X POST http://127.0.0.1:8000/api/query -H "Content-Type: application/json" -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'`

### M3 F08

1. ✅ `scripts/gen_demo_examples.py`：39 题 → 候选 18（剔除 10 条 `incorrect` + 11 条非 main 套件）→
   **实测筛出 9 条**（再剔除 3 条截断 + 6 条首正文超 9 s）；清单落
   `data/eval/20260904_v2/demo_examples.json`（入 Git，含 `category_label`/`capability`/`expect`/`measured`）；
2. ✅ 后端 `GET /api/demo/examples`（读清单；缺失返回 503）+ 前端按类别分组渲染、显示能力标签与实测时延
   （`ChatPane.vue` 不再有硬编码示例题）；
3. ✅ 前端构建通过（`cd frontend && npm run build`），同源托管下页面与接口一并可用；
4. ✅ 冒烟：`python scripts/smoke_deploy.py --base http://127.0.0.1:8000` → **9/9 条通过**
   （`finish_reason=normal`、回答非空、10 条引用），复跑命中缓存 29 ms；`--check-rate-limit` 第 29 次触发限流；
5. ✅ 无密钥整机冒烟已跑（§2.1-5 / §九）：9/9 示例题离线作答、31–79 ms、自动降级 keyword。

### M4 部署

1. ✅ `server/api.py` 静态挂载 dist（路由注册之后）+ `FRONTEND_DIST` 配置 + `run_server.py` 启动自检；
2. ✅ `scripts/smoke_deploy.py`（6 步：health / 页面 / 示例题接口 / 逐条示例题 / 缓存命中 / 限流）；
3. ✅ `docs/deploy.md`：前置、数据制品清单与体积、从零构建、环境变量（含密钥别名链与外网依赖）、
   启动自检、冒烟与"预热"用法、演示预期表现、故障排查、授权边界；
4. ✅ 限流复核：默认 30/min，实测第 29 次请求触发（判据与"参数不合法"区分开）；
5. ✅ 无密钥整机冒烟（与 M3 第 5 条同一项，已跑，见 §九）。

### M5 质量优化与收口

1. 拒答扩展（双处同步）→ 跑 `--suites main,refusal`，确认 X01–X03 转 `unknown_answer` 且 main 无新拒答；
2. AND 兜底 + raw/evidence 的 `event_type` 元数据 → 重建索引 → 跑 `--suites main,long_rewrite,filter_loss`；
3. 全量对照报告（向量 / LLM / 质量优化三个维度与 `run_20260913_postaudit` 并排）；
4. `RAGv5-开发说明.md` 回填实测（§十）、T7 文档同步、`pytest` 全绿。

---

## 六、验证方案

### 6.1 自动化与冒烟

| 层次 | 命令 | 判据 |
| --- | --- | --- |
| 单测 | `python -m pytest tests -q` | 全部通过；v5 新增用例覆盖：余弦距离→相似度换算、归一化边界、`where` 过滤与 `_pass_meta` 一致、hybrid 三种策略档位、F02 兜底、拒答新规则、AND 兜底、Chroma 缺失/损坏降级 |
| 依赖共存 | ~~临时环境安装 `chromadb`~~ **已完成（2026-09-13）**：项目环境已装 `chromadb 1.3.4`，与 `fastapi`/`pydantic`/`numpy`/`openai` 共存无冲突 | 通过；`requirements.txt` 记 `chromadb>=1.3` |
| 向量一致性 | `python scripts/check_vector_consistency.py --sample 20` | `chroma.count()==len(ids)==npy.shape[0]`；抽样 20 条问句的 Chroma top-20 与暴力余弦 top-20 重合率 ≥ 0.9（低于则记录实测值并评估 HNSW 参数） |
| 题库 | `python scripts/run_evaluation.py check-bank` | 结构通过 + 词典提示条数与 v4 一致（7 条） |
| 回归对拍 | 复跑两次不同 `--run-id`，比较 `traces.jsonl`（忽略 `stage_ms`） | 0 内容差异（证明非 LLM 链路确定性未被破坏） |
| 部署冒烟 | `python scripts/smoke_deploy.py` | 退出码 0；报告落 `logs/` |
| 基线完整性 | 复核 `data/eval/20260904_v2/runs/run_20260913_postaudit/traces.jsonl` 大小与时间戳 | 未被 v5 改动重写 |

`scripts/smoke_deploy.py` 设计（新建）：

```
1) GET /api/health                → status=ok，记录 version / text_mode / vector_available / llm_available
2) 逐条示例题 POST /api/query      → done、answer 非空、finish_reason ∈ {normal, refused?}（示例题要求 normal）、
                                     证据数满足 examples[].expect 下限
3) 复跑第 1 条（同进程同 session） → cache_hit=true 且 answer 与首次一致
4) --check-rate-limit（可选）      → 连续 31 次请求后出现 error(invalid_request)
5) 未配置 LLM 时打印"离线形态"标记，配置了则额外记录 model_used
输出：logs/smoke_<ts>.json + 控制台汇总；任一断言失败 → 非零退出
```

### 6.2 评测对照矩阵（T6）

| 配置 | 目的 | 命令片段 |
| --- | --- | --- |
| `dual` / `text-only`（关键词 and_or） | 生产基线（对齐 v4） | `run --suites main --configs dual,text-only` |
| `text-only-and` | 长改写 AND 失效面 | `run --suites long_rewrite --configs text-only-and,text-only-or` |
| `vector` / `hybrid` | 向量通道效果（需补预设） | `run --suites main --configs text-only,vector,hybrid` |
| `hybrid` 三档位 | 定 §4.2 的融合默认口径（weighted / rrf / fallback） | `run --suites main --configs text-only,hybrid` + 改 `TEXT_HYBRID_STRATEGY` 各跑一次 |
| 分块参数三组 | T8：500/100、800/80、1200/120 对比 | `python scripts/compare_chunking.py --sizes 500/100,800/80,1200/120 --configs dual,text-only` |
| `--llm` 三连跑 | 真实模型分布（R2） | `run --suites main --llm --run-id v5_llm_{1,2,3}` |
| `filter_loss` | event_type 元数据修复效果 | `run --suites filter_loss --configs dual,text-only` |
| `refusal` | 拒答规则效果 | `run --suites refusal` |

报告要求：每个配置给出"文本 top-k 召回 / 融合文本携带 / 回答覆盖 / ok-n"，并与
`run_20260913_postaudit` 并排；随机性按 R2 标注；分块实验报告另给"片段数 → 向量化请求数与
存储体积"的成本对比（batch=10 的放大效应）。

### 6.3 人工复核（不可省）

1. **14 条重点题**（`review_report.md` §4.3(4)）逐条看是否转 `correct`；
2. **示例题清单**逐条人工过一遍（能力标注、表述、是否适合对外展示）；
3. **拒答 3 题**（X01–X03）确认拒答文案得体、不再硬答；
4. 评分口径不变：人工评分，不得用模型评分替代（F10 口径）。

### 6.4 通过标准（进入收口的门槛）

- 规划说明 §二 的各条验收判据逐条有证据（日志/报告/trace）；
- `pytest` 全绿、`check-bank` 通过、两次复跑对拍 0 差异；
- 对照报告结论明确：向量/LLM 至少一项相对基线有**可解释的提升**，或明确说明"无提升及原因"；
- 部署冒烟与"照文档从零启动"均成功。

---

## 七、部署与运行手册（`docs/deploy.md` 的内容骨架）

1. **前置**：Python 环境 + `pip install -r requirements.txt`；Node 环境仅构建前端时需要；
2. **数据制品**：`data/snapshot/<v>`、`data/index/<v>`（含 `vectors/`）随包携带；启动校验版本一致；
3. **环境变量**：见附录 B（必填：`LEGACY_*` 仅离线链路需要；在线演示至少确认 `TEXT_MODE`、限流、
   `LLM_*`、`EMBEDDING_*` 是否配置）；
4. **启动**：`python scripts/run_server.py --host 0.0.0.0 --port 8000`（同源托管时前端无需单独启动）；
5. **自检**：`curl http://127.0.0.1:8000/api/health` → `status=ok`，核对 `vector_available` /
   `llm_available` / `meta.text_mode` 与预期一致；
6. **冒烟**：`python scripts/smoke_deploy.py`；
7. **常见故障**：
   - 启动即 `FileNotFoundError: 快照…无同版本索引目录` → 数据制品缺失或版本不一致；
   - `vector_available=false` 但 `.env` 配了 `EMBEDDING_*` → 构建期没落向量（查 `manifest.vectors.mode`）；
   - 缓存"不生效" → 重启后首次必然全量，属预期；限流"误伤" → 按 IP 计数，演示前确认出口 IP；
   - 多 worker 导致图谱/缓存重复 → `workers` 必须为 1。
8. **外网依赖与"无外网演示"的边界**（部署前必须确认演示现场能否访问公网）：

   | 环节 | 是否需要公网 | 说明 |
   | --- | --- | --- |
   | 建索引（F09/F11，一次性） | **需要** | 云端 embedding 批量调用；构建完成后产物（Chroma + FTS5）本地可用 |
   | 图谱检索 F03 / 关键词检索 F04 | 不需要 | 纯本地内存与 SQLite |
   | 提问时的**向量检索** | **需要** | 问题要实时转成向量（云端 embedding），无网时 `vector_available` 仍在但查询会失败 → 必须走降级 |
   | 提问时的**真实 LLM（F06/F02）** | **需要** | 中转 `api.commandcode.ai` 或官方 `api.deepseek.com` 都不可达 → `finish_reason=degraded` 离线回答器 |
   | 离线摘要回答器（`heuristic-offline`） | 不需要 | 无密钥/断网时的兜底路径 |

   - **有外网（推荐）**：主演示路径 = 向量 + 真实 LLM；同时保留降级链以应对现场网络抖动。
   - **无外网**：主演示路径只能是"关键词检索 + 离线摘要回答器"，回答质量会退回 v4 基线形态
     （0 correct / 23 partial / 16 incorrect）——**F08 的示例题选型与冒烟判据都要按这个形态验收**，
     并且不要在现场演示"真实模型"相关能力（会看到降级提示）。
   - **折中**：用手机热点/移动网络给演示机提供外网；或在有网环境部署好后现场只访问页面
     （但提问仍需演示机能出公网，前端所在机器与后端同机时即等同）。
   - 注意：回答缓存是**进程内**的（重启即失），所以"提前预热缓存"必须在**演示机启动服务之后、
     演示开始之前**完成（冒烟脚本天然会做一次）；跨重启不保留，不能当作离线方案。

---

## 八、风险与回退

| 风险 | 预防 | 回退 |
| --- | --- | --- |
| 向量通道排序不可解释（R1） | §4.2 口径 + 四配置对照；先评测后调权重 | `TEXT_MODE=keyword` 一键回退 |
| 真实 LLM 评测随机（R2） | 固定模型与温度、2–3 次取样 | 评测加 `--llm` 开关；生产可 `ENABLE_LLM_ENTITY_FALLBACK=false` |
| 流式改造破坏事件顺序/缓存 | 顺序契约不变 + 冒烟两条路径 | 保留 `_chunk_answer_stream` 分支，可切回按句切 |
| 向量召回与"无共享词拒答"冲突 | §4.2-1 按模式分档 + refusal 套件复跑 | 规则回退为"仅 keyword 生效" |
| 降级结果污染缓存 | §4.9 仅 normal/refused 入缓存 | 重启进程清空缓存 |
| 索引重建破坏既有基线 | `--vectors-only` + 先 `--sample` + 不改旧 run 目录 | 保留 v4 索引目录，切 `TEXT_MODE=keyword` 即回到 v4 口径 |
| `chromadb` 依赖与现有环境冲突 | M0 依赖共存预检并锁定版本 | 构建与检索侧改用独立环境/进程；或退为 npy 暴力余弦（§4.3 已保留 npy 审计副本） |
| HNSW 近似召回导致静默漏检 | 一致性抽检（§六.1，重合率 ≥ 0.9）+ 抽检记录 | 调 HNSW 参数（`M`/`search_ef`，参数名以安装版本为准）；必要时按 ids 顺序回退暴力余弦 |
| 分块参数实验得出"需切换"但成本上升 | 报告给成本对比（片段数 → 请求数与存储体积） | 保持 800/80；或只对特定语料分档（需再评测） |
| Chroma 元数据限制误用（list/None） | 元数据只写标量、缺失 key 直接省略（§4.3） | 写入前校验并报错，避免静默丢过滤能力 |
| F02 兜底拖垮首 Token | 默认关闭 + 独立 8s 超时 + 结果缓存 | 关闭开关 |
| 示例题引入未审核问题 | 清单来自题库过滤 + 人工复核 + 冒烟断言 | 重新生成清单（不动前端） |
| 公开演示的数据授权 | 未确认授权前仅内部/比赛演示 | 只演示已授权范围 |
| 契约漂移 | 新字段先改 `data-contract.md` | T7 收口逐条比对 |

---

## 九、验收对照矩阵

| 需求（规划说明） | 实现落点 | 证据 | 状态 |
| --- | --- | --- | --- |
| §2.1-1 无登录可提问 | 前端单页 + 同源托管 | ✅ 冒烟：`GET /` 返回页面、示例题一键提问全通 | ✅ |
| §2.1-2 示例题稳定 | `demo_examples.json` + 流式/缓存 | ✅ 冒烟 9/9 条 `finish_reason=normal`、回答非空、10 条引用；复跑命中缓存 29 ms | ✅ |
| §2.1-3 两类能力覆盖 | `capability` + `expect` 下限 | ✅ 清单 9 条全部 `capability=both`（图谱与文本证据同时存在），冒烟断言证据下限 | ✅ |
| §2.1-4 来自已审核题库（含实测筛题） | `scripts/gen_demo_examples.py` | ✅ 39 题 → 候选 18 → **实测筛出 9 条**（剔除 10 条 incorrect、11 条非 main 套件、3 条截断、6 条超时延） | ✅ |
| §2.1-5 无密钥可演示 | 离线回答器 + 关键词通道 | ✅ 整机无密钥冒烟（`LLM_BASE_URL=""` + `EMBEDDING_BASE_URL=""`）：9/9 示例题离线作答、31–79 ms、缓存一致；模式自动降级 keyword | ✅ |
| §2.1-6 能力标签 | `ChatPane.vue` 分组渲染 + 标签 | ✅ 按类别分组、显示"图谱+文本"能力标签与实测时延；前端 `npm run build` 通过 | ✅ |
| §2.2-1 真实流式 | §4.4 桥接（已实现） | ✅ 实测中转下 197–535 个 `answer` 增量 + 1,194–1,536 个 `thinking` 增量 | ✅ |
| §2.2-2 降级链 | 既有 `llm_client` + §4.9 | ✅ 实测不可达 endpoint → `degraded`/`heuristic-offline`，再问同题**不命中缓存** | ✅ |
| §2.2-3 F02 兜底 | `understand.py` 新分支（已实现） | ✅ `server/query/prompts.py`（抽取提示词+JSON 容错解析）+ `llm_fallback.py`（同步客户端、独立 8 s 超时）+ `understand.py` 的兜底分支（失败/超时/解析失败一律降级、结果按问句缓存）；11 条用例；端到端实测：词典命中的问题零额外调用，`介绍一下拿破仑战争的时间线。` → `entities=['拿破仑战争']`、`llm_entity_used=True`。**实测坑：兜底 `max_tokens=256` 会被推理 token 吃满导致正文为空**，已提到 1024 | ✅ |
| §2.2-4 LLM 对照 | 评测 `--llm` × 3（已跑）+ 评分（AI 代理口径） | ✅ 三次取样（各 28 题）：回答覆盖 **84.5% / 90.8% / 97.0%**（v4 基线 67.6%）、引用携带 77.1%、文本召回 97.0%、ok/n 24→26→**28/28**；**答案正确性（样本 3）25 correct / 3 partial / 0 incorrect**（v4：0/23/16），引用 28/28 supported；3 条 partial 均为模型偶发失败降级离线摘要；生成耗时中位 9.3–10.5 s、最大 25.5 s；截断告警 5/3 次（不入 trace，见下方缺口）。14 条重点题（套件内 10 条）：8/10 → 8/10 → **10/10**，前两次掉队题不同 → 随机性已量化。评分表 `runs/v5_llm_3/scores.jsonl`（AI 代理口径，用户已确认） | ✅ |
| §2.3-1 向量重建（百炼 v4 + Chroma） | §4.3 | ✅ 9,544 条 / dim 1024 / 955 批 / 365 s；`chroma.count()==ids==npy==9544` | ✅ |
| §2.3-2 vector/hybrid | `searcher.py` + `scoring.py` + `__init__.py` | ✅ 单测 + 四配置实测（hybrid 文本召回 97.0%、回答覆盖 85.1%） | ✅ |
| §2.3-3 降级（索引损坏/缺失） | 既有校验 + Chroma 探测 | ✅ `tests/test_vector_degradation.py` 覆盖三种破坏（集合目录缺失/集合名不匹配、`ids.json` 改短、集合少行）→ 均 `vector_available=False` 且 `search_vector` 返回空不抛错 | ✅ |
| §2.3-4 四配置对照 | T6 预设（已补） | ✅ `v5_vec_compare`/`v5_vec_fusion10`/`v5_vec_longrewrite`：回答覆盖 71.7%→85.1%；长改写 AND 0% vs vector/hybrid 100% | ✅ |
| §2.4-1 托管 | §4.8（已实现） | ✅ 冒烟：`GET /` 返回 HTML（909 B）+ `/api/health` ok | ✅ |
| §2.4-2 冒烟 | `scripts/smoke_deploy.py` | ✅ 6 步全通（health / 页面 / 示例题接口 / 9 条示例题 / 缓存命中 / 限流），报告落 `logs/smoke_*.json` | ✅ |
| §2.4-3 限流复核 | 既有滑动窗口 30/min | ✅ 实测第 29 次请求触发 `rate limited`（判据与"参数不合法"已区分） | ✅ |
| §2.4-4 部署说明 | `docs/deploy.md` | ✅ 从零构建 → 环境变量 → 启动自检 → 冒烟预热 → 故障排查（含外网依赖与授权边界） | ✅ |
| §2.5-1 拒答补强 | §4.6（已实现） | ✅ refusal 套件：**X01–X03 全部转 `correct_refusal`**（此前 `should_refuse_answered`）；main 套件无新增误拒（28/28、失败类别 `-`） | ✅ |
| §2.5-2 AND 兜底 | `searcher.py`（已实现） | ✅ text-only（关键词 and_or）四项指标齐升：文本召回 **94.6%→95.8%**、融合携带 88.1%→89.3%、引用携带 62.8%→65.2%、回答覆盖 **71.7%→74.1%**，无新增失败 | ✅ |
| §2.5-3 筛选元数据 | `chunking.py`（evidence 关联事件卡片补 `event_type`，覆盖 **7503/7503**）+ `_pass_meta` 对无该元数据的原文片段放行 | ✅ filter_loss 套件：**F01 开筛选后 0% → 100%/100%/50%/100%**（四题 filters-on 全绿） | ✅ |
| §2.5-6 契约同步 | `data-contract.md` | ✅ `thinking` 改为实际发射；`text_results` 补 `mode`（keyword/vector/hybrid/none）与筛选语义说明 | ✅ |
| §2.5-7 分块参数实验（T8） | §4.10 + `compare_chunking.py` | ✅ 三组对照报告 + "保持 800/80"结论 | ✅ |

---

## 十、文档同步清单（✅ 全部完成；2026-09-14 复查）

1. ✅ 本文档：§三 改动清单按实况回填（含 2026-09-14 补齐的 `build.py` 接线 / `--rebuild-chroma` / `requirements.txt`）、§九 状态列全 ✅、§十一 进度快照滚动更新；
2. ✅ `docs/RAG_v1/RAGv5-规划说明.md`：状态行改"已完成"、§七 里程碑逐条收口、§八-7 的 `thinking` 边界条目作废、§四.2 待确认项给结论、§十 现状核查表加落地说明；
3. ✅ `docs/RAG_v1/README.md`：v5 三行（规划说明 / 开发说明 / 阶段工作总结）与遗留项整改方案行；
4. ✅ `docs/README.md`：功能状态表 F04/F06/F08/F11、开发顺序第 4/5 条、阶段命名说明；
5. ✅ `docs/features/04-text-retrieval.md`、`06-grounded-answer.md`、`08-demo-mode.md`、`11-text-indexing.md`：状态行与"已确认需求"；
6. ✅ `docs/data-contract.md`：`text_results.mode`、`thinking`、`entities.llm_entity_used`、筛选语义；
7. ✅ `contracts/request.py`：`F02Output.llm_entity_used`（未做"按请求切模式"，故请求契约不新增字段）；
8. ✅ `docs/changes/20260913-ragv5-summary.md` 与 `RAGv5-阶段工作总结.md`；
9. ✅ `scripts/README.md`、`server/text/README.md`、`server/query/README.md`（2026-09-14 补：新脚本、Chroma 检索、F02 兜底）；
10. ✅ `README.md`（RAG 根）：用例数 134、启动与演示部署入口、依赖（chromadb）与密钥来源（2026-09-14 补）；
11. ✅ `docs/deploy.md`：构建/向量重建命令与冒烟流程（2026-09-14 补 `--rebuild-chroma`）。

---

## 十一、进度快照（滚动更新）

> 最后更新：2026-09-14。本快照随实现推进滚动改写；逐条验收状态见 §九。

### 11.1 已完成（附实测证据）

| 任务 | 完成内容 | 关键证据 |
| --- | --- | --- |
| **T8 分块参数实验** | 索引变体机制（`--index-suffix` + 加载侧回指快照）+ 三组对照 + 结论"**保持 800/80**" | `data/eval/20260904_v2/chunk_exp_report.md`；`scripts/compare_chunking.py` |
| **T3 向量线**（构建 + 检索 + 接线） | 百炼 v4 客户端、分批落盘/断点续跑/并发、Chroma 落库；`search_vector`/`search_hybrid`、三档融合；`TEXT_MODE` 部署级接线、缓存键含 mode（C3）、拒答按模式分档、`meta.text_mode` 真实反映 | 9,544 条 / dim 1024 / 955 批 / 365 s（并发 4）；一致性抽检重合率 **0.935**；`runs/v5_vec_fusion10` 四配置表（回答覆盖 **71.7% → 85.1%**，hybrid 文本召回 **97.0%**）；`runs/v5_vec_longrewrite`（AND 0% vs vector/hybrid 100%）；三档策略对照定档 **rrf** |
| **T1 F08 演示模式** | `gen_demo_examples.py`（评分过滤 + 能力标注 + **实测时延/截断筛题**）→ `demo_examples.json`（9 条）→ `GET /api/demo/examples` → 前端按类别分组 + 能力标签 + 实测时延；**去掉硬编码示例题** | 39 题 → 候选 18 → **实测筛出 9 条**（3 条截断、6 条超时延被剔除）；`smoke_deploy.py` **9/9 通过**（normal、非空、10 条引用），复跑命中缓存 29 ms |
| **T2 真实 LLM（主体）** | 密钥别名链（`RAG-command` 中转 / `RAG-deepseek-v4` 官方）、`LLM_MAX_TOKENS`、两端推理字段兼容（`reasoning`/`reasoning_content`）、队列桥接的**真 token 流式**、`thinking` 事件实际发射、缓存卫生（degraded 不入缓存）、`finish_reason=length` 截断告警 | 端到端：535–1,194 个 `answer` 增量 / 1,194–1,536 个 `thinking` 增量；不可达 endpoint → `degraded`+`heuristic-offline` 且**不写缓存** |
| **T4 部署（同源托管）** | `FRONTEND_DIST` + 路由注册后挂载 dist + 启动自检；`smoke_deploy.py` 6 步冒烟；`docs/deploy.md`（含"冒烟即预热"用法与演示预期表现）；限流复核 | `GET /` → HTML（909 B）；冒烟第 29 次请求触发 `rate limited`；部署手册覆盖从零构建到故障排查 |
| **T6 评测对照（部分）** | 新增 `vector`/`hybrid` 预设；修掉"报告层白名单静默丢弃新配置"；`fusion_limit` 改为跟随部署配置（0 = 取 settings） | `evaluation/chain.py`、`evaluation/report.py` |
| **既有缺陷修复** | `--suites` 逐字符解析、`np.save` 自动补 `.npy`、报告配置白名单、冒烟脚本误把 `stage` 当 `data.stage` | 用例 **50 → 70 全过** |
| **T3 收尾补齐**（2026-09-14 复查） | `build.py` 向量接线（`_build_vectors`：有密钥 → 嵌入 + Chroma；无密钥 → 空占位不中断）；`--rebuild-chroma`（从审计副本重建，不调云端，与 `--vectors-only` 互斥）；`requirements.txt` 补 `chromadb>=1.3`；`demo_mode` 按 §4.7 结论②删除；`server/query`、`server/text`、`scripts`、根 `README.md` 四处 README 同步 | 真索引 `--rebuild-chroma` 实测 **9544 条 / 10.6 s**，重建后一致性抽检 **0.932**（下限 0.9） |
| **同名实体朝代消歧**（2026-09-14 复查） | 见 §4.12：F02 问句朝代参与同名候选选择（偏好不硬过滤）；`dynasty_disambiguated` 进契约 + entities 事件 + 评测 trace | 实测"西汉的井陉之战"→ `event_0219`、"唐朝的潼关之战"→ `event_0612`；对 9 演示题 + 28 评测题影响 0；用例 **113 → 134 全过** |
| **地点坐标获取**（2026-09-15） | 见 §4.13：复用旧项目 geocoding 工具链 + 新增 `scripts/fetch_place_coords.py`（断点续跑、配额保护、按（地名+省）去重、按地址线索排序） | **3,193/3,595 目标成功（89%）**，覆盖 **4,819/5,316 行（91%）**；演示 9 个地点全部取到坐标 |
| **坐标接入 + 前端地图**（2026-09-15） | 见 §4.14：写回旧库（4,819 行）→ 重导出快照 `20260915_v1` → 索引复用旧向量（chunk 文本逐条一致）→ 前端 `MapView.vue`（echarts + 省级底图）；面板选点规则重写（同名聚类 + 优先带地址线索的簇） | 一致性 **0.932**（题库查询）；`panel.map_points` 对"赤壁之战"出 **夏口/赤壁/江陵** 三点；冒烟 **9/9 通过**（缓存命中 23 ms）；用例 **134 → 140 全过** |

**证据裁剪实验（18 → 10 条送模证据）**：`QUERY_FUSION_LIMIT` 做成可配（默认 18 保持 v4 口径，`.env` 设 10）。
实测对比：`v5_vec_compare`（18 条）vs `v5_vec_fusion10`（10 条）——**回答覆盖完全不变**
（vector/hybrid 均 85.1%），hybrid 文本召回还从 95.2% 升到 **97.0%**；而演示可用题从 **5 条升到 9 条**
（截断告警 13 次 → 3 次，首正文 4.4–8.8 s → 3.8–7.7 s）。结论：**证据裁剪是"降本提观感"的关键旋钮**，
后续若仍见截断，先降到 8 再观察。

### 11.2 进行中 / 部分完成

| 项 | 缺口 |
| --- | --- |
| 评分口径 | ✅ 已定（2026-09-14 用户确认）：**由 AI 代理按 `evaluation/grading.py` 口径评估即为对外口径**，不做人工复核；评分表 `runs/v5_llm_3/scores.jsonl`（25 correct / 3 partial / 0 incorrect），报告含逐条失败清单供随时回看 |
| 演示时延波动 | 同一题三次实测差异大（首正文 3.96 s ↔ 18.07 s，取决于中转负载与推理长度）；缓解：演示前跑冒烟预热缓存（副本同题 <100 ms） |
| 截断的可观测性 | 截断只在日志告警（三次取样分别 5/3/2 次），**未进 trace**；已加"空正文兜底"，但"截断到半句"仍可能出现 |

### 11.3 未开工（可选项，需用户决定）

- **可选治理项**（规划 §2.5-4/5）：1,215 组同名实体人工回填（已改为代码侧朝代消歧，见 §4.12）、
  R1 方案①（朝代偏置折进文本 `_score`）；~~地点坐标补录~~ ✅ 已完成（§4.13/§4.14）。
- **坐标剩余项**：402 个无地址线索的古地名未取到坐标（占目标 11%）；如需提高覆盖率，
  给旧项目 698 条映射表补条目后 `--retry-failed` 重跑即可。

> T7 文档收口已于 2026-09-13 完成：`docs/README.md` 与 `features/04|06|08|11` 状态行已同步、
> `changes/20260913-ragv5-summary.md` 与 `RAGv5-阶段工作总结.md` 已写、
> `data-contract.md` 已补 `text_results.mode` / `thinking` / `entities.llm_entity_used` / 筛选语义。

### 11.4 后续建议

1. 评分口径已确认（AI 代理评估，2026-09-14），无需人工复核；
2. 演示前固化流程：`smoke_deploy.py` 预热 → 记录当次延迟 → 演示（写入 `docs/deploy.md`）；
3. 可选治理项按用户决定是否排期；
4. 阶段收口提交：新建 `ragv5-*` 分支 → 提交并推送 GitHub。**前置已就绪**：`.gitignore` 已修
   （2026-09-14）——`data/snapshot/`、`data/index/` 改为"忽略产物、保留源码与说明"，此前**从未入库**的
   16 个 `.py`（含 `embeddings.py`、`vector_pipeline.py`、`chroma_store.py`、`chunking.py`）现可见；
   提交切分与安全检查见 [RAGv5-遗留项整改方案.md](RAGv5-遗留项整改方案.md) §A2–A4。

---

## 附录 A：命令速查

```bash
# 环境（示例）
E:/anaconda/envs/AI_Agent/python.exe -m pytest tests -q
python scripts/run_evaluation.py check-bank

# 索引：先小样（验证维度与观感），再全量（百炼 v4 嵌入 + 写 Chroma，约 955 次请求）
python scripts/build_index.py --vectors-only --sample 8
python scripts/build_index.py --vectors-only
python scripts/check_vector_consistency.py --sample 20      # Chroma vs 暴力余弦一致性抽检

# 服务与冒烟
python scripts/run_server.py --host 0.0.0.0 --port 8000
python scripts/smoke_deploy.py --examples data/eval/20260904_v2/demo_examples.json
curl -N -X POST http://127.0.0.1:8000/api/query -H "Content-Type: application/json" \
  -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'

# 评测
python scripts/run_evaluation.py run --suites main --configs dual,text-only
python scripts/run_evaluation.py run --suites main --configs text-only,vector,hybrid
python scripts/run_evaluation.py run --suites main --llm --run-id v5_llm_1
python scripts/run_evaluation.py report

# 分块参数实验（T8，先于全量建向量）
python scripts/compare_chunking.py --sizes 500/100,800/80,1200/120 --configs dual,text-only

# 前端
cd frontend && npm run build
```

## 附录 B：环境变量清单（v5 视角）

| 变量 | 现状 | v5 动作 |
| --- | --- | --- |
| `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TIMEOUT_SECONDS / LLM_MAX_RETRIES` | 配置项已有；预填 `https://api.deepseek.com/v1` + `deepseek-v4-flash`（都不是当前要用的值） | **项目期内**：`LLM_BASE_URL=https://api.commandcode.ai/provider/v1`、`LLM_MODEL=deepseek/deepseek-v4.1-flash`、密钥走 **`RAG-command`**；**项目结束后**切官方：`https://api.deepseek.com/v1` + `deepseek-flash` + `RAG-deepseek-v4`（删掉 `RAG-command` 即零代码回落） |
| 密钥别名链 | 无（原实现只读 `LLM_API_KEY`） | 新增优先级链 `LLM_API_KEY` → `DEEPSEEK_API_KEY` → **`RAG-command`**（中转，项目期优先）→ **`RAG-deepseek-v4`**（官方）；连字符名 Linux 不可用，别名是部署必需 |
| `LLM_MAX_TOKENS` | 无（`create()` 未传该参数） | 新增，默认 1024；推理模型设小会让正文为空（§4.11 实测） |
| `FALLBACK_LLM_*` | 已有（默认空 = 不降级） | **指向官方 endpoint**（`https://api.deepseek.com/v1` + `RAG-deepseek-v4` + `deepseek-flash`），让"中转挂了自动降级到官方"成为既有代码的自然行为 |
| `EMBEDDING_BASE_URL` | 定义存在但全仓无读取点 | T3 接入；填 `https://dashscope.aliyuncs.com/compatible-mode/v1`（阿里云百炼） |
| `EMBEDDING_API_KEY` / **`DASHSCOPE_API_KEY`** | 前者未配置；**后者已在系统环境变量中（2026-09-13 实测可用）** | 读取优先级 `EMBEDDING_API_KEY` → `DASHSCOPE_API_KEY`；**密钥不写进 `.env`**，只靠系统环境变量 |
| `EMBEDDING_MODEL` | 同上 | 填 `text-embedding-v4` |
| `EMBEDDING_DIM` | 同上（`.env.example` 只是示例 1024） | 定 **1024**（**已实测接口返回 1024**，与配置一致）；构建时仍断言实测维度 |
| `EMBEDDING_BATCH_SIZE` / `EMBEDDING_TIMEOUT_SECONDS` | 无 | 新增；batch 默认 **10**（百炼硬上限，不可配大） |
| `CHROMA_COLLECTION` | 无 | 新增（默认 `chunks_v1`；`hnsw:space=cosine` 在建库时锁定） |
| `TEXT_MODE` | **无** | 新增（默认 keyword） |
| `TEXT_HYBRID_STRATEGY` | 无 | 新增（`weighted`/`rrf`/`fallback`，默认 `weighted`；由评测定终值） |
| `TEXT_HYBRID_KEYWORD_WEIGHT` | 无 | 新增（默认 0.5） |
| `ENABLE_LLM_ENTITY_FALLBACK` / `LLM_ENTITY_TIMEOUT_SECONDS` | 无 | 新增（默认 false / 8s） |
| `FRONTEND_DIST` | 无 | 新增（默认 `frontend/dist`） |
| `DEMO_MODE` | 已删除（2026-09-14，§4.7 结论②） | 示例区恒显示，不再有该开关 |
| `RATE_LIMIT_PER_MINUTE / CACHE_TTL_SECONDS / HISTORY_MAX_TURNS / QUERY_TOP_K_*` | 已有且在用 | 按演示负载复核（§2.4-3） |
| `INDEX_BUILD_EMBEDDINGS` | 已有 | 语义改为"是否调用云端向量模型"，与 `--no-embeddings` 对齐 |
| `CHUNK_MAX_CHARS / CHUNK_OVERLAP_CHARS` | 已有（800 / 80） | 由 T8 实验决定是否调整 |

## 附录 C：关键行号索引（开工前基线，改动前复查）

| 关注点 | 位置 |
| --- | --- |
| F02 兜底预留属性 / 主流程 | `server/query/understand.py:40-45` / `:98-187` |
| 模式判定与降级 | `server/text/scoring.py:19-26` |
| 向量可用性探测 | `server/text/searcher.py:45-60` |
| 关键词检索与归一化 | `server/text/searcher.py:62-119`、`:121-145` |
| 元数据筛选（event_type 剔除点） | `server/text/searcher.py:161-172` |
| F04 三分支入口 | `server/text/__init__.py:47-56` |
| F05 加权排序与名额分配 | `server/fusion/fusion.py:98-125` |
| 写死 mode / 空回调 / 无条件下缓存 | `server/sse.py:160-163` / `:227` / `:241` |
| 拒答硬规则 | `server/sse.py:189-216`（副本 `evaluation/chain.py:230-237`） |
| 假流式切句 | `server/sse.py:44-63`、`:246-249` |
| 缓存键与 TTL | `server/generate/cache.py:24-35`、`:41-55` |
| LLM 流式与降级链 | `server/generate/llm_client.py:52-105` |
| 生成主流程 | `server/generate/__init__.py:45-82` |
| **分块规则（段落/句子/重叠/硬切）** | `data/index/chunking.py:20-52`、`:55-121`、`:137-162` |
| **切分参数默认值** | `config/defaults.py`（`CHUNK_MAX_CHARS=800` / `CHUNK_OVERLAP_CHARS=80`） |
| **关键词索引（jieba + FTS5）** | `data/index/fts.py:34-48`（分词与词典）、`:51-96`（建库）、`:99-`（查询） |
| 向量构建（无调用点） | `data/index/vectors.py:31-51` |
| 索引编排（无条件占位） | `data/index/build.py:40-41`、`:59-63` |
| 片段统计基线（供 T8 对照） | `data/index/20260904_v2/manifest.json`（9,544 片段）、`build_report.json` |
| 硬编码示例题 | `frontend/src/components/chat/ChatPane.vue:11-16` |
| 评测配置与预设 | `evaluation/chain.py:25-64`、`:187-191` |
| 评测 CLI | `evaluation/cli.py:334-356` |
| 限流与路由 | `server/api.py:116-131`、`:105/141/153` |
| 启动加载与 meta | `server/runtime.py:74-113` |
