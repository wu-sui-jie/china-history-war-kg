# backend — 旧知识库后端

Flask 应用，为管理台（`frontend/`）提供图谱查询、节点 CRUD、数据运营与旧版智能问答接口。

- **运行时**：Python 3.8（`place-name-KG` 环境），端口 **5000**
- **数据**：SQLite（主存储，WAL 模式）+ Neo4j（可视化与图谱查询，SQLite 变更后同步）
- **智能问答**：规则引擎（`rules/rule_base.json`，20 条）+ Ollama 本地大模型
- **问答设计细节**：见 [规则引擎与LLM问答设计.md](规则引擎与LLM问答设计.md)

## 目录结构

```text
backend/
├── app.py                    # Flask 应用入口：应用装配、全局钩子、启停（路由已拆到 blueprints/）
├── blueprints/               # 路由按业务分五组（P2-1 收官，URL 与拆分前逐字相同）
│   ├── auth.py               #   登录注册 / 账号信息 / 用户管理 / 菜单与权限
│   ├── node.py               #   节点增删改查与节点查询（写接口受 require_write_role 保护）
│   ├── graph.py              #   图谱检索、四类关系图、节点子图、关系分析、全局搜索
│   ├── workspace.py          #   仪表盘 / 数据集 / 质检 / 实体详情 / 时间轴 / 地图 / 修复工单
│   └── llm.py                #   旧问答（同步与 SSE）与文本抽取——都消耗 LLM 配额
├── roles.py                  # 角色常量（WRITE_ROLES / ROLE_RANKS / *_MENU_IDS）与鉴权装饰器
├── requirements.txt          # 本模块依赖声明（UTF-8，带版本下界）
├── db_utils.py               # DbUtil：SQLite 读写 + 同步 Neo4j
├── model_search.py           # neo4j_db：Neo4j 图查询与节点写操作
├── models.py                 # SQLAlchemy 模型（UserInfo + 4 实体 + 4 关系表）
├── jwt_util.py               # JWT 签发与校验（含 exp 过期声明）
├── common_utils.py           # 跨模块小工具：safe_text / safe_float / safe_identifier / LRU 缓存
├── relation_types.py         # 事件-事件关系类型的唯一权威表（别名 ↔ 标准名）
├── import_json_to_sqlite.py  # 从抽取结果 JSON 导入 SQLite（--source 可指定，需 --yes 确认重建）
├── sync_sqlite_to_neo4j.py   # SQLite → Neo4j 同步（全量/增量）
├── entity_extract/           # 实体抽取：规则快速命中 + Ollama 兜底
├── inference/                # 旧版智能问答：规则引擎与大模型集成
├── rules/rule_base.json      # 推理规则库（20 条：细分规则 17 + 复合规则 3）
├── data/current_dataset.json # 当前数据集元信息（由 import 脚本写入，app.py 5 处读取）
├── data/raw/                 # 原始战争史文本存档（**无代码读取**，仅作留档）
├── data/processed/           # 分表 JSON：由 import 脚本每次导入时重建，已 gitignore
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

### 序列化键的大小写风格（改前端前先看这张表）

四张表的 `to_dict()` 用了三套命名风格，是历史遗留（前端已有代码按类型特判）。**统一风格会同时改动
接口契约与前端**，所以短期只在这里记录映射，长期统一时按本表逐项替换：

| 模型 | 键风格 | 名称字段（SQLite 列 → 序列化键） | 其他字段示例 |
| --- | --- | --- | --- |
| `Event` | 大驼峰（对齐抽取文档字段名） | `name` → `EventName` | `EventType` `StartDate` `EndDate` `DynastyName` `Place` `Aggressor` `Defender` `KeyPersons` `Action` `Result` `TroopSize` `Impact` `Remark`，加小写的 `source_text` |
| `Place` | 小写下划线（含两处历史别名） | `name` → `geo_name` | `modern_name`，同时兼容 `ModernName`；`DynastyName` `Province` `City` `District_County` `Specific_location`（别名 `Specific_Location`）`longitude` `latitude` `coord_source` `coord_confidence` `coord_note` |
| `Organization` | 大驼峰 | `name` → `OrgName` | `OrgType` `DynastyName` `Description` `Remark` |
| `Person` | 大驼峰 | `name` → `PersonName` | `DynastyName` `OrgName`（注意：这里是所属势力，对应 SQLite 列 `org`）`Role` `Remark` |

所有模型都另有 `id` / `neo4j_id` / `created_at` 三个公共键（由 `_base_with_meta` 附加）。
前端取名称用 `utils/knowledge.ts` 的 `nodeDisplayName()`，它已按 `EventName → PersonName → OrgName → geo_name → name` 顺序兜底。

## 数据流

```text
用户操作 → 前端请求 → Flask 路由（app.py）
    → SQLite 操作（db_utils.py，SQLAlchemy 模型 models.py）
    → 同步 Neo4j（model_search.py）→ 图谱可视化
```

**同步规则**：节点增删改一律先写 SQLite，再按 `neo4j_id` 同步 Neo4j；创建节点时把 Neo4j ID 回写
SQLite 的 `neo4j_id` 字段，供后续更新/删除定位。如果某节点还没有 `neo4j_id`，同步逻辑会按名字回退查找。

删除的顺序相反：**先删 SQLite 并提交，再删 Neo4j**——这样 SQLite 侧失败时不会留下「Neo4j 已删、
SQLite 还在」的永久不一致。Neo4j 侧失败不再静默吞掉：响应里带 `sync_status` / `sync_error`，
质检接口 `/api/quality/report` 还会给出两侧节点计数对账（`sync_reconciliation`）。

## 接口清单

### 认证

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/login` | POST | 登录，返回 JWT token（含 `exp`，有效期默认 7 天，`JWT_TTL_SECONDS` 可调） |
| `/api/sign_in` | POST | 注册（创建的角色是 `viewer`，**只读**） |
| `/api/userinfo` | GET/POST | 当前 token 对应的用户信息（`id`/`account`/`name`/`role`；前端用它取账号 id 做问答记录隔离） |
| `/api/admin/users` | GET | 用户列表（仅 `admin`；响应不含口令字段） |
| `/api/admin/users/<id>/role` | POST | 改角色（仅 `admin`；不能改自己、角色值过白名单、用户不存在给 404） |
| `/user/menu` / `/user/permission` | GET | 菜单与权限（菜单按角色裁剪，见下「角色职责与三处口径」） |

除 `/`、`/api/login`、`/api/sign_in`、`/static*` 外，所有接口经全局 `before_request` 校验 token：

- 未带 token、token 过期或伪造：**HTTP 401** + 响应体 `{"code": 401, "msg": ...}`；
- token 有效但角色无写权限：**HTTP 403**（只作用于写接口，见下）。

**写权限角色**：`UserInfo.role` 为 `admin` / `editor` 才能调 `/create_node`、`/update_node`、
`/delete_node`、`/api/node/update_properties`、`/api/extract/entities-events`、`/api/ai/inference`、
`/api/ai/inference/stream`；注册得到的 `viewer` 只读。存量账号（`role` 为空）在启动迁移里回填，
**兜底值取最小权限 `viewer`**（空值只让人少看几个页面，不会让人多写几个接口）。

**角色职责与三处口径**（改权限前先看这张表）：

| 角色 | 读接口 | 写接口 | 数据运营菜单 | 文本实体识别 / 旧问答助手（消耗 LLM 配额） | 用户管理 |
| --- | --- | --- | --- | --- | --- |
| `viewer`（注册默认） | ✅ | ❌ 403 | ❌ | ❌ | ❌ |
| `editor` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |

两点容易混淆，写清楚：

- **消耗 LLM 配额的接口限 `editor` 及以上**：`/api/extract/entities-events`（文本实体识别）与
  `/api/ai/inference[/stream]`（旧「历史问答助手」）。后者入口已从导航栏下线、由 RAG 问答承接，
  但接口与页面都还在，所以按同一口径限制；
- **RAG 问答对全部角色开放**：`/knowledge/rag` 是独立服务，任何登录账号都能用它的问答。
  因此「问答要不要限角色」的答案是：**走 RAG 不限；走旧接口限 editor**。

分级语义是 `admin ⊃ editor ⊃ viewer`。同一个判断在三个地方各有一份，**改一处要一起改**：

| 位置 | 形式 |
| --- | --- |
| `backend/roles.py` | `ROLE_RANKS` 分级表；`require_write_role`（editor 级）、`require_admin`（admin 级）；菜单裁剪白名单 `ADMIN_MENU_IDS` / `EDITOR_MENU_IDS` 与可见性判断。第 7 轮路由蓝图拆分时从 `app.py` 抽成独立模块（`app.py` 现 415 行）。菜单数据本体的 `get_menu()` 在 `backend/blueprints/auth.py` |
| `frontend/src/router/` | 路由 `meta.requiresRole`（写"最低需要的角色"）+ `index.ts` 的 `ROLE_RANK` 比对 |
| `frontend/src/store/user.ts` | 菜单白名单（后端不下发的项不会出现） |
| `backend/tests/` | 常驻用例（**43 例**）：非 admin 进不去 `/api/admin/*`、菜单三级裁剪、提权/降权立刻生效、抽接口限 editor、抽取提示词与录制回放。`cd backend && python -m pytest tests -q`（第 6 轮审核 H4 建立，后续轮次扩充） |

**生效时机**：写接口的 403 是每次请求实时查库，改完立刻生效；**菜单是登录时下发的**，
被改角色的人需要重新登录（或重新触发 `loadMenus`）才会看到菜单变化。

**升级账号**（两个途径，任选）：

1. 管理员在界面上操作：「用户管理」页把角色下拉改掉再保存（仅 `admin` 可见）。
   防呆由服务端执行——不能改自己的角色（否则最后一个管理员可以把自己降级、系统失管），
   角色值必须在白名单内；
2. 兜底：直接改库（没有第二个管理员、或界面不可用时用）。

```sql
-- 提权（改成 admin 或 editor）
UPDATE UserInfo SET role = 'editor' WHERE account = 'someone';
-- 查看全部账号与角色
SELECT id, account, name, role FROM UserInfo ORDER BY id;
-- 确认提权结果
SELECT account, role FROM UserInfo WHERE account = 'someone';
```

改完提醒对方**重新登录**：菜单在登录时下发，否则对方界面上看不到新入口（接口权限已生效）。

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
| `/api/node/by_type` | GET | 按节点类型取节点列表（`type=Event\|Place\|Person\|Organization`） |
| `/api/node/by_relationship` | GET | 按关系类型取相关节点与关系 |
| `/api/node/search_by_name` | GET | 按名称模糊搜索节点（`name`、`limit`） |
| `/api/node_types` / `/api/relationship_types` / `/api/relationship_types_by_Event` | GET | 类型枚举 |

> 标签与关系类型无法参数化，代码侧用 `common_utils.safe_identifier()` 做白名单校验后内联；
> 其余一律走 Cypher 查询参数，不走字符串拼接。

### 节点管理（写 SQLite 并同步 Neo4j）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/find_node_page` | POST | 分页查询节点（管理台列表用） |
| `/create_node` / `/update_node` / `/delete_node` | POST | 节点增删改（需 `admin`/`editor` 角色） |
| `/api/node/update_properties` | POST | 更新节点属性（质检页用，需写权限） |
| `/api/node/detail` | GET | 节点详情（SQLite 优先，回退 Neo4j） |

### 数据运营

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/dashboard/overview` | GET | 首页仪表盘 |
| `/api/dataset/overview` / `/api/dataset/versions` / `/api/dataset/version_detail` | GET | 数据集概览、版本列表与详情 |
| `/api/quality/report` / `/api/quality/workbench` | GET | 图谱质检报告与工作台（含 SQLite↔Neo4j 计数对账） |
| `/api/repair/issues` | GET | 修复工作台问题列表（可按 `type` 过滤，前端「数据修复工作台」用） |
| `/api/entity/detail` | GET | 实体详情（聚合关系、质检项、时间线） |
| `/api/timeline/overview` / `/api/timeline/events` | GET | 战争时间轴 |
| `/api/map/events` | GET | 事件地图点位（含坐标缺失标记） |
| `/api/relation-analysis/query` | GET/POST | 关系分析 |
| `/api/search/global` | GET | 全局搜索 |
| `/api/extract/entities-events` | POST | 文本实体/事件识别（调用 `entity-event-relation`） |

### 用户管理（仅 admin）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/admin/users` | GET | 用户列表（id / 账号 / 昵称 / 角色，不含口令字段） |
| `/api/admin/users/<id>/role` | POST | 改角色，body `{"role": "admin" \| "editor" \| "viewer"}` |

两者都由 `@require_admin` 守：非 admin 一律 **403**。改角色还有两条防呆——不能改自己的角色
（否则最后一个管理员能把自己降级、系统失管，见上文提权说明），角色值必须在白名单内
（否则 400）。前端页面在「用户管理」（`frontend/src/views/admin/UserManagement.vue`），
菜单只对 admin 下发。

### 智能问答（旧版）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/ai/inference` | POST/GET | 非流式问答，返回回答 + 图谱数据 |
| `/api/ai/inference/stream` | POST | SSE 流式问答（前端实际使用） |

## 运行方式

```bash
# 用 place-name-KG 环境（Python 3.8.20，依赖已装齐；本机解释器路径见根 README 的「本机环境备注」）
conda activate place-name-KG
cd backend
python app.py
# 服务地址: http://localhost:5000
```

依赖见 [requirements.txt](requirements.txt)（`pip install -r backend/requirements.txt`）。
首次启动会自动初始化 SQLite schema，并做一次结构迁移（补 `UserInfo.role` 列、回填存量角色、
补 `account` 唯一索引）。

**监听地址与调试开关**（默认只监听本机、且不开 debug）：

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `BACKEND_HOST` | `127.0.0.1` | 监听地址。对局域网开放才改 `0.0.0.0` |
| `BACKEND_PORT` | `5000` | 监听端口 |
| `FLASK_DEBUG` | 关 | 设 `1`/`true` 开 Werkzeug 调试器。**开了就等于给对方任意代码执行，只在本机调试时开** |

Ollama（智能问答用）：

```bash
ollama serve
ollama pull deepseek-r1:7b
```

## 数据导入与同步（可选，换数据集时用）

```bash
# 默认读取 entity-event-relation/output/.../9_final_all.json
# 注意：会 drop_all() 重建整个 SQLite 库，因此必须显式加 --yes
python import_json_to_sqlite.py --yes

# 也可指定数据源：单个结果 JSON，或发布子集
python import_json_to_sqlite.py --source ../entity-event-relation/output/<...>/9_final_all.json --yes
python import_json_to_sqlite.py --source ../entity-event-relation/output/<...>/published/final.json --yes

# 每次导入都会把源数据拆成 8 个分表 JSON 写到 data/processed/（供旧版按表读取）；
# 该目录已 gitignore，是产物不是输入，删掉也不影响下次导入。

# 将 SQLite 数据同步到 Neo4j（关系用 MERGE 写入，重复执行不会产生重复边）
python sync_sqlite_to_neo4j.py --mode full
```

## 环境变量与配置位置

敏感配置统一由 `local_settings.py` 读取，顺序为**环境变量 → `backend/.env` → 占位默认值**；
真实值写在 `backend/.env`（已 gitignore，模板见 `backend/.env.example`）。

| 配置 | 键名 | 默认值 |
| --- | --- | --- |
| Neo4j 地址 / 用户 | `NEO4J_URI` / `NEO4J_USER` | `bolt://localhost:7687` / `neo4j` |
| Neo4j 口令 | `NEO4J_PASSWORD` | 无默认值；未配置时启动即报出配置指引 |
| JWT 密钥 | `JWT_SECRET` | 无默认值；未配置时进程内随机生成（重启后旧 token 失效，生产必须显式配置） |
| JWT 有效期（秒） | `JWT_TTL_SECONDS` | `604800`（7 天） |
| 监听地址 / 端口 | `BACKEND_HOST` / `BACKEND_PORT` | `127.0.0.1` / `5000` |
| 调试开关 | `FLASK_DEBUG` | 关（生产必须保持关闭） |

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

0. **改接口先找对文件**：路由在 `blueprints/` 下按业务分组（五组见上表），
   `app.py` 只留应用装配与全局钩子（鉴权 `before_request`、实体提取器单例绑定）。**蓝图不加
   url_prefix**，URL 必须与拆分前逐字相同；改动的行为不变性由
   `python tools/snapshot_responses.py` 的 67 请求前后对照兜底（见该脚本的文件头）。
1. **数据库初始化**：首次运行自动创建 SQLite 表结构，并做一次结构迁移（`UserInfo.role` 列、
   `account` 唯一索引、关系表证据字段）；WAL 与 `synchronous=NORMAL` 由 SQLAlchemy 的
   connect 事件钩子在**每个新连接**上设置，不依赖启动时那一次 PRAGMA
2. **同步失败不再静默**：节点操作会立即尝试同步 Neo4j，失败时 SQLite 不回滚，但响应里带
   `sync_status=failed` 与原因；`/api/quality/report` 的两侧计数对账（`sync_reconciliation`）
   与工作台 summary 的 `sync_mismatch` 用来发现累积的缺口
3. **两套问答互不影响**：本模块的问答依赖 Ollama 常驻；RAG 问答是独立服务，见 `RAG/README.md`
4. **菜单接口**：`/user/menu`、`/user/permission` 由前端直连本接口（mockjs 已在 2026-09 移除，
   生产包不含它）。菜单项要改三处（后端接口、`frontend/src/store/user.ts` 白名单、路由表），
   见 [../frontend/README.md](../frontend/README.md)
