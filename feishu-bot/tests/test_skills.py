"""技能层守护用例（开发文档 10.1 的 `skills` 行）。

三条最要紧的口径：
1. 分流规则命中顺序（/help 命令优先，knowledge_qa 兜底）；
2. 降级卡片的触发条件（超时 / 5xx / 错误码映射）；
3. **413 绝不说"问题过长"**——那是机器人侧历史组装的问题，说成用户的错会把人带偏。
"""

from __future__ import annotations

import httpx
import pytest

from bot.cards.builder import TRUNCATED_NOTICE
from bot.rag_client import DemoExamplesCache, RagClient, RagError
from bot.skills.base import SkillContext
from bot.skills.help import HelpSkill
from bot.skills.knowledge_qa import KnowledgeQaSkill
from bot.skills.report_error import ReportErrorSkill

from card_helpers import button_values
from fake_rag import Case, FakeRag


class FakeFeishu:
    def __init__(self):
        self.uploaded: list[str] = []
        self.sent: list[tuple[str, dict]] = []

    def upload_image(self, path):
        self.uploaded.append(str(path))
        return f"img-{len(self.uploaded)}"

    def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return "sent-1"


def make_ctx(question: str = "介绍一下长平之战", history=None, event=None) -> SkillContext:
    from types import SimpleNamespace

    message_event = event or SimpleNamespace(
        event_id="ev", message_id="om_1", chat_id="oc_1", chat_type="p2p",
        open_id="ou_1", text=question, message_type="text", mentions=[],
        session_key="ou_1:oc_1")
    return SkillContext(event=message_event, question=question,
                        session_key="ou_1:oc_1", history=history or [])


@pytest.fixture
def live_skill(config, session):
    """接在假 RAG 上的 knowledge_qa（真发 HTTP，覆盖客户端到技能全链）。"""
    created: list[tuple[FakeRag, RagClient]] = []

    def factory(**fake_kwargs) -> KnowledgeQaSkill:
        fake = FakeRag(**fake_kwargs).start()
        rag = RagClient(fake.base_url, query_timeout=5.0, connect_timeout=2.0)
        rag.fake = fake                     # 测试专用：供断言请求体/请求头
        created.append((fake, rag))
        return KnowledgeQaSkill(rag=rag, session=session, config=config)

    yield factory
    for fake, rag in reversed(created):
        rag.close()
        fake.stop()


def _fake_of(skill: KnowledgeQaSkill) -> FakeRag:
    return skill.rag.fake


# ---- 分流顺序 ----


def test_help_command_matches_before_knowledge_qa():
    from bot.skills.base import SkillRegistry

    class NeverMatched:
        name = "never"

        def match(self, ctx):
            raise AssertionError("兜底技能不该在 /help 之前被调用")

    registry = SkillRegistry([HelpSkill(), NeverMatched()])
    reply = registry.run(make_ctx("/help"))
    assert reply is not None and reply.kind == "card"


def test_knowledge_qa_matches_everything_else(config, session, live_skill):
    skill = live_skill()
    assert skill.match(make_ctx("随便问点什么")) is True


def test_report_error_never_matches_text(config, session):
    skill = ReportErrorSkill(session=session, feishu=FakeFeishu(), config=config)
    assert skill.match(make_ctx("我要反馈")) is False
    assert skill.card_action == "report_error"


def test_help_recognises_variants():
    skill = HelpSkill()
    for text in ("/help", "/HELP", "/help 我该问什么", "/帮助", "/帮助 长平之战", "/?"):
        assert skill.match(make_ctx(text)) is True
    for text in ("help", "请帮忙", "/helpx", "/帮帮忙"):
        assert skill.match(make_ctx(text)) is False


# ---- 正常回答 ----


def test_normal_answer_card_and_turn(config, session, live_skill):
    skill = live_skill()
    reply = skill.run(make_ctx())
    assert reply.kind == "card"
    text = reply.card["body"]["elements"][0]["content"]
    assert "**长平之战**" in text                 # 标题已收敛
    # 引用折叠区存在
    titles = [e["header"]["title"]["content"] for e in reply.card["body"]["elements"]
              if e.get("tag") == "collapsible_panel"]
    assert "引用（1）" in titles
    # 本轮回答要能写入历史
    assert reply.assistant_turn is not None
    assert reply.assistant_turn.finish_reason == "normal"
    assert reply.assistant_turn.content.startswith("# 长平之战")


def test_refused_answer_has_no_citations(config, session, live_skill):
    skill = live_skill(case=Case(answer_md="知识库未检索到相关史料，无法给出有依据的回答。",
                                 citations=[], panel={}, finish_reason="refused"))
    reply = skill.run(make_ctx())
    assert "未检索到相关史料" in reply.card["body"]["elements"][0]["content"]
    assert not [e for e in reply.card["body"]["elements"]
                if e.get("tag") == "collapsible_panel"]
    assert reply.assistant_turn.is_history_eligible is True      # refused 可入历史


def test_truncated_answer_adds_notice(config, session, live_skill):
    skill = live_skill(case=Case(truncated=True))
    reply = skill.run(make_ctx())
    assert TRUNCATED_NOTICE in reply.card["body"]["elements"][0]["content"]


def test_abnormal_finish_reason_degrades_and_skips_history(config, session, live_skill):
    skill = live_skill(case=Case(finish_reason="interrupted"))
    reply = skill.run(make_ctx())
    assert "答不上来" in reply.card["body"]["elements"][0]["content"]
    assert reply.assistant_turn is None            # 不写历史


def test_history_is_passed_to_rag(config, session, live_skill):
    skill = live_skill()
    ctx = make_ctx(history=[{"role": "user", "content": "上一问"},
                            {"role": "assistant", "content": "上一答"}])
    skill.run(ctx)
    fake = _fake_of(skill)
    body = fake.requests[-1]["body"]
    assert body["history"] == [{"role": "user", "content": "上一问"},
                               {"role": "assistant", "content": "上一答"}]
    assert body["session_id"] == "ou_1:oc_1"
    assert body["question"] == "介绍一下长平之战"


# ---- 降级触发条件 ----


def test_timeout_degrades_with_timeout_text(config, session):
    fake = FakeRag(delay=1.0).start()
    try:
        rag = RagClient(fake.base_url, query_timeout=0.2, connect_timeout=1.0)
        skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
        reply = skill.run(make_ctx())
        assert "超时" in reply.card["body"]["elements"][0]["content"]
        assert reply.assistant_turn is None
    finally:
        fake.stop()


@pytest.mark.parametrize("status,code,expect", [
    (503, "internal", "服务暂不可用"),
    (500, "server_busy", "服务暂不可用"),
    (429, "rate_limited", "服务暂不可用"),
    (413, "payload_too_large", "服务异常"),        # 413 走内部口径，绝不说"问题过长"
    (400, "invalid_request", "问题过长"),          # 唯一允许用户侧提示的场景
])
def test_error_code_to_degraded_text(config, session, status, code, expect):
    fake = FakeRag(status=status, error_code=code).start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
        reply = skill.run(make_ctx())
        text = reply.card["body"]["elements"][0]["content"]
        assert expect in text, f"{code} 应显示 {expect}，实际：{text}"
        assert reply.assistant_turn is None
    finally:
        fake.stop()


def test_payload_too_large_never_says_question_too_long(config, session):
    fake = FakeRag(status=413, error_code="payload_too_large",
                   error_message="请求体超过上限 65536 字节").start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
        reply = skill.run(make_ctx(history=[{"role": "user", "content": "甲" * 200}]))
        text = reply.card["body"]["elements"][0]["content"]
        assert "过长" not in text and "精简" not in text
    finally:
        fake.stop()


def test_unreachable_rag_degrades(config, session):
    """传输层失败 → "服务暂不可用"。

    用注入的 httpx 替身而不是连一个不存在的端口：Windows 上"连不上"有时表现为
    连接超时、有时表现为拒绝连接，测试要锁的是**分类结果**而不是操作系统的脾气。
    """
    class RefusingClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    rag = RagClient("http://127.0.0.1:1", query_timeout=1.0, connect_timeout=0.5)
    rag._http = RefusingClient()
    skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
    reply = skill.run(make_ctx())
    assert "服务暂不可用" in reply.card["body"]["elements"][0]["content"]


def test_unexpected_exception_degrades_not_crashes(config, session):
    class Boom:
        def query(self, *a, **k):
            raise ValueError("意料之外")

    skill = KnowledgeQaSkill(rag=Boom(), session=session, config=config)
    reply = skill.run(make_ctx())
    assert "答不上来" in reply.card["body"]["elements"][0]["content"]


def test_bot_key_header_sent_when_configured(config, session):
    fake = FakeRag().start()
    try:
        rag = RagClient(fake.base_url, bot_api_key="k-123", query_timeout=5.0)
        skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
        skill.run(make_ctx())
        assert fake.requests[-1]["headers"].get("X-Bot-Key") == "k-123"
    finally:
        fake.stop()


def test_bot_key_absent_when_not_configured(config, session):
    fake = FakeRag().start()
    try:
        rag = RagClient(fake.base_url, bot_api_key="", query_timeout=5.0)
        skill = KnowledgeQaSkill(rag=rag, session=session, config=config)
        skill.run(make_ctx())
        assert "X-Bot-Key" not in fake.requests[-1]["headers"]
    finally:
        fake.stop()


# ---- 示例问题按钮（P1-3）----


def test_examples_cache_used_for_buttons(config, session):
    fake = FakeRag(examples=["题一", "题二"]).start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        cache = DemoExamplesCache(rag, count=3, refresh_seconds=3600)
        skill = KnowledgeQaSkill(rag=rag, session=session, examples=cache, config=config)
        reply = skill.run(make_ctx())
        values = button_values(reply.card)
        assert {"action": "ask", "question": "题一"} in values
    finally:
        fake.stop()


def test_examples_failure_does_not_break_answer(config, session):
    """示例题接口挂了，问答必须照常（按钮是增强项）。"""
    fake = FakeRag().start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        cache = DemoExamplesCache(rag)
        cache.client = type("Boom", (), {"demo_examples": lambda self: []})()
        skill = KnowledgeQaSkill(rag=rag, session=session, examples=cache, config=config)
        reply = skill.run(make_ctx())
        assert "长平之战" in reply.card["body"]["elements"][0]["content"]
    finally:
        fake.stop()


def test_examples_cache_keeps_last_known_on_failure():
    calls = {"n": 0}

    class Flaky:
        def demo_examples(self):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"question": "题一"}]
            return []

    now = [1000.0]
    cache = DemoExamplesCache(Flaky(), count=3, refresh_seconds=10, clock=lambda: now[0])
    assert cache.questions() == ["题一"]
    now[0] += 20
    assert cache.questions() == ["题一"], "接口抖动不该让按钮区消失"


def test_examples_failure_is_negatively_cached():
    """取题失败也要命中缓存窗口（审查报告 3.1-3）。

    原先的守卫要求"缓存非空"，接口失败时永远不成立——每张卡片都同步重打一次
    `GET /api/demo/examples`，端点不可用时最坏吃满客户端超时，直接叠加在用户等待上。
    """
    calls = {"n": 0}

    class Down:
        def demo_examples(self):
            calls["n"] += 1
            return []

    now = [1000.0]
    cache = DemoExamplesCache(Down(), count=3, refresh_seconds=3600,
                              failure_retry_seconds=300, clock=lambda: now[0])
    assert cache.questions() == []
    assert calls["n"] == 1

    now[0] += 10                            # 窗口内：不再打接口（这就是负缓存）
    assert cache.questions() == []
    assert calls["n"] == 1

    now[0] += 300                          # 过了失败重试窗口：再试一次
    assert cache.questions() == []
    assert calls["n"] == 2


def test_examples_failure_window_never_exceeds_success_window():
    """失败重试窗口比成功刷新窗口更急，但不会被配成比它更长。"""
    cache = DemoExamplesCache(type("X", (), {"demo_examples": lambda self: []})(),
                              refresh_seconds=60, failure_retry_seconds=99999)
    assert cache.failure_retry_seconds == 60


def test_examples_failure_window_applies_with_stale_questions():
    """**留有旧题目时刷新失败，也要按失败窗口重试**（修复审核报告第三节，方案 A）。

    窗口若按"当前有没有题目"取档，这种情形会仍等满一小时——旧按钮还能用，
    但接口恢复了按钮区最多晚 1 小时才更新。
    """
    calls = {"n": 0}

    class Flaky:
        def demo_examples(self):
            calls["n"] += 1
            return [{"question": "题一"}] if calls["n"] == 1 else []

    now = [1000.0]
    cache = DemoExamplesCache(Flaky(), count=3, refresh_seconds=3600,
                              failure_retry_seconds=300, clock=lambda: now[0])
    assert cache.questions() == ["题一"]

    now[0] += 3600                      # 刷新窗口到期，这次失败（旧题目留着）
    assert cache.questions() == ["题一"]
    assert calls["n"] == 2

    now[0] += 10                        # 失败窗口内：不再打接口
    assert cache.questions() == ["题一"]
    assert calls["n"] == 2

    now[0] += 300                       # 过了失败窗口：再试
    assert cache.questions() == ["题一"]
    assert calls["n"] == 3


def test_examples_success_puts_window_back_to_refresh_interval():
    """成功一次后窗口回到刷新间隔，不会被上一次失败拖成"每 5 分钟打一次"。"""

    class Recovering:
        def __init__(self):
            self.n = 0

        def demo_examples(self):
            self.n += 1
            return [] if self.n == 1 else [{"question": "题一"}]

    client = Recovering()
    now = [1000.0]
    cache = DemoExamplesCache(client, count=3, refresh_seconds=3600,
                              failure_retry_seconds=300, clock=lambda: now[0])

    assert cache.questions() == []          # 第一次失败：进负缓存
    now[0] += 300
    assert cache.questions() == ["题一"]     # 到点重试成功
    assert client.n == 2

    now[0] += 300                           # 300s < 3600s：不再打接口
    assert cache.questions() == ["题一"]
    assert client.n == 2


# ---- RAG 客户端错误映射 ----


def test_rag_client_maps_http_errors():
    fake = FakeRag(status=504, error_code="timeout", error_message="超预算").start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        with pytest.raises(RagError) as err:
            rag.query("q", [], "s")
        assert err.value.code == "timeout"
        assert err.value.status == 504
    finally:
        fake.stop()


def test_rag_client_maps_connection_error():
    class RefusingClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    rag = RagClient("http://127.0.0.1:1", query_timeout=1.0, connect_timeout=0.5)
    rag._http = RefusingClient()
    with pytest.raises(RagError) as err:
        rag.query("q", [], "s")
    assert err.value.code == "transport"


def test_rag_client_maps_connect_timeout():
    class SlowClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectTimeout("connect timeout")

    rag = RagClient("http://127.0.0.1:1", query_timeout=1.0, connect_timeout=0.5)
    rag._http = SlowClient()
    with pytest.raises(RagError) as err:
        rag.query("q", [], "s")
    # 超时一律按 timeout 分类（机器人侧据此提示"超时"），不区分连接期还是读取期
    assert err.value.code == "timeout"


def test_rag_client_flags_payload_too_large():
    resp = httpx.Response(413, json={"error_code": "payload_too_large", "message": "too big"})
    rag = RagClient("http://x", query_timeout=5.0)
    err = rag._error_from_response(resp)
    assert err.is_payload_too_large and not err.is_invalid_request


def test_rag_client_requires_status_ok():
    fake = FakeRag().start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        data = rag.query("q", [], "s")
        assert data["finish_reason"] == "normal"
        assert data["_elapsed_ms"] >= 0
    finally:
        fake.stop()


# ---- 非流式接口的存在性探测（旧实例最容易噎住用户的点）----


@pytest.mark.parametrize("status,expected", [
    (400, True),    # 路由存在、空请求体被参数校验拒绝
    (413, True),
    (429, True),
    (404, False),   # 路由不存在
    (405, False),   # 同源托管把未匹配路径交给 StaticFiles，POST 得到 405
    (502, False),   # 反代后的服务不可用
])
def test_supports_json_query_by_status(status, expected):
    class StubClient:
        def post(self, *args, **kwargs):
            return httpx.Response(status, json={})

    rag = RagClient("http://127.0.0.1:8000", query_timeout=5.0)
    rag._http = StubClient()
    assert rag.supports_json_query() is expected


def test_supports_json_query_on_transport_failure():
    class RefusingClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("refused")

    rag = RagClient("http://127.0.0.1:8000", query_timeout=5.0)
    rag._http = RefusingClient()
    assert rag.supports_json_query() is False


def test_supports_json_query_against_real_route():
    """真跑一次：假 RAG 实现了该路由，探测必须为真。"""
    fake = FakeRag().start()
    try:
        rag = RagClient(fake.base_url, query_timeout=5.0)
        assert rag.supports_json_query() is True
    finally:
        fake.stop()
