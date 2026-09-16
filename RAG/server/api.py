"""FastAPI 服务入口（server/api.py）。

- POST /api/query：SSE 流式问答（F02→F03/F04→F05→F06）
- GET /api/health：健康检查（含活跃数据版本、制品哈希与 Git commit）
- GET /api/dicts：朝代/战争类型标准词典（F01 筛选下拉数据源，RAGv3）
- GET /api/demo/examples：F08 示例题清单
- 基础限流（无登录公开接口，按来源 IP 每分钟配额）

启动方式：
    python scripts/run_server.py [--version 20260915_v1]        # 推荐
    E:/anaconda/envs/AI_Agent/python.exe -m uvicorn server.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config.settings import Settings, get_settings
from contracts.request import QueryRequest, RequestValidationError
from contracts.sse import ErrorCode, FinishReason
from lib import release_info
from server.runtime import Runtime, build_runtime
from server.sse import sse_format, shutdown_sync_pool, sync_pool_stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动加载 + 关闭回收。

    构建失败不阻塞启动（由 /api/health 暴露 load_error），便于部署时先看健康接口；
    关闭时释放外部 HTTP 客户端（2026-09-15 审核 P1-9）。
    """
    settings = get_settings()
    runtime: Runtime | None = None
    load_error: str | None = None
    try:
        runtime = build_runtime(settings)
    except Exception as e:  # noqa: BLE001
        load_error = str(e)
    app.state.runtime = runtime
    app.state.load_error = load_error
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_per_minute,
        max_keys=settings.rate_limit_max_keys,
    )
    if runtime is not None:
        print(f"[api] runtime 就绪：数据版本 {runtime.version} | 索引 {runtime.index_dir.name} "
              f"| 向量 {'可用' if runtime.text.vector_available else '不可用'}"
              f" | LLM {'已配置' if runtime.generate.llm.available else '未配置'}")
    else:
        print(f"[api] runtime 加载失败：{load_error}")
    try:
        yield
    finally:
        # 必须 await：AsyncOpenAI 的关闭是 coroutine，同步调用会抛 RuntimeError 并被吞掉，
        # 连接池实际不会释放（第四轮复核 P0-3）。同时回收同步工作线程池（P1-5）。
        if runtime is not None:
            await runtime.shutdown()
        shutdown_sync_pool()


app = FastAPI(title="中国历代战争史 RAG 问答", version="ragv5", lifespan=lifespan)

_settings_boot = get_settings()

# CORS：默认 * 便于本地开发，生产用 CORS_ALLOW_ORIGINS 限定站点域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings_boot.cors_allow_origins or ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

# 兼容旧引用（部分脚本/测试直接读 app.state.settings）
app.state.settings = _settings_boot


def _load_dicts_payload(rt) -> dict:
    """读取当前快照的筛选词典（dicts.json）并转成 F01 需要的结构。

    返回 {"status": "ok", "version": 快照版本, "dynasty": [...], "event_type": [...],
          "sources": {...}}；运行时不完整/词典缺失时 status=error。
    """
    if rt is None:
        return {"status": "error", "message": "runtime not loaded"}
    try:
        import time as _t

        dicts_path = rt.snapshot_dir / "dicts.json"
        if not dicts_path.exists():
            return {"status": "error", "message": f"dicts.json 不存在: {dicts_path}"}
        raw = json.loads(dicts_path.read_text(encoding="utf-8"))
        dynasty_aliases = raw.get("dynasty_aliases") or {}
        # "不详"不作为筛选项；列表按名称排序，保证下拉稳定
        dynasty = sorted(
            [
                {
                    "standard": k,
                    "aliases": [v] if isinstance(v, str) else (v or []),
                }
                for k, v in dynasty_aliases.items()
                if k and str(k).strip() not in ("不详", "未知", "无")
            ],
            key=lambda x: x["standard"],
        )
        event_type = raw.get("event_type_standard") or []
        # 字典可能是字符串列表，也可能是 {standard, aliases} 列表，统一成 {standard}
        if event_type and isinstance(event_type[0], dict):
            event_type = [x.get("standard") for x in event_type if x.get("standard")]
        return {
            "status": "ok",
            "version": rt.version,
            "data_version": rt.version,
            "generated_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
            "dynasty": dynasty,
            "event_type": sorted(set(event_type)),
            "sources": {
                "file": str(dicts_path.name),
                "counts": {
                    "dynasty": len(dynasty),
                    "event_type": len(event_type),
                },
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"读取词典失败: {e}"}


@app.get("/api/dicts")
def dicts():
    """F01 筛选下拉用的标准词典（朝代 + 战争类型 + 数据版本）。"""
    payload = _load_dicts_payload(_runtime())
    if payload.get("status") == "error":
        return JSONResponse(payload, status_code=503)
    return payload


# 演示清单每条示例的必填字段（第四轮复核 P1-6：缺字段的清单不能被当成可用示例）
_DEMO_REQUIRED_FIELDS = ("id", "question", "category", "capability")
_DEMO_MEASURED_FIELDS = ("first_answer_ms", "finish_reason")


def _demo_path(rt) -> Path:
    return rt.settings.data_dir / "eval" / rt.version / "demo_examples.json"


def _validate_demo_examples(data: dict, rt) -> Optional[str]:
    """校验演示清单结构与版本一致性，返回错误信息（None = 通过）。

    第四轮复核 P1-6：旧实现只在 `file_version` 为真且不等时拒绝，**缺失/空值/类型错误
    会绕过检查**；清单结构本身也完全没有校验。这里要求版本严格相等，并检查
    source_run、measurement_mode 与每条示例的必填/实测字段。
    """
    file_version = data.get("version")
    if not isinstance(file_version, str) or not file_version:
        return (f"示例题清单缺少 version 字段；请用 `python scripts/gen_demo_examples.py "
                f"--version {rt.version} ...` 重新生成")
    if file_version != rt.version:
        return (f"示例题清单版本不一致：文件 version={file_version}，运行时版本={rt.version}。"
                f"请用 `python scripts/gen_demo_examples.py --version {rt.version} "
                f"--run <该版本的评测 run>` 重新生成后再上线")
    source_run = data.get("source_run")
    if not isinstance(source_run, str) or not source_run:
        return "示例题清单缺少 source_run（无法追溯评分与实测来源）"
    mode = data.get("measurement_mode")
    if mode not in ("real_llm", "offline", None):
        return f"示例题清单 measurement_mode 取值非法：{mode!r}"
    examples = data.get("examples")
    if not isinstance(examples, list):
        return "示例题清单 examples 必须是数组"
    if not examples:
        return "示例题清单 examples 为空（不应作为可用清单上线）"
    for i, item in enumerate(examples):
        if not isinstance(item, dict):
            return f"examples[{i}] 必须是对象"
        missing = [f for f in _DEMO_REQUIRED_FIELDS
                   if not isinstance(item.get(f), str) or not item.get(f)]
        if missing:
            return f"examples[{i}] 缺少必填字段：{', '.join(missing)}"
        measured = item.get("measured")
        if measured is not None:
            if not isinstance(measured, dict):
                return f"examples[{i}].measured 必须是对象"
            bad = [f for f in _DEMO_MEASURED_FIELDS
                   if f not in measured]
            if bad:
                return f"examples[{i}].measured 缺少字段：{', '.join(bad)}"
    return None


def _demo_status(rt) -> dict:
    """演示清单就绪状态（health 的 demo_ready 与接口共用同一份判定）。"""
    if rt is None:
        return {"demo_ready": False, "demo_error": "runtime not loaded"}
    path = _demo_path(rt)
    if not path.exists():
        return {"demo_ready": False, "demo_error": f"示例题清单不存在: {path}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"demo_ready": False, "demo_error": f"示例题清单解析失败: {e}"}
    if not isinstance(data, dict):
        return {"demo_ready": False, "demo_error": "示例题清单根节点必须是对象"}
    error = _validate_demo_examples(data, rt)
    if error:
        return {"demo_ready": False, "demo_error": error,
                "demo_version": data.get("version")}
    return {"demo_ready": True, "demo_error": None,
            "demo_version": data.get("version"),
            "demo_examples": len(data.get("examples") or [])}


def _load_demo_examples(rt) -> dict:
    """读取 F08 演示示例题清单（由 scripts/gen_demo_examples.py 生成并入库）。

    清单来自已审核题库（reviewed=True 且评分非 incorrect），前端不再硬编码示例题。
    校验规则见 _validate_demo_examples：版本必须严格等于运行时版本，
    否则示例时延/评分来自另一份数据，展示出来就是误导——宁可报错也不静默返回旧元数据。
    """
    if rt is None:
        return {"status": "error", "message": "runtime not loaded"}
    try:
        path = _demo_path(rt)
        if not path.exists():
            return {"status": "error", "message": f"示例题清单不存在: {path}"}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"status": "error", "message": "示例题清单根节点必须是对象"}
        error = _validate_demo_examples(data, rt)
        if error:
            return {"status": "error", "message": error,
                    "version": data.get("version"),
                    "runtime_version": rt.version}
        data["status"] = "ok"
        return data
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"读取示例题失败: {e}"}


@app.get("/api/demo/examples")
def demo_examples():
    """F08 演示模式：示例题清单（含类别标签、能力标注与实测时延）。"""
    payload = _load_demo_examples(_runtime())
    if payload.get("status") == "error":
        return JSONResponse(payload, status_code=503)
    return payload


# ---- 基础限流（进程内滑动窗口）----
class RateLimiter:
    """按来源 key 的滑动窗口限流。

    2026-09-15 审核 P1-3 修三处：无法并发原子（检查+追加要持锁）、key 表无界增长
    （伪造来源可刷爆内存）、XFF 无条件信任（任何人可伪造首段 IP 绕过限流）。
    """

    def __init__(self, per_minute: int, max_keys: int = 4096):
        self.per_minute = max(1, int(per_minute))
        self.max_keys = max(16, int(max_keys))
        self._hits: "OrderedDict[str, list[float]]" = OrderedDict()
        self._lock = threading.Lock()
        self.rejected = 0
        self.evicted_keys = 0        # 窗口已过期被清理的 key 数
        self.rejected_new_keys = 0   # 表满且无可清理项时被拒的新 key 数

    def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - 60
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > window_start]
            if len(hits) >= self.per_minute:
                self._hits[key] = hits
                self._hits.move_to_end(key)
                self.rejected += 1
                return False

            if key not in self._hits and len(self._hits) >= self.max_keys:
                # 表满：只清理"窗口内已无命中"的 key，绝不淘汰仍在窗口内的 key。
                # 旧实现直接淘汰最久未活跃项，被淘汰的 key 再次访问时配额从零开始，
                # 等于把限流窗口重置（第四轮复核 P1-4）。
                self._sweep_expired_locked(window_start)
                if len(self._hits) >= self.max_keys:
                    # 全是活跃 key：拒绝新来源，避免用淘汰换取"看起来还能用"
                    self.rejected_new_keys += 1
                    self.rejected += 1
                    return False

            hits.append(now)
            self._hits[key] = hits
            self._hits.move_to_end(key)
            return True

    def _sweep_expired_locked(self, window_start: float) -> None:
        """调用方必须持锁：移除窗口内已无命中的 key（这些 key 的配额已自然恢复）。"""
        stale = [k for k, ts in self._hits.items() if not any(t > window_start for t in ts)]
        for k in stale:
            self._hits.pop(k, None)
            self.evicted_keys += 1

    def stats(self) -> dict:
        with self._lock:
            return {"keys": len(self._hits), "max_keys": self.max_keys,
                    "rejected": self.rejected, "evicted_keys": self.evicted_keys,
                    "rejected_new_keys": self.rejected_new_keys,
                    "per_minute": self.per_minute}


def _runtime():
    """当前运行时；lifespan 未跑（测试/嵌入式挂载）时返回 None。"""
    return getattr(app.state, "runtime", None)


def _load_error():
    return getattr(app.state, "load_error", None)


def _rate_limiter() -> RateLimiter:
    """取当前限流器；lifespan 未跑（测试/嵌入式挂载）时按设置惰性创建。"""
    limiter = getattr(app.state, "rate_limiter", None)
    if limiter is None:
        settings: Settings = app.state.settings
        limiter = RateLimiter(settings.rate_limit_per_minute,
                              max_keys=settings.rate_limit_max_keys)
        app.state.rate_limiter = limiter
    return limiter


def _client_key(request: Request) -> str:
    """限流来源标识。

    X-Forwarded-For 只有在显式开启信任（RATE_LIMIT_TRUST_FORWARDED_FOR）时才采用；
    开启后仍可限定可信代理（RATE_LIMIT_TRUSTED_PROXIES），未列入的直连方所带 XFF 一律忽略。
    """
    settings: Settings = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    if not getattr(settings, "rate_limit_trust_forwarded_for", False):
        return peer
    trusted = list(getattr(settings, "rate_limit_trusted_proxies", []) or [])
    if trusted and peer not in trusted:
        return peer
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return peer


@app.get("/api/health")
def health():
    """健康与发布可追溯信息。

    第四轮复核 P0-4：健康接口必须能让验收证据反向定位到唯一源码与配置。
    为此补齐 source_dirty / release_id / config_fingerprint / artifact_manifest_sha256
    与 demo_ready；同时在活跃版本未显式固定时给出告警字段，避免"看起来正常但换了数据"。
    """
    rt = _runtime()
    payload = {
        "status": "ok" if rt else "error",
        "version": rt.version if rt else None,
        "vector_available": bool(rt.text.vector_available) if rt else False,
        "llm_available": rt.generate.llm.available if rt else False,
        "load_error": _load_error(),
        "meta": rt.meta if rt else None,
    }
    if not rt:
        return payload
    meta = rt.meta or {}
    payload.update({
        "index_version": rt.index_dir.name,
        "git_commit": meta.get("git_commit", ""),
        "source_dirty": meta.get("source_dirty"),
        "release_id": meta.get("release_id", ""),
        "config_fingerprint": meta.get("config_fingerprint", ""),
        "artifact_manifest_sha256": meta.get("artifact_manifest_sha256", ""),
        "version_selection": meta.get("version_selection", ""),
        "cache": rt.generate.cache.stats(),
        "rate_limit": _rate_limiter().stats(),
        "sync_pool": sync_pool_stats(),
    })
    payload.update(_demo_status(rt))
    if not meta.get("active_version_pinned"):
        payload["warnings"] = [
            "数据版本未显式固定（RAG_ACTIVE_VERSION / --version 未设置）："
            "当前按目录扫描选择最新一致版本，重启可能切换数据",
        ]
    return payload


class _PayloadTooLarge(Exception):
    pass


async def _read_json_limited(request: Request, limit: int) -> dict:
    """带尺寸上限地读取 JSON 请求体（P0-2）。

    先看 Content-Length 快速拒绝，再按流累计校验——只有前者会被分块传输绕过，
    只有后者会在超大 body 上先占满内存。
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _PayloadTooLarge(f"请求体超过上限 {limit} 字节（声明 {declared}）")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _PayloadTooLarge(f"请求体超过上限 {limit} 字节")
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RequestValidationError(f"JSON 解析失败: {e}") from e
    if not isinstance(payload, dict):
        raise RequestValidationError("请求体必须是 JSON 对象")
    return payload


def _json_error(status_code: int, message: str, code: ErrorCode) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "error_code": code.value, "message": message},
        status_code=status_code,
    )


@app.post("/api/query")
async def query(req: Request):
    """SSE 问答流。请求体见 contracts.request.QueryRequest。

    失败在**建立流之前**用 4xx 返回（P0-2 / P2）：客户端无需解析 SSE 就能区分
    参数错误、超限与限流；只有进入编排后的内部错误才走 SSE 的 error+done。
    """
    rt = _runtime()
    if rt is None:
        return _json_error(503, _load_error() or "runtime not loaded",
                           ErrorCode.INTERNAL)

    limiter = _rate_limiter()
    if not limiter.allow(_client_key(req)):
        return _json_error(
           429,
           f"请求过于频繁：每分钟最多 {limiter.per_minute} 次，请稍后再试",
           ErrorCode.RATE_LIMITED,
        )

    settings: Settings = app.state.settings
    try:
        payload = await _read_json_limited(req, settings.request_max_bytes)
    except _PayloadTooLarge as e:
        return _json_error(413, str(e), ErrorCode.PAYLOAD_TOO_LARGE)
    except RequestValidationError as e:
        return _json_error(400, f"请求格式不合法: {e}", ErrorCode.INVALID_REQUEST)

    try:
        q = QueryRequest.from_dict(payload, settings=settings)
    except RequestValidationError as e:
        return _json_error(400, f"请求不合法: {e}", ErrorCode.INVALID_REQUEST)

    async def gen():
        async for frame in _stream_with_heartbeat(rt, q, settings):
            yield frame

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # 反代/浏览器缓冲会破坏"逐字出现"的观感，也会延迟错误可见时间。
            # 不设 Connection 头：HTTP/1.1 默认长连接，HTTP/2 下该头非法。
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


async def _stream_with_heartbeat(rt, q: QueryRequest, settings: Settings):
    """给 run_query 套上心跳与整体 deadline。

    第四轮复核 P0-2 修正了三点：
    - 截止时间用**单调时钟**（time.monotonic）计算，不再拿心跳当等待上限：
      旧实现的心跳为 0 时会永久等待，心跳大于剩余时间时会晚一个完整心跳周期才收流；
    - 每次等待取 `min(心跳, 剩余时间)`，`remaining <= 0` 立即结束；
    - 超时按"是否已经送出正文"区分终态：已送正文 → interrupted（回答可能不完整），
      未送正文 → failed；错误码用专门的 timeout。

    心跳用 SSE 注释行（`: ping`），客户端解析器按行过滤即可，不会污染事件流。
    """
    from server.sse import run_query

    heartbeat = float(getattr(settings, "sse_heartbeat_seconds", 15.0) or 0)
    deadline = float(getattr(settings, "sse_max_duration_seconds", 300.0) or 0)
    deadline_at = time.monotonic() + deadline if deadline > 0 else None
    agen = run_query(rt, q)
    pending: asyncio.Task | None = None
    saw_answer = False

    def _timeout_frames(reason: str):
        """超时收尾：error(timeout) + done(interrupted|failed)。"""
        finish = FinishReason.INTERRUPTED.value if saw_answer else FinishReason.FAILED.value
        return (
            sse_format({"type": "error", "session_id": q.session_id, "stage": None,
                        "data": {"error_code": ErrorCode.TIMEOUT.value, "message": reason}}),
            sse_format({"type": "done", "session_id": q.session_id, "stage": None,
                        "data": {"finish_reason": finish, "model_used": ""}}),
        )

    try:
        while True:
            remaining = None
            if deadline_at is not None:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    for frame in _timeout_frames(f"生成超过 {deadline:.0f}s 上限，已中止"):
                        yield frame
                    break
            if pending is None:
                pending = asyncio.ensure_future(agen.__anext__())
            timeout = remaining
            if heartbeat > 0:
                timeout = heartbeat if remaining is None else min(heartbeat, remaining)
            try:
                frame = await asyncio.wait_for(asyncio.shield(pending), timeout)
            except asyncio.TimeoutError:
                # 到点的是心跳窗口，还是整体 deadline？
                if deadline_at is not None and time.monotonic() >= deadline_at:
                    for f in _timeout_frames(f"生成超过 {deadline:.0f}s 上限，已中止"):
                        yield f
                    break
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break
            pending = None
            if '"type": "answer"' in frame:
                saw_answer = True
            yield frame
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass


# ---- 同源托管（RAGv5 D8）：把前端构建产物一并发出，浏览器只访问一个地址 ----
# 必须在所有 /api 路由注册之后挂载：mount("/") 会兜住未匹配路径，先注册的 /api/* 优先生效。
_dist_dir = Path(str(_settings_boot.frontend_dist or ""))
if _dist_dir.is_dir() and (_dist_dir / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="frontend")
    print(f"[api] 同源托管已启用：{_dist_dir}（浏览器直接访问 / 即可，无需另起前端服务）")
else:
    print(f"[api] 未启用同源托管：未发现前端产物 {_dist_dir}"
          f"（需先 `cd frontend && npm run build`，或用 Vite 开发服务器联调）")
