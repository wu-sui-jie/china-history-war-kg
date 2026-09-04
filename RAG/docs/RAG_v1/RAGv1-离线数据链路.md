# RAGv1 开发说明：离线数据链路（F09 + F11）

- 阶段：RAGv1（离线数据底座）
- 状态：已完成 ✅（含外部核查 3 处缺口的闭环补齐）
- 完成时间：2026-09-04
- 范围：`docs/README.md` 建议开发顺序的第 1 步前半段——F09 基础快照 → F11 基础切分与关键词索引
  （补齐事件卡片结构化字段、关系-卡片字段映射表、data_issues 明细）

## 一、本阶段做了什么

在旧项目（SQLite + Neo4j + Flask/Vue，只读）之上，新建自包含的 `RAG/` 子项目，
打通 **F09 数据快照与治理 → F11 文本切分与关键词索引** 的整条离线数据链路，
并产出可被下一阶段（RAGv2 在线检索）直接消费的**带版本号的干净快照 + 文本索引**。

本阶段是纯离线、无外部模型依赖（向量部分已留接入点但默认占位），
不包含任何在线接口/前端页面（那些属于 RAGv2/RAGv3）。

### 完成的功能点

1. **目录骨架与项目约定**：按架构分层切目录、每层一份开发 README、F 编号只在文档与注释中追踪。
2. **配置体系**：路径默认值 + `.env` 环境变量覆盖 + 治理/索引开关；密钥不硬编码（`config/`）。
3. **共享数据契约**：把 `docs/data-contract.md` 落成唯一权威 Python 类型（证据/SSE/panel/冲突/词典/片段），各层引用不复制。
4. **F09 数据治理**：
   - 只读导出旧 SQLite 四类实体 + 事件 + 四类关系 + 旧原文文本；
   - 战争类型归一（可配置，不做无依据强合并）、朝代归一（修复"上古传说战争被误标夏"等 data issue）；
   - 低风险别名归一（现代地名→标准名），同名歧义分组输出待人工审核清单；
   - 孤立节点统计与标记（全部保留不删除）；
   - 事件卡片、关系证据语料拼装；
   - **事件卡片结构化字段（补齐后）**：卡片含独立结构化字段 aggressor/defender/persons/place/
     action/result/impact/scale（满足 data-contract L239"参与方/地点/结果等字段"），
     同时保留 description 富文本供切分与展示；
   - **关系-事件卡片字段映射表（补齐后）**：随快照输出 `relation_card_field_map.json`
     （data-contract L406-419），按 (relation, 目标实体类型) 细分（54 组合全覆盖），
     供 F05 field_vs_triple 冲突判定读取；
   - **data_issues 前后明细（补齐后）**：朝代修正逐条携带 before/after 字段快照与
     audit_status，满足人工审核可追溯；
   - 生成标准词典（战争类型/朝代别名/现代地名映射/事件类型映射）；
   - 每次治理生成新版本号（`YYYYMMDD_vN`），输出 `governance_report.json` 治理报告。
5. **F11 文本切分与索引**：
   - 三类语料（原始正文 raw / 事件卡片 event_card / 关系证据 evidence）统一切分；
   - 原文按 段落→句子 边界 + 超长重叠切分；事件卡片以事件为最小单元；证据保留可引用短文本；
   - 每条片段保留 doc_id/chunk_type/event 等元信息，可回溯来源；
   - SQLite FTS5 关键词索引 + jieba 中文分词（实体名入词典），`query_fts` 可中文检索；
   - 向量索引目录结构占位（云端 embed 接入点预留，`INDEX_BUILD_EMBEDDINGS` 开关控制）。
6. **一键脚本**：`export_snapshot.py`（F09）、`build_index.py`（F11）、`run_pipeline.py`（F09+F11 串联）。

## 二、功能 ↔ 文件/文件夹映射

### 项目结构总览（RAG/ 根 README）

```text
RAG/
├── docs/                     # 需求/功能文档（只读）
├── README.md                 # 代码组织与开发约定（根）
├── .env.example              # 环境变量模板
├── requirements.txt
├── config/                   # 配置加载
├── contracts/                # 共享数据契约
├── lib/                      # 无业务工具
├── scripts/                  # 离线任务入口（F09/F11）
├── data/
│   ├── snapshot/             # F09 治理层源码 + 产物(按版本)
│   ├── index/                # F11 索引层源码 + 产物(按版本)
│   └── raw/source_texts/     # 旧原文归一拷贝（产物，git 忽略）
├── server/                   # 在线层（F02–F06，RAGv2 占位）
├── frontend/                 # 前端（F01/F07，RAGv3 占位）
├── logs/                     # 运行日志（git 忽略）
└── tests/                    # 测试（预留）
```

### F09 → 代码文件

| 功能点 | 文件 |
| --- | --- |
| 只读导出旧数据（sqlite 4 实体表+events+4 关系表、原文） | `data/snapshot/export.py` |
| 别名与名称归一（现代地名别名、同名歧义分组） | `data/snapshot/alias.py` |
| 战争类型归一、朝代归一（data_issue 带前后明细） | `data/snapshot/normalize.py` |
| 关系-事件卡片字段映射表（relation×目标类型细分） | `data/snapshot/field_map.py` |
| 孤立节点统计与标记 | `data/snapshot/isolate.py` |
| 治理编排（id 分配、词典、事件卡片、证据语料、报告、版本） | `data/snapshot/governance.py` |
| 治理版本号管理 | `lib/versions.py` |
| F09 一键入口 | `scripts/export_snapshot.py` |

### F11 → 代码文件

| 功能点 | 文件 |
| --- | --- |
| 三类语料加载与切分（raw/card/evidence、句子边界+重叠） | `data/index/chunking.py` |
| SQLite FTS5 + jieba 中文关键词索引 | `data/index/fts.py` |
| 向量索引构建（占位 + 云端 embed 接入点） | `data/index/vectors.py` |
| 索引编排（chunks.jsonl/FTS/向量/manifest/报告） | `data/index/build.py` |
| F11 一键入口 | `scripts/build_index.py` |
| F09+F11 串联一键入口 | `scripts/run_pipeline.py` |

### 跨层支撑 → 代码文件

| 功能点 | 文件 |
| --- | --- |
| dataclass 基类（to_dict 序列化） | `contracts/base.py` |
| 证据/来源/置信度/片段类型枚举 | `contracts/evidence.py` |
| 问题类型 | `contracts/question.py` |
| 查询请求/F02 输出/纠正实体 | `contracts/request.py` |
| SSE 协议（事件/阶段/完成原因/错误码） | `contracts/sse.py` |
| 知识面板数据结构 | `contracts/panel.py` |
| 冲突对象 | `contracts/conflict.py` |
| 治理输出结构（实体/关系/词典/字段映射表） | `contracts/governance.py` |
| F11 片段结构 | `contracts/index.py` |
| 契约汇总导出 | `contracts/__init__.py` |
| 默认配置 | `config/defaults.py` |
| .env 加载与设置合并 | `config/settings.py` |
| JSON 读写 / 日志 / 版本 | `lib/json_io.py` `lib/logging_util.py` `lib/versions.py` |

### v1 交付的全部文件清单（含辅助/结构文件）

> 上面三张表只列"有业务逻辑"的模块；下面把 v1 创建/编写的**每一份文件**列全，
> 与 `git status` 新增清单一一对应，便于核对是否有遗漏。

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `README.md` | 根文档 | 代码组织与开发约定（分层理由、目录说明、版本约定） |
| `.env.example` | 配置模板 | 全部环境变量与注释（密钥不入库） |
| `.gitignore` | 配置 | 忽略 data 产物/日志/缓存/密钥 |
| `requirements.txt` | 配置 | 离线链路 Python 依赖 |
| `config/__init__.py` | 包标记 | 空 |
| `config/README.md` | 模块开发说明 | config 层职责/使用/边界 |
| `config/defaults.py` | 源码 | 默认路径与开关 |
| `config/settings.py` | 源码 | .env 加载与设置合并 |
| `contracts/__init__.py` | 包导出 | 汇总导出全部契约类型 |
| `contracts/base.py` | 源码 | dataclass 基类（to_dict 序列化，剔除 None） |
| `contracts/conflict.py` `evidence.py` `governance.py` `index.py` `panel.py` `question.py` `request.py` `sse.py` | 源码 | 各契约类型（见"跨层支撑"表） |
| `contracts/README.md` | 模块开发说明 | contracts 层职责/约定 |
| `lib/__init__.py` | 包标记 | 空 |
| `lib/json_io.py` `lib/logging_util.py` `lib/versions.py` | 源码 | 工具 |
| `lib/README.md` | 模块开发说明 | lib 层职责/约定 |
| `data/__init__.py` | 包标记 | 空 |
| `data/snapshot/__init__.py` | 包标记 | 空 |
| `data/snapshot/export.py` 等 6 个 | 源码 | F09 治理层：export/alias/normalize/field_map/isolate/governance（见 F09 表） |
| `data/snapshot/README.md` | 模块开发说明 | F09 层职责/产物/设计决策/验收 |
| `data/index/__init__.py` | 包标记 | 空 |
| `data/index/chunking.py` 等 4 个 | 源码 | F11 索引层（见 F11 表） |
| `data/index/README.md` | 模块开发说明 | F11 层职责/产物/切分规则/验收 |
| `scripts/export_snapshot.py` 等 3 个 | 源码 | 一键入口（见 F09/F11 表） |
| `scripts/README.md` | 模块开发说明 | scripts 层用法/约定 |
| `server/README.md` | 模块开发说明 | F02–F06 在线层规划（RAGv2 占位） |
| `frontend/README.md` | 模块开发说明 | F01/F07 前端规划（RAGv3 占位） |

说明：
- **每个"模块文件夹"都配了一份 README 开发说明**（config/contracts/lib/data/snapshot/data/index/
  scripts/server/frontend 共 9 份），这是项目约定"一层一目录一份开发说明"的落地，也是本阶段交付物的一部分。
- `docs/` 下的功能需求文档（features/、architecture.md、data-contract.md）是既有需求文档，
  不属于 v1 新建代码；v1 新增的是 `docs/RAG_v1/`（本开发说明目录）与对 `docs/README.md` 的索引更新。

## 三、跑通结果（验收对照）

执行 `python scripts/run_pipeline.py`（自动生成版本 `YYYYMMDD_vN`，本次基线为 `20260904_v2`）：

| 产物 | 数量 |
| --- | --- |
| 实体总数 | 9,925（事件 1,050 / 地点 5,316 / 人物 2,492 / 组织 1,067） |
| 关系边 | 17,700 |
| 证据语料（事件-地点关系原文） | 6,849 |
| 原文文档 | 2 部（约 68.5 万字） |
| 文本片段 | 9,544（raw 934 / event_card 1,107 / evidence 7,503） |
| FTS5 可检索片段 | 9,544 |
| 事件卡片（含结构化字段） | 1,050（aggressor 1,049 / defender 1,044 / place 1,050 / result 1,027 / persons 394） |
| 关系-卡片字段映射表条目 | 54（真实 (关系,目标类型) 组合全覆盖） |
| data_issues 朝代修正（含 before/after 明细） | 5 |

中文检索实测（OR + BM25 召回优先策略）：
"赤壁之战 曹操"、"牧野之战 武王"、"戚继光 抗倭"、"涿鹿之战" 均能召回相关片段并按相关度排序。

对应 F09/F11 验收标准：
- 运行时不依赖旧服务是否启动 ✅（只读 SQLite 文件 + 原文，快照自包含）
- 报告含治理前后对比 / 版本号 / 来源可追溯 ✅（治理前后对比以报告内孤立/重名/类型分布为准）
- 孤立节点全部保留 ✅（3,844 个标记 `is_isolated`）
- 人工审核项存在 ✅（同名歧义 1,215 组 → `audit/duplicate_name_groups.json`）
- 战争类型词典与事件类型映射已生成 ✅（`dicts.json`）
- 无无法回溯来源的片段 ✅（每片段含 doc_id/source）
- 索引与快照版本一致 ✅（均为 `20260904_v2`）
- 事件卡片含结构化字段（data-contract L239）✅（补齐后）
- 关系-事件卡片字段映射表随快照输出（L406-419）✅（补齐后）
- data_issues 逐条前后明细与审核状态 ✅（补齐后）

**基线版本说明**：当前唯一保留产物为快照+索引 `20260904_v2`（RAGv1 最终基线，供 RAGv2 F03/F04 加载）。
早期同内容 `20260904_v1` 已删除，避免选错版本。若后续重跑治理生成新版本，须快照与索引同版本使用。

## 四、数据质量发现（如实记录，写入治理报告）

1. **上古传说战争被误标朝代"夏"**：涿鹿之战、阪泉之战、神农斧隧之战等 5 条按名称/时间特征
   判为上古传说，朝代修正为"上古"。修正记录写入报告 `data_issues`（不做静默覆盖），
   每条携带 before/after 完整字段快照与 audit_status（人工审核可追溯）。
2. **事件名重复 1,215 组**（涉及 3,848 条记录）：如两个"神农斧隧之战"（event_type/dynasty 不同）。
   v1 不做自动 merge，全部保留，输出待人工审核清单。**这是 RAGv2 治理增强的人工审核重点。**
3. **地点坐标覆盖为 0**：旧 places 无经纬度。地图模块在后续阶段必须降级为地点列表，
   治理报告已记录覆盖率为 0 供引用。
4. **事件类型 27 类大多语义不同**：v1 不强行合并；`战争`(20)/`议和`(1)/`政治事件`(1)
   等泛化/疑似非战争类列入报告"待人工确认"。
5. **关系溯源字段**：event_place_relations 自带 evidence 原文与
   confidence/source_type（全部为 medium/extraction），v1 已完整携带并转为证据语料。

## 五、边界与未做事项（诚实说明）

1. 未做**实体自动 merge/消歧落库**（仅分组报告）；未做高风险自动补边（需人工审核后开启）。
2. **向量索引为占位**（云端 embed_fn 未注入、无密钥），关键词检索已可用；向量/混合检索在 RAGv2。
   **占位态边界（如实）**：当前 `vectors/ids.json` 含 9,544 个片段 id，而 `embeddings.npy` 形状为
   (0, 0)——即"有 id、无向量"。RAGv2 接入 embed_fn 后须**全量重建**向量文件，并校验
   `len(ids) == embeddings.shape[0]`，不一致则向量模式不可用、自动降级关键词模式。
3. 不含任何**在线接口**（F02–F06）与**前端页面**（F01/F07）。
4. `tests/` 目录已预留但未写自动化测试（v1 以脚本端到端跑通 + 报告核对代替）。
5. F08 演示模式按约定暂缓。

> 注：外部核查曾指出的 3 处缺口（事件卡片结构化字段、关系-卡片字段映射表、data_issues
> 前后明细）已在 v1 内补齐，详见"五之二"修订 1-3 的处置结果。

## 五之二、外部独立核查结论与修订记录

> 本节记录 v1 完成后由外部模型对"文档 vs 交付"做的独立核查结论，以及据此的修订与**闭环处置**，
> 供后续阶段追溯"文档曾经夸大/偏差过什么"。

### 核查总体结论

文档与实际交付基本一致：代码文件清单、产物数字（9,925/17,700/6,849/9,544）、版本一致性、
孤立保留、歧义清单、FTS 检索、朝代修正等**全部核对属实**。核查另指出 3 处偏差，均已核实确认；
三处均在进入 RAGv2 前**于 v1 内闭环补齐**（见下）。

### 修订 1：事件卡片"结构化字段"表述夸大（已核实属实）

- 原文档/功能需求暗示事件卡片含结构化参与方字段；**实际** 1,050 张卡仅 7 字段
  （event_id/name/dynasty/event_type/start_date/description/source），发起方、防守方、地点、人物、
  结果全部以 `发起方：…` 行文本写在 description 内，无独立结构化字段。
- 影响：不满足 data-contract.md L239"事件卡片证据在 content 中额外包含事件名、朝代、时间、参与方、
  结果等字段"；F05 的 field_vs_triple（L406–419）在现卡片上无法直接运行。
- 处置：**已在 v1 内闭环**——`data/snapshot/governance.py` 的 `_build_event_cards` 改为同时输出
  结构化字段（aggressor/defender/persons/place/action/result/impact/scale）与 description 富文本；
  F05 可直接读卡片结构化字段做冲突判定，无需解析 description。
- 根因：v1 属范围裁剪（description 富文本先行），文档未把这条妥协写清楚——**文档偏差，非蓄意夸大**。

### 修订 2：dicts.json 无"关系-事件卡片字段映射表"（已核实属实）

- data-contract.md L419 要求"映射表由 F09 随治理快照输出，F05 冲突判定时读取，不在各模块硬编码"；
  **实际** dicts.json 仅 5 类词典（event_type_standard/map、dynasty_aliases、place_modern_map、
  place_name_aliases），无关系-字段映射表。
- 处置：**已在 v1 内闭环**——新增 `data/snapshot/field_map.py`，按 (relation, 目标实体类型) 细分
  生成映射表，随快照输出为独立文件 `relation_card_field_map.json`（54 个真实组合全覆盖，
  语义精确到"发起方→组织=aggressor/exact、发起方→人物=persons/contains"等）；
  契约层新增 FieldMapRow 类型定义。

### 修订 3：data_issues 缺修正前后明细（已核实属实）

- F09 人工审核要求第 5 条"每条审核记录保存操作人、处理前内容、处理后内容和审核结论"；
  实际 5 条 dynasty_correction 只含 type/from/to/reason/source_row_id/name，
  "处理前内容/处理后内容/审核结论"未落完整行值。
- 处置：**已在 v1 内闭环**——`data/snapshot/normalize.py` 的 issue 结构补充
  before（原 dynasty/start_date/event_type）/ after（修正后）/ audit_status
  （auto_applied_pending_review），重跑后报告含逐条明细。

### 附带修订：docs/README 总览状态更新

- 总览功能清单 F09/F11 原标"待开发"，与 RAGv1 完成内容矛盾；已改为"部分完成"附说明，
  三处缺口闭环后最终更新为"已完成"（F11 云端向量索引为结构占位，属 RAGv2 联调项，不阻塞验收），
  维持"总览表为准"的文档维护约定。

## 六、如何复现

```bash
# 环境：Python 3.11（anaconda）；jieba/numpy/pydantic/python-dotenv 已装
cd RAG
cp .env.example .env          # 可选；离线链路不填密钥也能跑

python scripts/export_snapshot.py            # F09：生成 data/snapshot/<版本>/
python scripts/build_index.py                # F11：基于最新快照建索引
# 或一键串联：
python scripts/run_pipeline.py

# 检索冒烟验证（任选查询词）
python -c "import sys;sys.path.insert(0,'.');from pathlib import Path;from data.index import fts;fts.load_jieba_dicts(Path('data/snapshot/20260904_v2'));print(fts.query_fts('data/index/20260904_v2/chunks_fts.db','赤壁之战 曹操',5))"
```

## 七、下一阶段 RAGv2 要做什么（规划）

见 [RAGv2 规划说明](RAGv2-规划说明.md)（同目录），要点：
在线问答链路（F02 问题理解 → F03 图谱检索 → F04 文本检索 → F05 融合重排 → F06 回答生成 + SSE），
以及把 F09 的 audit 清单做人工审核后回填为高置信度治理。详细拆解、优先级与文件归属见规划文档。
