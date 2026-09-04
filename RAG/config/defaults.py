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

# ---- 版本号 ----
# 示例 "20260903_v1"。export/build 未显式给版本时取当天日期生成 v1。
VERSION_DATE_FORMAT = "%Y%m%d"
