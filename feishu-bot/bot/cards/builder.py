"""RAG 输出 → 飞书卡片（开发文档 5.5.2 / 5.5.3）。

**统一按卡片 2.0 规范**（`schema: "2.0"` + `body.elements`）：折叠面板等容器组件
只存在于 2.0，P0 就按 2.0 起手，避免 P1 时整体迁移（开发文档 5.5.2）。

裁剪纪律（需求文档 7/Q8）：panel/citations 一律**按字段取用**，不整表透传——
图谱侧的 `LIMIT 200` 是"看图护栏"，不是"给机器人的数据上限"。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from bot.cards.md_sanitizer import MAX_CHARS, sanitize, sanitize_with_counters

# ---- 文案（对外可见，改这里即可）----
CARD_TITLE = "战争史问答"
TRUNCATED_NOTICE = "（回答可能被截断，完整内容请在网页版查看）"
DEGRADED_TEXTS = {
    "timeout": "抱歉，这条我暂时答不上来（原因：超时），请稍后重试，或换个问法。",
    "unavailable": "抱歉，这条我暂时答不上来（原因：服务暂不可用），请稍后重试，或换个问法。",
    "internal": "抱歉，这条我暂时答不上来（原因：服务异常），请稍后重试，或换个问法。",
}
# 只有用户侧参数错误（问题超 500 字等）才说"问题过长"；
# 413 属于机器人侧历史组装问题，绝不能这样提示（开发文档 5.3 / 风险 8）。
TOO_LONG_TEXT = "问题过长或不支持，请精简后重试。"
NOT_TEXT_MESSAGE = "暂只支持文字提问，请把问题打成文字发我。"
# 两段式回复：知识问答要同步等 RAG（长回答实测 12–25s），
# 先回这张占位卡让用户确认"收到了、在查"，跑完再用整卡 PATCH 替换。
PLACEHOLDER_TEXT = ("正在检索史料，请稍候……\n\n"
                    "长回答通常需要 5–25 秒，这条卡片稍后会被完整回答替换。")
# 占位卡已送到用户眼前、但最终卡没能 PATCH 上去时的收尾文案：
# 不能让用户一直看着"正在检索…"（此时本轮回答按未送达处理，见 dispatcher）
SEND_FAILED_TEXT = "抱歉，这条回答没能发送成功，请再问一次。"

# ---- 文本化输出的条数上限（按字段裁剪的落地，超出加"……等 N 项"）----
MAX_ENTITY_CARDS = 10
MAX_DESCRIPTION_CHARS = 120
MAX_TIMELINE_ITEMS = 30
MAX_PLACES = 10
MAX_SUBGRAPH_NODES_TEXT = 30
MAX_SUBGRAPH_EDGES_TEXT = 50
MAX_CITATION_SNIPPET_CHARS = 400

CONFLICT_WARNING = "⚠ 信息存在冲突"


def _fold(title: str, elements: list[dict], *, expanded: bool = False) -> dict:
    """折叠面板（2.0 的 collapsible_panel）。

    默认收起：正文才是主体，引用/实体卡属于"想看再点"的证据层。

    `border` 必须是对象且**只**接受 `color` 与 `corner_radius`（官方文档）；
    早先按开发文档的骨架写成 `{"type": "default"}` 会被飞书拒收整张卡片：
    实测报 `unknown property, property: type, path: ... -> border`（code 200621）。
    """
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": "8px",
        "elements": elements,
    }


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _button(text: str, value: dict, *, button_type: str = "default") -> dict:
    """2.0 按钮。

    回传参数必须写在 `behaviors` 里（`{"type": "callback", "value": {...}}`）——
    顶层 `value` 只在 1.0 的字段表里，2.0 的 button 字段表没有它（实测按 1.0 写法会
    被飞书拒收整张卡片）。回调事件里仍从 `event.action.value` 取，与写法无关。
    另外 `text.content` 上限 100 字符。
    """
    return {"tag": "button", "text": {"tag": "plain_text", "content": text[:100]},
            "type": button_type,
            "behaviors": [{"type": "callback", "value": value}]}


def _button_value(button: dict) -> dict:
    """取按钮的回传参数（供 attach_feedback_button 判断按钮是否已存在）。"""
    for behavior in button.get("behaviors") or []:
        if isinstance(behavior, dict) and behavior.get("type") == "callback":
            value = behavior.get("value")
            return value if isinstance(value, dict) else {}
    return {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _describe_entity_card(card: dict) -> str:
    """实体卡 → 文字（开发文档 5.5.2 区表）。"""
    name = _clean(card.get("name")) or "（未命名）"
    meta = [_clean(card.get("type"))]
    if _clean(card.get("dynasty")):
        meta.append(_clean(card.get("dynasty")))
    start, end = _clean(card.get("start_date")), _clean(card.get("end_date"))
    if start or end:
        meta.append(f"{start}–{end}" if end and start != end else (start or end))
    if _clean(card.get("modern_name")):
        meta.append(f"今址：{_clean(card.get('modern_name'))}")
    head = f"**{name}**（{'｜'.join([m for m in meta if m])}）"

    lines = [head]
    # 事件的叙事字段（f 事件卡才有值）；只取存在的，不补空行
    for label, key in (("发起方", "aggressor"), ("防守方", "defender"),
                       ("经过", "action"), ("影响", "impact"), ("地点", "place")):
        value = _clean(card.get(key))
        if value:
            lines.append(f"{label}：{value}")
    description = _clean(card.get("description"))
    if description:
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "…"
        lines.append(f"简介：{description}")
    return "\n".join(lines)


def _render_timeline(timeline: Any) -> str:
    """时间线 → "label：事件（时间） → 事件（时间）"（开发文档 5.5.2 区表）。"""
    groups = (timeline or {}).get("groups") if isinstance(timeline, dict) else None
    if not isinstance(groups, list):
        return ""
    lines: list[str] = []
    total = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = group.get("items") or []
        parts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if total >= MAX_TIMELINE_ITEMS:
                break
            total += 1
            name = _clean(item.get("name")) or "（未命名）"
            date = _clean(item.get("start_date"))
            parts.append(f"{name}（{date}）" if date else name)
        if not parts:
            continue
        label = _clean(group.get("label")) or "时间不详"
        lines.append(f"**{label}**：{' → '.join(parts)}")
    if total >= MAX_TIMELINE_ITEMS:
        lines.append("……（更多事件请在网页版查看）")
    return "\n".join(lines)


def _render_places(map_points: Any) -> str:
    """地点列表：飞书侧不做地图，复用前端"无坐标→地点列表"的降级格式。"""
    if not isinstance(map_points, list):
        return ""
    lines: list[str] = []
    for point in map_points[:MAX_PLACES]:
        if not isinstance(point, dict):
            continue
        name = _clean(point.get("name")) or "（未命名）"
        modern = _clean(point.get("modern_name"))
        lines.append(f"- {name}（{modern}）" if modern else f"- {name}")
    if isinstance(map_points, list) and len(map_points) > MAX_PLACES:
        lines.append(f"……等 {len(map_points)} 处地点")
    return "\n".join(lines)


def _render_subgraph_text(subgraph: Any) -> str:
    """子图文字降级：节点/边按上限输出，超出加"……等 N 项"。

    这是**必经路径**：Node 缺失、渲染超时、空图都会走到这里（开发文档十二-2）。
    """
    if not isinstance(subgraph, dict):
        return ""
    nodes = [n for n in (subgraph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (subgraph.get("edges") or []) if isinstance(e, dict)]
    id_to_name = {_clean(n.get("id")): _clean(n.get("name")) for n in nodes}
    lines: list[str] = []

    node_names = [f"{_clean(n.get('name'))}（{_clean(n.get('type'))}）" for n in nodes]
    if node_names:
        header = f"**节点（{len(nodes)}）**：" if len(nodes) > MAX_SUBGRAPH_NODES_TEXT \
            else "**节点**："
        shown = node_names[:MAX_SUBGRAPH_NODES_TEXT]
        lines.append(header + " · ".join(shown))
        if len(node_names) > MAX_SUBGRAPH_NODES_TEXT:
            lines.append(f"……等 {len(nodes)} 个节点")

    if edges:
        lines.append(f"**关系（{len(edges)}）**：" if len(edges) > MAX_SUBGRAPH_EDGES_TEXT
                     else "**关系**：")
        for edge in edges[:MAX_SUBGRAPH_EDGES_TEXT]:
            source = id_to_name.get(_clean(edge.get("source")), _clean(edge.get("source")))
            target = id_to_name.get(_clean(edge.get("target")), _clean(edge.get("target")))
            relation = _clean(edge.get("relation")) or "相关"
            lines.append(f"- {source} —{relation}→ {target}")
        if len(edges) > MAX_SUBGRAPH_EDGES_TEXT:
            lines.append(f"……等 {len(edges)} 条关系")
    return "\n".join(lines)


def _render_citations(citations: Sequence[dict], conflicts: Sequence[dict]) -> str:
    """引用区：每条一行"**[index] title（kind）**"，片段以引用行展示。

    `snippet` 直接用 RAG 返回的片段，机器人侧不拼接、不放完整原文（版权约束，风险 6）。
    """
    lines: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        index = citation.get("index")
        title = _clean(citation.get("title")) or "（无标题）"
        kind = _clean(citation.get("kind"))
        head = f"**[{index}] {title}**" if index is not None else f"**{title}**"
        if kind:
            head += f"（{kind}）"
        lines.append(head)
        snippet = _clean(citation.get("snippet"))
        if snippet:
            if len(snippet) > MAX_CITATION_SNIPPET_CHARS:
                snippet = snippet[:MAX_CITATION_SNIPPET_CHARS] + "…"
            # 引用行承载片段；片段内的换行也必须逐行加 >，否则引用块会断
            lines.extend(f"> {part}" if part.strip() else ">" for part in snippet.split("\n"))
    if conflicts:
        # 只提示存在冲突，不展开细节（开发文档 5.5.2 区表）
        lines.append(f"{CONFLICT_WARNING}（{len(conflicts)} 处，详情请在网页版查看）")
    return "\n".join(lines)


def build_answer_card(*, answer_md: str, citations: Iterable[dict] | None = None,
                      conflicts: Iterable[dict] | None = None, panel: dict | None = None,
                      msg_key: str | None = None, examples: Sequence[str] | None = None,
                      subgraph_img_key: str | None = None, truncated: bool = False,
                      title: str = CARD_TITLE,
                      template: str = "blue") -> dict:
    """知识问答卡片（2.0）。

    `subgraph_img_key` 与 `panel.subgraph` 二选一：图片上传成功时传 img_key，
    否则自动走文字降级——**不允许空白**。
    """
    panel = panel if isinstance(panel, dict) else {}
    citations = [c for c in (citations or []) if isinstance(c, dict)]
    conflicts = [c for c in (conflicts or []) if isinstance(c, dict)]

    body = sanitize(answer_md or "")
    if truncated:
        body = f"{body}\n\n{TRUNCATED_NOTICE}" if body else TRUNCATED_NOTICE

    elements: list[dict] = [_md(body or "（没有可显示的内容）")]

    # 引用区（起步只出列表；调优方向是折叠与回调）
    if citations or conflicts:
        count = len(citations)
        title_text = f"引用（{count}）" if count else "引用"
        elements.append(_fold(title_text, [_md(_render_citations(citations, conflicts))]))

    # 实体卡文字化
    entity_cards = [c for c in (panel.get("entity_cards") or []) if isinstance(c, dict)]
    if entity_cards:
        shown = entity_cards[:MAX_ENTITY_CARDS]
        blocks = [_describe_entity_card(c) for c in shown]
        if len(entity_cards) > MAX_ENTITY_CARDS:
            blocks.append(f"……等 {len(entity_cards)} 个相关实体")
        elements.append(_fold(f"相关实体（{len(entity_cards)}）", [_md("\n\n".join(blocks))]))

    # 时间线文字化
    timeline_text = _render_timeline(panel.get("timeline"))
    if timeline_text:
        elements.append(_fold("时间线", [_md(timeline_text)]))

    # 子图：图片优先，失败/空图走文字降级
    subgraph = panel.get("subgraph") or {}
    if isinstance(subgraph, dict) and (subgraph.get("nodes") or subgraph.get("edges")):
        if subgraph_img_key:
            elements.append({"tag": "img", "img_key": subgraph_img_key,
                             "alt": {"tag": "plain_text", "content": "知识图谱子图"},
                             "preview": True})
        else:
            text = _render_subgraph_text(subgraph)
            if text:
                elements.append(_fold("关系图（文字版）", [_md(text)]))

    # 地点列表
    places_text = _render_places(panel.get("map_points"))
    if places_text:
        elements.append(_fold("相关地点", [_md(places_text)]))

    # 示例问题按钮
    # 2.0 已删除交互模块（"tag": "action"），按钮**直接放进 elements**（官方迁移说明）。
    # 代价是每个按钮独占一行（body.direction 默认纵向）；要横排得用 column_set，
    # 但那多一层容器、字段更多，验收期先取"文档明确支持、字段最少"的形态。
    demo = [q for q in (examples or []) if _clean(q)][:3]
    if demo:
        elements.append(_md("**试试问这些**"))
        elements.extend(
            _button(q if len(q) <= 20 else q[:19] + "…", {"action": "ask", "question": q})
            for q in demo
        )

    # 纠错反馈按钮
    if msg_key:
        elements.append(
            _button("反馈有误", {"action": "report_error", "msg_key": str(msg_key)}))

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "body": {"elements": elements},
    }


def attach_feedback_button(card: dict, msg_key: int | str | None) -> dict:
    """给已组好的卡片补上"反馈有误"按钮。

    为什么是"补"而不是一次组好：按钮的回传参数要带 `messages.id`，而这个 id 只有
    先落库才有；而卡片必须在发送前组好。因此流程是"落库拿 id → 补按钮 → 发送 →
    回填 bot_message_id"。原地修改并返回同一张卡片（调用方通常直接发它）。

    按钮在 2.0 里是 elements 的**直接成员**（没有 action 容器），所以这里按
    "顶层 button + behaviors 里的 action 值"判断是否已存在。
    """
    if msg_key is None or not isinstance(card, dict):
        return card
    elements = card.get("body", {}).get("elements")
    if not isinstance(elements, list):
        return card
    for element in elements:
        if element.get("tag") != "button":
            continue
        if _button_value(element).get("action") == "report_error":
            return card          # 已经有反馈按钮，不重复加
    elements.append(_button("反馈有误", {"action": "report_error", "msg_key": str(msg_key)}))
    return card


def build_degraded_card(reason: str = "internal") -> dict:
    """降级卡片（开发文档 5.3）：文案固定，不给用户看内部错误细节。

    `reason` 只用于选文案：timeout → 超时；其余（5xx / server_busy / 413 / 传输层）
    一律"服务暂不可用"。413 属于机器人侧历史组装问题，绝不提示"问题过长"。
    降级卡片不带"反馈有误"按钮：没有回答可纠错，也没有对应的 messages 行可指。
    """
    text = DEGRADED_TEXTS.get(reason, DEGRADED_TEXTS["internal"])
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": CARD_TITLE}, "template": "orange"},
        "body": {"elements": [_md(text)]},
    }


def build_too_long_card() -> dict:
    """问题过长/不支持（唯一允许提示用户"精简后重试"的场景）。"""
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": CARD_TITLE}, "template": "orange"},
        "body": {"elements": [_md(TOO_LONG_TEXT)]},
    }


def build_notice_card(text: str, *, title: str = CARD_TITLE) -> dict:
    """纯提示卡片（非文本消息、空问题、启动公告等）。"""
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "grey"},
        "body": {"elements": [_md(text)]},
    }


def build_placeholder_card(text: str = PLACEHOLDER_TEXT) -> dict:
    """两段式回复的占位卡，走与提示卡相同的灰色模板。

    为什么用灰色而不是答案卡的蓝色：它**不是答案**，不该看起来像答案——
    用户扫一眼就知道"还在查"，而不是"机器人回了句废话"。
    这张卡随后会被 `patch_card` 整卡替换（`config.update_multi=True` 正是 PATCH 的前提），
    消息 id 不变，因此不打扰会话流、也不新增一条消息。
    """
    return build_notice_card(text)


def build_feedback_ticket_card(*, feedback_id: int, open_id: str, question: str,
                               answer_md: str, citations: Sequence[dict] | None = None,
                               created_at: str = "") -> dict:
    """纠错工单卡片（开发文档 5.7）：发运营群。

    回答摘要取收敛后的前 500 字——工单是给人看的，不需要完整正文；
    完整内容运营侧可回查 SQLite（feedback 表）。
    """
    summary = sanitize(answer_md or "")
    if len(summary) > 500:
        summary = summary[:500] + "…"
    citations = [c for c in (citations or []) if isinstance(c, dict)]
    titles = "、".join(_clean(c.get("title")) for c in citations[:10] if _clean(c.get("title")))

    lines = [
        f"**提问人**：{open_id}",
        f"**问题**：{question}",
        "",
        "**回答摘要**",
        summary or "（空）",
    ]
    if titles:
        lines.extend(["", f"**引用（{len(citations)}）**", titles])
    if created_at:
        lines.extend(["", f"**时间**：{created_at}"])

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": f"纠错反馈 #{feedback_id}"},
                   "template": "red"},
        "body": {"elements": [_md("\n".join(lines))]},
    }


def build_answer_reply(answer_md: str, *, finish_reason: str = "normal",
                       citations: Sequence[dict] | None = None,
                       conflicts: Sequence[dict] | None = None,
                       panel: dict | None = None, truncated: bool = False,
                       msg_key: str | None = None, examples: Sequence[str] | None = None,
                       subgraph_img_key: str | None = None) -> tuple[dict, dict]:
    """组卡片并给出**元信息**（调用方据此判断这轮是不是降级了、收敛改了什么）。

    返回 `(card, meta)`。`meta` 恒有 `degraded` 键：
    - `degraded=False`：meta 是 Markdown 收敛计数（headings/table_rows/...）；
    - `degraded=True`：meta 只带 `reason`（契约外终态），调用方据此不写历史。

    终态不属于 normal / refused / degraded 时**不组答案卡**，直接给降级卡片：
    非流式接口本应把失败表达为 HTTP 错误，走到这里说明有契约外的情况，
    宁可按"答不上来"处理，也不要把半截回答当完整答案发出去。
    """
    if finish_reason not in ("normal", "refused", "degraded"):
        return build_degraded_card("internal"), {"degraded": True,
                                                 "reason": finish_reason or "unknown"}

    card = build_answer_card(
        answer_md=answer_md, citations=citations, conflicts=conflicts, panel=panel,
        msg_key=msg_key, examples=examples, subgraph_img_key=subgraph_img_key,
        truncated=truncated,
        template="blue" if finish_reason == "normal" else "turquoise",
    )
    _, counters = sanitize_with_counters(answer_md, max_chars=MAX_CHARS)
    return card, {**counters, "degraded": False}


def build_turn(*, answer_md: str, finish_reason: str,
               citations: Sequence[dict] | None = None):
    """构造要写入会话历史的本轮回答（content 用 answer_md 原文，保真优先）。

    定义在 skills/base.py 的 `AssistantTurn`；这里做延迟导入只是为了让
    cards 层不反向依赖 session 层（层次：skills → cards/session → db）。
    """
    from bot.session import AssistantTurn

    return AssistantTurn(content=answer_md, finish_reason=finish_reason,
                         citations=list(citations or []))


def card_to_json(card: dict) -> str:
    """飞书消息的 content 字段是 JSON 字符串（不是对象）。"""
    return json.dumps(card, ensure_ascii=False)
