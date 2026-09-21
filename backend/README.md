# backend — 旧知识库后端

Flask 应用，为管理台（`frontend/`）提供图谱查询、节点 CRUD、数据运营与旧版智能问答接口。

- **运行时**：Python 3.8（`place-name-KG` 环境），端口 **5000**
- **数据**：SQLite（主存储，WAL 模式）+ Neo4j（可视化与图谱查询，SQLite 变更后同步）
- **智能问答**：规则引擎（`rules/rule_base.json`，20 条）+ Ollama 本地大模型
- **问答设计细节**：见 [规则引擎与LLM问答设计.md](规则引擎与LLM问答设计.md)

## 目录结构

```text
backend/
├── app.py                    # Flask 应用入口与全部路由（:5000）
├── db_utils.py               # DbUtil：SQLite 读写 + 同步 Neo4j
├── model_search.py           # neo4j_db：Neo4j 图查询与节点写操作
├── models.py                 # SQLAlchemy 模型（UserInfo + 4 实体 + 4 关系表）
├── jwt_util.py               # JWT 签发与校验
├── common_utils.py           # 跨模块小工具：safe_text / safe_float / LRU 缓存
├── relation_types.py         # 事件-事件关系类型的唯一权威表（别名 ↔ 标准名）
├── import_json_to_sqlite.py  # 从抽取结果 JSON 导入 SQLite（--source 可指定）
├── sync_sqlite_to_neo4j.py   # SQLite → Neo4j 同步（全量/增量）
├── entity_extract/           # 实体抽取：规则快速命中 + Ollama 兜底
├── inference/                # 旧版智能问答：规则引擎与大模型集成
├── rules/rule_base.json      # 推理规则库（20 条：细分规则 17 + 复合规则 3）
├── data/current_dataset.json # 当前数据集元信息（由 import 脚本写入，app.py 5 处读取）
├── data/raw/                 # 原始战争史文本存档（**无代码读取**，仅作留档）
└── data/processed/           # 分表 JSON：由 import 脚本每次导入时重建，已 gitignore
├── database                  # SQLite 数据库文件
└── historical_places.txt     # 历史地名词典（jieba 自定义词典）
```

## 数据模型

| 模型 | 表名 | 说明 |
| --- | --- | --- |
| `UserInfo` | `UserInfo` | 用户信息（登录/注册） |
| `Event` | `events` | 战争事件（名称、类型、起止时间、朝代、地点、攻守方、结果、影响、坐标、质检标记等） |
| `Place` | `places` | 战争地点（历史地名、现代地名、省市区、具体位置、经纬度、坐标来源与置信度） |
| `Organization` | `organizations` | 势力组织（名称、类型、朝代、简介） |
| `Person` | `persons` | 历史人物（名称、朝代、所属势力、角色） |
| `EventEventRelation` | `event_event_relations` | 事件-事件关系（因果/顺承/并列/包含/条件） |
| `EventOrganizationRel` | `event_organization_rel` | 事件-组织关系（发起方/防守方/…） |
| `EventPlaceRelation` | `event_place_relations` | 事件-地点关系（主战场/出发地/…） |
| `EventPersonRelation` | `event_person_relations` | 事件-人物关系（统帅/谋士/…） |

Neo4j 侧节点标签为 `:Event` `:Place` `:Organization` `:Person`，关系类型即业务关系名。

## 数据流

```text
用户操作 → 前端请求 → Flask 路由（app.py）
    → SQLite 操作（db_utils.py，SQLAlchemy 模型 models.py）
    → 同步 Neo4j（model_search.py）→ 图谱可视化
```

**同步规则**：节点增删改一律先写 SQLite，再按 `neo4j_id` 同步 Neo4j；创建节点时把 Neo4j ID 回写
SQLite 的 `neo4j_id` 字段，供后续更新/删除定位。如果某节点还没有 `neo4j_id`，同步逻辑会按名字回退查找。

## 接口清单

### 认证

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/login` | POST | 登录，返回 JWT token |
| `/api/sign_in` | POST | 注册 |
| `/api/userinfo` | GET/POST | 当前用户信息 |
| `/user/menu` / `/user/permission` | GET | 菜单与权限（**前端实际未使用**，见下） |

除 `/`、`/api/login`、`/api/sign_in`、`/static*` 外，所有接口经全局 `before_request` 校验 token。
注意：未通过时响应体是 `{"code": 403, ...}`，但 **HTTP 状态码是 200**——判断鉴权必须读响应体里的 `code`。

### 图谱查询（读 Neo4j）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/search_name_kg` | POST | 按名称/类型/关系搜索图谱（前端图谱页用这个） |
| `/api/graph/event_event` | GET | 事件-事件关系图（支持 `name`、`rel_type`） |
| `/api/graph/event_organization` | GET | 事件-组织关系图 |
| `/api/graph/event_person` | GET | 事件-人物关系图 |
| `/api/graph/event_place` | GET | 事件-地点关系图 |
| `/api/graph/node_context` | GET | 指定节点的上下文子图 |
| `/api/node/relations` | GET | 指定节点的关联关系（图谱页点节点展开用） |
| `/api/node_types` / `/api/relationship_types` / `/api/relationship_types_by_Event` | GET | 类型枚举 |

### 节点管理（写 SQLite 并同步 Neo4j）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/find_node_page` | POST | 分页查询节点（管理台列表用） |
| `/create_node` / `/update_node` / `/delete_node` | POST | 节点增删改 |
| `/api/node/update_properties` | POST | 更新节点属性（质检页用） |
| `/api/node/detail` | GET | 节点详情（SQLite 优先，回退 Neo4j） |

### 数据运营

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/dashboard/overview` | GET | 首页仪表盘 |
| `/api/dataset/overview` / `/api/dataset/versions` / `/api/dataset/version_detail` | GET | 数据集概览、版本列表与详情 |
| `/api/quality/report` / `/api/quality/workbench` | GET | 图谱质检报告与工作台 |
| `/api/entity/detail` | GET | 实体详情（聚合关系、质检项、时间线） |
| `/api/timeline/overview` / `/api/timeline/events` | GET | 战争时间轴 |
| `/api/map/events` | GET | 事件地图点位（含坐标缺失标记） |
| `/api/relation-analysis/query` | GET/POST | 关系分析 |
| `/api/search/global` | GET | 全局搜索 |
| `/api/extract/entities-events` | POST | 文本实体/事件识别（调用 `entity-event-relation`） |

### 智能问答（旧版）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/ai/inference` | POST/GET | 非流式问答，返回回答 + 图谱数据 |
| `/api/ai/inference/stream` | POST | SSE 流式问答（前端实际使用） |

## 运行方式

```bash
# 用 place-name-KG 环境（Python 3.8.20，依赖已装齐）
cd backend
E:/anaconda/envs/place-name-KG/python.exe app.py
# 服务地址: http://localhost:5000
```

依赖（Flask、flask-cors、SQLAlchemy、PyJWT、py2neo、Werkzeug、requests、ollama）已装在该环境。
**`backend/` 下没有 `requirements.txt`**——如需在新机器复现，按上面清单安装即可。
首次启动会自动初始化 SQLite schema。

Ollama（智能问答用）：

```bash
ollama serve
ollama pull deepseek-r1:7b
```

## 数据导入与同步（可选，换数据集时用）

```bash
# 默认读取 entity-event-relation/output/.../9_final_all.json
python import_json_to_sqlite.py

# 也可指定数据源：单个结果 JSON，或发布子集
python import_json_to_sqlite.py --source ../entity-event-relation/output/<...>/9_final_all.json
python import_json_to_sqlite.py --source ../entity-event-relation/output/<...>/published/final.json

# 每次导入都会把源数据拆成 8 个分表 JSON 写到 data/processed/（供旧版按表读取）；
# 该目录已 gitignore，是产物不是输入，删掉也不影响下次导入。

# 将 SQLite 数据同步到 Neo4j
python sync_sqlite_to_neo4j.py
```

## 环境变量与配置位置

敏感配置统一由 `local_settings.py` 读取，顺序为**环境变量 → `backend/.env` → 占位默认值**；
真实值写在 `backend/.env`（已 gitignore，模板见 `backend/.env.example`）。

| 配置 | 键名 | 默认值 |
| --- | --- | --- |
| Neo4j 地址 / 用户 | `NEO4J_URI` / `NEO4J_USER` | `bolt://localhost:7687` / `neo4j` |
| Neo4j 口令 | `NEO4J_PASSWORD` | 无默认值；未配置时启动即报出配置指引 |
| JWT 密钥 | `JWT_SECRET` | 无默认值；未配置时进程内随机生成（重启后旧 token 失效，生产必须显式配置） |

```bash
cp backend/.env.example backend/.env    # 然后填入你的 Neo4j 口令与 JWT 密钥
```

非敏感配置：

| 配置 | 位置 | 默认值 |
| --- | --- | --- |
| SQLite 路径 | 各入口脚本按 `APP_PATH` 推导 | `backend/database` |
| Ollama 服务 | 各调用点（`ollama` 客户端默认） | `http://localhost:11434` |
| 推理规则库 | `rules/rule_base.json`（相对 CWD） | 必须从 `backend/` 目录启动 |

## 注意事项

1. **数据库初始化**：首次运行自动创建 SQLite 表结构；WAL 模式已启用
2. **同步是同步的**：节点操作会立即尝试同步 Neo4j，Neo4j 不可用会记录错误但不回滚 SQLite
3. **两套问答互不影响**：本模块的问答依赖 Ollama 常驻；RAG 问答是独立服务，见 `RAG/README.md`
4. **菜单接口是死代码**：`/user/menu`、`/user/permission` 从未被前端调用——前端由 mockjs 拦截并返回
   硬编码菜单，加菜单项要改三处，见 [../frontend/README.md](../frontend/README.md)
