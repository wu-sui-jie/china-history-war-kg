# RAGv2 开发说明：在线问答链路（F02–F06）

- 阶段：RAGv2（在线问答主链路）
- 状态：已完成 ✅
- 完成时间：2026-09-04
- 范围：`docs/README.md` 建议开发顺序第 1 步后半段——服务骨架 + F02→F03/F04→F05→F06 在线问答链
  以 SSE 暴露；F09 治理增强（人工审核回填）随阶段落地
- 前置：RAGv1 已完成（快照 + 索引 `20260904_v2`）

## 一、本阶段做了什么

在 RAGv1 的快照（实体/关系/词典/事件卡片）与索引（FTS5）之上，实现请求时运行的问答链，
提供 `POST /api/query` SSE 流式接口（FastAPI + uvicorn），打通
**用户输入问题 → 收到带引用与过程事件、可增量阅读的回答**。

无外部模型密钥时链路完整可用（F02 词典/规则识别为默认路径、F04 关键词检索、F06 离线
摘要回答器），因此"演示/验收"不依赖 deepseek-v4-flash 是否可用；配置密钥后 F06 切换为
真实流式生成（见"边界与未做事项"第 2 条）。

### 完成的功能点

1. **服务骨架与运行时**
   - `scripts/run_server.py` 启动 FastAPI + uvicorn，`GET /api/health` 健康检查；
   - `server/runtime.py` 启动时加载快照与索引并校验版本一致（manifest.source_snapshot），
     组装 F02–F06 各层；
   - 基础限流（进程内滑动窗口，按 IP 每分钟配额，`.env` `RATE_LIMIT_PER_MINUTE`）。
2. **F02 问题理解与实体识别（`server/query/`）**
   - 词典/规则优先识别：实体标准名+别名贪心最长匹配、事件"XX之战"简称兜底
     （"牧野之战"→"周武王灭商牧野之战"）；
   - 同名歧义降级：同一 mention 命中多实体时**全部进 candidates**（带 dynasty/event_type/
     entity_id），entities 取首选；不等待人工审核；
   - 多轮指代消解：结合 history 把"它/这场战争"还原为上一轮实体并改写问题；
   - 问题类型判定规则（single_entity/relation/event_event/comparison/timeline/background/
     unknown）；朝代过滤器从问题中抽取进入 filters；
   - `corrected_entities` add/replace/remove 纠正重查。
3. **F03 图谱检索（`server/graph/`）**
   - 启动加载 `entities.json + relations.json` 为内存图（邻接索引，排除 pending_review 边）；
   - 按问题类型执行：single_entity 属性+1 跳 / relation 邻接 / event_event 事件关系+两跳路径 /
     comparison 各跳 / timeline / background 实体上下文；
   - 输出 graph_triple 证据 + 命中实体（供 F05 panel），孤立节点不虚构关系。
4. **F04 文本检索（`server/text/`）**
   - 读 `chunks_fts.db`（FTS5），**AND 优先、OR 兜底**（OR 剔除单字虚词控噪）；
   - min-max 归一化 score 到 0~1；事件卡片 content 携带结构化字段（data-contract L239）；
   - 向量可用性校验：`len(ids) == embeddings.shape[0]`；当前占位态（(0,0)）→
     `vector_available=False` 自动降级关键词（代码检查，非仅风险文字）。
5. **F05 证据融合与重排（`server/fusion/`）**
   - 合并图谱+文本证据、按 (subject,relation,object) 去重；按问题类型加权分配名额
     （关系问题高图谱、背景问题高文本）+ 文本保底；图谱多值关系组内裁剪；
   - 统一分配 citation_index；
   - **冲突判定**：different_object（仅 exact 单值组发起方/防守方，避免"多值并列"误报）
     + field_vs_triple（读 `relation_card_field_map.json`，persons 等字段缺失跳过不判冲突，
     event_event/unknown 组不参与，同 (subject,字段) 去重只报一条）；
   - **panel 装配（唯一装配方）**：entity_cards/subgraph/timeline(按朝代分组，时间不详归末组)/
     map_points（无坐标地点不进图，降级由前端做）。
6. **F06 证据溯源回答生成（`server/generate/`）**
   - 提示词构造（system 含证据块+引用编号；防泄露规则与固定回复）；
   - LLM 客户端（OpenAI 兼容，超时/重试/备用降级）；无密钥 → 离线摘要回答器
     （把融合证据组织为带引用编号的回答），保证链路端到端验收；
   - 拒答路径：无证据 / 实体空且文本与问题无共享词 → finish_reason=refused；
   - 回答缓存：键=rewritten+历史摘要+filters+数据版本+模型；命中按 SSE 回放
     session_start→status(entity_linking)→entities→status(cache_hit)→answer→
     citations→panel→done（panel 一并缓存，无重复调用模型）。
7. **SSE 编排（`server/sse.py`）**
   - 事件顺序严格按 data-contract：session_start→status(entity_linking)→entities→
     status(graph_search)→graph_results→status(text_search)→text_results→status(fusion)→
     fusion→status(generating)→answer 增量（按句子切段模拟流式）→citations→panel→done；
   - 每事件带 session_id/stage；error 事件打印 traceback 便于排查；
   - finish_reason：normal/refused/degraded/cancelled。
8. **F09 治理增强（人工审核回填，`data/snapshot/apply_audit.py` + `scripts/apply_audit.py`）**
   - 定义 `audit_decisions.json` 回填契约（source_version/operator/decisions，
     每条含 action/实体/处理前后/operator/conclusion/confidence，对应 F09 人工审核要求第 5 条）；
   - 支持 add_alias / remove_alias / merge；事件合并需显式 allow_event_merge（防误伤）；
     回填生成**新版本快照** + 报告 audit_applied 明细（不覆盖源版本），随后提示重建同版本索引。

## 二、功能 ↔ 文件/文件夹映射

| 功能 | 目录/文件 | 说明 |
| --- | --- | --- |
| 服务入口/健康/限流 | `server/api.py` | FastAPI app |
| SSE 编排 | `server/sse.py` | run_query 事件序列 |
| 启动加载/版本校验 | `server/runtime.py` | build_runtime |
| 启动脚本 | `scripts/run_server.py` | uvicorn 启动 |
| F02 问题理解 | `server/query/` | understand / dictionary_matcher / classifier |
| F03 图谱检索 | `server/graph/` | graph_index / search / query_strategies |
| F04 文本检索 | `server/text/` | searcher / scoring |
| F05 融合重排 | `server/fusion/` | fusion / conflict / panel_builder |
| F06 回答生成 | `server/generate/` | prompts / llm_client / refusal / cache |
| 跨层契约补充 | `contracts/retrieval.py`、`request.py`(from_dict/CandidateOption 扩展)、`__init__.py` | F04/F05 输出信封等 |
| 配置扩展 | `config/defaults.py` `settings.py` `.env.example` | LLM/限流/缓存/top-k |
| F09 回填 | `data/snapshot/apply_audit.py`、`scripts/apply_audit.py` | 人工审核回填 |

## 三、跑通结果（验收对照）

启动：`python scripts/run_server.py`（Python 3.11 + fastapi/uvicorn/openai，安装见 requirements）。

| 验证项 | 结果 |
| --- | --- |
| SSE 事件序列 | session_start→status→entities→…→answer(多段)→citations→panel→done ✅ |
| 正常问题（"赤壁之战的主帅是谁"） | done normal；citations/panel 完整；回答引用与证据对应 ✅ |
| 多轮指代（先问"长平之战…"再问"它的主将是谁"） | entities=[长平之战]、rewritten=长平之战的主将是谁 ✅ |
| 同名歧义（井陉之战战国/西汉） | entities 取首选 + candidates 含 2 个 option（带 dynasty）✅ |
| 简称兜底（"牧野之战"） | 识别为"周武王灭商牧野之战"（medium）✅ |
| 无实体+检索不相关（"巴黎埃菲尔铁塔…"） | finish_reason=refused（依据不足）✅ |
| 回答缓存 | 二次同问题 → session_start→status(entity_linking)→entities→
  status(cache_hit)→answer→citations→panel→done；复核修复后 panel 完整 ✅ |
| 运行时版本一致性 | 快照/索引均为 20260904_v2 ✅ |
| 非功能 | 本地关键词+图谱检索 SSE 首事件 ~10ms、整链 30~50ms（无 LLM）；
  首 Token 预算需接 LLM 后按 F10 实测 |
| F09 回填 | add_alias 生效入 aliases；merge 事件被安全拦截（需 allow_event_merge）✅ |

演示问题集（在 `docs/features` 建议的 F02 验收层面）跑通样例：井陉之战/牧野之战/涿鹿之战/
阪泉之战/戚继光/洛阳/长平之战/赤壁之战 实体识别均正确，图谱与文本证据可返回。

## 四、数据特征发现（本期新增，写入代码注释供 F10 参考）

1. **多值关系 ≠ 冲突**：旧库"统帅/将领/君主/谋士"等实为多个并列事实行（白起/王龄/廉颇
   同为长平之战将领），不是矛盾。因此 different_object 冲突只对 exact 单值组
   （发起方/防守方）生效；多值并列保留为多条证据。此规则已按 RAGv1 数据特征落成代码，
   若 F10 评测出现真实矛盾再放开。
2. **persons 字段覆盖仅 37.5%**：event_card 无 persons/为空时跳过该条比对（"卡片没写"
   ≠"与图谱不一致"），已作为通用规则落在 conflict.py，避免误报洪峰。
3. **同名实体极普遍**：井陉之战×2、阪泉地点×3、洛阳地点×40（跨朝代）。F02 歧义降级
   与 candidates 按 dynasty/event_type 区分是必要路径；人工审核回填（apply_audit）是后续
   治理手段而非前置。
4. **"牧野之战"式口语简称**：标准事件名是"周武王灭商牧野之战"，词典不含简称；
   需事件全名包含匹配兜底（置信度 medium）。

## 五、边界与未做事项（诚实说明）

1. **F02 LLM 兜底未启用**：规划中"词典未命中 → 调 deepseek-v4-flash"本阶段留了
   `enable_llm` 开关但默认关闭（本机无 deepseek key）。词典/规则默认路径已覆盖演示问题；
   口语化复杂句命中率待 F10 评测后决定是否开 LLM。
2. **F06 离线摘要回答器**：无 LLM 密钥时用启发式把融合证据排成可读回答（model_used=
   heuristic-offline，finish_reason=normal）。这是演示降级，不是规划定义的 degraded
   （degraded 保留给"配置了主模型但调用失败切换备用模型"）。接入真实 key 后自动切换。
3. **向量模式未落地**：F11 仍为占位（ids 有、矩阵 0 行）；F04 `vector_available=False`
   自动关键词。接云端 embed 后须全量重建（`INDEX_BUILD_EMBEDDINGS=true` 时 vectors.build_vectors
   注入 embed_fn），再验证 `len(ids)==shape[0]`。
4. **answer 流式**：当前按句子切段模拟增量；接真实 LLM 后改为按 token 增量事件。
5. **限流为进程内实现**：多进程/多机部署需换 Redis 或网关限流。
6. **回答缓存为进程内 TTL**（`CACHE_TTL_SECONDS`）：演示足够；多实例需共享存储。
7. **F03 图谱检索深度**：event_event 两跳路径已实现，未做更深的路径枚举
   （F03 待确认项：默认 1~2 跳，按 F10 结果再调）。
8. **未写自动化测试**（`tests/` 仍预留）：v2 以脚本端到端 SSE 冒烟 + 本表核对代替，
   与 v1 保持一致。

## 六、如何复现

```bash
# 环境：Python 3.11（本机用 E:/anaconda/envs/AI_Agent，已含 fastapi/uvicorn/openai）
cd RAG
# 无 LLM 密钥也能跑完整检索链（F06 用离线摘要回答器）
python scripts/run_server.py --port 8000

# 另开终端，冒烟 SSE（示例问题）
curl -N -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'

# 健康检查
curl http://127.0.0.1:8000/api/health

# 接入真实模型：在 RAG/.env 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 后重启
# F09 人工审核回填（示例）
python scripts/apply_audit.py --decisions my_audit_decisions.json
```

## 七、下一阶段 RAGv3 要做什么（规划）

前端页面 F01 问答主界面 + F07 知识面板（Vue3+TS+Vite），消费本阶段 SSE 事件流与 panel 数据；
F10 评测体系与问题题库；F08 演示模式。参见 `docs/README.md` 阶段索引。

## 八、第三方复核修复记录（2026-09-04）

第三方复核发现 2 个高优先级问题，本次已修复并复测：

1. **缓存命中缺 panel**：修复前二次同问事件序列无 `panel`。现在缓存 payload
   保存 panel（`server/generate/cache.py::build_cache_payload`），
   命中分支补发 `panel` 后 `done`（`server/sse.py`）。
2. **filters 未传给文本检索 / event_type 无法过滤**：修复前 `server/sse.py`
   对文本检索硬编码 `filters=None`，且 FTS 索引 chunks 表没有 event_type 列。
   现在：sse 传入 filters；`data/index/fts.py` 的 chunks 表冗余 event_type；
   `server/text/searcher.py` 做真实 event_type 过滤，并已重建 `20260904_v2` 索引。
3. 顺带修复：`history_max_turns` 在 `server/query/understand.py` 中生效，
   按最近 N 个 user 轮裁剪历史。

详细需求/总结文档见 `docs/changes/`。
