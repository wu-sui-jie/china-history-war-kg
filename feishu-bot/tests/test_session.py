"""会话与历史组装的守护用例（开发文档 10.1）。

重点锁住"**总字节预算优先于条数与单条上限**"这条：RAG 的 64KB 请求体上限在参数
校验之前生效，照抄"4000 字符 × 40 条"的历史在中文 UTF-8 下能到 480KB，
真发出去必然 413，而 413 对用户是个说不清的错误。
"""

from __future__ import annotations

import json

from bot.db import now_ts
from bot.session import (HISTORY_ELIGIBLE_REASONS, AssistantTurn, SessionStore,
                         as_rag_history, session_key)


# ---- 会话键 ----


def test_session_key_isolates_user_and_chat():
    assert session_key("ou_a", "oc_1") != session_key("ou_a", "oc_2")
    assert session_key("ou_a", "oc_1") != session_key("ou_b", "oc_1")
    assert session_key("ou_a", "oc_1") == "ou_a:oc_1"


def test_session_key_within_rag_limit():
    key = session_key("ou_" + "a" * 30, "oc_" + "b" * 30)
    assert len(key) <= 128          # RAG 契约的 session_id_max_chars


def test_touch_is_idempotent(session, db):
    session.touch("ou_a:oc_1", "ou_a", "oc_1")
    session.touch("ou_a:oc_1", "ou_a", "oc_1")
    rows = db.query_all("SELECT * FROM sessions")
    assert len(rows) == 1


# ---- 历史组装 ----
#
# 注意：这些用例一律写**成对的 user + assistant 行**，因为真实链路就是这样写的
# （dispatcher._record_turn 只在"能入历史的轮次"里成对写）。只写提问不写回答的
# 数据是旧版本/异常路径的产物，会被 _drop_orphans 清掉——那本身另有一组用例覆盖。


def _pair(store, key, question: str, answer: str = "答", reason: str = "normal") -> None:
    store.record_user(key, None, question)
    store.record_assistant(key, bot_message_id="b", content=answer, finish_reason=reason)


def test_history_requires_newer_than_ttl(session, db):
    _pair(session, "k", "很久以前的问题", "很久以前的回答")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 25 * 3600,))
    _pair(session, "k", "刚才的问题", "刚才的回答")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["刚才的问题", "刚才的回答"]


def test_history_truncates_single_long_content(session):
    _pair(session, "k", "问", "甲" * 5000)
    history = session.history_for_rag("k")
    assert [t["role"] for t in history] == ["user", "assistant"]
    content = history[1]["content"]
    # 默认对回答轮用更严的 history_assistant_max_chars（800）
    assert len(content) == 800
    assert content.endswith("……（已截断）")
    assert content.startswith("甲")


def test_assistant_history_cap_does_not_touch_questions(session):
    """截断只针对回答轮：提问是短文本、且是指代消解的依据，必须原样保留。"""
    long_question = "问" * 1200
    _pair(session, "k", long_question, "答" * 2000)
    history = session.history_for_rag("k")
    assert history[0]["content"] == long_question            # 提问不截
    assert len(history[1]["content"]) == 800                  # 回答截到 800


def test_assistant_history_cap_can_be_disabled():
    """HISTORY_ASSISTANT_MAX_CHARS=0 表示不额外截断（只受 HISTORY_CONTENT_MAX_CHARS 约束）。"""
    import tempfile
    from pathlib import Path

    from bot.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        db.connect()
        try:
            store = SessionStore(db, history_assistant_max_chars=0)
            store.record_user("k", None, "问")
            store.record_assistant("k", bot_message_id="b", content="答" * 2000,
                                   finish_reason="normal")
            assert len(store.history_for_rag("k")[1]["content"]) == 2000
        finally:
            db.close()


def test_history_byte_budget_beats_item_count():
    """条数上限很大、单条也很短时，真正生效的是总字节预算。"""
    import tempfile
    from pathlib import Path

    from bot.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        db.connect()
        try:
            store = SessionStore(db, ttl_hours=24.0, history_max_bytes=4 * 1024,
                                 history_max_items=40, history_content_max_chars=4000)
            for i in range(20):
                _pair(store, "k", f"第{i}个问题" + "甲" * 300, f"第{i}个回答" + "乙" * 300)
            history = store.history_for_rag("k")
            payload = json.dumps(history, ensure_ascii=False).encode("utf-8")
            assert len(payload) <= 4 * 1024
            assert len(history) < 40, "字节预算必须真的截住了，而不是靠条数上限"
            # 从最近往前取：最后一条必须在，最早那条必须不在
            assert "第19个回答" in history[-1]["content"]
            assert all("第0个问题" not in t["content"] for t in history)
        finally:
            db.close()


def test_history_keeps_chronological_order(session):
    _pair(session, "k", "第一问", "第一答")
    _pair(session, "k", "第二问", "第二答")
    history = session.history_for_rag("k")
    assert [t["role"] for t in history] == ["user", "assistant", "user", "assistant"]
    assert [t["content"] for t in history] == ["第一问", "第一答", "第二问", "第二答"]


def test_history_drops_leading_orphan_assistant(session):
    """历史以提问开头才对模型有意义；孤立的回答轮要被丢掉。"""
    session.record_assistant("k", bot_message_id="b0", content="没有提问的回答",
                             finish_reason="normal")
    _pair(session, "k", "真问题", "真回答")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["真问题", "真回答"]


def test_history_drops_trailing_unanswered_question(session):
    """尾部没有回答的提问是孤儿（旧版本/进程被打断留下的），必须丢掉。

    真机踩过：这类行留在历史里会让每次提问变成缓存未命中（历史参与缓存键），
    本该毫秒返回的问题变成 12–16s 的真生成，直接把机器人 25s 预算顶穿。
    """
    session.record_user("k", None, "第一问")
    session.record_assistant("k", bot_message_id="b1", content="第一答",
                             finish_reason="normal")
    session.record_user("k", None, "/help")               # 旧版本写下的命令行
    session.record_user("k", None, "超时那次的提问")        # 旧版本写下的孤儿提问
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["第一问", "第一答"]


def test_history_keeps_only_last_of_consecutive_questions(session):
    """连续提问（旧数据里的中间孤儿）只保留最后一条。"""
    session.record_user("k", None, "孤儿提问A")
    session.record_user("k", None, "孤儿提问B")
    session.record_user("k", None, "真问题")
    session.record_assistant("k", bot_message_id="b1", content="回答",
                             finish_reason="normal")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["真问题", "回答"]


def test_history_all_orphans_yields_empty(session):
    """整段历史都是孤儿时返回空（宁可没有上下文，也不发垃圾给模型）。"""
    session.record_assistant("k", bot_message_id="b", content="孤立的回答",
                             finish_reason="normal")
    session.record_user("k", None, "/help")
    session.record_user("k", None, "没答上的问题")
    assert session.history_for_rag("k") == []


def test_ineligible_finish_reasons_excluded_from_history(session):
    """interrupted / failed / cancelled 不得进入下一轮历史（RAG 契约要求）。

    正常路径压根不写这些终态（dispatcher 会拦），这里直接写库验证查询侧的兜底。
    """
    session.record_user("k", None, "第一问")
    for bad in ("interrupted", "failed", "cancelled"):
        session.record_assistant("k", bot_message_id="b", content=f"半截回答-{bad}",
                                 finish_reason=bad)
    session.record_assistant("k", bot_message_id="b2", content="完整回答",
                             finish_reason="normal")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["第一问", "完整回答"]
    assert "interrupted" not in json.dumps(history, ensure_ascii=False)


def test_ineligible_reasons_not_consuming_item_quota(session):
    """不可入历史的轮次不该白占条数名额（过滤要发生在 SQL 里）。"""
    session.record_user("k", None, "问")
    for _ in range(5):
        session.record_assistant("k", bot_message_id="b", content="半截",
                                 finish_reason="interrupted")
    session.record_assistant("k", bot_message_id="b2", content="好回答",
                             finish_reason="normal")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["问", "好回答"]


def test_history_scoped_per_session(session):
    _pair(session, "ou_a:oc_1", "A 的问题", "A 的回答")
    _pair(session, "ou_a:oc_2", "同人别群的问题", "别群的回答")
    _pair(session, "ou_b:oc_1", "别人同群的问题", "别人的回答")
    assert [t["content"] for t in session.history_for_rag("ou_a:oc_1")] == ["A 的问题", "A 的回答"]


def test_history_max_items_respected(session):
    for i in range(25):
        _pair(session, "k", f"问题{i}", f"回答{i}")
    history = session.history_for_rag("k")
    # 条数上限 40 指的是"消息条数"：25 轮 = 50 条，只保留最近 40 条
    assert len(history) == 40
    assert history[-1]["content"] == "回答24"
    assert history[0]["role"] == "user"      # 从提问开始，不带孤儿回答


# ---- 终态准入 ----


def test_assistant_turn_eligibility():
    for reason in ("normal", "refused", "degraded"):
        assert reason in HISTORY_ELIGIBLE_REASONS
        assert AssistantTurn(content="x", finish_reason=reason).is_history_eligible
    for reason in ("interrupted", "failed", "cancelled", ""):
        assert not AssistantTurn(content="x", finish_reason=reason).is_history_eligible


# ---- 请求形状 ----


def test_as_rag_history_shape(session):
    session.record_user("k", None, "问")
    session.record_assistant("k", bot_message_id="b", content="答", finish_reason="normal")
    rows = as_rag_history(session.history_for_rag("k"))
    assert rows == [{"role": "user", "content": "问"},
                    {"role": "assistant", "content": "答"}]
    assert set(rows[0]) == {"role", "content"}


# ---- 反馈取数 ----


def test_question_before_assistant_uses_insertion_order(session, db):
    """同一秒内的两条提问也要能定序，否则工单里的问题会串台。"""
    session.record_user("k", None, "第一问")
    aid1 = session.record_assistant("k", bot_message_id="b1", content="答1",
                                    finish_reason="normal")
    session.record_user("k", None, "第二问")
    aid2 = session.record_assistant("k", bot_message_id="b2", content="答2",
                                    finish_reason="normal")
    assert session.question_before_assistant("k", aid1) == "第一问"
    assert session.question_before_assistant("k", aid2) == "第二问"


def test_feedback_roundtrip(session):
    fid = session.record_feedback(open_id="ou_a", key="k", message_id="om_1",
                                  question="问", answer_md="答",
                                  citations=[{"index": 1, "title": "t"}])
    row = session.get_feedback(fid)
    assert row["status"] == "open"
    assert json.loads(row["citations"])[0]["title"] == "t"
    assert session.find_feedback(open_id="ou_a", bot_message_id="om_1")["id"] == fid
    assert session.find_feedback(open_id="ou_b", bot_message_id="om_1") is None
    assert session.find_feedback(open_id="ou_a", bot_message_id=None) is None


# ---- 清理 ----


def test_delete_messages_removes_only_given_rows(session, db):
    """发送失败撤回本轮：两行一起删，别的轮次不受影响。"""
    _pair(session, "k", "保留下来的问题", "保留下来的回答")
    user_id = session.record_user("k", None, "没发出去的问题")
    assistant_id = session.record_assistant("k", bot_message_id=None, content="没发出去的回答",
                                            finish_reason="normal")

    assert session.delete_messages([user_id, assistant_id]) == 2
    assert [t["content"] for t in session.history_for_rag("k")] == ["保留下来的问题",
                                                                  "保留下来的回答"]
    # 幂等：重复撤回不会误删别的行（id 已被删掉）
    assert session.delete_messages([user_id, assistant_id]) == 0
    assert session.delete_messages([None, 0]) == 0      # 无 id 时不发 SQL


def test_reset_clears_messages_and_session_but_keeps_feedback(session, db):
    """`/new` 的口径：上下文清空，纠错记录保留。"""
    session.touch("k", "ou_a", "oc_1")
    _pair(session, "k", "旧问题", "旧回答")
    _pair(session, "k2", "别人的问题", "别人的回答")
    session.record_feedback(open_id="ou_a", key="k", message_id="om_1",
                            question="旧问题", answer_md="旧回答")

    counts = session.reset("k")

    assert counts == {"messages": 2, "sessions": 1}
    assert session.history_for_rag("k") == []
    assert db.query_one("SELECT COUNT(*) FROM sessions WHERE session_key = ?", ("k",))[0] == 0
    # 别的会话不受影响；反馈是治理队列，不随重置消失
    assert [t["content"] for t in session.history_for_rag("k2")] == ["别人的问题", "别人的回答"]
    assert db.query_one("SELECT COUNT(*) FROM feedback")[0] == 1


def test_reset_on_unknown_session_is_a_noop(session):
    assert session.reset("nobody:nowhere") == {"messages": 0, "sessions": 0}


def test_cleanup_removes_expired_rows(session, db):
    session.touch("k", "ou_a", "oc_1")
    _pair(session, "k", "旧问题", "旧回答")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 30 * 3600,))
    db.execute("UPDATE sessions SET updated_at = ?", (now_ts() - 30 * 3600,))
    db.execute("INSERT INTO processed_events (event_id, event_type, received_at) "
               "VALUES (?, ?, ?)", ("old", "im.message.receive_v1",
                                    now_ts() - 30 * 3600))
    _pair(session, "k2", "新问题", "新回答")

    counts = session.cleanup(events_ttl_hours=24.0)
    assert counts["messages"] == 2          # 旧问题 + 旧回答
    assert counts["processed_events"] == 1
    assert counts["sessions"] == 1
    assert [t["content"] for t in session.history_for_rag("k2")] == ["新问题", "新回答"]


def test_cleanup_dry_run_keeps_rows(session, db):
    session.record_user("k", None, "旧问题")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 30 * 3600,))
    counts = session.cleanup(events_ttl_hours=24.0, dry_run=True)
    assert counts["messages"] == 1
    assert db.query_one("SELECT COUNT(*) FROM messages")[0] == 1
