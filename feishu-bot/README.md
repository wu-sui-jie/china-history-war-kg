# feishu-bot — 飞书知识问答机器人

整个 china-war 项目的**项目级 IM 入口**：仓库根顶层目录，与 `backend/`、`frontend/`、
`RAG/`、`entity-event-relation/` 平级。当前 **P0–P2 功能已实现**（基础闭环、卡片交互
与多轮追问、子图出图与纠错反馈），未做真实飞书租户的端到端人工验收。

## 定位

- 独立进程，飞书官方 SDK WebSocket 长连接接入（无需公网回调地址，个人本机可跑）；
- 技能框架：起步 `knowledge_qa`（调 RAG 非流式接口回答知识问题）、
  `report_error`（纠错反馈收集与投递），外加 `/help` 命令；
  意图分流用规则，不引入 LLM；
- 对 RAG 的唯一依赖是 HTTP 接口（`POST /api/query/json` 等），RAG 引擎保持只读、无状态；
- 依赖独立（`requirements.txt` 不并入 RAG），状态自有（SQLite 会话与反馈）；
- **删除边界**：删除本目录即可完全下线，不影响其他模块；RAG 侧新增的非流式接口为纯增量能力，无需回滚。

## 快速开始

```bash
# 0) 前置：RAG 服务已启动（另一进程），并在飞书开放平台建好企业自建应用
#    （开通机器人能力；事件订阅选「长连接」模式，订阅 im.message.receive_v1 与 card.action.trigger）

# 1) 依赖（可与 RAG 共用 Python 3.11 环境）
cd feishu-bot
pip install -r requirements.txt

# 2) 配置
cp .env.example .env        # 填 FEISHU_APP_ID / FEISHU_APP_SECRET（其余按需）

# 3) 子图出图（P2，可选；不做也能跑，子图会走文字降级）
cd render && npm install echarts @resvg/resvg-js d3-force && cd ..

# 4) 启动（注意用 `python`，不要用 Windows 的 `py` 启动器——`py` 不走 conda 环境）
python main.py
```

日常维护：

```bash
python scripts/cleanup_db.py --dry-run        # 看会清理多少过期会话/事件记录
python scripts/consistency_check.py --limit 3 # SSE 与非流式接口的一致性回归（RAG 发布后跑）
pytest -q                                     # 单元 + 集成测试（不依赖飞书与真实 RAG）
```

## 检测与验收

分三层，**能自动化的都在第 1 层**，需要真实飞书租户的只有第 3 层。

### 第 1 层：本地自动化（无需飞书、无需凭证）

```bash
pytest -q                                     # 单元 + 集成测试（不依赖飞书与真实 RAG）
python scripts/local_smoke.py                 # 真调 RAG 的端到端冒烟，打印卡片结构
python scripts/local_smoke.py --dead-rag      # 看降级卡片（不依赖任何服务）
python scripts/consistency_check.py --limit 3 # SSE 与非流式接口结果必须一致（RAG 发布后跑）
```

`local_smoke.py` 会把卡片 JSON 落到 `data/smoke/`，可**直接贴进飞书开放平台的
「卡片搭建工具」**预览渲染效果——这是不接飞书就能检查卡片长相的办法。它依次验证：
RAG 探活与非流式接口是否存在 → `/help` → 主问题（是否出子图、几个折叠区、哪些按钮）
→ 追问（历史是否透传）→ 纠错反馈（工单卡片 + 落库）→ 事件去重。

### 第 2 层：飞书开放平台一次性配置

控制台每一页该选什么，照着下表走（括号里是页面左侧的菜单名）：

| 页面（控制台） | 怎么选 / 怎么填 |
| --- | --- |
| 添加应用能力（应用能力 → 添加应用能力） | **只加「机器人」**。网页应用 / 工作台小组件 / 云文档小组件 / 多维表格插件 / 链接预览 / 移动应用登录 / 原生集成应用都与此项目无关，不要加 |
| 机器人（应用能力 → 机器人） | 「如何开始使用」填一句欢迎语（≤64 字，单聊里给用户看），例如：`直接发问题给我，例如「介绍一下长平之战」；群里 @我 提问，输入 /help 看说明。`；「机器人自定义菜单」不用配 |
| 消息卡片回调请求方式（机器人页内的说明） | **必须走新版「卡片回传交互」**。新版回调（`card.action.trigger`）在长连接上以 event 帧下发，SDK 会分发给我们的处理器；**旧版（`CARD` 帧）被 `lark-oapi` 1.7.3 直接丢弃**（`ws/client.py` 对 `MessageType.CARD` 立即 return），配了也不生效——所以**不要**去加「卡片回传交互（旧版）」 |
| 权限管理（开发配置 → 权限管理） | 最小集合，**括号内是控制台上显示的名字**：`im:message`（获取与发送单聊、群组消息）、`im:message:send_as_bot`（以应用身份发消息）、`im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息）、`im:message.group_at_msg:readonly`（**获取群组中用户@机器人消息** —— 群聊提问靠它）、`im:resource`（上传图片，子图出图用）。**最省事的路径**：先去「事件与回调」添加事件，控制台会在「所需权限」列列出可选项（"开通以下任一权限即可"），按需点开——注意那一列是**可折叠**的，只开折叠外的第一条会得到"单聊能用、群里 @ 没反应" |
| 事件与回调（开发配置 → 事件与回调） | ① 订阅方式选 **长连接**；② 事件订阅里添加「接收消息」`im.message.receive_v1`；③ 回调订阅里添加「卡片回传交互」（即 `card.action.trigger`）。**不需要**填 Encrypt Key / Verification Token —— 长连接是 SDK 自己的通道，不做签名校验也不解密（已核对代码：event 帧是明文 JSON 直接交给处理器，官方 `FeishuChannel` 也传空值） |
| 安全设置（开发配置 → 安全设置） | 不用配 IP 白名单：长连接是**出站**连接，机器人不暴露端口 |
| 凭证与基础信息（基础信息 → 凭证与基础信息） | 抄 App ID 与 App Secret 到 `feishu-bot/.env`。只有 App Secret 敏感，`.env` 已在 `.gitignore` 里 |
| 版本管理与发布（应用发布 → 版本管理与发布） | 顶部横幅写着"应用发布后，当前配置方可生效"，所以必须**创建版本 → 可用范围选自己（或指定成员）→ 保存并申请发布**。仅自己可见通常无需管理员审批；想先试可用「测试企业和人员」 |
| 日志检索（运营监控 → 日志检索） | 排查用：权限不足、事件投递失败都会给出明确错误码 |

三者确认"配置真的生效"的办法（比猜可靠）：

1. 启动机器人后回「事件与回调」页，长连接状态应显示**已连接**；
2. 机器人启动日志里的 `机器人 open_id：ou_xxx` —— 这一行只有凭证正确、能换取 tenant_access_token 时才会打出来；
3. 若出现「获取机器人 open_id 失败」，群聊 @ 判断会退化为按 mention 文本判断（仍可用），具体错误码去「日志检索」查。

### 第 3 层：接上飞书后的人工验收清单

**最短体验路径**（约 10 分钟，覆盖全部 P0–P2 功能；先按这个走一遍，再看下面的详细表）：

1. 飞书客户端搜索应用名（如「战争事件聊天机器人」）进入单聊，先发 `/help`
   → 应立刻收到使用说明卡片（**不调 RAG**，最快验证"链路通"）
2. 发 `介绍一下长平之战。` → 收到卡片：正文 + `引用（N）` 折叠区 + 示例问题按钮 + 「反馈有误」按钮
3. 点开 `引用（N）` 看原文片段；有子图的问题（如 `介绍一下涿鹿之战。`）卡片里应有关系图**图片**
4. 点卡片底部的示例问题按钮 → 收到那条问题的新回答（验证按钮回调）
5. 追问 `他后来怎么样了？` → 回答能对应上一轮（验证多轮上下文）
6. 把机器人拉进一个测试群（仅自己可见的应用**不能**加入外部群），群里 `@机器人 介绍一下赤壁之战。`
   → 同效；**不 @ 时机器人不应有任何反应**
7. 点「反馈有误」→ 立刻弹 toast「已收到反馈」；**Ctrl-C 停掉机器人再启动**，追问上文仍在（会话落 SQLite）

每一步在日志里都能对上（`处理提问` / `技能命中：knowledge_qa` / `RAG 回答：… finish=normal` /
`duplicate 事件已丢弃`），出错时把对应几行发出来即可定位。

详细表格（逐条操作 / 期望 / 排查定位）：

按顺序走一遍，每条都给了「怎么操作」与「看哪里」。日志关键字可直接 `grep`。

| # | 操作 | 期望结果 | 不对时看哪里 |
| --- | --- | --- | --- |
| 1 | `python main.py` 启动 | 打印"RAG 就绪""机器人服务已启动"，无 ERROR | 出现"未提供 /api/query/json"→ RAG 是改动前的旧实例，重启它 |
| 2 | 单聊问「介绍一下长平之战。」 | 收到卡片：正文 + 折叠的引用区 | 日志 `处理提问`（有）→ `技能命中：knowledge_qa`；只有"降级"→ 看 RAG 日志 |
| 3 | 展开引用区 | 能看到原文片段摘要 | 引用为空 → 该问题确实没有证据，换一题 |
| 4 | 群里 @机器人 问同一题 | 同效；不 @ 时不响应 | 日志"群聊消息未 @ 机器人，忽略"；若 @ 了也不响应 → 看"未取到机器人 open_id"警告 |
| 5 | 接着问「他后来怎么样了」 | 回答能对应上一轮的对象 | 日志中 RAG 收到 `history` 条数；空 → 查 `messages` 表 |
| 6 | 点卡片底部「试试问这些」 | 直接得到该问题的新回答卡片 | 按钮没有 → `/api/demo/examples` 不可用（日志有警告） |
| 7 | **P0-4**：点「反馈有误」 | toast「已收到反馈」+ 运营群收到工单卡片 | 无 toast → 长连接没收到 `card.action.trigger`（事件订阅漏配）；有 toast 但运营群没卡片 → 查 `FEISHU_OPERATORS_CHAT_ID` |
| 8 | **P0-4**：连点两次同一按钮 | 两条都被处理（第二个示例问题会再答一次） | 只处理一次 → `CARD_DEDUPE_WINDOW_SECONDS` 设大了；日志有 `duplicate 卡片回调已丢弃` |
| 9 | 停掉 RAG 再提问 | 收到"服务暂不可用"降级卡片 | 若收到空白或没回复 → 看 worker 日志 |
| 10 | 重启机器人后再追问 | 上下文仍在（会话在 SQLite） | 丢了 → 检查 `BOT_DB_PATH` 是否被换过 |
| 11 | 子图类问题（如"介绍一下涿鹿之战"） | 卡片里有关系图图片 | 只有文字版 → 日志"子图出图不可用：…"（Node 未装或 node_modules 缺失） |
| 12 | 把 `render/node_modules` 改名后再问 | 自动降级为文字列表，**不空白** | 空白 → 记 bug |

> 第 7、8 两条是需求文档风险 4 点名的 **P0-4 实测项**：长连接能否收到卡片回调、
> `event_id` 是否可用于去重、真实连点是否被误杀。跑过这两条，P1/P2 的交互设计才算被证实。

### 排查速查

| 现象 | 常见原因 |
| --- | --- |
| 启动即退出并打印缺少 `FEISHU_APP_ID` | 没建 `.env`（`cp .env.example .env` 后填凭证） |
| `ModuleNotFoundError: No module named 'lark_oapi'` | **解释器不是装依赖的那个**：Windows 上用 `py main.py` 会走 `py` 启动器自己的默认解释器（本机实测指向 `E:\python\python_dataspace\python.exe`），**不进入** conda 环境（哪怕提示符显示 `(AI_Agent)`）。改用 `python main.py`，或直接用绝对路径 `E:\anaconda\envs\AI_Agent\python.exe main.py`。核对命令：`python -c "import sys, lark_oapi; print(sys.executable)"` |
| 日志 `回复消息失败：code=99991672 … scopes is required: [im:message:send, im:message, im:message:send_as_bot]` | 应用缺**发消息**权限。去「权限管理 → 应用身份权限（tenant_access_token）」勾选 **`im:message:send_as_bot`（以应用的身份发消息）**，建议同时勾 `im:message`（获取与发送单聊、群组消息）；两条都是**免审权限**，开通后**重新发布一版**生效。**用户身份权限（user_access_token）那列不用开**——机器人全程用应用身份 |
| `WARNING 子图渲染失败（exit=0）` 且错误信息为空 | 旧版本的中文路径编码问题（已修）：Node 输出的 UTF-8 被按本地编码解读 → JSON 解析失败。用当前代码重启机器人即可；仍失败时日志会带上 stdout/stderr 片段 |
| `Ctrl-C` 后日志说"正在停止"但进程不退 | 旧版本把主线程停在 SDK 的 asyncio 循环里（该循环无公开 stop()）。当前版本长连接跑在后台守护线程，Ctrl-C 即时退出；应急可**连按两次 Ctrl-C** 强制退出 |
| 每次提问都是"服务暂不可用" | RAG 没起、`RAG_BASE_URL` 写错、或跑的是**改动前的旧 RAG 实例**（启动日志的 ERROR 会点名） |
| 每次提问都是"超时" | RAG 刚重启（冷启动要加载词典/向量库、首次调用 embedding），或模型确实慢；`RAG_QUERY_TIMEOUT` 是 25s 的有意预算 |
| 收不到任何消息 | 事件订阅没选长连接 / 没订阅 `im.message.receive_v1` / 应用未发布（配置要发布后才生效）/ 单聊没先给机器人发消息 |
| 单聊正常、群里 @ 没反应 | 缺群聊权限：事件页那列「所需权限」是**可折叠**的，只开了第一条"读取用户发给机器人的单聊消息"就收不到群消息。需要开通 **「获取群组中用户@机器人消息」**（`im:message.group_at_msg:readonly`），开通后**重新发布一版**才生效。注意别误开"获取群组中所有消息（敏感权限）"——那要管理员审批，而且我们不读未 @ 的消息 |
| 版本表单里「事件订阅变更 / 权限变更」显示"暂无" | 那两个区块是给审核人看的差异说明，自建应用仅自己可见时是**免审**发布，不靠它们把关；关键是「事件与回调」页里事件确实在列表里、权限显示「已开通」。表单内容是**打开页面时生成的一次性快照**，改完配置要退出重进才会刷新 |
| 同一条提问回复了两条 | 去重没生效：看 `processed_events` 表与 `duplicate` 日志（正常应有一条 `duplicate` 记录） |
| 卡片显示成裸文本或 `**` 乱飞 | Markdown 收敛器漏了语法：把该回答存下来加进 `tests/data/eval_answers.json` 的样例并跑 `pytest tests/test_md_sanitizer.py` |

## 目录

```text
feishu-bot/
├── main.py                # 入口：配置校验 → 建表 → 自检 → ws 长连接
├── config.py              # 环境变量读取与校验（启动即 fail-fast）
├── bot/
│   ├── feishu_client.py   # SDK 封装：ws 接入、发消息/卡片、上传图片、PATCH 卡片
│   ├── dispatcher.py      # 事件接入：去重、入队、worker 线程
│   ├── session.py         # 会话与历史（SQLite，含字节预算组装）
│   ├── db.py              # SQLite 连接与建表
│   ├── rag_client.py      # RAG HTTP 客户端（超时与错误映射）+ 示例题缓存
│   ├── skills/            # base（协议）/ help / knowledge_qa / report_error
│   ├── cards/             # md_sanitizer（白名单收敛器）+ builder（卡片 2.0）
│   └── render/subgraph.py # P2：子图 → PNG → image_key（失败自动降级）
├── render/                # Node SSR 出图（echarts + resvg + d3-force）
├── scripts/               # consistency_check.py / cleanup_db.py
└── tests/                 # 单测 + 假 RAG 集成测试
```

## 文档

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [docs/需求与方案.md](docs/需求与方案.md) | 目标 G1–G6、两层架构、分期 P0–P2、改造点清单、关键技术决策、代码事实表、待决项与风险 | 需求已确认（v2.1）；P0–P2 已实现 |
| [docs/开发文档.md](docs/开发文档.md) | 技术选型、目录结构、进程模型、模块设计、RAG 非流式接口契约、SQLite 表结构、配置项、任务拆解与验收、测试与部署 | v1.2（2026-09-21）：实现已落地，含实现期补充说明 |

实现与设计文档的差异都在开发文档的「实现说明」小节里逐条记录（技能扩展字段、
卡片按钮的落库顺序、纠错回执取舍等）。
