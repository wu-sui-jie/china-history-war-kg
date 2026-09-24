# 干戈纪略 · 中国历史战争知识图谱

一套以中国历代战争史为对象的知识图谱与智能问答系统，由三部分组成：

| 部分 | 内容 | 运行时 |
| --- | --- | --- |
| **旧知识库系统** | `backend/`（Flask + SQLite + Neo4j）与 `frontend/`（Vue3 + layui-vue 管理台）：图谱可视化、节点关系管理、数据运营、旧版智能问答 | Python 3.8 / Node ≥ 18 |
| **RAG 问答系统** | `RAG/`（代码在本仓库内，但作为独立服务单独部署）：图谱 + 文本双通道检索增强问答，自带 Vue3 前端 | Python 3.11 |
| **飞书机器人** | `feishu-bot/`：项目级 IM 入口，把 RAG 接进飞书（长连接，无需公网回调）；技能框架起步两个技能（知识问答 / 纠错反馈） | Python 3.11 / Node ≥ 18（子图出图） |
| **知识抽取** | `entity-event-relation/`：从战争史文献抽取实体/事件/关系的离线流水线，附学术评估 | Python 3.8+ |

> **先看文档总索引：[docs/README.md](docs/README.md)** —— 按"我想做什么"定位到具体文档。
> 想知道「现在什么状态、还差什么、下一步做什么」：[docs/项目现状与后续计划.md](docs/项目现状与后续计划.md)。
> 跨模块的路径/端口/反代与安全边界见 [docs/集成与入口约定.md](docs/集成与入口约定.md)。

## 系统架构

本项目采用 **SQLite + Neo4j** 双数据库架构：

- **SQLite**：关系型数据库，作为主数据存储，负责数据的增删改查
- **Neo4j**：图数据库，用于知识图谱可视化展示与问答时的图谱查询
- **数据流向**：SQLite → Neo4j（同步操作）

RAG 子系统不直接连这两个库，只读取自己目录下的治理快照与索引（由 RAG 的 F09/F11 离线流程从旧库导出）。

## 环境配置

### 基础环境

- **JDK**: 17（Neo4j 5.x 要求）
- **Python**: 3.8+（旧后端与知识抽取）/ 3.11（RAG——chromadb 要求 ≥ 3.10，因此两者必须分开建环境）
- **Node.js**: ≥ 18（旧前端与 RAG 前端构建）

> 本机（作者开发机）的安装位置与 conda 环境名集中在文末「本机环境备注」，与代码无关，
> 换机器时按本节要求自行安装即可。

### 数据库配置

**Neo4j 图数据库**

- **版本**: 5.x
- **连接**: 默认 `bolt://localhost:7687`，用户名 `neo4j`，口令由 `backend/.env` 的 `NEO4J_PASSWORD` 提供（见下）
- **启动方式**: 终端执行 `neo4j console`，然后访问 http://localhost:7474/

**SQLite 数据库**

- **文件位置**: `backend/database`
- **WAL 模式**: 已启用，支持高并发读写

**Ollama**（旧版智能问答用）

```bash
ollama serve                 # 启动服务
ollama pull deepseek-r1:7b   # 首次需拉取模型（约 4GB）
```

## 项目结构

```text
china-war/
├── README.md                  # 本文件：项目总入口
├── docs/                      # 文档总索引 + 跨模块集成约定
├── deploy/                    # 部署到服务器（nginx + systemd + 脚本）→ deploy/README.md
├── backend/                   # 旧后端（Flask + SQLite + Neo4j）→ backend/README.md
│   ├── app.py                 #   Flask 应用入口（:5000）
│   ├── db_utils.py            #   SQLite 读写与 Neo4j 同步
│   ├── model_search.py        #   Neo4j 图查询
│   ├── models.py              #   SQLAlchemy 数据模型
│   ├── common_utils.py        #   跨模块小工具（safe_text / LRU 等）
│   ├── relation_types.py      #   事件-事件关系类型唯一权威表
│   ├── import_json_to_sqlite.py / sync_sqlite_to_neo4j.py   # 数据导入与同步
│   ├── entity_extract/        #   实体抽取（规则 + Ollama 兜底）
│   ├── inference/             #   旧版智能问答（规则引擎 + 大模型）
│   ├── rules/rule_base.json   #   推理规则库（20 条）
│   └── data/                  #   current_dataset.json（数据集元信息）
│                              #   processed/ 由导入脚本重建（已 gitignore）；raw/ 原始文本存档
├── frontend/                  # 旧前端（Vue3 + TS + layui-vue，:3001）→ frontend/README.md
│   └── src/
│       ├── views/knowledge/       # 图谱可视化、实体详情、时间轴、地图、RAG 入口
│       ├── views/knowledge-list/  # 节点关系管理（事件/组织/人物/地点）
│       ├── views/inference/       # 旧版智能问答页
│       ├── views/workspace/       # 数据运营（仪表盘/数据集/质检/修复）
│       └── layouts/ router/ api/ store/ utils/
├── entity-event-relation/     # 知识抽取与评估（离线）→ entity-event-relation/README.md
├── RAG/                       # RAG 问答系统（独立服务，代码在本仓库内）→ RAG/README.md
├── feishu-bot/                # 飞书知识问答机器人（项目级 IM 入口）→ feishu-bot/README.md
│   ├── main.py                #   入口：配置校验 → 建表 → 自检 → ws 长连接
│   ├── bot/                   #   事件接入/会话/技能/卡片/子图出图
│   ├── render/                #   Node SSR 出图脚本（echarts + resvg + d3-force）
│   └── scripts/               #   一致性回归、过期数据清理
└── requirements.txt           # 旧项目的 Python 依赖（见下方说明）
```

`backend/` 目前没有 `requirements.txt`。实际依赖为 Flask、flask-cors、SQLAlchemy、PyJWT、py2neo、
Werkzeug、requests 等；本机 `place-name-KG` 环境已全部具备，直接用它即可，无需再装。

## 功能模块

### 1. 战争关系图

知识图谱可视化，分四个子页面：**关联战争**（事件-事件）、**参战势力**（事件-组织）、
**相关人物**（事件-人物）、**发生地点**（事件-地点）。每页支持按名称搜索与按关系类型筛选，
点击节点可展开其关联关系。四个子页面由同一份实现按维度参数化渲染
（`frontend/src/views/knowledge/graph/EntityGraph.vue`），新增维度只需加一份配置。

### 2. 节点关系管理

战争事件 / 参战组织 / 相关人物 / 发生地点四类实体的增删改查与属性编辑，
直接操作 SQLite 并同步到 Neo4j（创建节点时保存 Neo4j ID 到 SQLite 的 `neo4j_id` 字段）。

### 3. 智能问答（两套并存）

| 入口 | 实现 | 说明 |
| --- | --- | --- |
| 菜单「历史问答助手」 | `backend/inference/` + `frontend/src/views/inference/` | 规则引擎（20 条推理规则）辅助大模型回答，SSE 流式输出，附带知识图谱可视化 |
| 菜单「RAG 智能问答」 | `RAG/`（iframe 承载） | 图谱 + 文本双通道检索、证据溯源引用、知识面板（实体卡/子图/时间线/地图） |

两者的关系与边界见 [docs/集成与入口约定.md](docs/集成与入口约定.md)。

## 运行项目

> **要部署到服务器**（让其他人用浏览器访问、你的电脑不用开机）：见 **[deploy/README.md](deploy/README.md)** ——
> 里面备好了 nginx 配置、systemd 服务单元与初始化/自检脚本，照做即可。以下内容是**本机开发**口径。

> ⚠️ **克隆后先看这条：仓库里没有数据，这是正常的。**
> 数据制品一律不入库（体积大，且原书全文受版权约束）：`backend/database`（SQLite 主库）、
> `entity-event-relation/output/`（抽取结果）、`RAG/data/snapshot`、`RAG/data/index` 都要本地重建。
> 直接启动的话，旧后端是**空表**、RAG 会报数据版本缺失——不是环境装错了。

### 数据从哪来

四步依赖关系（各步的详细参数见对应模块 README）：

```text
原书文本 → ① 知识抽取 → ② 导入 SQLite → ③ 同步 Neo4j
                              └────────→ ④ 导出 RAG 快照 → 建索引 → 起服务
```

| 步骤 | 在哪执行 | 命令 | 产物 |
| --- | --- | --- | --- |
| ① 知识抽取 | `entity-event-relation/` | `python main.py data/中国历代战争简史.txt` | `output/<批次>/9_final_all.json` |
| ② 导入 SQLite | `backend/` | `python import_json_to_sqlite.py --yes` | `backend/database` |
| ③ 同步 Neo4j | `backend/` | `python sync_sqlite_to_neo4j.py --mode full` | Neo4j 图数据 |
| ④ 导出快照与索引 | `RAG/` | `python scripts/export_snapshot.py` → `python scripts/build_index.py` | `RAG/data/snapshot`、`RAG/data/index` |

说明：

- ①需要自备原书文本（放 `entity-event-relation/data/`，见该模块 README 第四节），且要配置大模型 API；
  不做抽取、只想把系统跑起来时，从已有环境拷贝一份 `9_final_all.json`，直接从第 ② 步开始即可。
- ②会 **drop_all() 重建整个 SQLite 库**，因此默认拒绝执行，确认覆盖时加 `--yes`。
- ④的 `RAG/data/snapshot` 与 `RAG/data/index` **必须同名版本**，否则 RAG 启动即报版本不一致。

本项目由**两个独立环境**组成，运行时不同、需要分别启动；浏览器只访问旧前端一个入口：

| 环境 | 组成 | 运行时 | 端口 |
| --- | --- | --- | --- |
| 旧知识库系统 | Flask 后端（`backend/`）+ layui 管理台（`frontend/`） | Python 3.8、Node ≥ 18 | 5000 / 3001 |
| RAG 问答系统（`RAG/`） | FastAPI 服务 + Vue3 前端（dist 由 RAG 服务同源托管） | Python 3.11 | 8000 |

`frontend/vite.config.ts` 已配好代理：`/api` → 5000、`/rag` → 8000，所以浏览器只需访问
**http://localhost:3001**；RAG 问答页入口为菜单「知识图谱 → RAG 智能问答」，或直接访问 `/#/knowledge/rag`。

### 前置条件

**两个模块必须用不同的 Python 环境**：RAG 的 chromadb 要求 Python ≥ 3.10，而旧后端整套按
Python 3.8 编写（Flask + py2neo 生态），装进同一个环境必有一边跑不起来。

| 组件 | 要求 | 本机对应环境（`E:/anaconda`） |
| --- | --- | --- |
| Node.js | ≥ 18（旧前端与 RAG 前端构建） | —（走 Node/pnpm，不用 conda） |
| 旧后端 | Python 3.8+；Neo4j 5.x（图谱可视化与问答）；Ollama 及模型（旧问答用）；抽取链包 `war_extraction` 需装好——`pip install -e entity-event-relation`（P2-4 起为正式包，不再靠 `sys.path` 注入） | **`place-name-KG`**（Python 3.8.20） |
| RAG 服务 | Python 3.11 + RAG 依赖（见 `RAG/requirements.txt`） | **`AI_Agent`**（Python 3.11.15） |
| RAG 前端产物 | `RAG/frontend/dist` 必须是**并入模式**构建产物（`npm run build:integration`），否则 `/rag/` 页面白屏 | — |

### 启动顺序

**1. 构建 RAG 前端产物**（首次、或 RAG 前端代码有改动时）

```bash
cd RAG/frontend
npm install                 # 首次
npm run build:integration   # 必须用并入模式：base=/rag/、接口前缀=/rag/api
```

> 若之前在 RAG 前端执行过 `npm run build`（独立部署模式），`dist` 会被覆盖成 `base=/`，
> 页面在 `/rag/` 下会白屏——补跑一次 `npm run build:integration` 即可恢复。
> 口径见 [docs/集成与入口约定.md](docs/集成与入口约定.md) 第三节。

**2. 启动 RAG 服务（:8000，用 `AI_Agent` 环境）**

```bash
cd RAG
E:/anaconda/envs/AI_Agent/python.exe scripts/run_server.py --port 8000 --version 20260915_v1
# Anaconda Prompt / CMD 下等价写法：
#   conda activate AI_Agent && python scripts/run_server.py --port 8000 --version 20260915_v1
```

`--version` 固定数据版本（省略则自动取最新一致版本；生产档下必须显式指定）。

**3. 启动旧后端（:5000，用 `place-name-KG` 环境）**

```bash
# 首次（或 entity-event-relation 有改动时）：装依赖 + 以可编辑方式装上抽取链包
E:/anaconda/envs/place-name-KG/python.exe -m pip install -r requirements.txt
E:/anaconda/envs/place-name-KG/python.exe -m pip install -e entity-event-relation

cd backend
E:/anaconda/envs/place-name-KG/python.exe app.py
# Anaconda Prompt / CMD 下等价写法：
#   conda activate place-name-KG && pip install -r requirements.txt && pip install -e entity-event-relation && cd backend && python app.py
```

> 抽取链 `war_extraction` 已是正式包（P2-4），**漏装第二步会在导入时报
> `ModuleNotFoundError: No module named 'war_extraction'`**。

首次启动会自动初始化 SQLite schema；Neo4j/Ollama 配置与数据导入见 [backend/README.md](backend/README.md)。

**4. 启动旧前端（:3001）**

```bash
cd frontend
pnpm install    # 首次（或 npm install）
pnpm dev        # 或 npm run dev
```

访问 **http://localhost:3001**（端口在 `vite.config.ts` 固定为 3001，不是 Vite 默认的 5173）。

### 启动后自检

```bash
curl -s http://127.0.0.1:5000/api/graph/event_event | head -c 120   # 旧后端：应返回图谱 JSON
curl -s http://127.0.0.1:8000/api/health | head -c 200              # RAG：应返回 status=ok
curl -s http://127.0.0.1:3001/rag/ | grep assets                    # 代理：应看到 /rag/assets/... 前缀
```

更细的口径：旧后端 [backend/README.md](backend/README.md)、旧前端 [frontend/README.md](frontend/README.md)、
RAG 启动与部署 [RAG/README.md](RAG/README.md) 与 [RAG/docs/deploy.md](RAG/docs/deploy.md)、
两个环境的集成约定 [docs/集成与入口约定.md](docs/集成与入口约定.md)。

## API 接口

### 知识图谱接口（旧后端）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/search_name_kg` | POST | 搜索知识图谱（按名称/类型/关系分发） |
| `/api/graph/event_event` | GET | 事件-事件关系图 |
| `/api/graph/event_organization` | GET | 事件-组织关系图 |
| `/api/graph/event_person` | GET | 事件-人物关系图 |
| `/api/graph/event_place` | GET | 事件-地点关系图 |
| `/api/node/relations` | GET | 指定节点的关联关系 |

### 节点管理接口（旧后端）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/find_node_page` | POST | 分页查询节点 |
| `/create_node` | POST | 创建节点 |
| `/update_node` | POST | 更新节点 |
| `/delete_node` | POST | 删除节点 |
| `/api/node/detail` | GET | 获取节点详情 |
| `/api/node/update_properties` | POST | 更新节点属性 |

### 数据运营接口（旧后端）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/dashboard/overview` | GET | 首页仪表盘 |
| `/api/dataset/overview` | GET | 数据集概览 |
| `/api/dataset/versions` | GET | 数据版本列表 |
| `/api/quality/workbench` | GET | 图谱质检工作台 |
| `/api/entity/detail` | GET | 实体详情（含关系/质检/时间线） |
| `/api/timeline/overview` | GET | 战争时间轴总览 |
| `/api/map/events` | GET | 事件地图点位 |

### 问答接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/ai/inference` | POST | 旧版智能问答（非流式） |
| `/api/ai/inference/stream` | POST | 旧版智能问答（SSE 流式） |
| `/api/query` | POST | RAG 问答（SSE 流式，由 RAG 服务提供） |
| `/api/query/json` | POST | RAG 问答（非流式，一次取完整结果；飞书机器人等非浏览器调用方使用，可选 `X-Bot-Key`） |
| `/api/health` / `/api/dicts` / `/api/demo/examples` | GET | RAG 健康检查 / 筛选项词典 / 演示示例题 |

完整接口清单与请求/响应示例见 [backend/README.md](backend/README.md)（旧后端）与
[RAG/docs/data-contract.md](RAG/docs/data-contract.md)（RAG）。

## 技术栈

**旧后端**：Flask、SQLAlchemy、Py2neo、JWT、jieba、Ollama（大模型服务）

**旧前端**：Vue 3、TypeScript、layui-vue、ECharts（图谱可视化）、Axios、Pinia、Vite

**RAG**：FastAPI、pydantic、chromadb（向量检索）、jieba、Vue 3（前端）、百炼 `text-embedding-v4`

## 注意事项

1. **数据存储**：所有数据操作首先写入 SQLite，然后同步到 Neo4j
2. **节点 ID**：创建节点时会同时获取 Neo4j ID 并保存到 SQLite，供后续更新/删除使用
3. **可视化**：知识图谱展示从 Neo4j 读取数据
4. **两套问答互不影响**：旧问答依赖 Ollama 常驻；RAG 无 LLM 密钥时走离线摘要回答器，仍可端到端验收
5. **密钥一律走 .env**（两个子系统都是，均已 gitignore）：RAG 见 `RAG/.env.example`；
   旧后端见 `backend/.env.example`（`NEO4J_PASSWORD`、`JWT_SECRET`）。
   当前 HEAD 的源码里没有明文凭据，但 **git 历史（初始提交 `57eea5b`）里曾提交过旧口令与密钥，
   旧值视同已泄露**——部署前务必轮换，步骤见 [deploy/README.md](deploy/README.md) 第六节第 7 条。

---

## 本机环境备注

> 以下只是作者开发机的实际安装位置，**不是项目要求**；换机器按「环境配置」一节自装即可。
> 文档正文里不再重复这些路径。

| 项 | 本机位置 |
| --- | --- |
| conda 根目录 | `E:/anaconda` |
| 旧后端 / 知识抽取环境 | `E:/anaconda/envs/place-name-KG`（Python 3.8.20） |
| RAG / 飞书机器人环境 | `E:/anaconda/envs/AI_Agent`（Python 3.11.15） |
| JDK | `C:\Program Files\Java\jdk-17` |
| Neo4j | `D:\neo4j\neo4j-community-5.26.19` |

