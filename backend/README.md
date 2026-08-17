# 后端模块说明

## 目录结构

```
backend/
├── app.py                  # Flask应用入口，Web服务主文件
├── db_utils.py             # SQLite数据库操作工具类
├── model_search.py         # Neo4j图数据库查询与操作
├── models.py               # SQLAlchemy数据模型定义
├── jwt_util.py             # JWT认证工具
├── import_json_to_sqlite.py # JSON数据导入工具
├── sync_sqlite_to_neo4j.py  # SQLite到Neo4j同步工具
├── database                # SQLite数据库文件
├── requirements.txt        # Python依赖
├── historical_places.txt   # 历史实体词典（用于jieba）
│
├── entity_extract/         # 实体抽取模块（基于大模型）
│   ├── __init__.py
│   └── extractor.py        # 实体提取器（Ollama+deepseek）
│
├── inference/              # 智能问答模块
│   ├── __init__.py
│   └── rule_llm_integration.py  # 规则引擎与大模型集成
│
├── data/                   # 数据目录
│   ├── processed/          # 处理后的JSON数据
│   │   ├── 事件表_Event.json
│   │   ├── 人物表_Person.json
│   │   ├── 地点表_Place.json
│   │   ├── 组织表_Organization.json
│   │   ├── 事件-事件关系表_event_event_relations.json
│   │   ├── 事件-人物关系表_event_person_relations.json
│   │   ├── 事件-地点关系表_event_place_relations.json
│   │   └── 事件-组织关系表_event_organization_rel.json
│   └── raw/                # 原始数据文件
│
└── rules/                  # 规则配置
    └── rule_base.json      # 战争事件推理规则库
```

## 核心文件说明

### app.py
Flask应用主入口，提供以下功能：
- **用户认证**: 登录、注册、JWT token验证
- **知识图谱接口**: 搜索、关系图谱查询
- **节点管理接口**: 节点的增删改查（操作SQLite）
- **智能问答接口**: AI推理与知识图谱展示
- **数据同步**: SQLite到Neo4j的同步管理

主要接口：
```python
# 用户认证
POST   /api/login                    # 用户登录
POST   /api/sign_in                  # 用户注册
GET    /api/userinfo                 # 获取用户信息

# 知识图谱
POST   /search_name_kg               # 搜索图谱
GET    /api/graph/event_event        # 事件-事件关系
GET    /api/graph/event_organization # 事件-组织关系
GET    /api/graph/event_person       # 事件-人物关系
GET    /api/graph/event_place        # 事件-地点关系
GET    /api/node_types               # 获取节点类型
GET    /api/relationship_types       # 获取关系类型

# 节点管理
POST   /api/find_node_page           # 分页查询节点
POST   /create_node                  # 创建节点
POST   /update_node                  # 更新节点
POST   /delete_node                  # 删除节点
GET    /api/node/detail              # 获取节点详情
POST   /api/node/update_properties   # 更新节点属性

# 智能问答
POST   /api/ai/inference             # AI推理问答
```

### db_utils.py
SQLite数据库操作工具类 `DbUtil`，提供：
- **用户管理**: `authentication()`, `find_user()`, `add_user()`
- **节点管理**: `create_node()`, `update_node()`, `delete_node()`, `update_node_properties()`
- **数据查询**: `find_node_page()`, `get_node_detail_sqlite()`
- **数据导入**: 支持从JSON文件导入数据到SQLite

**数据同步机制**:
- 节点操作（增删改）首先执行SQLite操作
- 然后同步执行Neo4j操作
- 创建节点时保存Neo4j ID到SQLite的 `neo4j_id` 字段

### model_search.py
Neo4j图数据库操作类 `neo4j_db`，提供：
- **节点查询**: `search_nodes_by_name()`, `get_nodes_by_type()`
- **关系查询**: `get_relationship_types()`, `get_node_relations()`
- **关系图谱**: `get_event_event_relations()`, `get_event_organization_relations()` 等
- **节点操作**: `create_node()`, `update_node()`, `delete_node()`

### models.py
SQLAlchemy数据模型定义：

| 模型 | 表名 | 说明 |
|------|------|------|
| `UserInfo` | UserInfo | 用户信息表 |
| `Event` | events | 战争事件表 |
| `Place` | places | 战争地点表 |
| `Organization` | organizations | 势力组织表 |
| `Person` | persons | 历史人物表 |
| `EventEventRelation` | event_event_relations | 事件-事件关系表 |
| `EventOrganizationRel` | event_organization_rel | 事件-组织关系表 |
| `EventPlaceRelation` | event_place_relations | 事件-地点关系表 |
| `EventPersonRelation` | event_person_relations | 事件-人物关系表 |

### entity_extract/
实体抽取模块：
- **extractor.py**: 使用Ollama+deepseek-r1:7b大模型进行实体识别
- 支持提取四种类型的实体：战争事件、人物、组织、地点
- 包含实体相关性过滤功能，只返回最相关的实体
- 后处理包括去重、过滤和排序

### inference/
智能问答模块：
- **rule_llm_integration.py**: 规则引擎与大模型集成
- 支持基于知识图谱的问答推理
- 规则引擎支持战争事件-地点、战争事件-组织关系的反向推理
- 复合规则支持多步推理

## 数据库架构

### SQLite (主存储)
```sql
-- 事件表
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    neo4j_id INTEGER UNIQUE,
    name VARCHAR(255) NOT NULL,
    event_type VARCHAR(100),
    start_date VARCHAR(50),
    end_date VARCHAR(50),
    dynasty VARCHAR(100),
    place VARCHAR(255),
    aggressor VARCHAR(255),
    defender VARCHAR(255),
    person VARCHAR(255),
    action VARCHAR(50),
    result TEXT,
    scale VARCHAR(255),
    impact TEXT,
    source VARCHAR(255),
    relations VARCHAR(255),
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 地点表
CREATE TABLE places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    neo4j_id INTEGER UNIQUE,
    name VARCHAR(255) NOT NULL,
    modern_name VARCHAR(255),
    dynasty VARCHAR(100),
    province VARCHAR(100),
    city VARCHAR(100),
    district VARCHAR(100),
    specific_location VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 组织表
CREATE TABLE organizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    neo4j_id INTEGER UNIQUE,
    name VARCHAR(255) NOT NULL,
    org_type VARCHAR(50),
    dynasty VARCHAR(50),
    description TEXT,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 人物表
CREATE TABLE persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    neo4j_id INTEGER UNIQUE,
    name VARCHAR(255) NOT NULL,
    dynasty VARCHAR(100),
    org VARCHAR(255),
    role VARCHAR(255),
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Neo4j (可视化)
- **节点标签**: `:Event`, `:Place`, `:Organization`, `:Person`
- **关系类型**: 根据业务定义（如'发起方', '防守方', '主战场'等）

## 数据流向

```
用户操作
    ↓
前端请求
    ↓
Flask接口 (app.py)
    ↓
SQLite操作 (db_utils.py) ←→ SQLAlchemy模型 (models.py)
    ↓ (同步)
Neo4j操作 (model_search.py)
    ↓
图谱可视化
```

智能问答数据流：
```
用户问题
    ↓
实体提取 (entity_extract/extractor.py)
    ↓
图谱查询 (inference/rule_llm_integration.py)
    ↓
规则推理 + 大模型生成
    ↓
返回答案和图谱数据
```

## 运行方式

### 环境要求
- Python 3.8+
- Neo4j 5.x
- Ollama（用于大模型推理）

### 安装依赖
```bash
# 进入后端目录
cd backend

# 安装依赖
pip install -r requirements.txt
```

### 启动Ollama服务（用于智能问答）
```bash
# 启动Ollama服务
ollama serve

# 拉取模型（如果未安装）
ollama pull deepseek-r1:7b
```

### 启动后端服务
```bash
# 启动服务
python app.py

# 服务地址: http://localhost:5000
```

### 数据导入（可选）
```bash
# 默认直接导入完整提取结果 entity-event-relation/output/.../9_final_all.json
python import_json_to_sqlite.py

# 将SQLite数据同步到Neo4j
python sync_sqlite_to_neo4j.py
```

也可以手动指定数据源：

```bash
# 指定完整提取结果
python import_json_to_sqlite.py --source ../entity-event-relation/output/中国历代战争简史_测试数据/9_final_all.json

# 如需只导入高置信发布子集，也可以指定 published/final.json
python import_json_to_sqlite.py --source ../entity-event-relation/output/中国历代战争简史_测试数据/published/final.json

# 或兼容旧的 backend/data/processed 目录
python import_json_to_sqlite.py --source ./data/processed
```

## 环境变量

```bash
# Neo4j配置 (model_search.py中配置)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678

# SQLite配置 (app.py中配置)
SQLITE_PATH=./database

# Ollama配置 (默认使用本地服务)
OLLAMA_HOST=http://localhost:11434
```

## 注意事项

1. **数据库初始化**: 首次运行会自动创建SQLite表结构
2. **WAL模式**: SQLite已启用WAL模式，支持高并发
3. **数据同步**: 节点操作同步执行，确保数据一致性
4. **Neo4j ID**: 创建节点时会保存Neo4j ID到SQLite，用于后续更新/删除
5. **Ollama依赖**: 智能问答功能需要Ollama服务正常运行
6. **模型要求**: 首次使用需要下载deepseek-r1:7b模型（约4GB）

## 技术栈

- **Flask**: Web框架
- **SQLAlchemy**: ORM框架，操作SQLite
- **Py2neo**: Neo4j图数据库驱动
- **JWT**: 用户认证
- **Ollama**: 大模型服务框架
- **SQLite**: 主数据存储（WAL模式）
