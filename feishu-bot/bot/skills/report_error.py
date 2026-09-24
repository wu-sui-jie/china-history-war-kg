"""report_error：纠错反馈收集与投递（开发文档 5.7，P2-2）。

触发方式与 knowledge_qa 不同：**不走文本分流**，只由卡片按钮回调触发
（按钮即意图，无需文本规则，开发文档 5.3 的分流表）。

流转：
    用户点"反馈有误" → 按 msg_key 取 question / answer_md / citations
    → 落 feedback 表（status=open）
    → 组工单卡片发运营群
    → 用户侧回执（PATCH 原卡片按钮区，失败不阻塞）
"""

from __future__ import annotations

import json
import logging
import time

from bot.cards.builder import build_feedback_ticket_card, build_notice_card
from bot.skills.base import Reply, SkillContext

log = logging.getLogger(__name__)

ACK_TEXT = "已收到反馈，谢谢！我们会尽快核对这条回答。"


class ReportErrorSkill:
    """纠错反馈技能（仅由卡片按钮触发）。"""

    name = "report_error"
    card_action = "report_error"

    def __init__(self, *, session, feishu, config):
        self.session = session
        self.feishu = feishu
        self.config = config

    def match(self, ctx: SkillContext) -> bool:
        # 文本消息永远不命中本技能（按钮即意图）
        return False

    def run(self, ctx: SkillContext) -> Reply:  # pragma: no cover - 不会走到
        return Reply(kind="card", card=build_notice_card(ACK_TEXT))

    # ---- 卡片按钮入口 ----
    def handle_card_action(self, action, dispatcher) -> None:
        """按钮回调的**业务处理**（在 worker 线程里执行，不在 SDK 回调里）。

        用户侧回执由 dispatcher 的即时 toast 承担（"已收到反馈，谢谢！"）。
        这里**不做 PATCH 整卡**：飞书的 PATCH 是整卡替换，而 messages 表只存了
        正文与引用、没有 panel（实体卡/子图），重建卡片会把用户正在看的内容弄丢——
        拿不准就不要改（开发文档 5.7 把该步骤标为"可选"）。
        """
        try:
            msg_id = int(str(action.value.get("msg_key") or "0"))
        except ValueError:
            log.warning("report_error 的 msg_key 非法：%r", action.value.get("msg_key"))
            return
        message = self.session.get_message(msg_id) if msg_id else None
        if not message or message.get("role") != "assistant":
            # 卡片可能来自更早的数据（清理过）或消息不存在：不给用户报错，
            # toast 已经说了"已收到"，只留日志供运营侧核对
            log.warning("反馈指向的消息不存在或不是回答：msg_key=%s", msg_id)
            return

        # 同一用户对同一条回答重复点按钮：不再重复建单与投递（飞书侧按钮点一次仍会触发）
        existing = self.session.find_feedback(
            open_id=action.open_id,
            bot_message_id=message.get("bot_message_id") or action.message_id,
        )
        if existing:
            log.info("重复反馈已忽略：已有 feedback #%s（open_id=%s msg_key=%s）",
                     existing.get("id"), action.open_id, msg_id)
            return

        question = self.session.question_before_assistant(message["session_key"], msg_id)
        answer_md = str(message.get("content") or "")
        citations = self._parse_citations(message.get("citations"))

        feedback_id = self.session.record_feedback(
            open_id=action.open_id, key=str(message["session_key"]),
            message_id=message.get("bot_message_id") or action.message_id,
            question=question, answer_md=answer_md, citations=citations,
        )
        log.info("纠错反馈已落库：id=%s open_id=%s msg_key=%s", feedback_id,
                 action.open_id, msg_id)

        self._deliver(feedback_id, action, question, answer_md, citations)

    # ---- 内部 ----
    @staticmethod
    def _parse_citations(raw) -> list[dict]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        return [c for c in parsed if isinstance(c, dict)] if isinstance(parsed, list) else []

    def _deliver(self, feedback_id: int, action, question: str, answer_md: str,
                 citations: list[dict]) -> None:
        """投递工单到运营群；未配置目标群时只留日志（不阻塞回执）。"""
        chat_id = (self.config.feishu_operators_chat_id or "").strip()
        if not chat_id:
            log.warning("未配置 FEISHU_OPERATORS_CHAT_ID：纠错反馈 #%s 只落在 SQLite，"
                        "没有投递到运营群", feedback_id)
            return
        card = build_feedback_ticket_card(
            feedback_id=feedback_id, open_id=action.open_id, question=question,
            answer_md=answer_md, citations=citations,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            self.feishu.send_card(chat_id, card)
            log.info("纠错工单已投递：id=%s chat=%s", feedback_id, chat_id)
        except Exception as e:  # noqa: BLE001 - 投递失败不回滚建单
            log.exception("纠错工单投递失败：id=%s chat=%s err=%s", feedback_id, chat_id, e)
