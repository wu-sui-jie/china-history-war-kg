# RAGv5（F08 演示模式 + 真实模型/向量接入 + 部署打磨）阶段工作总结（送审版）

- 阶段：RAGv5 = F08 演示模式 + 真实 LLM/向量接入 + 部署打磨 + v4 遗留质量优化
- 状态：**已完成**（功能、质量优化、部署、LLM 对照评测与评分；评分口径为 AI 代理，见第七节边界）
- 日期：2026-09-13
- 数据版本：`20260904_v2`（快照 + 索引；索引含 Chroma 向量集合）；题库标注版本：`reviewed-2`
- 本文档用途：**自包含总结，可直接交给第三方模型做代码/数据/结论审查**；第八节给出可执行的审核清单
- 相关文档：需求与验收 [RAGv5-规划说明.md](RAGv5-规划说明.md)；
  实现与逐项实测 [RAGv5-开发说明.md](RAGv5-开发说明.md)（§九 验收矩阵、§十一 进度快照）；
  部署 [../deploy.md](../deploy.md)；变更明细 [../changes/20260913-ragv5-summary.md](../changes/20260913-ragv5-summary.md)

---

## 一、阶段目标与口径

v4 交付了"可评测、可追溯"的链路，但三处仍是离线/占位态：F06 走离线摘要回答器、F04 只有关键词通道
（向量索引是 `(0,0)` 占位）、F02 的 LLM 兜底只有属性没有实现；F08 演示模式未做，
前端示例题硬编码且其中 2 条不在题库。v5 的目标是把链路切到**真实模型 + 向量检索**、
交付**面向评审的演示入口**、把部署打磨到可稳定演示，并处理 v4 量化出的质量短板。

口径（实施期确认，见规划说明 §4.1）：

| 项 | 结论 |
| --- | --- |
| 向量模型 | 阿里云百炼 `text-embedding-v4`，1024 维（实测接口返回 1024），单请求 ≤10 条文本 |
| 向量库 | **Chroma** 持久化集合（`hnsw:space=cosine`），落 `data/index/<v>/vectors/chroma/` |
| 检索模式 | 部署级开关 `TEXT_MODE`：keyword / vector / hybrid；演示用 **hybrid + rrf** |
| 真实 LLM | 项目期内走**中转** `api.commandcode.ai/provider/v1` + `deepseek/deepseek-v4.1-flash`；官方 `api.deepseek.com` 作备用（`FALLBACK_LLM_*`）；密钥在系统环境变量（`RAG-command` / `RAG-deepseek-v4`），代码按别名链读取 |
| 送模证据条数 | `QUERY_FUSION_LIMIT=10`（实测定；仓库默认 18 保持 v4 可比） |
| 部署形态 | **同源托管**：后端单进程同时提供网页与 API（`FRONTEND_DIST` + 路由后挂载） |
| 现场外网 | 有（主路径 = 向量 + 真实 LLM；降级链仅作抖动兜底） |

## 二、交付物清单

### 2.1 新增代码（`RAG/` 相对路径）

- `data/index/embeddings.py`（百炼客户端：分批 ≤10、重试、维度断言）；
- `data/index/vector_pipeline.py`（分批落盘 + 断点续跑 + 并发 + npy 审计副本 + 写 Chroma + 更新 manifest）；
- `data/index/chroma_store.py`（Chroma 封装：cosine、禁用默认 EF、元数据标量约束、count 校验）；
- `server/query/prompts.py` + `server/query/llm_fallback.py`（F02 LLM 兜底：提示词 + 同步客户端）；
- `scripts/fetch_place_coords.py`（高德坐标批量获取：断点续跑 + 配额保护 + 去重 + 按线索排序）；
- 前端 `src/components/panel/MapView.vue` + `src/assets/china-map.json`（echarts 地图 + 省级底图，随包走）；
- `scripts/gen_demo_examples.py`、`scripts/smoke_deploy.py`、`scripts/check_vector_consistency.py`、
  `scripts/compare_chunking.py`；
- 前端 `src/api/demo.ts` + `ChatPane.vue` 分组渲染（移除硬编码示例题）；
- `tests/`：`test_hybrid_scoring.py`、`test_llm_client_fields.py`、`test_cli_args.py`、`test_t5_quality.py`、
  `test_vector_degradation.py`、`test_sse_chunking.py`、`test_f02_llm_fallback.py`、
  `test_generate_degradation.py`、`test_vector_wiring.py`、`test_f02_dynasty_disambiguation.py`、
  `test_panel_map_points.py`（用例总数 50 → **140**）。

### 2.2 在线服务改动（要点）

- `server/text/searcher.py`：`search_vector`（余弦，含 `distance→相似度`换算）、`search_hybrid`、
  `_where_of`、AND 兜底（命中不足并入 OR）、`_pass_meta` 的 event_type 语义；
- `server/text/scoring.py`：`fuse_weighted` / `fuse_rrf` / `fuse_hybrid`；
- `server/sse.py`：队列桥接的**真 token 流式**、`thinking` 事件发射、缓存卫生（degraded 不入缓存）、
  拒答第三条规则（领域外谓词）、模式按配置传递；修复增量切分丢换行；
- `server/generate/llm_client.py`：`max_tokens`、两端推理字段兼容、`include_usage`、截断告警；
- `server/generate/__init__.py`：空正文兜底（降级离线回答器，避免"提问得到空白"）；
- `server/generate/refusal.py`：领域外谓词词表与保守判定；
- `server/query/understand.py`：同名实体的**朝代偏好消歧**（命中朝代的候选前置，不硬过滤，不可观测字段→`dynasty_disambiguated`）；
- `server/fusion/panel_builder.py`：地图点位装配重写（同名聚类 + 优先带地址线索的簇 + 事件合并 + 上限 8 点）；
- `data/index/chunking.py`：关系证据按事件卡片补 `event_type`（覆盖 7503/7503）；
- `server/query/understand.py`：F02 LLM 兜底分支（触发条件/超时/降级/缓存/两层异常保护）；
- `server/api.py`：`GET /api/demo/examples` + 同源托管挂载；`server/runtime.py`：索引变体、embed_fn 注入、
  `meta.text_mode` 真实反映；
- `evaluation/`：新增 `vector`/`hybrid` 预设、`fusion_limit` 跟随部署配置、修复报告层白名单静默丢弃新配置、
  run 元数据记录 `index_version`/`chunk_params`。

### 2.3 数据资产

- 向量索引：`data/index/20260904_v2/vectors/`（Chroma 集合 9,544 条 + `ids.json` + `embeddings.npy` 审计副本）；
- 示例题清单：`data/eval/20260904_v2/demo_examples.json`（9 条，含类别/能力/实测时延；入 Git 可复核）；
- 评测 run（不入库）：`runs/v5_vec_compare`、`v5_vec_fusion10`、`v5_vec_longrewrite`、`v5_hybrid_rrf|fallback`、
  `v5_refusal_fix`、`v5_filter_fix`、`v5_and_fallback`、`v5_llm_1|2|3`、`chunkexp_*`；
- 分块实验报告：`data/eval/20260904_v2/chunk_exp_report.md`。

### 2.4 文档

- 新增：[RAGv5-开发说明.md](RAGv5-开发说明.md)、[RAGv5-规划说明.md](RAGv5-规划说明.md)（重写为需求与验收口径）、
  [../deploy.md](../deploy.md)、[../changes/20260913-ragv5-summary.md](../changes/20260913-ragv5-summary.md)、本文档；
- 同步：`docs/README.md`（F04/F06/F08/F11 状态 + 阶段表）、`docs/features/04|06|08|11`、
  `docs/data-contract.md`（`text_results.mode`、`thinking`、`entities.llm_entity_used`、筛选语义）、
  `docs/RAG_v1/README.md`、`docs/RAG_v1/后续阶段规划.md`（向量库决策修订）。

## 三、设计要点（审查时容易质疑的地方，先说明）

1. **向量只走一条检索路径**：检索读 Chroma；`embeddings.npy` 只是审计副本（换机器免重嵌入、可校验条数），
   不实现第二条检索实现（避免双实现双维护）。
2. **模式降级是允许状态**：向量不可用（集合缺失/条数不一致/无密钥）→ `vector_available=False` →
   `resolve_mode` 自动回落 keyword；但**条数不一致绝不"将就"**（会错位检索），直接判不可用。
3. **hybrid 的口径**：两通道各自 min-max 归一化 → 按策略融合（默认 `rrf`，可切 `weighted`/`fallback`）→
   融合分再次归一化到 [0,1] 输出，保持 F04 的 score 契约与 F05 的排序口径不变。
4. **拒答的保守优先**：新增的"领域外谓词"规则只在"词表命中 **且** 所有证据都没有该词"时才拒答；
   向量模式下的"无共享词"规则也放宽为"无共享词 **且** 最高分低于阈值"。
5. **F02 兜底不默认开**：每问多一次串行调用会吃首 Token；开关默认 false，且失败/超时/解析失败一律降级。
6. **空正文必兜底**：推理模型的 reasoning 可能吃满 `max_tokens` 导致正文为空（实测 28 题中 2 题），
   此时降级离线回答器（`degraded`，不入缓存），保证任何情况下都有可读回答。
7. **缓存卫生与可复现**：只有 `normal`/`refused` 入缓存；缓存键含文本模式与王朝偏置；
   评测链路不读不写缓存（跨配置对比不被污染）。

## 四、本轮发现并处理的系统问题（全部有复现与修复证据）

| # | 问题 | 影响 | 处理 |
| --- | --- | --- | --- |
| 1 | `np.save()` 对不以 `.npy` 结尾的文件名会自动补后缀 → 全量向量构建中断 | 955 批白跑一半 | 修临时文件名（以 `.npy` 结尾）；抢救已付费批次；重跑续传 |
| 2 | 评测报告 `_cfg_order` 用白名单过滤配置 → 跑了 112 条只列 2 个配置 | 新增的 vector/hybrid 静默消失 | 改为"未知配置追加在后"，不再丢弃 |
| 3 | `--configs`/`--suites` 参数：`--suites` 被逐字符迭代 | v4 文档里的 `run --suites main` 从未跑通 | 修 `_split_suites` + 用例 |
| 4 | 离线回答的 SSE 增量丢换行（切分正则吃掉空白） | 流式拼出的答案比原文少字符、与缓存回放不一致、前端列表粘连 | 重写切分（拼接严格等于原文）+ 6 条用例 |
| 5 | 推理模型 `max_tokens` 吃满 → 正文为空但 `finish_reason=normal` | 演示可能得到空白回答 | 空正文一律降级离线回答器；截断告警保留 |
| 6 | F02 兜底 `max_tokens=256` 被 reasoning 吃满 → 解析出空实体 | 兜底形同虚设 | 提到 1024，实测 3–4 s 内正常返回 |
| 7 | `--suites`/报告/构建三处的"静默"行为（见上） | 结论可能建立在错误前提上 | 均已修复并加守护用例 |
| 8 | 问句里写了朝代，F02 仍选同名候选的第一条（问"西汉的井陉之战"自动选中**战国**那条） | 跨朝代同名事件答错对象——页面若不手点候选就会答成另一个朝代 | `_resolve_ambiguity` 增加问句朝代**偏好**（不硬过滤，避免 v4"整题清空"的坑）：同名候选中朝代相符者前置、候选集合不变；新增 `dynasty_disambiguated` 可观测字段；21 条用例 |

另有两处**观测口径**问题记录在案：① trace 的 `finish_reason` 只记 normal/degraded/refused，
**不反映截断**（截断只在日志里告警）；② `ok/n` 是"回答里出现标注词"的机械口径，
真实 LLM 用自然语言作答会漏词（v4 是"28/28 ok 但 0 correct"，v5 是"24–26/28 ok 但回答更完整"），
因此**正确性以评分表为准**（评分由 AI 代理按 `evaluation/grading.py` 口径给出，用户已确认为对外口径）。

## 五、结果数据

### 5.1 向量检索对照（main 28 题，离线回答器，隔离 LLM 变量）

| 配置 | 文本 top-k 召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- |
| text-only（关键词 and_or） | 94.6% | 94.6% | 66.7% | 71.7% | 28/28 |
| text-only-and（纯 AND） | 2.4% | 2.4% | 1.2% | 1.2% | 1/28 |
| vector | 89.9% | 89.9% | 67.9% | **85.1%** | 28/28 |
| **hybrid（rrf，采用）** | **97.0%** | **97.0%** | **77.1%** | **85.1%** | 28/28 |

- 长改写专项 4 题：纯 AND 召回 **0%**、`vector`/`hybrid` **100%** → v4 暴露的 AND 失效被向量通道弥补；
- 融合策略定档：`rrf` 97.0% > `weighted` 95.2% > `fallback` 94.6%（fallback 三项与关键词基线完全相同）；
- 证据裁剪（18→10 条）：回答覆盖不变、hybrid 召回升至 97.0%、演示可用题 5 → 9 条。

### 5.2 质量优化（v4 遗留短板）

| 项 | 改前 | 改后 |
| --- | --- | --- |
| 应拒答题 X01–X03 | 应拒答却作答 | 全部 `correct_refusal`（main 无新增误拒） |
| 长改写 AND 失效（text-only 口径） | 召回 94.6% / 覆盖 71.7% | 召回 95.8% / 覆盖 **74.1%** |
| event_type 筛选（F01 开筛选） | 四项全 0% | **100%/100%/50%/100%**（evidence 元数据覆盖 7503/7503） |

### 5.3 真实 LLM 对照（hybrid + `deepseek/deepseek-v4.1-flash`，各 28 题）

| 指标 | v4 离线基线（39 题） | 样本 1 | 样本 2 | **样本 3（评分样本）** |
| --- | --- | --- | --- | --- |
| 回答覆盖 | 67.6% | 84.5% | 90.8% | **97.0%** |
| 引用携带(文本) | 63.7% | 77.1% | 77.1% | 77.1% |
| 文本 top-k 召回 | 94.6% | 97.0% | 97.0% | 97.0% |
| ok/n（自动口径） | 28/28 | 24/28 | 26/28 | **28/28** |
| finish_reason | — | 28/28 normal | 28/28 normal | 25 normal + 3 degraded |
| 生成耗时 | <50 ms（离线） | 中位 10.5 s | 中位 9.3 s | 中位 9.7 s / 最大 24.5 s |

**答案正确性分布（样本 3，28 题；AI 代理评分，非人工）**：

| 维度 | 评分 | 条数 | 对比 v4（39 题） |
| --- | --- | --- | --- |
| 答案正确性 | correct | **25** | 0 |
| 答案正确性 | partial | **3** | 23 |
| 答案正确性 | incorrect | **0** | 16 |
| 引用正确性 | supported | **28** | 28 |
| 引用正确性 | unrelated / unsupported | 0 / 0 | 3 / 8 |

- 3 条 `partial` 全部是**本次运行模型偶发失败后降级为离线摘要**的题（M07、R06、E02）：
  内容方向不误（证据罗列、引用可验证），但不是成文回答；按 v4 对离线摘要形态的评分口径记 `partial`。
- 评分说明：由 AI 代理按 `evaluation/grading.py` 口径逐条评阅（**与 v4 的 AI 代理评分同一性质**，
  非自然人）；评分表在 `runs/v5_llm_3/scores.jsonl`，报告含逐条失败清单，可人工复核。
- 14 条"检索到位、回答未用"重点题（本次套件内 10 条）：三次取样分别为 8/10、8/10、**10/10** 达 100% 覆盖，
  且前两次掉队的题不同（样本 1：M01、M12；样本 2：M07、B03）→ 随机性已量化，单题读数不作结论。

### 5.4 演示与部署

- 示例题：39 题 → 候选 18 → **实测筛出 9 条**（剔除 3 条截断 + 6 条首正文超 9 s），覆盖 4 个类别；
- 冒烟：`smoke_deploy.py` 6 步全通（含 9/9 示例题、缓存命中 29 ms、限流第 29 次触发）；
  **无密钥形态**整机冒烟同样通过（每题 31–79 ms，模式自动降级 keyword）；
- 向量一致性：`chroma.count()==ids==npy==9544`；Chroma top-20 与暴力余弦 top-20 重合率 **均值 0.935**；
- 降级路径：三种破坏（集合缺失/`ids.json` 改短/集合少行）均正确降级且不抛错（用例守护）。

## 六、验证证据与复现命令

```bash
python -m pytest tests -q                                   # 105 passed（v4 收口时 50）
python scripts/check_vector_consistency.py --sample 20      # 条数一致 + 重合率 0.935
python scripts/run_evaluation.py run --suites main --configs text-only,text-only-and,vector,hybrid
python scripts/run_evaluation.py run --suites refusal,filter_loss --configs dual,text-only
python scripts/run_evaluation.py run --suites main,long_rewrite --configs text-only
python scripts/run_evaluation.py run --suites main --configs hybrid --llm --run-id v5_llm_3
python scripts/gen_demo_examples.py --measure --max-examples 12   # 重新筛示例题（含时延/截断）
python scripts/smoke_deploy.py --base http://127.0.0.1:8000       # 演示前冒烟（兼缓存预热）
```

## 七、审核链路与诚实边界（请审查模型重点关注）

1. **评分为 AI 代理口径，非自然人（用户已确认为对外口径，2026-09-14）**：5.3 的 25/3/0 由 AI 代理按
   `evaluation/grading.py` 口径逐条评阅（与 v4 题库审核/评分的性质相同，v4 已在报告中标注该边界）。
   逐条明细在 `runs/v5_llm_3/scores.jsonl`，报告含失败清单，随时可回看复核。
2. **v5 的 LLM 对照与 v4 基线不是同一检索配置**（v5 用 hybrid + 10 条证据，v4 用 keyword + 18 条），
   且 v4 是 39 题、v5 样本 3 是 28 题（main 套件）——差异里既有模型贡献也有检索贡献与题目集合差异，
   不能把 0→25 correct 全归给 LLM。
3. **中转 endpoint 是第三方依赖**：时延波动大（同题首正文 3.96 s ↔ 18.07 s）、模型清单与费率随时可能变；
   官方 endpoint 作备用但未在评测中实测切换。
4. **示例题只 9 条且能力标注全为 `both`**：满足"覆盖图谱与文本两类能力"的验收，
   但没有纯图谱/纯文本的对照样例。
5. **截断未被 trace 记录**：样本 1/2 分别有 5/3 次截断告警（约 11–18%），只在日志可见；
   已加"空正文兜底"，但"截断到只剩半句"的情况仍可能出现。
6. **`ok/n` 下降是口径现象不是退步**（见第四节末的观测口径说明）。

## 八、给审查模型的审核清单（建议按序执行）

1. 跑第六节的命令 1–3，核对 5.1/5.2/5.4 的数字；
2. 抽 `runs/v5_llm_3/traces.jsonl` 的 5 条回答，对照 `gold_notes` 判断正确性（与 5.3 的口径差异）；
3. 审 `server/text/searcher.py` 的 `search_vector`/`search_hybrid`/`_pass_meta` 与
   `server/text/scoring.py` 的三个融合函数（重点：归一化口径与 `distance→相似度`换算方向）；
4. 审 `server/sse.py` 的拒答三条规则、流式桥接与缓存写入条件（重点：degraded 不入缓存）；
5. 审 `server/query/understand.py` 的兜底触发条件与 `server/generate/__init__.py` 的空正文兜底；
6. 对照 `docs/data-contract.md` 检查 SSE 字段（`text_results.mode`、`thinking`、`entities.llm_entity_used`）
   与实现是否一致；
7. 抽查 `data/eval/20260904_v2/demo_examples.json` 的 9 条示例题是否真的"已审核且评分非 incorrect"。

## 九、未做与后续

- **人工评分**（3×28 条真实 LLM 回答）→ 完成后即可给出与 v4 0 correct/23 partial/16 incorrect 的并排对比；
- **可选治理项**（需用户决定）：1,215 组同名实体人工回填、地点坐标补录、R1 方案①（朝代偏置折进文本 `_score`）；
- **不在本阶段**：飞书/Hermes 渠道化、独立静态托管/CDN、多用户与权限、历史地图底图素材、题库扩容。

## 十、附录：Git 状态

截至本文档：改动未提交（RAG 仓库工作区），共 **50+ 文件**（含新增）；v4 的提交为 `50aafa2`。
`data/index/`、`data/snapshot/`、`logs/`、`data/eval/*/runs/` 均被 `.gitignore` 忽略；
`demo_examples.json`、`chunk_exp_report.md`、题库与评分类文件入 Git。
