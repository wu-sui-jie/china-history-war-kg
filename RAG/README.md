# RAG 智能问答子项目

> 面向中国历代战争史知识库的「图谱 + 文本」双通道 RAG 问答系统。
> 功能需求与验收标准见 [docs/](docs/README.md)；当前版本、测试数、门禁状态见
> [docs/current-status.md](docs/current-status.md)（唯一事实源）；
> 阶段交付与审核整改经过见 [docs/CHANGELOG.md](docs/CHANGELOG.md)。
> 本文档只讲**代码怎么组织、怎么开发、怎么跑**。

## 一、这是什么

在旧项目（SQLite + Neo4j + Flask/Vue）只读数据之上，新建一套自包含的 RAG 问答：

- **离线链路**：`F09 数据快照与治理` → `F11 文本切分与索引构建`。
- **在线链路**：`F02 实体识别` → `F03 图谱检索 + F04 文本检索` → `F05 融合重排` → `F06 回答生成` → `F01/F07 前端展示`。

RAG 运行时不依赖旧后端服务、旧前端、Neo4j 是否启动，只读取自己目录下的治理快照与索引。

## 二、代码怎么分层

需求文档按 F01~F11 编号组织，但**代码目录按架构分层切，不按编号平铺**。原因是运行链路是横向的：
F02→F06 是一条串行流水线，共享同一套"统一证据对象"（见 [docs/data-contract.md](docs/data-contract.md)）。
若按编号各占一个文件夹、各实现"自己那一版"证据结构，契约会被复制多份，字段一旦漂移接口就崩。
F 编号不放进目录名，而是标注在**文档**与**模块 docstring/注释**里，功能清单仍能一对一追到代码模块。

| 目录 | 归属功能 | 定位 | 说明 |
| --- | --- | --- | --- |
| `contracts/` | 全部 | 共享数据契约与类型 | evidence、SSE 事件、panel、词典结构。各层 import 这里，不各自复制。 |
| `config/` | 全部 | 配置与环境变量加载 | `.env` 读取、默认配置、路径约定。 |
| `lib/` | 全部 | 无业务小工具 | 版本号、JSON 读写、日志等跨层复用。 |
| `scripts/` | F09/F11 入口 + 在线服务 + F10 | 离线任务入口 + 服务启动 + 评测 | `export_snapshot.py`、`build_index.py`、`run_pipeline.py`、`run_server.py`、`apply_audit.py`、`run_evaluation.py`、`gen_demo_examples.py` 等，可命令行一键跑。 |
| `data/snapshot/` | F09 | 离线产物：治理快照 | 只读导出、别名/归一、词典、治理报告、人工审核回填（apply_audit）。 |
| `data/index/` | F11 | 离线产物：文本与向量索引 | 切分、FTS5 关键词索引、Chroma 向量索引（百炼 `text-embedding-v4` / 1024 维）。 |
| `data/eval/` | F10 | 离线产物：评测题库与运行结果 | 题库 questions.jsonl（入 Git）+ runs/ 运行痕迹（不入 Git）。 |
| `server/query/` | F02 | 在线：问题理解 | 词典/规则实体识别、歧义降级、指代消解、改写。 |
| `server/graph/` | F03 | 在线：图谱检索 | 加载快照，按问题类型执行图谱查询。 |
| `server/text/` | F04 | 在线：文本检索 | 关键词（AND/OR/BM25）+ 向量（Chroma HNSW）+ hybrid 融合，向量不可用自动降级关键词。 |
| `server/fusion/` | F05 | 在线：融合重排 | 证据合并、去重、引用编号、冲突判定、panel 装配（panel 唯一装配方）。 |
| `server/generate/` | F06 | 在线：回答生成 | SSE 流式回答（真实 LLM token 增量 + 推理 `thinking`）、拒答、缓存、降级（无 key 走离线摘要回答器）。 |
| `server/` | 入口 | FastAPI app + SSE 编排 | `api.py`、`sse.py`、`runtime.py`。 |
| `frontend/` | F01/F07 | 在线：单页前端 | 问答页 + 知识面板（实体卡/图谱子图/时间线/地图/证据）+ F08 示例题。 |
| `evaluation/` | F10 | 离线：问答效果评测 | 题库管理、进程内复跑、指标/报告、人工评分模板。见 `evaluation/README.md`。 |
| `tests/` | 全部 | 测试 | 单元/集成/守护用例。运行：`python -m pytest tests -q`。 |

每一层（目录）内都有一份 `README.md`（开发说明），讲清楚：本层是什么、实现哪些 Fxx、
输入/输出、怎么跑、怎么测试、边界约定。**用例数不写在这里**——以 `docs/current-status.md` 为准，
避免同一个数字在多份文档里各写一个版本。

## 三、目录速览

```text
RAG/
├── README.md                # 本文档：代码组织与开发约定
├── docs/                    # 需求/功能/契约/部署文档（见 docs/README.md）
├── .env.example             # 环境变量模板（密钥不入库）
├── requirements*.txt/.lock  # 依赖声明与锁定（见第六节）
├── config/                  # 配置加载
├── contracts/               # 共享数据契约 + 版本化的 Python dataclass
├── lib/                     # 无业务工具
├── scripts/                 # 离线任务入口 + 服务启动 + 评测
├── data/
│   ├── raw/source_texts/    # 旧原文文本（utf-8）
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
旧项目只读数据（china-war/backend/database + 原文 txt）
   │  scripts/export_snapshot.py            （F09）
   ▼
data/snapshot/<版本>/            干净图谱快照 + 词典 + 治理报告
   │  scripts/build_inferred_relations.py （P2 规则推理固化，可选）
   ▼
data/snapshot/<版本>/inferred_relations.json   规则推理边（带溯源标记）
   │  scripts/build_index.py                （F11）
   ▼
data/index/<版本>/               FTS5 关键词索引 + 向量索引
   ▼
（后续在线链路 F03/F04 读取）
```

**版本约定（重要）**：每次 F09 治理生成新快照版本号（如 `20260915_v1`），F11 基于该版本构建索引，
F03/F04 运行时确认快照与索引版本一致。版本号贯穿 [docs/data-contract.md](docs/data-contract.md) 的
`source_version`。生产档（`RAG_REQUIRE_ACTIVE_VERSION=true`）下必须显式固定版本，
不存在"自动选最新目录"的路径。

## 五、环境与配置

密钥/接口不硬编码。复制 `.env.example` 为 `.env` 并填写（离线链路可先不填 LLM/向量密钥）：

```bash
cp .env.example .env
```

密钥两种来源都支持：写进 `.env`，或放在**系统环境变量**里（推荐，避免误提交）；读取优先级为
`LLM_API_KEY → DEEPSEEK_API_KEY → RAG-command → RAG-deepseek-v4`（生成模型）与
`EMBEDDING_API_KEY → DASHSCOPE_API_KEY`（向量模型），完整清单与部署口径见 [docs/deploy.md](docs/deploy.md)。

运行环境：Python 3.11（Chroma 依赖树要求 ≥3.10；锁文件按 3.11 生成）。离线链路依赖 jieba/numpy/pydantic，
在线链路另需 fastapi/uvicorn/openai（见 `requirements.txt`）。

## 六、如何运行

```bash
# —— 离线链路 ——
python scripts/export_snapshot.py      # F09 导出并治理快照
python scripts/build_index.py          # F11 构建文本与向量索引
python scripts/run_pipeline.py         # （可选）端到端离线流水线

# —— 在线问答服务 ——
# --version 把版本写进 RAG_ACTIVE_VERSION，数据版本被显式固定
python scripts/run_server.py --port 8000 --version 20260915_v1
# 冒烟：curl -N -X POST http://127.0.0.1:8000/api/query \
#   -H "Content-Type: application/json; charset=utf-8" \
#   -d '{"session_id":"s1","question":"赤壁之战的主帅是谁？"}'
# 健康检查：curl http://127.0.0.1:8000/api/health
# 请求边界：体 64 KiB、问题 500 字、历史 40 条等；超限在调用模型前返回 4xx

# —— 人工审核回填（F09 增强）——
python scripts/apply_audit.py --decisions audit_decisions.json

# —— 演示部署（同源托管：前端 dist 由后端一并提供）——
cd frontend && npm run build && cd ..     # 注意：与并入模式产物互相覆盖，见下
python scripts/run_server.py --port 8000
python scripts/smoke_deploy.py --base http://127.0.0.1:8000   # 冒烟六步，兼作缓存预热
```

> **前端构建有两种互斥模式**：`npm run build`（独立部署，base `/`、接口前缀 `/api`）与
> `npm run build:integration`（并入旧知识库系统，base `/rag/`、接口前缀 `/rag/api`）。
> 两者共用同一个 `dist/`，互相覆盖；挂到旧系统 `/rag/` 入口下必须用并入模式。
> 口径见 china-war 仓库的 `docs/集成与入口约定.md`。

依赖声明与锁定分四个文件（各司其职，不要合并或删除）：

| 文件 | 角色 | 维护方式 |
| --- | --- | --- |
| `requirements.txt` | 直接依赖声明（`>=` 下界），回答"要什么" | 人工 |
| `requirements-dev.txt` | `-r requirements.txt` + 测试/审计依赖 | 人工 |
| `requirements.lock` | pip-compile 解析出的完整依赖树（含传递依赖） | 工具生成 |
| `requirements-dev.lock` | 同上、含开发依赖；CI 与发布用它安装（`--require-hashes`） | 工具生成 |

锁文件体积较大（≈150 KB）属正常：每条依赖带多个 sha256 哈希（同一包在不同 Python 版本与平台的
发布产物各有哈希），用于供应链校验与跨机器复现。日常开发装 `requirements-dev.txt` 即可。

## 七、开发约定（项目级）

> 本仓库 = RAG 问答系统（独立仓库，仅含 RAG 子项目代码与需求文档）。
> 旧项目（`backend/`、`entity-event-relation/` 等）的源码与数据**不在本仓库内**——
> 原书文本受版权约束、快照由旧数据重建，均不进公开仓库。

1. F09 只读依赖旧项目 SQLite 与原文文本，但**不修改**旧代码；旧数据源在本机/私有环境提供，
   路径经 `.env` 的 `LEGACY_SQLITE_PATH` / `LEGACY_RAW_TEXTS` 配置。
2. 所有密钥走 `.env`（`.env` 已被 .gitignore 忽略，`.env.example` 入库）。
3. 数据产物（治理快照、索引、日志、缓存）放在 `data/`、`logs/` 下，均被 .gitignore 忽略。
4. 涉及 [docs/data-contract.md](docs/data-contract.md) 的字段调整，必须同步更新该文件与
   `contracts/` 中的定义，再同步受影响的功能文档。
5. 每个实现层一个目录、一份 README（开发说明），F 编号在 README 与模块 docstring 中标注。
6. 版本、测试数、数据计数等数字**只在 `docs/current-status.md` 维护**，
   由 `scripts/check_docs.py --strict` 机械核对。
7. 文档与配置改动后跑一次 `python scripts/check_docs.py --strict`（相对链接、current 口径、
   `.env.example`、数据计数四类检查）。

## 八、路线图

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| RAGv1 | F09 快照与治理 + F11 文本切分与关键词索引（离线数据底座） | ✅ 已完成 |
| RAGv2 | F02–F06 在线问答链路 + SSE 服务 + F09 人工审核回填 | ✅ 已完成 |
| RAGv3 | F01/F07 前端页面 | ✅ 已完成 |
| RAGv4 | F10 评测（题库/指标/报告/人工评分） | ✅ 已完成 |
| RAGv5 | F08 演示模式 + 真实 LLM/向量接入与部署打磨 | ✅ 已完成 |

每个阶段交付了什么、当时的设计口径、踩过哪些坑，以及随后六轮独立审核整改的结论，
统一见 [docs/CHANGELOG.md](docs/CHANGELOG.md)。
