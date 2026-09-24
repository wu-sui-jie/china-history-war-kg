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


def test_history_requires_newer_than_ttl(session, db):
    session.record_user("k", None, "很久以前的问题")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 25 * 3600,))
    session.record_user("k", None, "刚才的问题")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["刚才的问题"]


def test_history_truncates_single_long_content(session):
    session.record_user("k", None, "甲" * 5000)
    history = session.history_for_rag("k")
    assert len(history) == 1
    content = history[0]["content"]
    assert len(content) == 4000
    assert content.endswith("……（已截断）")
    assert content.startswith("甲")


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
            for i in range(40):
                store.record_user("k", None, f"第{i}个问题" + "甲" * 300)
            history = store.history_for_rag("k")
            payload = json.dumps(history, ensure_ascii=False).encode("utf-8")
            assert len(payload) <= 4 * 1024
            assert len(history) < 40, "字节预算必须真的截住了，而不是靠条数上限"
            # 从最近往前取：最后一条必须在，最早那条必须不在
            assert "第39个问题" in history[-1]["content"]
            assert all("第0个问题" not in t["content"] for t in history)
        finally:
            db.close()


def test_history_keeps_chronological_order(session):
    session.record_user("k", None, "第一问")
    session.record_assistant("k", bot_message_id="b1", content="第一答",
                             finish_reason="normal")
    session.record_user("k", None, "第二问")
    history = session.history_for_rag("k")
    assert [t["role"] for t in history] == ["user", "assistant", "user"]
    assert [t["content"] for t in history] == ["第一问", "第一答", "第二问"]


def test_history_drops_leading_orphan_assistant(session):
    """历史以提问开头才对模型有意义；孤立的回答轮要被丢掉。"""
    session.record_assistant("k", bot_message_id="b0", content="没有提问的回答",
                             finish_reason="normal")
    session.record_user("k", None, "真问题")
    history = session.history_for_rag("k")
    assert [t["content"] for t in history] == ["真问题"]


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
    session.record_user("ou_a:oc_1", None, "A 的问题")
    session.record_user("ou_a:oc_2", None, "同人别群的问题")
    session.record_user("ou_b:oc_1", None, "别人同群的问题")
    assert [t["content"] for t in session.history_for_rag("ou_a:oc_1")] == ["A 的问题"]


def test_history_max_items_respected(session):
    for i in range(50):
        session.record_user("k", None, f"问题{i}")
    history = session.history_for_rag("k")
    assert len(history) == 40
    assert history[-1]["content"] == "问题49"


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


def test_cleanup_removes_expired_rows(session, db):
    session.touch("k", "ou_a", "oc_1")
    session.record_user("k", None, "旧问题")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 30 * 3600,))
    db.execute("UPDATE sessions SET updated_at = ?", (now_ts() - 30 * 3600,))
    db.execute("INSERT INTO processed_events (event_id, event_type, received_at) "
               "VALUES (?, ?, ?)", ("old", "im.message.receive_v1",
                                    now_ts() - 30 * 3600))
    session.record_user("k2", None, "新问题")

    counts = session.cleanup(events_ttl_hours=24.0)
    assert counts["messages"] == 1
    assert counts["processed_events"] == 1
    assert counts["sessions"] == 1
    assert [t["content"] for t in session.history_for_rag("k2")] == ["新问题"]


def test_cleanup_dry_run_keeps_rows(session, db):
    session.record_user("k", None, "旧问题")
    db.execute("UPDATE messages SET created_at = ?", (now_ts() - 30 * 3600,))
    counts = session.cleanup(events_ttl_hours=24.0, dry_run=True)
    assert counts["messages"] == 1
    assert db.query_one("SELECT COUNT(*) FROM messages")[0] == 1
