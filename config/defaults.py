"""默认配置：路径与开关的默认值。

所有目录默认相对 RAG 子项目根；可用 .env / 环境变量覆盖，见 settings.py。
"""

from pathlib import Path

# RAG 子项目根目录 = 本文件上两级（config/..）
RAG_ROOT = Path(__file__).resolve().parent.parent

# ---- 数据目录 ----
DATA_DIR = RAG_ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "source_texts"      # 旧原文归一后的拷贝
SNAPSHOT_DIR = DATA_DIR / "snapshot"             # F09 治理快照（带版本子目录）
INDEX_DIR = DATA_DIR / "index"                   # F11 索引（带版本子目录）
CACHE_DIR = DATA_DIR / "cache"                   # 运行时缓存
LOG_DIR = RAG_ROOT / "logs"
# 同源托管（D8）：后端把前端构建产物一并发出，浏览器只访问一个地址
FRONTEND_DIST = RAG_ROOT / "frontend" / "dist"

# ---- 旧项目只读数据源默认值（可用 .env 覆盖）----
LEGACY_SQLITE_PATH = RAG_ROOT.parent / "backend" / "database"
LEGACY_RAW_TEXTS = [
    RAG_ROOT.parent / "entity-event-relation" / "data" / "中国历代战争简史.txt",
    RAG_ROOT.parent / "entity-event-relation" / "data" / "中国战争史地图集.txt",
]

# ---- 治理开关 ----
# 高风险“补边”候选关系抽取，默认关闭（需人工审核）
GOVERNANCE_ENABLE_RELATION_EXTRACTION = False

# ---- 索引开关 ----
# 构建索引时是否调用云端向量模型；无密钥可关掉只建 FTS5 关键词索引
INDEX_BUILD_EMBEDDINGS = True

# ---- 云端文本向量模型（F11 构建 / F04 向量检索；无密钥自动降级关键词）----
EMBEDDING_BASE_URL = ""                # 阿里云百炼 https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY = ""                 # 留空则读系统环境变量 DASHSCOPE_API_KEY
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1024                   # 实测接口返回 1024
EMBEDDING_BATCH_SIZE = 10              # 百炼硬上限：单请求最多 10 条文本，不可配大
EMBEDDING_TIMEOUT_SECONDS = 60
# 向量库（D2）：Chroma 持久化集合
CHROMA_COLLECTION = "chunks_v1"

# ---- 文本检索模式（T3，部署级全局开关；vector/hybrid 依赖向量索引已构建）----
# 2026-09-15 审核整改：代码级兜底与部署默认对齐为 hybrid/rrf，换环境丢失 .env 时
# 不再静默回退关键词模式；向量/Chroma 不可用时由检索层自动降级 keyword 并上报 mode。
TEXT_MODE = "hybrid"                   # keyword / vector / hybrid
TEXT_HYBRID_STRATEGY = "rrf"           # weighted / rrf / fallback
# 2026-09-13 对照评测定档（main 套件 28 题）：rrf 文本召回 97.0% > weighted 95.2% > fallback 94.6%；
# 回答覆盖 rrf/weighted 并列 85.1%，fallback 仅 71.7%（= 关键词基线，等于没做融合）
TEXT_HYBRID_KEYWORD_WEIGHT = 0.5       # weighted 档位下关键词通道权重（向量权重 = 1 - 该值）
# 向量/hybrid 模式下"无共享词"拒答规则的分数阈值（低于它才允许拒答，避免误拒语义命中）
VECTOR_REFUSAL_MIN_SCORE = 0.25

# ---- 文本切分参数 ----
CHUNK_MAX_CHARS = 800          # 单片段最大字符数
CHUNK_OVERLAP_CHARS = 80       # 相邻片段重叠字符数（按句子边界二次切分）

# ---- 在线链路（RAGv2，F02–F06）----
# 大模型（OpenAI 兼容）。RAGv5 起默认走"中转" endpoint，具体地址与模型在 .env 配（密钥不入库）
LLM_BASE_URL = ""                      # 中转示例 https://api.commandcode.ai/provider/v1；官方 https://api.deepseek.com/v1
LLM_API_KEY = ""                       # 密钥只从环境变量读；见 settings.py 的别名链
LLM_MODEL = "deepseek/deepseek-v4.1-flash"   # 中转模型 id；官方口径为 deepseek-flash
LLM_TIMEOUT_SECONDS = 60
LLM_MAX_RETRIES = 2
# F02 LLM 兜底（词典完全未命中 → 模型抽实体）：默认关闭；开启后每问多一次串行调用，
# 直接影响首 Token 预算，故演示默认不开（见 RAGv5 开发说明 §4.5）
ENABLE_LLM_ENTITY_FALLBACK = False
LLM_ENTITY_TIMEOUT_SECONDS = 8        # 兜底独立超时，不复用 F06 的 60 s
# 输出上限：deepseek 系列是推理模型，会先消耗 reasoning token。v5 实测 1024 会被打满
# （reasoning 736 + 正文 288）导致回答被截断，故默认 2048；设得过小还会导致正文为空。
LLM_MAX_TOKENS = 3072
# 备用生成模型（主模型失败降级用；为空 = 不降级）
FALLBACK_LLM_BASE_URL = ""
FALLBACK_LLM_API_KEY = ""
FALLBACK_LLM_MODEL = ""

# ---- 数据版本（2026-09-15 审核 P0-7）----
# 活跃数据版本必须显式固定，否则进程重启时目录里出现更大版本号就会静默切换，
# 灰度/回滚都不可控。留空 = 开发态取"最新一致版本"；生产请在 .env 配 RAG_ACTIVE_VERSION。
ACTIVE_VERSION = ""

# ---- 进程外/内资源边界（2026-09-15 审核 P0-2）----
REQUEST_MAX_BYTES = 65536             # /api/query 请求体上限（字节）；超限 413
QUESTION_MAX_CHARS = 500              # 单次提问字符数上限
SESSION_ID_MAX_CHARS = 128            # 会话 id 字符数上限
HISTORY_CONTENT_MAX_CHARS = 4000      # 单条历史消息字符数上限
HISTORY_MAX_ITEMS = 40               # 历史的条数上限（角色消息总数）
CORRECTIONS_MAX_ITEMS = 20            # corrected_entities 条数上限
FILTERS_MAX_ITEMS = 20                # 单个筛选维度的取值个数上限
FILTER_VALUE_MAX_CHARS = 64           # 单个筛选值字符数上限

# ---- 演示 / 限流 / 缓存（在线链路）----
# 说明：无 DEMO_MODE 开关——示例区恒显示（F08）
RATE_LIMIT_PER_MINUTE = 30            # 无登录公开接口的基础限流
RATE_LIMIT_MAX_KEYS = 4096            # 限流 key 表容量上限（防伪造来源刷爆内存）
# 是否信任 X-Forwarded-For：默认**不信任**（直连安全）。部署在反向代理后才开启，
# 并配合 RATE_LIMIT_TRUSTED_PROXIES 限定可信代理，否则任何人可伪造首段 IP 绕过限流。
RATE_LIMIT_TRUST_FORWARDED_FOR = False
RATE_LIMIT_TRUSTED_PROXIES = ""       # 逗号分隔的代理 IP；空 = 信任任意直连方（仅当上面的开关为真）
CACHE_TTL_SECONDS = 3600              # 回答缓存有效期
CACHE_MAX_ENTRIES = 2048              # 进程内回答缓存条目上限（超出按最旧淘汰）
HISTORY_MAX_TURNS = 4                 # 携带会话历史的最大轮数
QUERY_TOP_K_GRAPH = 40                # F03 图谱证据上限
QUERY_TOP_K_TEXT = 30                 # F04 文本证据上限（融合后再裁剪）
# 送入 F06 的融合证据条数上限（18 = v4 口径）。实测：18 条 → prompt 约 4,300 token →
# 推理模型更易把 max_tokens 吃满而截断、首正文更慢；演示可按需下调（见 RAGv5 开发说明 §四.11）
QUERY_FUSION_LIMIT = 18

# ---- 在线链路安全与稳定性（2026-09-15 审核 P0-3 / P1-8）----
# 是否把模型原始 reasoning 增量推给公共 SSE。默认关闭：
# 推理内容可能包含中间判断与上下文复述，属于模型内部过程，不应直接暴露给调用方
# （见 docs/features/06-grounded-answer.md 的过滤要求）。开启仅用于本地调试。
EXPOSE_THINKING = False
# SSE 连接保活与整体上限：心跳让反代/浏览器知道连接还活着；deadline 防止挂死连接占资源。
SSE_HEARTBEAT_SECONDS = 15
SSE_MAX_DURATION_SECONDS = 300
# CORS 允许来源（逗号分隔）。默认 * 便于本地开发；生产应配成实际站点域名。
CORS_ALLOW_ORIGINS = "*"
# 显式确认"就是要公开 API"（ALLOW_PUBLIC_CORS=true）。
# 生产（RAG_REQUIRE_ACTIVE_VERSION=true）下若 CORS 仍为 *，服务启动会直接失败：
# 无登录的公开问答接口暴露给任意站点，等于把限流配额与模型成本开放给所有人
# （第五轮审核 R5-5）。确实需要公开时把这个开关打开，让风险变成显式决定。
ALLOW_PUBLIC_CORS = False

# ---- 同步工作线程池（2026-09-15 第四轮复核 P1-5）----
# F02/F03/F04 的同步调用（embedding/Chroma/SQLite/图谱）走这个独立线程池。
# max_workers 限制并发；max_queue 限制排队，超出以 server_busy 拒绝，避免断连请求无限堆积。
SYNC_POOL_MAX_WORKERS = 8
SYNC_POOL_MAX_QUEUE = 32
# 收尾余量：外部调用预算 + 余量必须严格小于 SSE_MAX_DURATION_SECONDS（工作单 P1-5）
SHUTDOWN_MARGIN_SECONDS = 15
# 停机时等待在途同步任务的上限（秒）：先停收新任务、撤销排队任务，再用这个上限
# 等正在跑的任务结束，最后才关闭外部 HTTP 客户端（第五轮审核 P0-3）。
# 同步调用无法中断，超时未结束的会被记录为警告并由各自的 HTTP 超时兜底。
SHUTDOWN_DRAIN_SECONDS = 10

# ---- 版本号 ----
# 示例 "20260903_v1"。export/build 未显式给版本时取当天日期生成 v1。
VERSION_DATE_FORMAT = "%Y%m%d"
