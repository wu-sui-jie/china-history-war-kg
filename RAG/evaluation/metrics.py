"""客观指标与失败归因（evaluation/metrics.py）。

指标定义（均基于题库 expected_entities 中的标注词，词在文本中以子串出现即视为覆盖；
词可短于实体标准名，如"牧野之战"⊂"周武王灭商牧野之战"）：

- entity_matched[i]：F02 识别出的实体 mention/标准名 与标注词完全相等
  （说明系统确实定位到了该实体）；
- graph_hit[i]：图谱检索结果（triple 两端或 hit_entities 名）包含标注词；
- text_topk[i]：文本检索 top-k 证据的正文包含标注词（相关文本是否被召回）；
- fused[i]：融合后（图谱+文本）保留的证据包含标注词（是否通过融合/裁剪）；
- fused_text[i]：融合保留的"文本类"证据包含标注词
  （对应专项口径"相关原文是否进入最终证据"）；
- cite_text[i]：引用列表里非图谱类引用 snippet 含标注词
  （相关文本是否进入引用）；
- answer[i]：最终回答文本包含标注词（相关文本是否进入回答）。

自动归因 verdict（report 结合 answerable 综合；答案/引用正确性仍以人工评分为准）：
ok / retrieval_fail(检索失败) / fusion_cut(召回被排序或裁剪挤出) /
answer_miss(回答未引用相关文本) / refused(无证据拒答) / correct_refusal /
should_refuse_answered(应拒答却作答) / no_expected(题库未标注词，指标不适用)
"""

from __future__ import annotations

from typing import Optional

from contracts.evidence import Evidence


def _text_of(ev: Evidence) -> str:
    return (ev.content or {}).get("text") or ""


def _kind_of(ev) -> str:
    kind = getattr(ev, "kind", None)
    return kind.value if hasattr(kind, "value") else str(kind)


def _is_graph(ev) -> bool:
    return _kind_of(ev) == "graph_triple"


def _contains(text: str, word: str) -> bool:
    return bool(word and word in text)


def _entity_name_set(entity_objs) -> set[str]:
    out = set()
    for e in entity_objs:
        for key in ("standard_name", "name"):
            v = getattr(e, key, None)
            if v:
                out.add(v)
    return out


def compute_metrics(*, expected_names: list[str],
                    entity_objs=None,
                    graph_evidence=None, hit_entity_names=None,
                    text_evidence=None, fused_evidence=None,
                    citations=None, answer_text: str = "",
                    answerable: Optional[bool] = None,
                    refusal: Optional[dict] = None) -> dict:
    """对一条 trace 计算覆盖指标。

    参数对象保留运行时类型（Evidence/EntityRef），由 chain.run_question 在
    内存中调用本函数，避免 trace JSON 丢失全文后再做统计。

    refusal 传入时 answer 覆盖清零：拒答文案只是机械复述问句（含标注词），
    不代表真正给出回答，避免把拒答误判为"回答覆盖通过"。
    """
    names = [w for w in (expected_names or []) if w]
    total = len(names)
    if total == 0:
        return {
            "expected_total": 0, "coverage": {}, "counts": {},
            "verdict": "no_expected", "refused": bool(refusal),
        }
    refused = bool(refusal)

    graph_ev = list(graph_evidence or [])
    text_ev = list(text_evidence or [])
    fused_ev = list(fused_evidence or [])
    citations = citations or []

    matched = _entity_name_set(entity_objs or [])
    graph_haystack = " ".join(
        [str(h) for h in (hit_entity_names or [])]
        + [f"{c.get('subject','')} {c.get('object','')}" for c in
           [e.content or {} for e in graph_ev]]
    )
    text_topk_hay = "\n".join(_text_of(ev) for ev in text_ev)
    fused_text_hay = "\n".join(
        _text_of(ev) for ev in fused_ev if not _is_graph(ev)
    )
    fused_all_hay = "\n".join(
        (_text_of(ev) if not _is_graph(ev)
         else f"{ev.content.get('subject','')} {ev.content.get('object','')}")
        for ev in fused_ev
    )
    cite_text_hay = "\n".join(
        (c.get("snippet") or "") for c in citations
        if c.get("kind") != "graph_triple"
    )

    entity_matched, graph_hit, text_topk = [], [], []
    fused, fused_text, cite_text, answer = [], [], [], []
    for w in names:
        entity_matched.append(w in matched)
        graph_hit.append(_contains(graph_haystack, w))
        text_topk.append(_contains(text_topk_hay, w))
        fused.append(_contains(fused_all_hay, w))
        fused_text.append(_contains(fused_text_hay, w))
        cite_text.append(_contains(cite_text_hay, w))
        answer.append(False if refused else _contains(answer_text, w))

    def _cnt(arr) -> int:
        return sum(1 for b in arr if b)

    coverage = {
        "names": names,
        "entity_matched": entity_matched,
        "graph_hit": graph_hit,
        "text_topk": text_topk,
        "fused": fused,
        "fused_text": fused_text,
        "cite_text": cite_text,
        "answer": answer,
    }
    counts = {
        "entity_matched": _cnt(entity_matched),
        "graph_hit": _cnt(graph_hit),
        "text_topk": _cnt(text_topk),
        "fused": _cnt(fused),
        "fused_text": _cnt(fused_text),
        "cite_text": _cnt(cite_text),
        "answer": _cnt(answer),
    }
    return {
        "expected_total": total,
        "coverage": coverage,
        "counts": counts,
        "answerable": answerable,
        "refused": refused,
    }


def classify(auto: dict, answerable: bool,
             refusal: Optional[dict] = None) -> str:
    """把一条 trace 的指标与拒答结果归到失败类别（verdict）。

    与 F10 验收第 2 条"报告能区分答案错/引用错/检索失败/无证据"的映射：
    - refused / correct_refusal → 无证据（拒答，是否正确由 answerable 决定）；
    - retrieval_fail → 检索失败（相关实体与文本都未召回）；
    - fusion_cut → 检索到相关文本但在融合/裁剪被挤出（引用错的高发点）；
    - answer_miss → 证据携带但回答未引用（引用错/答案错候选）；
    - 其余 ok → 再由人工评分判定答案/引用正确性。
    """
    if refusal:
        return "correct_refusal" if not answerable else "refused"
    if not answerable:
        return "should_refuse_answered"
    if auto.get("expected_total", 0) == 0:
        return "no_expected"
    counts = auto.get("counts", {})
    if counts.get("answer", 0) > 0:
        return "ok"
    if counts.get("text_topk", 0) == 0 and counts.get("graph_hit", 0) == 0 \
            and counts.get("entity_matched", 0) == 0:
        return "retrieval_fail"
    if counts.get("text_topk", 0) > 0 and counts.get("fused_text", 0) == 0:
        return "fusion_cut"
    return "answer_miss"


VERDICT_LABEL = {
    "ok": "覆盖通过（待人工评分）",
    "retrieval_fail": "检索失败：相关实体与文本均未召回",
    "fusion_cut": "相关文本被排序/裁剪挤出融合证据",
    "answer_miss": "回答未引用相关文本",
    "refused": "无证据拒答（应可答）",
    "correct_refusal": "正确拒答",
    "should_refuse_answered": "应拒答却作答",
    "no_expected": "题库未标注词，指标不适用",
}
