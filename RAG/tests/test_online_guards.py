"""在线链路的正确性与边界守护用例（2026-09-15 全项目复核整改）。

覆盖第三轮/第四轮报告里最容易回归的几条：
- P0-1 同步检索不再冻结事件循环（并发请求互不阻塞）；
- P0-2 请求体/字段超限在调用外部服务前被拒（契约层报错、API 层 4xx）；
- P0-3 原始 reasoning 默认不外发到公共 SSE；
- P1-1 客户端断连后生成任务被取消回收；
- P1-4 回答缓存有容量上限与过期清扫；
- P1-3 限流器 key 表有界、并发原子、XFF 默认不信任；
- P1-5 正文已流出后不再透明重试（避免拼接两段答案）。

不依赖 data/ 下的快照与索引：全部使用替身运行时与替身检索。
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from config.settings import Settings, get_settings
from contracts.request import QueryRequest, RequestValidationError
from contracts.sse import FinishReason
from server.generate.cache import AnswerCache
from server.generate.llm_client import LLMClient, LLMResponse


# ---------------------------------------------------------------- 替身运行时
class _FakeEntities:
    def __init__(self):
        self.entities = []
        self.candidates = []
        self.question_type = None
        self.rewritten_question = "改写后的问题"
        self.dynasty_bias = []
        self.filters = None

    def to_dict(self):
        return {}


class _FakeUnderstanding:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def trim_history(self, history):
        return list(history or [])

    def understand(self, question, history=None, corrected=None, filters=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)          # 模拟同步阻塞（jieba/LLM 兜底）
        return _FakeEntities()


class _FakeGraphResult:
    def __init__(self):
        self.evidence = []
        self.hit_entities = []
        self.related_event_ids = []
        self.neighbors = {}


class _FakeTextResult:
    def __init__(self):
        from contracts.retrieval import TextResult
        self._inner = TextResult(evidence=[], mode="keyword", vector_available=False)

    def __getattr__(self, item):
        return getattr(self._inner, item)


def _evidence() -> list:
    """一条最小可用证据：没有它 run_query 会走"无证据 → 拒答"短路，测不到生成段。"""
    from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType

    return [Evidence(
        evidence_id="text_1", kind=EvidenceKind.RAW_TEXT,
        source_type=SourceType.ORIGINAL_TEXT, source_version="test",
        confidence=Confidence.MEDIUM, content={"text": "长平之战中白起大破赵军。"},
        related_entities=[], score=1.0, citation_index=1,
    )]


class _FakeFusionOutput:
    def __init__(self):
        self.evidence = _evidence()
        self.conflicts = []
        self.citation_index = []


class _FakeFusion:
    def assemble(self, graph, text, qtype, limit=None):
        return _FakeFusionOutput(), {"entity_cards": [], "subgraph": {"nodes": [], "edges": []},
                                     "timeline": {"groups": []}, "map_points": []}


class _FakeLLM:
    def __init__(self, available: bool = True):
        self.available = available


class _FakeGenerator:
    """可按需阻塞/取消的生成器替身，接口与 AnswerGenerator 对齐。"""

    def __init__(self, hold: bool = False, delta: str = "答案正文"):
        self.llm = _FakeLLM()
        self.cache = AnswerCache(None, ttl=60)
        self.hold = hold
        self.delta = delta
        self.started = asyncio.Event()
        self.cancelled = False
        self.finished = False

    def check_cache(self, rewritten, history, filters, entities=None,
                    corrections=None, dynasty_bias=None):
        return None, "key"

    async def generate(self, question, rewritten, evidence, history=None, filters=None,
                       on_delta=None, on_thinking=None, stats_out=None):
        self.started.set()
        if on_thinking:
            on_thinking("模型内部推理过程（不应外发）")
        if on_delta and self.delta:
            on_delta(self.delta)
        try:
            if self.hold:
                await asyncio.Event().wait()      # 永不返回，等被取消
            self.finished = True
            return FinishReason.NORMAL.value, "fake", self.delta
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    @staticmethod
    def build_citations(evidence):
        return []


class _FakeRuntime:
    def __init__(self, settings, *, understand_delay=0.0,
                 graph_evidence=None, hold_generation=False, generator=None):
        self.settings = settings
        self.version = "test_v1"
        self.index_dir = None
        self.snapshot_dir = None
        self.question = _FakeUnderstanding(delay=understand_delay)
        self.graph = object()
        self.text = object()
        self.fusion = _FakeFusion()
        self.generate = generator or _FakeGenerator(hold=hold_generation)
        self._graph_evidence = graph_evidence or []
        self.text_delay = 0.0

    def shutdown(self):
        pass


def _settings(**overrides) -> Settings:
    s = get_settings()
    s.llm_base_url = ""
    s.llm_api_key = ""
    s.fallback_llm_base_url = ""
    s.fallback_llm_api_key = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


async def _collect(runtime, question="测试问题", limit=None):
    """跑一遍 run_query，返回解析后的事件列表。"""
    from server.sse import run_query

    req = QueryRequest.from_dict({"session_id": "sid", "question": question})
    events = []
    async for frame in run_query(runtime, req):
        assert frame.startswith("data: ")
        events.append(json.loads(frame[len("data: "):]))
        if limit and len(events) >= limit:
            break
    return events


def _patch_searches(monkeypatch, graph_evidence=None, text_delay=0.0):
    """把 F03/F04 检索替换为可控替身（run_query 在函数内 import，patch 模块属性即可）。"""
    import server.graph
    import server.text

    def fake_graph_search(graph, names, qtype, filters=None, top_k=None, dynasty_bias=None):
        return _FakeGraphResult()

    def fake_text_search(searcher, query, filters=None, mode="keyword", top_k=None,
                         keyword_mode="and_or", dynasty_bias=None,
                         hybrid_strategy="weighted", hybrid_keyword_weight=0.5):
        if text_delay:
            time.sleep(text_delay)
        return _FakeTextResult()

    monkeypatch.setattr(server.graph, "search", fake_graph_search)
    monkeypatch.setattr(server.text, "search", fake_text_search)


# ---------------------------------------------------------------- P0-1 事件循环
def test_slow_sync_search_does_not_block_event_loop(monkeypatch):
    """同步检索在 0.3 s 阻塞期间，事件循环仍能推进协程（旧实现会完全冻结）。"""
    _patch_searches(monkeypatch)
    rt = _FakeRuntime(_settings(), understand_delay=0.3)

    async def main():
        ticks = {"n": 0}

        async def ticker():
            while True:
                await asyncio.sleep(0.01)
                ticks["n"] += 1

        task = asyncio.create_task(ticker())
        try:
            await _collect(rt)
        finally:
            task.cancel()
        return ticks["n"]

    ticks = asyncio.run(main())
    # 阻塞 0.3 s + 线程切换：阻塞式实现里 ticks 会停在 0～1
    assert ticks >= 10, f"事件循环被同步理解调用阻塞（ticks={ticks}）"


# ---------------------------------------------------------------- P0-3 reasoning
def test_reasoning_not_exposed_by_default(monkeypatch):
    _patch_searches(monkeypatch)
    rt = _FakeRuntime(_settings(), generator=_FakeGenerator(delta="回答"))
    events = asyncio.run(_collect(rt))
    assert not [e for e in events if e["type"] == "thinking"]
    done = [e for e in events if e["type"] == "done"][-1]
    # 度量仍然可见：思考帧数与首思考时延（不含内容）
    assert done["data"]["thinking_frames"] == 1
    assert done["data"]["first_thinking_ms"] is not None


def test_reasoning_exposed_when_enabled(monkeypatch):
    _patch_searches(monkeypatch)
    rt = _FakeRuntime(_settings(expose_thinking=True),
                      generator=_FakeGenerator(delta="回答"))
    events = asyncio.run(_collect(rt))
    thinking = [e for e in events if e["type"] == "thinking"]
    assert len(thinking) == 1
    assert "不应外发" in thinking[0]["data"]["delta"]


# ---------------------------------------------------------------- P1-1 取消回收
def test_disconnect_cancels_generation_task(monkeypatch):
    """消费方在生成中途停止迭代 → 生成任务必须被取消（不再空烧 token）。"""
    _patch_searches(monkeypatch)
    gen = _FakeGenerator(hold=True, delta="半截回答")
    rt = _FakeRuntime(_settings(), generator=gen)

    async def main():
        from server.sse import run_query

        req = QueryRequest.from_dict({"session_id": "sid", "question": "测试问题"})
        agen = run_query(rt, req)
        async for frame in agen:
            payload = json.loads(frame[len("data: "):])
            if payload["type"] == "answer":
                break                        # 模拟浏览器断连
        await agen.aclose()
        await asyncio.sleep(0.05)
        return gen

    gen = asyncio.run(main())
    assert gen.cancelled is True
    assert gen.finished is False


# ---------------------------------------------------------------- P1-5 流式重试
def test_no_retry_after_content_emitted(monkeypatch):
    """正文已流出后失败：不得重试，也不得切换备用模型（避免拼出重复答案）。"""
    s = _settings()
    s.llm_base_url = "https://example.invalid/v1"
    s.llm_api_key = "test-key"
    s.llm_max_retries = 2
    client = LLMClient(s)
    attempts = {"n": 0}

    async def fake_stream_once(cli, model, messages, on_delta, degraded,
                               on_thinking=None, max_tokens=None, collected=None):
        attempts["n"] += 1
        on_delta("已经流出的半截正文")
        if collected is not None:
            collected.append("已经流出的半截正文")
        raise RuntimeError("connection reset")

    monkeypatch.setattr(client, "_stream_once", fake_stream_once)
    resp = asyncio.run(client.stream_chat([{"role": "user", "content": "hi"}],
                                          on_delta=lambda _: None))
    assert attempts["n"] == 1, "已输出正文仍触发了重试"
    assert resp.partial is True
    assert resp.text == "已经流出的半截正文"
    assert resp.error


def test_no_content_emitted_still_retries(monkeypatch):
    """尚未输出任何正文时，重试策略保持不变（主模型 3 次尝试）。"""
    s = _settings()
    s.llm_base_url = "https://example.invalid/v1"
    s.llm_api_key = "test-key"
    s.llm_max_retries = 2
    client = LLMClient(s)
    attempts = {"n": 0}

    async def fake_stream_once(cli, model, messages, on_delta, degraded,
                               on_thinking=None, max_tokens=None, collected=None):
        attempts["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(client, "_stream_once", fake_stream_once)
    resp = asyncio.run(client.stream_chat([{"role": "user", "content": "hi"}],
                                          on_delta=lambda _: None))
    assert attempts["n"] == 3
    assert resp.partial is False


def test_generator_marks_partial_as_interrupted():
    """部分正文已送达的失败 → finish_reason=interrupted，且不再叠加启发式答案。"""
    from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType
    from server.generate import AnswerGenerator

    gen = AnswerGenerator(_settings(), None, "test_v1")
    emitted: list[str] = []

    class _PartialLLM:
        available = True

        async def stream_chat(self, messages, on_delta, on_thinking=None,
                              max_tokens=None, stats_out=None):
            on_delta("半截回答")
            return LLMResponse(text="半截回答", model_used="fake", partial=True,
                               error="llm_error: boom")

    gen.llm = _PartialLLM()
    evidence = [Evidence(
        evidence_id="text_1", kind=EvidenceKind.RAW_TEXT,
        source_type=SourceType.ORIGINAL_TEXT, source_version="test",
        confidence=Confidence.MEDIUM, content={"text": "长平之战中白起大破赵军。"},
        related_entities=[], score=1.0, citation_index=1,
    )]
    reason, model_used, answer = asyncio.run(gen.generate(
        "测试问题", "测试问题", evidence,
        on_delta=lambda t: emitted.append(t),
    ))
    assert reason == FinishReason.INTERRUPTED.value
    assert answer == "半截回答"
    # 只回调过一次（部分是流式过程中发出的），没有追加"离线摘要"第二段
    assert emitted == ["半截回答"]


# ---------------------------------------------------------------- P0-2 请求边界
@pytest.mark.parametrize("payload,keyword", [
    ({"session_id": "s", "question": "q" * 501}, "question"),
    ({"session_id": "s" * 129, "question": "q"}, "session_id"),
    ({"session_id": "s", "question": "q",
      "history": [{"role": "user", "content": "x"}] * 41}, "history"),
    ({"session_id": "s", "question": "q",
      "history": [{"role": "sys", "content": "x"}]}, "role"),
    ({"session_id": "s", "question": "q",
      "history": [{"role": "user", "content": "x" * 4001}]}, "content"),
    ({"session_id": "s", "question": "q", "filters": {"dynasty": ["战"] * 21}}, "dynasty"),
    ({"session_id": "s", "question": "q",
      "corrected_entities": [{"action": "add", "name": "白起"}] * 21}, "corrected_entities"),
    ({"session_id": "s", "question": "q",
      "corrected_entities": [{"action": "delete"}]}, "action"),
    ({"session_id": "s", "question": "q", "filters": {"dynasty": "战国"}}, "filters"),
    ({"session_id": "", "question": "q"}, "session_id"),
    ({"session_id": "s", "question": "   "}, "question"),
])
def test_request_limits_reject_before_external_calls(payload, keyword):
    with pytest.raises(RequestValidationError) as err:
        QueryRequest.from_dict(payload)
    assert keyword in str(err.value)


def test_request_allows_normal_payload():
    q = QueryRequest.from_dict({
        "session_id": "s1",
        "question": "介绍一下长平之战。",
        "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        "filters": {"dynasty": ["战国"], "event_type": []},
        "corrected_entities": [{"action": "add", "entity_type": "人物", "name": "白起"}],
        "unknown_field": 1,          # 额外字段忽略而不是报错
    })
    assert q.session_id == "s1"
    assert len(q.history) == 2
    assert q.filters.dynasty == ["战国"]
    assert q.corrected_entities[0].name == "白起"


# ---------------------------------------------------------------- API 4xx
def _api_client():
    """构造 ASGI 测试客户端；每次调用重置限流器（app.state 在测试间是共享的）。"""
    from httpx import ASGITransport, AsyncClient

    from server import api as api_mod

    api_mod.app.state.settings = _settings(request_max_bytes=2048)
    api_mod.app.state.runtime = _FakeRuntime(api_mod.app.state.settings)
    api_mod.app.state.load_error = None
    api_mod.app.state.rate_limiter = api_mod.RateLimiter(1000, max_keys=64)
    transport = ASGITransport(app=api_mod.app)
    return AsyncClient(transport=transport, base_url="http://test"), api_mod


def test_api_rejects_oversized_body_and_bad_request():
    async def main():
        client, _ = _api_client()
        async with client:
            big = await client.post("/api/query", content=b"x" * 4096,
                                    headers={"content-type": "application/json"})
            bad = await client.post("/api/query", json={"session_id": "s"},
                                    headers={"content-type": "application/json"})
            long_q = await client.post("/api/query",
                                       json={"session_id": "s", "question": "q" * 501})
            return big, bad, long_q

    big, bad, long_q = asyncio.run(main())
    assert big.status_code == 413
    assert big.json()["error_code"] == "payload_too_large"
    assert bad.status_code == 400
    assert bad.json()["error_code"] == "invalid_request"
    assert long_q.status_code == 400


def test_api_rate_limit_returns_429():
    async def main():
        client, api_mod = _api_client()
        api_mod.app.state.rate_limiter = api_mod.RateLimiter(2, max_keys=64)
        async with client:
            codes = []
            for _ in range(3):
                resp = await client.post("/api/query",
                                         json={"session_id": "s", "question": "q"})
                codes.append(resp.status_code)
                await resp.aclose()
            return codes

    codes = asyncio.run(main())
    assert codes[:2] == [200, 200]
    assert codes[2] == 429


def test_heartbeat_wrapper_cancels_generation_on_close(monkeypatch):
    """API 心跳包装器被关闭（客户端断连时 Starlette 的行为）→ 内层生成被取消。"""
    _patch_searches(monkeypatch)
    gen = _FakeGenerator(hold=True, delta="半截回答")
    rt = _FakeRuntime(_settings(sse_heartbeat_seconds=0.05, sse_max_duration_seconds=30),
                      generator=gen)

    async def main():
        from server.api import _stream_with_heartbeat

        req = QueryRequest.from_dict({"session_id": "s", "question": "介绍一下长平之战。"})
        agen = _stream_with_heartbeat(rt, req, rt.settings)
        saw_answer = False
        async for frame in agen:
            if '"answer"' in frame:
                saw_answer = True
                break
        await agen.aclose()
        await asyncio.sleep(0.05)
        return saw_answer

    assert asyncio.run(main()) is True
    assert gen.cancelled is True
    assert gen.finished is False


def test_heartbeat_wrapper_emits_ping_while_idle(monkeypatch):
    """检索/生成长时间无事件时，心跳注释行保证连接不被反代掐断。"""
    _patch_searches(monkeypatch)
    gen = _FakeGenerator(hold=True, delta="")      # 不出正文，持续阻塞
    rt = _FakeRuntime(_settings(sse_heartbeat_seconds=0.05, sse_max_duration_seconds=30),
                      generator=gen)

    async def main():
        from server.api import _stream_with_heartbeat

        req = QueryRequest.from_dict({"session_id": "s", "question": "介绍一下长平之战。"})
        agen = _stream_with_heartbeat(rt, req, rt.settings)
        pings = 0
        async for frame in agen:
            if frame.startswith(":"):
                pings += 1
                if pings >= 2:
                    break
        await agen.aclose()
        return pings

    assert asyncio.run(main()) >= 2


def test_api_query_stream_ends_with_done(monkeypatch):
    """正常路径：SSE 事件以 done 收尾，且心跳/头不影响事件解析。"""
    _patch_searches(monkeypatch)

    async def main():
        client, _ = _api_client()
        async with client:
            async with client.stream("POST", "/api/query",
                                     json={"session_id": "s", "question": "介绍一下长平之战。"}) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                body = "".join([chunk async for chunk in r.aiter_text()])
                return body

    body = asyncio.run(main())
    types = [json.loads(line[len("data: "):])["type"]
             for line in body.splitlines()
             if line.startswith("data: ")]
    assert types[0] == "session_start"
    assert types[-1] == "done"


# ---------------------------------------------------------------- P1-4 缓存容量
def test_answer_cache_evicts_oldest_beyond_capacity():
    cache = AnswerCache(None, ttl=60, max_entries=3)
    for i in range(5):
        cache.put(f"k{i}", {"answer": i})
    stats = cache.stats()
    assert stats["entries"] <= 3
    assert stats["evictions"] >= 2
    assert cache.get("k0") is None       # 最旧被淘汰
    assert cache.get("k4") is not None


def test_answer_cache_sweeps_expired_on_write():
    cache = AnswerCache(None, ttl=0.01, max_entries=100)
    cache.put("old", {"answer": 1})
    time.sleep(0.05)
    for i in range(cache._SWEEP_EVERY):
        cache.put(f"new{i}", {"answer": i})
    assert cache.get("old") is None


# ---------------------------------------------------------------- P1-3 限流器
def test_rate_limiter_is_bounded_and_counts_rejections():
    from server.api import RateLimiter

    limiter = RateLimiter(1, max_keys=16)
    for i in range(200):
        limiter.allow(f"key-{i}")
    stats = limiter.stats()
    assert stats["keys"] <= 16, "限流 key 表无界增长"
    # 第四轮复核 P1-4：表满后不再淘汰**窗口内仍活跃**的 key（那会重置配额），
    # 改为拒绝新来源 —— 计数体现在 rejected_new_keys 上。
    assert stats["rejected_new_keys"] > 0


def test_rate_limiter_full_table_does_not_reset_active_quota():
    """表满时不得淘汰仍在窗口内的 key：被淘汰 key 再来访问时配额必须仍然耗尽。"""
    from server.api import RateLimiter

    limiter = RateLimiter(2, max_keys=16)
    victim = "10.0.0.1"
    assert limiter.allow(victim) is True
    assert limiter.allow(victim) is True
    assert limiter.allow(victim) is False          # 配额用尽

    # 用新 key 灌满表（这些 key 都在窗口内，属于"活跃"）
    for i in range(40):
        limiter.allow(f"10.9.9.{i}")

    # 旧 key 的窗口内配额不能被淘汰重置
    assert limiter.allow(victim) is False


def test_rate_limiter_rejects_over_quota():
    from server.api import RateLimiter

    limiter = RateLimiter(3, max_keys=16)
    assert [limiter.allow("a") for _ in range(3)] == [True, True, True]
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True     # 不同来源互不影响
    assert limiter.stats()["rejected"] == 1


# ---------------------------------------------------------------- P0-7 版本固定
def test_active_version_pin_is_enforced(tmp_path):
    """显式配置的版本不存在时必须报错，而不是静默回退到最新目录。"""
    from server.runtime import resolve_version

    s = _settings()
    s.snapshot_dir = tmp_path / "snapshot"
    s.index_dir = tmp_path / "index"
    (s.snapshot_dir / "20260101_v1").mkdir(parents=True)
    (s.index_dir / "20260101_v1").mkdir(parents=True)
    s.active_version = "20260101_v1"
    version, _snap, _idx = resolve_version(s)
    assert version == "20260101_v1"

    s.active_version = "20260909_v9"
    with pytest.raises(FileNotFoundError):
        resolve_version(s)


def test_release_artifacts_hash_key_files(tmp_path):
    """发布指纹：manifest / 向量 ids 的哈希与 Git commit 能取到（缺失时为空串不抛错）。"""
    from lib import release_info

    snap = tmp_path / "snapshot" / "20260101_v1"
    idx = tmp_path / "index" / "20260101_v1" / "vectors"
    snap.mkdir(parents=True)
    idx.mkdir(parents=True)
    (snap / "manifest.json").write_text('{"version":"20260101_v1"}', encoding="utf-8")
    (idx.parent / "manifest.json").write_text('{"source_snapshot":"20260101_v1"}', encoding="utf-8")
    (idx / "ids.json").write_text('["a","b"]', encoding="utf-8")

    info = release_info.version_artifacts(snap, idx.parent)
    assert len(info["snapshot_manifest_sha256"]) == 64
    assert len(info["index_manifest_sha256"]) == 64
    assert len(info["vector_ids_sha256"]) == 64
    # 同一内容哈希稳定；不同内容哈希不同
    assert release_info.file_sha256(snap / "manifest.json") == info["snapshot_manifest_sha256"]
    assert release_info.file_sha256(tmp_path / "missing.json") == ""


def test_env_example_keys_are_wired():
    """`.env.example` 里写出的键必须真的被 config 读取（防拼错、防文档与实现脱节）。

    这是 CI 清单里"`.env.example` 与 Settings 差异"检查的最小可用版本。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    example = (root / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{2,})=", example, flags=re.MULTILINE))
    assert keys, "未能从 .env.example 解析出任何配置键"

    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [root / "config" / "settings.py", root / "config" / "defaults.py"]
    )
    # 密钥别名链与平台注入键不属于 config 直接 os.environ.get 的键名
    aliases = {
        "LLM_API_KEY", "DEEPSEEK_API_KEY", "RAG-command", "RAG-deepseek-v4",
        "FALLBACK_LLM_API_KEY", "EMBEDDING_API_KEY", "DASHSCOPE_API_KEY",
    }
    missing = sorted(k for k in keys if k not in sources and k not in aliases)
    assert not missing, f".env.example 中的键未在 config 中被读取：{missing}"


def test_config_validation_fails_fast():
    s = _settings()
    s.text_mode = "hybird"          # 拼错
    with pytest.raises(ValueError) as err:
        s.validate()
    assert "TEXT_MODE" in str(err.value)

    s = _settings()
    s.rate_limit_per_minute = 0
    with pytest.raises(ValueError):
        s.validate()


# ------------------------------------------- XFF 取哪一段（第 14 轮审计 P1-3）


def test_限流来源取_X_Real_IP_而不是_XFF_首段():
    """nginx 用 `$proxy_add_x_forwarded_for`：**客户端自带的 XFF 在最前**，真实地址追加在后。

    原实现取首段 → 攻击者每次换一个伪造值就换一个限流桶（IP 维度形同不存在）。
    现在优先用 nginx 覆盖下发的 `X-Real-IP`。
    """
    from starlette.requests import Request

    from server.api import _client_key

    settings = SimpleNamespace(rate_limit_trust_forwarded_for=True,
                               rate_limit_trusted_proxies=["127.0.0.1"])
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))

    def key(headers: dict[str, str]) -> str:
        # 对端必须是**可信代理**（同机 nginx 就是 127.0.0.1），否则转发头一律忽略
        scope = {"type": "http", "app": app, "client": ("127.0.0.1", 1234), "headers": [
            (name.lower().encode(), value.encode()) for name, value in headers.items()]}
        return _client_key(Request(scope))

    real = "203.0.113.7"
    # 同一个真实来源、三次不同伪造首段 → 必须是同一个桶
    keys = {
        key({"x-forwarded-for": f"10.0.0.{i}, {real}", "x-real-ip": real})
        for i in range(3)
    }
    assert keys == {real}, f"伪造的首段把限流桶切碎了：{keys}"

    # 退路：没有 X-Real-IP 时取**最右段**
    assert key({"x-forwarded-for": f"9.9.9.9, 5.6.7.8, {real}"}) == real


def test_未显式信任反代时任何转发头都不采信():
    """默认档位下 XFF / X-Real-IP 都是客户端自己写的，一律忽略（客户端地址为准）。"""
    from starlette.requests import Request

    from server.api import _client_key

    settings = SimpleNamespace(rate_limit_trust_forwarded_for=False,
                               rate_limit_trusted_proxies=[])
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    scope = {"type": "http", "app": app, "client": ("192.0.2.5", 1234), "headers": [
        (b"x-forwarded-for", b"1.2.3.4"), (b"x-real-ip", b"1.2.3.4")]}

    assert _client_key(Request(scope)) == "192.0.2.5"


def test_对端不在可信代理列表时忽略转发头():
    """只有**可信代理**传来的转发头才认：直连方自己带的一律忽略。"""
    from starlette.requests import Request

    from server.api import _client_key

    settings = SimpleNamespace(rate_limit_trust_forwarded_for=True,
                               rate_limit_trusted_proxies=["127.0.0.1"])
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    scope = {"type": "http", "app": app, "client": ("203.0.113.99", 1234), "headers": [
        (b"x-real-ip", b"1.2.3.4")]}

    assert _client_key(Request(scope)) == "203.0.113.99"
