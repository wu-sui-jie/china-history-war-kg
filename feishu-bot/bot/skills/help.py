"""help 技能：使用说明卡片（开发文档 5.3 的预留规则位，P1 随示例问题按钮一起加）。

规则：文本以 `/help` 开头（命令前缀）即命中，零延迟、可测试；
不引入 LLM 意图识别（技能只有个位数时，规则分流是更稳的选择）。
"""

from __future__ import annotations

from bot.cards.builder import build_notice_card
from bot.skills.base import Reply, SkillContext

HELP_TEXT = """**我能做什么**
直接发问题给我就行，例如「介绍一下长平之战」。

**群聊里怎么用**
@我 之后输入问题；不 @ 我不会被触发。

**追问**
同一会话内可以直接说「他后来怎么样了」，我会带着上文理解指代。
会话按「用户 × 群」隔离，换群或换人不会串上下文。

**命令**
- `/help`：显示这份说明

**回答里有疑问**
点回答卡片上的「反馈有误」，运营侧会收到这条纠错。"""

# 命令别名（全小写比对）。带不带参数都命中：只比**第一个词**——
# `/help 长平之战` 与 `/帮助 长平之战` 必须同规则，不能一个认全等、一个认前缀。
HELP_COMMANDS = ("/help", "/帮助", "/?")


class HelpSkill:
    """命令式技能：`/help`。"""

    name = "help"

    def match(self, ctx: SkillContext) -> bool:
        return _is_help_command(ctx.question)

    def run(self, ctx: SkillContext) -> Reply:
        return Reply(kind="card", card=build_notice_card(HELP_TEXT))


def _is_help_command(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    # 第一个词是命令词即命中（"帮我看看"这类不以斜杠开头的自然语言不会误命中）
    return text.split(maxsplit=1)[0] in HELP_COMMANDS
