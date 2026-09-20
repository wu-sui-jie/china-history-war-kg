# RAG 智能问答页面功能总览

> 本文档只作为功能清单和文档索引，不展开具体功能实现。  
> 每个功能的“作用、如何实现、验收标准”等详细说明，请点击对应功能文档查看。

## 页面形态说明

本项目现阶段只提供一个用户可见页面：**智能问答页（单页面）**。

- F01 是问答主页面，负责对话、筛选、会话记录和整体交互。
- F02 至 F06 是问答处理链路，属于后端过程，不单独做成页面。
- F07 是问答页右侧的知识展示面板，不单独成页。
- F08 演示模式为欢迎页常驻示例区（无开关、无登录墙），RAGv5 已完成。
- F09 数据快照与知识库治理属于离线数据流程，不进入页面。
- F11 文本切分与索引构建属于离线数据流程，不进入页面。
- F10 问答效果评测属于离线评测流程，不进入页面。

## 文档地图

docs/ 下的文档按三层组织，**读什么取决于你要做什么**：

**1. 现役文档（日常开发看这些）**

| 文档 | 什么时候看 |
| --- | --- |
| [current-status.md](current-status.md) | **唯一事实源**：版本、测试数、门禁状态、数据计数、发布状态 |
| [data-contract.md](data-contract.md) | 改接口/字段/证据结构/SSE 事件前必读（字段调整必须同步本文件与 `contracts/`） |
| [architecture.md](architecture.md) | 理解整体分层与调用关系 |
| [deploy.md](deploy.md) | 部署、排障、环境变量 |
| [features/](features/)（11 份） | 每个功能的“作用 / 实现 / 验收标准”；改某功能前先看对应那份 |

**2. 需求与设计（改动立项时的口径）**

| 目录 | 内容 |
| --- | --- |
| [RAG_v2/](RAG_v2/) | 借鉴旧问答系统的需求分析、P2 规则推理移植的需求与设计（2026-09-20） |

**3. 历史过程记录（审计追溯用，日常不必读）**

| 目录 | 内容 | 讲什么 |
| --- | --- | --- |
| [RAG_v1/](RAG_v1/)（13 份 + 索引） | 阶段开发说明（RAGv1–v5）、规划说明、阶段总结 | 这个阶段做了什么、怎么用 |
| [changes/](changes/)（24 份 + 索引） | 各轮审核报告、整改工作单、复核裁定、变更总结 | 这一轮改了什么、依据是什么 |

历史记录**只增不改**：后续若推翻前轮结论，在新文档里说明而不是回改旧文档；归档文档顶部都有
“归档说明”横幅，标明其性质与现行事实源。

```text
RAG/docs/
├── README.md                  # 总览与功能索引（本文件）
├── current-status.md          # 唯一事实源：版本 / 测试 / 门禁 / 数据计数
├── data-contract.md           # 统一数据契约与流式协议
├── architecture.md            # 总体架构
├── deploy.md                  # 部署与排障
├── features/                  # 功能分块文档（F01–F11，现役）
├── RAG_v2/                    # 需求与设计（借鉴旧问答系统 / 规则推理移植）
├── RAG_v1/                    # 阶段开发说明（历史）→ 索引 RAG_v1/README.md
└── changes/                   # 审核与整改过程记录（历史）→ 索引 changes/README.md
```

## 功能清单

| 编号 | 功能 | 状态 | 详细文档 |
| --- | --- | --- | --- |
| F01 | 智能问答主界面（单页面） | 已完成（RAGv3 前端） | [01-qa-main.md](features/01-qa-main.md) |
| F02 | 实体识别与消歧 | 已完成（RAGv2 词典/规则版） | [02-entity-linking.md](features/02-entity-linking.md) |
| F03 | 图谱检索通道（GraphRAG） | 已完成（RAGv2 服务版） | [03-graph-retrieval.md](features/03-graph-retrieval.md) |
| F04 | 文本检索通道 | 已完成（RAGv5：关键词 + 向量 + hybrid 融合，默认 hybrid/rrf） | [04-text-retrieval.md](features/04-text-retrieval.md) |
| F05 | 检索结果融合与重排 | 已完成（RAGv2 服务版） | [05-fusion-rerank.md](features/05-fusion-rerank.md) |
| F06 | 证据溯源回答生成 | 已完成（RAGv5：真实 LLM 流式 + 推理增量 + 降级链） | [06-grounded-answer.md](features/06-grounded-answer.md) |
| F07 | 可视化知识面板 | 已完成（RAGv3 前端渲染） | [07-knowledge-panel.md](features/07-knowledge-panel.md) |
| F08 | 演示模式与示例问题 | 已完成（RAGv5：示例题来自已审核题库 + 按类别/能力标签 + 一键提问） | [08-demo-mode.md](features/08-demo-mode.md) |
| F09 | 数据快照与知识库治理 | 已完成 | [09-data-governance.md](features/09-data-governance.md) |
| F10 | 问答效果评测 | 已完成（RAGv4：评测闭环 + 题库 reviewed-2 全通过 + 人工评分） | [10-evaluation.md](features/10-evaluation.md) |
| F11 | 文本切分与索引构建 | 已完成（RAGv5 补齐向量索引：百炼 v4 + Chroma，9,544 条 / 1024 维） | [11-text-indexing.md](features/11-text-indexing.md) |

> 状态说明：
> - F09/F11 在 RAGv1 已完整交付——基础快照 + 治理（含事件卡片结构化字段、
>   关系-事件卡片字段映射表、data_issues 明细）+ FTS5 关键词索引。详见
>   [RAG_v1/RAGv1-离线数据链路.md](RAG_v1/RAGv1-离线数据链路.md)。
> - F02–F06 在 RAGv2 已交付**在线服务版**：SSE `POST /api/query`（含 F02 词典/规则
>   识别+歧义降级+指代消解、F03 内存图谱检索、F04 关键词检索、F05 融合/冲突/panel 装配、
>   F06 回答生成）。边界：F02 LLM 兜底默认关闭、F04 向量模式待云端 embed 接入、
>   F06 无 LLM key 时用离线摘要回答器（配 key 后自动切真实流式）。详见
>   [RAG_v1/RAGv2-在线问答链路.md](RAG_v1/RAGv2-在线问答链路.md)。
> - F01/F07 在 RAGv3 已交付**前端单页**（Vue3+TS+Vite）：SSE 消费会话、过程状态、引用、
>   实体纠正与 panel 事件；地图无坐标时降级为地点列表；后端补充 `GET /api/dicts`
>   供筛选下拉使用。详见 [RAG_v1/RAGv3-开发说明.md](RAG_v1/RAGv3-开发说明.md)。
> - F10 在 RAGv4 已交付**评测闭环 + 首轮评审（含修复复测）**：`evaluation/` 包（题库管理 / 进程内复跑 /
>   覆盖率指标与失败归因 / 报告 / 人工评分模板导出）+ 题库初版
>   `data/eval/20260904_v2/questions.jsonl`（39 条，reviewed-2）+ 首轮基线报告
>   （已量化：F02 朝代子串误判拒答、F04 长改写 AND 失效、event_type 筛选原文损耗、
>   应拒答缺口）。首轮题库审核（委托 AI 代理，35 条通过、4 条保留缺陷样本）与
>   39 条答案/引用评分已完成（0 correct/23 partial/16 incorrect；引用
>   28 supported/3 unrelated/8 unsupported，根因是离线摘要回答器形态）；期间修复了
>   F02 实体名排序跨进程非确定性、F02 朝代别名子串误判、F03 证据 ID 跨表重复，
>   题库升至 reviewed-2（39/39 通过）。第二轮由第三方模型做功能审核（报告
>   [RAG_v1/RAGv4-阶段审核报告.md](RAG_v1/RAGv4-阶段审核报告.md)），T1–T8 已整改：
>   评分溯源归档、问句朝代识别改"软偏置"（不再硬过滤）、报告类别列、失败桶按来源
>   分桶等；复审轮 R1–R7（文本侧偏置口径、朝代后缀补"代"、题库条目版本同步等）亦已整改。
>   基线重跑为 `run_20260913_postaudit`（评分 0 correct/23 partial/16 incorrect）。详见
>   [RAG_v1/RAGv4-开发说明.md](RAG_v1/RAGv4-开发说明.md) 与
>   [changes/20260904-ragv4-summary.md](changes/20260904-ragv4-summary.md)。

## 数据流

```text
F09 旧数据导出与知识库治理
    ├→ F03 使用的干净图谱快照
    └→ F11 文本切分与索引构建
            ↓
      F04 使用的文本与向量索引
```

## 请求流

```text
F01 用户提问
    ↓
F02 问题类型判定、实体识别、多轮指代消解、查询改写
    ↓
F03 图谱检索 + F04 文本检索
    ↓
F05 证据融合、排序与引用编号分配
    ↓
F06 生成带引用回答并推送过程事件
    ↓
F07 展示知识面板
```

F09、F11 是离线数据流；F02 至 F06 是请求时运行链，不要混读。

## 建议开发顺序

1. MVP：F09 基础快照 → F11 基础切分与向量索引 → F02/F03/F04 最小检索 → F05 融合 → F06 回答 → F01/F07 页面。✅（F09/F11 已在 RAGv1 完成；F02–F06 检索与回答链已在 RAGv2 完成）
2. 数据增强：F09 实体消歧、孤立节点处理、人工抽检。✅（RAGv1 分组清单；RAGv2 补充 apply_audit 人工审核回填）
3. 质量闭环：F10 人工评测与问题题库。✅（RAGv4 已交付题库 reviewed-2 + 评测工具 + 双通道基线与人工评分）
4. 演示稳定性：模型降级、回答缓存、限流、部署。✅（RAGv5 已完成：真实 LLM 流式 + 向量/hybrid 检索 + 同源托管 + 冒烟/预热脚本；见 [RAG_v1/RAGv5-开发说明.md](RAG_v1/RAGv5-开发说明.md) 与 [deploy.md](deploy.md)）
5. F08 演示模式：✅ 已完成（RAGv5：示例题取自已审核题库，按类别分组 + 能力标签 + 一键提问）。

总体架构见 architecture.md，字段和流式协议见 data-contract.md。

## 开发阶段文档

功能文档回答"要做什么/怎么验收"；开发阶段文档回答"已做到哪、对应哪些文件、下一步做什么"。

| 阶段 | 范围 | 状态 | 开发说明 |
| --- | --- | --- | --- |
| RAGv1 | F09 数据快照与治理 + F11 文本切分与关键词索引（离线数据底座） | ✅ 已完成 | [RAG_v1/RAGv1-离线数据链路.md](RAG_v1/RAGv1-离线数据链路.md) |
| RAGv2 | 在线问答链路 F02→F03/F04→F05→F06 + SSE 服务 + F09 人工审核回填 | ✅ 已完成 | [RAG_v1/RAGv2-在线问答链路.md](RAG_v1/RAGv2-在线问答链路.md) |
| RAGv3 | F01 问答页 + F07 知识面板（前端） | ✅ 已完成 | [RAG_v1/RAGv3-开发说明.md](RAG_v1/RAGv3-开发说明.md)（任务分析 [RAG_v1/RAGv3-规划分析.md](RAG_v1/RAGv3-规划分析.md)） |
| RAGv4 | F10 问答效果评测（质量闭环） | ✅ 已完成（题库 reviewed-2 全通过 + 人工评分完成 + F02/F03 修复复测） | [RAG_v1/RAGv4-开发说明.md](RAG_v1/RAGv4-开发说明.md)（任务规划 [RAG_v1/后续阶段规划.md](RAG_v1/后续阶段规划.md)；变更总结 [changes/20260904-ragv4-summary.md](changes/20260904-ragv4-summary.md)、[changes/20260913-ragv4-review-summary.md](changes/20260913-ragv4-review-summary.md)；送审总结 [RAG_v1/RAGv4-阶段工作总结.md](RAG_v1/RAGv4-阶段工作总结.md)） |
| RAGv5 | F08 演示模式 + 真实模型/向量接入与部署打磨 | ✅ 已完成（向量/hybrid 检索 + 真实 LLM 流式 + 同源部署 + F08 示例题 + T5 质量优化；LLM 对照 25 correct/3 partial/0 incorrect，评分口径为 AI 代理） | [RAG_v1/RAGv5-开发说明.md](RAG_v1/RAGv5-开发说明.md)（需求与验收 [RAG_v1/RAGv5-规划说明.md](RAG_v1/RAGv5-规划说明.md)；送审总结 [RAG_v1/RAGv5-阶段工作总结.md](RAG_v1/RAGv5-阶段工作总结.md)；部署 [deploy.md](deploy.md)） |

> 阶段命名说明：RAGv4、RAGv5 编号均已按提案实际启用并完成（RAGv5 于 2026-09-13 收口，
> 2026-09-14 复查补齐 T3 收尾项；编号若与项目既定口径不一致以既定口径为准）。
> 任务明细与建议顺序见上表「后续阶段规划」。

阶段文档索引见 [RAG_v1/README.md](RAG_v1/README.md)。

> 2026-09-15 全项目审核（文档与代码一致性）的整改工作单与整改记录见
> [第一轮全项目审核报告](changes/20260915-round1-full-audit-report.md)，
> 改动清单与理由见 [changes/20260915-full-audit-fix-summary.md](changes/20260915-full-audit-fix-summary.md)。
>
> 同日第四轮全项目复核（[第四轮复核分析与优化建议](changes/20260915-round4-full-review-analysis.md)，
> 含第三轮工作单 [第三轮审核报告与整改方案](changes/20260915-round3-audit-and-remediation-plan.md)）的
> 代码整改与验证证据见 [changes/20260915-round4-review-fix-summary.md](changes/20260915-round4-review-fix-summary.md)；
> 复核（第四轮）发现的发布阻断与正确性问题的修复记录见
> [changes/20260915-round4-review-fix-summary-2.md](changes/20260915-round4-review-fix-summary-2.md)。
>
> 第五轮复核（[changes/20260916-round5-remediation-review-and-full-project-audit.md](changes/20260916-round5-remediation-review-and-full-project-audit.md)：
> 13 项重裁定 + R5-1…R5-7 新增问题）的整改，见两份配套文档：
> **修改说明**（依据哪条审核要求改了哪些文件、如何验证）
> [changes/20260916-round5-remediation-change-note.md](changes/20260916-round5-remediation-change-note.md)、
> **结论对照**（每项的重裁定与完成判定）
> [changes/20260916-round5-review-remediation-summary.md](changes/20260916-round5-review-remediation-summary.md)。
> 当前状态数字统一见 [current-status.md](current-status.md)。
>
> 第六轮独立复核（对第五轮整改修改说明的核验 + 全项目增量清查，含 SHA256SUMS 行尾、
> drain 并发、血缘判定缺口等 8 项 B 类遗留与增量发现，及下一轮整改顺序）见
> [changes/20260916-round6-review-of-round5-remediation.md](changes/20260916-round6-review-of-round5-remediation.md)。
>
> 第六轮复核的整改，见两份配套文档：**修改说明**
> [changes/20260916-round6-remediation-change-note.md](changes/20260916-round6-remediation-change-note.md)、
> **结论对照** [changes/20260916-round6-review-remediation-summary.md](changes/20260916-round6-review-remediation-summary.md)。

## 文档维护约定

1. 每个功能文档使用相同的章节结构，便于检索和后续修改。
2. 新增或删除功能时，先更新本总览，再同步创建或归档对应功能文档。
3. 功能文档中的“状态”只保留一种当前状态，例如“待确认”“待开发”“开发中”“已完成”。
4. 本文档不写具体实现，只做功能索引和文档导航。

## 项目级约定

1. 不修改 backend、frontend、entity-event-relation 中的旧代码。
2. 整个项目代码（含旧代码与 RAG 子项目）后续统一提交到新建的 GitHub 公开仓库。
3. 不再推送到当前旧仓库 debug-debug-backup。
4. F08 演示模式已于 RAGv5 交付（示例题来自已审核题库，欢迎页常驻展示，无演示开关）。
