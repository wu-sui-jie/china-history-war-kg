"""knowledge_qa：知识问答技能（开发文档 5.3，P0-3）。

流程：
    取 history（由 dispatcher 组装）→ rag_client.query
    → 组卡片（正文 + 引用折叠 + 实体卡/时间线/地点/子图）
    → Reply（含 assistant_turn，供 dispatcher 落库）
"""

from __future__ import annotations

import logging

from bot.cards.builder import (build_answer_reply, build_degraded_card,
                               build_too_long_card, build_turn)
from bot.rag_client import DEGRADED_CODES, RagError
from bot.session import as_rag_history
from bot.skills.base import Reply, SkillContext

log = logging.getLogger(__name__)


class KnowledgeQaSkill:
    """兜底技能：所有普通文本消息都走这里（match 恒真）。"""

    name = "knowledge_qa"
    # 卡片按钮 value.action=ask（示例问题/追问按钮）也归本技能处理
    card_action = "ask"
    # 两段式回复（批次③-1）：本技能同步等 RAG，长回答实测 12–25s，
    # 声明它之后 dispatcher 会先回一张"正在检索…"占位卡，跑完再 PATCH 成最终卡
    wants_placeholder = True

    def __init__(self, *, rag, session, renderer=None, examples=None, config=None):
        self.rag = rag
        self.session = session
        self.renderer = renderer      # bot/render/subgraph.py，可为 None（未启用）
        self.examples = examples      # DemoExamplesCache，可为 None
        self.config = config

    def match(self, ctx: SkillContext) -> bool:
        # 兜底技能：按注册顺序排在最后，前面的技能都不命中就轮到它
        return True

    def run(self, ctx: SkillContext) -> Reply:
        try:
            data = self.rag.query(ctx.question, as_rag_history(ctx.history), ctx.session_key)
        except RagError as e:
            return self._degraded_reply(e, ctx)
        except Exception as e:  # noqa: BLE001 - 任何未预期异常都不能让用户收到空白
            log.exception("RAG 调用出现未预期异常：%s", e)
            return Reply(kind="card", card=build_degraded_card("internal"))

        answer_md = str(data.get("answer_md") or "")
        finish_reason = str(data.get("finish_reason") or "")
        citations = list(data.get("citations") or [])
        conflicts = list(data.get("conflicts") or [])
        panel = data.get("panel") if isinstance(data.get("panel"), dict) else {}
        truncated = bool(data.get("truncated"))

        log.info("RAG 回答：session=%s finish=%s cache_hit=%s truncated=%s "
                 "citations=%d panel_keys=%s elapsed_ms=%s",
                 ctx.session_key, finish_reason, data.get("cache_hit"), truncated,
                 len(citations), sorted(panel.keys()), data.get("_elapsed_ms"))

        card, meta = build_answer_reply(
            answer_md, finish_reason=finish_reason, citations=citations,
            conflicts=conflicts, panel=panel, truncated=truncated,
            examples=self._examples(), subgraph_img_key=self._subgraph_image(panel),
        )
        if meta.get("degraded"):
            # 终态不属于 normal/refused/degraded：按答不上来处理，不写历史
            log.warning("回答终态异常，已降级：finish_reason=%r", finish_reason)
            return Reply(kind="card", card=card)
        if meta:
            log.debug("Markdown 收敛计数：%s", {k: v for k, v in meta.items()
                                                 if k != "degraded"})

        return Reply(kind="card", card=card,
                     assistant_turn=build_turn(answer_md=answer_md,
                                               finish_reason=finish_reason,
                                               citations=citations))

    # ---- 卡片按钮：ask（开发文档 5.5.3）----
    def handle_card_action(self, action, dispatcher) -> None:
        question = str(action.value.get("question") or "").strip()
        if not question:
            log.warning("ask 动作缺少 question，已忽略：value=%r", action.value)
            return
        event = dispatcher.build_synthetic_event(action, question)
        log.info("按钮提问：session=%s question=%r", event.session_key, question[:60])
        dispatcher.handle_message(event)

    # ---- 内部 ----
    def _examples(self) -> list[str]:
        """示例问题（P1-3）；拿不到就返回空列表，按钮区不渲染。"""
        if self.examples is None:
            return []
        try:
            return self.examples.questions()
        except Exception as e:  # noqa: BLE001 - 按钮是增强项，坏了不能影响问答
            log.warning("示例题获取异常（不影响回答）：%s", e)
            return []

    def _subgraph_image(self, panel: dict) -> str | None:
        """子图 → image_key（P2-1）。任何失败都返回 None，由 builder 走文字降级。

        上传动作在 renderer 内部完成（它同时持有 Node 渲染与 Feishu 上传两端），
        这里只管"有没有可渲染的子图"。
        """
        if self.renderer is None:
            return None
        subgraph = panel.get("subgraph") if isinstance(panel, dict) else None
        if not isinstance(subgraph, dict) or not (subgraph.get("nodes") or subgraph.get("edges")):
            return None
        try:
            return self.renderer.render_and_upload(subgraph)
        except Exception as e:  # noqa: BLE001 - 降级路径是必经路径
            log.warning("子图出图失败，已降级为文字列表：%s", e)
            return None

    def _degraded_reply(self, error: RagError, ctx: SkillContext) -> Reply:
        """RAG 调用失败 → 降级卡片（开发文档 5.3 的触发条件表）。"""
        if error.is_invalid_request:
            # 唯一允许"问题过长"文案的场景
            log.info("RAG 判定问题不合法：code=%s question_len=%d",
                     error.code, len(ctx.question))
            return Reply(kind="card", card=build_too_long_card())

        # 降级原因分三档（第 14 轮审计 P3-6）：timeout 单独一档（文案说"稍后再试"），
        # 集合里的可重试失败归 unavailable，其余才落到 internal。
        # 判定依据取自 rag_client.DEGRADED_CODES —— 它原先是个**没有任何引用**的常量，
        # 真正决定降级的是这里一句硬编码，于是"往集合里加错误码"会静默无效。
        reason = ("timeout" if error.code == "timeout"
                  else "unavailable" if error.code in DEGRADED_CODES
                  else "internal")
        if error.is_payload_too_large:
            # 413 是机器人侧历史组装的问题（风险 8）：日志里必须留下请求规模，
            # 否则只能靠猜"是不是历史太长了"
            history_bytes = len(str(ctx.history).encode("utf-8"))
            log.error("请求体过大（413）：历史约 %d 字节、%d 条——属机器人侧问题，"
                      "已按通用降级文案回复用户", history_bytes, len(ctx.history))
            reason = "internal"
        log.warning("回答降级：code=%s status=%s reason=%s message=%s",
                    error.code, error.status, reason, error.message)
        return Reply(kind="card", card=build_degraded_card(reason))
