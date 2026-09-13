# RAG 智能问答子项目

> 面向中国历代战争史知识库的「图谱 + 文本」双通道 RAG 问答系统。
> 功能需求与验收标准见 [docs/](docs/README.md)；本文档只讲**代码怎么组织、怎么开发、怎么跑**。

## 一、这是什么

在旧项目（SQLite + Neo4j + Flask/Vue）只读数据之上，新建一套自包含的 RAG 问答：

- **离线链路**：`F09 数据快照与治理` → `F11 文本切分与索引构建`。
- **在线链路**：`F02 实体识别` → `F03 图谱检索 + F04 文本检索` → `F05 融合重排` → `F06 回答生成` → `F01/F07 前端展示`。

RAG 运行时不依赖旧后端服务、旧前端、Neo4j 是否启动，只读取自己目录下的治理快照与索引。

## 二、为什么按“层”切目录，而不是按“功能编号 F01~F11”

这是本项目最重要的代码组织决策。需求文档按 F01~F11 编号组织，但**代码目录不按编号切**，原因：

1. **运行链路是横向的，功能是纵向的。**
   F02→F03→F04→F05→F06 是一条串行流水线，共享同一套证据对象（data-contract.md 的“统一证据对象”）。若 F03/F04/F05/F06 各占一个文件夹、各自实现“自己那一版”证据结构，统一契约会被复制 4 份，字段一旦漂移接口就崩。文档也明确规定字段调整要同步 data-contract.md——这正是“按层共享同一套类型”的信号。

2. **离线与在线被切进同一个桶会很混乱。**
   F09/F11 是一次性离线脚本（build time），F02~F06 是常驻在线服务（run time）。若按功能编号平铺，启动逻辑、依赖、环境互相污染。

3. **功能编号 ≠ 代码模块边界。**
   F07 知识面板横跨后端 panel 装配（F05 承担）与前端渲染；“文本切分 F11”产出 F04 检索消费的索引。按编号平铺无法表达这种复用。

### 切法：目录 = 架构分层 + 数据流边界

| 目录 | 归属功能 | 定位 | 说明 |
| --- | --- | --- | --- |
| `contracts/` | 全部 | 共享数据契约与类型 | evidence、SSE 事件、panel、词典结构。各层 import 这里，不各自复制。 |
| `config/` | 全部 | 配置与环境变量加载 | `.env` 读取、默认配置、路径约定。 |
| `lib/` | 全部 | 无业务小工具 | 版本号、JSON 读写、日志等跨层复用。 |
| `scripts/` | F09/F11 入口 + RAGv2 服务 + F10 | 离线任务入口 + 在线服务/回填 + 评测 | `export_snapshot.py`、`build_index.py`、`run_pipeline.py`、`run_server.py`、`apply_audit.py`、`run_evaluation.py`、`gen_draft_bank.py`。可命令行一键跑。 |
| `data/snapshot/` | F09 | 离线产物：治理快照 | 只读导出、别名/归一、词典、治理报告、人工审核回填（apply_audit）。 |
| `data/index/` | F11 | 离线产物：文本与向量索引 | 切分、FTS5 关键词索引、向量索引。 |
| `data/eval/` | F10 | 离线产物：评测题库与运行结果 | 题库 questions.jsonl（入 Git）+ runs/ 运行痕迹（不入 Git，见 evaluation/）。 |
| `server/query/` | F02 | 在线：问题理解 | 词典/规则实体识别、歧义降级、指代消解、改写（RAGv2 已实现）。 |
| `server/graph/` | F03 | 在线：图谱检索 | 加载快照，按问题类型执行图谱查询（RAGv2 已实现）。 |
| `server/text/` | F04 | 在线：文本检索 | 读索引，关键词（AND/OR）检索 + 向量降级（RAGv2 已实现关键词版）。 |
| `server/fusion/` | F05 | 在线：融合重排 | 证据合并、去重、引用编号、冲突判定、panel 装配（RAGv2 已实现）。 |
| `server/generate/` | F06 | 在线：回答生成 | SSE 流式回答、拒答、缓存、降级（RAGv2 已实现，LLM 需配 key）。 |
| `server/` | 入口 | FastAPI app + SSE 编排 | `api.py`、`sse.py`、`runtime.py`（RAGv2 已实现）。 |
| `frontend/` | F01/F07 | 在线：单页前端 | （RAGv3 阶段）问答页 + 知识面板。 |
| `evaluation/` | F10 | 离线：问答效果评测 | （RAGv4 阶段）题库管理、进程内复跑、指标/报告、人工评分模板。见 `evaluation/README.md`。 |
| `tests/` | 全部 | 测试 | 单元/集成测试（RAGv4 已补 evaluation / keyword_mode / 跨进程确定性 / 朝代识别 / 证据 ID / F02 端到端回归，共 50 个；端到端冒烟仍以脚本验证）。运行：`python -m pytest tests -q`。 |

> **功能编号 Fxx 怎么追踪？**
> 不放进目录名，而是放进**文档**与**模块 docstring / 注释**。例如 `data/snapshot/` 在 README 中注明“本层实现 F09”，`scripts/build_index.py` docstring 注明“F11”。这样功能清单仍能一对一追到代码模块，又不会造成契约复制。

### “一个模块一个文件夹 + 每文件夹一份开发 md”

你提的“每个模块一个文件夹、每文件夹一份开发说明 md”我完全保留，只是把“模块”定义为**上表每一层**，而不是 F01~F11。每一层（目录）内都放一份 `README.md`（开发说明），讲清楚：

- 本层是什么、实现哪些 Fxx；
- 输入/输出（哪些文件、什么格式、版本号怎么对）；
- 怎么跑（命令/脚本）；
- 怎么测试、验收标准对应哪些功能文档；
- 边界约定（本层不该做什么）。

目录层级较少、职责清晰时，不为每一层再套子目录 + 嵌套 README，避免过度设计。

## 三、目录速览

```text
RAG/
├── docs/                    # 需求/功能文档 + RAG_v1/ 阶段开发说明
│   ├── README.md            # 功能总览与文档导航
│   ├── RAG_v1/              # 阶段开发说明（RAGv1/v2/v3…）
│   └── features/            # 功能需求文档（F01–F11）
├── README.md                # 本文档：代码组织与开发约定
├── requirements.txt
├── .env.example             # 环境变量模板（密钥不入库）
├── config/                  # 配置加载
├── contracts/               # 共享数据契约 + 版本化的 Python dataclass
├── lib/                     # 无业务工具
├── scripts/                 # 离线任务入口（F09/F11/F10 一键跑）
├── data/
│   ├── raw/source_texts/    # 旧原文文本的软链/拷贝（utf-8）
│   ├── snapshot/            # F09 治理后快照 + 治理报告（带版本）
│   ├── index/               # F11 文本/向量索引（带版本）
│   ├── eval/                # F10 评测题库（带版本；runs/ 运行痕迹不入库）
│   └── cache/               # 运行时缓存（LLM 等），不入库
├── server/                  # 在线服务（FastAPI）各层
├── evaluation/              # F10 离线评测（题库/指标/报告/评分）
├── frontend/                # Vue3 前端
├── logs/                    # 运行日志，不入库
└── tests/
```

## 四、离线数据流与版本约定

```text
旧项目只读数据（backend/database + 原文 txt）
   │  scripts/export_snapshot.py   （F09）
   ▼
data/snapshot/<版本>/            干净图谱快照 + 词典 + 治理报告
   │  scripts/build_index.py       （F11）
   ▼
data/index/<版本>/               FTS5 关键词索引 + 向量索引
   ▼
（后续在线链路 F03/F04 读取）
```

**版本约定（重要）**：每次 F09 治理生成新快照版本号（如 `20260903_v1`），F11 基于该版本构建索引，F03/F04 运行时确认快照与索引版本一致。版本号贯穿 data-contract.md 的 `source_version`。

## 五、环境与配置

密钥/接口不硬编码。复制 `.env.example` 为 `.env` 并填写（在线链路需要 LLM/向量模型密钥；离线链路可先不填）。配置读取统一走 `config/`。

```bash
cp .env.example .env
```

## 六、如何运行

```bash
# —— 离线链路（RAGv1）——
# 1. 导出并治理快照（F09）
python scripts/export_snapshot.py
# 2. 构建文本与向量索引（F11）
python scripts/build_index.py
# 3. （可选）端到端离线流水线
python scripts/run_pipeline.py

# —— 在线问答链路（RAGv2）——
# 启动 SSE 问答服务（无 LLM key 也能跑检索链，F06 走离线摘要回答器）
python scripts/run_server.py --port 8000
# 冒烟：curl -N -X POST http://127.0.0.1:8000/api/query \
#   -H "Content-Type: application/json; charset=utf-8" \
#   -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'
# 健康检查：curl http://127.0.0.1:8000/api/health

# —— 人工审核回填（RAGv2 F09 增强）——
python scripts/apply_audit.py --decisions audit_decisions.json
```

详细命令、参数与产物说明见各层 README。运行环境：Python 3.11；离线链路依赖 jieba/numpy/pydantic，
在线链路另需 fastapi/uvicorn/openai（见 requirements.txt）。本机建议使用
已装上述依赖的 `E:/anaconda/envs/AI_Agent` 环境。

## 七、开发约定（项目级）

> 本仓库 = RAG 问答系统（独立仓库，仅含 RAG 子项目代码与需求文档）。
> 旧项目（backend/entity-event-relation 等）的源码与数据**不在本仓库内**——原书文本受版权约束、
> 快照由旧数据重建，均不进公开仓库。

1. F09 只读依赖旧项目 SQLite 与原文文本，但**不修改**旧代码；旧数据源在本机/私有环境提供，
   路径经 `.env` 的 `LEGACY_SQLITE_PATH` / `LEGACY_RAW_TEXTS` 配置（默认指向 china-war 仓库内旧文件）。
2. 所有密钥走 `.env`（`.env` 已被 .gitignore 忽略，`.env.example` 入库）。
3. 数据产物（治理快照、索引、日志、缓存）放在 data/、logs/ 下，均被 .gitignore 忽略，不进 Git。
4. 涉及 data-contract.md 的字段调整，必须同步更新 `docs/data-contract.md` 和 `contracts/` 中的定义。
5. 每个实现层一个目录、一份 README（开发说明），F 编号在 README 与模块 docstring 中标注。
6. 仓库托管：GitHub 公开仓库 `wu-sui-jie/china-history-war-kg`，新仓库新历史（不包含旧 china-war
   仓库的提交历史），不再推送旧仓库 debug-debug-backup。

## 八、路线图（当前实现状态）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| RAGv1 | F09 快照与治理 + F11 文本切分与关键词索引（离线数据底座） | ✅ 已完成 |
| RAGv2 | F02–F06 在线问答链路 + SSE 服务 + F09 人工审核回填 | ✅ 已完成（LLM/向量联调边界见 RAGv2 完成文档） |
| RAGv3 | F01/F07 前端页面 | ✅ 已完成（见 [docs/RAG_v1/RAGv3-开发说明.md](docs/RAG_v1/RAGv3-开发说明.md)） |
| RAGv4 | F10 评测（题库/指标/报告/人工评分 + 第三方审核整改） | ✅ 已完成（见 [docs/RAG_v1/RAGv4-开发说明.md](docs/RAG_v1/RAGv4-开发说明.md)、[阶段工作总结](docs/RAG_v1/RAGv4-阶段工作总结.md)） |
| RAGv5 | F08 演示模式 + 真实 LLM/向量接入与部署打磨 | ⬜ 规划中（规划说明见 [docs/RAG_v1/RAGv5-规划说明.md](docs/RAG_v1/RAGv5-规划说明.md)） |
