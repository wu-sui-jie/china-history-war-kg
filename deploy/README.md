# 部署到服务器

> 目标：把整套系统装到一台服务器上，其他人用浏览器访问，**你的电脑不用开机**。
>
> 本目录是部署包：nginx 配置、systemd 服务单元、环境变量模板、7 个脚本（`scripts/` 下四个编号脚本
> 加 `install_services.sh`、`check_rag_auth.sh`、`selfcheck.sh`）。按本文从上到下执行即可。
> 路径与端口的完整约定见 [../docs/集成与入口约定.md](../docs/集成与入口约定.md)，本文只讲怎么落地。

---

## 一、先看清楚要部署什么

系统由 **4 个进程 + 2 个外部服务** 组成。只有 nginx 的 80 端口对外，其余端口一律只听本机。

| 组件 | 目录 | 运行时 | 端口 | 作用 | 是否必需 |
| --- | --- | --- | --- | --- | --- |
| 旧后端（Flask） | `backend/` | Python 3.8 | 5000（仅本机） | 图谱增删改查、数据运营、旧版问答 | 必需 |
| 旧前端（管理台） | `frontend/` | 静态文件 | 由 nginx 发出 | 所有页面入口 | 必需 |
| RAG 问答服务 | `RAG/` | Python 3.11 | 8000（仅本机） | RAG 问答 + 自带前端 | 必需 |
| 图数据库 | — | JDK 17 | 7687（仅本机） | Neo4j，图谱可视化与查询 | 必需 |
| 飞书机器人 | `feishu-bot/` | Python 3.11 | 无（长连接） | 飞书里问答、纠错反馈 | 可选 |
| 旧版问答大模型 | — | — | 11434（仅本机） | Ollama，只服务旧版问答 | 可选 |

访问流向：

```
                    浏览器（用户）
                          │  http://你的域名或IP/
                          ▼
                    nginx :80（唯一对外入口）
        ┌─────────────────┼──────────────────────┐
        │ /               │ /static/*            │ /rag/*        │ 其余
        ▼                 ▼                      ▼               ▼
  frontend/dist/     frontend/dist/         RAG :8000      Flask :5000
   index.html         assets、图片           页面 + API      图谱 CRUD
                                                   ▲               │
                                                   │               ▼
                                       飞书机器人 ──┘       SQLite + Neo4j
                                     （长连接，不走 nginx）
```

**两个 Python 环境不能合并**：RAG 的 chromadb 要求 Python ≥ 3.10，旧后端的 Flask + py2neo 按 3.8 编写。
所以服务器上要建两个独立环境（脚本已内置）。

---

## 二、动手前的四个决定

| 决定 | 影响什么 | 本文默认 |
| --- | --- | --- |
| **1. 服务器系统** | 脚本按 Ubuntu 22.04/24.04 写；CentOS/Rocky 需把 `apt` 换成 `dnf` | Ubuntu |
| **2. 访问地址** | 只用 IP：nginx `server_name _`、RAG 的 `CORS_ALLOW_ORIGINS=http://IP`；有域名则都填域名 | 公网 IP |
| **3. 要不要 HTTPS** | 有域名就上 certbot（本文第八节），纯 IP 只能 HTTP | 先 HTTP |
| **4. 要不要旧版问答 / 飞书机器人** | 旧版问答要额外装 Ollama 并常驻 7B 模型（约 8 GB 内存）；飞书机器人要飞书开放平台建应用 | 都可不装 |

第 4 项不作决定也不阻塞：**不装 Ollama，RAG 问答照常可用**（菜单里的「历史问答助手」才会不可用）；
不装飞书机器人，其余功能完全不受影响。

---

## 三、服务器规格与外网要求

| 项 | 要求 |
| --- | --- |
| CPU / 内存 | 2 核 4 GB 起。若装 Ollama 跑 7B 模型，内存加到 **8 GB+**（或配 GPU） |
| 磁盘 | 40 GB。其中 RAG 数据制品约 400 MB、Neo4j 数据与日志、构建时 node_modules 约 1 GB |
| 外网 | **必需**：RAG 要访问云端大模型与你自己的向量服务（`api.commandcode.ai` / `dashscope.aliyuncs.com`）。纯内网服务器需换成本地模型，属另一套改造 |
| 安全组 / 防火墙 | 放行 **80**（上 HTTPS 再放行 443）。**5000 / 8000 / 7687 不要对公网开放** |

---

## 四、部署步骤

以下命令里的 `你的服务器` 换成 IP 或域名。默认安装到 `/opt/china-war`，运行用户 `chinawar`。

### 第 1 步：把代码与数据传到服务器（在你自己的电脑上执行）

```bash
# 在项目根目录（就是本目录的上一级）
bash deploy/scripts/04_upload_from_local.sh root@你的服务器
```

这一步会做两件事：

1. 同步代码（排除 `node_modules`、`.git`、日志等，带上本机已构建好的 `dist`）
2. 同步 **不在 git 仓库里的数据制品**，这些必须单独传：
   - `backend/database` —— SQLite 主库（约 13 MB）
   - `backend/data/` —— 原始文本等（`raw/` 受版权约束，按需）
   - `RAG/data/` —— 检索快照与索引（约 400 MB，RAG 问答的数据来源）

> 只想传代码：加 `--code-only`；只补数据：加 `--data-only`。

### 第 2 步：初始化服务器环境

```bash
ssh root@你的服务器
cd /opt/china-war
sudo bash deploy/scripts/01_setup_server.sh
```

脚本做的事：装系统基础包 → 装 Node 20 → 建 `chinawar` 用户 → 装 Miniconda →
建两个 Python 环境（`china-war-backend` = 3.8，`china-war-rag` = 3.11）→ 装三份依赖。

- 幂等，可重复执行。
- 国内服务器下载慢，可加镜像：`sudo PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy/scripts/01_setup_server.sh`

### 第 3 步：安装 Neo4j（图数据库）

```bash
sudo bash deploy/scripts/02_install_neo4j.sh
```

脚本会装 Neo4j 5、设置初始口令并启动。**把打印出来的口令记下来**，下一步要用。

### 第 4 步：重建图数据

Neo4j 的图数据**不需要从本机迁移**——SQLite 才是主存储，用同步脚本全量重建：

```bash
cd /opt/china-war/backend
sudo -u chinawar /opt/miniconda3/envs/china-war-backend/bin/python sync_sqlite_to_neo4j.py --mode full
```

> **必须显式写 `--mode full`**：脚本的 `--mode` 默认值是 `increment`（增量），
> 首次重建图数据时用默认值不会得到全量结果。

> 什么时候要重跑：以后在管理台改了图谱数据，SQLite 会自动写，但 Neo4j 不会自动跟——重跑这条命令即可。

### 第 5 步：写三个配置文件

**旧后端**（Neo4j 口令 + JWT 密钥 + 服务间密钥）：

```bash
cd /opt/china-war
cp deploy/env/backend.env backend/.env
openssl rand -base64 48          # 生成 JWT_SECRET，复制输出
openssl rand -hex 32             # 生成 INTERNAL_SERVICE_KEY，复制输出
nano backend/.env                # 填 NEO4J_PASSWORD（第 3 步的口令）、JWT_SECRET、
                                 # INTERNAL_SERVICE_KEY（后者要与 RAG/.env 的
                                 # RAG_INTERNAL_SERVICE_KEY 同值）
```

`INTERNAL_SERVICE_KEY` 是**服务间密钥**（第 13 轮复核新增）：RAG 用它调用旧后端的
`/api/internal/token/introspect`，确认"这张凭证现在还作不作数"。不配的后果见下面 RAG 的第 4 项。

**RAG 服务**（模型端点、检索、限流、CORS）：

```bash
cp deploy/env/rag.env RAG/.env
nano RAG/.env                    # 至少改 CORS_ALLOW_ORIGINS 为实际访问地址
```

RAG 有三个**必改项**，不改虽然能启动但行为是错的（`deploy/env/rag.env` 里已用 ★ 标出）：

1. `RATE_LIMIT_TRUST_FORWARDED_FOR=true` + `RATE_LIMIT_TRUSTED_PROXIES=127.0.0.1`
   —— 不加的话，所有用户对 RAG 来说都是同一个 IP，`RATE_LIMIT_PER_MINUTE=30` 会变成**全站合计 30 次/分钟**。
2. `CORS_ALLOW_ORIGINS` 不能留 `*`（显式生产档下服务会拒绝启动）。只用 IP 就填 `http://你的公网IP`，
   有域名填 `https://你的域名`。
3. `RAG_AUTH_MODE` 必须显式选一个（第 13 轮整改）：
   - `RAG_AUTH_MODE=jwt`：本服务验签，**同时**要配 `RAG_JWT_SECRET`（与 backend 的 `JWT_SECRET` 同值）；
   - `RAG_AUTH_MODE=nginx`：由 nginx 的 `auth_basic` 或前置网关把关，服务只监听回环；
   - `RAG_AUTH_MODE=disabled`：不校验身份，**显式生产档下会拒绝启动**。

   ★ 这一项以前只打一条 WARNING，而 WARNING 会被忽略——"nginx 在把关"和"根本没人在把关"
   在配置里长得一模一样。现在必须显式二选一：留空或写 `disabled` 时，只要同时设了
   `RAG_REQUIRE_ACTIVE_VERSION=true`，服务启动即失败。选 `nginx` 档时若监听的不是回环地址，
   `run_server.py` 也会拒绝启动（那种情况下同网段可以绕过 nginx 直连后端）。
   旧开关 `RAG_REQUIRE_AUTH` 仍被接受（`true` 等价于 `jwt`），只用于兼容改造前的配置。

   ★ **生产默认是 `jwt`**（模板里已经是这个值）。原模板写的是 `nginx`，而 nginx 侧的
   `auth_basic` 是注释状态——照着两份模板部署会得到"RAG 以为 nginx 在鉴权、nginx 其实没配"
   的组合：两边都能正常启动、日志里没有任何异常，唯一后果是公网 RAG 没有访问控制。
   现在这个组合在**安装阶段**就会被拦住（见第 6 步的鉴权门禁），不再依赖你记得改注释。

4. `RAG_INTROSPECT_URL` + `RAG_INTERNAL_SERVICE_KEY`（第 13 轮复核新增，**强烈建议配上**）：

   不配的后果很具体：**账号被停用或改密码后，旧 token 在自然过期前（默认 7 天）仍能调用
   RAG 问答接口**——旧后端本身会立刻拒绝该凭证，于是安全动作只在一半系统上生效。
   配上之后 RAG 会按 `RAG_INTROSPECT_TTL_SECONDS`（默认 30 秒）向后端确认一次凭证状态，
   **该 TTL 就是撤销生效延迟的上界**。密钥与 backend 的 `INTERNAL_SERVICE_KEY` 必须同值。

   ★ **生产档下这一项必须显式选择**（第 13 轮复核整改 §2.7）：要么按上面配齐（或加一行
   `RAG_REQUIRE_REVOCATION_CHECK=true` 表示"这个部署必须有"），要么设
   `RAG_ALLOW_DELAYED_REVOCATION=true` 明确接受延迟。两个都不做，服务会**拒绝启动**，
   安装门禁也会在同一口径上拦一次（`check_rag_auth.sh`）。

**RAG 的密钥单独放**，不写进项目目录（遵循 `RAG/.env.example` 的约定）：

```bash
sudo mkdir -p /etc/china-war
sudo cp deploy/env/rag-secrets.env /etc/china-war/rag-secrets.env
sudo chmod 600 /etc/china-war/rag-secrets.env
sudo nano /etc/china-war/rag-secrets.env
# LLM_API_KEY=sk-xxx          大模型（也可以叫 DEEPSEEK_API_KEY）
# DASHSCOPE_API_KEY=sk-xxx    阿里云百炼，文本向量化用
```

> 这个文件第 7 步也会自动补一份（不存在时才创建），所以先做后做都行。

> **暂时没有密钥也能跑**：RAG 会自动降级——检索退化为关键词、回答由离线摘要器产出（`finish_reason=degraded`），
> 页面和链路都能验收。补齐密钥后 `sudo systemctl restart china-war-rag` 即可。

**飞书机器人**（只有要用时才配）：

```bash
cp feishu-bot/.env.example feishu-bot/.env    # 填 FEISHU_APP_ID / FEISHU_APP_SECRET
```

飞书开放平台侧的配置（开启机器人能力、事件订阅选「长连接」、订阅 `im.message.receive_v1`
与 `card.action.trigger`）照着 [feishu-bot/README.md](../feishu-bot/README.md) 的「第 2 层」做一遍即可，
不需要公网回调地址。

### 第 6 步：构建前端

服务器上如果只部署、不改前端，可以跳过（第 1 步已经把本机构建好的 `dist` 传上来了）。

改了前端代码，或想让服务器自己构建：

```bash
sudo bash deploy/scripts/03_build_frontend.sh
```

脚本会构建**两个**产物，模式不能弄反：

- `frontend/` → `npm run build`（base = `/static/`，由 nginx 提供）
- `RAG/frontend/` → `npm run build:integration`（base = `/rag/`，由 RAG 服务同源托管）

脚本最后会校验 RAG 产物的前缀确实是 `/rag/assets/`，用错模式会直接报错阻止。

### 第 7 步：装服务并启动

```bash
sudo bash deploy/scripts/install_services.sh
```

这一步会：装 nginx → 写 nginx 站点 → 登记三个 systemd 服务（`china-war-backend`、`china-war-rag`、
`china-war-bot`）→ **跑一次鉴权门禁** → 启动并设为开机自启。

**鉴权门禁**（第 13 轮复核新增，`deploy/scripts/check_rag_auth.sh`）在启动服务**之前**执行，
任一失败即终止安装。它校验的是"两份配置各看起来都对、合起来却漏了一半"这类组合：

| 检查 | 挡住的失败模式 |
| --- | --- |
| 鉴权模式取值合法；生产档下不能是 `disabled` | 拼错取值静默回落、生产漏配 |
| `jwt` 档：RAG 与 backend 两侧 JWT 密钥**同值** | 这套校验最容易踩的坑——表现为问答全部 401 |
| `jwt` 档：撤销查询要么两侧配齐，要么明确告警 | "停用账号后在 token 到期前仍可用"这条边界被静默继承 |
| `nginx` 档：`.htpasswd` 存在、`auth_basic` 处于**启用**状态、`nginx -T` 生效配置里 `/rag/` 带认证 | 复核发现的原始缺陷：模板说 nginx 把关，nginx 里那两行其实是注释 |
| 密钥不是模板占位符、长度达标（JWT 与服务间密钥 ≥ 32、Neo4j 口令 ≥ 12），Neo4j 口令不是出厂默认值 | 把同一个 `CHANGE_ME_...` 复制到两侧能通过"非空 + 同值"检查；占位符判定与 `RAG/scripts/check_secrets.py` 同口径 |
| 生产档下撤销策略已显式选择（配齐查询，或 `RAG_ALLOW_DELAYED_REVOCATION=true`） | "两个值都不填"等于静默接受"停用账号后 7 天内仍可用" |
| 两种档位：RAG 只监听回环；nginx 屏蔽 `/api/internal/` | 直连后端端口绕过鉴权、内部接口暴露到公网 |

单独跑（改完配置后复核）：

```bash
sudo bash deploy/scripts/check_rag_auth.sh
```

**另有一个 systemd 定时器**（第 13 轮整改新增，补偿队列的自动重放）：

```bash
sudo cp deploy/systemd/china-war-outbox-retry.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now china-war-outbox-retry.timer
```

它每分钟跑一次 `backend/retry_sync.py --due-only`，把 Neo4j 写入失败留下的待办按退避
（30 秒 → 2 分钟 → 10 分钟 → 1 小时）自动补齐；超过 5 次转 `abandoned` 等人工处理。
观察方式：

```bash
systemctl list-timers china-war-outbox-retry.timer
cd /opt/china-war/backend && python retry_sync.py --list      # 还差多少、卡在哪
```

不用这个定时器也不会丢数据——待办落在 `neo4j_sync_jobs` 表里，只是要人工跑一次重放。

绑域名时：

```bash
sudo SERVER_NAME=your.domain bash deploy/scripts/install_services.sh
```

nginx 站点配置也可以直接用现成的 [nginx/china-war.conf](nginx/china-war.conf)（里面写清了每条 location 的用途）。

### 第 8 步：自检

```bash
bash deploy/scripts/selfcheck.sh
PUBLIC_HOST=你的公网IP bash deploy/scripts/selfcheck.sh    # 也验证经 nginx 的对外入口
```

它会按用户的真实访问路径逐项检查：三个服务是否 active、后端接口、RAG health（含向量/大模型可用性、
数据版本是否固定）、首页与 `/rag/` 的静态资源前缀、`/rag/api/health` 经反代是否可达、Neo4j 连接。
失败项会给出对应的排查方向。

**另外建议看一眼两边的 health**（限制策略/撤销查询到底开没开，只有运行中的服务知道）：

```bash
curl -s http://127.0.0.1:8000/api/health \
  | python3 -c "import json,sys; a=json.load(sys.stdin)['auth']; print(a['mode'], a['revocation'])"
```

`auth.revocation.policy` 是三态：`enforced`（已启用查询，`max_delay_seconds` 即缓存 TTL）、
`delayed`（未启用：停用/改密码后旧 token 在自然过期前仍可用）、`not-applicable`（非 jwt 档）。
`last_ok_at` / `last_failure_at` / `last_failure_reason` 反映 backend 是否可达——
backend 重启时最想知道的就是"它恢复了没有"。

旧后端的运行状态（免登录）：

```bash
curl -s http://127.0.0.1:5000/api/health
# {"code":200,"data":{"status":"ok","login_guard":{"failure_mode":"closed"}}}
```

`failure_mode=closed` 表示"限流表不可用时拒绝登录（503）"；`open` 表示放行。

### 第 9 步：放行端口并访问

- 云服务器**安全组**放行 TCP 80（上 HTTPS 再放行 443）；
- 系统防火墙：`sudo ufw allow 80/tcp`

然后在浏览器打开：

| 入口 | 地址 |
| --- | --- |
| 管理台首页 | `http://你的域名或IP/` |
| RAG 智能问答 | 菜单「知识图谱 → RAG 智能问答」，或直接 `http://你的域名或IP/#/knowledge/rag` |

---

## 五、日常运维

```bash
# 看日志（三个服务都输出到 journald）
journalctl -u china-war-rag -f
journalctl -u china-war-backend -f
journalctl -u china-war-bot -f

# 重启
sudo systemctl restart china-war-rag
sudo systemctl restart china-war-backend china-war-rag china-war-bot

# 更新代码后（本机上执行上传，然后在服务器上）
sudo chown -R chinawar:chinawar /opt/china-war     # 上传的文件属主会变成 root
sudo systemctl restart china-war-backend china-war-rag
```

### 账号与角色（提权）

注册接口一律建**只读**（`viewer`）。需要写数据或数据运营入口，把角色改成 `editor`；
需要用户管理入口，改成 `admin`。三种方式：

0. **全新库还没有任何管理员时**（首次部署的必由之路）用引导命令——注册接口只建 viewer，
   而「用户管理」页只有 admin 能进，没有管理员就永远进不去：

   ```bash
   cd /opt/china-war/backend
   python create_admin.py --list                # 看清现有账号与角色
   python create_admin.py --account alice       # 把已注册的账号提升为 admin
   ```

   只在库中确实没有管理员时才动数据（已有管理员时命令会拒绝并提示走界面）；
   口令交互输入、不进 shell 历史。**首次引导不要再手写 `UPDATE ... SET role='admin'`**：
   手写 SQL 没有"仅首次可用"这层护栏，任何时刻都能提权且不留痕。

1. **界面上改（推荐）**：用 `admin` 账号登录 → 左侧「用户管理」→ 改角色 → 保存。
   只有 `admin` 能看到这个入口，接口也由服务端判权限（`require_admin`）；
2. **改库兜底**（界面不可用时）：

```bash
# 在服务器上（SQLite 库就在后端目录里）
cd /opt/china-war/backend
sqlite3 database "UPDATE UserInfo SET role = 'editor' WHERE account = 'someone';"
sqlite3 database "SELECT id, account, name, role FROM UserInfo ORDER BY id;"    # 确认
```

**改完必须让本人重新登录**：写接口的权限是每次请求实时查库（立即生效），但**菜单是登录时
下发的**——不重新登录，对方界面上不会出现新入口。角色职责与分级规则（admin ⊃ editor ⊃
viewer）见 [backend/README.md 的「角色职责与三处口径」表](../backend/README.md)。

### 数据更新后必须做的两步

RAG 读的是**离线制品**，不是实时读旧库。在管理台改了图谱数据后，RAG 不会自动感知：

```bash
cd /opt/china-war/RAG
sudo -u chinawar /opt/miniconda3/envs/china-war-rag/bin/python scripts/export_snapshot.py   # 从 SQLite 导出快照
sudo -u chinawar /opt/miniconda3/envs/china-war-rag/bin/python scripts/build_index.py        # 重建文本与向量索引
sudo systemctl restart china-war-rag
```

两条提醒：

- **重切文本需要原始战争史文本**（`entity-event-relation/data/*.txt`）。这些文件受版权约束，既不在
  git 仓库里，第 1 步的上传脚本也不会带上它们；只有确实要重新切分时才手动传上去。
- 只想补/重建**向量**、不重新切分时，用 `scripts/build_index.py --vectors-only`：它复用服务器上已有的
  `chunks.jsonl`，不需要原始文本。

换数据版本时，**两处要同步改**：`/etc/systemd/system/china-war-rag.service` 里的 `--version`，
以及 `RAG/.env` 里的 `RAG_ACTIVE_VERSION`（然后 `systemctl daemon-reload && systemctl restart china-war-rag`）。

---

## 六、安全边界（务必读完再决定要不要开放公网）

1. **RAG 的鉴权必须显式配置**（第 13 轮复核后已改为默认 `jwt`）：服务端验签旧后端签发的 JWT，
   两条问答通道一视同仁；未开启时 `/api/health` 会持续告警。旧后台的登录门禁**管不到 RAG**——
   `/rag/*` 被 nginx 直接转给了 8000，不经过 Flask。
2. **不要依赖"两份配置各看起来都对"**。原先的模板写 `RAG_AUTH_MODE=nginx`，而 nginx 的
   `auth_basic` 是注释状态——这种组合两边都能正常启动、日志里没有异常，唯一后果是公网 RAG
   没有访问控制。现在：模板默认 `jwt`；若确实要用 nginx 档，`deploy/scripts/check_rag_auth.sh`
   会在安装阶段校验 `.htpasswd` 存在、`auth_basic` 确实启用、`nginx -T` 生效配置里 `/rag/`
   带认证，缺一即拒绝安装。
3. **停用账号/改密码后，RAG 是否立刻拒绝取决于撤销查询有没有配**：配齐
   `RAG_INTROSPECT_URL` + `RAG_INTERNAL_SERVICE_KEY` 后最迟 `RAG_INTROSPECT_TTL_SECONDS`
   内失效；不配则要到 token 自然过期（默认 7 天）。health 里会报当前属于哪一种。
4. 用 certbot 上 HTTPS 后，`RAG/.env` 的 `CORS_ALLOW_ORIGINS` 要同步改成 `https://你的域名`。
5. **别把 5000 / 8000 / 7687 开到公网**。systemd 单元已把两个后端限定在 `127.0.0.1`，Neo4j 默认也只监听本机。
   服务间内部接口（`/api/internal/`）只走回环，nginx 对公网直接返回 404。
6. `.env` 与 `/etc/china-war/rag-secrets.env` 里是口令和密钥，权限保持 `600`。
7. 本仓库是公开仓库——真实口令、密钥一律只写在服务器上，不要提交进 git。
8. **首次部署前轮换历史凭据（必做）**：git 历史（初始提交 `57eea5b`）里曾明文提交过 Neo4j 口令与 JWT 密钥。
   当前 HEAD 已改为从 `backend/.env` 读取，但**历史里的旧值仍可取回**，视同已泄露：
   - Neo4j 口令：在 Neo4j 上执行 `ALTER USER neo4j SET PASSWORD '<新口令>'`，同步更新 `backend/.env` 的 `NEO4J_PASSWORD`；
   - JWT 密钥：换一个全新的 `JWT_SECRET`（`openssl rand -base64 48`）。换掉后所有旧 token 立即失效，用户需重新登录；
   - 服务间密钥：换 `INTERNAL_SERVICE_KEY` / `RAG_INTERNAL_SERVICE_KEY`（两侧同值，`openssl rand -hex 32`）。

   两步都做完，泄露的旧值才真正作废。彻底方案是用 `git filter-repo` 重写历史并强制推送，
   代价是全部提交哈希都会改变，是否值得另行决定。

---

## 七、常见故障

| 现象 | 原因与处理 |
| --- | --- |
| 服务起不来 | `journalctl -u china-war-rag -n 50` 看真实报错。最常见：`.env` 缺失、数据版本目录不存在 |
| 启动即报 CORS 被拒绝 | 显式生产档下 `CORS_ALLOW_ORIGINS` 仍是 `*`：改成实际地址，或显式 `ALLOW_PUBLIC_CORS=true` |
| 启动即报快照/索引版本不一致 | `RAG/data/snapshot/<v>` 与 `RAG/data/index/<v>` 必须同名；`--version` 与 `.env` 要一致 |
| 页面白屏、Network 一堆 404 | 前端构建模式错了：旧前端要 `npm run build`，RAG 前端要 `npm run build:integration`，重跑第 6 步 |
| 回答“憋住”，最后一次性全出来 | nginx 少了 `proxy_buffering off`（配置已带，别删） |
| 多人同时用就开始 429 | `RAG/.env` 的 `RATE_LIMIT_TRUST_FORWARDED_FOR` 没开（见第 5 步必改项） |
| 图谱页空白 / 后端报 Neo4j 连接失败 | Neo4j 没起或口令不对：`systemctl status neo4j`、核对 `backend/.env` 的 `NEO4J_PASSWORD` |
| RAG health 里 `vector_available=false` | `RAG/data/index/<v>/vectors/chroma/` 缺失，或没配 `DASHSCOPE_API_KEY`；会自动降级关键词检索 |
| `llm_available=false` | 没配大模型密钥，或服务器出不了外网 |
| 上传后服务报权限错误 | 文件属主是 root：`sudo chown -R chinawar:chinawar /opt/china-war` |
| 旧版问答没反应 | 它依赖 Ollama 常驻：`ollama serve` + `ollama pull deepseek-r1:7b`（8 GB 内存起步）。RAG 问答不受影响 |

---

## 八、可选：HTTPS、旧版问答、备份

**HTTPS**（有域名）：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain
# 然后改 RAG/.env 的 CORS_ALLOW_ORIGINS 为 https://your.domain，并重启 RAG
```

**旧版问答（Ollama）**：

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull deepseek-r1:7b       # 约 4 GB，拉完常驻内存约 8 GB
```

**数据备份**：主存储是 SQLite，备份它就够了（Neo4j 可随时重建）：

```bash
sqlite3 /opt/china-war/backend/database ".backup '/var/backups/china-war-$(date +%F).db'"
```

配一条 crontab 每天跑即可。RAG 的 `data/snapshot`、`data/index` 是派生制品，丢了按第五节的命令重建。

---

## 附：本目录文件清单

```
deploy/
├── README.md                       # 本文
├── nginx/china-war.conf            # nginx 站点（三个入口分流 + SSE 不缓冲）
├── systemd/
│   ├── china-war-backend.service   # 旧后端 :5000
│   ├── china-war-rag.service       # RAG :8000（含 --version 数据版本固定）
│   ├── china-war-bot.service       # 飞书机器人（可选）
│   ├── china-war-outbox-retry.service  # 补偿队列自动重放（oneshot，由 timer 触发）
│   └── china-war-outbox-retry.timer    # 每分钟触发一次上面的 service
├── env/
│   ├── backend.env                 # 旧后端生产模板 → backend/.env
│   ├── rag.env                     # RAG 生产模板 → RAG/.env
│   └── rag-secrets.env             # 密钥模板 → /etc/china-war/rag-secrets.env
└── scripts/
    ├── 01_setup_server.sh          # 系统包 + Node + Miniconda + 两个 Python 环境 + 依赖
    ├── 02_install_neo4j.sh         # Neo4j 5 安装与初始口令
    ├── 03_build_frontend.sh        # 两个前端产物（含模式校验）
    ├── 04_upload_from_local.sh     # 【本机执行】上传代码与数据制品
    ├── install_services.sh         # 写 systemd 单元与 nginx 站点、跑鉴权门禁、启动
    ├── check_rag_auth.sh           # 鉴权门禁：模式/两侧密钥同值/nginx 认证真实生效/回环监听
    └── selfcheck.sh                # 按用户访问路径逐项自检
```
