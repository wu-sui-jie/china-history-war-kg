"""SSE 事件序列化与查询编排（server/sse.py）。

`run_query(runtime, req)` 是纯异步生成器：逐步产出 SSE 事件字符串，
由 api.py 用 StreamingResponse 输出。这样编排逻辑不依赖 web 框架，便于测试。

事件顺序严格按 data-contract.md：
session_start → status(entity_linking) → entities →
  命中：status(cache_hit) → answer → citations → panel → done；
  未命中：status(graph_search) → graph_results → status(text_search) → text_results →
    status(fusion) → fusion → status(generating) → answer 增量 → citations → panel → done
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional

from config.settings import Settings
from contracts.request import QueryRequest
from contracts.sse import (
    ErrorCode,
    FinishReason,
    SSEEvent,
    SSEEventType,
    StatusStage,
)
from contracts.question import QuestionType
from lib.redact import public_text
from server.runtime import Runtime

# 日志 handler 由 server.api 在启动时统一配置（rag.* 命名空间，见 lib/logging_util）
logger = logging.getLogger("rag.sse")


# query 级错误（不可恢复，直接 error+done）
def _event(type_: SSEEventType, session_id: str, stage=None, data=None) -> dict:
    ev = SSEEvent(type=type_, session_id=session_id, stage=stage, data=data)
    return ev.to_dict()


def sse_format(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


class SyncPoolBusy(RuntimeError):
    """同步工作池排队已满：拒绝新的同步任务，避免无限堆积。"""


class SyncWorkPool:
    """有界、可观测的同步工作线程池。

    为什么不用默认 executor：`run_in_executor(None, ...)` 的队列没有上限，
    而"断连后仍在跑"的 embedding / Chroma / SQLite 调用不会因为 Future 被取消而停止，
    于是大量断连请求可以把默认线程池（进程内共享）占满，连累其他用法。
    这里给出独立执行器 + 排队上限 + 计数：
    - max_workers：并发上限；
    - max_queue：排队上限，超出直接拒绝（上层以 server_busy 收尾，而不是无限等待）。
    """

    def __init__(self, max_workers: int, max_queue: int):
        self.max_workers = max(1, int(max_workers))
        self.max_queue = max(0, int(max_queue))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="rag-sync")
        # 用 RLock 而不是 Lock：Future.cancel() 会在**持锁路径上同步触发** done 回调，
        # 回调 _forget 需要同一把锁；非重入锁会在 note_cancel/begin_drain 里自锁死
        # （实测：整份测试套件卡在 test_sync_pool_cancel_before_start_frees_queue_slot）。
        self._lock = threading.RLock()
        # 分离计数：active = 正在执行的线程任务；queued = 已提交未开始；
        # in_flight = active + queued（容量判定用后者）
        self._active = 0
        self._queued = 0
        self._peak_active = 0
        self._peak_in_flight = 0
        self._completed = 0
        self._rejected = 0
        self._cancelled_before_start = 0
        self._running_after_disconnect = 0
        self._drained_out = 0
        self._closed = False
        self._draining = False
        # 未完成任务句柄：停机时要能逐个 cancel()，否则排队任务仍会在客户端关闭后开跑
        self._futures: "set" = set()
        # "彻底空闲"事件（active == 0 且 queued == 0）：等待方阻塞在 Event 上，
        # 用轮询等待不行——轮询若发生在 async 路径上，会把事件循环按
        # SHUTDOWN_DRAIN_SECONDS 的时长整个卡住
        self._idle = threading.Event()
        self._idle.set()

    def _refresh_idle_locked(self) -> None:
        """调用方必须持锁：按 active/queued 重算空闲事件。

        只看 active 是不够的：排队任务随时可能被 worker 捞起来执行，
        提前判空会让"收尾完成"变成假信号。
        """
        if self._active == 0 and self._queued == 0:
            self._idle.set()
        else:
            self._idle.clear()

    def submit(self, fn, *args, **kwargs):
        with self._lock:
            if self._closed:
                self._rejected += 1
                raise SyncPoolBusy("同步工作池已关闭")
            if self._draining:
                # 停机中：不再接收新任务，让调用方立刻拿到 server_busy 而不是排队等死
                self._rejected += 1
                raise SyncPoolBusy("同步工作池正在停机，不再接收新任务")
            in_flight = self._active + self._queued
            if in_flight >= self.max_workers + self.max_queue:
                self._rejected += 1
                raise SyncPoolBusy(
                    f"同步工作池繁忙（在途 {in_flight} > 上限 "
                    f"{self.max_workers + self.max_queue}），请稍后重试"
                )
            self._queued += 1
            self._peak_in_flight = max(self._peak_in_flight, in_flight + 1)
            self._refresh_idle_locked()

            def _wrapped(*a, **kw):
                # 任务真正开始时才从 queued 转入 active
                with self._lock:
                    self._queued = max(0, self._queued - 1)
                    self._active += 1
                    self._peak_active = max(self._peak_active, self._active)
                    self._refresh_idle_locked()
                try:
                    return fn(*a, **kw)
                finally:
                    with self._lock:
                        self._active = max(0, self._active - 1)
                        self._completed += 1
                        self._refresh_idle_locked()

            # 提交与登记必须在**同一个临界区**里完成（B2）：否则 begin_drain 的快照
            # 可能落在"已提交、未登记"之间，该排队任务逃过撤销并可能在客户端关闭后开跑
            try:
                future = self._executor.submit(_wrapped, *args, **kwargs)
            except BaseException:
                self._queued = max(0, self._queued - 1)
                self._refresh_idle_locked()
                raise
            self._futures.add(future)
        future.add_done_callback(self._forget)
        return future

    def _forget(self, future) -> None:
        with self._lock:
            self._futures.discard(future)

    def note_cancel(self, future) -> None:
        """调用方在等待期间被取消：能撤就撤掉尚未开始的任务，并记账。"""
        with self._lock:
            if future.cancel():
                # 尚未开始：ThreadPoolExecutor 不会再执行它，queued 计数由 _wrapped
                # 的补偿路径归还（这里直接扣减以避免永久占用队列名额）
                self._queued = max(0, self._queued - 1)
                self._cancelled_before_start += 1
                self._drained_out += 1
                self._refresh_idle_locked()
            else:
                # 已经在跑：同步函数无法中断，只能等它自己超时（记为观测项）
                self._running_after_disconnect += 1

    def begin_drain(self) -> int:
        """停止接收新任务，并撤销所有尚未开始的排队任务；返回被撤销条数。

        停机顺序的第一、二步：先关闸，再把"还没开跑"的任务撤掉。
        撤销必须在关闭外部客户端**之前**完成，否则这些任务会在客户端已关闭后开跑
        （同步函数无法中断，只能先于客户端关闭把它们清掉）。
        """
        with self._lock:
            self._draining = True
            futures = list(self._futures)
        cancelled = 0
        for future in futures:
            with self._lock:
                if future.cancel():
                    self._queued = max(0, self._queued - 1)
                    self._cancelled_before_start += 1
                    self._drained_out += 1
                    cancelled += 1
                    self._refresh_idle_locked()
        return cancelled

    def wait_idle(self, timeout: float) -> bool:
        """有上限地等待**彻底空闲**（active 与 queued 都为 0）；返回是否达成。

        同步版本（供非 async 调用方与测试使用）。不要用
        `executor.shutdown(wait=True)`：那会无限期阻塞退出流程，
        而同步调用（embedding HTTP / Chroma / SQLite）最坏要等自身超时。
        """
        return self._idle.wait(max(0.0, float(timeout)))

    async def wait_idle_async(self, timeout: float) -> bool:
        """`wait_idle` 的异步版本：用 `asyncio.sleep` 让出事件循环（B2）。

        async 路径（`Runtime.shutdown`）必须用它——停机期间事件循环仍要服务
        正在收尾的 SSE 流与健康检查，被 `time.sleep` 卡住等于整站冻结。
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if self._idle.is_set():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.02, remaining))

    def stats(self) -> dict:
        with self._lock:
            return {
                "initialized": True,
                "scope": "process",       # 进程级单例；多实例部署各自统计
                "max_workers": self.max_workers,
                "max_queue": self.max_queue,
                "active": self._active,
                "queued": self._queued,
                "in_flight": self._active + self._queued,
                "peak_active": self._peak_active,
                "peak_in_flight": self._peak_in_flight,
                "completed": self._completed,
                "rejected": self._rejected,
                "cancelled_before_start": self._cancelled_before_start,
                "running_after_disconnect": self._running_after_disconnect,
                "drained_out": self._drained_out,
                "draining": self._draining,
                "closed": self._closed,
            }

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._draining = True
        self._executor.shutdown(wait=wait, cancel_futures=True)


_sync_pool: Optional[SyncWorkPool] = None
_sync_pool_lock = threading.Lock()


def get_sync_pool(settings: Optional[Settings] = None) -> SyncWorkPool:
    """进程内单例同步工作池（首次调用时按配置创建）。"""
    global _sync_pool
    with _sync_pool_lock:
        if _sync_pool is None:
            if settings is None:
                from config.settings import get_settings

                settings = get_settings()
            _sync_pool = SyncWorkPool(
                max_workers=getattr(settings, "sync_pool_max_workers", 8),
                max_queue=getattr(settings, "sync_pool_max_queue", 32),
            )
        return _sync_pool


async def shutdown_sync_pool_async(drain_seconds: float = 0.0) -> dict:
    """`shutdown_sync_pool` 的异步版本：等待期间让出事件循环。

    async 路径（`Runtime.shutdown`）必须用它：停机时事件循环还要服务正在收尾的
    SSE 流与健康检查，用同步轮询等待会把整个进程冻结 `drain_seconds` 那么久。
    """
    global _sync_pool
    with _sync_pool_lock:
        pool, _sync_pool = _sync_pool, None
    if pool is None:
        return {"initialized": False, "cancelled_queued": 0, "drained": True, "active_left": 0}
    cancelled = pool.begin_drain()
    drained = await pool.wait_idle_async(max(0.0, float(drain_seconds)))
    active_left = pool.stats()["active"]
    pool.shutdown(wait=False)
    return {
        "initialized": True,
        "cancelled_queued": cancelled,
        "drained": drained,
        "active_left": active_left,
    }


def shutdown_sync_pool(drain_seconds: float = 0.0) -> dict:
    """关闭同步工作池（同步调用方：脚本、测试、无 Runtime 的 lifespan 兜底）。

    停机顺序：
    1. 停止接收新任务（begin_drain 置位 draining）；
    2. 撤销全部尚未开始的排队任务；
    3. 用 `drain_seconds` 上限等待彻底空闲（active 与 queued 都为 0）；
    4. 关闭执行器（不等待）。

    调用方必须**先**执行本函数、**再**关闭外部 HTTP 客户端：反过来会让在途任务
    拿着已关闭的客户端发请求。返回值是这次收尾的观测快照，便于写进日志/健康报告。
    """
    global _sync_pool
    with _sync_pool_lock:
        pool, _sync_pool = _sync_pool, None
    if pool is None:
        return {"initialized": False, "cancelled_queued": 0, "drained": True, "active_left": 0}
    cancelled = pool.begin_drain()
    drained = pool.wait_idle(max(0.0, float(drain_seconds)))
    active_left = pool.stats()["active"]
    pool.shutdown(wait=False)
    return {
        "initialized": True,
        "cancelled_queued": cancelled,
        "drained": drained,
        "active_left": active_left,
    }


def empty_sync_pool_stats(settings: Optional[Settings] = None) -> dict:
    """同步池尚未创建时的**零值统计**，字段与 `SyncWorkPool.stats()` 完全一致。

    为什么需要：池未创建时返回 `{}` 会让监控看到的 schema 随"是否已经跑过第一次查询"
    变化，无法写稳定的告警规则。
    """
    if settings is None:
        from config.settings import get_settings

        settings = get_settings()
    return {
        "initialized": False,
        "scope": "process",
        "max_workers": max(1, int(getattr(settings, "sync_pool_max_workers", 8))),
        "max_queue": max(0, int(getattr(settings, "sync_pool_max_queue", 32))),
        "active": 0,
        "queued": 0,
        "in_flight": 0,
        "peak_active": 0,
        "peak_in_flight": 0,
        "completed": 0,
        "rejected": 0,
        "cancelled_before_start": 0,
        "running_after_disconnect": 0,
        "drained_out": 0,
        "draining": False,
        "closed": False,
    }


def sync_pool_stats() -> dict:
    """同步池统计：无论是否已创建，都返回同一套字段。"""
    pool = _sync_pool
    return pool.stats() if pool is not None else empty_sync_pool_stats()


async def run_in_thread(fn, *args, **kwargs):
    """把同步阻塞调用下沉到有界线程池，避免冻结事件循环。

    需要隔离的同步点：F04 查询侧 embedding（云端 HTTP，含超时与重试）、F02 的 LLM 兜底、
    FTS5/Chroma 的同步查询以及图谱遍历。它们都在 async 编排里被直接调用，
    一个慢请求会卡住同一 worker 上的所有连接（含 /api/health）。

    不用 `asyncio.to_thread`：它跑在 asyncio 的默认线程池上，队列无上限、无法做容量隔离；
    本函数的价值就在「有界池 + 取消记账」，与 Python 版本无关（运行环境见 README，为 3.11）。
    也不用 `loop.run_in_executor(None, ...)`：同样是默认池，没有上限。
    """
    if kwargs:
        fn = functools.partial(fn, *args, **kwargs)
        args = ()
    pool = get_sync_pool()
    future = pool.submit(fn, *args)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        # 请求被取消（客户端断连/超时）：排队中的任务应立刻撤掉，别继续占队列名额；
        # 已经在跑的同步函数无法打断，只能等它自己超时。
        pool.note_cancel(future)
        raise


def _chunk_answer_stream(text: str, chunk_chars: int = 160):
    """把完整答案切成小段用于 SSE 增量（离线摘要回答器/拒答文案等非 token 流路径）。

    硬要求：**增量拼接必须严格等于原文**。若按句末标点切分时把标点后的空白/换行一起
    吃掉，流式拼回的答案会丢换行（实测比原文少 19 个字符，前端 markdown 列表会粘连），
    而缓存回放的是完整文本 → 两边不一致。
    """
    if not text:
        return
    import re

    # 切成"保留全部字符"的最小单元：整行（含行尾换行）+ 行内按句末标点切分
    units: list[str] = []
    for line in text.splitlines(keepends=True):
        start = 0
        for m in re.finditer(r"[。！？!?；;]+", line):
            units.append(line[start:m.end()])
            start = m.end()
        if start < len(line):
            units.append(line[start:])

    buf = ""
    for u in units:
        buf += u
        if len(buf) >= chunk_chars or u.rstrip("\n")[-1:] in ("。", "！", "？", "!", "?", "；", ";"):
            yield buf
            buf = ""
    if buf:
        yield buf


def _text_shares_any(evidence_list, q_words: set) -> bool:
    """判断任一文本证据与查询词是否有 ≥1 个共享词（≥2 字）。"""
    from data.index import fts as _fts
    for ev in evidence_list:
        text = (ev.content or {}).get("text") or ""
        ev_words = {w for w in _fts.tokenize(text) if len(w) >= 2}
        if ev_words & q_words:
            return True
    return False


async def run_query(runtime: Runtime, req: QueryRequest) -> AsyncIterator[str]:
    """编排一次完整查询，产出 SSE 帧序列。"""
    sid = req.session_id
    gen = runtime.generate
    settings = runtime.settings

    # 生成任务句柄：断连/异常时必须在 finally 里取消并回收
    gen_task: Optional[asyncio.Task] = None

    # ---- session_start ----
    yield sse_format(_event(SSEEventType.SESSION_START, sid,
                            stage=StatusStage.START.value))
    try:
        # ---- F02：问题理解 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.ENTITY_LINKING.value))
        t0 = time.time()
        # 历史统一口径：F02 理解、缓存键、F06 提示词共用同一份裁剪后历史
        # （history_max_turns 个 user 轮及其后助手消息），避免三个窗口不一致
        # 造成“实际上下文相同却互不命中缓存”的效率损耗。
        hist = runtime.question.trim_history(req.history)
        # F02 走线程：链内含词典匹配（jieba）与可选的 LLM 兜底（同步 HTTP，最长 8 s），
        # 直接 await 会阻塞整个事件循环。
        out = await run_in_thread(
            runtime.question.understand,
            req.question, history=hist,
            corrected=req.corrected_entities,
            filters=req.filters,
        )
        yield sse_format(_event(
            SSEEventType.ENTITIES, sid, stage=StatusStage.ENTITY_LINKING.value,
            data={"entities": [e.to_dict() for e in out.entities],
                  "candidates": [c.to_dict() for c in out.candidates],
                  "question_type": out.question_type.value if out.question_type else "unknown",
                  "rewritten_question": out.rewritten_question,
                  "dynasty_bias": list(out.dynasty_bias or []),
                  "llm_entity_used": bool(getattr(out, "llm_entity_used", False)),
                  "dynasty_disambiguated": bool(getattr(out, "dynasty_disambiguated", False)),
                  "elapsed_ms": int((time.time() - t0) * 1000)}))

        # 缓存检查（在检索前查，命中则回放）
        # 缓存键需包含王朝偏置：它会影响图谱/文本排序结果
        cache_filters = dict(out.filters.to_dict() if out.filters else {})
        if out.dynasty_bias:
            cache_filters["dynasty_bias"] = list(out.dynasty_bias)
        # 缓存键必须覆盖纠正后的实体与纠正指令本身：
        # add 类纠正不改写问题文本，只按问题做键会让"纠正后"命中"纠正前"的答案。
        cache_key = gen.check_cache(
            out.rewritten_question, hist, cache_filters or None,
            entities=[e.to_dict() for e in out.entities],
            corrections=req.corrected_entities,
            dynasty_bias=list(out.dynasty_bias or []),
        )
        cached = cache_key[0]

        if cached:
            yield sse_format(_event(SSEEventType.STATUS, sid,
                                    stage=StatusStage.CACHE_HIT.value))
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.CACHE_HIT.value,
                                    data={"delta": cached["answer"]}))
            yield sse_format(_event(SSEEventType.CITATIONS, sid,
                                    stage=StatusStage.CACHE_HIT.value,
                                    data={"citations": cached["citations"],
                                          "conflicts": cached["conflicts"]}))
            if cached.get("panel") is not None:
                yield sse_format(_event(SSEEventType.PANEL, sid,
                                        stage=StatusStage.CACHE_HIT.value,
                                        data=cached["panel"]))
            yield sse_format(_event(
                SSEEventType.DONE, sid,
                data={"finish_reason": cached["finish_reason"],
                      "model_used": cached.get("model_used", ""),
                      "cache_hit": True}))
            return

        # ---- F03 + F04：图谱/文本（事件顺序 graph_search 先于 text_search）----
        names = [e.standard_name or e.name for e in out.entities if e.standard_name or e.name]
        qtype: QuestionType = out.question_type or QuestionType.UNKNOWN
        filters = out.filters.to_dict() if out.filters else None
        bias = list(out.dynasty_bias or [])

        graph_result = None
        text_result = None

        from server.graph import search as gsearch
        from server.text import search as tsearch

        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.GRAPH_SEARCH.value))
        graph_result = await run_in_thread(
            gsearch, runtime.graph, names, qtype, filters=filters,
            top_k=settings.query_top_k_graph, dynasty_bias=bias)

        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.TEXT_SEARCH.value))
        # F04 同步点最多：查询侧 embedding 是云端 HTTP 调用（60 s 超时 + 3 次尝试），
        # 后面还有 Chroma 查询与 SQLite 取片段，全部下沉到线程。
        text_result = await run_in_thread(
            tsearch, runtime.text, out.rewritten_question or req.question,
            filters=filters, mode=settings.text_mode,
            top_k=settings.query_top_k_text,
            dynasty_bias=bias,
            hybrid_strategy=settings.text_hybrid_strategy,
            hybrid_keyword_weight=settings.text_hybrid_keyword_weight)

        # 推送检索结果事件
        yield sse_format(_event(
            SSEEventType.GRAPH_RESULTS, sid, stage=StatusStage.GRAPH_SEARCH.value,
            data={"evidence": [e.to_dict() for e in (graph_result.evidence if graph_result else [])],
                  "hit_entities": [h for h in (graph_result.hit_entities if graph_result else [])]}))

        yield sse_format(_event(
            SSEEventType.TEXT_RESULTS, sid, stage=StatusStage.TEXT_SEARCH.value,
            data={"evidence": [e.to_dict() for e in (text_result.evidence if text_result else [])],
                  "mode": (text_result.mode if text_result else "none")}))

        # ---- F05 融合 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.FUSION.value))
        fused, panel = runtime.fusion.assemble(graph_result, text_result, qtype,
                                               limit=settings.query_fusion_limit)

        yield sse_format(_event(
            SSEEventType.FUSION, sid, stage=StatusStage.FUSION.value,
            data={
                "evidence_count": len(fused.evidence),
                "conflicts": [c.to_dict() for c in fused.conflicts],
                "citation_index": [a.to_dict() for a in fused.citation_index],
            }))

        # ---- 拒答硬规则 ----
        from server.generate import refusal as refusal_mod
        # 1) 完全无证据 → 直接拒答
        if not fused.evidence:
            reason = f"知识库未检索到与「{req.question}」相关的史料，无法给出有依据的回答。"
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.GENERATING.value,
                                    data={"delta": reason}))
            yield sse_format(_event(
                SSEEventType.DONE, sid,
                data={"finish_reason": FinishReason.REFUSED.value, "model_used": ""}))
            return

        # 2) 实体为空 + 文本证据与问题无共享词 → 依据不足（拒绝给噪音回答）
        #    词法层面判断"证据与问题不相关"，避免 OR 兜底召回完全无关片段。
        #    阈值保守：query 与任一证据 text 共享 ≥1 个长度≥2 的关键词即放行。
        #    注意（RAGv5）：向量通道按语义召回，证据可能**不含问题里的任何词**，
        #    因此该规则只在关键词模式严格生效；vector/hybrid 下放宽为
        #    "无共享词 **且** 证据最高分低于阈值" 才拒答，避免误拒语义命中。
        if not names and text_result is not None and text_result.evidence:
            from data.index import fts as _fts
            q_words = {w for w in _fts.tokenize(req.question) if len(w) >= 2}
            mode = (text_result.mode or "keyword").lower()
            top_score = max((e.score or 0.0) for e in text_result.evidence)
            weak_score = top_score < settings.vector_refusal_min_score
            if q_words and not _text_shares_any(text_result.evidence, q_words) \
                    and (mode == "keyword" or weak_score):
                reason = (f"「{req.question}」未识别出知识库实体，且检索到的文本与问题不相关，"
                          "当前无法给出有依据的回答。建议换个说法或补充具体事件/人物名。")
                yield sse_format(_event(SSEEventType.ANSWER, sid,
                                        stage=StatusStage.GENERATING.value,
                                        data={"delta": reason}))
                yield sse_format(_event(
                    SSEEventType.DONE, sid,
                    data={"finish_reason": FinishReason.REFUSED.value, "model_used": ""}))
                return

        # 3) 领域外谓词：问的是知识库不可能覆盖的属性/器物
        #    （邮箱、电话、度假、坦克、股票…），且这些词在所有证据里都不出现 → 拒答。
        #    保守优先：证据里出现过该词就放行，避免把可答题误拒（X01–X03 应拒答却作答的补强）。
        scope_reason = refusal_mod.out_of_scope_reason(req.question, fused.evidence)
        if scope_reason:
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.GENERATING.value,
                                    data={"delta": scope_reason}))
            yield sse_format(_event(
                SSEEventType.DONE, sid,
                data={"finish_reason": FinishReason.REFUSED.value, "model_used": ""}))
            return

        # ---- F06 生成 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.GENERATING.value))

        gen_filters = out.filters.to_dict() if out.filters else None
        # 本次调用的统计（并发下不能用 gen.last_usage：那是"最近一次"口径，会互相覆盖）
        gen_stats: dict = {}
        expose_thinking = bool(getattr(settings, "expose_thinking", False))
        # 推理过程只做内部度量：公共 SSE 默认**不**输出原始 reasoning。
        # 保留首思考时延与长度用于观测，内容本身只有在 EXPOSE_THINKING 打开时才外发。
        think_metrics = {"frames": 0, "chars": 0, "first_ms": None}

        if gen.llm.available:
            # 真实 LLM：on_delta/on_thinking 经队列桥接进 SSE（token 级增量）。
            # run_query 是异步生成器，而 generate() 是 coroutine，故用 task + 队列边产边发。
            answer_q: asyncio.Queue = asyncio.Queue()
            think_q: asyncio.Queue = asyncio.Queue()
            t_gen = time.time()

            def _on_delta(text: str) -> None:
                answer_q.put_nowait(text)

            def _on_thinking(text: str) -> None:
                think_metrics["frames"] += 1
                think_metrics["chars"] += len(text or "")
                if think_metrics["first_ms"] is None:
                    think_metrics["first_ms"] = int((time.time() - t_gen) * 1000)
                if expose_thinking:
                    think_q.put_nowait(text)

            gen_task = asyncio.create_task(gen.generate(
                req.question, out.rewritten_question, fused.evidence,
                history=hist, filters=gen_filters,
                on_delta=_on_delta, on_thinking=_on_thinking,
                stats_out=gen_stats,
            ))
            # 首个增量的长度决定"是否真的是 token 级流式"：
            # 降级/启发式路径会一次性回调整段文本，此时退回按句切分，避免"一大坨瞬时出现"。
            split_mode: Optional[bool] = None
            while True:
                while expose_thinking and not think_q.empty():
                    yield sse_format(_event(
                        SSEEventType.THINKING, sid, stage=StatusStage.GENERATING.value,
                        data={"delta": think_q.get_nowait()}))
                while not answer_q.empty():
                    delta = answer_q.get_nowait()
                    if split_mode is None:
                        split_mode = len(delta) > 200
                    if split_mode:
                        for part in _chunk_answer_stream(delta):
                            yield sse_format(_event(
                                SSEEventType.ANSWER, sid,
                                stage=StatusStage.GENERATING.value,
                                data={"delta": part}))
                    else:
                        yield sse_format(_event(
                            SSEEventType.ANSWER, sid,
                            stage=StatusStage.GENERATING.value,
                            data={"delta": delta}))
                if gen_task.done() and answer_q.empty() and think_q.empty():
                    break
                await asyncio.sleep(0.02)   # 让出事件循环，等下一个增量
            finish_reason, model_used, full_answer = await gen_task
            gen_task = None
        else:
            # 无 LLM（离线摘要回答器/拒答）：一次性拿到全文，再按句切分模拟打字机
            finish_reason, model_used, full_answer = await gen.generate(
                req.question, out.rewritten_question, fused.evidence,
                history=hist, filters=gen_filters,
                stats_out=gen_stats,
            )
            for part in _chunk_answer_stream(full_answer):
                yield sse_format(_event(SSEEventType.ANSWER, sid,
                                        stage=StatusStage.GENERATING.value,
                                        data={"delta": part}))

        # 缓存 payload（含 citations + panel，供命中时完整回放）
        from server.generate.cache import build_cache_payload
        citations = gen.build_citations(fused.evidence)
        payload = build_cache_payload(
            full_answer,
            [c for c in citations],
            [c.to_dict() for c in fused.conflicts],
            finish_reason,
            model_used,
            panel=panel.to_dict() if hasattr(panel, "to_dict") else panel,
        )
        # 缓存卫生：degraded（网络抖动/降级）与 cancelled 不写缓存，
        # 否则一次抖动会被回放整个 TTL 周期；normal / refused 是确定性结果，可缓存。
        if cache_key[1] and finish_reason in (FinishReason.NORMAL.value,
                                             FinishReason.REFUSED.value):
            gen.cache.put(cache_key[1], payload)

        # citations + panel
        yield sse_format(_event(SSEEventType.CITATIONS, sid,
                                stage=StatusStage.GENERATING.value,
                                data={"citations": citations,
                                      "conflicts": [c.to_dict() for c in fused.conflicts]}))
        yield sse_format(_event(SSEEventType.PANEL, sid,
                                stage=StatusStage.GENERATING.value,
                                data=panel.to_dict() if hasattr(panel, "to_dict") else panel))
        done_data = {"finish_reason": finish_reason, "model_used": model_used}
        if think_metrics["frames"]:
            # 只报"有没有思考、多久、多长"，不报内容
            done_data["first_thinking_ms"] = think_metrics["first_ms"]
            done_data["thinking_frames"] = think_metrics["frames"]
        if gen_stats.get("truncated"):
            done_data["truncated"] = True
        yield sse_format(_event(SSEEventType.DONE, sid, data=done_data))

    except SyncPoolBusy as e:
        # 同步工作池排队已满：这是容量保护，不是内部故障，用专门错误码让调用方可区分
        yield sse_format(_event(SSEEventType.ERROR, sid,
                                data={"error_code": ErrorCode.SERVER_BUSY.value,
                                      "message": str(e)}))
        yield sse_format(_event(
            SSEEventType.DONE, sid,
            data={"finish_reason": FinishReason.FAILED.value, "model_used": ""}))
    except Exception as e:  # noqa: BLE001
        # 内部错误：error 事件 + done。走 logger.exception 而不是 print_exc：
        # 前者带时间戳/级别并落 logs/server.log，后者只写 stderr、服务化部署后捞不到。
        logger.exception("问答编排内部错误（sid=%s）", sid)
        yield sse_format(_event(SSEEventType.ERROR, sid,
                                data={"error_code": ErrorCode.INTERNAL.value,
                                      "message": f"内部错误: {public_text(e)}"}))
        # 终态用 failed 而不是 cancelled：error+done(cancelled) 会让前端把异常轮
        # 误判成"正常结束/用户取消"，进而把空回答写进下一轮历史。
        yield sse_format(_event(
            SSEEventType.DONE, sid,
            data={"finish_reason": FinishReason.FAILED.value, "model_used": ""}))
    finally:
        # 客户端断连（StreamingResponse 关闭生成器）或异常退出：取消模型任务并回收，
        # 否则请求结束后仍会继续消耗 token 并可能抛"未检索异常"。
        if gen_task is not None and not gen_task.done():
            gen_task.cancel()
            try:
                await gen_task
            except BaseException:  # noqa: BLE001  # CancelledError/自身异常都无需再上报
                pass
