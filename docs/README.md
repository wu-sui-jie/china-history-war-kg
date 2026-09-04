# RAG 智能问答页面功能总览

> 本文档只作为功能清单和文档索引，不展开具体功能实现。  
> 每个功能的“作用、如何实现、验收标准”等详细说明，请点击对应功能文档查看。

## 页面形态说明

本项目现阶段只提供一个用户可见页面：**智能问答页（单页面）**。

- F01 是问答主页面，负责对话、筛选、会话记录和整体交互。
- F02 至 F06 是问答处理链路，属于后端过程，不单独做成页面。
- F07 是问答页右侧的知识展示面板，不单独成页。
- F08 演示模式暂缓，待其他功能完成后补充。
- F09 数据快照与知识库治理属于离线数据流程，不进入页面。
- F11 文本切分与索引构建属于离线数据流程，不进入页面。
- F10 问答效果评测属于离线评测流程，不进入页面。

## 文档结构

```text
RAG/docs/
├── README.md                  # 总览与功能索引（本文件）
├── architecture.md            # 总体架构
├── data-contract.md           # 统一数据契约与流式协议
└── features/                  # 功能分块文档
    ├── 01-qa-main.md
    ├── 02-entity-linking.md
    ├── 03-graph-retrieval.md
    ├── 04-text-retrieval.md
    ├── 05-fusion-rerank.md
    ├── 06-grounded-answer.md
    ├── 07-knowledge-panel.md
    ├── 08-demo-mode.md
    ├── 09-data-governance.md
    ├── 10-evaluation.md
    └── 11-text-indexing.md
```

## 功能清单

| 编号 | 功能 | 状态 | 详细文档 |
| --- | --- | --- | --- |
| F01 | 智能问答主界面（单页面） | 已完成（RAGv3 前端） | [01-qa-main.md](features/01-qa-main.md) |
| F02 | 实体识别与消歧 | 已完成（RAGv2 词典/规则版） | [02-entity-linking.md](features/02-entity-linking.md) |
| F03 | 图谱检索通道（GraphRAG） | 已完成（RAGv2 服务版） | [03-graph-retrieval.md](features/03-graph-retrieval.md) |
| F04 | 文本检索通道 | 已完成（RAGv2 关键词版，向量待接入） | [04-text-retrieval.md](features/04-text-retrieval.md) |
| F05 | 检索结果融合与重排 | 已完成（RAGv2 服务版） | [05-fusion-rerank.md](features/05-fusion-rerank.md) |
| F06 | 证据溯源回答生成 | 部分完成（链路通；LLM 需配 key 切换） | [06-grounded-answer.md](features/06-grounded-answer.md) |
| F07 | 可视化知识面板 | 已完成（RAGv3 前端渲染） | [07-knowledge-panel.md](features/07-knowledge-panel.md) |
| F08 | 演示模式与示例问题 | 暂缓 | [08-demo-mode.md](features/08-demo-mode.md) |
| F09 | 数据快照与知识库治理 | 已完成 | [09-data-governance.md](features/09-data-governance.md) |
| F10 | 问答效果评测 | 待开发 | [10-evaluation.md](features/10-evaluation.md) |
| F11 | 文本切分与索引构建 | 已完成 | [11-text-indexing.md](features/11-text-indexing.md) |

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
3. 质量闭环：F10 人工评测与问题题库。⬜（下一阶段）
4. 演示稳定性：模型降级、回答缓存、限流、部署。◐（RAGv2 已含缓存/降级/限流，真实模型部署待定）
5. 暂缓：F08 演示模式，待核心功能完成后补充。

总体架构见 architecture.md，字段和流式协议见 data-contract.md。

## 开发阶段文档

功能文档回答"要做什么/怎么验收"；开发阶段文档回答"已做到哪、对应哪些文件、下一步做什么"。

| 阶段 | 范围 | 状态 | 开发说明 |
| --- | --- | --- | --- |
| RAGv1 | F09 数据快照与治理 + F11 文本切分与关键词索引（离线数据底座） | ✅ 已完成 | [RAG_v1/RAGv1-离线数据链路.md](RAG_v1/RAGv1-离线数据链路.md) |
| RAGv2 | 在线问答链路 F02→F03/F04→F05→F06 + SSE 服务 + F09 人工审核回填 | ✅ 已完成 | [RAG_v1/RAGv2-在线问答链路.md](RAG_v1/RAGv2-在线问答链路.md) |
| RAGv3 | F01 问答页 + F07 知识面板（前端） | ✅ 已完成 | [RAG_v1/RAGv3-开发说明.md](RAG_v1/RAGv3-开发说明.md)（任务分析 [RAG_v1/RAGv3-规划分析.md](RAG_v1/RAGv3-规划分析.md)） |
| RAGv4 | F10 问答效果评测（质量闭环） | ⬜ 规划中 | [RAG_v1/后续阶段规划.md](RAG_v1/后续阶段规划.md) |
| RAGv5 | F08 演示模式 + 真实模型/向量接入与部署打磨 | ⬜ 规划中 | [RAG_v1/后续阶段规划.md](RAG_v1/后续阶段规划.md) |

> 阶段命名说明：RAGv4/RAGv5 为后续工作提案性划分（正式文档原只命名到 RAGv3），
> 编号若与项目既定口径不一致以既定口径为准。任务明细与建议顺序见上表「后续阶段规划」。

阶段文档索引见 [RAG_v1/README.md](RAG_v1/README.md)。

## 文档维护约定

1. 每个功能文档使用相同的章节结构，便于检索和后续修改。
2. 新增或删除功能时，先更新本总览，再同步创建或归档对应功能文档。
3. 功能文档中的“状态”只保留一种当前状态，例如“待确认”“待开发”“开发中”“已完成”。
4. 本文档不写具体实现，只做功能索引和文档导航。

## 项目级约定

1. 不修改 backend、frontend、entity-event-relation 中的旧代码。
2. 整个项目代码（含旧代码与 RAG 子项目）后续统一提交到新建的 GitHub 公开仓库。
3. 不再推送到当前旧仓库 debug-debug-backup。
4. F08 演示模式暂缓，待其他功能模块完成后再补充需求与验收标准。
