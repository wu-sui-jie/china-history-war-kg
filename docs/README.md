# 项目文档导航

本项目由**三套可独立运行的部分**组成，文档分散在各部分的 README 与 docs/ 下。
本文档是唯一的文档总索引：**先在这里定位，再进对应目录细读**。

```text
china-war/
├── README.md                     ← 项目总入口：架构、环境、启动、API
├── docs/                         ← 本目录：跨模块的集成与导航文档
│   ├── README.md                 （本文件）
│   └── 集成与入口约定.md          （路径/端口、并入模式构建、nginx、安全边界、排障）
├── backend/README.md             ← 旧后端（Flask，:5000）
├── frontend/README.md            ← 旧前端（Vue3 + layui-vue 管理台，:3001）
├── entity-event-relation/README.md ← 知识抽取与评估（离线跑，不参与 Web 运行）
└── RAG/                          ← RAG 问答系统（独立仓库，以 submodule 接入）
    ├── README.md                 ← 代码组织、开发约定、运行方式
    └── docs/README.md            ← 功能总览与文档地图（唯一事实源在 docs/current-status.md）
```

## 按目的找文档

| 我想…… | 看这里 |
| --- | --- |
| 把整套系统跑起来 | 根 [README.md](../README.md) 的「环境配置」「运行项目」 |
| 搞清一个域名下怎么分流、为什么有并入模式构建 | [集成与入口约定.md](集成与入口约定.md) |
| 改旧后端的接口/数据模型 | [backend/README.md](../backend/README.md) |
| 改旧前端的页面/菜单 | [frontend/README.md](../frontend/README.md) |
| 改 RAG 的功能/契约/检索链 | [RAG/docs/README.md](../RAG/docs/README.md) |
| 查 RAG 当前版本、测试数、数据计数 | [RAG/docs/current-status.md](../RAG/docs/current-status.md)（唯一事实源） |
| 理解规则引擎与旧问答的推理设计 | [backend/规则引擎与LLM问答设计.md](../backend/规则引擎与LLM问答设计.md) |
| 重跑知识抽取或评估 | [entity-event-relation/README.md](../entity-event-relation/README.md) |
| 追 RAG 阶段交付与历轮审核整改 | [RAG/docs/CHANGELOG.md](../RAG/docs/CHANGELOG.md) |

## 两个环境、三个服务

运行时**必须用两个不同的 Python 环境**（RAG 的 chromadb 要求 ≥3.10，旧后端整套按 3.8 编写）：

| 服务 | 端口 | 环境 | 运行方式 |
| --- | --- | --- | --- |
| RAG 问答（FastAPI，含前端 dist 同源托管） | 8000 | `AI_Agent`（Python 3.11） | `RAG/scripts/run_server.py` |
| 旧后端（Flask） | 5000 | `place-name-KG`（Python 3.8） | `backend/app.py` |
| 旧前端（Vite 开发服务器） | 3001 | —（Node ≥ 18） | `frontend` 下的 `pnpm dev` |

浏览器只访问 **http://localhost:3001** 一个入口：`/api` 由 vite 代理到 5000，`/rag` 代理到 8000。
详细依赖与自检命令见根 README 的「运行项目」。

## 文档维护约定

1. **数字只写一处**：RAG 的版本、测试数、数据计数只在 `RAG/docs/current-status.md` 维护，
   由 `RAG/scripts/check_docs.py --strict` 机械核对；其他文档引用而不复制。
2. **跨模块的约定写在本文档所在目录**（`docs/`），不要塞进根 README；
   单模块细节写在该模块自己的 README。
3. **过程性记录不长期保留**：阶段开发说明、审核报告、整改工作单在收口后压缩为一份归纳文档
   （如 `RAG/docs/CHANGELOG.md`），逐条细节由 Git 历史承担。
4. 改完文档跑一次对应仓库的文档检查（RAG 侧）：`python RAG/scripts/check_docs.py --strict`。
