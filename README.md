# 干戈纪略

## 系统架构

本项目采用 **SQLite + Neo4j** 双数据库架构：
- **SQLite**: 关系型数据库，作为主数据存储，负责数据的增删改查
- **Neo4j**: 图数据库，用于知识图谱可视化展示
- **数据流向**: SQLite → Neo4j（同步操作）

## 环境配置

### 1. 基础环境
- **JDK**: 17 (C:\Program Files\Java\jdk-17)
- **Python**: 3.8.10
- **Node.js**: 16+ (用于前端)

### 2. 数据库配置

#### Neo4j 图数据库
- **安装路径**: D:\neo4j\neo4j-community-5.26.19
- **用户名**: neo4j
- **密码**: 12345678
- **启动方式**:
  1. 终端输入 `neo4j console` 打开控制台
  2. 访问 http://localhost:7474/ 进入Neo4j前端页面

#### SQLite 数据库
- **文件位置**: `backend/database`
- **WAL模式**: 已启用，支持高并发读写

### 3. 安装依赖
```bash
pip install -r requirements.txt
```

## 项目结构

```
china-history-war-kg/
├── backend/              # 后端服务
│   ├── app.py           # Flask应用入口
│   ├── db_utils.py      # SQLite数据库操作
│   ├── model_search.py  # Neo4j图数据库操作
│   ├── models.py        # SQLAlchemy数据模型
│   ├── jwt_util.py      # JWT认证工具
│   ├── entity_extract/  # 实体抽取模块
│   ├── inference/       # 智能问答模块
│   └── data/            # 数据文件
│       ├── processed/   # 处理后的JSON数据
│       └── raw/         # 原始数据
├── frontend/            # 前端服务(Vue3)
│   ├── src/
│   │   ├── views/
│   │   │   ├── knowledge/
│   │   │   │   └── graph/      # 战争关系图
│   │   │   │       ├── OverviewGraph.vue    # 总览
│   │   │   │       ├── event/EventGraph.vue # 关联战争
│   │   │   │       ├── organization/        # 参战势力
│   │   │   │       ├── person/              # 相关人物
│   │   │   │       └── place/               # 发生地点
│   │   │   └── knowledge-list/  # 节点关系管理
│   │   │       ├── NodeLayout.vue
│   │   │       ├── event/EventNode.vue      # 战争事件
│   │   │       ├── organization/            # 参战组织
│   │   │       ├── person/                  # 相关人物
│   │   │       └── place/                   # 发生地点
│   │   └── layouts/     # 布局组件
│   └── ...
└── README.md
```

## 功能模块

### 1. 战争关系图
知识图谱可视化展示，包含以下子页面：

| 子页面 | 展示内容 | 关系筛选选项 |
|--------|----------|--------------|
| **关联战争** | 事件与事件之间的关系 | 因果关系、顺承关系、并列关系、包含关系、条件关系 |
| **参战势力** | 组织与事件之间的关系 | 发起方、防守方、支援方、同盟方、投降方、被俘方、议和方、调停方 |
| **相关人物** | 人物与事件之间的关系 | 统帅、将领、谋士、使者、君主、参与者、俘虏、阵亡、投降、叛变、可汗 |
| **发生地点** | 地点与事件之间的关系 | 主战场、次要战场、出发地、目的地、途经地、驻防地、指挥所、补给地、战略要地、议和地点 |

### 2. 节点关系管理
节点数据的增删改查管理，包含四个子页面：
- **战争事件**: 管理战争事件实体（名称、朝代、时间、地点等）
- **参战组织**: 管理势力组织实体（名称、朝代、组织类型等）
- **相关人物**: 管理历史人物实体（名称、朝代、所属势力、角色等）
- **发生地点**: 管理战争地点实体（名称、朝代、现代名称、所属省市等）

**数据操作说明**:
- 新增/修改/删除节点：直接操作SQLite数据库
- 同步机制：SQLite数据变更后同步更新到Neo4j
- 节点属性：支持完整的CRUD操作

### 3. 智能问答
基于大模型的历史战争知识问答系统：
- 自然语言提问
- 实体识别与关系抽取
- 知识图谱数据展示

## 运行项目

本项目由**两个独立环境**组成，运行时不同、需要分别启动；浏览器只访问旧前端一个入口：

| 环境 | 组成 | 运行时 | 端口 |
| --- | --- | --- | --- |
| 旧知识库系统 | Flask 后端（`backend/`）+ layui 管理台（`frontend/`） | Python 3.8、Node ≥ 18 | 5000 / 3001 |
| RAG 问答系统（独立仓库 `RAG/`） | FastAPI 服务 + Vue3 前端（dist 由 RAG 服务同源托管） | Python 3.11 | 8000 |

`frontend/vite.config.ts` 已配好代理：`/api` → 5000、`/rag` → 8000，所以浏览器只需访问
**http://localhost:3001**；RAG 问答页入口为菜单「知识图谱 → RAG 智能问答」，或直接访问 `/#/knowledge/rag`。

### 前置条件

**两个模块必须用不同的 Python 环境**：RAG 的 chromadb 要求 Python ≥ 3.10，而旧后端整套按
Python 3.8 编写（Flask + py2neo 生态），装进同一个环境必有一边跑不起来。

| 组件 | 要求 | 本机对应环境（`E:/anaconda`） |
| --- | --- | --- |
| Node.js | ≥ 18（旧前端与 RAG 前端构建） | —（走 Node/pnpm，不用 conda） |
| 旧后端 | Python 3.8+；Neo4j 5.x（默认 `bolt://localhost:7687`，图谱可视化与问答）；Ollama 及模型（旧智能问答用，见 [backend/README.md](backend/README.md)）；同级 `entity-event-relation/` 目录必须完整——后端通过 `sys.path` 引用其 `src.*`（`backend/app.py:56-59`） | **`place-name-KG`**（Python 3.8.20；Flask / flask-cors / SQLAlchemy / py2neo / PyJWT / werkzeug 已装齐） |
| RAG 服务 | Python 3.11 + RAG 依赖（见 `RAG/requirements.txt`：fastapi / uvicorn / chromadb / openai 等） | **`AI_Agent`**（Python 3.11.15；fastapi / uvicorn / chromadb / openai / jieba 已装齐） |
| RAG 前端产物 | `RAG/frontend/dist` 必须是**并入模式**构建产物（`npm run build:integration`），否则 `/rag/` 页面白屏 | — |

> `backend/` 目录下目前没有 `requirements.txt`（`backend/README.md` 里的 `pip install -r requirements.txt`
> 已失效）。实际依赖为 Flask、flask-cors、SQLAlchemy、PyJWT、py2neo、Werkzeug、requests 等；
> 本机 `place-name-KG` 环境已全部具备，直接用它即可，无需再装。

### 启动顺序

**1. 构建 RAG 前端产物**（首次、或 RAG 前端代码有改动时）

```bash
cd RAG/frontend
npm install                 # 首次
npm run build:integration   # 必须用并入模式：base=/rag/、接口前缀=/rag/api
```

> 若之前在 RAG 前端执行过 `npm run build`（独立部署模式），`dist` 会被覆盖成 `base=/`，
> 页面在 `/rag/` 下会白屏——补跑一次 `npm run build:integration` 即可恢复。
> 口径见 [docs/RAG集成-Web入口合并.md](docs/RAG集成-Web入口合并.md) 第三节。

**2. 启动 RAG 服务（:8000，用 `AI_Agent` 环境）**

```bash
cd RAG
E:/anaconda/envs/AI_Agent/python.exe scripts/run_server.py --port 8000 --version 20260915_v1
# Anaconda Prompt / CMD 下等价写法：
#   conda activate AI_Agent && python scripts/run_server.py --port 8000 --version 20260915_v1
```

- `--version` 固定数据版本（省略则自动取最新一致版本）

**3. 启动旧后端（:5000，用 `place-name-KG` 环境）**

```bash
cd backend
E:/anaconda/envs/place-name-KG/python.exe app.py
# Anaconda Prompt / CMD 下等价写法：
#   conda activate place-name-KG && python app.py
```

- 首次启动会自动初始化 SQLite schema；Neo4j/Ollama 配置与数据导入见 [backend/README.md](backend/README.md)
- 三个终端（RAG / 旧后端 / 旧前端）各用各的环境，互不影响

**4. 启动旧前端（:3001）**

```bash
cd frontend
pnpm install    # 首次（或 npm install）
pnpm dev        # 或 npm run dev
```

- 访问 **http://localhost:3001**（端口在 `vite.config.ts` 固定为 3001，不是 Vite 默认的 5173）

### 启动后自检

```bash
curl -s http://127.0.0.1:5000/api/graph/event_event | head -c 120   # 旧后端：应返回图谱 JSON
curl -s http://127.0.0.1:8000/api/health | head -c 200              # RAG：应返回 status=ok
curl -s http://127.0.0.1:3001/rag/ | grep assets                    # 代理：应看到 /rag/assets/... 前缀
```

更细的口径：旧后端 [backend/README.md](backend/README.md)、旧前端 [frontend/README.md](frontend/README.md)、
RAG 启动与部署 [RAG/README.md](RAG/README.md) 与 [RAG/docs/deploy.md](RAG/docs/deploy.md)、
两个环境的集成约定 [docs/RAG集成-Web入口合并.md](docs/RAG集成-Web入口合并.md)。

## API接口

### 知识图谱接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/search_name_kg` | POST | 搜索知识图谱 |
| `/api/graph/event_event` | GET | 获取事件-事件关系图 |
| `/api/graph/event_organization` | GET | 获取事件-组织关系图 |
| `/api/graph/event_person` | GET | 获取事件-人物关系图 |
| `/api/graph/event_place` | GET | 获取事件-地点关系图 |

### 节点管理接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/find_node_page` | POST | 分页查询节点 |
| `/create_node` | POST | 创建节点 |
| `/update_node` | POST | 更新节点 |
| `/delete_node` | POST | 删除节点 |
| `/api/node/detail` | GET | 获取节点详情 |

### 智能问答接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/ai/inference` | POST/GET | 智能问答 |

## 技术栈

### 后端
- **Flask**: Web框架
- **SQLAlchemy**: ORM框架，操作SQLite
- **Py2neo**: Neo4j图数据库驱动
- **JWT**: 用户认证
- **jieba**: 中文分词

### 前端
- **Vue 3**: 前端框架
- **TypeScript**: 类型安全
- **LayUI Vue**: UI组件库
- **ECharts**: 知识图谱可视化
- **Axios**: HTTP请求

## 注意事项

1. **数据存储**: 所有数据操作首先写入SQLite，然后同步到Neo4j
2. **节点ID**: 创建节点时会同时获取Neo4j ID并保存到SQLite
3. **可视化**: 知识图谱展示从Neo4j读取数据
4. **文字颜色**: 所有节点文字颜色为黑色，便于阅读
5. **图谱居中**: 图表加载完成后自动居中显示
