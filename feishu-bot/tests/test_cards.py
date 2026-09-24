"""卡片组装的守护用例（开发文档 10.1 的 `cards` 行，P0 起）。

锁住四件事：2.0 骨架、引用折叠区、按钮 value 含 action 与 msg_key、truncated 提示行。
这些只要坏一处，用户看到的就是"卡片渲染错乱"或"按钮点了没反应"。
"""

from __future__ import annotations

import json

from bot.cards.builder import (CARD_TITLE, CONFLICT_WARNING, TRUNCATED_NOTICE,
                               build_answer_card, build_answer_reply, build_degraded_card,
                               build_feedback_ticket_card, build_notice_card,
                               build_too_long_card)


def _elements(card: dict) -> list[dict]:
    return card["body"]["elements"]


def _folds(card: dict) -> list[dict]:
    return [e for e in _elements(card) if e.get("tag") == "collapsible_panel"]


def _panels_by_title(card: dict) -> dict[str, dict]:
    return {f["header"]["title"]["content"]: f for f in _folds(card)}


def _markdown_text(card: dict) -> str:
    return "\n".join(e.get("content", "") for e in _elements(card) if e.get("tag") == "markdown")


# ---- 1) 2.0 骨架 ----


def test_card_uses_schema_2_0_with_body_elements():
    card = build_answer_card(answer_md="正文")
    assert card["schema"] == "2.0"
    assert isinstance(card["body"]["elements"], list)
    assert card["header"]["title"]["content"] == CARD_TITLE
    assert "elements" not in card          # 不能混用 1.0 骨架
    json.dumps(card, ensure_ascii=False)   # 必须可序列化（content 是 JSON 字符串）


def test_answer_body_is_sanitized():
    card = build_answer_card(answer_md="# 标题\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n![图](http://a/b.png)")
    body = _elements(card)[0]["content"]
    assert body.startswith("**标题**")
    assert "**甲** · **乙**" in body
    assert "![图]" not in body
    assert "# 标题" not in body


def test_empty_answer_does_not_produce_empty_card():
    card = build_answer_card(answer_md="")
    assert _elements(card)[0]["content"] == "（没有可显示的内容）"


# ---- 2) 引用折叠区 ----


def test_citations_go_into_collapsible_panel():
    card = build_answer_card(answer_md="正文", citations=[
        {"index": 1, "kind": "graph_triple", "title": "长平之战—主战场→长平", "snippet": ""},
        {"index": 2, "kind": "event_card", "title": "白起攻韩上党之战",
         "snippet": "类型：开疆拓土\n朝代：战国"},
    ])
    folds = _panels_by_title(card)
    assert "引用（2）" in folds
    content = folds["引用（2）"]["elements"][0]["content"]
    assert "**[1] 长平之战—主战场→长平**（graph_triple）" in content
    assert "**[2] 白起攻韩上党之战**（event_card）" in content
    # 片段用引用行承载，多行片段逐行加 >
    assert "> 类型：开疆拓土" in content
    assert "> 朝代：战国" in content
    # 默认收起：正文才是主体
    assert folds["引用（2）"]["expanded"] is False


def test_conflicts_append_warning_line_only():
    card = build_answer_card(answer_md="正文", citations=[{"index": 1, "title": "t", "kind": "k"}],
                             conflicts=[{"subject": "长平之战", "field": "统帅",
                                         "conflict_type": "different_object"}])
    content = _panels_by_title(card)["引用（1）"]["elements"][0]["content"]
    assert CONFLICT_WARNING in content
    assert "different_object" not in content      # 不展开细节


def test_no_citations_no_panel():
    card = build_answer_card(answer_md="正文")
    assert not _folds(card)


def test_citation_snippet_is_truncated_but_not_rewritten():
    long_snippet = "甲" * 500
    card = build_answer_card(answer_md="x", citations=[
        {"index": 1, "kind": "raw_text", "title": "t", "snippet": long_snippet}])
    content = _panels_by_title(card)["引用（1）"]["elements"][0]["content"]
    assert "甲" * 400 in content
    assert "甲" * 401 not in content


# ---- 3) panel 文本化（P1-2）----


def test_entity_cards_are_textified():
    card = build_answer_card(answer_md="正文", panel={"entity_cards": [{
        "entity_id": "e1", "type": "事件", "name": "长平之战", "dynasty": "战国",
        "start_date": "前262年", "end_date": "前260年", "aggressor": "秦军",
        "defender": "赵军", "action": "围攻", "impact": "赵军大败", "place": "长平",
        "description": "战国后期规模最大的一次战役",
    }]})
    content = _panels_by_title(card)["相关实体（1）"]["elements"][0]["content"]
    assert "**长平之战**（事件｜战国｜前262年–前260年）" in content
    assert "发起方：秦军" in content and "防守方：赵军" in content
    assert "影响：赵军大败" in content


def test_entity_card_dates_collapse_when_same_or_missing():
    card = build_answer_card(answer_md="x", panel={"entity_cards": [
        {"name": "人物甲", "type": "人物", "start_date": "前100年"},
        {"name": "人物乙", "type": "人物", "start_date": "前100年", "end_date": "前100年"},
    ]})
    content = _panels_by_title(card)["相关实体（2）"]["elements"][0]["content"]
    assert "**人物甲**（人物｜前100年）" in content
    assert "**人物乙**（人物｜前100年）" in content


def test_timeline_is_textified():
    card = build_answer_card(answer_md="x", panel={"timeline": {"groups": [
        {"label": "战国", "items": [
            {"event_id": "e1", "name": "长平之战", "start_date": "前260年"},
            {"event_id": "e2", "name": "邯郸之战", "start_date": "前259年"}]},
        {"label": "时间不详", "items": [{"event_id": "e3", "name": "某战"}]},
    ]}})
    content = _panels_by_title(card)["时间线"]["elements"][0]["content"]
    assert "**战国**：长平之战（前260年） → 邯郸之战（前259年）" in content
    assert "**时间不详**：某战" in content


def test_places_use_fallback_format():
    card = build_answer_card(answer_md="x", panel={"map_points": [
        {"place_id": "p1", "name": "长平", "modern_name": "山西高平"},
        {"place_id": "p2", "name": "河内"},
    ]})
    content = _panels_by_title(card)["相关地点"]["elements"][0]["content"]
    assert "- 长平（山西高平）" in content
    assert "- 河内" in content


# ---- 4) 子图：图片优先、文字降级（P2-1）----


def test_subgraph_renders_image_when_img_key_present():
    card = build_answer_card(answer_md="x", subgraph_img_key="img_key_1", panel={
        "subgraph": {"nodes": [{"id": "n1", "type": "事件", "name": "长平之战"}], "edges": []}})
    images = [e for e in _elements(card) if e.get("tag") == "img"]
    assert images and images[0]["img_key"] == "img_key_1"


def test_subgraph_falls_back_to_text_without_img_key():
    card = build_answer_card(answer_md="x", panel={"subgraph": {
        "nodes": [{"id": "n1", "type": "事件", "name": "长平之战"},
                  {"id": "n2", "type": "地点", "name": "长平"}],
        "edges": [{"source": "n1", "target": "n2", "relation": "主战场"}]}})
    assert not [e for e in _elements(card) if e.get("tag") == "img"]
    content = _panels_by_title(card)["关系图（文字版）"]["elements"][0]["content"]
    assert "长平之战（事件）" in content
    # 边用可读的节点名 + 关系，不暴露内部 id
    assert "- 长平之战 —主战场→ 长平" in content
    assert "n1" not in content


def test_subgraph_text_caps_are_applied():
    nodes = [{"id": f"n{i}", "type": "事件", "name": f"事件{i}"} for i in range(40)]
    edges = [{"source": f"n{i}", "target": f"n{(i + 1) % 40}", "relation": "相关"}
             for i in range(60)]
    card = build_answer_card(answer_md="x", panel={"subgraph": {"nodes": nodes, "edges": edges}})
    content = _panels_by_title(card)["关系图（文字版）"]["elements"][0]["content"]
    # 节点 ≤ 30、边 ≤ 50，超出给"……等 N 项"（按字段裁剪的落地）
    assert content.count("事件1（事件）") or "事件1（事件）" in content
    assert "……等 40 个节点" in content
    assert "……等 60 条关系" in content
    assert content.count("—相关→") == 50


def test_empty_subgraph_produces_no_section():
    card = build_answer_card(answer_md="x", panel={"subgraph": {"nodes": [], "edges": []}})
    assert not _folds(card)


# ---- 5) 按钮（P1-3 / P2-2）----


def _buttons(card: dict) -> list[dict]:
    return [e for e in _elements(card) if e.get("tag") == "button"]


def _btn_value(button: dict) -> dict:
    """取按钮回传参数：2.0 里在 behaviors[type=callback].value（不是顶层 value）。"""
    for behavior in button.get("behaviors") or []:
        if behavior.get("type") == "callback":
            return behavior.get("value") or {}
    return {}


def test_example_buttons_carry_ask_action():
    card = build_answer_card(answer_md="x", examples=["介绍一下长平之战", "白起是谁"])
    values = [_btn_value(b) for b in _buttons(card)]
    assert {"action": "ask", "question": "介绍一下长平之战"} in values
    assert len(values) == 2


def test_example_buttons_limited_to_three():
    card = build_answer_card(answer_md="x", examples=[f"问题{i}" for i in range(6)])
    assert len(_buttons(card)) == 3


def test_feedback_button_carries_msg_key():
    card = build_answer_card(answer_md="x", msg_key=17)
    assert {"action": "report_error", "msg_key": "17"} in [_btn_value(b) for b in _buttons(card)]


def test_no_msg_key_no_feedback_button():
    card = build_answer_card(answer_md="x")
    assert _buttons(card) == []


def test_buttons_use_plain_text_labels():
    card = build_answer_card(answer_md="x", msg_key=1, examples=["问"])
    for button in _buttons(card):
        assert button["text"]["tag"] == "plain_text"
        assert button["text"]["content"]


def test_button_label_capped_at_100_chars():
    """2.0 里 button.text.content 上限 100 字符（超了会被拒收）。"""
    card = build_answer_card(answer_md="x", examples=["甲" * 200])
    assert all(len(b["text"]["content"]) <= 100 for b in _buttons(card))


def test_attach_feedback_button_is_idempotent():
    from bot.cards.builder import attach_feedback_button

    card = build_answer_card(answer_md="x", msg_key=5)
    before = len(_buttons(card))
    attach_feedback_button(card, 5)          # 已经有一个反馈按钮
    assert len(_buttons(card)) == before


# ---- 5b) 卡片 2.0 合规守卫 ----
#
# 这一组是**真机教训**固化的：首版卡片混用了 1.0 写法，被飞书整卡拒收
# （code 200621 `unknown property, property: type, path: ... -> border`）。
# 飞书没有离线校验接口，所以把已知的 2.0 规则写成本地断言——再犯就是在测试里挂掉，
# 而不是等用户点开卡片才发现。

_ALLOWED_CONFIG_KEYS = {"update_multi", "width_mode", "streaming_mode", "enable_forward",
                        "enable_forward_interaction", "style", "summary", "locales"}
_ALLOWED_BORDER_KEYS = {"color", "corner_radius"}
_ALLOWED_TAGS = {"markdown", "hr", "img", "button", "collapsible_panel"}


def _walk_elements(card: dict) -> list[dict]:
    out: list[dict] = []
    stack = list(_elements(card))
    while stack:
        element = stack.pop()
        out.append(element)
        stack.extend(element.get("elements") or [])       # 折叠面板内的子元素
    return out


def _all_cards() -> dict[str, dict]:
    from bot.cards.builder import (build_degraded_card, build_feedback_ticket_card,
                                   build_notice_card, build_placeholder_card,
                                   build_too_long_card)

    return {
        "answer": build_answer_card(answer_md="# 标题", citations=[{"index": 1, "title": "t",
                                                                   "kind": "k"}],
                                   conflicts=[{"subject": "s"}],
                                   panel={"entity_cards": [{"name": "n", "type": "事件"}],
                                          "timeline": {"groups": [{"label": "d", "items": [
                                              {"name": "e", "start_date": "前1年"}]}]},
                                          "map_points": [{"name": "p"}],
                                          "subgraph": {"nodes": [{"id": "a", "name": "n",
                                                                  "type": "事件"}],
                                                       "edges": []}},
                                   truncated=True, msg_key=3, examples=["问一句"],
                                   subgraph_img_key="img_x"),
        "degraded": build_degraded_card("timeout"),
        "too_long": build_too_long_card(),
        "notice": build_notice_card("提示"),
        "placeholder": build_placeholder_card(),
        "ticket": build_feedback_ticket_card(feedback_id=1, open_id="ou", question="q",
                                             answer_md="a", citations=[{"title": "t"}]),
    }


def test_no_legacy_1_0_action_container():
    """2.0 已删除交互模块（tag=action）：按钮必须是 elements 的直接成员。"""
    for name, card in _all_cards().items():
        tags = [e.get("tag") for e in _walk_elements(card)]
        assert "action" not in tags, f"{name}: 仍在使用 1.0 的 action 容器"


def test_config_only_uses_2_0_keys():
    """config 里不能出现 1.0 字段（wide_screen_mode 等）。"""
    for name, card in _all_cards().items():
        unknown = set(card.get("config") or {}) - _ALLOWED_CONFIG_KEYS
        assert not unknown, f"{name}: config 含 2.0 不支持的字段 {sorted(unknown)}"
        assert card["config"].get("update_multi") is True


def test_buttons_use_behaviors_not_top_level_value():
    for name, card in _all_cards().items():
        for button in [e for e in _walk_elements(card) if e.get("tag") == "button"]:
            assert "value" not in button, f"{name}: 按钮用了 1.0 的顶层 value"
            behaviors = button.get("behaviors") or []
            assert behaviors and behaviors[0]["type"] == "callback", f"{name}: 缺 behaviors"
            assert isinstance(behaviors[0].get("value"), dict)


def test_collapsible_panel_border_only_color_and_radius():
    for name, card in _all_cards().items():
        for panel in [e for e in _walk_elements(card) if e.get("tag") == "collapsible_panel"]:
            unknown = set(panel.get("border") or {}) - _ALLOWED_BORDER_KEYS
            assert not unknown, f"{name}: border 含非法字段 {sorted(unknown)}"
            assert panel["header"]["title"]["tag"] == "plain_text"


def test_image_element_shape():
    """img 的 alt 是必填，且只支持 plain_text。"""
    card = _all_cards()["answer"]
    images = [e for e in _walk_elements(card) if e.get("tag") == "img"]
    assert images
    for image in images:
        assert image["img_key"]
        assert image["alt"]["tag"] == "plain_text"


def test_only_known_component_tags():
    """只使用已核实的 2.0 组件 tag（新增组件时同步 _ALLOWED_TAGS）。"""
    for name, card in _all_cards().items():
        unknown = {e.get("tag") for e in _walk_elements(card)} - _ALLOWED_TAGS
        assert not unknown, f"{name}: 出现未核实的组件 tag {sorted(unknown)}"


def test_elements_within_platform_limits():
    """一张卡片最多 200 个元素；markdown 内容非空。"""
    for name, card in _all_cards().items():
        elements = _walk_elements(card)
        assert len(elements) <= 200, f"{name}: 元素数超限"
        for element in elements:
            if element.get("tag") == "markdown":
                assert element.get("content") is not None


# ---- 6) truncated 提示 ----


def test_truncated_notice_appended():
    card = build_answer_card(answer_md="正文", truncated=True)
    assert TRUNCATED_NOTICE in _elements(card)[0]["content"]


def test_not_truncated_no_notice():
    card = build_answer_card(answer_md="正文")
    assert TRUNCATED_NOTICE not in _elements(card)[0]["content"]


# ---- 7) 降级卡片与终态门禁 ----


def test_degraded_card_texts():
    assert "超时" in _markdown_text(build_degraded_card("timeout"))
    assert "服务暂不可用" in _markdown_text(build_degraded_card("unavailable"))
    assert "服务异常" in _markdown_text(build_degraded_card("internal"))
    # 未知原因按内部异常处理，不泄漏内部细节
    text = _markdown_text(build_degraded_card("weird_code"))
    assert "服务异常" in text and "weird_code" not in text


def test_too_long_card_is_the_only_place_saying_so():
    assert "问题过长" in _markdown_text(build_too_long_card())
    for reason in ("timeout", "unavailable", "internal"):
        assert "问题过长" not in _markdown_text(build_degraded_card(reason))


def test_answer_reply_rejects_abnormal_finish_reason():
    """契约外终态（如 interrupted）不得当完整答案发出去。"""
    card, counters = build_answer_reply("半截回答", finish_reason="interrupted")
    assert "答不上来" in _markdown_text(card)
    assert counters.get("reason") == "interrupted"


def test_answer_reply_accepts_normal_refused_degraded():
    for reason in ("normal", "refused", "degraded"):
        card, _ = build_answer_reply("正文", finish_reason=reason)
        assert "正文" in _markdown_text(card)


def test_refused_card_has_no_citation_panel():
    card, _ = build_answer_reply("知识库未检索到相关史料", finish_reason="refused")
    assert not _folds(card)


# ---- 8) 工单卡片与提示卡片 ----


def test_feedback_ticket_card_contents():
    card = build_feedback_ticket_card(
        feedback_id=3, open_id="ou_x", question="长平之战是谁打的",
        answer_md="# 长平之战\n\n" + "甲" * 900,
        citations=[{"index": 1, "title": "长平之战"}], created_at="2026-09-21 10:00:00")
    assert card["header"]["title"]["content"] == "纠错反馈 #3"
    text = _markdown_text(card)
    assert "ou_x" in text and "长平之战是谁打的" in text
    assert "**长平之战**" in text                    # 回答摘要经过收敛（标题变加粗）
    assert len(text) < 1200                        # 摘要有上限，不是整篇转发
    assert "2026-09-21 10:00:00" in text


def test_notice_card_is_minimal():
    card = build_notice_card("暂只支持文字提问")
    assert _markdown_text(card) == "暂只支持文字提问"
    assert card["header"]["template"] == "grey"


def test_placeholder_card_is_grey_and_replaceable():
    """占位卡（批次③-1）：灰色（不是答案）、有 update_multi（PATCH 的前提）。"""
    from bot.cards.builder import PLACEHOLDER_TEXT, build_placeholder_card

    card = build_placeholder_card()
    assert "正在检索" in _markdown_text(card)
    assert card["header"]["template"] == "grey", "占位卡不该看起来像答案"
    assert card["config"]["update_multi"] is True, "整卡替换要求 update_multi"
    assert [_markdown_text(build_placeholder_card())] == [PLACEHOLDER_TEXT]
