"""help 技能：使用说明卡片（开发文档 5.3 的第 1 号分流规则）。

规则：文本的第一个词命中 `/help`、`/帮助`、`/?` 即命中（命令前缀，零延迟、可测试）；
不引入 LLM 意图识别（技能只有个位数时，规则分流是更稳的选择）。
匹配口径与命令词表见 `bot/skills/base.py::matches_command`——命令技能共用同一句。
"""

from __future__ import annotations

from bot.cards.builder import build_notice_card
from bot.skills.base import Reply, SkillContext, matches_command

HELP_TEXT = """**我能做什么**
直接发问题给我就行，例如「介绍一下长平之战」。
长回答通常要等 5–25 秒：我会先回一张「正在检索…」的卡片，查到后替换成正式回答。

**群聊里怎么用**
@我 之后输入问题；不 @ 我不会被触发。

**追问**
同一会话内可以直接说「他后来怎么样了」，我会带着上文理解指代。
会话按「用户 × 群」隔离，换群或换人不会串上下文。

**命令**
- `/help`：显示这份说明
- `/new`：清空本会话上下文，重新开始

**回答里有疑问**
点回答卡片上的「反馈有误」，运营侧会收到这条纠错。"""

# 命令别名（比对前统一转小写）。带不带参数都命中：只比第一个词。
HELP_COMMANDS = ("/help", "/帮助", "/?")


class HelpSkill:
    """命令式技能：`/help`。"""

    name = "help"

    def match(self, ctx: SkillContext) -> bool:
        return matches_command(ctx.question, HELP_COMMANDS)

    def run(self, ctx: SkillContext) -> Reply:
        return Reply(kind="card", card=build_notice_card(HELP_TEXT))


def _is_help_command(question: str) -> bool:
    """保留这个薄封装给测试与旧调用方用；判断口径只有 `matches_command` 一处。"""
    return matches_command(question, HELP_COMMANDS)
