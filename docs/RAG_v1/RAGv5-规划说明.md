# RAGv5 规划说明：F08 演示模式 + 真实模型/向量接入与部署打磨

- 文档类型：阶段**需求与验收口径**（开工前）。实现设计、文件级改动、实施步骤与部署手册见
  [RAGv5-开发说明.md](RAGv5-开发说明.md)（同目录，已先行产出"开工前设计版"）
- 状态：✅ **已完成**（M0–M5 全部收口，2026-09-14 复查补齐 T3 收尾项与文档同步）。
  §4.1 选型已定、§4.2 待确认项已逐条落地（第 2/4 项结论见开发说明 §4.7 与遗留项整改方案）；
  规划已过两轮第三方审核，B1–B5 与 C1–C3 / R1–R2 已全部纳入本文）
- 日期：2026-09-13（第四版）
  - 修订记录（第四版）：⑥ 记录实施结果与实施期决策——D7 演示现场有外网、D8 同源托管、
    D9 送模证据条数=10（实测定）、D5 融合档位定 `rrf`、D6 补齐 LLM 两端字段差异与实测；
    ⑦ 里程碑 M0–M4 标记完成，验收条目按实测证据标记 ✅/◐（明细见开发说明 §九、§十一）
  - 修订记录（第二版）：① 补入审核报告 §28.3 的 C1–C3（F02 LLM 兜底未实现、生产侧文本模式无接线点、
    缓存键未含模式）与 §28.4 的 R1–R2（hybrid 分数量纲、真实 LLM 评测随机性）；
    ② 按**开工前代码逐条核对**修正 6 处与代码不符的表述（勘查结论见第十节，明细见开发说明 §二）；
    ③ 新增 §2.5-6 契约同步项；④ §三 拆出 T7 文档与契约同步
  - 修订记录（第三版）：⑤ **补入此前缺失的离线链路口径**——数据清洗（F09）与数据分块（F11）
    是 RAGv1 既成交付，向量化与向量存储此前**未实现**；⑥ 依用户 2026-09-13 决策，
    确定向量模型（阿里云百炼 `text-embedding-v4`，1024 维）与向量库（**Chroma**，
    替代原"自研 FTS5+NumPy 足够"的暂定结论）；⑦ 新增 §2.5-7 分块参数对比实验（T8，用户确认执行）；
    ⑧ hybrid 融合口径不再要求凭空拍板，改为"默认线性加权 + 档位可切 + 由对照评测决定"（开发说明 §四.2）
- 范围：`docs/README.md` 建议开发顺序第 4、5 条 —— 演示稳定性（模型降级/缓存/限流/部署）
  与暂缓的 F08 演示模式；即 [后续阶段规划.md](后续阶段规划.md) 第三节定义的任务，
  叠加 v4 已量化、留待本阶段处理的质量优化项
- 前置：v4 收口已完成 —— 题库 `reviewed-2`（39/39）、当前基线 `run_20260913_postaudit`、
  审核报告 [RAGv4-阶段审核报告.md](RAGv4-阶段审核报告.md) 五轮（T1–T8 / R1–R7 / A1–A6 /
  B1–B5 / C1'–C3、R1–R2）整改与验收完毕，v4 已提交 Git（`50aafa2`）
- 需求来源：`docs/features/08-demo-mode.md`（F08）、`docs/features/06-grounded-answer.md`（F06）、
  `docs/features/04-text-retrieval.md`（F04）、`docs/features/11-text-indexing.md`（F11）、
  `docs/features/10-evaluation.md`（F10）、[后续阶段规划.md](后续阶段规划.md) §三、
  审核报告 §十八（v4 边界）、§二十（现成输入）、§28.3（任务缺口）、§28.4（技术风险）

## 一、目标

v4 交付的是"可评测、可追溯"的链路，但三处仍是**离线/占位态**，另一处是**未开工**：

| # | 现状 | 证据 |
| --- | --- | --- |
| 1 | F06 走离线摘要回答器（`llm_used=false`，无密钥） | 评分 0 correct / 23 partial / 16 incorrect，根因是回答器形态而非检索失败 |
| 2 | F04 只有关键词通道，向量索引为占位 | `data/index/20260904_v2/vectors/embeddings.npy` 为 `(0,0)`、`manifest.vectors.mode=placeholder`、`dim=0` |
| 3 | F02 的 LLM 兜底**未实现** | `server/query/understand.py:40-45` 只把 `llm_client/enable_llm` 存为属性，`understand()` 主流程无任何 LLM 分支；`server/runtime.py:83-84` 恒传 `llm_client=None, enable_llm=False`（审核 C1） |
| 4 | F08 演示模式未做，前端示例题为硬编码，且 **4 条里 2 条不在已审核题库** | `frontend/src/components/chat/ChatPane.vue:11-16` 的 `EXAMPLES`；题库 39 条中"牧野""井陉"命中 0 条 → 稳定性无保障 |

v5 目标：把链路切到**真实模型 + 向量检索**，交付**面向评审/访客的演示入口**，把部署形态
打磨到可稳定演示；同时处理 v4 用评测量化出的质量短板，并把实现与契约之间的漂移补齐。

## 二、需求与验收标准

### 2.1 F08 演示模式

| # | 需求 | 验收（可观测判据） |
| --- | --- | --- |
| 1 | 页面打开即可提问，不需要输入账号（源：[F08](../features/08-demo-mode.md) 验收 1） | 无登录壳/路由守卫；从零启动服务后直达首页；`GET /api/health` 返回 `status=ok` 即进入演示（该接口已存在，`server/api.py:141`） |
| 2 | 示例问题点击后能稳定返回完整回答（源：F08 验收 2） | 每条示例题连续跑 3 次（含 1 次缓存命中路径）均 `done`、`answer` 非空、`finish_reason=normal`；**缓存命中需在同一进程内复现**（缓存是进程内 dict，重启即空，见开发说明 §四.9） |
| 3 | 示例问题覆盖图谱检索与文本检索两类能力（源：F08 验收 3） | 示例清单逐条标注 `capability`（graph / text / both）与期望证据下限；冒烟脚本逐题断言 `graph_results.evidence` / `text_results.evidence` 满足下限，并留存冒烟日志 |
| 4 | 示例题必须来自已审核题库（本规划增补，源自 v4 教训） | 清单由 `data/eval/20260904_v2/questions.jsonl` 中 `reviewed=True` 且人工评分非 `incorrect` 的题生成；附生成脚本与过滤计数日志；前端不再硬编码问题（当前 4 条中"牧野之战…""井陉之战…"2 条不在题库）。**并须实测响应表现后筛题**：逐题记录首 thinking / 首正文时延与是否触顶截断（`finish_reason=length`），只把"首正文快且不截断"的题放进演示清单（实测同一模型下 4.76 s/不截断 与 13.11 s/截断 并存，见开发说明 §四.11） |
| 5 | 演示默认低成本可运行（增补） | 无 `LLM_*` / `EMBEDDING_*` 密钥时全链路仍可用（离线回答器 + 关键词检索），示例题冒烟用同一套判据通过 |
| 6 | 示例题显示能力分类标签（增补） | 前端按题库 `category`（关系型 / 背景型 / 时间线型 / 实体介绍等）分组渲染并显示标签 |

### 2.2 真实 LLM 接入（F06 落地）

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | `.env` 配 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 后 F06 自动切真实流式（token 级增量） | 同一问题 SSE `answer` 为多次 token 级增量，且增量拼接与最终整段一致；**推理阶段不得混入 `answer`**（只发 `thinking`，见 D6）；`done.finish_reason=normal`、`model_used` 为配置模型。**当前为"先算完再按句切"的假流式**：`server/sse.py:227` 传 `on_delta=lambda _: None`，增量由 `_chunk_answer_stream` 生成，需接线（开发说明 §四.4） |
| 2 | 超时/重试/备用模型降级链保持有效 | 断网/错 key 时 `finish_reason=degraded`（`heuristic-offline` 或备用模型）且回答仍产出；**降级结果不得写入回答缓存**（当前 `server/sse.py:241` 无条件下写，会把一次抖动污染成 1 小时缓存，见开发说明 §四.9） |
| 3 | **实现** F02 的 LLM 兜底（审核 C1：当前仅预留接口） | ✅ **已完成（2026-09-13）**：`server/query/prompts.py` + `llm_fallback.py` + `understand.py` 兜底分支（词典完全未命中才触发、独立 8 s 超时、失败一律降级、逐问缓存、两层异常保护）；可观测字段 `llm_entity_used` 进 entities 事件与 trace；11 条用例；端到端实测 `介绍一下拿破仑战争的时间线。` → `entities=['拿破仑战争']`，词典命中时零额外调用。默认关闭（`ENABLE_LLM_ENTITY_FALLBACK=false`） |
| 4 | **对照评测**：同题库重跑 `llm_used=true`，与基线 `run_20260913_postaudit` 对比 | ✅ **已完成三次取样 + 评分（2026-09-13）**：`runs/v5_llm_1`、`v5_llm_2`、`v5_llm_3`（hybrid + 真实 LLM，各 28 题）——回答覆盖 84.5% / 90.8% / **97.0%**（v4 离线基线 67.6%）、引用携带 77.1%、文本召回 97.0%。**答案正确性分布（样本 3，AI 代理评分）25 correct / 3 partial / 0 incorrect**（v4：0/23/16）、引用 28/28 supported；3 条 partial 均为模型偶发失败后降级离线摘要。14 条重点题（套件内 10 条）三次取样 8/10 → 8/10 → **10/10**，掉队题两次不同（随机性已量化）。评分表 `runs/v5_llm_3/scores.jsonl`（评分口径：用户确认由 AI 代理评估） |

### 2.3 向量检索启用（F04/F11 收尾）

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | 接入云端中文 embedding（**已定：阿里云百炼 `text-embedding-v4`**，1024 维，OpenAI 兼容接口），全量重建索引并写入**向量库（已定：Chroma 持久化库，`hnsw:space=cosine`）** | `manifest.vectors.mode="chroma"` 且记录 `dim/model/count/space`；`chroma.count() == len(ids.json) == embeddings.npy 行数 == 9,544`（分块实验后为新片段数）；`ids.json` 与 `npy` 作为审计副本保留。**当前构建侧未接线**：`data/index/build.py:61` 无条件写占位，`data/index/vectors.py:31 build_vectors(embed_fn=...)` 全仓无调用点，`--no-embeddings` 只影响一行日志（开发说明 §二、§四.3） |
| 2 | 实现 F04 `vector` / `hybrid` 分支（向量检索走 Chroma HNSW + 余弦） | 切 `mode=vector` 能返回结果，hybrid 返回融合分。**当前 `vector` 分支返回空**（`server/text/__init__.py:54-56`，且仅在 `vector_available=True` 时可达），**`hybrid` 等同纯 keyword 无融合**（`:50-53`）。验收含：Chroma 返回的 `distance`（= 1 − 余弦）换算为相似度后再归一化；抽样 20 条问句的 Chroma top-20 与暴力余弦 top-20 重合率 ≥ 0.9（HNSW 近似的可观测保障） |
| 3 | 加载校验与自动降级保持（索引损坏/缺失 → 关键词模式） | 三种破坏各测一次：删 `chroma/`、改短 `ids.json`、`count()` 与 ids 不一致 → `vector_available=false`、实际模式回落 keyword、不报错。校验位于 `server/text/searcher.py:45-60`、降级位于 `server/text/scoring.py:19-26`（不在 `data/index/vectors.py`） |
| 4 | **对照评测**：配置 `text-only（关键词 and_or）/ text-only-and / vector / hybrid` 同题库对比 | ✅ **已完成（2026-09-13，离线回答器、main 套件 28 题）**：回答覆盖 `vector`/`hybrid` 均 85.1%、关键词 71.7%、纯 AND 1.2%（**+13.4 个百分点，提升完全来自检索质量**）；文本 top-k 召回 hybrid 95.2%、关键词 94.6%、纯向量 89.9%；**长改写专项 4 题：纯 AND 召回 0%、`vector`/`hybrid` 均 100% → v4 暴露的 AND 失效已被向量通道弥补**。hybrid 三档策略亦已对照定档（见 D5）。**前置**：`EvalConfig` 预设已补 `vector`/`hybrid`；报告层原先把新配置静默丢弃（`_cfg_order` 白名单）已修 |

### 2.4 部署形态

| # | 需求 | 验收 |
| --- | --- | --- |
| 1 | 前端 `npm run build` 产物可静态托管或由后端同源托管 | 单进程启动后浏览器直接访问页面并完成一次问答。**后端当前没有任何静态托管**（无 `StaticFiles` / `app.mount`，路由仅 `/api/dicts`、`/api/health`、`/api/query` 三条），同源托管属新建（开发说明 §四.8） |
| 2 | `/api/health` 与两条 SSE 路径冒烟回归（全量检索 / 缓存命中） | 新增冒烟脚本一次跑通并记录日志（当前无冒烟脚本）；缓存命中路径须在同一进程内先跑全量再重复提问 |
| 3 | 限流（`RATE_LIMIT_PER_MINUTE`，默认 30/min）与缓存 TTL 按演示负载复核 | 限流已实现为 60 秒滑动窗口（`server/api.py:116-131`）；按默认 30/min 构造超限请求，触发 SSE `error` 帧（`error_code=invalid_request`）且前端有可读提示；记录"默认值是否调整"的复核结论 |
| 4 | 部署说明文档（端口、环境变量、数据目录、依赖、启动与自检） | 照文档从零启动成功。**须写明数据制品**：`data/snapshot/`、`data/index/`、`logs/` 被 `.gitignore` 忽略，部署包需另行携带，启动时靠 `server/runtime.py:46-71` 做快照/索引版本一致性校验；`workers` 必须为 1（内存图谱与回答缓存均为进程内） |

### 2.5 质量优化项（v4 已量化，本阶段处理）

| # | 问题（v4 证据） | v5 要求 |
| --- | --- | --- |
| 1 | 拒答规则只覆盖"无证据/无共享词"，X01–X03 应拒答却作答 | ✅ **已完成**：新增"领域外谓词"规则（邮箱/电话/度假/坦克…，**词表命中且证据里完全没有该词**才拒答）；refusal 套件 X01–X03 全部转 `correct_refusal`，main 套件无新增误拒。宿主 `server/sse.py` 与 `evaluation/chain.py` 两处同步 |
| 2 | F04 长改写 AND 优先必然失效（L01–L04 纯 AND 召回 0%、全拒答） | ✅ **已完成**：① 向量通道承接（长改写专项 `vector`/`hybrid` 召回 100%）；② 关键词路径补兜底——AND 命中不足 3 条时**并入** OR 结果（不再只在 AND 为空时才兜底）。text-only 四项指标齐升：召回 94.6%→95.8%、融合携带 88.1%→89.3%、引用携带 62.8%→65.2%、回答覆盖 71.7%→74.1% |
| 3 | `event_type` 筛选把 raw/evidence 整批剔除（F01 文本召回 100%→0%） | ✅ **已完成**：关系证据按事件卡片补齐 `event_type`（覆盖 **7503/7503**）；`_pass_meta` 调整为"有该元数据的严格过滤、原文片段（无事件归属）放行"。filter_loss 套件 F01 开筛选后 **0% → 100%/100%/50%/100%**，四题 filters-on 全绿 |
| 4 | 可选治理增强：为 1,215 组同名实体做人工审核回填；地点坐标覆盖率 0（快照 5316 个地点全无经纬度） | **同名实体：不做合并**（2026-09-14 用户判定——同名事件分属不同朝代，合并会丢掉一个朝代的记载且不可逆；1,215 组保持原样），改为**代码侧朝代消歧**（见 §2.5-8），全量人工回填不做。**地点坐标：已完成并接入**（2026-09-15 复用旧项目高德编码工具链，3,193/3,595 目标成功、覆盖 4,819/5,316 行 = 91%；剩余 402 条为高德引擎无结果的古地名）——坐标已写回旧库并重导出快照 `20260915_v1`、索引复用旧向量（文本逐条一致，无需重新嵌入）、前端新增地图组件（echarts + 中国底图），详见开发说明 §4.13/§4.14 |
| 5 | 可选：R1 方案①（朝代偏置折进文本 `_score`） | 如启用须做成默认关闭开关，并重跑基线复核 B03/L03 |
| 6 | **契约与实现漂移**：SSE `text_results.data` 已携带 `mode`（`server/sse.py:174`）但 `docs/data-contract.md` 只写 `evidence`；`contracts/retrieval.py:67-72` 的 `TextResult.mode / vector_available` 未进契约 | 同步 `docs/data-contract.md`：`text_results.data` 补 `mode`（keyword/vector/hybrid/none）并写明 `vector_available` 的探测与降级口径；v5 新增字段一律"先改契约、再加实现" |
| 7 | **分块参数从未做过对照实验**（用户已确认本阶段补做）：现为 `CHUNK_MAX_CHARS=800` / `CHUNK_OVERLAP_CHARS=80`（`config/defaults.py`，RAGv1 初版取值）；实测 9,544 片段、平均 329 字、中位 240 字，且有 105 条 <10 字的过短证据片段 | ✅ **已完成（2026-09-13）**：三组对照（500/100、800/80、1200/120）实跑，**结论为保持 800/80**——文本 top-k 召回在基线最好（94.6% 对 93.2%/92.9%），回答覆盖差异方向相反且量级 1.2–2.1 个百分点，500/100 成本 +22% 而收益仅"引用携带"一项。数据见 `data/eval/20260904_v2/chunk_exp_report.md`，另留有"向量通道接入后复测 500/100 vs 800/80"的建议 |
| 8 | **同名实体"问了朝代仍选错对象"**（2026-09-14 复查发现）：F02 歧义解析只在页面下拉硬筛选时按朝代选，问句里提到的朝代只进 `dynasty_bias`（排序加权）；实测问"西汉的井陉之战"仍选中**战国**那条、"唐朝的潼关之战"选中南北朝那条 | ✅ **已完成**：`_resolve_ambiguity` 增加"问句朝代偏好"——同名候选中朝代相符者前置（**偏好，不硬过滤**，避免 v4"问商朝把属夏的鸣条之战整题清空"的坑），候选集合不变、页面仍可纠正；朝代名宽松比对（唐 ≡ 唐朝 ≡ 唐代，五代 ≠ 五代十国）；新增可观测字段 `dynasty_disambiguated`（契约 + entities 事件 + 评测 trace）。实测"西汉的井陉之战"→ `event_0219`、"唐朝的潼关之战"→ `event_0612`；对现有 9 道演示题与 28 道评测题影响 **0**（无题同时"提朝代 + 命中同名"） |

## 三、任务分解与改动点（文件级）

| 任务 | 主要改动点 | 依赖 |
| --- | --- | --- |
| **T1 F08 演示模式** | 前端：`frontend/src/components/chat/ChatPane.vue`（示例题改为按能力分组渲染）、新增 `frontend/src/api/demo.ts`（或并入 `http.ts`）、`frontend/src/types/contract.ts`（示例题类型）；后端：`server/api.py` 增 `GET /api/demo/examples`；清单构建期生成：新增 `scripts/gen_demo_examples.py` → `data/eval/<v>/demo_examples.json`（入 Git、可追溯、可人工复核）；`config` 的 `demo_mode` **新建行为**（当前仅 `config/settings.py:65/130` 定义，`server/` 与 `frontend/` 均无引用） | 无（可先于 T2/T3） |
| **T2 真实 LLM** | `server/generate/llm_client.py`（`stream_chat(on_delta)` 已具备）、`server/generate/__init__.py`（`generate()` 现为阻塞式返回全文，需支持增量回调）、`server/sse.py`（把 `on_delta`（`:227` 现为空回调）接到 SSE `answer` 帧，替换 `_chunk_answer_stream` 假流式）、**`server/query/understand.py`（新实现 LLM 兜底分支 + 失败降级 + 用例，当前无实现）**、`server/runtime.py`（传 `llm_client`/开关，当前恒关）、`server/generate/cache.py`（degraded 不入缓存） | 密钥与模型口径（§四-1） |
| **T3 向量检索** | 构建侧：`data/index/vectors.py`（`build_vectors(embed_fn=...)` 已具备、**无调用点**）、`data/index/build.py:59-63`（从"无条件写占位"改为"嵌入并写 Chroma"）、新增 `data/index/embeddings.py`（**百炼 `text-embedding-v4`** 客户端：`dimensions=1024`、**batch ≤ 10**、退避重试、断点续跑、维度断言）、新增 `data/index/chroma_store.py`（`PersistentClient` + `hnsw:space=cosine` + `embedding_function=None` + 元数据标量约束）；检索侧：`server/text/searcher.py`（`search_vector()` 走 Chroma，**distance=1−余弦需换算**；`search_hybrid()` 按开发说明 §四.2）、`server/text/__init__.py:50-56`（三分支）、`server/text/scoring.py`（模式判定已具备）；`requirements.txt` 增 `chromadb`（**先做依赖共存预检**）。**生产接线（审核 C2，缺了等于线上不生效）**：`config/settings.py` + `.env.example` 新增文本模式开关（如 `TEXT_MODE`，默认 keyword；当前**不存在**该配置项）、`server/sse.py:160-163` 按配置传 `mode`（当前写死 keyword）、`server/runtime.py:108` 的 `meta["text_mode"]` 反映真实模式。**缓存键（审核 C3）**：`server/generate/cache.py:24 cache_key` 纳入 mode（可照 `dynasty_bias` 经 `filters` 进键的先例，`server/sse.py:112-115`）；若做成部署级全局开关，须在部署文档写明该前提。**契约**：`docs/data-contract.md` 补 `text_results.mode` | 密钥与模型维度（§四-1/2） |
| **T4 部署** | `server/api.py`（新增静态托管 `frontend/dist`，SPA 回退 index.html；当前无 `StaticFiles`）、`scripts/run_server.py`（启动自检打印：快照版本 / 文本模式 / 向量可用 / LLM 可用；已写死 `workers=1`，多 worker 会各自加载一份内存图谱与缓存，勿改）、新增 `scripts/smoke_deploy.py`、部署说明文档；`frontend/vite.config.ts` 仅在"子路径托管"时才需改基址（前端已用相对 `/api` 路径，同源根托管可直接用） | T1–T3 稳定后 |
| **T5 质量优化** | `server/generate/refusal.py`（判空与文案）、`server/sse.py:189-216`（两条拒答硬规则宿主）+ **`evaluation/chain.py:230-237` 同口径副本须同步改**、`server/text/searcher.py`（AND 兜底/取词放宽）、`data/snapshot/governance.py` + `scripts/build_index.py`（raw/evidence 元数据） | 可与 T2/T3 并行 |
| **T6 评测对照** | `evaluation/chain.py`（`EvalConfig.mode` 与 `graph_top_k` 已具备，**需补 vector/hybrid 预设**）、`evaluation/cli.py`（`--configs` 支持新模式；`--llm` 已具备）、新增对照报告模板/章节；评分口径不变（`evaluation/grading.py`） | 依赖 T2/T3 |
| **T7 契约与文档同步** | `docs/data-contract.md`（`text_results.mode`）、`docs/README.md` 功能状态表（F04/F06/F08）、`docs/features/` 下 04、06、08、11 四份功能文档的状态行、`docs/RAG_v1/README.md` 索引、`RAGv5-开发说明.md` 回填实测、`docs/changes/<date>-ragv5-summary.md` | 收口时 |
| **T8 分块参数实验** | 新增 `scripts/compare_chunking.py`（三组参数各建独立版本索引 + 跑离线评测 + 汇总）、`config/defaults.py` 注释补候选值、产物 `data/eval/<v>/chunk_exp_report.md`；**800/80 的既有索引必须保留**（v4 基线只在 800/80 下可比） | **先于 T3 全量建向量** |

## 四、前置条件与待确认事项

### 4.1 已定（2026-09-13 用户决策）

| # | 事项 | 结论 |
| --- | --- | --- |
| D1 | **embedding 模型与密钥** | 阿里云百炼 `text-embedding-v4`（OpenAI 兼容：`https://dashscope.aliyuncs.com/compatible-mode/v1`），维度 **1024**（已实测接口返回 1024）。**密钥已在系统环境变量 `DASHSCOPE_API_KEY` 中**（2026-09-13 实测可读且调用成功），代码读取优先级为 `EMBEDDING_API_KEY` → `DASHSCOPE_API_KEY`，不要求写进 `.env`。单请求 ≤ 10 条文本（实测 batch=10 可用），单条 ≤ 8,192 token；全量 9,544 片段约 955 批，串行约 25 分钟、3–4 并发约 6–8 分钟，成本约 1–2 元 |
| D2 | **向量存储** | **Chroma 持久化库**（`PersistentClient`，落 `data/index/<v>/vectors/chroma/`，`hnsw:space=cosine`）。此决策**取代**原"自研 FTS5 + NumPy 足够、暂不引入向量库"的暂定结论（该分析作为历史判断依据保留在 [后续阶段规划.md](后续阶段规划.md) §三，已按本决策追加修订说明）；`ids.json` 与 `embeddings.npy` 保留为审计副本（不参与检索，见开发说明 §四.3）。**依赖风险已消除**：项目环境已装 `chromadb 1.3.4`，与 `fastapi 0.121` / `pydantic 2.11` / `numpy 2.3` / `openai 2.7` 共存实测无冲突 |
| D3 | **向量检索算法** | Chroma 内建 **HNSW 近似最近邻 + 余弦距离**（返回 `distance = 1 − 余弦相似度`，需换算后再归一化）；元数据过滤走 `where` 前置过滤。HNSW 的近似性需用"与暴力余弦 top-k 抽样比对"兜底（开发说明 §六.1） |
| D4 | **分块参数实验** | 本阶段做一次小规模对比实验（500/100、800/80、1200/120），先于全量建向量执行（§2.5-7、开发说明 §四.10） |
| D5 | **hybrid 融合口径** | ✅ **已定档 `rrf`（2026-09-13 评测定档）**：`main` 套件 28 题、离线回答器下三档对照——`rrf` 文本 top-k 召回 **97.0%** > `weighted` 95.2% > `fallback` 94.6%；回答覆盖 `rrf`/`weighted` 并列 **85.1%**，`fallback` 仅 71.7%（与关键词基线完全相同，等于没做融合）。代码保留三档（`TEXT_HYBRID_STRATEGY` 一个环境变量切换），`weighted` 的权重仍可配 |
| D6 | **真实 LLM** | base_url `https://api.commandcode.ai/provider/v1`（多模型聚合），模型 **`deepseek/deepseek-v4.1-flash`**（已确认在 `/models` 的 69 个模型列表中），密钥在机器级环境变量 **`RAG-command`**（2026-09-13 实测可用）。**官方作为备用/项目结束后口径**：`https://api.deepseek.com/v1` + `deepseek-flash` + `RAG-deepseek-v4`。读取优先级 `LLM_API_KEY → DEEPSEEK_API_KEY → RAG-command → RAG-deepseek-v4`（连字符名 Linux 不合法，别名链是部署必需；删掉 `RAG-command` 即零代码切回官方）。`FALLBACK_LLM_*` 指向官方三件套 → "中转挂了自动降级到官方"。**已实现的实测口径**：两端都流式输出推理但**字段名不同**（中转 `delta.reasoning`、官方 `delta.reasoning_content`，必须同时兼容）；**推理长度波动极大**（同一模型：赤壁题 reasoning 161 token、首正文 4.76 s；长平题 1547 token、首正文 13.11 s）；**`max_tokens` 会触顶截断**（实测 1024 与 2048 均被打满），须保留 `finish_reason=length` 告警并考虑裁剪送模证据；首 Token 口径以"首个可见输出（thinking）"为准（长平题 4.83 s）。细节与数据见开发说明 §四.11 |
| D7 | **演示现场有外网**（用户 2026-09-13 确认） | 主演示路径 = **向量检索 + 真实 LLM**；降级链（关键词通道 + 离线摘要回答器）只作现场抖动/故障兜底，不作为主路径验收。F08 示例题与冒烟的验收按"有网形态"执行，另跑一次无 key/断网形态作为降级回归 |
| D8 | **部署形态 = 同源托管**（用户 2026-09-13 确认） | 后端单进程同时提供网页与 API：浏览器只访问 `http://演示机:8000/`，页面调同源的 `/api/*`。前端已全用相对路径（无需改构建配置），后端需新增 `dist` 静态挂载（T4）。不做独立静态托管/CDN、不做容器 |
| D9 | **送模证据条数 = 10**（`QUERY_FUSION_LIMIT`，2026-09-13 实测定） | 18（v4 口径）时 prompt 约 4,300 token，导致推理变长 → 答案被 `max_tokens` 截断、首正文变慢：18 道候选示例题里 **13 道不可用**（5 条截断 + 8 条超 9 s）。降到 10 后：**回答覆盖完全不变**（vector/hybrid 仍 85.1%），hybrid 文本召回 95.2% → **97.0%**，可用示例题 **5 → 9 条**（截断告警 13 → 3 次）。仓库默认仍 18（保持 v4 可比），`.env` 设 10；若仍见截断先降到 8 |

### 4.2 待确认（开工前需拍板；实施期已逐条落地）

| # | 事项 | 结论 |
| --- | --- | --- |
| 1 | **F08 示例题最终组数**与是否需要"评委模式"面板（F08 功能文档的两个待确认项） | ✅ 定为 **9 条**（实测筛出，`demo_examples.json`）；**不做**独立"评委模式"面板，示例区按类别分组 + 能力标签 + 实测时延 |
| 2 | **`demo_mode` 配置项语义**：是"演示开关"（控制示例区、限流档位）还是直接删除。当前是死配置，两种选择都合理，但不能留着不接 | ✅ **删除**（开发说明 §4.7 结论②，2026-09-14 落地）：示例区恒显示，不留死配置 |
| 3 | **文本模式开关形态**：部署级全局开关（开发说明推荐）vs 按请求可切（决定缓存键形态与请求契约是否新增字段） | ✅ **部署级全局开关** `TEXT_MODE`（仓库默认 keyword，演示 `.env` 设 hybrid）；缓存键已含 mode（C3）；请求契约不新增字段 |
| 4 | **仓库与数据制品策略**：RAG 是嵌套 git 仓库，外层 `china-war` 仍把 `RAG/` 视为未跟踪；项目级"统一提交到新公开仓库"口径未落地。公开演示前需确认数据制品（`data/snapshot`、`data/index`，含新增的 Chroma 目录）的携带与授权 | ◐ **部分落地**：`.gitignore` 已修（2026-09-14）——`data/snapshot/`、`data/index/` 改为"忽略产物、保留源码与说明"，18 个源码/说明文件现可入库（详见 [RAGv5-遗留项整改方案.md](RAGv5-遗留项整改方案.md) §A1）；**剩余**：新建 v5 分支提交并推送，以及产物（快照/索引/Chroma）携带与授权的最终确认（产物保持不入库、随部署包拷贝） |

> 部署目标（同源）与现场外网已确认，见 D7/D8；密钥两项均已就绪（D1/D6）；第 4 项若与飞书渠道化 P0 并行推进需协调。

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
  人工评分不得用模型评分替代；
- **评测能力现状**：`EvalConfig` 已含 `mode`（keyword/vector/hybrid）与 `graph_top_k`、
  透传已接（`evaluation/chain.py:33/189`），`run --llm` 开关已存在；但 `--configs` 预设
  仍只有 `dual / text-only / text-only-and / text-only-or`，v5 跑向量对照前需补预设（T6）。
  另需新增两项**离线专项口径**：分块参数三组对照（T8，见开发说明 §四.10）与
  `vector`/`hybrid` 两档的额外指标（Chroma top-k 与暴力余弦 top-k 的重合率，用于量化
  HNSW 近似带来的召回损失）。

## 六、风险与对策

| 风险 | 对策 |
| --- | --- |
| LLM 输出随机 → 评测不可复现（审核 R2） | 固定模型版本与温度、先跑一轮缓存（或禁用缓存）；同一配置 **2–3 次取样取分布**（或明确接受单次并标注置信边界）；报告注明随机性 |
| 密钥成本/限流 | 演示默认低频；`.env` 单独配置演示用 key；限流按演示负载复核；F02 兜底默认关闭（开启后每问两次串行 LLM 调用，直接影响首 Token，见 `docs/architecture.md` 非功能要求 7/8） |
| embedding 全量重建耗时/费用 | **已实测（2026-09-13）**：单条 1.07 s / 10 条批量 1.50 s、返回维度 1024 与配置一致 → 9,544 片段约 955 批，串行约 25 分钟、3–4 并发约 6–8 分钟，成本约 1–2 元；断点续跑复用已完成批次（batch ≤ 10 是百炼硬上限）。**查询期**：每次提问向量化 8–13 token（约 0.000005 元）、耗时中位 0.589 s；生成侧 token 量是其 176–391 倍 → **成本瓶颈在 LLM 不在 embedding**；同题重复提问命中回答缓存时连向量化都不发生（缓存检查在检索之前） |
| 索引目录不可覆盖 | `data/index/build.py:40-41` 已存在索引目录即报错：重建向量须使用新快照版本目录，或先删除旧索引目录；步骤写进开发说明与部署手册 |
| 向量索引体积与启动时间 | 1024 维 × 9,544 片段：`npy` 审计副本约 39 MB + Chroma 自身副本，合计百 MB 量级，本地可接受；若分块实验导致片段数显著上升，按"片段数 → 存储与请求数"重新评估 |
| 演示现场不稳定（无网/超时） | 降级链已就位（离线回答器/关键词模式）；演示前跑冒烟脚本 |
| F08 示例题被系统缺陷影响 | 选型规则从"评分非 incorrect 的已审核题"中选；避开 F02 类缺陷问法；冒烟逐题断言证据下限 |
| hybrid 分数量纲不一致（审核 R1） | 关键词分为 min-max 后的 bm25 反值、向量分为余弦相似度，而 F05 还按问题类型权重对图谱/文本分配额与排序（`server/fusion/fusion.py:98-125`）；**须先定义"向量分归一化 + hybrid 融合口径（含与 F05 权重的衔接）"再实现**，否则排序不可解释、连带影响 citation 与评分。本文 §2.3-4 已纳入对照验收，口径草案见开发说明 §四.2 |
| **向量通道与拒答硬规则冲突** | 第二条硬规则按"证据与问题是否有 ≥2 字共享词"判定（`server/sse.py:204-216`）；向量召回的语义相关片段可能不含问题词 → 误拒。对策：规则按模式分档（keyword 严格、vector/hybrid 用"分数 + 共享词"组合），并在评测中复核拒答 3 题 |
| **可观测性随模式变化** | 模式切换后 `meta.text_mode`、SSE `text_results.mode`、评测 trace 必须同步反映真实模式，否则"看起来生效"无法核查（C2 的核心） |
| 契约漂移继续扩大 | v5 新字段一律先改 `docs/data-contract.md` 再加实现；T7 收口时逐条比对实现与契约 |
| **推理长度波动 → 答案截断与首正文变慢** | 同一模型下推理量差一个量级（实测 reasoning 161 vs 1547 token），首正文 4.76 s vs 13.11 s；**hybrid 检索下证据更多、prompt 涨到 4,349 token，推理 1,510 token，`max_tokens=2048` 再次触顶**。对策：上限提到 **3072**；保留 `finish_reason=length` 告警；F08 示例题按实测时延与是否截断筛题（§2.1-4）；必要时裁剪送模证据（`FUSION_LIMIT` 18 → 10–12）或提示词控篇幅 |
| **中转服务是第三方依赖** | 项目期主用 `api.commandcode.ai`（多模型聚合），其可用性、费率与模型清单随时可能变。对策：`FALLBACK_LLM_*` 指向官方 endpoint 作降级；启动自检打印实际 `model`；演示前跑冒烟；首 Token 口径改用"首个可见输出（thinking 流）" |
| 公开演示的数据授权 | 原始图书文本用于建库需确认授权（`docs/architecture.md` 风险 1）；未确认前仅内部/比赛演示 |
| ~~`chromadb` 依赖共存~~ | **已排除（2026-09-13）**：项目环境已有 `chromadb 1.3.4`，与 `fastapi 0.121` / `pydantic 2.11` / `numpy 2.3` / `openai 2.7` 共存无冲突，`requirements.txt` 记 `chromadb>=1.3` 即可 |
| **HNSW 近似召回** | Chroma 走近似最近邻，可能静默漏掉本应召回的片段，影响召回指标与评测结论。对策：抽样 20 条问句比对 Chroma top-20 与暴力余弦 top-20（重合率 ≥ 0.9），把实测值写进报告；不足则调 HNSW 参数或记录该限制 |
| **分块实验的成本放大** | 片段数增加会按 batch=10 线性放大向量化请求数与 Chroma 体积。对策：实验报告必须给出"片段数 → 请求数 / 存储体积"成本对比，再决定是否切换 |

## 七、里程碑与建议顺序

1. **M0 开工准备**：拍板 §4.2 的 5 项待确认（至少部署目标、`demo_mode` 语义、文本模式开关形态、
   演示现场是否有外网）、确认第十节的现状核查结论、建立 v5 分支、
   ~~`chromadb` 依赖共存预检~~（✅ 已完成）、**分块参数对比实验（T8）**（✅ 已完成，结论为保持 800/80）；
2. **M1 向量线**：✅ **已完成主体（2026-09-13）**——百炼 embedding 接线（构建侧，含小样维度验证）→
   全量嵌入并写 Chroma（9,544 条 / dim 1024 / 955 批 / 365 s）→ F04 向量/hybrid 实现 + 生产接线与缓存键 →
   一致性抽检（Chroma vs 暴力余弦重合率 0.935）→ 四配置 + 三档位对照评测（回答覆盖 71.7% → 85.1%）
   → 融合策略定档 `rrf`；剩余：`.env` 已切 `TEXT_MODE=hybrid`（仓库默认仍为 keyword），
   演示示例题选型与向量通道的提示词调优归入 M3/M5；
3. **M2 LLM 线（可与 M1 并行）**：✅ **已完成（2026-09-13，09-14 补 F02 兜底）**——真实流式（队列桥接 + `thinking` 事件）、
   配置/别名链/`LLM_MAX_TOKENS`、缓存卫生均已实现并端到端实测；✅ F02 LLM 兜底（§4.5）、
   ✅ 证据裁剪调优（`QUERY_FUSION_LIMIT` 18 → 10，D9）、✅ `--llm` 三次取样与 14 条重点题复测（8/10→8/10→10/10）；
4. **M3 F08**：✅ **已完成（2026-09-13）**——示例题清单生成（39 题 → 候选 18 → **实测筛出 9 条**，含时延/截断维度）
   → `GET /api/demo/examples` → 前端按类别分组 + 能力标签 + 实测时延（硬编码示例题已移除）→
   冒烟 **9/9 通过**、复跑命中缓存 29 ms；✅ 无密钥整机冒烟已跑（§2.1-5：31–79 ms、自动降级 keyword）；
5. **M4 部署**：✅ **已完成（2026-09-13）**——同源托管挂载与启动自检、`smoke_deploy.py`（6 步冒烟）、
   `docs/deploy.md`（含"冒烟即预热"用法与演示预期表现）、限流复核（第 29 次请求触发）；
   ✅ 无密钥形态整机冒烟已跑（与 M3 同一项）；
6. **M5 质量优化与收口**：✅ **已完成**——拒答补强（X01–X03 → `correct_refusal`）、
   AND 兜底（text-only 覆盖 71.7%→74.1%）、筛选元数据（F01 开筛选 0%→100%）、
   契约补 `text_results.mode` 均已完成；✅ `--llm` 对照评测（三次取样，28 题评分 25/3/0）与
   14 条重点题复测；✅ T7 文档收口（功能状态表、`changes/20260913-ragv5-summary.md`、
   `RAGv5-阶段工作总结.md`）；28 条评分口径已确认（2026-09-14 用户：由 AI 代理评估即对外口径）。

## 八、边界与不做

1. 不做多用户/账号/权限体系（演示无需登录）；
2. 不做历史地图底图素材（涉及素材与授权，另行评估；当前地点仍为列表降级）；
3. 不用模型评分替代人工评分（F10 口径不变）；
4. 不修改旧 `backend/`、旧 `frontend/`、`entity-event-relation/` 代码（项目级约定）；
5. 不在本阶段扩大题库规模（如确需扩题，按 v4 的 `REVIEW_GUIDE.md` 走一轮审核）；
6. **不做飞书 / Hermes 渠道化**（`new/RAG分离与飞书Hermes渠道化-需求分析.md` 待评审另行立项）：
   其中的 P0"非流式 JSON 接口 + 独立部署包"与本阶段部署打磨有交集，若并行启动需协调
   `server/api.py` 的改动归属；
7. ~~不实现 SSE 协议里已定义但从未发射的 `thinking` 事件~~ → **已变更（2026-09-13）**：
   `thinking` 已实现并实际发射（推理模型的 reasoning 阶段，`server/sse.py:282`），
   契约与 `docs/data-contract.md` 已改为"实际发射、对接方可依赖"。原边界条目作废。

## 九、本阶段产物清单（预期）

| 产物 | 路径 |
| --- | --- |
| 需求与验收（本文档） | `docs/RAG_v1/RAGv5-规划说明.md` |
| 开发说明 | `docs/RAG_v1/RAGv5-开发说明.md`（开工前设计版已产出；实现后回填实测数据与行号） |
| 对照评测报告 | `data/eval/<v>/runs/<llm_run>/report.md` + 与基线的对照小节 |
| 示例题清单（可追溯） | `data/eval/<v>/demo_examples.json`（由题库过滤生成，附生成脚本与计数日志） |
| 向量库与索引 | `data/index/<v>/vectors/chroma/`（Chroma 持久化集合）+ `ids.json` / `embeddings.npy`（审计副本） |
| 分块实验结果 | `data/eval/<v>/chunk_exp_report.md`（三组参数对照 + 切换/保持结论） |
| 向量一致性抽检 | `scripts/check_vector_consistency.py` 的输出报告（Chroma vs 暴力余弦重合率） |
| 契约更新 | `docs/data-contract.md`（`text_results.mode` 等） |
| 部署说明 | `docs/deploy.md` 或在 `README.md` 增部署章节 |
| 变更总结 | `docs/changes/<date>-ragv5-summary.md` |
| 文档同步 | `docs/README.md` 功能状态表、`docs/features/` 的 F04/F06/F08/F11、`docs/RAG_v1/README.md` |

## 十、开工前现状核查摘要（2026-09-13，逐条核对到代码）

完整证据（file:line）与"规划原表述 → 修正后"对照见 [RAGv5-开发说明.md](RAGv5-开发说明.md) §二。

| # | 事项 | 核查结论 | 影响 |
| --- | --- | --- | --- |
| 1 | 向量索引构建 | `data/index/build.py:61` 无条件写占位；`build_vectors(embed_fn=...)` 无调用点；`--no-embeddings` 只改日志 | T3 需新增"构建侧接线"，不只是"重建" |
| 2 | 向量加载校验/降级 | 位于 `server/text/searcher.py:45-60` 与 `server/text/scoring.py:19-26`，**不在** `data/index/vectors.py` | 文档行号引用已修正 |
| 3 | `hybrid` 模式 | 当前等同纯 keyword（`server/text/__init__.py:50-53`），非"返回空" | §2.3-2 措辞已修正 |
| 4 | 静态托管 | 后端无 `StaticFiles`/`app.mount`；路由仅 3 条 | §2.4-1 属新建功能 |
| 5 | `TEXT_MODE` | config 与 `.env.example` 中**不存在**；`demo_mode`、`EMBEDDING_*` 存在但全仓无读取点 | T3/T1 为新建配置与接线 |
| 6 | 评测模式对照 | `EvalConfig.mode` 已具备，但 CLI 无 vector/hybrid 预设 | §2.3-4 需先补预设 |
| 7 | 假流式 | `server/sse.py:227` 传空回调，增量由 `_chunk_answer_stream` 生成 | §2.2-1 非"接线即可"，需改造 `generate()` 为增量 |
| 8 | 契约漂移 | SSE `text_results` 实际带 `mode`（`server/sse.py:174`），契约未记录 | 新增 §2.5-6 与 T7 |
| 9 | v4 Git 固化 | 已完成（RAG 仓库 `50aafa2`，分支 `ragv4-evaluation`） | §四-8 仅剩仓库与数据制品策略待定 |
| 10 | **`--suites` 参数实际不可用（T8 实施时发现并已修复）** | `evaluation/cli.py` 原把 `--suites` 字符串当可迭代对象，`run --suites main` 会报"未知套件: ['m','a','i','n']"——即 v4 开发说明里写的命令**从未真正跑通**（历史 run 都是"空 = 全部套件"） | 已修（改为逗号分隔解析）并补守护用例 `tests/test_cli_args.py`（用例数 50 → 54） |

> **落地情况（2026-09-14 复查）**：第 1–8、10 项均已落地（对应实现与实测见
> [RAGv5-开发说明.md](RAGv5-开发说明.md) §三/§九/§十一）；第 5 项的 `demo_mode` 已删除、
> `TEXT_MODE` 已接线为部署级开关；第 9 项除"仓库与数据制品策略"外均已就绪——提交/推送前需先修
> `.gitignore`（源码目前被整目录忽略），见 §4.2-4 与 [RAGv5-遗留项整改方案.md](RAGv5-遗留项整改方案.md) §A1。
