# RAG 问答系统部署与演示手册（RAGv5）

> 目标形态（决策 D8）：**同源托管**——一个后端进程同时提供网页与 API，浏览器只访问一个地址。
> 本文按"从零到可演示"的顺序写，照做即可复现。

## 一、前置条件

| 项 | 要求 |
| --- | --- |
| Python | 3.11（本项目在 `E:/anaconda/envs/AI_Agent` 验证；依赖见 `requirements.txt`） |
| Node | 仅"重新构建前端"时需要（`frontend/`，Vue3 + Vite） |
| 数据制品 | `data/snapshot/<v>` + `data/index/<v>`（**两目录已被 .gitignore 忽略，需随部署包携带**） |
| 外部服务 | ① 阿里云百炼（文本向量化）② LLM endpoint（中转 `api.commandcode.ai` 或官方 `api.deepseek.com`）——**两者都需要外网** |
| 密钥 | 走系统环境变量（**不写进仓库、不写进 `.env`**）：见第四节 |

## 二、数据制品

> 版本说明（2026-09-15）：当前活跃版本为 `20260915_v1`（在 `20260904_v2` 基础上写入地点坐标，
> 文本片段与向量逐条一致）；历史版本 `20260904_v2` 保留可回退。

```
data/
├── snapshot/20260915_v1/          # F09 治理快照（entities/relations/event_cards/dicts/…；含地点坐标）
├── index/20260915_v1/             # F11 索引（版本号必须与快照一致）
│   ├── chunks.jsonl               # 9,544 片段（切分：800 字 / 80 重叠）
│   ├── chunks_fts.db              # 关键词索引（SQLite FTS5 + jieba）
│   └── vectors/                   # 向量（RAGv5）
│       ├── chroma/                # Chroma 持久化集合（**检索只走这里**）
│       ├── ids.json               # 片段 id 顺序（与矩阵/集合行对齐）
│       ├── embeddings.npy         # 审计副本（不参与检索；换机器可免重嵌入）
│       └── _parts/                # 分批缓存（可选携带；保留可断点续跑，可删）
└── eval/20260915_v1/              # 题库、评分、示例题清单
    └── demo_examples.json         # F08 示例题（入 Git，可人工复核）
```

体积参考：索引目录约 150 MB（其中 `chroma/` 47 MB、`npy` 38 MB、`_parts/` 42 MB、`chunks_fts.db` 约 23 MB）。

## 三、从零构建（若已有制品可跳过）

```bash
cd RAG

# 1) 快照（F09）——需可读旧项目数据源（.env 里的 LEGACY_*）
python scripts/export_snapshot.py --version 20260915_v1

# 2) 索引：切分 + FTS5（+ 向量）——F11
python scripts/build_index.py --version 20260915_v1 --no-embeddings   # 只建关键词索引（无向量密钥时）
python scripts/build_index.py --version 20260915_v1                   # 有向量密钥：同一步嵌入并写 Chroma

# 3) 向量（RAGv5 T3）——复用既有 chunks，只补/重建 vectors/
python scripts/build_index.py --vectors-only --sample 8      # 先小样验证维度（约 5 秒）
python scripts/build_index.py --vectors-only --concurrency 4 # 全量（9,544 条约 6 分钟）
python scripts/check_vector_consistency.py --sample 20       # 一致性抽检（重合率 ≥0.9）
# chroma/ 丢失或损坏时（不调云端、不重复计费）：
python scripts/build_index.py --rebuild-chroma --version 20260915_v1

# 4) 前端构建产物（供同源托管）
cd frontend && npm install && npm run build && cd ..
```

## 四、环境变量

**A. `.env`（项目根，不入库；非密钥项）**——关键项：

| 变量 | 演示取值 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` / `LLM_MODEL` | `https://api.commandcode.ai/provider/v1` / `deepseek/deepseek-v4.1-flash` | 项目期走中转（便宜）；项目后切官方 `api.deepseek.com/v1` + `deepseek-flash` |
| `LLM_MAX_TOKENS` | `3072` | 推理模型先花 reasoning token，设小会导致**正文为空/被截断** |
| `FALLBACK_LLM_BASE_URL` / `FALLBACK_LLM_MODEL` | `https://api.deepseek.com/v1` / `deepseek-flash` | 中转不可用时自动降级到官方 |
| `TEXT_MODE` | `hybrid` | keyword / vector / hybrid（**部署级开关，切换需重启**） |
| `TEXT_HYBRID_STRATEGY` | `rrf` | weighted / rrf / fallback（评测定档 rrf 召回最高） |
| `QUERY_FUSION_LIMIT` | `10` | 送模证据条数；18（v4 口径）时 prompt 约 4,300 token → 断更易截断、首字更慢 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | 百炼 + `text-embedding-v4` / `1024` | 查询侧向量化用 |
| `RATE_LIMIT_PER_MINUTE` / `CACHE_TTL_SECONDS` | `30` / `3600` | 演示负载足够；缓存是**进**程内的，重启即空 |

**B. 系统环境变量（密钥，只在这里）**——读取优先级即别名链：

```
LLM_API_KEY → DEEPSEEK_API_KEY → RAG-command（中转，项目期） → RAG-deepseek-v4（官方）
EMBEDDING_API_KEY → DASHSCOPE_API_KEY（百炼）
```

注意两点：Windows 新建环境变量需**重启进程**才会被继承；**连字符变量名在 Linux 上不合法**，
迁到 Linux 时请用 `LLM_API_KEY` / `DEEPSEEK_API_KEY` 之类的名字。

## 五、启动与自检

```bash
python scripts/run_server.py --host 0.0.0.0 --port 8000
```

启动日志会打印：版本、`text_mode`、LLM 模型与备用模型、同源托管目录、自检结果。
`workers` 固定为 1（内存图谱、回答缓存、限流计数都在进程内，多 worker 会各持一份）。

```bash
curl http://127.0.0.1:8000/api/health    # status=ok，核对 vector_available / llm_available / meta.text_mode
curl -I http://127.0.0.1:8000/           # 200 + text/html（同源托管生效）
```

演示机若只在本机访问，用 `--host 127.0.0.1`；要局域网投屏给评委看，用 `--host 0.0.0.0` 并放行防火墙端口。

## 六、冒烟与"预热"（演示前必做）

```bash
python scripts/smoke_deploy.py --base http://127.0.0.1:8000
```

它按顺序检查：健康检查 → `GET /` 返回页面 → 示例题接口 → **逐条跑示例题**（断言
`finish_reason=normal`、回答非空、有引用、证据数达标——即图谱/文本通道证据数不低于
示例题 `expect.graph_min` / `text_min` 下限）→ 重复第一题验证缓存命中 → （可选
`--check-rate-limit`）限流。报告落 `logs/smoke_<时间>.json`，失败时退出码非 0。

**它同时是"预热"**：回答缓存是进程内的，冒烟会把示例题都跑一遍，之后演示时点击示例题
只需几十毫秒（实测 29 ms）——**演示前不要重启服务**，否则缓存清空、首字又要等几秒到十几秒。

## 七、演示时的预期表现与话术

| 场景 | 预期 | 说明 |
| --- | --- | --- |
| 点击已预热的示例题 | 立即（<100 ms） | 命中回答缓存，界面直接回放答案 + 引用 + 面板 |
| 现场临时提问（未缓存） | 首段文字 **2–14 秒**；整段生成耗时中位 **约 10 秒**、最长约 25 秒 | 推理模型先"思考"；期间前端显示"正在生成"状态条，不是卡死。默认**不**外发原始 reasoning（只报 `done.first_thinking_ms`），需要时才设 `EXPOSE_THINKING=true`。数据来自 2×28 题的真实 LLM 评测 |
| 一次问答的总耗时 | 4–20 秒 | 与题目长度、证据数量、中转当时负载都有关，**同一题不同时刻也会波动** |
| 检索通道 | hybrid（关键词 + 向量融合） | 面板里能看到图谱证据与文本证据两类 |
| 断网/中转不可用 | 自动降级 | 回答由离线摘要回答器产出，`finish_reason=degraded`，页面仍可用 |

## 八、常见故障

| 现象 | 原因与处理 |
| --- | --- |
| 启动即 `FileNotFoundError: 快照…无同版本索引目录` | 数据制品缺失或快照/索引版本不一致（两者必须同名） |
| `/` 返回 404 或 JSON | 前端未构建（`cd frontend && npm run build`），或 `FRONTEND_DIST` 指错 |
| `vector_available=false` | 索引目录缺 `vectors/chroma/`，或 `count()` 与 `ids.json` 不一致，或没配向量密钥 → 会自动降级关键词 |
| 回答被截断（日志 `LLM 输出触及 max_tokens`） | 调小 `QUERY_FUSION_LIMIT`（先试 8）或调大 `LLM_MAX_TOKENS` |
| 首字很久（>15 s） | 中转负载波动或该题推理很长；换题目、或先跑一次让它进缓存 |
| 缓存"不生效" | 缓存是进程内的：重启后首次必然全量；另外改变 `TEXT_MODE` 也会换缓存键 |
| 限流误伤 | 默认按**直连来源 IP** 计数，`X-Forwarded-For` 不参与（防伪造）；部署在反向代理后才设 `RATE_LIMIT_TRUST_FORWARDED_FOR=true` 并用 `RATE_LIMIT_TRUSTED_PROXIES` 限定可信代理。演示前确认出口 IP，必要时调 `RATE_LIMIT_PER_MINUTE` |
| `chromadb` 导入失败 | 依赖只声明下界（`chromadb>=1.3`），本项目实测环境为 `chromadb 1.3.4`；如需可复现请自行生成锁文件（见 P2-6） |

## 九、数据来源与授权

- 原始图书文本用于切分建库，**公开演示前需确认授权**（见 `docs/architecture.md` 风险记录）；
- 未确认授权时：仅内部/比赛演示，或只演示不含原文引用的部分。
