"""F06 拒答判定与文案（server/generate/refusal.py）。

拒答路径（features/06 初版规则 + RAGv5 补强）：
1. 图谱证据为空 且 文本证据为空 → 直接拒答（finish_reason=refused）。
2. 实体为空 且 文本证据与问题无共享词 → 依据不足拒答（sse.py / evaluation 两处同口径）。
3. **（RAGv5 §4.6 新增）领域外谓词**：问题问的是知识库不可能覆盖的属性/器物
   （邮箱、电话、度假、坦克、股票…），**且这些词在所有证据里都不出现** → 拒答。
   保守优先：只要证据里出现过该词，就不拒答（宁可少拒，不可把可答题拒掉）。
4. score 不作为拒答阈值（向量模式下的分数阈值另见 VECTOR_REFUSAL_MIN_SCORE）。
"""

from __future__ import annotations

from contracts.evidence import Evidence

# 知识库不可能覆盖的现代/虚构属性与器物（词表保守、可扩展；仅在证据里完全没有该词时才拒答）
OUT_OF_SCOPE_PREDICATES: tuple[str, ...] = (
    # 现代联系方式/身份信息
    "邮箱", "email", "E-mail", "电话", "手机", "微信", "QQ", "身份证", "护照", "银行卡",
    "密码", "账号", "微博", "抖音", "朋友圈", "短信", "快递",
    # 现代生活/商业
    "工资", "薪资", "月薪", "年薪", "股票", "基金", "房价", "票房", "收视率", "代言",
    "粉丝", "综艺", "演唱会", "游戏", "电竞", "直播", "网红", "度假", "旅游", "机票", "酒店",
    # 现代科技器物（古代战争史不涉及）
    "坦克", "飞机", "航母", "潜艇", "导弹", "火车", "汽车", "高铁", "手机信号", "卫星",
    # 个人身体/隐私信息
    "身高", "体重", "生日", "星座", "血型", "爱好", "性格测试",
)


def has_any_evidence(evidence: list[Evidence]) -> bool:
    return bool(evidence)


def refusal_reply(question: str) -> str:
    return (
        f"抱歉，关于「{question}」，当前知识库中没有检索到足够的相关史料，"
        "无法给出有依据的回答。可以换个问法，或补充朝代/战争类型筛选条件后重试。"
    )


def _evidence_haystack(evidence: list[Evidence]) -> str:
    """把证据拼成一段用于"是否覆盖该词"判定的文本（含图谱三元组与文本正文）。"""
    parts: list[str] = []
    for ev in evidence or []:
        content = ev.content or {}
        if content.get("text"):
            parts.append(str(content["text"]))
        for key in ("subject", "relation", "object", "event_name"):
            if content.get(key):
                parts.append(str(content[key]))
    return "\n".join(parts)


def out_of_scope_reason(question: str, evidence: list[Evidence]) -> str | None:
    """领域外谓词规则：命中词表且证据里完全没有该词 → 返回拒答文案；否则 None。

    设计要点（RAGv5 §4.6）：只做"确定不该答"的判定，命中不确定时一律不拒答，
    避免把可通过史料回答的问题误拒。
    """
    if not question or not evidence:
        return None
    hits = [w for w in OUT_OF_SCOPE_PREDICATES if w in question]
    if not hits:
        return None
    haystack = _evidence_haystack(evidence)
    if any(w in haystack for w in hits):
        return None      # 证据里确实提到过 → 交给正常回答流程
    return (
        f"「{question}」问到的信息（{'、'.join(hits)}）不在本知识库的收录范围内"
        "（中国历代战争史史料：战争、人物、地点、事件与关系），无法给出有依据的回答。"
        "可以改为询问相关战役的经过、结果、参战人物或时间地点。"
    )
