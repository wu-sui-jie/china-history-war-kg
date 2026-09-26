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
| 旧后端（Flask） | `backend/` | Python 3.11 | 5000（仅本机） | 图谱增删改查、数据运营、旧版问答 | 必需 |
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

**两个环境仍然分开，但主版本统一为 3.11**：RAG 的 chromadb 要求 Python ≥ 3.10；旧后端（Flask + py2neo）也已在 3.11 上验证通过（四套测试与线上接口全过，2026-09-26 真机实测），于是全仓统一到 **Python 3.11**——服务器上仍是两个独立环境（依赖集不同：一个是 Flask + py2neo，一个是 FastAPI + chromadb），分环境是**隔离选择**，Python 版本不再是理由。

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
建两个 Python 环境（`china-war-backend`、`china-war-rag`，**都是 3.11**）→ 装依赖
（含仓库内的抽取链包 `war_extraction`：按 `backend/requirements.txt` 写明的顺序做
`pip install -e entity-event-relation` 可编辑安装；漏装它旧后端会以 `ModuleNotFoundError` 崩溃）。

- 幂等，可重复执行。
- 国内服务器下载慢，可加镜像：`sudo PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy/scripts/01_setup_server.sh`
- **不要**加 `USE_UNIFIED_LOCK=1`：仓库根的 `requirements.lock` 在 Linux 上装不上（见第九节 9.3），默认的 `requirements.txt` 才是这台机器上验证过的路径。
- 已有旧环境要迁到 3.11？用 `06_unify_py311.sh`（把 3.8 环境改名保留为回滚点，再建 3.11 环境，systemd 单元无需改动）。

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

`INTERNAL_SERVICE_KEY` 是**服务间密钥**：RAG 用它调用旧后端的
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
3. `RAG_AUTH_MODE` 必须显式选一个：
   - `RAG_AUTH_MODE=jwt`：本服务验签，**同时**要配 `RAG_JWT_SECRET`（与 backend 的 `JWT_SECRET` 同值）；
   - `RAG_AUTH_MODE=nginx`：由 nginx 的 `auth_basic` 或前置网关把关，服务只监听回环；
   - `RAG_AUTH_MODE=disabled`：不校验身份，**显式生产档下会拒绝启动**。

   ★ 这一项必须**显式二选一**，不能靠"看起来配了"：留空或写 `disabled` 时，只要同时设了
   `RAG_REQUIRE_ACTIVE_VERSION=true`，服务启动即失败。选 `nginx` 档时若监听的不是回环地址，
   `run_server.py` 也会拒绝启动（那种情况下同网段可以绕过 nginx 直连后端）。
   要求显式的原因："nginx 在把关"和"根本没人在把关"在配置里长得一模一样，只打一条 WARNING
   会被忽略。旧开关 `RAG_REQUIRE_AUTH` 仍被接受（`true` 等价于 `jwt`），只用于兼容按旧名字写的配置。

   ★ **生产默认是 `jwt`**（模板里已经是这个值）。若档位选了 `nginx` 而 nginx 侧的
   `auth_basic` 还处于注释状态，就会得到"RAG 以为 nginx 在鉴权、nginx 其实没配"的组合：
   两边都能正常启动、日志里没有任何异常，唯一后果是公网 RAG 没有访问控制。
   这个组合在**安装阶段**就会被拦住（见第 6 步的鉴权门禁），不依赖你记得去改 nginx 注释。

4. `RAG_INTROSPECT_URL` + `RAG_INTERNAL_SERVICE_KEY`（**强烈建议配上**）：

   不配的后果很具体：**账号被停用或改密码后，旧 token 在自然过期前（默认 7 天）仍能调用
   RAG 问答接口**——旧后端本身会立刻拒绝该凭证，于是安全动作只在一半系统上生效。
   配上之后 RAG 会按 `RAG_INTROSPECT_TTL_SECONDS`（默认 30 秒）向后端确认一次凭证状态，
   **该 TTL 就是撤销生效延迟的上界**。密钥与 backend 的 `INTERNAL_SERVICE_KEY` 必须同值。

   ★ **生产档下这一项必须显式选择**：要么按上面配齐（或加一行
   `RAG_REQUIRE_REVOCATION_CHECK=true` 表示"这个部署必须有"），要么设
   `RAG_ALLOW_DELAYED_REVOCATION=true` 明确接受延迟。两个都不做，服务会**拒绝启动**，
   安装门禁也会在同一口径上拦一次（`check_rag_auth.sh`）。

5. `RAG_BOT_API_KEY`（只在**要接飞书机器人**时必填）：

   机器人没有用户身份，只会发 `X-Bot-Key`，而 jwt 档推出 `require_auth=True`——
   两边都不配的结果是**机器人每问必被 401**，机器人侧却只显示"RAG 不可用"，
   排障方向完全是反的。取值为随机串（`openssl rand -hex 32`），
   与 `feishu-bot/.env` 的 `RAG_BOT_API_KEY` **同值**。

   ★ 安装门禁按"仓库里有没有 `feishu-bot/.env`"判断要不要部署机器人：要部署却没配 → 拒绝安装；
   不部署只提醒。运行期 `/api/health` 的 `warnings` 也会给出同一条提示。

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

**鉴权门禁**（`deploy/scripts/check_rag_auth.sh`）在启动服务**之前**执行，
任一失败即终止安装。它校验的是"两份配置各看起来都对、合起来却漏了一半"这类组合：

| 检查 | 挡住的失败模式 |
| --- | --- |
| 鉴权模式取值合法；生产档下不能是 `disabled` | 拼错取值静默回落、生产漏配 |
| `jwt` 档：RAG 与 backend 两侧 JWT 密钥**同值** | 这套校验最容易踩的坑——表现为问答全部 401 |
| `jwt` 档：撤销查询要么两侧配齐，要么明确告警 | "停用账号后在 token 到期前仍可用"这条边界被静默继承 |
| `nginx` 档：`.htpasswd` 存在、`auth_basic` 处于**启用**状态、`nginx -T` 生效配置里 `/rag/` 带认证 | 档位说 nginx 把关、nginx 里那两行其实是注释：两边都能正常启动，公网 RAG 没有访问控制 |
| 密钥不是模板占位符、长度达标（JWT 与服务间密钥 ≥ 32、Neo4j 口令 ≥ 12），Neo4j 口令不是出厂默认值 | 把同一个 `CHANGE_ME_...` 复制到两侧能通过"非空 + 同值"检查；占位符判定与 `RAG/scripts/check_secrets.py` 同口径 |
| 生产档下撤销策略已显式选择（配齐查询，或 `RAG_ALLOW_DELAYED_REVOCATION=true`） | "两个值都不填"等于静默接受"停用账号后 7 天内仍可用" |
| 两种档位：RAG 只监听回环；nginx 屏蔽 `/api/internal/` | 直连后端端口绕过鉴权、内部接口暴露到公网 |

单独跑（改完配置后复核）：

```bash
sudo bash deploy/scripts/check_rag_auth.sh
```

**另有一个 systemd 定时器**（补偿队列的自动重放）：

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

1. **RAG 的鉴权默认是 `jwt` 档**：服务端验签旧后端签发的 JWT，
   两条问答通道一视同仁；未开启时 `/api/health` 会持续告警。旧后台的登录门禁**管不到 RAG**——
   `/rag/*` 被 nginx 直接转给了 8000，不经过 Flask。
2. **不要依赖"两份配置各看起来都对"**。档位写 `RAG_AUTH_MODE=nginx`、而 nginx 的
   `auth_basic` 是注释状态，这种组合两边都能正常启动、日志里没有异常，唯一后果是公网 RAG
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
8. **首次部署前轮换历史凭据（必做）**：git 历史（初始提交 `57eea5b`）里明文提交过 Neo4j 口令与 JWT 密钥，
   这些旧值**仍可从历史中取回**，视同已泄露（当前代码已从 `backend/.env` 读取，不影响这个结论）：
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

**数据备份**：主存储是 SQLite（`backend/database`）——账号、人工维护的节点与关系、Neo4j 写失败的补偿队列都在里面；**Neo4j 只是它的派生副本**（丢了能从主库重建，反过来不行）。仓库里有现成的脚本与定时器，别自己拼 crontab：

```bash
sudo bash /opt/china-war/deploy/scripts/05_backup.sh      # 立刻备份一次（全量）
sudo systemctl start china-war-backup.service             # 与定时任务完全同一条路径
systemctl list-timers china-war-backup.timer              # 每天 03:20，Persistent=true
ls -la /var/backups/china-war/                            # 备份落在这里
```

`install_services.sh` 会自动装好 `china-war-backup.timer` 并立刻跑一次。备份内容是 SQLite 在线快照（用 sqlite3 的 backup API，**不是 `cp`**——该库是 WAL 模式，`cp` 会漏掉 `-wal` 里未合并的事务，得到一份"看起来正常、少了最近若干次写入"的旧库）+ 三份 `.env` + `/etc/china-war/rag-secrets.env` + systemd 单元 + nginx 站点 + 两个环境的包清单。保留最近 7 天与最近 4 个周日。

**恢复步骤写在 `deploy/scripts/05_backup.sh` 头部**（该停哪些服务、覆盖哪个文件、怎么对账）。请真的演练一次：没演练过的备份只能算"文件还在"。

RAG 的 `data/snapshot`、`data/index` 是派生制品，丢了按第五节重建；`selfcheck.sh` 会检查备份定时器在跑、**且真的产出过备份**（只装定时器不算数）。

---

## 九、真机部署实测记录（2026-09-26）

> **服务器租期**：这一节记录的第一台服务器（公网 IP `47.117.100.163`）是**临时租用**的，
> **2026-10-26 到期**；到期后该 IP 上的三个服务、nginx 入口与演示环境都会不可用。
> 续费或迁到新机器后，按本文第四节重做部署与自检，并更新
> `deploy/scripts/verify_deploy_auth.py` 的默认 IP（可用 `PUBLIC_HOST` 覆盖）。

这一节记的是**只在真机上才会现形**的坑。它们的共同点是：本机看不出来、CI 也全绿，而后果都是"部署看起来成功了，其实没生效"。

### 9.1 换行符：CRLF 让服务器上的脚本一行都跑不了

服务器上的现象：

```text
/opt/china-war/deploy/scripts/check_rag_auth.sh: line 40: $'\r': command not found
/opt/china-war/deploy/scripts/check_rag_auth.sh: syntax error near unexpected token `$'{\r''
```

实测 229 个文本文件带 CR，包括 `deploy/scripts/*.sh`、`deploy/nginx/china-war.conf` 与 `/etc/nginx/sites-available/china-war.conf`。**最要紧的是鉴权门禁脚本**：它跑不起来，而 `install_services.sh` 靠它的结论决定"要不要放行启动"——于是"该拦的没拦"，而两侧都没有任何提示。

根因在开发机：`core.autocrlf=true` 让检出到工作区的是 CRLF（git 索引里一直是 LF），打包上传把 CR 原样带过去了。修法两层：

1. 仓库根 `.gitattributes`（`* text=auto eol=lf`）——属性优先级高于 `core.autocrlf`，任何平台检出都是 LF；
2. `04_upload_from_local.sh` 上传前兜底：对在 Linux 上必须为 LF 的文件（`*.sh` / `*.service` / `*.timer` / `*.conf` / env 模板）检测到 CR 就地转换并打印。

排查提示：`grep -rl $'\r' --include='*.sh' .` 一眼看出哪些文件中招。

### 9.2 `RAG/data/` 里住着源码，而上传把它当"数据目录"排除了

服务器上的现象：

```text
File "/opt/china-war/RAG/server/runtime.py", line 25, in <module>
    from data.index.chroma_store import ChromaClients
ImportError: cannot import name 'ChromaClients' from 'data.index.chroma_store'
```

报错长得像"代码写错了"，实际是"服务器上是半新半旧的代码"：`RAG/data/index/*.py`（`chroma_store.py`、`chunking.py`…）是**被 git 跟踪的源码**，由 RAG 以 `data.index.*` 导入；而上传脚本把 `RAG/data` 整个排除了（那个目录同时住着 400 MB 的向量索引与快照）。于是新版 `runtime.py` 配旧版 `chroma_store.py`，RAG 反复重启。

修法：上传脚本新增一步——把 `git ls-files RAG/data backend/data` 列出的文件单独同步一遍。大制品都没有被跟踪，所以这个列表给出的恰好是"小而必需"的那部分。**改 RAG 的检索/索引代码后，确认这一步跑过**（脚本会打印同步了多少个文件）。

### 9.3 统一 lock（`requirements.lock`）在 Linux 上装不上

```text
ERROR: In --require-hashes mode, all requirements must have their versions pinned with ==.
These do not: uvloop>=0.15.1 (from uvicorn[standard]>=0.18.3 -> chromadb==1.5.9 -> requirements.lock)
```

这份锁是在 Windows 开发机上生成的：`uvicorn[standard]` 经 `--strip-extras` 剥掉了 extras，而 Linux 专有的 **uvloop** 在 Windows 的 `pip freeze` 里根本不存在。本机验证"锁可重装"时看不出来——恰好因为 Windows 不需要 uvloop。

现状：`01_setup_server.sh` **默认走 `requirements.txt`**（与既有部署一致），要用锁得显式 `USE_UNIFIED_LOCK=1`。待办是把生成器改成平台完整（或让 CI 在 Linux 上真装一遍）；在那之前不要把它当"验证过的组合"用在服务器上。

### 9.4 `install_services.sh` 的单元名缺 `.service`（已修）

那个 `for unit in …` 循环里前三个名字没有 `.service` 后缀，脚本用它拼 `deploy/systemd/${unit}` 与 `/etc/systemd/system/${unit}` 两个路径，于是在 2/5 步直接 `die "缺少 …/systemd/china-war-backend"`——照本文从上到下执行的人必然卡在这里。`bash -n` 只查语法，名字写错一个字都不会说。现在 `scripts/check_deploy_config.py` 会在 CI 里逐个断言这些名字对应真实文件。

### 9.5 飞书机器人：两侧共享密钥必须同值

jwt 档下 RAG 要求 `X-Bot-Key`；`RAG/.env` 的 `RAG_BOT_API_KEY` 若没配、或与 `feishu-bot/.env` 的值不同，机器人每问必 401，而机器人侧只会说"RAG 不可用"——**排障方向是反的**。`check_rag_auth.sh` 会拦住这个组合（这正是它存在的意义）。本次部署发现飞书侧键存在但值为空，已补齐同值。

### 9.6 切换后的实测结论（Python 3.11）

- 两个环境都是 Python 3.11.16（`china-war-backend`、`china-war-rag`）。旧 3.8 环境已按计划删除，删除前记录了 `conda list --explicit` 与 `conda env export` 到备份目录，可精确重建；
- `install_services.sh` 全流程通过、鉴权门禁 11/11、`selfcheck.sh` 18/18；
- 运行期验收用 `deploy/scripts/verify_deploy_auth.py`：它用真实密钥签一个 token，验证"有效 token 放行、已删号 token 立刻 401、无服务间密钥调内部接口被拒、公网匿名 401、后端错误响应是 JSON"，并打印 RAG `health.auth` 的撤销状态。改动鉴权相关代码后跑它。

### 9.7 怎么确认"服务器上的代码是最新的"

别靠时间戳猜（tar 会保留源文件的 mtime，改了本地文件重新上传后，服务器上的时间看着反而更旧），用 `scripts/compare_server_code.py` 逐字节比：

```bash
# 本机：生成 HEAD 的哈希清单（必须 -X utf8，理由见脚本头部）
python -X utf8 scripts/compare_server_code.py --manifest > /tmp/manifest.txt
# 送上去比对（工具本身随代码上传，位于 /opt/china-war/scripts/）
tar czf - -C /tmp manifest.txt | ssh root@<服务器> \
    'tar xzf - -C /root && python3 /opt/china-war/scripts/compare_server_code.py /root/manifest.txt'
```

**只看前两行**：`内容与本地不一致` 与 `服务器上不存在` 都必须是 0。第三类"服务器上有、清单里没有"是缓存/构建产物/未提交的数据文件（`.zcode`、egg-info、`dist/build-mode.txt` 等），一般可忽略——但若里面有 `.py` 或 `.sh`，要查：那可能是只在服务器上直接改过的代码，重启后行为与仓库不一致。

2026-09-26 实测：首次比对查出 3 个文件落后（`deploy/README.md`、`docs/README.md`、`scripts/check_deploy_config.py`）——都是**非运行时代码**，运行代码（`backend/`、`RAG/`、`feishu-bot/`、`entity-event-relation/`）一直是逐字节一致的。补齐后 658/658 全部一致。

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
│   ├── china-war-outbox-retry.timer    # 每分钟触发一次上面的 service
│   ├── china-war-backup.service    # 每日备份（SQLite 在线快照 + 配置与密钥）
│   └── china-war-backup.timer      # 每天 03:20 触发，Persistent=true（关机则开机补跑）
├── env/
│   ├── backend.env                 # 旧后端生产模板 → backend/.env
│   ├── rag.env                     # RAG 生产模板 → RAG/.env
│   └── rag-secrets.env             # 密钥模板 → /etc/china-war/rag-secrets.env
└── scripts/
    ├── 01_setup_server.sh          # 系统包 + Node + Miniconda + 两个 Python 环境（3.11）+ 依赖
    ├── 02_install_neo4j.sh         # Neo4j 5 安装与初始口令
    ├── 03_build_frontend.sh        # 两个前端产物（含模式校验）
    ├── 04_upload_from_local.sh     # 【本机执行】上传代码与数据制品（含换行符兜底）
    ├── 05_backup.sh                # 【服务器】备份 SQLite + 配置密钥（可 --db-only）
    ├── 06_unify_py311.sh           # 【服务器】把后端环境统一到 3.11（旧的改名留作回滚点）
    ├── install_services.sh         # 写 systemd 单元与 nginx 站点、跑鉴权门禁、启动
    ├── check_rag_auth.sh           # 鉴权门禁：模式/两侧密钥同值/nginx 认证真实生效/回环监听
    ├── verify_deploy_auth.py       # 运行期验收：签真 token 验放行、撤销、匿名拒绝、JSON 错误
    └── selfcheck.sh                # 按用户访问路径逐项自检（含备份与两个定时器）
```
