# 项目文档导航

本项目由**四套可独立运行的部分**组成，文档分散在各部分的 README 与 `docs/` 下。
本文档是唯一的文档总索引：**先在这里定位，再进对应目录细读**。
项目级 `docs/` 下只有四份文档（跨模块的集成、状态与历史），单模块细节在各自的 README。

```text
china-war/
├── README.md                       ← 项目总入口：架构、环境、启动、API（含「本机环境备注」）
├── docs/                           ← 本目录：跨模块的集成、状态与历史
│   ├── README.md                   （本文件：唯一的文档总索引）
│   ├── 项目现状与后续计划.md        （**先看这个**：一句话现状 / 已完成按主题 / 未完成按性质 / 后续计划 / 实测数字 / 接手须知）
│   ├── 集成与入口约定.md            （路径端口、两个服务的划分、并入模式构建与白屏、nginx 分流、安全边界、四个 Python 模块统一 3.11 的环境口径、排障）
│   └── 项目审查与修复历史.md        （历次审查发现与修复的**按主题**归纳：问题 / 现处理 / 验证证据 / 还剩什么）
├── deploy/README.md                ← 部署到服务器（nginx 配置、systemd 单元、初始化与自检脚本、安装门禁）
├── backend/README.md               ← 旧后端（Flask，:5000）
├── backend/docs/规则引擎与LLM问答设计.md
├── frontend/README.md              ← 旧前端（Vue3 + layui-vue 管理台，:3001）
├── entity-event-relation/README.md ← 知识抽取与评估（离线跑，不参与 Web 运行；包名 war_extraction）
│   └── data/annotations/README.md  ← 人工标注规范（评估的分子分母）
├── RAG/                            ← RAG 问答系统（代码在本仓库内，独立服务）
│   ├── README.md                   ← 代码组织、开发约定、运行方式
│   └── docs/                       ← README.md（功能总览与文档地图）、current-status.md（**唯一事实源**）、
│                                     CHANGELOG.md、architecture.md、data-contract.md、deploy.md、features.md
└── feishu-bot/                     ← 飞书知识问答机器人（项目级 IM 入口，可选）
    ├── README.md
    └── docs/                       ← 需求与方案.md / 开发文档.md / 修复历史.md
```

## 按目的找文档

| 我想…… | 看这里 |
| --- | --- |
| 把整套系统跑起来（本机开发） | 根 [README.md](../README.md) 的「环境配置」「运行项目」 |
| 搞清要装什么环境、为什么四个模块统一 3.11、服务器上怎么装依赖 | [集成与入口约定.md](集成与入口约定.md) 第三节 |
| **部署到服务器，让别人访问** | **[deploy/README.md](../deploy/README.md)** |
| 搞清一个域名下怎么分流、为什么有并入模式构建、页面白屏是什么原因 | [集成与入口约定.md](集成与入口约定.md) 第四、五节 |
| 搞清生产必须改哪些环境变量、鉴权与密钥怎么配 | [集成与入口约定.md](集成与入口约定.md) 第六、七节 |
| **想快速知道项目现在什么状态、还差什么** | **[项目现状与后续计划.md](项目现状与后续计划.md)**（一句话现状、已完成按主题、未完成按"需人工/代码待办/待决策"、后续按阶段带验收标准、实测数字、接手须知） |
| 查某个问题当初是怎么发现的、现在怎么处理、还剩什么 | [项目审查与修复历史.md](项目审查与修复历史.md)（按主题，共 17 个主题 + 三张附录：被证伪与更正的结论 / 前车之鉴 / 仍未收口的遗留项） |
| 查某个具体的实现细节（改了哪一行、哪条用例） | 用 [项目审查与修复历史.md](项目审查与修复历史.md) 里记的**提交号**回溯 Git 历史——历次工作单与审核报告已压缩进这份文档，逐条细节由 Git 承担 |
| **改代码前先看这个** | [项目现状与后续计划.md](项目现状与后续计划.md) 第六节「接手须知」：改各模块前的固定动作、每轮收尾动作、本机的坑 |
| 改旧后端的接口/数据模型 | [backend/README.md](../backend/README.md) |
| 改旧前端的页面/菜单 | [frontend/README.md](../frontend/README.md) |
| 理解规则引擎与旧问答的推理设计 | [backend/docs/规则引擎与LLM问答设计.md](../backend/docs/规则引擎与LLM问答设计.md) |
| 改 RAG 的功能/契约/检索链 | [RAG/docs/README.md](../RAG/docs/README.md) |
| 查 RAG 当前版本、测试数、数据计数 | [RAG/docs/current-status.md](../RAG/docs/current-status.md)（唯一事实源） |
| 查 RAG 的部署、发布与鉴权 | [RAG/docs/deploy.md](../RAG/docs/deploy.md)、[RAG/docs/architecture.md](../RAG/docs/architecture.md) |
| 查 RAG 阶段交付与历轮审核整改 | [RAG/docs/CHANGELOG.md](../RAG/docs/CHANGELOG.md) |
| 重跑知识抽取或评估 | [entity-event-relation/README.md](../entity-event-relation/README.md) |
| 改人工标注（评估的分子分母） | [data/annotations/README.md](../entity-event-relation/data/annotations/README.md)：字段口径、关系名取值表、五条已知局限、"改标注的流程" |
| 把 RAG 接进飞书（问答 / 纠错反馈） | [feishu-bot/README.md](../feishu-bot/README.md) → [开发文档](../feishu-bot/docs/开发文档.md)、[需求与方案](../feishu-bot/docs/需求与方案.md)、[修复历史](../feishu-bot/docs/修复历史.md) |
| 改部署脚本、systemd 单元或自检 | [deploy/README.md](../deploy/README.md)；配置结构由 `scripts/check_deploy_config.py` 机械检查（已进 CI） |

## 两个服务、四个进程

四个 Python 模块**统一在 Python 3.11**；本地共用一个 conda 环境，生产按服务拆环境是隔离选择
而不是版本要求（完整口径见 [集成与入口约定.md](集成与入口约定.md) 第三节）：

| 服务 | 端口 | 环境 | 运行方式 |
| --- | --- | --- | --- |
| RAG 问答（FastAPI，含前端 dist 同源托管） | 8000 | `china-war-py311`（Python 3.11）；服务器侧 `china-war-rag`（3.11.16） | `cd RAG && python scripts/run_server.py --port 8000 --version <版本>` |
| 旧后端（Flask） | 5000 | `china-war-py311`（Python 3.11）；服务器侧 `china-war-backend`（3.11.16） | `cd backend && python app.py` |
| 旧前端（Vite 开发服务器） | 3001 | —（Node ≥ 18） | `cd frontend && pnpm dev` |
| 飞书机器人（可选，长连接无端口） | — | `china-war-py311`（Python 3.11） | `python feishu-bot/main.py` |

浏览器只访问 **http://localhost:3001** 一个入口：`/api` 由 vite 代理到 5000，`/rag` 代理到 8000。
离线抽取链（`entity-event-relation/`）不占端口、不在 Web 链路上。
详细依赖与自检命令见根 README 的「运行项目」与 [集成与入口约定.md](集成与入口约定.md) 第四节。

## 文档维护约定

1. **数字只写一处**：RAG 的版本、测试数、数据计数只在 `RAG/docs/current-status.md` 维护，
   由 `RAG/scripts/check_docs.py --strict` 机械核对；其他文档引用而不复制。
   项目级的实测数字汇总在 [项目现状与后续计划.md](项目现状与后续计划.md) 第五节，并注明口径与日期。
2. **跨模块的约定写在本文档所在目录**（`docs/`），不要塞进根 README；
   单模块细节写在该模块自己的 README。项目级 `docs/` 只保留这四份文档，不新增同类过程文档。
3. **过程性记录不长期保留**：阶段开发说明、审核报告、整改工作单在收口后压缩为一份归纳文档
   （先例是 `RAG/docs/CHANGELOG.md`；项目级先例是 [项目审查与修复历史.md](项目审查与修复历史.md)——
   2026-09-26 把 34 份过程文档按主题压缩成这一份），逐条的改动细节由 Git 历史承担。
4. 改完文档跑一次对应仓库的文档检查（RAG 侧）：`cd RAG && python scripts/check_docs.py --strict`。
