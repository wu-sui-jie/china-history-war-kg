"""new_session 技能：`/new` 重置当前会话。

用户的上下文只能等 24h TTL 自然过期，没有别的方式清空；而历史参与 RAG 回答缓存的
键——重置会话是用户自救"答得不对 / 答得越来越慢"的手段（开发文档 5.2）。

规则与 help 同构：文本第一个词命中命令词即触发（`base.matches_command`）。
只删本会话的 `sessions` / `messages` 行；**`feedback` 表不动**——纠错记录是治理队列，
不该因为用户重置会话而消失（开发文档 5.7）。

被删掉的 `messages.id` 就是已发出卡片上「反馈有误」按钮的 `msg_key`：用户在新会话里
再点旧卡片时，`report_error` 走既有的"消息不存在"静默分支（toast 已回执），
不给用户报错，只留一行日志供运营核对。
"""

from __future__ import annotations

import logging

from bot.cards.builder import build_notice_card
from bot.skills.base import Reply, SkillContext, matches_command

log = logging.getLogger(__name__)

# 命令别名（比对前统一转小写）。带不带参数都命中：只比第一个词。
NEW_COMMANDS = ("/new", "/重置")

RESET_TEXT = ("已清空本会话的上下文，可以重新开始了。\n\n"
              "此前轮次的问答不再作为追问的上下文；已提交的纠错反馈不受影响。")


class NewSessionSkill:
    """命令式技能：`/new`。"""

    name = "new_session"

    def __init__(self, *, session):
        self.session = session

    def match(self, ctx: SkillContext) -> bool:
        return matches_command(ctx.question, NEW_COMMANDS)

    def run(self, ctx: SkillContext) -> Reply:
        counts = self.session.reset(ctx.session_key)
        log.info("会话已重置：session=%s messages=%d sessions=%d",
                 ctx.session_key, counts["messages"], counts["sessions"])
        return Reply(kind="card", card=build_notice_card(RESET_TEXT))
