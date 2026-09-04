"""F06 拒答判定与文案（server/generate/refusal.py）。

拒答路径（features/06 初版规则）：
1. 图谱证据为空 且 文本证据为空 → 直接拒答（finish_reason=refused）。
2. 有证据但模型判断不足 → 由 LLM 说明依据不足（仍 normal，但文案说明）。
3. score 不作为拒答阈值。
"""

from __future__ import annotations

from contracts.evidence import Evidence


def has_any_evidence(evidence: list[Evidence]) -> bool:
    return bool(evidence)


def refusal_reply(question: str) -> str:
    return (
        f"抱歉，关于「{question}」，当前知识库中没有检索到足够的相关史料，"
        "无法给出有依据的回答。可以换个问法，或补充朝代/战争类型筛选条件后重试。"
    )
