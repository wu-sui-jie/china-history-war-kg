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
│   ├── docs/数据迭代记录.md        ← **要动数据先读这份**（版本对照：指纹 / 计数 / 指标、六步迭代流程）
│   ├── docs/抽样判定规范.md         ← **要做人工抽样判定前必读**（判据、分母口径、真实判例）
│   ├── docs/参考集重建规范.md       ← **要重建参考集前必读**（七步流程、工具、工作量）
│   └── data/annotations/README.md  ← 人工标注规范（评估的分子分母）
│       （模块 README 含「改抽取前必看：下游契约与静默失败清单」；过程记录由 git 历史承担）
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
| 查某个问题当初是怎么发现的、现在怎么处理、还剩什么 | [项目审查与修复历史.md](项目审查与修复历史.md)（按主题，共 30 个主题 + 三张附录：被证伪与更正的结论 / 前车之鉴 / 仍未收口的遗留项） |
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
| **要给数据做新一版 / 查两版之间差在哪** | **[数据迭代记录.md](../entity-event-relation/docs/数据迭代记录.md)**：版本对照（产物指纹 / 内容哈希 / 计数 / 三项指标）、这一版改了什么、为什么更好、六步迭代流程与验收口径 |
| **要做人工抽样判定 / 重建参考集** | [抽样判定规范.md](../entity-event-relation/docs/抽样判定规范.md)、[参考集重建规范.md](../entity-event-relation/docs/参考集重建规范.md)（两条人工线的作业规范，"动手前必读"） |
| **要改抽取链 / 提升抽取质量** | 模块 README 的 **[改抽取前必看：下游契约与静默失败清单](../entity-event-relation/README.md)**：跨模块硬/软依赖、枚举同步矩阵、静默失败清单、改键名时必须一次走完的七处。指标归因与历次整改方案属过程记录，已随收口删除（`git log` 可查） |
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
3. **过程性记录不长期保留——收口即压缩，不是归档。**
   阶段开发说明、审核报告、整改工作单、收尾执行单在**收口时**把结论提炼进"常驻文档"
   （模块 README / 现状文档 / 决策留档），**原件删除**；逐条细节由 Git 历史承担
   （`git log` 随时可查，而且比留在工作区里更完整）。
   - **反面教材（2026-09-27）**：`entity-event-relation/docs/` 一度堆到 10 份（6 份过程记录 +
     一个 `archive/`）。当时那次"归纳"只是把过程文档**移进 archive 文件夹**——换个地方堆放，
     数量没减、阅读面没小，等于没做。这次按本条真正收口：提炼出「下游契约」进模块 README、
     决策留档单独成节，然后删掉 6 份原件，`docs/` 只剩 3 份。
   - **判断标准**：一份文档如果"只有回看开发历程时才需要"，它就该进 git 历史；
     留下的必须能回答"**现在要动手做什么、按什么规矩做**"。
   - **不要为单个问题新开文档**：新知识加进已有的 README / 现状 / 迭代记录里。
4. **留存文档必须挂进索引**：模块 README 与本文档的「按目的找文档」要覆盖每一份留存文档，
   否则会重复 2026-09-26 那次"分析文档被压缩后无人找得到"的情况。
5. 改完文档跑一次文档检查：`cd RAG && python scripts/check_docs.py --strict`。
