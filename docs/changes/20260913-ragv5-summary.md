# RAGv5 变更总结：F08 演示模式 + 真实模型/向量接入 + 部署打磨（2026-09-13）

- 日期：2026-09-13
- 阶段：RAGv5（F08 演示模式 + 真实 LLM/向量接入 + 部署 + v4 遗留质量优化）
- 状态：**功能与部署已完成**；回答正确性的 LLM 对照评测与人工评分待补（见第五节）
- 需求与验收：`docs/RAG_v1/RAGv5-规划说明.md`；实现与实测：`docs/RAG_v1/RAGv5-开发说明.md`
  （§九 验收矩阵、§十一 进度快照）；部署：`docs/deploy.md`

## 一、新增内容

### 代码（新增）

- `data/index/embeddings.py`：百炼 `text-embedding-v4` 客户端（batch ≤10、重试、维度断言）；
- `data/index/vector_pipeline.py`：向量构建流水线（分批落盘 + **断点续跑** + 并发批次 +
  拼 `embeddings.npy` 审计副本 + 写 Chroma + 更新 manifest）；
- `data/index/chroma_store.py`：Chroma 持久化封装（`hnsw:space=cosine`、禁用默认 EF、
  元数据标量约束、`count()` 一致性）；
- `scripts/gen_demo_examples.py`：F08 示例题生成（已审核题库过滤 + 能力标注 + **实测时延/截断筛题**）；
- `scripts/gen_demo_examples.py` → `data/eval/20260904_v2/demo_examples.json`（9 条，入 Git 可复核）；
- `scripts/smoke_deploy.py`：部署冒烟 6 步（health / 页面 / 示例题接口 / 逐条示例题 / 缓存命中 / 限流），
  **兼作演示前缓存预热**；
- `scripts/check_vector_consistency.py`：Chroma 与暴力余弦的条数/近似召回一致性抽检；
- `scripts/compare_chunking.py`：分块参数对比实验（索引变体 + 离线评测 + 报告）；
- `tests/`：`test_hybrid_scoring.py`、`test_llm_client_fields.py`、`test_cli_args.py`、
  `test_t5_quality.py`、`test_vector_degradation.py`、`test_sse_chunking.py`；
- 前端：`src/api/demo.ts`、`DemoExample` 类型、`ChatPane.vue` 分组渲染（**移除硬编码示例题**）。

### 代码（修改要点）

- `server/text/searcher.py`：`search_vector`（Chroma 余弦，`distance→相似度`换算）、
  `search_hybrid`（三档融合）、`_where_of`、AND 兜底（命中不足并入 OR）、
  `_pass_meta` 的 event_type 语义调整（有元数据严格、原文片段放行）；
- `server/text/scoring.py`：`fuse_weighted` / `fuse_rrf` / `fuse_hybrid`；
- `server/sse.py`：**队列桥接的真实 token 流式**、`thinking` 事件实际发射、缓存卫生
  （degraded 不入缓存）、拒答第三条规则（领域外谓词）、模式按配置传递；
  修复 `_chunk_answer_stream` 丢换行（增量拼接严格等于原文）；
- `server/generate/llm_client.py`：`max_tokens`、两端推理字段兼容
  （`reasoning`/`reasoning_content`）、`stream_options.include_usage`、`finish_reason=length` 告警；
- `server/generate/refusal.py`：领域外谓词词表与保守判定（X01–X03 补强）；
- `config/settings.py` + `defaults.py` + `.env.example`：密钥别名链、`LLM_MAX_TOKENS`、
  `TEXT_MODE`/`TEXT_HYBRID_*`、`QUERY_FUSION_LIMIT`、`CHROMA_COLLECTION`、`FRONTEND_DIST` 等；
- `data/index/chunking.py`：关系证据按事件卡片补齐 `event_type`（覆盖 7503/7503）；
- `server/api.py`：`GET /api/demo/examples` + 同源托管静态挂载（路由注册之后）；
- `server/runtime.py`：`_resolve_index` 支持索引变体、注入 embed_fn、`meta.text_mode` 真实反映；
- `evaluation/`：新增 `vector`/`hybrid` 预设、`fusion_limit` 跟随部署配置、
  修复报告层白名单静默丢弃新配置、run 元数据记录 `index_version`/`chunk_params`；
- `scripts/build_index.py`：`--vectors-only`/`--sample`/`--no-resume`/`--concurrency`/`--index-suffix`；
- `scripts/run_server.py`：启动自检（版本/模式/LLM/前端产物）。

## 二、文档变更

- 新增 `docs/RAG_v1/RAGv5-开发说明.md`（设计 → 实测回填 + §十一 进度快照）、`docs/deploy.md`（部署手册）；
- 新增 `docs/RAG_v1/RAGv5-规划说明.md` 第四版修订（实施期决策 D5–D9、里程碑 M0–M4 完成）；
- 同步：`docs/README.md`（F04/F06/F08/F11 状态 + RAGv5 行）、`docs/features/04|06|08|11`、
  `docs/RAG_v1/README.md`、`docs/data-contract.md`（`text_results.mode`、`thinking`、筛选语义）；
- `docs/RAG_v1/后续阶段规划.md`：向量库选型决策追加 2026-09-13 修订（Chroma）。

## 三、关键结果（实测）

| 项 | 结果 |
| --- | --- |
| 向量索引 | 9,544 条 / 1024 维 / 955 批 / 365 s（并发 4）；`chroma.count()==ids==npy==9544` |
| 一致性抽检 | Chroma top-20 与暴力余弦 top-20 重合率 **均值 0.935**（最低 0.800） |
| 四配置对照（离线回答器，main 28 题） | 回答覆盖：关键词 71.7% → **vector/hybrid 85.1%**；文本召回 hybrid **97.0%** |
| 长改写专项 | 纯 AND 召回 0%；`vector`/`hybrid` **100%** → v4 暴露的 AND 失效被向量通道弥补 |
| 融合档位 | `rrf` 定档（文本召回 97.0% > weighted 95.2% > fallback 94.6%） |
| 证据裁剪 | `QUERY_FUSION_LIMIT` 18 → 10：回答覆盖不变，hybrid 召回 95.2%→97.0%，示例题可用数 5 → 9 |
| 真流式 | 535–1,194 个 `answer` 增量 + 1,194–1,536 个 `thinking` 增量；首正文 2–14 s（中位 4–7 s） |
| T5 质量优化 | 拒答 X01–X03 → `correct_refusal`；text-only 召回 94.6→95.8%、覆盖 71.7→74.1%；F01 筛选后 0%→100% |
| F08 + 部署 | 示例题 9 条（实测筛题）；冒烟 9/9 通过、缓存命中 29 ms；无密钥形态冒烟通过；限流第 29 次触发 |
| 回归 | `pytest` **91 passed**（v4 收口时 50） |

## 四、边界与后续

- **不在本阶段**：飞书/Hermes 渠道化、独立静态托管、多用户/权限、历史地图底图素材、
  题库扩容（见规划说明第八节）。
- **待补**：`--llm` 对照评测（2–3 次取样）与 14 条"检索到位、回答未用"重点题的人工评分；
  F02 的 LLM 兜底（`understand.py` 仍为预留）；可选项（1,215 组同名实体回填、地点坐标）。
- **已知限制**：中转 endpoint 的时延波动较大（同题首正文 3.96 s ↔ 18.07 s）；
  `max_tokens` 仍可能触顶（有告警）；回答缓存是进程内的（重启即空）。

## 五、回归与验证（复现命令）

```bash
python -m pytest tests -q                                   # 91 passed
python scripts/check_vector_consistency.py --sample 20      # 条数一致 + 重合率 ≥0.9
python scripts/run_evaluation.py run --suites main --configs text-only,text-only-and,vector,hybrid
python scripts/run_evaluation.py run --suites refusal,filter_loss --configs dual,text-only
python scripts/gen_demo_examples.py --measure --max-examples 12   # 重新筛示例题
python scripts/smoke_deploy.py --base http://127.0.0.1:8000       # 演示前冒烟（兼预热）
```
