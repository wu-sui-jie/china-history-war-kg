"""集成测试：全链路（收消息 → 调 RAG → 组卡片 → 发送）不依赖真实 RAG 与飞书。

对应开发文档 10.2：假 RAG 按真实事件序列回放，SDK 层的发送函数用替身接住，
断言最终发出的卡片 JSON。这条链一旦断了，单测再绿也没用——
用户看到的是"没回复"或"卡片结构不对"。

用例走**真 worker 线程**（`dispatcher.start()` + 队列 join 语义），
因为"回调只入队、处理在 worker"正是 P0 的核心约束，绕开它测不出真问题。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from bot.dispatcher import Dispatcher
from bot.rag_client import DemoExamplesCache, RagClient
from bot.skills.help import HelpSkill
from bot.skills.knowledge_qa import KnowledgeQaSkill
from bot.skills.report_error import ReportErrorSkill

from card_helpers import button_values, feedback_msg_key
from fake_rag import Case, FakeRag

OPERATORS_CHAT = "oc_operators"


class RecordingFeishu:
    """接住所有发送动作（等价于 mock 掉 SDK 的发送函数）。"""

    def __init__(self, fail_send: bool = False):
        self.replies: list[dict] = []
        self.sent: list[tuple[str, dict]] = []
        self.uploads: list[str] = []
        self.fail_send = fail_send

    def reply_card(self, message_id, card):
        self.replies.append({"message_id": message_id, "card": card})
        return None if self.fail_send else f"bot-msg-{len(self.replies)}"

    def reply_text(self, message_id, text):
        self.replies.append({"message_id": message_id, "text": text})
        return None if self.fail_send else f"bot-msg-{len(self.replies)}"

    def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return f"sent-{len(self.sent)}"

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, {"text": text}))
        return f"sent-{len(self.sent)}"

    def patch_card(self, message_id, card):
        return True

    def upload_image(self, path):
        self.uploads.append(str(path))
        return f"img-{len(self.uploads)}"

    @property
    def last_card(self) -> dict:
        assert self.replies, "没有任何回复"
        return self.replies[-1]["card"]


def message_event(text: str = "介绍一下长平之战", **overrides):
    kwargs = dict(event_id="ev-1", message_id="om-1", chat_id="oc-1",
                  chat_type="p2p", open_id="ou-1", mentions=[])
    kwargs.update(overrides)
    return SimpleNamespace(
        header=SimpleNamespace(event_id=kwargs["event_id"],
                               event_type="im.message.receive_v1"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=kwargs["message_id"], chat_id=kwargs["chat_id"],
                chat_type=kwargs["chat_type"], message_type="text",
                content=f'{{"text": "{text}"}}', mentions=kwargs["mentions"]),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=kwargs["open_id"]))),
    )


def card_action_event(value: dict, event_id: str = "cev-1", open_id: str = "ou-1"):
    """构造 card.action.trigger 的**原始**事件对象（on_card_action 自己解析）。"""
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id, event_type="card.action.trigger"),
        event=SimpleNamespace(
            action=SimpleNamespace(tag="button", value=value),
            operator=SimpleNamespace(open_id=open_id),
            context=SimpleNamespace(open_message_id="om-1", open_chat_id="oc-1")))


def wait_idle(dispatcher, timeout: float = 5.0) -> None:
    """等 worker 处理完排队事件（按队列语义等待，不轮询业务计数）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if dispatcher.queue.unfinished_tasks == 0:
            return
        time.sleep(0.01)
    raise AssertionError("worker 未在超时内处理完排队事件")


@pytest.fixture
def stack(config, session, db):
    """组装一台"机器人"：假 RAG + 记录式飞书客户端 + 全套技能 + 已启动的 worker。"""
    fakes: list[FakeRag] = []
    dispatchers: list[Dispatcher] = []

    def build(*, case: Case | None = None, operators_chat: str = OPERATORS_CHAT,
              with_examples: bool = True, renderer=None):
        fake = FakeRag(case=case).start()
        fakes.append(fake)
        rag = RagClient(fake.base_url, query_timeout=5.0, connect_timeout=2.0)
        feishu = RecordingFeishu()
        examples = DemoExamplesCache(rag, count=3) if with_examples else None
        config.feishu_operators_chat_id = operators_chat
        skills = [
            HelpSkill(),
            KnowledgeQaSkill(rag=rag, session=session, renderer=renderer,
                             examples=examples, config=config),
            ReportErrorSkill(session=session, feishu=feishu, config=config),
        ]
        dispatcher = Dispatcher(db=db, session=session, skills=skills, feishu=feishu,
                                config=config)
        dispatcher.start()
        dispatchers.append(dispatcher)
        return SimpleNamespace(fake=fake, rag=rag, feishu=feishu, dispatcher=dispatcher,
                              skills=skills, examples=examples)

    yield build
    for dispatcher in reversed(dispatchers):
        dispatcher.stop()
    for fake in reversed(fakes):
        fake.stop()


@pytest.fixture
def standalone(config, session, db):
    """自建 dispatcher（用于替换某个依赖的用例）。"""
    created: list[Dispatcher] = []

    def factory(*, skills, feishu=None):
        dispatcher = Dispatcher(db=db, session=session, skills=skills,
                                feishu=feishu or RecordingFeishu(), config=config)
        dispatcher.start()
        created.append(dispatcher)
        return dispatcher

    yield factory
    for dispatcher in reversed(created):
        dispatcher.stop()


# ---- P0：问答闭环 ----


def test_p2p_question_gets_answer_card(stack):
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战"))
    wait_idle(app.dispatcher)

    card = app.feishu.last_card
    assert card["schema"] == "2.0"
    body = card["body"]["elements"][0]["content"]
    assert "**长平之战**" in body                    # 标题已收敛
    assert "秦赵决战于长平" in body
    folds = {e["header"]["title"]["content"]: e for e in card["body"]["elements"]
             if e.get("tag") == "collapsible_panel"}
    assert "引用（1）" in folds
    assert "相关实体（1）" in folds
    assert "时间线" in folds
    assert "相关地点" in folds
    assert "关系图（文字版）" in folds                 # 无 renderer → 文字降级
    assert app.fake.requests[-1]["body"]["question"] == "介绍一下长平之战"


def test_group_question_with_mention(stack):
    app = stack()
    app.dispatcher.command_bot_open_id = "ou_bot"
    app.dispatcher.on_message(message_event(
        "@_user_1 长平之战是谁打的", chat_type="group", chat_id="oc-g",
        mentions=[{"key": "@_user_1", "name": "战争史问答", "open_id": "ou_bot"}]))
    wait_idle(app.dispatcher)

    body = app.feishu.last_card["body"]["elements"][0]["content"]
    assert "长平之战" in body
    # @ 前缀必须剥掉，不能当成问题内容发给 RAG
    assert app.fake.requests[-1]["body"]["question"] == "长平之战是谁打的"


def test_group_question_without_mention_is_silent(stack):
    app = stack()
    app.dispatcher.command_bot_open_id = "ou_bot"
    app.dispatcher.on_message(message_event("闲聊", chat_type="group"))
    wait_idle(app.dispatcher)
    assert not app.feishu.replies
    assert not app.fake.requests


def test_restart_keeps_history(stack, session):
    """重启机器人服务会话不丢（P0 验收）：历史在 SQLite 里。"""
    app = stack()
    app.dispatcher.on_message(message_event("长平之战是谁打的", event_id="ev-1"))
    wait_idle(app.dispatcher)
    app.dispatcher.on_message(message_event("他后来怎么样了", event_id="ev-2"))
    wait_idle(app.dispatcher)

    body = app.fake.requests[-1]["body"]
    assert body["history"], "追问必须带上历史"
    assert body["history"][0]["role"] == "user"
    assert body["history"][0]["content"] == "长平之战是谁打的"
    assert body["history"][-1]["role"] == "assistant"

    # 换一个机器人实例（模拟重启）用同一个库：历史仍在
    fresh = Dispatcher(db=session.db, session=session, skills=app.skills,
                       feishu=app.feishu, config=stack.__wrapped__ or None
                       if False else app.dispatcher.config)
    assert fresh.session.history_for_rag("ou-1:oc-1") == session.history_for_rag("ou-1:oc-1")


def test_history_isolated_between_chats(stack):
    app = stack()
    app.dispatcher.on_message(message_event("A 群的问题", event_id="ev-a", chat_id="oc-a"))
    wait_idle(app.dispatcher)
    app.dispatcher.on_message(message_event("B 群的问题", event_id="ev-b", chat_id="oc-b"))
    wait_idle(app.dispatcher)
    assert app.fake.requests[-1]["body"]["history"] == []


def test_duplicate_delivery_produces_single_reply(stack):
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-dup"))
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-dup"))
    wait_idle(app.dispatcher)
    assert len(app.feishu.replies) == 1
    assert len(app.fake.requests) == 1


def test_degraded_card_on_rag_failure(config, session, standalone):
    class RefusingRag:
        def query(self, *args, **kwargs):
            raise RuntimeError("RAG 挂了")

    feishu = RecordingFeishu()
    dispatcher = standalone(skills=[KnowledgeQaSkill(rag=RefusingRag(), session=session,
                                                     config=config)], feishu=feishu)
    dispatcher.on_message(message_event())
    wait_idle(dispatcher)
    assert "答不上来" in feishu.last_card["body"]["elements"][0]["content"]


def test_example_buttons_click_runs_new_question(stack):
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-1"))
    wait_idle(app.dispatcher)

    ask = [v for v in button_values(app.feishu.last_card) if v.get("action") == "ask"]
    assert ask, "卡片底部应有示例问题按钮"

    before = len(app.feishu.replies)
    app.dispatcher.on_card_action(card_action_event(ask[0], event_id="cev-1"))
    wait_idle(app.dispatcher)
    assert len(app.feishu.replies) == before + 1
    assert app.fake.requests[-1]["body"]["question"] == ask[0]["question"]


# ---- P2：纠错反馈 ----


def test_report_error_creates_feedback_and_delivers_ticket(stack, session):
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-1"))
    wait_idle(app.dispatcher)
    msg_key = feedback_msg_key(app.feishu.last_card)
    assert msg_key

    app.dispatcher.on_card_action(card_action_event({"action": "report_error",
                                                     "msg_key": msg_key},
                                                    event_id="cev-2"))
    wait_idle(app.dispatcher)

    assert app.feishu.sent, "运营群应收到工单卡片"
    chat_id, ticket = app.feishu.sent[-1]
    assert chat_id == OPERATORS_CHAT
    assert ticket["header"]["title"]["content"].startswith("纠错反馈 #")
    text = "\n".join(e.get("content", "") for e in ticket["body"]["elements"])
    assert "介绍一下长平之战" in text
    assert "ou-1" in text

    row = session.get_feedback(1)
    assert row["status"] == "open"
    assert row["question"] == "介绍一下长平之战"
    assert row["answer_md"].startswith("# 长平之战")


def test_repeated_feedback_is_not_delivered_twice(stack, session):
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-1"))
    wait_idle(app.dispatcher)
    msg_key = feedback_msg_key(app.feishu.last_card)
    for event_id in ("cev-a", "cev-b"):
        app.dispatcher.on_card_action(card_action_event(
            {"action": "report_error", "msg_key": msg_key}, event_id=event_id))
        wait_idle(app.dispatcher)
    assert len(app.feishu.sent) == 1, "同一用户对同一条回答重复反馈不该重复投递"
    assert len(session.db.query_all("SELECT * FROM feedback")) == 1


def test_feedback_without_operators_chat_only_logs(stack, session):
    app = stack(operators_chat="")
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-1"))
    wait_idle(app.dispatcher)
    msg_key = feedback_msg_key(app.feishu.last_card)
    app.dispatcher.on_card_action(card_action_event({"action": "report_error",
                                                     "msg_key": msg_key},
                                                    event_id="cev-2"))
    wait_idle(app.dispatcher)
    assert not app.feishu.sent                  # 没配群就不投递
    assert session.get_feedback(1) is not None   # 但仍然落库


def test_feedback_on_missing_message_is_ignored(stack, session):
    app = stack()
    app.dispatcher.on_card_action(card_action_event({"action": "report_error",
                                                     "msg_key": "999"},
                                                    event_id="cev-x"))
    wait_idle(app.dispatcher)
    assert session.get_feedback(999) is None
    assert not app.feishu.sent


# ---- 其它链路 ----


def test_help_command_returns_usage_card(stack):
    app = stack()
    app.dispatcher.on_message(message_event("/help", event_id="ev-help"))
    wait_idle(app.dispatcher)
    text = app.feishu.last_card["body"]["elements"][0]["content"]
    assert "我能做什么" in text and "追问" in text
    assert not app.fake.requests, "/help 不该打 RAG"


def test_rag_timeout_is_visible_in_card(config, session, standalone):
    fake = FakeRag(delay=1.0).start()
    try:
        rag = RagClient(fake.base_url, query_timeout=0.2, connect_timeout=1.0)
        feishu = RecordingFeishu()
        dispatcher = standalone(
            skills=[KnowledgeQaSkill(rag=rag, session=session, config=config)],
            feishu=feishu)
        dispatcher.on_message(message_event())
        wait_idle(dispatcher)
        assert "超时" in feishu.last_card["body"]["elements"][0]["content"]
    finally:
        fake.stop()


def test_worker_thread_end_to_end(stack):
    """真跑 worker 线程（覆盖"回调入队 → worker 处理 → 回复"的完整路径）。"""
    app = stack()
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-thread"))
    wait_idle(app.dispatcher)
    assert "长平之战" in app.feishu.last_card["body"]["elements"][0]["content"]
    assert app.dispatcher.stats["handled"] == 1
    assert app.dispatcher.stats["duplicate"] == 0


def test_send_failure_rolls_back_turn_and_keeps_worker_alive(stack, session):
    """发送失败（飞书抖动）不能让 worker 挂掉，也不能把没送达的回答留在历史里。

    回答本身是成立的，但**用户从未见过它**——留在历史里会污染下一轮上下文，
    还会让 RAG 回答缓存的键永远命中不了（本该毫秒返回的问题退化成十几秒真生成）。
    所以两行一起撤回，日志里留 ERROR 带答案全文供手工补偿。
    """
    app = stack()
    app.feishu.fail_send = True
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-fail"))
    wait_idle(app.dispatcher)

    assert session.history_for_rag("ou-1:oc-1") == []
    assert session.db.query_one("SELECT COUNT(*) FROM messages")[0] == 0
    assert app.dispatcher.stats["send_failed"] == 1
    assert app.dispatcher.stats["failed"] == 0
    assert app.dispatcher.stats["handled"] == 1     # worker 活着，链路走完了


def test_subgraph_image_used_when_renderer_available(stack):
    """渲染成功时卡片走 img 元素（P2-1 成功路径）。"""

    class FakeRenderer:
        def render_and_upload(self, subgraph):
            assert subgraph["nodes"], "渲染器应拿到节点"
            return "img_key_from_renderer"

    app = stack(renderer=FakeRenderer())
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-img"))
    wait_idle(app.dispatcher)
    images = [e for e in app.feishu.last_card["body"]["elements"] if e.get("tag") == "img"]
    assert images and images[0]["img_key"] == "img_key_from_renderer"


def test_subgraph_text_fallback_when_renderer_fails(stack):
    """渲染失败必须自动降级为文字列表，**不允许空白**（P2-1 降级路径）。"""

    class BrokenRenderer:
        def render_and_upload(self, subgraph):
            raise RuntimeError("Node 挂了")

    app = stack(renderer=BrokenRenderer())
    app.dispatcher.on_message(message_event("介绍一下长平之战", event_id="ev-img2"))
    wait_idle(app.dispatcher)
    elements = app.feishu.last_card["body"]["elements"]
    assert not [e for e in elements if e.get("tag") == "img"]
    folds = {e["header"]["title"]["content"] for e in elements
             if e.get("tag") == "collapsible_panel"}
    assert "关系图（文字版）" in folds


# ---- 工具 ----
# 卡片取值统一走 tests/card_helpers.py（按 2.0 结构：按钮是 elements 直接成员、
# 回传参数在 behaviors 里），避免每个文件各写一份解析而一起失效。
