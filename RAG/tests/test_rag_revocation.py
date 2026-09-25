"""凭证撤销查询器（第 13 轮复核，文档第四节方案 B）。

`server/introspection.py` 是"验签通过 ≠ 仍然有效"这条判断的落点：它向后端的内部接口
问一句"这张 token 现在还作不作数"，并按 TTL 缓存答案。这里考的是它自己的行为，
不经过 HTTP：`_post` 被换成替身，因此用例不依赖网络也不依赖后端服务。

四组断言：

1. **未配置就不拦人**：URL/密钥任一为空 = 不查询，行为与改造前一致；
2. **缓存**：TTL 内只问一次（否则 RAG 的 QPS 会变成后端的 QPS），且键是摘要不是明文；
3. **失败策略**：closed 拒绝、open 放行，且失败**不写缓存**（一次抖动不该变成一段时间的结果）；
4. **响应形状**：非 200、缺字段都按失败处理，不能猜。
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from server import introspection
from server.introspection import Introspector, Verdict

URL = "http://127.0.0.1:5000/api/internal/token/introspect"
KEY = "service-key-for-test"


class _Recorder:
    """记录调用次数的 `_post` 替身。"""

    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else Verdict(True)
        self.exc = exc
        self.calls: list[str] = []

    async def __call__(self, token):
        self.calls.append(token)
        if self.exc is not None:
            raise self.exc
        return self.result


def _client(**kwargs) -> Introspector:
    params = {"url": URL, "service_key": KEY, "ttl_seconds": 30.0}
    params.update(kwargs)
    return Introspector(**params)


def _run(coro):
    return asyncio.run(coro)


def test_未配置时不查询也不拦人():
    """半套或空配置 = 未启用：这是内网默认，行为必须与改造前一致。"""
    for url, key in (("", ""), (URL, ""), ("", KEY)):
        client = Introspector(url=url, service_key=key)
        recorder = _Recorder()
        client._post = recorder

        verdict = _run(client.check("some-token"))

        assert client.enabled is False
        assert verdict.active is True
        assert recorder.calls == []


def test_后端判定失效时返回不活跃():
    client = _client()
    client._post = _Recorder(Verdict(False, "后端判定该凭证已失效"))

    verdict = _run(client.check("some-token"))

    assert verdict.active is False
    assert verdict.reason


def test_未携带_token_时直接放行():
    """没有 token 就没有"该不该承认"这个问题；这里返回放行，让上层的规则决定结果。"""
    client = _client()
    client._post = _Recorder()

    assert _run(client.check("")).active is True


def test_ttl_内复用缓存(monkeypatch):
    """缓存是这套设计成立的前提：每个请求都问一次，后端要承受 RAG 的全部 QPS。"""
    clock = [1000.0]
    monkeypatch.setattr(introspection.time, "monotonic", lambda: clock[0])
    client = _client(ttl_seconds=30.0)
    recorder = _Recorder(Verdict(True))
    client._post = recorder

    for _ in range(5):
        assert _run(client.check("t")).active is True

    assert len(recorder.calls) == 1
    assert client.stats["hits"] == 4


def test_ttl_过期后重新查询(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(introspection.time, "monotonic", lambda: clock[0])
    client = _client(ttl_seconds=30.0)
    recorder = _Recorder(Verdict(True))
    client._post = recorder

    _run(client.check("t"))
    clock[0] += 31
    _run(client.check("t"))

    assert len(recorder.calls) == 2


def test_缓存键是摘要而不是_token_明文():
    """内存里的字典可能进崩溃转储或诊断输出，放明文 token 等于把凭证抄一份。"""
    client = _client()
    client._post = _Recorder(Verdict(True))
    token = "header.payload.signature"

    _run(client.check(token))

    keys = list(client._cache.keys())
    assert keys == [hashlib.sha256(token.encode("utf-8")).hexdigest()]
    assert token not in keys


def test_fail_closed_在后端不可用时拒绝():
    """默认策略：无法确认状态时拒绝。

    这是有意的取舍——"把后端打挂"不该成为一种绕过撤销的手段；代价是后端故障期间
    问答不可用，因此 health 会把当前策略报出来。
    """
    client = _client(fail_mode="closed")
    client._post = _Recorder(exc=RuntimeError("connection refused"))

    verdict = _run(client.check("t"))

    assert verdict.active is False
    assert "无法确认" in verdict.reason


def test_fail_open_在后端不可用时放行():
    client = _client(fail_mode="open")
    client._post = _Recorder(exc=RuntimeError("connection refused"))

    assert _run(client.check("t")).active is True


def test_失败不写缓存(monkeypatch):
    """把失败当结果缓存下来，会让后端的一次抖动变成这段时间内人人被拒（或被放行）。"""
    clock = [1000.0]
    monkeypatch.setattr(introspection.time, "monotonic", lambda: clock[0])
    client = _client(ttl_seconds=30.0, fail_mode="closed")
    recorder = _Recorder(exc=RuntimeError("boom"))
    client._post = recorder

    _run(client.check("t"))
    _run(client.check("t"))

    assert len(recorder.calls) == 2
    assert client._cache == {}
    assert client.stats["failures"] == 2


def test_缓存条目上限会淘汰最旧的(monkeypatch):
    """带新 token 的请求不能把内存撑爆（每个 token 一个条目）。"""
    clock = [1000.0]
    monkeypatch.setattr(introspection.time, "monotonic", lambda: clock[0])
    client = _client(ttl_seconds=3600.0, max_entries=2)
    client._post = _Recorder(Verdict(True))

    for token in ("a", "b", "c"):
        _run(client.check(token))

    assert len(client._cache) == 2
    assert hashlib.sha256(b"a").hexdigest() not in client._cache


def test_已撤销的结论也会被缓存():
    """反向查询同样要缓存：被撤销的 token 若每次都回查，等于给后端留了一个稳定的放大器。"""
    client = _client()
    recorder = _Recorder(Verdict(False, "已失效"))
    client._post = recorder

    for _ in range(3):
        assert _run(client.check("t")).active is False

    assert len(recorder.calls) == 1
    assert client.stats["revoked"] == 1


def test_snapshot_不含_token_或其摘要():
    client = _client()
    client._post = _Recorder(Verdict(True))
    _run(client.check("secret-token"))

    snapshot = client.snapshot()

    assert client.snapshot()["enabled"] is True
    assert not any("secret" in str(value) for value in snapshot.values())


@pytest.mark.parametrize("problem", ["响应不是 JSON", "缺少 data.active", "data.active 不是布尔值"])
def test_响应形状不对按失败处理(problem):
    """后端返回了但内容看不懂——不能猜，必须走失败策略。"""
    client = _client(fail_mode="closed")

    async def fake_post(token):
        raise RuntimeError(problem)

    client._post = fake_post

    assert _run(client.check("t")).active is False


def test_非_200_响应不算凭证无效():
    """401/503 说明本服务这边配置不对（密钥错、后端没启用该接口），不是凭证被撤销。

    两者混为一谈的后果：一个配错的密钥会让所有人被登出，排障方向还指向"账号出了问题"。
    """
    client = _client(fail_mode="open")

    async def fake_post(token):
        raise RuntimeError("introspect 返回 HTTP 401")

    client._post = fake_post

    # 走失败策略（open → 放行），而不是被当成"已撤销"拒绝
    assert _run(client.check("t")).active is True


def test_从配置构造时两个密钥名都认(monkeypatch):
    """RAG_INTERNAL_SERVICE_KEY 与 backend 的 INTERNAL_SERVICE_KEY 是同值两名的同一个东西，
    少一次改名就少一次"两边密钥不一致导致问答全 401"的踩坑机会。"""
    from config.settings import get_settings

    monkeypatch.setenv("RAG_INTROSPECT_URL", URL)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", KEY)
    monkeypatch.delenv("RAG_INTERNAL_SERVICE_KEY", raising=False)

    client = introspection.from_settings(get_settings())

    assert client.enabled is True
    assert client.service_key == KEY


def test_未配置时从配置构造也保持关闭(monkeypatch):
    from config.settings import get_settings

    for name in ("RAG_INTROSPECT_URL", "RAG_INTERNAL_SERVICE_KEY", "INTERNAL_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert introspection.from_settings(get_settings()).enabled is False
