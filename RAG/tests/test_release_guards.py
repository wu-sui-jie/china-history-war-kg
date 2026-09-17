"""第四轮复核（docs/changes/20260915-round4-review-audit-and-next-optimization.md）的守护用例。

覆盖发布阻断与正确性项：
- P0-1 强制版本检查必须在扫描目录之前生效；
- P0-2 SSE deadline 独立于心跳严格生效，并按是否已送出正文区分 interrupted / failed；
- P0-3 异步客户端在 shutdown 中真的被 await（且单个失败不影响其余）；
- P1-1 缓存键覆盖纠正实体与完整历史；
- P1-2 纠正指令的动作语义校验（非法请求 400，不产生 500 或静默 no-op）；
- P1-4 限流 key 表满时不得淘汰窗口内仍活跃的 key；
- P1-5 同步工作池有界，超出排队上限以 server_busy 拒绝；
- P1-6 演示清单缺少/错误 version 一律拒绝。

全部使用替身运行时，不依赖 data/ 下的快照与索引。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from config.settings import Settings, get_settings
from contracts.request import QueryRequest, RequestValidationError
from contracts.sse import ErrorCode, FinishReason


# ---------------------------------------------------------------- 公共替身
class _FakeEntities:
    entities: list = []
    candidates: list = []
    question_type = None
    rewritten_question = "改写后的问题"
    dynasty_bias: list = []
    filters = None


class _FakeUnderstanding:
    def __init__(self, llm=None):
        self.llm = llm
        self.llm_calls = 0

    def trim_history(self, history):
        return list(history or [])

    def understand(self, question, history=None, corrected=None, filters=None):
        return _FakeEntities()


def _evidence_list():
    from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType

    return [Evidence(
        evidence_id="text_1", kind=EvidenceKind.RAW_TEXT,
        source_type=SourceType.ORIGINAL_TEXT, source_version="test",
        confidence=Confidence.MEDIUM, content={"text": "长平之战中白起大破赵军。"},
        related_entities=[], score=1.0, citation_index=1,
    )]


class _FakeFusionOutput:
    def __init__(self):
        self.evidence = _evidence_list()
        self.conflicts = []
        self.citation_index = []


class _FakeFusion:
    def assemble(self, graph, text, qtype, limit=None):
        return _FakeFusionOutput(), {"entity_cards": [], "subgraph": {"nodes": [], "edges": []},
                                     "timeline": {"groups": []}, "map_points": []}


class _FakeAsyncCloser:
    """异步 closer 替身：记录调用次数，可注入异常。"""

    def __init__(self, name: str, log: list, boom: bool = False):
        self.name = name
        self.log = log
        self.boom = boom
        self.available = True

    async def aclose(self):
        self.log.append(self.name)
        if self.boom:
            raise RuntimeError(f"{self.name} 关闭失败")


class _FakeSyncCloser:
    def __init__(self, name: str, log: list):
        self.name = name
        self.log = log
        self.available = True

    def close(self):
        self.log.append(self.name)


class _FakeGenerator:
    def __init__(self, delta: str = "答案正文", hold: bool = False):
        self.llm = _FakeAsyncCloser("generate.llm", [])
        self.hold = hold
        self.delta = delta
        self.cancelled = False
        self.cache = _StubCache()

    def check_cache(self, rewritten, history, filters, entities=None,
                    corrections=None, dynasty_bias=None):
        return None, "key"

    async def generate(self, question, rewritten, evidence, history=None, filters=None,
                       on_delta=None, on_thinking=None, stats_out=None):
        if on_delta and self.delta:
            on_delta(self.delta)
        if self.hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return FinishReason.NORMAL.value, "fake", self.delta

    @staticmethod
    def build_citations(evidence):
        return []


class _StubCache:
    def put(self, key, payload):
        pass


class _FakeRuntime:
    """最小运行时替身（与 server.runtime.Runtime 的字段对齐）。"""

    def __init__(self, settings, *, generator=None, question=None, hold=False):
        from pathlib import Path

        self.settings = settings
        self.version = "test_v1"
        self.snapshot_dir = Path(".")
        self.index_dir = Path(".")
        self.question = question or _FakeUnderstanding()
        self.graph = object()
        self.text = object()
        self.fusion = _FakeFusion()
        self.generate = generator or _FakeGenerator(hold=hold)
        self.meta: dict = {}

    async def shutdown(self):        # pragma: no cover - 替身不复用真实实现
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


def _patch_searches(monkeypatch):
    import server.graph
    import server.text
    from contracts.retrieval import TextResult

    monkeypatch.setattr(server.graph, "search",
                        lambda *a, **k: type("G", (), {"evidence": [], "hit_entities": [],
                                                       "related_event_ids": [],
                                                       "neighbors": {}})())
    monkeypatch.setattr(server.text, "search",
                        lambda *a, **k: TextResult(evidence=[], mode="keyword",
                                                   vector_available=False))


async def _collect(runtime, req=None):
    from server.sse import run_query

    req = req or QueryRequest.from_dict({"session_id": "sid", "question": "测试问题"})
    return [json.loads(f[len("data: "):]) async for f in run_query(runtime, req)]


# ---------------------------------------------------------------- P0-1 版本
def _fake_data_dirs(tmp_path):
    snap = tmp_path / "snapshot"
    idx = tmp_path / "index"
    (snap / "20260101_v1").mkdir(parents=True)
    (idx / "20260101_v1").mkdir(parents=True)
    return snap, idx


def test_require_active_version_fails_when_unset(tmp_path):
    """require=true 且未指定版本：必须在扫描目录之前失败（P0-1）。"""
    from server.runtime import resolve_version

    snap, idx = _fake_data_dirs(tmp_path)
    s = _settings(snapshot_dir=snap, index_dir=idx,
                  active_version="", require_active_version=True)
    with pytest.raises(ValueError) as err:
        resolve_version(s)
    assert "RAG_ACTIVE_VERSION" in str(err.value)


def test_require_active_version_fails_when_missing(tmp_path):
    from server.runtime import resolve_version

    snap, idx = _fake_data_dirs(tmp_path)
    s = _settings(snapshot_dir=snap, index_dir=idx,
                  active_version="20260909_v9", require_active_version=True)
    with pytest.raises(FileNotFoundError):
        resolve_version(s)


def test_require_active_version_ok_when_present(tmp_path):
    from server.runtime import resolve_version

    snap, idx = _fake_data_dirs(tmp_path)
    s = _settings(snapshot_dir=snap, index_dir=idx,
                  active_version="20260101_v1", require_active_version=True)
    version, _snap, _idx = resolve_version(s)
    assert version == "20260101_v1"


def test_version_scan_allowed_in_dev_mode(tmp_path):
    """require=false 且未固定版本：允许按目录扫描（开发态）。"""
    from server.runtime import resolve_version

    snap, idx = _fake_data_dirs(tmp_path)
    s = _settings(snapshot_dir=snap, index_dir=idx,
                  active_version="", require_active_version=False)
    version, _snap, _idx = resolve_version(s)
    assert version == "20260101_v1"


def test_explicit_version_beats_env_pin(tmp_path):
    """显式参数优先于环境变量固定值（部署脚本 --version 能覆盖 .env）。"""
    from server.runtime import resolve_version

    snap, idx = _fake_data_dirs(tmp_path)
    (snap / "20260202_v1").mkdir()
    (idx / "20260202_v1").mkdir()
    s = _settings(snapshot_dir=snap, index_dir=idx,
                  active_version="20260101_v1", require_active_version=True)
    version, _snap, _idx = resolve_version(s, "20260202_v1")
    assert version == "20260202_v1"


# ---------------------------------------------------------------- P0-2 deadline
def _run_heartbeat(settings, generator, timeout_s=5.0):
    """跑一次心跳包装器，返回（事件列表, 实际耗时秒数）。"""
    from server.api import _stream_with_heartbeat

    rt = _FakeRuntime(settings, generator=generator)
    req = QueryRequest.from_dict({"session_id": "sid", "question": "测试问题"})

    async def main():
        t0 = time.monotonic()
        frames = [f async for f in _stream_with_heartbeat(rt, req, settings)]
        return frames, time.monotonic() - t0

    return asyncio.run(asyncio.wait_for(main(), timeout_s))


def test_deadline_holds_when_heartbeat_disabled(monkeypatch):
    """heartbeat=0：deadline 仍然严格生效（旧实现会永久等待）。"""
    _patch_searches(monkeypatch)
    s = _settings(sse_heartbeat_seconds=0, sse_max_duration_seconds=0.3)
    frames, elapsed = _run_heartbeat(s, _FakeGenerator(delta="", hold=True))
    assert elapsed < 1.5, f"heartbeat=0 时 deadline 失效（耗时 {elapsed:.2f}s）"
    done = [json.loads(f[6:]) for f in frames if f.startswith("data: ") and '"done"' in f][-1]
    assert done["data"]["finish_reason"] == FinishReason.FAILED.value
    err = [json.loads(f[6:]) for f in frames if f.startswith("data: ") and '"error"' in f][-1]
    assert err["data"]["error_code"] == ErrorCode.TIMEOUT.value


def test_deadline_shorter_than_heartbeat(monkeypatch):
    """heartbeat 大于 deadline：按 deadline 结束，不必等一个完整心跳周期。"""
    _patch_searches(monkeypatch)
    s = _settings(sse_heartbeat_seconds=30, sse_max_duration_seconds=0.3)
    frames, elapsed = _run_heartbeat(s, _FakeGenerator(delta="", hold=True))
    assert elapsed < 1.5, f"等待被心跳拖长（耗时 {elapsed:.2f}s）"
    assert any('"timeout"' in f for f in frames)


def test_timeout_after_partial_answer_is_interrupted(monkeypatch):
    """已送出正文后超时 → interrupted（回答可能不完整），不是 failed。"""
    _patch_searches(monkeypatch)
    s = _settings(sse_heartbeat_seconds=0, sse_max_duration_seconds=0.3)
    frames, _elapsed = _run_heartbeat(s, _FakeGenerator(delta="半截回答", hold=True))
    assert any('"type": "answer"' in f for f in frames)
    done = [json.loads(f[6:]) for f in frames if f.startswith("data: ") and '"done"' in f][-1]
    assert done["data"]["finish_reason"] == FinishReason.INTERRUPTED.value


def test_upstream_never_emits_still_times_out(monkeypatch):
    """上游一直不产出：到点仍要收流（不能挂死连接）。"""
    _patch_searches(monkeypatch)
    s = _settings(sse_heartbeat_seconds=0.1, sse_max_duration_seconds=0.5)
    frames, elapsed = _run_heartbeat(s, _FakeGenerator(delta="", hold=True), timeout_s=5)
    assert elapsed < 2.0
    assert any(f.startswith(":") for f in frames), "应至少发出一次心跳注释行"
    assert any('"done"' in f for f in frames)


# ---------------------------------------------------------------- P0-3 shutdown
def test_shutdown_awaits_async_closers():
    from server.runtime import Runtime

    log: list[str] = []
    rt = Runtime(settings=_settings(), version="v", snapshot_dir=None, index_dir=None)  # type: ignore[arg-type]
    rt.generate = type("G", (), {"llm": _FakeAsyncCloser("async", log)})()
    rt.question = type("Q", (), {"llm": _FakeSyncCloser("sync", log)})()

    asyncio.run(rt.shutdown())
    assert log == ["async", "sync"], "异步 closer 必须被 await、同步 closer 也必须执行"
    assert log.count("async") == 1


def test_shutdown_continues_after_one_failure():
    from server.runtime import Runtime

    log: list[str] = []
    rt = Runtime(settings=_settings(), version="v", snapshot_dir=None, index_dir=None)  # type: ignore[arg-type]
    rt.generate = type("G", (), {"llm": _FakeAsyncCloser("boom", log, boom=True)})()
    rt.question = type("Q", (), {"llm": _FakeSyncCloser("after", log)})()

    asyncio.run(rt.shutdown())      # 不抛异常
    assert log == ["boom", "after"], "单个资源关闭失败不应中断其余资源"


def test_shutdown_is_idempotent_without_closers():
    from server.runtime import Runtime

    rt = Runtime(settings=_settings(), version="v", snapshot_dir=None, index_dir=None)  # type: ignore[arg-type]
    rt.generate = object()
    rt.question = object()
    asyncio.run(rt.shutdown())      # 无 closer 也不报错


# ---------------------------------------------------------------- P1-2 纠正语义
@pytest.mark.parametrize("item,keyword", [
    ({"action": ["add"]}, "必须是字符串"),
    ({"action": {"a": 1}}, "必须是字符串"),
    ({"action": "add"}, "name"),
    ({"action": "add", "name": "白起"}, "entity_type"),
    ({"action": "replace"}, "original"),
    ({"action": "replace", "original": "白起"}, "replacement"),
    ({"action": "remove"}, "original"),
])
def test_correction_action_semantics(item, keyword):
    with pytest.raises(RequestValidationError) as err:
        QueryRequest.from_dict({"session_id": "s", "question": "q",
                                "corrected_entities": [item]})
    assert keyword in str(err.value)


@pytest.mark.parametrize("item,keyword", [
    # add 不接受“源实体”类字段，也不接受 replace 的 replacement
    ({"action": "add", "name": "白起", "entity_type": "人物", "original": "赵括"}, "original"),
    ({"action": "add", "name": "白起", "entity_type": "人物",
      "source_entity_id": "e1"}, "source_entity_id"),
    ({"action": "add", "name": "白起", "entity_type": "人物", "replacement": "廉颇"},
     "replacement"),
    # replace 不接受 name；remove 不接受任何目标实体字段
    ({"action": "replace", "original": "白起", "replacement": "廉颇", "name": "x"}, "name"),
    ({"action": "remove", "original": "白起", "name": "白起"}, "replacement"),
    ({"action": "remove", "original": "白起", "replacement_entity_id": "e2"},
     "replacement_entity_id"),
    # 含混的 entity_id 只在 add 上有定义
    ({"action": "remove", "original": "白起", "entity_id": "e1"}, "source_entity_id"),
    ({"action": "replace", "original": "白起", "replacement": "廉颇",
      "entity_id": "e1"}, "source_entity_id"),
    # 未声明字段一律拒绝
    ({"action": "add", "name": "白起", "entity_type": "人物", "whatever": 1}, "未声明字段"),
])
def test_correction_rejects_action_mismatched_fields(item, keyword):
    """动作与字段不匹配、或出现未声明字段 → 结构化 400（不再静默忽略/规范化）。"""
    with pytest.raises(RequestValidationError) as err:
        QueryRequest.from_dict({"session_id": "s", "question": "q",
                                "corrected_entities": [item]})
    assert keyword in str(err.value)


def test_correction_entity_id_alias_only_on_add():
    """兼容别名口径：add 的 entity_id 等价于 replacement_entity_id；remove/replace 传它报错。"""
    q = QueryRequest.from_dict({
        "session_id": "s", "question": "q",
        "corrected_entities": [
            {"action": "add", "name": "背水之战", "entity_type": "事件",
             "entity_id": "event_123"},
        ],
    })
    item = q.corrected_entities[0]
    assert item.entity_id == "event_123"
    assert item.replacement_entity_id == "event_123"


def test_correction_replace_and_remove_use_source_and_replacement_ids():
    q = QueryRequest.from_dict({
        "session_id": "s", "question": "q",
        "corrected_entities": [
            {"action": "replace", "source_entity_id": "event_zhan",
             "replacement_entity_id": "event_han",
             "original": "井陉之战", "replacement": "井陉之战", "entity_type": "事件"},
            {"action": "remove", "source_entity_id": "person_1"},
        ],
    })
    rep, rem = q.corrected_entities
    assert (rep.source_entity_id, rep.replacement_entity_id) == ("event_zhan", "event_han")
    assert rep.entity_id is None          # replace 不再有含混的 entity_id
    assert rem.source_entity_id == "person_1"


class _SameNameMatcher:
    """同名不同朝代的词典替身：井陉之战 同时存在战国与西汉两条。"""

    _by_name = {"井陉之战": [{"entity_id": "event_zhan", "type": "事件",
                              "name": "井陉之战", "dynasty": "战国"},
                             {"entity_id": "event_han", "type": "事件",
                              "name": "井陉之战", "dynasty": "西汉"}]}
    _by_id = {e["entity_id"]: e for e in _by_name["井陉之战"]}

    def by_id(self, entity_id):
        return self._by_id.get(entity_id) if entity_id else None


def _understand_stub():
    from server.query.understand import QuestionUnderstanding

    obj = QuestionUnderstanding.__new__(QuestionUnderstanding)
    obj.matcher = _SameNameMatcher()
    return obj


def _two_same_name_entities():
    from contracts.request import EntityRef

    return [
        EntityRef(name="井陉之战", type="事件", standard_name="井陉之战",
                  entity_id="event_zhan", dynasty="战国"),
        EntityRef(name="井陉之战", type="事件", standard_name="井陉之战",
                  entity_id="event_han", dynasty="西汉"),
    ]


def test_apply_corrections_remove_targets_source_id_not_list_order():
    from contracts.request import CorrectedEntity

    obj = _understand_stub()
    corrected = [CorrectedEntity(action="remove", original="井陉之战",
                                 source_entity_id="event_han")]
    left, _ = obj._apply_corrections(_two_same_name_entities(), [], corrected)
    assert [e.entity_id for e in left] == ["event_zhan"], "必须按 ID 移除，而不是列表第一项"


def test_apply_corrections_replace_lands_on_replacement_id():
    """用户选“西汉”这条，最终实体必须落到 event_han 且带上它的朝代（P1-3）。"""
    from contracts.request import CorrectedEntity

    obj = _understand_stub()
    corrected = [CorrectedEntity(action="replace", original="井陉之战",
                                 source_entity_id="event_zhan",
                                 replacement_entity_id="event_han",
                                 replacement="井陉之战")]
    replaced, _ = obj._apply_corrections(_two_same_name_entities(), [], corrected)
    ids = [e.entity_id for e in replaced]
    assert ids == ["event_han"], f"替换后应只保留 event_han，实际 {ids}"
    assert replaced[0].dynasty == "西汉", "目标实体的朝代必须来自 ID，而不是猜"


def test_apply_corrections_add_uses_replacement_id():
    from contracts.request import CorrectedEntity

    obj = _understand_stub()
    corrected = [CorrectedEntity(action="add", name="井陉之战",
                                 replacement_entity_id="event_han",
                                 entity_type="事件")]
    added, _ = obj._apply_corrections([], [], corrected)
    assert len(added) == 1
    assert added[0].entity_id == "event_han"
    assert added[0].dynasty == "西汉"


def test_name_lookup_refuses_to_guess_between_same_name_entities():
    """同名多候选时按名称解析不得返回 ID（否则等于用顺序猜用户意图）。"""
    obj = _understand_stub()
    entity_id, entity_type = obj._resolve_by_name("井陉之战")
    assert entity_id is None
    assert entity_type == "事件"       # 类型唯一时仍可用，ID 必须留空


# ---------------------------------------------------------------- P1-1 缓存键
def test_cache_key_changes_with_corrections():
    from server.generate.cache import cache_key
    from contracts.request import CorrectedEntity

    base = dict(rewritten="介绍一下井陉之战", history=[], filters={},
                source_version="v1", model="m", text_mode="hybrid")
    plain = cache_key(**base)
    added = cache_key(**base, corrections=[
        CorrectedEntity(action="add", entity_type="事件", name="井陉之战")])
    removed = cache_key(**base, corrections=[
        CorrectedEntity(action="remove", original="井陉之战")])
    replaced = cache_key(**base, corrections=[
        CorrectedEntity(action="replace", original="井陉之战", replacement="背水之战")])

    assert len({plain, added, removed, replaced}) == 4, "纠正语义必须进缓存键"


def test_cache_key_changes_with_entities():
    from server.generate.cache import cache_key

    base = dict(rewritten="介绍一下井陉之战", history=[], filters={},
                source_version="v1", model="m", text_mode="hybrid")
    a = cache_key(**base, entities=[{"entity_id": "e1", "standard_name": "井陉之战"}])
    b = cache_key(**base, entities=[{"entity_id": "e2", "standard_name": "井陉之战"}])
    assert a != b, "同名不同 ID 的实体必须产生不同的缓存键"


def test_cache_key_uses_full_history_not_tail():
    """尾部相同的两份历史不能碰撞（旧实现只取 JSON 尾部 400 字符）。"""
    from server.generate.cache import cache_key

    tail = [{"role": "user", "content": "同一句尾部问题"},
            {"role": "assistant", "content": "同一句尾部回答"}]
    hist_a = [{"role": "user", "content": "前缀甲" * 60},
              {"role": "assistant", "content": "回答甲"}] + tail
    hist_b = [{"role": "user", "content": "前缀乙" * 60},
              {"role": "assistant", "content": "回答乙"}] + tail
    base = dict(rewritten="问题", filters={}, source_version="v1",
                model="m", text_mode="hybrid")
    assert cache_key(history=hist_a, **base) != cache_key(history=hist_b, **base)


def test_cache_key_normalizes_order_and_extra_fields():
    """语义相同、字段顺序或冗余字段不同的请求应命中同一条缓存。"""
    from server.generate.cache import cache_key

    base = dict(rewritten="问题", source_version="v1", model="m", text_mode="hybrid")
    a = cache_key(history=[{"role": "user", "content": "甲", "extra": 1}],
                  filters={"dynasty": ["战国"]}, **base)
    b = cache_key(history=[{"content": "甲", "role": "user"}],
                  filters={"dynasty": ["战国"]}, **base)
    assert a == b


# ---------------------------------------------------------------- P1-4 限流
def test_rate_limiter_rejects_new_keys_instead_of_evicting_live_ones():
    from server.api import RateLimiter

    limiter = RateLimiter(1, max_keys=16)
    for i in range(16):
        assert limiter.allow(f"live-{i}") is True
    stats = limiter.stats()
    assert stats["keys"] == 16
    assert limiter.allow("newcomer") is False
    assert limiter.stats()["rejected_new_keys"] == 1


def test_rate_limiter_sweeps_expired_keys_to_make_room():
    """窗口过期（干净可清）的 key 仍应被清理，为后续请求腾出位置。"""
    from server.api import RateLimiter

    limiter = RateLimiter(1, max_keys=16)
    for i in range(16):
        limiter.allow(f"old-{i}")
    # 手工把窗口推到过期（不依赖 sleep，避免测试变慢）
    with limiter._lock:  # noqa: SLF001 - 测试直接构造过期状态
        for k in list(limiter._hits):
            limiter._hits[k] = [time.time() - 120]
    assert limiter.allow("fresh") is True
    assert limiter.stats()["evicted_keys"] == 16


# ---------------------------------------------------------------- P1-5 线程池
def test_sync_pool_rejects_when_queue_is_full():
    from server.sse import SyncPoolBusy, SyncWorkPool

    pool = SyncWorkPool(max_workers=1, max_queue=1)
    import threading

    release = threading.Event()

    def _block():
        release.wait(2.0)

    futures = [pool.submit(_block) for _ in range(2)]
    with pytest.raises(SyncPoolBusy):
        pool.submit(lambda: None)
    stats = pool.stats()
    assert stats["rejected"] == 1
    assert stats["max_workers"] == 1 and stats["max_queue"] == 1
    release.set()
    for f in futures:
        f.result(timeout=3)
    pool.shutdown()


def test_sync_pool_run_in_thread_returns_value():
    from server.sse import get_sync_pool, run_in_thread

    async def main():
        return await run_in_thread(lambda a, b=0: a + b, 2, b=3)

    assert asyncio.run(main()) == 5
    assert get_sync_pool().stats()["completed"] >= 1


# ---------------------------------------------------------------- P1-6 demo 清单
class _DemoRt:
    def __init__(self, data_dir, version="20260915_v1"):
        from pathlib import Path

        self.version = version
        self.settings = type("S", (), {"data_dir": Path(data_dir)})()


def _write_demo(tmp_path, payload):
    d = tmp_path / "eval" / "20260915_v1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "demo_examples.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _valid_demo(version="20260915_v1"):
    return {
        "version": version,
        "source_run": "run_x",
        "measurement_mode": "offline",
        "examples": [{
            "id": "M01", "question": "介绍一下长平之战。", "category": "single_entity",
            "capability": "both",
            "measured": {"first_answer_ms": 1000, "finish_reason": "normal"},
        }],
    }


def test_demo_missing_version_is_rejected(tmp_path):
    from server.api import _load_demo_examples

    payload = _valid_demo()
    payload.pop("version")
    rt = _DemoRt(_write_demo(tmp_path, payload))
    out = _load_demo_examples(rt)
    assert out["status"] == "error" and "version" in out["message"]


def test_demo_empty_version_is_rejected(tmp_path):
    from server.api import _load_demo_examples

    payload = _valid_demo()
    payload["version"] = ""
    rt = _DemoRt(_write_demo(tmp_path, payload))
    assert _load_demo_examples(rt)["status"] == "error"


def test_demo_version_mismatch_is_rejected(tmp_path):
    from server.api import _load_demo_examples

    rt = _DemoRt(_write_demo(tmp_path, _valid_demo("20260904_v2")))
    out = _load_demo_examples(rt)
    assert out["status"] == "error" and "不一致" in out["message"]


def test_demo_missing_source_run_or_bad_examples(tmp_path):
    from server.api import _load_demo_examples

    payload = _valid_demo()
    payload.pop("source_run")
    assert _load_demo_examples(_DemoRt(_write_demo(tmp_path, payload)))["status"] == "error"

    payload = _valid_demo()
    payload["examples"] = "not-a-list"
    assert _load_demo_examples(_DemoRt(_write_demo(tmp_path, payload)))["status"] == "error"

    payload = _valid_demo()
    payload["examples"] = [{"id": "M01"}]
    assert _load_demo_examples(_DemoRt(_write_demo(tmp_path, payload)))["status"] == "error"


def test_demo_valid_payload_passes(tmp_path):
    from server.api import _load_demo_examples, _demo_status

    rt = _DemoRt(_write_demo(tmp_path, _valid_demo()))
    out = _load_demo_examples(rt)
    assert out["status"] == "ok"
    status = _demo_status(rt)
    assert status["demo_ready"] is True
    assert status["demo_examples"] == 1


# ---------------------------------------------------------------- SSE 池繁忙
def test_run_query_reports_server_busy(monkeypatch):
    """同步池排队满 → server_busy 错误码（不是含糊的 internal）。"""
    import server.sse as sse_mod
    from server.sse import SyncPoolBusy

    _patch_searches(monkeypatch)
    rt = _FakeRuntime(_settings())

    def _boom(*a, **k):
        raise SyncPoolBusy("同步工作池繁忙")

    monkeypatch.setattr(sse_mod, "run_in_thread", _boom)
    events = asyncio.run(_collect(rt))
    err = [e for e in events if e["type"] == "error"][-1]
    assert err["data"]["error_code"] == ErrorCode.SERVER_BUSY.value
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["data"]["finish_reason"] == FinishReason.FAILED.value


# ---------------------------------------------------------------- P0-3 资源生命周期
class _SyncCloserWithClose:
    """同步 closer 替身（如 EmbeddingClient / 同步 OpenAI 封装）。"""

    def __init__(self, name: str, log: list):
        self.name = name
        self.log = log
        self.available = True

    def close(self):
        self.log.append(self.name)


class _EmbeddingLike:
    """EmbeddingClient 替身：可调用 + 可关闭 + 幂等。"""

    def __init__(self, log: list):
        self.log = log
        self.closed = 0
        self.available = True

    def __call__(self, texts):
        return [[0.0] for _ in texts]

    def close(self):
        self.closed += 1
        self.log.append("embedding")


def _runtime_with_resources(**owners):
    from server.runtime import Runtime

    rt = Runtime(settings=_settings(), version="v", snapshot_dir=None, index_dir=None)  # type: ignore[arg-type]
    for name, value in owners.items():
        setattr(rt, name, value)
    return rt


def test_shutdown_closes_embedding_client_once():
    """P0-3：向量客户端必须被 Runtime 枚举到并关闭，且只关一次。"""
    log: list[str] = []
    emb = _EmbeddingLike(log)
    rt = _runtime_with_resources(
        generate=type("G", (), {"llm": _FakeAsyncCloser("llm", log)})(),
        question=type("Q", (), {"llm": _SyncCloserWithClose("fallback", log)})(),
        embedding_client=emb,
    )
    asyncio.run(rt.shutdown())
    assert log == ["llm", "fallback", "embedding"], log
    assert emb.closed == 1


def test_shutdown_is_idempotent():
    log: list[str] = []
    emb = _EmbeddingLike(log)
    rt = _runtime_with_resources(
        generate=type("G", (), {"llm": _FakeAsyncCloser("llm", log)})(),
        embedding_client=emb,
    )
    asyncio.run(rt.shutdown())
    asyncio.run(rt.shutdown())
    assert emb.closed == 1, "重复 shutdown 不得重复关闭"
    assert log.count("llm") == 1


def test_resources_enumerates_all_clients():
    emb = _EmbeddingLike([])
    rt = _runtime_with_resources(
        generate=type("G", (), {"llm": _FakeAsyncCloser("llm", [])})(),
        question=type("Q", (), {"llm": _SyncCloserWithClose("fb", [])})(),
        embedding_client=emb,
    )
    names = [n for n, owner in rt.resources() if owner is not None]
    assert "embedding_client" in names
    assert "generate.llm" in names and "question.llm" in names


def test_embedding_client_close_is_idempotent_and_marks_unavailable():
    from data.index.embeddings import EmbeddingClient

    # 显式清空配置，避免依赖本机 .env（否则会真的去建客户端）
    client = EmbeddingClient(_settings(embedding_base_url="", embedding_model=""))
    assert client.available is False
    assert client._client is None
    client.close()
    client.close()                                # 幂等，不抛错


# ---------------------------------------------------------------- P0-4 版本来源
@pytest.mark.parametrize("cli_version,env_version,expected", [
    ("20260915_v1", "", "cli_explicit"),
    (None, "20260915_v1", "env_pinned"),
    (None, "", "latest_scan"),
])
def test_version_source_three_states(cli_version, env_version, expected):
    from server.runtime import version_source

    s = _settings(active_version=env_version)
    assert version_source(s, cli_version) == expected


# ---------------------------------------------------------------- P1-5 同步池
def test_sync_pool_separates_active_and_queued():
    from server.sse import SyncWorkPool

    import threading

    pool = SyncWorkPool(max_workers=1, max_queue=4)
    release = threading.Event()

    def _block():
        release.wait(3.0)

    running = pool.submit(_block)
    for _ in range(3):
        pool.submit(_block)
    # 等到第一个任务真正开始（active=1）
    for _ in range(50):
        if pool.stats()["active"] == 1:
            break
        time.sleep(0.02)
    stats = pool.stats()
    assert stats["active"] == 1, stats
    assert stats["queued"] == 3, stats
    assert stats["in_flight"] == 4
    assert stats["scope"] == "process"
    release.set()
    running.result(timeout=5)
    pool.shutdown()


def test_sync_pool_cancel_before_start_frees_queue_slot():
    from server.sse import SyncWorkPool

    import threading

    pool = SyncWorkPool(max_workers=1, max_queue=2)
    release = threading.Event()

    def _block():
        release.wait(3.0)

    first = pool.submit(_block)
    for _ in range(50):
        if pool.stats()["active"] == 1:
            break
        time.sleep(0.02)
    queued = pool.submit(_block)
    assert pool.stats()["queued"] == 1

    pool.note_cancel(queued)          # 模拟请求断连时撤销排队任务
    stats = pool.stats()
    assert stats["cancelled_before_start"] == 1
    assert stats["queued"] == 0
    assert queued.cancelled() is True
    release.set()
    first.result(timeout=5)
    pool.shutdown()


def test_sync_pool_budget_equal_to_deadline_is_rejected():
    """P1-5：预算 + 收尾余量必须严格小于 deadline，等于也不行。"""
    s = _settings(sse_max_duration_seconds=300, llm_timeout_seconds=95,
                  llm_max_retries=2, shutdown_margin_seconds=15)
    with pytest.raises(ValueError) as err:
        s.validate()
    assert "SSE_MAX_DURATION_SECONDS" in str(err.value)

    ok = _settings(sse_max_duration_seconds=300, llm_timeout_seconds=60,
                   llm_max_retries=2, shutdown_margin_seconds=15)
    ok.validate()          # 180 + 15 < 300 → 通过


# ---------------------------------------------------------------- P2-3 制品清单
def _make_chroma_like_db(path, vectors: int = 3):
    """构造一个最小 Chroma 元数据库（只含逻辑哈希用到的三张表）。"""
    import sqlite3

    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE collections (id TEXT, name TEXT, dimension INT);
        CREATE TABLE segments (id TEXT, type TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id TEXT, segment_id TEXT);
        """
    )
    con.execute("INSERT INTO collections VALUES ('c1', 'chunks_v1', 1024)")
    con.execute("INSERT INTO segments VALUES ('seg-vector', 'hnsw', 'VECTOR', 'c1')")
    con.execute("INSERT INTO segments VALUES ('seg-meta', 'sqlite', 'METADATA', 'c1')")
    for i in range(vectors):
        con.execute("INSERT INTO embeddings VALUES (?, 'seg-vector')", (f"id{i}",))
    con.commit()
    con.close()


def test_manifest_sqlite_logical_hash_ignores_runtime_writes(tmp_path):
    """逻辑哈希只认语义内容：运行态写入不改哈希，增删向量必须改（工作单 P2-3）。"""
    import sqlite3
    from scripts.build_artifact_manifest import _sqlite_logical_hash

    db = tmp_path / "chroma.sqlite3"
    _make_chroma_like_db(db, vectors=3)
    before = _sqlite_logical_hash(db)

    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS acquire_write (id TEXT)")   # 运行态新表
    con.execute("INSERT INTO acquire_write VALUES ('lock')")
    con.commit()
    con.close()
    assert _sqlite_logical_hash(db) == before, "运行态写入不应改变逻辑哈希"

    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO embeddings VALUES ('id-new', 'seg-vector')")
    con.commit()
    con.close()
    assert _sqlite_logical_hash(db) != before, "向量条数变化必须被发现"


def test_manifest_verify_detects_changed_and_unregistered_files(tmp_path, monkeypatch):
    """verify 必须同时抓三类问题：内容变化、缺失、未登记新文件。"""
    from scripts import build_artifact_manifest as bam

    root = tmp_path
    data_dir = root / "data"
    (data_dir / "index" / "v1").mkdir(parents=True)
    (data_dir / "snapshot" / "v1").mkdir(parents=True)
    (data_dir / "eval" / "v1").mkdir(parents=True)

    (data_dir / "snapshot" / "v1" / "entities.json").write_text("[]", encoding="utf-8")
    (data_dir / "snapshot" / "v1" / "manifest.json").write_text("{}", encoding="utf-8")
    (data_dir / "index" / "v1" / "manifest.json").write_text('{"source_snapshot":"v1"}',
                                                             encoding="utf-8")
    (data_dir / "index" / "v1" / "chunks_fts.db").write_bytes(b"fts")
    (data_dir / "eval" / "v1" / "questions.jsonl").write_text("{}\n", encoding="utf-8")
    (data_dir / "eval" / "v1" / "questions.meta.json").write_text("{}", encoding="utf-8")
    # 依赖声明属于发布必需项：临时根目录里补齐，让 missing 只反映真实缺口
    for rel in ("requirements.txt", "requirements-dev.txt", "frontend/package.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    settings = _settings(data_dir=data_dir,
                         snapshot_dir=data_dir / "snapshot",
                         index_dir=data_dir / "index",
                         frontend_dist=root / "dist")
    monkeypatch.setattr(bam, "repo_root", lambda: root)
    # verify 内部通过模块级 get_settings() 取活跃版本目录，必须一起替换，
    # 否则会去读真实的 data/（那正是"测试不得触碰正式制品"要避免的事）
    monkeypatch.setattr(bam, "get_settings", lambda: settings)

    expected = bam.expected_paths(settings, "v1")
    assert "data/index/v1/chunks_fts.db" in expected
    assert "data/eval/v1/questions.jsonl" in expected

    manifest = bam.collect(settings, "v1")
    assert manifest["missing"] == []
    out = data_dir / "release" / "artifact-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert bam.cmd_verify(type("A", (), {})()) == 0

    # 1) 内容变化
    (data_dir / "snapshot" / "v1" / "entities.json").write_text("[1]", encoding="utf-8")
    assert bam.cmd_verify(type("A", (), {})()) == 1
    (data_dir / "snapshot" / "v1" / "entities.json").write_text("[]", encoding="utf-8")

    # 2) 缺失
    (data_dir / "eval" / "v1" / "questions.jsonl").unlink()
    assert bam.cmd_verify(type("A", (), {})()) == 1
    (data_dir / "eval" / "v1" / "questions.jsonl").write_text("{}\n", encoding="utf-8")

    # 3) 未登记的新文件
    (data_dir / "index" / "v1" / "vectors").mkdir(exist_ok=True)
    (data_dir / "index" / "v1" / "vectors" / "ids.json").write_text("[]", encoding="utf-8")
    assert bam.cmd_verify(type("A", (), {})()) == 1
    (data_dir / "index" / "v1" / "vectors" / "ids.json").unlink()
    assert bam.cmd_verify(type("A", (), {})()) == 0


def test_manifest_build_refuses_dirty_worktree(tmp_path, monkeypatch):
    """未提交改动时禁止生成发布清单（release 门禁）。"""
    from scripts import build_artifact_manifest as bam

    # 脏检查现在走 release_info.git_status_lines（内含 new/ 等约定排除清单），直接替换它
    monkeypatch.setattr(bam, "git_status_lines", lambda root, prefixes=None: [" M dirty.py"])
    settings = _settings(data_dir=tmp_path)
    monkeypatch.setattr(bam, "get_settings", lambda: settings)
    monkeypatch.setattr(bam, "repo_root", lambda: tmp_path)
    args = type("A", (), {"version": "v1", "require_clean": True})()
    assert bam.cmd_build(args) == 3


# ============================================================ 第五轮审核守护用例
# 覆盖：P0-3 停机顺序、P1-5/R5-6 同步池零值 schema、P0-4 版本来源、R5-5 生产 CORS。

def _reset_pool(monkeypatch):
    """把同步池单例换成新实例并保证测试结束后关闭（避免线程泄漏到其他用例）。"""
    import server.sse as sse_mod

    monkeypatch.setattr(sse_mod, "_sync_pool", None)
    return sse_mod


def test_sync_pool_stats_has_stable_schema_before_first_query(monkeypatch):
    """R5-6：池尚未创建时 health 也必须返回完整零值统计，而不是 {}。"""
    sse_mod = _reset_pool(monkeypatch)
    empty = sse_mod.sync_pool_stats()
    assert empty, "未初始化的池不能返回空字典"
    assert empty["initialized"] is False
    assert empty["active"] == 0 and empty["queued"] == 0 and empty["in_flight"] == 0
    assert empty["rejected"] == 0 and empty["closed"] is False

    pool = sse_mod.get_sync_pool(_settings())
    live = pool.stats()
    assert set(live) == set(empty), "初始化前后字段集合必须一致，否则监控无法固定 schema"
    assert live["initialized"] is True
    sse_mod.shutdown_sync_pool()


def test_shutdown_drains_active_before_closing_clients(monkeypatch):
    """P0-3：停机顺序必须是"同步池先收尾、外部客户端后关闭"。

    旧实现先关客户端再关池，池里在跑的 embedding/LLM 任务会拿着已关闭的客户端发请求。
    """
    import asyncio
    import threading

    sse_mod = _reset_pool(monkeypatch)
    order: list[str] = []
    started = threading.Event()

    pool = sse_mod.get_sync_pool(_settings(sync_pool_max_workers=2, sync_pool_max_queue=2))

    def _slow_task():
        started.set()
        order.append("task-start")
        time.sleep(0.25)
        order.append("task-end")

    pool.submit(_slow_task)
    assert started.wait(3.0), "任务未在预期时间内开始"

    class _RecordingCloser:
        def close(self):
            order.append("client-close")

    rt = _runtime_with_resources(generate=type("G", (), {"llm": _RecordingCloser()})())
    asyncio.run(rt.shutdown(drain_seconds=5.0))

    assert "task-end" in order, "在途任务必须被等待结束"
    assert order.index("task-end") < order.index("client-close"), \
        f"客户端关闭必须发生在在途任务结束之后，实际顺序 {order}"


def test_shutdown_cancels_queued_tasks_without_running_them(monkeypatch):
    """P0-3 第 2 步：排队任务必须在停机时被撤销，绝不能开跑。"""
    import threading

    sse_mod = _reset_pool(monkeypatch)
    ran: list[str] = []
    release = threading.Event()

    pool = sse_mod.get_sync_pool(_settings(sync_pool_max_workers=1, sync_pool_max_queue=4))
    pool.submit(lambda: release.wait(5.0))          # 占住唯一的 worker
    for _ in range(50):
        if pool.stats()["active"] == 1:
            break
        time.sleep(0.02)
    for i in range(3):
        pool.submit(lambda i=i: ran.append(f"queued-{i}"))   # 这些必须被撤销

    # 在 drain 窗口内让在途任务自然结束：验证"排队被撤销"与"在途被等到"可以同时成立
    threading.Timer(0.2, release.set).start()
    outcome = sse_mod.shutdown_sync_pool(drain_seconds=5.0)

    # 被撤销的任务不能再被 worker 捞起来执行
    time.sleep(0.3)
    assert outcome["cancelled_queued"] >= 1, outcome
    assert ran == [], f"被撤销的排队任务不得执行，实际执行了 {ran}"
    assert outcome["drained"] is True, outcome
    assert outcome["active_left"] == 0


def test_async_shutdown_does_not_block_event_loop(monkeypatch):
    """B2：async 停机路径的 drain 期间，事件循环必须仍能调度其他协程。

    旧实现在协程里直接调用同步 `wait_idle`（`time.sleep` 轮询），会把正在收尾的
    SSE 流与健康检查一起冻结最长 `SHUTDOWN_DRAIN_SECONDS`。
    """
    import threading

    sse_mod = _reset_pool(monkeypatch)
    release = threading.Event()

    pool = sse_mod.get_sync_pool(_settings(sync_pool_max_workers=1, sync_pool_max_queue=2))
    pool.submit(lambda: release.wait(5.0))          # 占住 worker，让 drain 必须等待
    for _ in range(50):
        if pool.stats()["active"] == 1:
            break
        time.sleep(0.02)

    ticks: list = []

    async def _heartbeat():
        while True:
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    async def _main():
        hb = asyncio.ensure_future(_heartbeat())
        threading.Timer(0.3, release.set).start()
        outcome = await sse_mod.shutdown_sync_pool_async(drain_seconds=5.0)
        drain_end = time.monotonic()
        hb.cancel()
        return outcome, drain_end

    outcome, drain_end = asyncio.run(_main())
    assert outcome["drained"] is True, outcome
    assert ticks, "drain 期间事件循环被完全阻塞（心跳协程一次都没跑）"
    assert min(ticks) < drain_end, "心跳只能在 drain 结束后推进 → 事件循环被同步等待卡住"


def test_submit_registers_future_before_returning(monkeypatch):
    """B2：submit 返回时 future 必须已在待撤销集合里（否则停机快照可能漏掉它）。"""
    import threading

    from server.sse import SyncWorkPool

    pool = SyncWorkPool(max_workers=1, max_queue=1)
    release = threading.Event()
    try:
        first = pool.submit(lambda: release.wait(1.0))
        second = pool.submit(lambda: None)
        assert first in pool._futures and second in pool._futures
        assert pool.begin_drain() >= 1, "排队中的任务必须能被 begin_drain 撤销"
    finally:
        release.set()
        pool.shutdown()


def test_wait_idle_waits_for_queued_not_only_active(monkeypatch):
    """B2：判空闲必须同时看 active 与 queued，否则排队任务会被漏在"收尾完成"之后。"""
    import threading

    from server.sse import SyncWorkPool

    pool = SyncWorkPool(max_workers=1, max_queue=3)
    release = threading.Event()
    ran: list = []
    try:
        pool.submit(lambda: release.wait(2.0))
        for _ in range(50):
            if pool.stats()["active"] == 1:
                break
            time.sleep(0.02)
        pool.submit(lambda: ran.append("queued"))
        assert pool.stats()["queued"] == 1
        # 只等 active 的实现会立刻返回 True；正确实现必须等到排队任务也跑完
        assert pool.wait_idle(0.3) is False
        release.set()
        assert pool.wait_idle(3.0) is True
        assert ran == ["queued"]
    finally:
        release.set()
        pool.shutdown()


def test_build_runtime_closes_clients_when_later_layer_fails(tmp_path, monkeypatch):
    """Z3：构建半途失败时必须关闭已建立的 embedding 客户端（不能丢下连接池）。"""
    import server.fusion
    import server.graph
    import server.query
    import server.runtime as rt_mod
    import server.text
    import data.index.embeddings as emb_mod

    closed: list = []

    class _Client:
        available = True

        def __call__(self, texts):
            return [[0.0] for _ in texts]

        def close(self):
            closed.append("embedding")

    monkeypatch.setattr(rt_mod, "resolve_version",
                        lambda settings, version=None: ("v1", tmp_path, tmp_path))
    monkeypatch.setattr(server.query, "load_understanding", lambda *a, **k: object())
    monkeypatch.setattr(server.graph, "load_graph",
                        lambda *a, **k: type("G", (), {"entities": [], "relations": []})())
    monkeypatch.setattr(emb_mod, "build_embedding_client", lambda *a, **k: _Client())
    monkeypatch.setattr(server.text, "load_searcher",
                        lambda *a, **k: type("T", (), {"vector_available": False})())

    def _boom(*a, **k):
        raise RuntimeError("模拟 F05 加载失败")

    monkeypatch.setattr(server.fusion, "load_fusion", _boom)

    with pytest.raises(RuntimeError):
        rt_mod.build_runtime(_settings(), "v1")
    assert closed == ["embedding"], "构建失败后必须关闭已建立的客户端"


def test_version_source_cli_explicit_via_env_hint(monkeypatch):
    """P0-4：run_server 传 --version 后 health 必须显示 cli_explicit。"""
    from scripts import run_server
    from server.runtime import version_source

    monkeypatch.delenv("RAG_ACTIVE_VERSION", raising=False)
    monkeypatch.delenv("RAG_VERSION_SOURCE", raising=False)
    run_server.pin_version("20260915_v1")
    assert os.environ["RAG_ACTIVE_VERSION"] == "20260915_v1"
    assert os.environ["RAG_VERSION_SOURCE"] == "cli_explicit"

    s = _settings(active_version="20260915_v1", version_source_hint="cli_explicit")
    assert version_source(s) == "cli_explicit"

    # 只写环境变量（.env 固定）时仍是 env_pinned，不能冒充 cli_explicit
    s2 = _settings(active_version="20260915_v1", version_source_hint="")
    assert version_source(s2) == "env_pinned"

    monkeypatch.delenv("RAG_ACTIVE_VERSION", raising=False)
    monkeypatch.delenv("RAG_VERSION_SOURCE", raising=False)


@pytest.mark.parametrize("require_active,explicit,origins,allow_public,expect_problem", [
    (True, True, ["*"], False, True),      # 显式生产 + 通配 + 未确认 → 拒绝启动
    (True, True, ["*"], True, False),      # 显式确认公开
    (True, True, ["https://example.com"], False, False),   # 限定域名
    (True, False, ["*"], False, False),    # 隐式生产（run_server --version）：只告警
    (False, False, ["*"], False, False),   # 开发态
])
def test_cors_startup_gate(require_active, explicit, origins, allow_public, expect_problem):
    """R5-5：显式生产档 + wildcard CORS 必须显式确认，否则启动即失败。"""
    s = _settings(require_active_version=require_active,
                  require_active_version_explicit=explicit,
                  cors_allow_origins=list(origins),
                  allow_public_cors=allow_public)
    problem = s.cors_startup_problem()
    if expect_problem:
        assert problem and "CORS_ALLOW_ORIGINS" in problem
    else:
        assert problem is None


def test_version_source_hint_is_cross_checked(monkeypatch):
    """B4：声明与实际不符时以实际行为为准（不盲信 RAG_VERSION_SOURCE 残留值）。"""
    from server.runtime import version_source

    # 声明 cli_explicit 但环境里没有固定版本 → 实际是扫描选版
    stale = _settings(active_version="", version_source_hint="cli_explicit")
    assert version_source(stale) == "latest_scan"

    stale_env = _settings(active_version="", version_source_hint="env_pinned")
    assert version_source(stale_env) == "latest_scan"

    # 声明与固定版本同时存在 → 采信声明
    ok = _settings(active_version="20260915_v1", version_source_hint="cli_explicit")
    assert version_source(ok) == "cli_explicit"


def test_pin_version_clears_stale_cli_marker(monkeypatch):
    """B4：不传 --version 时必须清掉残留的 cli_explicit 声明。"""
    from scripts import run_server

    monkeypatch.setenv("RAG_VERSION_SOURCE", "cli_explicit")
    assert run_server.pin_version(None) == ""
    assert "RAG_VERSION_SOURCE" not in os.environ, "残留声明必须被清掉"

    # 显式传版本时照常写入
    monkeypatch.delenv("RAG_ACTIVE_VERSION", raising=False)
    assert run_server.pin_version("20260915_v1") == "cli_explicit"
    assert os.environ["RAG_ACTIVE_VERSION"] == "20260915_v1"
    monkeypatch.delenv("RAG_ACTIVE_VERSION", raising=False)
    monkeypatch.delenv("RAG_VERSION_SOURCE", raising=False)


def test_version_source_rejects_illegal_value():
    """B4：非法取值必须在配置校验期报错，而不是静默回落。"""
    s = _settings(version_source_hint="cli")      # 拼错
    with pytest.raises(ValueError) as err:
        s.validate()
    assert "RAG_VERSION_SOURCE" in str(err.value)


def test_cors_warning_is_non_blocking():
    """隐式生产仍要留下可见告警（health.warnings + 启动日志），只是不阻断。"""
    s = _settings(require_active_version=True, require_active_version_explicit=False,
                  cors_allow_origins=["*"], allow_public_cors=False)
    assert s.cors_warning() and "CORS_ALLOW_ORIGINS=*" in s.cors_warning()

    ok = _settings(cors_allow_origins=["https://example.com"])
    assert ok.cors_warning() is None


def test_explicit_empty_cors_origins_is_rejected():
    """B8：`CORS_ALLOW_ORIGINS=`（显式空串）必须报错，不能静默变成通配符。"""
    s = _settings(cors_allow_origins=[])
    with pytest.raises(ValueError) as err:
        s.validate()
    assert "CORS_ALLOW_ORIGINS" in str(err.value)


def test_loading_settings_with_empty_cors_env_is_rejected(monkeypatch):
    """B8：从环境变量读到空串时，get_settings 也必须直接失败。"""
    from config.settings import get_settings

    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    with pytest.raises(ValueError):
        get_settings()
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)


def test_health_schema_is_stable_when_runtime_missing():
    """B8：runtime 加载失败时 health 的键集合必须与正常态一致（零值对象）。"""
    from fastapi.testclient import TestClient

    import server.api as api_mod

    previous_runtime = getattr(api_mod.app.state, "runtime", None)
    previous_error = getattr(api_mod.app.state, "load_error", None)
    api_mod.app.state.runtime = None
    api_mod.app.state.load_error = "模拟：runtime 构建失败"
    try:
        # 不用 with（那会跑 lifespan 并把 runtime 重新建起来）
        client = TestClient(api_mod.app)
        failed = client.get("/api/health").json()
    finally:
        api_mod.app.state.runtime = previous_runtime
        api_mod.app.state.load_error = previous_error

    assert failed["status"] == "error"
    for key in ("cache", "sync_pool", "rate_limit", "demo_ready", "version_selection",
                "config_fingerprint", "artifact_manifest_sha256"):
        assert key in failed, f"错误态 health 缺少键 {key}（schema 不稳定）"
    assert failed["cache"]["entries"] == 0
    assert failed["sync_pool"]["active"] == 0
    assert set(failed["cache"]) == {"entries", "max_entries", "evictions", "ttl_seconds"}


def test_cors_gate_actually_raises_on_import(monkeypatch):
    """R5-5 端到端：显式生产 + 通配 CORS 时导入 server.api 必须直接失败（不是只打日志）。"""
    import subprocess
    import sys

    env = {**os.environ,
           "RAG_REQUIRE_ACTIVE_VERSION": "true",
           "RAG_ACTIVE_VERSION": "20260915_v1",
           "CORS_ALLOW_ORIGINS": "*",
           "ALLOW_PUBLIC_CORS": "false"}
    proc = subprocess.run([sys.executable, "-c", "import server.api"],
                          cwd=str(Path(__file__).resolve().parent.parent),
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode != 0, "显式生产 + 通配 CORS 不得静默启动"
    assert "CORS 配置被拒绝" in (proc.stderr + proc.stdout)


def test_smoke_output_symbols_are_encoding_safe(monkeypatch):
    """R5-1：GBK/ASCII 控制台不得因 ✓/✗ 抛 UnicodeEncodeError。"""
    from scripts import smoke_deploy

    class _FakeStdout:
        def __init__(self, encoding):
            self.encoding = encoding
            self.written: list = []

        def write(self, text):
            # 模拟真实控制台：用声明的编码解码，编码不了就抛
            text.encode(self.encoding)
            self.written.append(text)

        def flush(self):
            pass

    monkeypatch.setattr(smoke_deploy.sys, "stdout", _FakeStdout("utf-8"))
    assert smoke_deploy._output_symbols() == ("✓", "✗")

    monkeypatch.setattr(smoke_deploy.sys, "stdout", _FakeStdout("gbk"))
    ok_prefix, fail_prefix = smoke_deploy._output_symbols()
    assert (ok_prefix, fail_prefix) == ("[OK]", "[FAIL]")
    # 安全打印不得抛错（GBK 控制台里打印中文与 [FAIL] 都能编码）
    smoke_deploy._safe_print("  [FAIL] 请求失败：连接被拒绝")

