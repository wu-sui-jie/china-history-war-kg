"""F02 问题理解对外入口（server/query/__init__.py）。

load_understanding(snapshot_dir, settings) → QuestionUnderstanding（含词典匹配器）。
understand(...) 见 understand.py。
"""

from __future__ import annotations

from pathlib import Path

from server.query.understand import QuestionUnderstanding

__all__ = ["QuestionUnderstanding"]


def load_understanding(snapshot_dir: Path,
                       llm_client=None,
                       enable_llm: bool = False,
                       history_max_turns: int = 4) -> QuestionUnderstanding:
    from server.query.dictionary_matcher import DictionaryMatcher, load_jieba
    load_jieba(snapshot_dir)  # 实体名入 jieba，保证分词整名命中
    matcher = DictionaryMatcher(snapshot_dir)
    return QuestionUnderstanding(
        matcher=matcher,
        llm_client=llm_client,
        enable_llm=enable_llm,
        history_max_turns=history_max_turns,
    )
