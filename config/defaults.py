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
# 构建向量索引时是否调用云端向量模型；无密钥可关掉只建 FTS5 关键词索引
INDEX_BUILD_EMBEDDINGS = True

# ---- 文本切分参数 ----
CHUNK_MAX_CHARS = 800          # 单片段最大字符数
CHUNK_OVERLAP_CHARS = 80       # 相邻片段重叠字符数（按句子边界二次切分）

# ---- 在线链路（RAGv2，F02–F06）----
# 大模型（deepseek-v4-flash，OpenAI 兼容）
LLM_BASE_URL = ""                      # 例如 https://api.deepseek.com/v1
LLM_API_KEY = ""                       # 密钥只从 .env / 环境变量读取
LLM_MODEL = "deepseek-v4-flash"
LLM_TIMEOUT_SECONDS = 60
LLM_MAX_RETRIES = 2
# 备用生成模型（主模型失败降级用；为空 = 不降级）
FALLBACK_LLM_BASE_URL = ""
FALLBACK_LLM_API_KEY = ""
FALLBACK_LLM_MODEL = ""

# ---- 演示 / 限流 / 缓存（在线链路）----
DEMO_MODE = False
RATE_LIMIT_PER_MINUTE = 30            # 无登录公开接口的基础限流
CACHE_TTL_SECONDS = 3600              # 回答缓存有效期
HISTORY_MAX_TURNS = 4                 # 携带会话历史的最大轮数
QUERY_TOP_K_GRAPH = 40                # F03 图谱证据上限
QUERY_TOP_K_TEXT = 30                 # F04 文本证据上限（融合后再裁剪）

# ---- 版本号 ----
# 示例 "20260903_v1"。export/build 未显式给版本时取当天日期生成 v1。
VERSION_DATE_FORMAT = "%Y%m%d"
