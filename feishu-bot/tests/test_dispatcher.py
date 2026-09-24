"""事件接入的守护用例（开发文档 10.1 / P0-4）。

三件事必须成立，否则线上表现为"用户收到重复回答"或"机器人不响应"：
1. event_id 去重（含重复投递）——飞书是"至少一次"投递；
2. 群聊 @ 判断与 @ 剥离——判断错了要么不响应，要么把 "@机器人" 当问题发给 RAG；
3. 非 text 消息有明确回执——静默忽略会让用户以为机器人坏了。

测试全部用 SimpleNamespace 构造事件，**不导入 lark-oapi、不联网**。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.cards.builder import build_notice_card
from card_helpers import button_values
from bot.dispatcher import (Dispatcher, mentioned_bot, parse_card_action,
                            parse_message_event, strip_mentions)


def make_message_event(*, text: str = "介绍一下长平之战", chat_type: str = "p2p",
                       message_type: str = "text", mentions=None, event_id: str = "ev1",
                       message_id: str = "om_1", chat_id: str = "oc_1",
                       open_id: str = "ou_1"):
    """构造 SDK 事件对象的最小等价物（字段路径与真实事件一致）。"""
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id, event_type="im.message.receive_v1"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=message_id, chat_id=chat_id, chat_type=chat_type,
                message_type=message_type, content=f'{{"text": "{text}"}}',
                mentions=mentions or [],
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id)),
        ),
    )


def make_card_event(*, value: dict, event_id: str = "cev1", open_id: str = "ou_1",
                    open_message_id: str = "om_card", open_chat_id: str = "oc_1"):
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id, event_type="card.action.trigger"),
        event=SimpleNamespace(
            action=SimpleNamespace(tag="button", value=value),
            operator=SimpleNamespace(open_id=open_id),
            context=SimpleNamespace(open_message_id=open_message_id,
                                    open_chat_id=open_chat_id),
        ),
    )


# ---- 解析 ----


def test_parse_message_event_extracts_fields():
    event = parse_message_event(make_message_event(text="你好"))
    assert event.event_id == "ev1"
    assert event.message_id == "om_1"
    assert event.chat_id == "oc_1"
    assert event.chat_type == "p2p"
    assert event.open_id == "ou_1"
    assert event.text == "你好"
    assert event.session_key == "ou_1:oc_1"


def test_parse_message_event_handles_bad_shapes():
    assert parse_message_event(SimpleNamespace()) is None
    # 缺少 open_id：无法定位会话，必须丢弃而不是用空值建会话
    broken = make_message_event()
    broken.event.sender = SimpleNamespace(sender_id=None)
    assert parse_message_event(broken) is None


def test_parse_message_event_non_json_content_is_empty_text():
    raw = make_message_event()
    raw.event.message.content = "not-json"
    assert parse_message_event(raw).text == ""


def test_parse_card_action_extracts_value():
    action = parse_card_action(make_card_event(
        value={"action": "report_error", "msg_key": "12"}))
    assert action.action == "report_error"
    assert action.value["msg_key"] == "12"
    assert action.open_id == "ou_1"
    assert action.message_id == "om_card"
    assert action.chat_id == "oc_1"


# ---- 群聊 @ 判断与剥离 ----


def test_mentioned_bot_by_open_id():
    event = parse_message_event(make_message_event(
        chat_type="group", text="@_user_1 介绍一下长平之战",
        mentions=[{"key": "@_user_1", "name": "战争史问答", "open_id": "ou_bot"}]))
    assert mentioned_bot(event, "ou_bot") is True
    assert mentioned_bot(event, "ou_other") is False


def test_mentioned_bot_falls_back_to_key_prefix_without_bot_open_id():
    event = parse_message_event(make_message_event(
        chat_type="group", text="@_user_1 介绍一下长平之战",
        mentions=[{"key": "@_user_1", "name": "战争史问答", "open_id": ""}]))
    assert mentioned_bot(event, None) is True


def test_no_mention_in_group():
    event = parse_message_event(make_message_event(chat_type="group", text="随便聊聊"))
    assert mentioned_bot(event, "ou_bot") is False


def test_strip_mentions_removes_key_and_collapses_spaces():
    text = "@_user_1 介绍一下长平之战"
    assert strip_mentions(text, [{"key": "@_user_1"}]) == "介绍一下长平之战"
    assert strip_mentions("@_user_1", [{"key": "@_user_1"}]) == ""


def test_strip_mentions_keeps_other_people_mentions():
    """只剥机器人自己的 mention（开发文档 5.1）：@别人也是问题语义的一部分。"""
    mentions = [{"key": "@_user_1", "name": "战争史问答", "open_id": "ou_bot"},
                {"key": "@_user_2", "name": "张三", "open_id": "ou_zhang"}]
    text = "@_user_1 介绍一下 @_user_2 提到的赤壁之战"

    assert strip_mentions(text, mentions, "ou_bot") == "介绍一下 @_user_2 提到的赤壁之战"

    # 拿不到 bot open_id 时退化为"只剥开头那个 mention key"——宁可少剥，不要多剥
    assert strip_mentions(text, mentions, None) == "介绍一下 @_user_2 提到的赤壁之战"
    # 机器人 mention 不在开头、又没有 open_id 可比对时不动文本（少剥的安全侧）
    assert strip_mentions("介绍一下 @_user_1 提到的赤壁之战", mentions,
                          None) == "介绍一下 @_user_1 提到的赤壁之战"


# ---- 去重 ----


class FakeFeishu:
    """记录发送内容，不联网。"""

    def __init__(self):
        self.replies: list[tuple[str, dict]] = []
        self.sent: list[tuple[str, dict]] = []
        self.patched: list[tuple[str, dict]] = []
        self.uploads: list[str] = []

    def reply_card(self, message_id, card):
        self.replies.append((message_id, card))
        return f"bot-{len(self.replies)}"

    def reply_text(self, message_id, text):
        self.replies.append((message_id, {"text": text}))
        return f"bot-{len(self.replies)}"

    def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return f"sent-{len(self.sent)}"

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, {"text": text}))
        return f"sent-{len(self.sent)}"

    def patch_card(self, message_id, card):
        self.patched.append((message_id, card))
        return True

    def upload_image(self, path):
        self.uploads.append(str(path))
        return f"img-{len(self.uploads)}"


class EchoSkill:
    """最小技能：把问题原样回显成卡片（P0-2 验收里的"已收到：xxx"）。"""

    name = "echo"

    def match(self, ctx) -> bool:
        return True

    def run(self, ctx):
        from bot.skills.base import Reply

        return Reply(kind="card", card=build_notice_card(f"已收到：{ctx.question}"))


def make_dispatcher(config, session, db, skills=None, feishu=None, clock=None) -> Dispatcher:
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return Dispatcher(db=db, session=session, skills=skills or [EchoSkill()],
                      feishu=feishu or FakeFeishu(), config=config, **kwargs)


def _answer_skill():
    """带 assistant_turn 的最小技能（落库路径的用例都用它）。"""
    from bot.cards.builder import build_turn
    from bot.skills.base import Reply

    class AnswerSkill:
        name = "answer"

        def match(self, ctx):
            return True

        def run(self, ctx):
            return Reply(kind="card", card=build_notice_card("回答"),
                         assistant_turn=build_turn(answer_md="**答**", finish_reason="normal",
                                                   citations=[{"index": 1, "title": "t"}]))

    return AnswerSkill()


def test_duplicate_message_event_is_dropped(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    dispatcher.handle_message = lambda event: dispatcher.stats.__setitem__(
        "handled", dispatcher.stats["handled"] + 1)

    dispatcher.on_message(make_message_event(event_id="ev-dup"))
    dispatcher.on_message(make_message_event(event_id="ev-dup"))
    assert dispatcher.stats["received"] == 2
    assert dispatcher.stats["duplicate"] == 1
    assert dispatcher.stats["enqueued"] == 1


def test_dedupe_survives_process_restart(config, session, db):
    """去重落库的意义：进程重启后仍能压制飞书的重投。"""
    first = make_dispatcher(config, session, db)
    first.on_message(make_message_event(event_id="ev-persist"))
    assert first.stats["enqueued"] == 1

    second = make_dispatcher(config, session, db)      # 模拟重启（新对象、同一 DB）
    second.on_message(make_message_event(event_id="ev-persist"))
    assert second.stats["enqueued"] == 0
    assert second.stats["duplicate"] == 1


def test_message_dedupe_without_event_id_uses_message_id(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    dispatcher.on_message(make_message_event(event_id="", message_id="om_x"))
    dispatcher.on_message(make_message_event(event_id="", message_id="om_x"))
    assert dispatcher.stats["enqueued"] == 1
    # 另一条消息（不同 message_id）必须放行
    dispatcher.on_message(make_message_event(event_id="", message_id="om_y"))
    assert dispatcher.stats["enqueued"] == 2


# ---- 卡片回调去重（P0-4 验收口径）----


def test_card_action_dedupe_by_event_id(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    event = make_card_event(value={"action": "ask", "question": "问"}, event_id="cev-1")
    dispatcher.on_card_action(event)
    dispatcher.on_card_action(event)
    assert dispatcher.stats["enqueued"] == 1
    assert dispatcher.stats["duplicate"] == 1


def test_card_action_fallback_window_allows_real_repeated_clicks(config, session, db):
    """连点两次同一按钮必须两条都处理（P0-4 的明确验收项）。

    窗口只有 2s（默认）：真实二次点击之间必然超过它，所以两次都要放行；
    同一次点击的重投发生在毫秒级，会被压住。
    """
    now = [1000.0]
    dispatcher = make_dispatcher(config, session, db, clock=lambda: now[0])
    event = make_card_event(value={"action": "ask", "question": "问"}, event_id="")
    dispatcher.on_card_action(event)
    assert dispatcher.stats["enqueued"] == 1

    # 毫秒级重投（同一次点击）：压掉
    now[0] += 0.3
    dispatcher.on_card_action(event)
    assert dispatcher.stats["enqueued"] == 1
    assert dispatcher.stats["duplicate"] == 1

    # 3 秒后的真实二次点击：放行
    now[0] += 3.0
    dispatcher.on_card_action(event)
    assert dispatcher.stats["enqueued"] == 2


def test_card_action_without_action_value_is_ignored(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    assert dispatcher.on_card_action(make_card_event(value={})) is None
    assert dispatcher.stats["enqueued"] == 0


def test_card_action_returns_immediate_toast(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    resp = dispatcher.on_card_action(make_card_event(value={"action": "ask",
                                                            "question": "问"}))
    assert resp["toast"]["content"]
    resp2 = dispatcher.on_card_action(make_card_event(
        value={"action": "report_error", "msg_key": "1"}, event_id="cev-2"))
    assert "反馈" in resp2["toast"]["content"]


# ---- worker 与分流 ----


def test_handle_message_replies_but_does_not_record_without_answer(config, session, db):
    """没有回答的轮次（这里是 EchoSkill，不含 assistant_turn）不写入会话历史。

    规则见 dispatcher._record_turn：命令轮与降级轮都不落库——它们进 RAG 上下文没有价值，
    而且历史参与回答缓存的键，多一条无意义的历史会让同一问题从缓存命中变成真生成
    （真机踩过：同一个"介绍一下长平之战"因多了一条 /help 历史，从 34ms 变成 16–25s）。
    """
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.handle_message(parse_message_event(make_message_event(text="介绍一下长平之战")))

    assert feishu.replies
    assert feishu.replies[0][0] == "om_1"
    assert "已收到：介绍一下长平之战" in feishu.replies[0][1]["body"]["elements"][0]["content"]
    assert session.history_for_rag("ou_1:oc_1") == []


def test_handle_message_records_answer_round_in_order(config, session, db):
    """有回答的轮次：user 行先写、assistant 行后写（反馈工单靠这个顺序取问题）。"""
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, skills=[_answer_skill()], feishu=feishu)
    dispatcher.handle_message(parse_message_event(make_message_event(text="介绍一下长平之战")))

    rows = db.query_all("SELECT id, role, content, bot_message_id FROM messages ORDER BY id")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "介绍一下长平之战"
    assert rows[1]["bot_message_id"] == "bot-1"          # 发送成功后回填
    assert session.question_before_assistant("ou_1:oc_1", rows[1]["id"]) == "介绍一下长平之战"
    # 卡片上带了指向 assistant 行的反馈按钮
    values = button_values(feishu.replies[0][1])
    assert {"action": "report_error", "msg_key": str(rows[1]["id"])} in values


def test_send_failure_rolls_back_the_whole_turn(config, session, db):
    """发送失败（重试后仍未成功）要把刚落库的两行一起撤回（审查报告 3.1-4）。

    用户什么都没收到，这轮却留在历史里会污染下一轮上下文，也让 RAG 回答缓存的键
    永远命中不了（线上实测：本该毫秒返回的问题退化成十几秒真生成）。
    """
    class BrokenFeishu(FakeFeishu):
        def reply_card(self, message_id, card):
            raise ConnectionError("SSLEOFError(8, 'UNEXPECTED_EOF_WHILE_READING')")

    feishu = BrokenFeishu()
    dispatcher = make_dispatcher(config, session, db, skills=[_answer_skill()], feishu=feishu)
    dispatcher.handle_message(parse_message_event(make_message_event(text="介绍一下长平之战")))

    assert db.query_all("SELECT id FROM messages") == []
    assert session.history_for_rag("ou_1:oc_1") == []
    assert dispatcher.stats["send_failed"] == 1
    assert dispatcher.stats["failed"] == 0       # 不算"任务失败"，链路本身是通的


def test_group_message_without_mention_is_ignored(config, session, db):
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.command_bot_open_id = "ou_bot"
    dispatcher.handle_message(parse_message_event(make_message_event(
        chat_type="group", text="没 @ 机器人的闲聊", mentions=[])))
    assert not feishu.replies


def test_group_message_with_mention_strips_prefix(config, session, db):
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.command_bot_open_id = "ou_bot"
    dispatcher.handle_message(parse_message_event(make_message_event(
        chat_type="group", text="@_user_1 长平之战是谁打的",
        mentions=[{"key": "@_user_1", "name": "战争史问答", "open_id": "ou_bot"}])))
    assert "已收到：长平之战是谁打的" in feishu.replies[0][1]["body"]["elements"][0]["content"]


def test_group_message_keeps_mention_of_other_people(config, session, db):
    """群里 @ 别人是问题的一部分，不能连它一起剥掉（审查报告 3.2-2）。"""
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.command_bot_open_id = "ou_bot"
    dispatcher.handle_message(parse_message_event(make_message_event(
        chat_type="group", text="@_user_1 介绍一下 @_user_2 提到的赤壁之战",
        mentions=[{"key": "@_user_1", "name": "战争史问答", "open_id": "ou_bot"},
                  {"key": "@_user_2", "name": "张三", "open_id": "ou_zhang"}])))
    body = feishu.replies[0][1]["body"]["elements"][0]["content"]
    assert "介绍一下 @_user_2 提到的赤壁之战" in body


def test_group_message_mention_only_replies_nothing(config, session, db):
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.command_bot_open_id = "ou_bot"
    dispatcher.handle_message(parse_message_event(make_message_event(
        chat_type="group", text="@_user_1",
        mentions=[{"key": "@_user_1", "open_id": "ou_bot"}])))
    assert not feishu.replies


def test_non_text_message_gets_notice(config, session, db):
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.handle_message(parse_message_event(make_message_event(
        message_type="image", text="")))
    assert "只支持文字提问" in feishu.replies[0][1]["body"]["elements"][0]["content"]


def test_worker_survives_skill_exception(config, session, db):
    feishu = FakeFeishu()

    class Boom:
        name = "boom"

        def match(self, ctx):
            return True

        def run(self, ctx):
            raise RuntimeError("技能炸了")

    dispatcher = make_dispatcher(config, session, db, skills=[Boom()], feishu=feishu)
    dispatcher.handle_message(parse_message_event(make_message_event()))
    # 不能静默：用户要拿到降级卡片
    assert feishu.replies
    assert "答不上来" in feishu.replies[0][1]["body"]["elements"][0]["content"]


def test_worker_loop_consumes_queue_and_stops(config, session, db):
    feishu = FakeFeishu()
    dispatcher = make_dispatcher(config, session, db, feishu=feishu)
    dispatcher.start()
    try:
        dispatcher.on_message(make_message_event(event_id="ev-loop"))
        for _ in range(100):
            if dispatcher.stats["handled"]:
                break
            import time
            time.sleep(0.02)
        assert dispatcher.stats["handled"] == 1
        assert feishu.replies
    finally:
        dispatcher.stop()


def test_unknown_card_action_is_logged_not_crashing(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    dispatcher.handle_card_action(parse_card_action(make_card_event(value={"action": "nope"})))
    assert dispatcher.stats["handled"] == 0


def test_synthetic_event_from_card_has_no_group_semantics(config, session, db):
    dispatcher = make_dispatcher(config, session, db)
    action = parse_card_action(make_card_event(value={"action": "ask", "question": "追问一下"}))
    event = dispatcher.build_synthetic_event(action, "追问一下")
    assert event.is_group is False
    assert event.session_key == "ou_1:oc_1"
    assert event.message_id == "om_card"


@pytest.mark.parametrize("bad", [
    SimpleNamespace(),                       # 完全空对象
    SimpleNamespace(header=None, event=None),
])
def test_parsers_tolerate_bad_payloads(bad):
    assert parse_message_event(bad) is None
    assert parse_card_action(bad) is None
