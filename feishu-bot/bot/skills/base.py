"""技能框架：接口、上下文与注册表（开发文档 5.3）。

设计原则（继承需求文档第三节旧版 Q7/Q10 的修正结论）：
- 机器人服务是所有"动作"的唯一归属地，RAG 只回答知识问题；
- 意图分流起步用**规则**（按钮回调 / 命令前缀），不引入 LLM 意图识别——
  技能只有 2 个，规则零延迟且可测试；
- RAG 是**工具**而非 agent：调用的是"数据/接口"，不是"页面"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable

from bot.session import AssistantTurn

if TYPE_CHECKING:  # 仅类型标注用，避免运行期循环导入（dispatcher 导入本模块）
    from bot.dispatcher import MessageEvent


@dataclass
class Reply:
    """技能返回值。`kind` 为 "card" 时用 card，为 "text" 时用 text（P0 只有 card，text 留作兜底）。"""

    kind: str                                  # "card" | "text"
    card: dict | None = None
    text: str | None = None
    # 在开发文档 5.3 的 Reply 之上扩展一个可选字段：本轮回答若要写入会话历史，
    # 由技能填这里，dispatcher 负责落库。实际顺序是
    # "落库拿 id → 补反馈按钮 → 发送 → 回填 bot_message_id"——按钮的 value 要带
    # messages.id，所以必须先落库再发送（开发文档第十三节第 3 条）。
    # 不填（如降级卡片）表示这轮不进入历史。
    assistant_turn: AssistantTurn | None = None


@dataclass
class SkillContext:
    """一次技能执行的全部输入（开发文档 5.3）。"""

    event: "MessageEvent"
    question: str                       # 剥离 @ 后的问题文本
    session_key: str
    history: list[dict] = field(default_factory=list)   # 组装好的 RAG history


def command_word(text: str) -> str:
    """取文本的第一个词（小写），命令式技能的统一匹配口径。

    为什么按"第一个词"而不是 `startswith`：`/help 长平之战` 与 `/帮助 长平之战`
    必须同规则，而 `startswith` 得为每个别名各写一条前缀判断（早先就是这么写的，
    结果 `/帮助 带参数` 不命中）；这样也天然排除了 `/helpx` 这类"以命令开头的新词"。
    """
    cleaned = (text or "").strip().lower()
    return cleaned.split(maxsplit=1)[0] if cleaned else ""


def matches_command(text: str, commands: Sequence[str]) -> bool:
    """文本的第一个词是否命中命令词表（命令式技能的 `match` 用这一句）。"""
    word = command_word(text)
    return bool(word) and word in commands


@runtime_checkable
class Skill(Protocol):
    """技能协议：`name` 标识 + `match` 判定 + `run` 执行。

    可选属性 `wants_placeholder = True`：本技能可能长时间阻塞（如同步等 RAG），
    dispatcher 会在执行它**之前**先回一张占位卡，跑完后用 PATCH 整卡替换（两段式
    回复，批次③-1）。不声明表示秒回，走普通单段式发送——命令技能、提示卡片都不需要。
    """

    name: str

    def match(self, ctx: SkillContext) -> bool: ...

    def run(self, ctx: SkillContext) -> Reply: ...


class SkillRegistry:
    """按注册顺序匹配，命中即执行；全不命中则用兜底技能（knowledge_qa 的 match 恒真）。"""

    def __init__(self, skills: list[Any] | None = None):
        self.skills: list[Any] = list(skills or [])

    def register(self, skill: Any) -> None:
        self.skills.append(skill)

    def resolve(self, ctx: SkillContext) -> Any | None:
        for skill in self.skills:
            if skill.match(ctx):
                return skill
        return None

    def run(self, ctx: SkillContext) -> Reply | None:
        skill = self.resolve(ctx)
        return skill.run(ctx) if skill is not None else None
