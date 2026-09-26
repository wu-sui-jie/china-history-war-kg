"""凭证撤销查询：向旧后端确认"这张 token 现在还作不作数"（方案 B：Token Introspection）。

## 为什么需要这一步

验签（`server/auth.py`）解决的问题是"知道 /rag/ 地址就能调问答接口"，但验签只证明
**这张 token 是旧后端用那把密钥签的**，它证明不了这张 token 现在还该不该被承认：

    管理员停用某账号（或本人改了密码）→ 旧后端立刻拒绝该 token
                                        → RAG 仍然放行，直到 JWT 自然过期（默认 7 天）

安全动作在一半的系统上生效等于没生效。旧后端已经具备完整的判定（`disabled` +
`token_version`），本模块负责把那个判定搬到 RAG 的请求路径上。

## 为什么是"查后端"而不是"本地读库"

RAG 与旧后端各有自己的进程与数据边界，直接打开 backend 的 SQLite 会把两个服务
耦合成"必须同机、同文件权限、同 schema 版本"。走一个受服务密钥保护的内部接口，
耦合面只有一处 HTTP 契约。也考虑过共享 Redis（文档方案 C），但当前是单机部署，
为一次带缓存的查询引入新中间件不划算；将来多实例时再按文档换。

## 三件事必须一起做，缺一件这套设计就站不住

1. **缓存**。每个 RAG 请求都去问一次后端，等于把 RAG 的 QPS 变成后端的 QPS。
   按 token 缓存 `introspect_ttl_seconds`（默认 30 秒），撤销生效延迟的上界就是这个 TTL。
   缓存键用 token 的 **sha256**，不是 token 本身：内存里的字典可能被 dump 进崩溃报告。
2. **失败策略显式**。后端连不上时放行还是拒绝，必须由部署方选（`RAG_INTROSPECT_FAIL_MODE`）。
   默认 `closed`——这是一条安全查询，"把后端打挂"不该成为一种绕过撤销的手段。
   选 `open` 的理由也成立（可用性优先），但那是一个需要写下来的决定。
3. **不缓存失败**。查询失败不是"这张凭证无效"，把失败当结果缓存下来会让后端的一次
   抖动变成这段时间内人人被拒；反过来缓存"失败=有效"则更糟。失败路径每次都重新问。

## 与 `RAG_AUTH_MODE` 的关系

三个档位都适用，但只有 `jwt` 档下本服务才验签，因此只有 jwt 档会走到这里。
`nginx` / `disabled` 档不验签，也就没有"这张 token 该不该承认"这个问题。
`introspect_url` 与 `introspect_service_key` 任一为空 = 不启用查询，
此时 /api/health 会明确报出"撤销延迟到 token 到期"的边界（见 api.health 的 warnings）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("rag.auth.introspect")

# 缓存条目上限。超出后按插入顺序淘汰最旧的一条（dict 保序），
# 防止"每个请求带一个新 token"把内存撑爆。默认 2048 条 ≈ 几 KB 的字符串哈希。
DEFAULT_MAX_ENTRIES = 2048

# 失败结论的负缓存时长（秒）。取小值：它只影响"后端不可用期间"的重试频率，
# 不影响结论（结论由 fail_mode 决定，缓存与否完全一致）。见 Introspector.__init__。
FAILURE_CACHE_SECONDS = 5.0

# 可用的失败策略取值。写成常量而不是散落的字符串比较，避免拼错时静默落到某一边。
FAIL_CLOSED = "closed"
FAIL_OPEN = "open"
FAIL_MODES = (FAIL_CLOSED, FAIL_OPEN)


@dataclass(frozen=True)
class Verdict:
    """一次查询的结论。`reason` 只用于服务端日志，不回给客户端。

    `unavailable` 用来把**两种"不活跃"分开**：

    - `active=False, unavailable=False` → 后端明确判定这张凭证已失效（撤销）→ 401；
    - `active=False, unavailable=True`  → 本服务**无法确认**凭证状态（后端抖动/重启）
      → 503，客户端该重试而不是重新登录。

    两者都回 401「登录已失效，请重新登录」会把用户与运维都指向错误的方向：
    后端抖一下全体用户被登出，而重新登录拿到的 token 仍会被同样拒绝。
    """

    active: bool
    reason: str = ""
    unavailable: bool = False


class Introspector:
    """带缓存的凭证状态查询器。

    线程/协程安全性：缓存是普通 dict，只在事件循环里被读写（本服务的问答路径都在
    同一个 loop 上），因此不加锁。将来若引入多 loop 或线程执行，这里需要一并改
    ——所以把这句话写在这里，而不是留给下一个人去猜。
    """

    def __init__(self, *, url: str, service_key: str, ttl_seconds: float = 30.0,
                 fail_mode: str = FAIL_CLOSED, timeout_seconds: float = 3.0,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 failure_cache_seconds: float = FAILURE_CACHE_SECONDS):
        self.url = (url or "").strip()
        self.service_key = (service_key or "").strip()
        self.ttl_seconds = float(ttl_seconds)
        self.fail_mode = fail_mode if fail_mode in FAIL_MODES else FAIL_CLOSED
        self.timeout_seconds = float(timeout_seconds)
        self.max_entries = max(1, int(max_entries))
        # 失败结果的秒级负缓存。它不是"把失败当结论缓存"：
        # 缓存下来的仍然是**按失败策略得出的那个结论**，与不缓存时逐次判定的结果完全一致，
        # 只是不再让每个请求都在 3 秒超时上排一次队（后端不可用时这是纯浪费）。
        self.failure_cache_seconds = max(0.0, float(failure_cache_seconds))
        self._cache: dict[str, tuple[float, Verdict]] = {}
        # 在途查询表：按 token 摘要合并并发查询（single-flight）
        self._inflight: dict[str, "asyncio.Future"] = {}
        self._client = None            # httpx.AsyncClient，惰性创建
        self.stats = {"queries": 0, "hits": 0, "revoked": 0, "failures": 0}
        # 最近一次查询的成败：`failures>0` 只说明"历史上失败过"，
        # 而"现在后端是不是可达"要看这两个时刻——backend 重启后运维最想知道的正是
        # "它恢复了吗"，那时 counters 帮不上忙。
        self._last_ok_at: Optional[float] = None
        self._last_failure_at: Optional[float] = None
        self._last_failure_reason = ""

    @property
    def enabled(self) -> bool:
        """URL 与密钥都配好才算启用——半套配置不能算"已开启撤销检查"。"""
        return bool(self.url and self.service_key)

    @property
    def fail_closed(self) -> bool:
        return self.fail_mode == FAIL_CLOSED

    # ---- HTTP ----
    async def _post(self, token: str) -> Verdict:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        response = await self._client.post(
            self.url,
            json={"token": token},
            headers={"X-Internal-Service-Key": self.service_key},
        )
        if response.status_code != 200:
            # 401/503 说明**本服务这边**的配置不对（密钥不符、后端没启用该接口），
            # 不是"这张凭证无效"。按失败处理，让它走失败策略而不是被当成撤销。
            raise RuntimeError(f"introspect 返回 HTTP {response.status_code}")
        body = response.json()
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
            raise RuntimeError("introspect 响应缺少布尔字段 data.active")
        if data["active"]:
            return Verdict(True)
        return Verdict(False, "后端判定该凭证已失效")

    async def check(self, token: str) -> Verdict:
        """查询 `token` 的当前状态；任何内部故障都按 `fail_mode` 折算成结论。"""
        if not self.enabled or not token:
            return Verdict(True)

        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        now = time.monotonic()   # 进程内缓存，用单调时钟是对的（不落库、不跨重启）
        if cached is not None and cached[0] > now:
            self.stats["hits"] += 1
            return cached[1]

        # single-flight：同一个 token 的并发请求只发一次后端查询，其余 await 同一个 Future。
        # 为什么需要：TTL 到期瞬间，同一用户的多个并发请求会各自发起一次查询；
        # 后端不可用时更糟——每个请求都要在 3 秒超时上排队，而这发生在限流之前。
        inflight = self._inflight.get(key)
        if inflight is not None:
            # shield：等的一方被取消（客户端断连）不该把共享的那次查询也取消掉
            return await asyncio.shield(inflight)

        future: "asyncio.Future" = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            verdict = await self._query(token, key)
        finally:
            self._inflight.pop(key, None)
            if not future.done():
                future.set_result(verdict)
        return verdict

    async def _query(self, token: str, key: str) -> Verdict:
        """真正发一次查询（`check` 的在途去重之后走到这里）。"""
        now = time.monotonic()
        self.stats["queries"] += 1
        try:
            verdict = await self._post(token)
        except Exception as exc:  # noqa: BLE001 - 任何异常都归结为"无法确认状态"
            self.stats["failures"] += 1
            self._last_failure_at = time.time()
            self._last_failure_reason = _public_reason(exc)
            if self.fail_closed:
                logger.warning("⚠️ 凭证状态查询失败，按失败策略拒绝本次请求：%s", exc)
                verdict = Verdict(False, "无法确认凭证状态（后端不可用）", unavailable=True)
            else:
                logger.warning("⚠️ 凭证状态查询失败，按失败策略放行本次请求：%s", exc)
                verdict = Verdict(True, "无法确认凭证状态（按 open 策略放行）", unavailable=True)
            # 失败结论只缓存**秒级**：缓存的是"按策略得出的结论"，与逐次判定一致，
            # 但避免后端不可用时每个请求都排一次超时（见 __init__ 的说明）
            if self.failure_cache_seconds > 0:
                self._cache[key] = (now + self.failure_cache_seconds, verdict)
                self._prune(now)
            return verdict

        self._last_ok_at = time.time()
        self._last_failure_reason = ""
        self._store(key, verdict, now)
        if not verdict.active:
            self.stats["revoked"] += 1
        return verdict

    def _store(self, key: str, verdict: Verdict, now: float) -> None:
        self._cache[key] = (now + self.ttl_seconds, verdict)
        self._prune(now)

    def _prune(self, now: float) -> None:
        """清掉过期项；仍然超限时按插入顺序丢最旧的一批。"""
        if len(self._cache) <= self.max_entries:
            expired = [k for k, (expires, _) in self._cache.items() if expires <= now]
            for k in expired:
                self._cache.pop(k, None)
            return
        # 超限时不再逐条判断过期：直接把最旧的一批丢掉（插入顺序 ≈ 使用顺序）
        overflow = len(self._cache) - self.max_entries
        for k in list(self._cache.keys())[:overflow]:
            self._cache.pop(k, None)

    def snapshot(self) -> dict:
        """/api/health 用的状态快照（不含任何 token 或它的哈希）。

        `last_ok_at` / `last_failure_at` / `last_failure_reason` 是 §2.9 要的
        "backend 可达状态"：计数器只回答"历史上失败过几次"，运维要的是"现在通不通、
        上次不通是什么时候、为什么"。
        """
        return {
            "enabled": self.enabled,
            "url_configured": bool(self.url),
            "key_configured": bool(self.service_key),
            "ttl_seconds": self.ttl_seconds,
            "fail_mode": self.fail_mode,
            "cached_entries": len(self._cache),
            "inflight": len(self._inflight),
            "last_ok_at": self._last_ok_at,
            "last_failure_at": self._last_failure_at,
            "last_failure_reason": self._last_failure_reason,
            **self.stats,
        }

    async def aclose(self) -> None:
        """关闭连接池（停机时由 api.lifespan 调用）。"""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


def _public_reason(exc: Exception, limit: int = 200) -> str:
    """给 health / 日志用的失败原因：**只取第一行并截断**。

    异常文本可能带上内部 URL、端口甚至路径；health 是运维看的（不是公开接口），
    但没必要把这些细节复制进一个会被采集的字段。第一行足够区分
    "连接被拒 / 超时 / HTTP 401 / 响应形状不对"这几类。
    """
    text_of_error = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text_of_error[:limit]


def from_settings(settings) -> Introspector:
    """按配置构造查询器（api.py 的单一入口，避免取值口径散落各处）。

    密钥名接受两个：`RAG_INTERNAL_SERVICE_KEY` 是本服务自己的名字；
    `INTERNAL_SERVICE_KEY` 是旧后端 backend/.env 里的名字。单机部署时同一台机器上
    往往只配一次，两个名字都认可以让部署方少一次复制粘贴错误——
    而这类"两边密钥不一致"的错误表现为问答全 401，最难一眼看出。
    """
    return Introspector(
        url=getattr(settings, "introspect_url", ""),
        service_key=getattr(settings, "introspect_service_key", ""),
        ttl_seconds=getattr(settings, "introspect_ttl_seconds", 30.0),
        fail_mode=getattr(settings, "introspect_fail_mode", FAIL_CLOSED),
        timeout_seconds=getattr(settings, "introspect_timeout_seconds", 3.0),
    )


def extract_reason(verdict: Verdict) -> Optional[str]:
    """给日志用的原因（没有原因时返回 None，让调用方的日志格式统一）。"""
    return verdict.reason or None
