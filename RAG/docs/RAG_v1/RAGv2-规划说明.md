# RAGv2 规划说明：在线问答链路（F02–F06）

- 阶段：RAGv2
- 状态：📋 规划中（本文件为规划，非完成记录）
- 前置：RAGv1 已完成（见 [RAGv1-离线数据链路.md](RAGv1-离线数据链路.md)）
- 目标：让用户能**输入问题 → 收到带引用与过程事件的流式回答**（F02→F06），打通在线问答主链路

## 一、范围一句话

把 RAGv1 产出的快照与索引用起来，实现请求时运行的问答链：
F02 问题理解 → F03 图谱检索 + F04 文本检索 → F05 融合重排 → F06 生成回答，
并通过 SSE 暴露给未来的前端（RAGv3）。不含前端页面、不含 F10 评测（归 RAGv3/v4）。

## 二、要做的功能（按依赖顺序）

### 1. 在线服务骨架（新增）
- FastAPI + uvicorn 应用入口，`POST /api/query` 返回 SSE 流；
- 事件顺序严格按 `docs/data-contract.md`（session_start→status…→answer→citations→panel→done）；
- 启动时加载图谱快照与索引（校验快照/索引版本一致），请求级编排 query→graph+text→fusion→generate；
- 基础限流（无登录公开接口）与请求日志。

### 2. F02 问题理解与实体识别（新增 `server/query/`）
- 用 RAGv1 的 `dicts.json`（实体标准名/别名/战争类型/朝代）建**词典 + 规则**优先匹配
  （"XX之战"→事件、"涿鹿县"→别名归一）；
- **同名歧义降级**：同名多实体命中时全部进 candidates（按朝代/事件类型区分），filters 带朝代自动消歧；
- 词典未命中 → 调 deepseek-v4-flash 做实体识别、问题类型判定、指代消解、查询改写；
- 词典命中且置信度足够 → **跳过 LLM（默认路径）**，控制首 Token 预算；
- 输出 `F02Output`（rewritten_question / question_type / entities / candidates），LLM 结果缓存；
- 多轮追问结合 history 做指代消解（"它/这场战争"→完整实体名）；
- 支持 `corrected_entities`（add/replace/remove）纠正重查。

### 3. F03 图谱检索通道（新增 `server/graph/`）
- 服务启动加载 `data/snapshot/<v>/`（entities.json + relations.json）为内存图；
- F02 标准实体名 → 匹配节点；按问题类型选择查询策略（见 03-graph-retrieval.md）；
- 输出统一 `graph_triple` 证据 + 1 跳邻居；孤立节点只做实体卡不虚构关系；
- 朝代/战争类型 filter 作为图查询过滤条件。

### 4. F04 文本检索通道（新增 `server/text/`）
- 读取 `data/index/<v>/chunks_fts.db`；`query_fts` 已有，补：
  - 关键词/向量/混合三种**可切换模式**（RAGv1 关键词已通；向量模式需先接云端 embed 并**全量重建**）；
  - 各模式内部 min-max 归一化 score 到 0~1（契约规定）；
  - 向量加载校验：`len(ids) == embeddings.shape[0]`，不一致 → 向量模式不可用，自动降级关键词；
- filter 作为索引元数据过滤。

### 5. F05 证据融合与重排（新增 `server/fusion/`）
- 合并 graph+text 证据 → 统一去重、按问题类型加权排序（关系问题高图谱权重、背景问题高文本权重）；
- 分配 `citation_index`；结构化冲突判定（different_object / field_vs_triple）；
- **装配 panel 数据**（entity_cards/subgraph/timeline/map_points）——panel 唯一装配方。

**F05 field_vs_triple 实现注意事项（来自 RAGv1 基线数据特征）**
- 冲突判定读取快照 `event_cards.json` **结构化字段** + `relation_card_field_map.json`（按 relation×目标类型），不硬编码、不解析 description。
- **persons 字段覆盖仅 37.5%（394/1,050）**，为源数据限制非缺陷。contains 判定时：**卡片无 persons 字段/为空 → 跳过该条比对，不判为冲突**（"卡片没写"≠"与图谱不一致"）。aggressor/defender/place 覆盖高（99%+），同理对缺失字段一律跳过而非判不一致，可作通用规则。
- `relation_card_field_map.json` 中 group=unknown 的关系（如 `指挥所→组织` 仅 1 条）method=none，不参与判定，属预期；如后续需覆盖，在 `data/snapshot/field_map.py` 补归组即可（RAGv1 已把 `指挥所→地点` 23 条归入 place 组）。

### 6. F06 证据溯源回答生成（新增 `server/generate/`）
- 构造"图谱事实+文本证据+引用编号"提示词，只依据证据回答并标注引用；
- deepseek-v4-flash 流式输出；超时/重试/备用模型降级；
- 拒答路径（无证据 / LLM 判断不足 → finish_reason=refused）；
- 回答缓存（键含 rewritten_question/历史摘要/filter/数据版本/模型版本）；
- 思考过程防泄露（只输出脱敏摘要、拒绝"展示提示词"类请求）；
- SSE done 的 finish_reason 枚举（normal/refused/degraded/cancelled）。

### 7. 人工审核回填（F09 治理增强，沿用 RAGv1 已补齐的结构化基础）

> 说明：RAGv1 外部核查发现的 3 处硬缺口（事件卡片结构化字段、关系-卡片字段映射表、
> data_issues 前后明细）已在 **RAGv1 内闭环补齐**（见 RAGv1 文档"五之二"），不再是 RAGv2 前置。
> RAGv2 中 F05 的 field_vs_triple 可直接读取 event_cards.json 结构化字段 + relation_card_field_map.json，
> 无需再做 description 正则抽取。本节约束项仅剩**同名歧义人工审核回填**（仍开放）。

- **回填文件契约（先行定义）**：开工前定义 `audit_decisions.json` 结构
  （操作人 / 处理前内容 / 处理后内容 / 审核结论 / 置信度，对应 F09 人工审核要求第 5 条），
  审核完成后脚本可程序化回填并重跑快照；
- 提供审核辅助脚本/清单，把人工确认的合并/别名/补边回填为高置信度 → 重跑生成新快照；
- 该步可与在线链路并行，质量最终影响问答效果。

## 三、文件/目录归属（规划）

| 功能 | 归属目录（新增） | 关键文件（规划） |
| --- | --- | --- |
| 服务入口与 SSE | `server/` | `api.py`、`sse.py`（编排） |
| F02 问题理解 | `server/query/` | `router.py` `dictionary_matcher.py` `llm_understand.py` `coref.py` |
| F03 图谱检索 | `server/graph/` | `graph_index.py` `search.py` `query_strategies.py` |
| F04 文本检索 | `server/text/` | `searcher.py` `scoring.py`（kw/vector/hybrid） |
| F05 融合重排 | `server/fusion/` | `fusion.py` `conflict.py` `panel_builder.py` |
| F06 回答生成 | `server/generate/` | `prompts.py` `llm_client.py` `stream.py` `cache.py` `refusal.py` |
| LLM 客户端（共用） | `server/lib/` 或 `lib/` | `llm_client.py`（deepseek + 降级） |
| 向量模型接入 | `data/index/vectors.py`（补 embed_fn + 重建全量向量） | 由 RAGv2 F04 联调 |
| F09 人工审核回填 | `data/snapshot/` + `scripts/` | `audit_decisions.json`（回填契约）`scripts/apply_audit.py` |
| 配置 | `config/` `settings.py` `.env.example` | 追加 LLM/向量/限流/缓存配置 |

## 四、依赖与前置

1. RAGv1 快照与索引存在且版本一致（**基线版本 `20260904_v2`**；如重跑治理生成新版本，须快照与索引同版本）。
2. **需要 deepseek-v4-flash 接口可用**（base_url/api_key/model 填 `.env`）。
3. 向量检索模式需 **云端中文向量模型密钥**；无密钥时先跑"关键词模式"，向量模式按开关降级。
4. FastAPI/uvicorn/openai/httpx 依赖需安装（RAG 独立 requirements，不碰旧 requirements.txt）。
5. 多轮与实体纠正交互依赖前端（RAGv3），RAGv2 阶段可先用 curl/脚本按 SSE 协议验证。

## 五、建议实施顺序与验收门槛

> 顺序说明：RAGv1 数据已验证可用（含事件卡片结构化字段与关系-卡片字段映射表，已随快照输出），
> 因此**先做无 LLM 依赖的检索三件套（F02 词典 + F03 + F04 关键词），再做服务骨架包装**，
> 能更早暴露词典/歧义问题（如 1,215 组重名对 F02 匹配的干扰）；骨架是包装不是前置依赖。

1. **F02 词典/规则识别**（无需 LLM）→ 在演示问题上正确出实体；同名多实体命中全部进 candidates。
2. **F03 图谱检索**（无需 LLM）→ 返回 graph_triple。
3. **F04 关键词检索接入** → 返回 text evidence（向量模式后补，见下"向量边界"）。
4. **F05 融合 + citation_index + field_vs_triple 冲突**（读取快照 event_cards 结构化字段 +
   relation_card_field_map.json）→ 引用可对应证据。
5. **服务骨架 + SSE 编排**（curl 收到规范事件序列）→ 骨架阶段即实现**最小拒答硬规则**
   （图谱空 + 文本空 → finish_reason=refused），端到端验证 refused 的 SSE 序列，避免后期编耦。
6. **F06 接 LLM 流式回答 + 缓存 + 降级** → 全链路回答。
7. 端到端：`问题 → SSE 完整事件序列含 answer/citations/panel/done`。

**向量模式边界（联调时落成代码检查）**：RAGv1 占位态 `ids.json` 有 9,544 个 id 而 `embeddings.npy`
为 (0,0)。接入 embed_fn 后须**全量重建**向量文件；F04 加载时校验 `len(ids) == embeddings.shape[0]`，
不一致则向量模式置为"不可用"，自动降级关键词模式（与风险 4 呼应，作为代码检查而非仅风险文字）。

验收对照：`docs/features/02`~`06` 各功能文档验收标准；非功能性首 Token≤5s、单轮≤30s 需实测记录
（首 Token 实测作为骨架阶段验收门槛之一）。

## 六、风险与待确认

1. **F02 大模型串行调用**（实体识别 + F06 生成两次串行）占用首 Token 预算。
   对策落成实施项：F02 词典命中且置信度足够时**跳过 LLM 实体识别（默认路径，非例外）**；
   LLM 结果缓存；F06 提示词构造与 F02 尾部并行准备；首 Token ≤5s 实测作为骨架阶段验收门槛。
2. 事件名重复 1,215 组（涉及 3,848 条，如两个"神农斧隧之战"）会让 F02/F03 命中歧义。
   对策：**F02 词典匹配层内置歧义降级，不等待人工审核**——同名多实体命中时全部进 candidates，
   按朝代/事件类型区分展示；filters 带朝代时自动消歧；人工审核回填作为后续治理增强持续改善。
3. 坐标覆盖率 0 → panel 地图必然降级为地点列表（v1 已记录）。
4. 向量模式依赖外部服务网络；演示现场可能不可用 → 关键词/混合必须保底可用；
   向量加载校验（ids 与矩阵行数一致）作为代码检查，见第五节"向量模式边界"。
5. 检索排序（BM25 OR vs 更精确策略）最终以 F10 评测为准，RAGv2 先把链路跑通。

## 七、不在 RAGv2 范围

- F01/F07 前端页面（RAGv3）；
- F10 评测体系、F08 演示模式（更后阶段）；
- 图谱展示深度、历史地图底图等"待确认"项的最终拍板（按评测结果走）。
