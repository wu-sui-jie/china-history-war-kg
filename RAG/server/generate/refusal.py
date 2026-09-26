"""F06 拒答判定与文案（server/generate/refusal.py）。

拒答路径（见 docs/features.md 第六节的拒答判定规则）：
1. 图谱证据为空 且 文本证据为空 → 直接拒答（finish_reason=refused）。
2. 实体为空 且 文本证据与问题无共享词 → 依据不足拒答（sse.py / evaluation 两处同口径）。
3. **领域外谓词**：问题问的是知识库不可能覆盖的属性/器物
   （邮箱、电话、度假、坦克、股票…），**且这些词在所有证据里都不出现** → 拒答。
   保守优先：只要证据里出现过该词，就不拒答（宁可少拒，不可把可答题拒掉）。
4. score 不单独作为拒答阈值；向量/hybrid 模式下作为低分兜底信号之一，与规则 2
   叠加生效（VECTOR_REFUSAL_MIN_SCORE 在 sse.py 的编排里读取）。
5. **模型自拒识别**：模型按提示词规则在
   证据不足时用固定句式说明（见 prompts.py 规则 4），生成返回前经
   detect_model_refusal 识别 → finish_reason=refused，前端据此标记"依据不足"。
"""

from __future__ import annotations

import re

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


# 模型自拒文本的识别句式：提示词要求模型在证据不足时使用「依据现有资料无法确认」
# （见 prompts.py 规则 4），以下为实测常见等价表述。只收录语义明确的自拒短语，
# 不收录"无法回答"这类单独出现的短语，避免把正常回答误判为拒答。
MODEL_REFUSAL_PATTERNS: tuple[str, ...] = (
    # 提示词规则 4 要求的固定句式及其变体
    "依据现有资料无法", "根据现有资料无法", "现有资料无法确认",
    "依据现有史料无法", "根据现有史料无法",
    # 常见等价自拒表述
    "现有资料不足以", "现有史料不足以",
    "无法给出有依据的回答", "无法提供有依据的回答",
    "资料中没有相关记载", "史料中没有相关记载",
    "未检索到相关", "没有检索到相关",
)

# 拒答句式只在正文前 N 字内判定：模型按提示词应在开头说明依据不足；
# 正文中后段出现的"某细节无法确认"属于局部不确定，不应整体判为拒答。
MODEL_REFUSAL_HEAD_CHARS = 200

# 引用编号（如 [1][2]）：模型被要求对引用处标注编号；自拒回答没有可支撑的引用。
_CITATION_RE = re.compile(r"\[\d+\]")


def detect_model_refusal(text: str) -> bool:
    """识别模型自拒文本：正文前 200 字内命中固定拒答句式，且全文不含引用编号。

    要求"不含引用编号"的原因：像"依据现有资料无法确认其出生年份，但据 [1] 他参与了
    长平之战"这类**局部不确定**的正常回答带引用，不应整体判为拒答；
    宁可漏判（保持 normal），不误判可用回答。
    """
    if not text:
        return False
    if not any(p in text[:MODEL_REFUSAL_HEAD_CHARS] for p in MODEL_REFUSAL_PATTERNS):
        return False
    return not _CITATION_RE.search(text)


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

    设计要点：只做"确定不该答"的判定，命中不确定时一律不拒答，
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
