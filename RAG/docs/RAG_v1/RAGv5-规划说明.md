# RAGv5 规划说明：F08 演示模式 + 真实模型/向量接入与部署打磨

- 文档类型：阶段需求与开发规划（**开工前**；实现完成后另写 `RAGv5-开发说明.md`）
- 状态：⬜ 规划中（待用户确认密钥来源、部署目标与待确认项后开工）
- 日期：2026-09-13
- 范围：`docs/README.md` 建议开发顺序第 4、5 条 —— 演示稳定性（模型降级/缓存/限流/部署）
  与暂缓的 F08 演示模式；即 [后续阶段规划.md](后续阶段规划.md) 第三节定义的任务，
  叠加 v4 已量化、留待本阶段处理的质量优化项
- 前置：v4 收口（题库 `reviewed-2`、当前基线 `run_20260913_postaudit`、审核报告
  [RAGv4-阶段审核报告.md](RAGv4-阶段审核报告.md) 四轮（T1–T8 / R1–R7 / A1–A6 / B1–B5）整改完毕）
- 需求来源：`docs/features/08-demo-mode.md`（F08）、`docs/features/06-grounded-answer.md`（F06）、
  `docs/features/04-text-retrieval.md`（F04）、`docs/features/11-text-indexing.md`（F11）、
  [后续阶段规划.md](后续阶段规划.md) §三、审核报告 §十八（v4 边界）与 §二十（现成输入）

## 一、目标

v4 交付的是"可评测、可追溯"的链路，但三处仍是**离线/占位态**：F06 走离线摘要回答器
（`llm_used=false`）、F04 只有关键词通道（`embeddings.npy` 为 (0,0) 占位）、F08 演示模式未做
（前端仅有 4 条硬编码示例题 `frontend/src/components/chat/ChatPane.vue:11`，其中
"牧野之战与武王伐纣有什么关系？"与"井陉之战发生在什么时候？"两条**均不在已审核题库**
（题库 39 条中没有"牧野/井陉"相关题目），稳定性无保障）。

v5 目标：把链路切到**真实模型 + 向量检索**，交付**面向评审/访客的演示入口**，并把部署形态
打磨到可稳定演示；同时处理 v4 用评测量化出的质量短板。

## 二、需求与验收标准

### 2.1 F08 演示模式（功能文档三条验收）

| # | 验收标准（来自 [F08](../features/08-demo-mode.md)） | 验证方式 |
| --- | --- | --- |
| 1 | 页面打开即可提问，不需要输入账号 | 无登录壳/路由守卫；`/api/health` 可用即进入 |
| 2 | 示例问题点击后能稳定返回完整回答 | 每个示例题连续跑 3 次（含缓存命中路径）均 `done` 且回答非空 |
| 3 | 示例问题覆盖图谱检索与文本检索两类能力 | 示例题清单里标注能力类型，逐条给出 graph/text 证据存在的冒烟记录 |

增补验收（本规划新增，源自 v4 教训）：

4. **示例题必须来自已审核题库**：`data/eval/20260904_v2/questions.jsonl` 中
   `reviewed=True` 且人工评分非 `incorrect` 的题；前端不再硬编码未审核问题；
5. **演示默认低成本可运行**：无 LLM key 时仍完整演示（离线回答器）；配 key 后自动流式；
6. 示例题显示能力分类标签（关系型 / 背景型 / 时间线型 / 实体介绍等，取自题库 `category`）。

### 2.2 真实 LLM 接入（F06 落地）

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | `.env` 配 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 后 F06 自动切真实流式（token 级增量） | 同一问题回答为自然语言成文且 SSE `answer` 为多次增量 |
| 2 | 超时/重试/备用模型降级链保持有效 | 断网/错 key 时 `finish_reason=degraded` 且回答仍产出 |
| 3 | **实现** F02 的 LLM 兜底（当前仅为预留接口：`understand.py` 存了 `llm_client/enable_llm` 但主流程无 LLM 分支，见审核报告 §28.3 C1）：词典完全未命中 → LLM 实体识别/类型判定 + 失败降级 | 构造词典未命中的自然问句，`entities` 非空；断网/错 key 时降级回词典路径且不报错；补用例 |
| 4 | **对照评测**：同题库重跑 `llm_used=true`，与基线 `run_20260913_postaudit` 对比 | 输出对照报告：答案正确性分布必须优于 0 correct/23 partial/16 incorrect；重点复测 §五 的 14 条"检索到位、回答未用"题 |

### 2.3 向量检索启用（F04/F11 收尾）

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | 接入云端中文 embedding（`EMBEDDING_*`），`scripts/build_index.py` 全量重建索引 | `vectors/embeddings.npy` 形状 `(N, dim>0)` 且 `len(ids)==N`；manifest `vectors.mode != placeholder` |
| 2 | 实现 F04 的 `vector` / `hybrid` 分支（当前 `server/text/__init__.py` 中 vector 分支返回空） | 切 `mode=vector` 能返回结果；hybrid 融合关键词+向量 |
| 3 | 加载校验与自动降级保持（索引损坏/缺失 → 关键词模式） | 人为破坏 ids 后启动，`vector_available=false` 且不报错 |
| 4 | **对照评测**：建议配置 `text-only（关键词，and_or）/ text-only-and / vector / hybrid` 同题库对比（`EvalConfig` 已支持 `mode` 字段 keyword/vector/hybrid，2026-09-13 第四轮 B2 补齐；vector/hybrid 生效依赖本节 1–2 条落地） | 报告含各配置的文本 top-k 召回与回答覆盖对比；特别关注 v4 暴露的长改写问题（AND 失效）是否被向量通道弥补 |

### 2.4 部署形态

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | 前端 `npm run build` 产物可静态托管或由后端同源托管 | 单进程启动后浏览器直接访问页面并完成一次问答 |
| 2 | `/api/health` 与两条 SSE 路径冒烟回归（全量检索 / 缓存命中） | 冒烟脚本一次跑通并记录日志 |
| 3 | 限流（`RATE_LIMIT_PER_MINUTE`，默认 30/min，`api.py` 已实现滑动窗口）与缓存 TTL 按演示负载复核 | 按默认 30/min 构造超限请求触发限流且前端有可读提示 |
| 4 | 部署说明文档（端口、环境变量、数据目录、开机步骤） | 照文档从零启动成功 |

### 2.5 质量优化项（v4 已量化，本阶段处理）

| # | 问题（v4 证据） | v5 要求 |
| --- | --- | --- |
| 1 | 拒答规则只覆盖"无证据/无共享词"，X01–X03 应拒答却作答 | 补规则（属性/信息不存在类问题），3 题转为正确拒答（`unknown_answer`） |
| 2 | F04 长改写 AND 优先必然失效（L01–L04 纯 AND 召回 0%、全拒答） | 给出兜底策略并评测验证（如改写失败→放宽取词/向量通道承接） |
| 3 | `event_type` 筛选把 raw/evidence 整批剔除（F01 文本召回 100%→0%） | 为 raw/evidence 补元数据或放宽筛选项，专项指标回升 |
| 4 | 可选治理增强：为 1,215 组同名实体做人工审核回填（产出 `audit_decisions.json` 交 `scripts/apply_audit.py --decisions` 生成新快照）；地点坐标覆盖率 0 | 由用户决定是否做（坐标涉及素材与授权，见第八节） |
| 5 | 可选：R1 方案①（朝代偏置折进文本 `_score`） | 如启用须做成默认关闭开关，并重跑基线复核 B03/L03 |

## 三、任务分解与改动点（文件级）

| 任务 | 主要改动点 |
| --- | --- |
| T1 F08 演示模式 | `frontend/src/components/chat/ChatPane.vue`（示例题改为按能力分组渲染）、新增 `frontend/src/api/demo.ts`（或并入 `http.ts`）读示例题、`server/api.py` 增 `GET /api/demo/examples`（或构建期从题库生成静态 JSON）、`config` 的 `demo_mode` **新建行为**（当前仅配置定义，`server/` 与 `frontend/` 均未使用） |
| T2 真实 LLM | `.env`/`config/settings.py`（已具备）、**`server/query/understand.py`（新实现 LLM 兜底分支 + 失败降级 + 用例，当前无实现）**、`server/runtime.py`（传 llm_client/开关）、`server/generate/llm_client.py`（token 回调已预留，需接上）、`server/sse.py`（`on_delta` 接 SSE 帧，替换 `_chunk_answer_stream` 假流式） |
| T3 向量检索 | `data/index/vectors.py`（已具备 encode 路径，需确认模型/维度）、`scripts/build_index.py`（默认即构建向量，仅无密钥时才加 `--no-embeddings`）、`server/text/searcher.py`（补向量检索与 hybrid 打分）、`server/text/__init__.py`（vector/hybrid 分支实现）、`server/text/scoring.py`（模式判定）。**生产侧接线（审核 C2，缺了等于线上不生效）**：`config/settings.py` + `.env.example` 增文本模式开关（如 `TEXT_MODE`，默认 keyword）、`server/sse.py` 按配置传 `mode`（当前写死 keyword）、`server/runtime.py` 的 `meta["text_mode"]` 反映真实模式；**缓存键**：`server/generate/cache.py::cache_key` 纳入 mode（审核 C3；按请求可切时必须加，部署级全局开关则需在文档写明前提），可照 `dynasty_bias` 进入缓存键的先例 |
| T4 部署 | `scripts/run_server.py`、可选 `scripts/smoke_deploy.py`、前端 `vite.config.ts`（生产 API 基址）、部署说明文档 |
| T5 质量优化 | `server/generate/refusal.py`（拒答规则）、`server/sse.py`（两条拒答硬规则扩展）、`server/text/searcher.py`（AND 兜底/取词放宽）、`data/snapshot/governance.py` + `scripts/build_index.py`（raw/evidence 元数据） |
| T6 评测对照 | `evaluation/`（口径不变；`EvalConfig` 已含 `mode` 与 `graph_top_k`，可直接跑关键词/向量对照；`run --llm` 跑真实模型对照）、新增对照报告模板/章节 |

## 四、前置条件与待确认事项

1. **真实 LLM 与 embedding 密钥来源**（base_url / model / 预算与限流）；
2. **embedding 模型与维度**（决定 `EMBEDDING_DIM` 与索引体积；中文模型选型）；
3. **部署目标**：前端静态托管 vs 与后端同源托管；是否有容器/CI 需求；
4. **F08 示例题最终组数**与是否需要"评委模式"面板（F08 功能文档的两个待确认项）；
5. **向量库与 RAG 框架取舍**决策留痕：继续用自研 FTS5+NumPy（轻量、可控）还是引入
   faiss/qdrant 等（数据量 ~9.5k 片段，当前评估：自研足够，除非要做大规模扩展）；
6. v4 的 Git 固化（审核报告 §十九建议先提交 v4 再开 v5）。

## 五、数据与评测口径继承（v4 资产直接复用）

- **题库**：`data/eval/20260904_v2/questions.jsonl`（`reviewed-2`，39/39 通过；main 28 /
  long_rewrite 4 / filter_loss 4 / refusal 3）；
- **对照基线**：`data/eval/20260904_v2/runs/run_20260913_postaudit`（87 条 trace + 报告 +
  39 条评分）；v5 的效果对比一律以它为"无 LLM / 关键词通道"参照；
- **当前基线数据**（供 v5 对照，来自 v4 报告）：

  | 维度 | dual | text-only |
  | --- | --- | --- |
  | 实体命中 | 42.6% | 42.6% |
  | 图谱命中 | 85.4% | - |
  | 文本 top-k 召回 | 94.6% | 94.6% |
  | 融合文本携带 | 86.3% | 94.6% |
  | 引用携带(文本) | 63.7% | 66.7% |
  | 回答覆盖 | 67.6% | 71.7% |
  | ok/n | 28/28 | 28/28 |

  评分：答案 0 correct / 23 partial / 16 incorrect；引用 28 supported / 3 unrelated /
  8 unsupported；
- **复评重点样本**：受托审核报告（`data/eval/20260904_v2/review/review_report.md`）§4.3(4) 的 14 条"检索到位、回答未用"题 ——
  `M01、M03、M06、M07、M08、M11、M12、R01、R03、L01、L04、B03、F03、F04`：
  接入真实 LLM 后应重点看是否转为 `correct`；
- **F08 示例题选型规则**：从 `reviewed=True` 且评分非 `incorrect` 的题中选并覆盖
  图谱/文本两类能力。当前 `incorrect` 全集 16 条（选型时统一按"评分非 incorrect"过滤）：
  `B02、E01、F01、L01、L02、M03、M07、R02、R03、R04、R05、T01、T03、X01、X02、X03`
  （注意 `F02` 是 `partial` 而非 incorrect，勿误列）；
- **评分口径**：`evaluation/grading.py` 与 [RAGv4-开发说明](RAGv4-开发说明.md) 附录一；
  人工评分不得用模型评分替代。

## 六、风险与对策

| 风险 | 对策 |
| --- | --- |
| LLM 输出随机 → 评测不可复现 | 对照评测固定模型版本与温度、先跑一轮缓存（或禁用缓存）；报告注明随机性 |
| 密钥成本/限流 | 演示默认低频；`.env` 单独配置演示用 key；限流按演示负载复核 |
| embedding 全量重建耗时/费用 | 分批编码 + 断点续跑；先小样验证维度与质量再全量 |
| 向量索引体积与启动时间 | 9.5k 片段规模可接受；必要时降维/量化 |
| 演示现场不稳定（无网/超时） | 降级链已就位（离线回答器/关键词模式）；演示前跑冒烟脚本 |
| F08 示例题被系统缺陷影响 | 选型规则从"评分非 incorrect 的已审核题"中选；避开 F02 类缺陷问法 |
| hybrid 分数量纲不一致（审核 R1） | 关键词分为 min-max 后的 bm25 反值、向量分为余弦相似度，而 F05 还按问题类型权重重排；须先定义"向量分归一化 + hybrid 融合口径（含与 F05 权重衔接）"再实现，否则排序不可解释、连带影响 citation 与评分。T3 已列该条并纳入 §2.3-4 对照验收 |
| 真实 LLM 评测随机性（审核 R2） | 固定模型版本与温度、注明随机性；同一配置 **2–3 次取样取分布**（或明确接受单次并标注置信边界），避免用单次运行下"必须优于基线"的结论 |

## 七、里程碑与建议顺序

1. **M0**：v4 Git 固化（提交 + 分支），确认 v5 待确认项（第四节）；
2. **M1 向量线**：embedding 接入 → 索引重建 → F04 向量/hybrid 实现 → 四配置对照评测；
3. **M2 LLM 线（可与 M1 并行）**：真实 LLM 接入 → F02 兜底开关 → 同题库对照评测 →
   重点复测 14 条样本；
4. **M3 F08**：从已通过题库选题 → 后端示例接口（或构建期生成）→ 前端分类标签与一键提问 →
   三条验收 + 增补验收冒烟；
5. **M4 部署**：同源/静态托管、冒烟脚本、限流缓存复核、部署说明；
6. **M5 质量优化与收口**：拒答补强、AND 兜底、筛选元数据 → 重跑对照报告 →
   写 `RAGv5-开发说明.md` 并同步索引与功能状态。

## 八、边界与不做

1. 不做多用户/账号/权限体系（演示无需登录）；
2. 不做历史地图底图素材（涉及素材与授权，另行评估；当前地点仍为列表降级）；
3. 不用模型评分替代人工评分（F10 口径不变）；
4. 不修改旧 `backend/`、旧 `frontend/`、`entity-event-relation/` 代码（项目级约定）；
5. 不在本阶段扩大题库规模（如确需扩题，按 v4 的 `REVIEW_GUIDE.md` 走一轮审核）。

## 九、本阶段产物清单（预期）

| 产物 | 路径 |
| --- | --- |
| 开发说明（实现后） | `docs/RAG_v5/RAGv5-开发说明.md`（或 `docs/RAG_v1/RAGv5-开发说明.md`，沿用现有约定） |
| 对照评测报告 | `data/eval/<v>/runs/<llm_run>/report.md` + 与基线的对照小节 |
| 示例题清单（可追溯） | 由题库过滤生成（`reviewed=True` 且评分非 incorrect），必要时落 `data/eval/<v>/demo_examples.json` |
| 部署说明 | `docs/deploy.md` 或在 `README.md` 增部署章节 |
| 变更总结 | `docs/changes/<date>-ragv5-summary.md` |
