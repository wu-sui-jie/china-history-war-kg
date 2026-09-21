# RAG 智能问答页面功能总览

> 本文档只作为功能清单和文档索引，不展开具体功能实现。
> 每个功能的"作用、如何实现、验收标准"等详细说明，请点击对应功能文档查看。

## 页面形态说明

本项目现阶段只提供一个用户可见页面：**智能问答页（单页面）**。

- F01 是问答主页面，负责对话、筛选、会话记录和整体交互。
- F02 至 F06 是问答处理链路，属于后端过程，不单独做成页面。
- F07 是问答页右侧的知识展示面板，不单独成页。
- F08 演示模式为欢迎页常驻示例区（无开关、无登录墙）。
- F09 数据快照与知识库治理属于离线数据流程，不进入页面。
- F11 文本切分与索引构建属于离线数据流程，不进入页面。
- F10 问答效果评测属于离线评测流程，不进入页面。

## 文档地图

docs/ 下的文档按两层组织，**读什么取决于你要做什么**：

| 文档 | 什么时候看 |
| --- | --- |
| [current-status.md](current-status.md) | **唯一事实源**：版本、测试数、门禁状态、数据计数、发布状态。不要在其他文档里复制这些数字 |
| [data-contract.md](data-contract.md) | 改接口/字段/证据结构/SSE 事件前必读（字段调整必须同步本文件与 `contracts/`） |
| [architecture.md](architecture.md) | 理解整体分层与调用关系 |
| [deploy.md](deploy.md) | 部署、排障、环境变量 |
| [features/](features/)（11 份） | 每个功能的"作用 / 实现 / 验收标准"；改某功能前先看对应那份 |
| [CHANGELOG.md](CHANGELOG.md) | 阶段交付与六轮审核整改的归纳记录（审计追溯、复盘"为什么这么改"时看） |
| [RAG_v2/](RAG_v2/) | 借鉴旧问答系统的需求分析、规则推理移植的需求与设计 |

```text
RAG/docs/
├── README.md                  # 总览与功能索引（本文件）
├── current-status.md          # 唯一事实源：版本 / 测试 / 门禁 / 数据计数
├── data-contract.md           # 统一数据契约与流式协议
├── architecture.md            # 总体架构
├── deploy.md                  # 部署与排障
├── CHANGELOG.md               # 阶段与审核整改汇总（历史）
├── features/                  # 功能分块文档（F01–F11，现役）
└── RAG_v2/                    # 需求与设计（借鉴旧问答系统 / 规则推理移植）
```

## 功能清单

| 编号 | 功能 | 状态 | 详细文档 |
| --- | --- | --- | --- |
| F01 | 智能问答主界面（单页面） | 已完成 | [01-qa-main.md](features/01-qa-main.md) |
| F02 | 实体识别与消歧 | 已完成（词典/规则版，LLM 兜底开关预留） | [02-entity-linking.md](features/02-entity-linking.md) |
| F03 | 图谱检索通道（GraphRAG） | 已完成 | [03-graph-retrieval.md](features/03-graph-retrieval.md) |
| F04 | 文本检索通道 | 已完成（关键词 + 向量 + hybrid 融合，默认 hybrid/rrf） | [04-text-retrieval.md](features/04-text-retrieval.md) |
| F05 | 检索结果融合与重排 | 已完成 | [05-fusion-rerank.md](features/05-fusion-rerank.md) |
| F06 | 证据溯源回答生成 | 已完成（真实 LLM 流式 + 推理增量 + 降级链） | [06-grounded-answer.md](features/06-grounded-answer.md) |
| F07 | 可视化知识面板 | 已完成 | [07-knowledge-panel.md](features/07-knowledge-panel.md) |
| F08 | 演示模式与示例问题 | 已完成（示例题取自已审核题库） | [08-demo-mode.md](features/08-demo-mode.md) |
| F09 | 数据快照与知识库治理 | 已完成 | [09-data-governance.md](features/09-data-governance.md) |
| F10 | 问答效果评测 | 已完成（评测闭环 + 题库 reviewed-2 全通过 + 人工评分） | [10-evaluation.md](features/10-evaluation.md) |
| F11 | 文本切分与索引构建 | 已完成（含向量索引：百炼 v4 + Chroma，1024 维） | [11-text-indexing.md](features/11-text-indexing.md) |

> 各功能由哪个阶段交付、当时的设计口径与踩过的坑，见 [CHANGELOG.md](CHANGELOG.md)；
> 当前版本号、测试数、数据计数一律见 [current-status.md](current-status.md)。

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

## 文档维护约定

1. 每个功能文档使用相同的章节结构，便于检索和后续修改。
2. 新增或删除功能时，先更新本总览，再同步创建或归档对应功能文档。
3. 功能文档中的"状态"只保留一种当前状态，例如"待确认""待开发""开发中""已完成"。
4. 本文档不写具体实现，只做功能索引和文档导航。
5. 版本、测试数、数据计数等**数字只在 `current-status.md` 维护**，
   由 `scripts/check_docs.py --strict` 机械核对；需要写进其他文档时用引用而非复制。
