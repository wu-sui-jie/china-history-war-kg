"""
Extraction configuration shared by prompts, cache, export, and evaluation.
"""

from datetime import datetime, timezone, timedelta


EXTRACTION_VERSION = "extraction-v2-20260420"
PROMPT_VERSION = "prompt-v2-20260420"
CONFIG_VERSION = "config-v1-20260420"

PROMPT_VERSIONS = {
    "entity_extraction": "entity-prompt-v2-20260420",
    "event_type": "event-type-prompt-v2-20260420",
    "event_identification": "event-identification-prompt-v2-20260420",
    "full_event": "full-event-prompt-v2-20260420",
    "relation_extraction": "relation-prompt-v2-20260420",
}

DEFAULT_CHUNK_SIZE = 1800
DEFAULT_OVERLAP = 200


def current_timestamp() -> str:
    """Return an Asia/Singapore timestamp for metadata and change logs."""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S +08:00")


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
