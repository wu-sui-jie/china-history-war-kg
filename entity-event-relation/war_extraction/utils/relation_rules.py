"""
事件-事件关系仲裁。

EER-6：这段规则此前在 `main.py`（自由函数）与 `war_extraction/extractors/relation_extractor.py`
（同名方法）里各一份，逐字相同——改一处忘一处就会让"抽出来的关系和清理后的关系"对不上。
"""
from __future__ import annotations

__all__ = [
    "SEQUENTIAL_KEYWORDS",
    "STRONG_CAUSAL_KEYWORDS",
    "PARALLEL_KEYWORDS",
    "arbitrate_event_event_relation",
]

SEQUENTIAL_KEYWORDS = ("之后", "以后", "随后", "其后", "后来", "次年", "继而", "接着", "然后", "遂")
STRONG_CAUSAL_KEYWORDS = ("因此", "于是", "导致", "引发", "致使", "使得", "造成", "因而", "从而", "迫使")
PARALLEL_KEYWORDS = ("同时", "并", "并且", "同年", "相继", "并发")


def arbitrate_event_event_relation(normalizer, rel):
    """
    按证据文本里的关键词，修正事件-事件关系的类型（就地改 ``rel.relation`` 并返回同一个对象）。

    规则：模型说是"因果关系"、但证据里没有强因果词时，降级为"顺承关系"（有顺承词或
    证据文本非空）或"并列关系"；说是"顺承关系"、但证据里有并列词且没有顺承词时，
    改判"并列关系"。
    """
    evidence = getattr(rel, "evidence", None) or ""
    relation = normalizer.normalize_relation(getattr(rel, "relation", None))

    has_sequential_keyword = any(token in evidence for token in SEQUENTIAL_KEYWORDS)
    has_strong_causal_keyword = any(token in evidence for token in STRONG_CAUSAL_KEYWORDS)
    has_parallel_keyword = any(token in evidence for token in PARALLEL_KEYWORDS)

    if relation == "因果关系" and not has_strong_causal_keyword:
        relation = "顺承关系" if has_sequential_keyword or evidence else "并列关系"
    elif relation == "顺承关系" and has_parallel_keyword and not has_sequential_keyword:
        relation = "并列关系"

    rel.relation = relation
    return rel
