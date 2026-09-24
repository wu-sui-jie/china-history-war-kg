"""
Extraction configuration shared by prompts, cache, export, and evaluation.
"""

import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

#: 全项目的墙钟口径：metadata 时间戳、批次目录名都用它，避免各处自行 datetime.now()
_TZ_SINGAPORE = timezone(timedelta(hours=8))

#: 提示词模板源码目录（按 __file__ 锚定，与 C-2 的路径口径一致）
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

EXTRACTION_VERSION = "extraction-v2-20260420"
CONFIG_VERSION = "config-v1-20260420"


def prompt_source_hash(name: str = None) -> str:
    """
    提示词模板源码的 sha256 前 8 位（EER-5 / 第 11 轮 C-4）。

    **为什么机械派生版本号。** 原先 `PROMPT_VERSION` 是手写的单一版本串，改提示词忘了
    bump 就会：①缓存键不变 → 命中旧结果，改了等于没改；②产物 metadata 里的
    `prompt_version` 与实际提示词不符（学术评估里这是硬伤）。现在版本号由源码哈希拼出来，
    改一个字符就换版本，缓存自动失效，不依赖人记得。

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

#: 分项版本：各自只跟本阶段那个模板文件的哈希（改事件模板不该让实体阶段的缓存失效）
PROMPT_VERSION_FILES = {
    "entity_extraction": "entity_prompts.py",
    "event_type": "event_prompts.py",
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

    Added 2026-09-25（第 11 轮 A-1）：给"每次运行一个目录"的产物命名用，
    与 ``current_timestamp`` 共用同一个时区，免得目录名与 metadata 差几小时。
    """
    return datetime.now(_TZ_SINGAPORE).strftime("%Y%m%d_%H%M%S")


def cache_context(model_name: str, stage: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                  overlap: int = DEFAULT_OVERLAP) -> dict:
    """
    Added 2026-04-20 16:33:36 +08:00: Version cache keys by model, prompt,
    extraction stage, and splitter settings to avoid stale DeepSeek results.
    """
    return {
        "model_name": model_name,
        "prompt_version": PROMPT_VERSION,
        "extraction_version": EXTRACTION_VERSION,
        "config_version": CONFIG_VERSION,
        "stage": stage,
        "chunk_size": chunk_size,
        "overlap": overlap,
    }
