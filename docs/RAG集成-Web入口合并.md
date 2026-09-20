# RAG 并入旧知识库系统 —— Web 入口合并说明

> 2026-09-20。本文档讲的是**入口合并**，不是代码合并：RAG 仍是独立服务、独立仓库、
> 独立进程；合并的是"用户从哪里进入"和"对外只有一个域名"。

## 一、为什么不是代码合并

| 系统 | 运行时 | Web 框架 |
| --- | --- | --- |
| 旧知识库系统 | Python 3.8 | Flask + SQLAlchemy + py2neo |
| RAG | Python 3.11 | FastAPI + pydantic + chromadb |

把 RAG 做成 Flask 蓝图会同时引入依赖冲突、启动耦合与部署耦合，并且会废掉 RAGv5 已经
过审的部署链路（版本固定、health 校验、CORS 启动门禁、冒烟脚本）。因此边界定在 HTTP：
**旧系统管数据维护，RAG 管问答，反代把两者拼成一个入口。**

这条结论与 `RAG/new/RAG分离与飞书Hermes接入-答疑纪要.md` 的 Q1 一致（那里还规划了
飞书/Hermes 渠道；本文档只处理 Web 入口这一支）。

## 二、路径与端口约定

| 服务 | 端口 | 对外路径 | 说明 |
| --- | --- | --- | --- |
| 旧前端（layui 管理台） | 开发 3001 / 生产由 nginx 发 | `/static/*`（vite base） | 菜单与页面 |
| 旧后端（Flask） | 5000 | `/api/*` 及其余 | 图谱 CRUD、质量台、旧问答 |
| RAG 后端（FastAPI） | 8000 | `/rag/*` | 页面与接口都在这个前缀下 |

**关键约定**：`/rag/*` 去掉前缀后正好对上 RAG 自己的路径约定——页面在 `/*`、
接口在 `/api/*`。所以**一条反代规则同时覆盖页面与接口**，不需要为两者分别配置：

```text
浏览器  /rag/              → RAG 服务  /              （前端 dist 的 index.html）
浏览器  /rag/assets/x.js   → RAG 服务  /assets/x.js
浏览器  /rag/api/query     → RAG 服务  /api/query     （SSE 问答）
```

这也是为什么 RAG 前端必须以**并入模式**构建：它的接口前缀被参数化成 `/rag/api`
（原先是写死的根绝对路径 `/api/...`，挂到子路径后会打到旧 Flask 的 `/api/*` 上——
两边同前缀，必然串台）。

## 三、开发环境怎么跑

```bash
# 1) 构建 RAG 前端（并入模式：base=/rag/、接口前缀=/rag/api，参数在 RAG/frontend/.env.integration）
cd RAG/frontend && npm run build:integration && cd ../..

# 2) 启动 RAG 服务（同源托管：检测到 frontend/dist 就把产物挂到 /）
cd RAG && python scripts/run_server.py --port 8000
#    数据版本建议显式固定：--version 20260915_v1

# 3) 启动旧后台（其 vite 代理把 /rag 转发到 8000、/api 转发到 5000）
cd frontend && npm run dev

# 4) 启动旧后端
cd backend && python app.py
```

浏览器访问 `http://127.0.0.1:3001/#/knowledge/rag`（菜单「知识图谱 → RAG 智能问答」）。

改动前的自检（不需要浏览器）：

```bash
curl -s  http://127.0.0.1:3001/rag/          | head -20   # 应返回 dist 的 index.html
curl -s  http://127.0.0.1:3001/rag/api/health            # 应返回 RAG 的 health JSON
curl -s  http://127.0.0.1:3001/rag/ | grep assets        # 资源前缀必须是 /rag/assets/...（见下）
```

**构建产物模式（踩过的坑，2026-09-20）**：`dist/` 只有一份，`npm run build`（base=/）
与 `npm run build:integration`（base=/rag/）**互相覆盖**。并入环境下若误用普通模式构建，
index.html 会引用 `/assets/...` 而不是 `/rag/assets/...`——浏览器在 3001 上请求这些路径拿到 404，
表现为 **iframe 一片空白且控制台不报错**（只有 Network 面板能看到 404）。

- 只要页面是经 `/rag/` 访问的（开发 3001 反代、生产 nginx），就必须 `npm run build:integration`；
- 独立部署（直连 RAG 服务根路径、无子路径前缀）才用 `npm run build`；
- 在 RAG 前端跑过测试/构建门禁（`npm test`、`npm run build`）之后，若要继续用并入入口，
  记得补一次 `npm run build:integration` 把产物换回来。

## 四、生产部署：nginx 分流

```nginx
upstream china_war_backend { server 127.0.0.1:5000; }
upstream rag_backend       { server 127.0.0.1:8000; }

server {
    listen 80;
    server_name your.host;

    # 旧后台前端静态资源（vite base = /static/）
    location /static/ {
        alias /path/to/china-war/frontend/dist/;
        try_files $uri $uri/ =404;
    }

    # RAG 智能问答：页面与接口都在 /rag/ 下，整体转发并去掉前缀。
    # proxy_pass 结尾的斜杠就是"去掉 /rag"（等价于开发环境 vite 的 rewrite）。
    location /rag/ {
        proxy_pass http://rag_backend/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 关键项：RAG 的回答是流式响应，任何缓冲都会让回答"憋住不出字"
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection "";
        # RAG 的 SSE_MAX_DURATION_SECONDS 默认 300，这里留足余量
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # 旧后端（含 /api/*）
    location / {
        proxy_pass http://china_war_backend;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

前端与接口同源之后，**浏览器侧完全不涉及 CORS**（不需要给 RAG 配 `CORS_ALLOW_ORIGINS`）。

## 五、必须一起改的两个 RAG 环境变量（否则生产会静默出错）

这两项都是"不改也能启动，但行为错"的类型，务必在部署前确认：

### 1. 限流会退化成全体用户共享一个桶

RAG 默认按**直连来源 IP** 计数，且 `X-Forwarded-For` 不参与（防伪造）。放到反代后面，
所有用户对 RAG 来说都来自 `127.0.0.1`，于是 `RATE_LIMIT_PER_MINUTE=30` 变成
**全站合计 30 次/分钟**——一个人多问几句，其他人就一起被 429。

```bash
RATE_LIMIT_TRUST_FORWARDED_FOR=true
RATE_LIMIT_TRUSTED_PROXIES=127.0.0.1      # nginx 与 RAG 同机；不同机填 nginx 的出口 IP
```

（依据：`RAG/docs/deploy.md` 的故障排查表、`RAG/config/defaults.py` 第 97–102 行。）

### 2. 生产档与 `CORS_ALLOW_ORIGINS=*` 互斥，会直接启动失败

RAG 的启动门禁规定：显式生产档（`RAG_REQUIRE_ACTIVE_VERSION=true`）下若 CORS 仍是
通配符，服务**拒绝启动**（避免无登录的公开问答接口被任意站点调用）。同源反代本身不需要
CORS，但这个门禁仍会触发，需要显式表态：

```bash
# 二选一：
CORS_ALLOW_ORIGINS=https://your.host     # 收窄到实际站点
ALLOW_PUBLIC_CORS=true                   # 或者显式承认"就是要公开"
```

（依据：`RAG/server/api.py` 第 81–99 行、`RAG/config/defaults.py` 第 120–126 行。）

## 六、仓库组织：RAG 作为 submodule

RAG 在自己的仓库 `wu-sui-jie/china-history-war-kg` 独立演进（独立 history、CI、评测链），
在 china-war 中以 submodule 形式固定到某个提交：

```bash
git clone --recursive <china-war-url>     # 首次克隆要带 --recursive
git submodule update --init RAG           # 已克隆过则用这条

cd RAG && git fetch && git checkout <目标提交> && cd ..   # 升级 RAG 版本
git add RAG && git commit -m "chore: 升级 RAG 到 <提交>"
```

之所以不直接把代码并进 china-war：RAG 是**公开仓库**，而原书文本与治理快照受版权约束、
不能入库（见 `RAG/README.md` 第七节）。submodule 的边界天然阻止 china-war 的 `git add .`
把 RAG 的数据制品与 `.env` 一起提交上去。

> 注意：`RAG/new/` 目录（分离与飞书接入的评审文档）目前还是 RAG 仓库里的未跟踪文件，
> 没有随本次提交入库；需要归档的话在 RAG 仓库单独提交。

## 七、数据不会自动跟着旧库更新

入口合并**不解决数据同步**：RAG 的 `data/snapshot`、`data/index` 是 F09/F11 离线制品，
旧库改了图谱数据，RAG 不会自动感知。换数据版本仍是固定流程：

```bash
cd RAG
python scripts/export_snapshot.py     # F09 治理快照
python scripts/build_index.py         # F11 文本与向量索引
python scripts/run_server.py --port 8000 --version <新版本号>   # 重启并固定版本
```

## 八、安全边界（务必知道）

- **RAG 自身没有任何鉴权**：`server/api.py` 里没有 auth/jwt 依赖，`/api/query` 谁都能调。
- **旧后台的登录门禁管不到 RAG**：Flask 的全局 `before_request` 只作用于经过它的请求，
  而 `/rag/*` 被反代直接送去了 8000，不经过 Flask。旧前端路由上的 `requireAuth` 只是
  前端守卫，绕过成本为零。
- 因此当前形态**依赖内网隔离**。若 RAG 必须暴露公网，需在 nginx 的 `location /rag/`
  上加认证（如 `auth_basic`、或 `auth_request` 交给上层 SSO），不要只靠前端菜单隐藏。
- 另外提醒：`RAG/new/RAG分离与飞书Hermes接入-答疑纪要.md` 的 Q6 写「旧后端只读接口
  目前无后端鉴权」，这一条**与代码不符**——`backend/app.py` 有全局 token 校验
  （第 1746 行 `before_request`，放行 `/`、`/api/login`、`/api/sign_in`、`/static*`）。
  该校验较弱（只验 token 存在并 decode，不区分读写），但确实存在；该文档的这处结论
  建议修订。
- **判断旧后端鉴权别看 HTTP 状态码**（2026-09-20 实测）：未带 Token 请求受保护接口时，
  响应体是 `{"code": 403, "msg": "您还未登录，请先登录"}`，但 **HTTP 状态码是 200**——
  `jsonify(...)` 没有带状态码，Flask 默认回 200。用 `curl -w "%{http_code}"` 或只看
  HTTP 层会误判成"没有鉴权"，必须读响应体里的 `code` 字段。前端 axios 也是按体里的
  `code` 判断的。

## 九、故障排查

| 现象 | 多半是 |
| --- | --- |
| 页面能开，但问答请求 404/打到旧后端 | RAG 前端用了默认模式构建（前缀 `/api`）。改用 `npm run build:integration` 重建 |
| 页面白屏、资源 404 | dist 是默认模式（`/assets/...`）而反代在 `/rag/` 下；同样用 `build:integration` 重建 |
| 回答"憋住"，最后一次性全出来 | 反代缓冲没关：确认 `proxy_buffering off` 与 `proxy_read_timeout` |
| 多人同时用就开始 429 | 限流按反代 IP 计数：按第五节设 `RATE_LIMIT_TRUST_FORWARDED_FOR` |
| 改了 `RAG/frontend/vite.config.ts` 但 `npm run dev` 没生效 | `vue-tsc -b` 会在同目录生成 `vite.config.js`（已 gitignore），Vite 解析顺序里 `.js` 优先于 `.ts`，于是读到旧配置。删掉 `frontend/vite.config.js` 与 `vite.config.d.ts` 再跑 |
| RAG 服务启动即失败，日志提到 CORS | 生产档 + `CORS_ALLOW_ORIGINS=*`：见第五节第 2 条 |
| 侧边栏找不到「RAG 智能问答」 | 只改了 `backend/app.py` 的菜单。菜单实际来自 `frontend/src/mockjs/user.ts`（mockjs 拦了 `/user/menu`），且还要过 `store/user.ts` 的白名单——见第十节 |
| 直接开 `:8000` 白屏、控制台一堆 `/rag/assets/*` 404 | dist 是并入模式，只能在 `/rag/` 前缀下访问。要么走旧前端/nginx 的 `/rag` 入口，要么用 `npm run build` 重建回默认模式 |

## 十、改动清单（本次）

RAG 仓库（提交 `ab2b32c`）：

- 新增 `frontend/src/api/base.ts`：接口前缀的唯一来源，默认 `/api`；
- 新增 `frontend/.env.integration`、`npm run build:integration`：并入模式的构建参数；
- `frontend/vite.config.ts`：`base` 与开发代理由 `VITE_BASE_PATH` / `VITE_API_BASE` 推导；
- `frontend/src/api/{http,demo,sse}.ts`、`frontend/src/env.d.ts`：改用接口前缀；
- `frontend/README.md`：补并入模式说明。

china-war 仓库：

- `frontend/vite.config.ts`：新增 `/rag` 代理规则；
- `frontend/src/views/knowledge/RagAssistant.vue`：新增（iframe 承载 RAG 页面）；
- `frontend/src/router/module/base-routes.ts`：新增路由 `/knowledge/rag`；
- `frontend/src/config/index.ts`：新增 `ragBase`；
- `frontend/src/mockjs/user.ts`：菜单数据新增「RAG 智能问答」（**这才是侧边栏实际渲染的源**，见下）；
- `frontend/src/store/user.ts`：`mergeWorkspaceMenus` 的 id 白名单放行 `/knowledge/rag`；
- `backend/app.py`：`/user/menu` 新增菜单项（**目前对界面无效**，原因见下；保留是为了这份接口本身正确）；
- `.gitmodules`：RAG 以 submodule 接入。

### 加菜单项要改三处（踩过的坑）

`backend/app.py` 的 `get_menu()` 对这个前端**从未被调用过**：`frontend/src/main.ts:6`
无条件 `import './mockjs'`，mockjs 在 XHR 层拦下了 `/user/login`、`/user/menu`、
`/user/permission`（`src/mockjs/index.ts`），于是登录与菜单全部由
`frontend/src/mockjs/user.ts` 的 mock 数据提供。所以 2026-09-20 第一次加菜单项时，
只改后端＝页面上什么都不出现。

正确的三处：

1. `frontend/src/mockjs/user.ts` —— 菜单数据（实际生效的源）；
2. `frontend/src/store/user.ts` 的 `mergeWorkspaceMenus` —— id 白名单，不在清单里的项会被
   `.filter(Boolean)` **静默丢弃**；
3. `frontend/src/router/module/base-routes.ts` —— 路由，否则点进去是 404。

（若哪天真去掉了 mockjs，`backend/app.py` 的 `get_menu()` 才成为菜单源，届时同样要满足第 2、3 条。）

默认构建口径未变：`npm run build` 仍是独立部署形态（base `/`、接口前缀 `/api`），
RAGv5 的同源托管与部署链路不受影响。
