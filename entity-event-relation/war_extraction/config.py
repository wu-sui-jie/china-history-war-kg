"""
Extraction configuration shared by prompts, cache, export, and evaluation.
"""

import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

#: 全项目的墙钟口径：metadata 时间戳、批次目录名都用它，避免各处自行 datetime.now()
_TZ_SINGAPORE = timezone(timedelta(hours=8))

#: 提示词模板源码目录（按 __file__ 锚定，与缓存/配置目录同一路径口径）
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

EXTRACTION_VERSION = "extraction-v2-20260420"

#: 模型目录（缓存键要带上"产物 schema 长什么样"）
_MODELS_DIR = Path(__file__).resolve().parent / "models"

#: 配置目录（与 Normalizer 的默认配置目录同一处）
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

#: **参与缓存失效**的配置文件。刻意不含 `eval_config.json`：它只影响评估阈值，
#: 调评估口径不该让整本抽取的缓存全部失效（那是真金白银）。含进来的四个都会改变
#: 抽取产物本身：别名表改名字归并、关系映射改关系名归一、朝代表参与年份/朝代判断、
#: 发布规则改发布子集。
CACHE_CONFIG_FILES = (
    "aliases.json",
    "relation_types.json",
    "dynasty_ranges.json",
    "publish_rules.json",
)

_CONFIG_BASELINE = "config-v1-20260420"
_SCHEMA_BASELINE = "schema-v1-20260420"


def config_source_hash() -> str:
    """
    `config/*.json` 里"会影响抽取产物"的那几个文件的 sha256 前 8 位。

    **为什么要并入缓存键。** `CONFIG_VERSION` 原先是一个硬编码串，于是改了
    `aliases.json`（别名归一）或 `relation_types.json`（关系名归一）之后，
    缓存键**一点不变**——旧的分段结果照样命中，产物里的名字与关系名要么还是旧的、
    要么与新规则不一致。实测到的现象是"改了别名表，重跑一遍什么都没变"，
    而这件事没有任何提示。并入哈希之后，改一个字符就让相关缓存失效。
    """
    digest = hashlib.sha256()
    for name in CACHE_CONFIG_FILES:
        path = _CONFIG_DIR / name
        if not path.is_file():
            # 与 publish_rules / text_cleaner 同一口径：**缺文件必须出声**。
            # 静默跳过会让"配置文件没加载"表现为"缓存键少一个因子"，
            # 而哈希少一项是完全看不出来的——哈希照样算得出一个 8 位串。
            print(f"  [配置缺失] {path} 不存在：{name} 不参与缓存键"
                  f"（改这个文件不会让缓存失效，产物可能与当前规则不一致）")
            continue
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


def model_schema_hash() -> str:
    """
    pydantic 模型源码的 sha256 前 8 位。

    字段增删会改变写进缓存与产物的 JSON 形状，而"形状变了、缓存键没变"会让旧结果
    以新 schema 被读出来（缺字段、多字段都静默）。所以把它也并入缓存键。
    """
    digest = hashlib.sha256()
    for path in sorted(_MODELS_DIR.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


#: 配置版本：人写语义串 + **配置源码哈希**（改别名/关系映射/发布规则就换版本）
CONFIG_VERSION = f"{_CONFIG_BASELINE}+{config_source_hash()}"

#: 产物 schema 版本：人写语义串 + **模型源码哈希**
SCHEMA_VERSION = f"{_SCHEMA_BASELINE}+{model_schema_hash()}"


def prompt_source_hash(name: str = None) -> str:
    """
    提示词模板源码的 sha256 前 8 位。

    **为什么机械派生版本号。** 单靠人写的版本串，改提示词忘了 bump 就会：
    ①缓存键不变 → 命中旧结果，改了等于没改；②产物 metadata 里的 `prompt_version`
    与实际提示词不符（学术评估里这是硬伤）。这里把哈希拼进版本串，改一个字符就换版本，
    缓存自动失效，不依赖人记得。

    Args:
        name: 只对某个模板文件取哈希（如 "entity_prompts.py"）；None 表示本目录下全部

    Returns:
        8 位小写十六进制串
    """
    digest = hashlib.sha256()
    if name:
        files = [_PROMPTS_DIR / name]
    else:
        files = sorted(_PROMPTS_DIR.glob("*.py"))
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


#: 提示词版本：人写的语义版本 + **源码哈希**。哈希段变化 = 提示词文本变了，缓存键随之失效。
_PROMPT_BASELINE = "prompt-v2-20260420"
PROMPT_VERSION = f"{_PROMPT_BASELINE}+{prompt_source_hash()}"

#: 分项版本：各自只跟本阶段那个模板文件的哈希（改事件模板不该让实体阶段的缓存失效）。
#: `event_type` 这一项随"事件类型判定"阶段的删除一起去掉了（该阶段是死代码，
#: 全仓无调用者，`EventType` 一直由 `full_event` 产出）。
#:
#: 注意：**整个提示词目录**的哈希（`PROMPT_VERSION`）才进缓存键，所以改任一模板都会让
#: 三个阶段的缓存全部失效——分项版本目前只用于产物 metadata 里标明各阶段的模板版本。
PROMPT_VERSION_FILES = {
    "entity_extraction": "entity_prompts.py",
    "event_identification": "event_prompts.py",
    "full_event": "event_prompts.py",
    "relation_extraction": "relation_prompts.py",
}

PROMPT_VERSIONS = {
    stage: f"{stage}-prompt-v2-20260420+{prompt_source_hash(filename)}"
    for stage, filename in PROMPT_VERSION_FILES.items()
}

DEFAULT_CHUNK_SIZE = 1800
DEFAULT_OVERLAP = 200


def current_timestamp() -> str:
    """Return an Asia/Singapore timestamp for metadata and change logs."""
    return datetime.now(_TZ_SINGAPORE).strftime("%Y-%m-%d %H:%M:%S +08:00")


def current_time_tag() -> str:
    """
    文件名 / 目录名用的紧凑时间标签（``20260925_183012``）。

    给"每次运行一个目录"的产物命名用，与 ``current_timestamp`` 共用同一个时区，
    免得目录名与 metadata 里的时间差几小时。
    """
    return datetime.now(_TZ_SINGAPORE).strftime("%Y%m%d_%H%M%S")


def cache_context(model_name: str, stage: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                  overlap: int = DEFAULT_OVERLAP) -> dict:
    """
    缓存键的上下文：按模型、提示词版本、抽取阶段与分段参数区分，避免复用旧响应。

    除提示词版本外还并入 `config_version`（config/*.json 哈希）与 `schema_version`
    （模型源码哈希）：这两样变了而键不变时，"改了规则/改了字段，重跑结果没变"
    会是个完全静默的坑。
    """
    return {
        "model_name": model_name,
        "prompt_version": PROMPT_VERSION,
        "extraction_version": EXTRACTION_VERSION,
        "config_version": CONFIG_VERSION,
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "chunk_size": chunk_size,
        "overlap": overlap,
    }
