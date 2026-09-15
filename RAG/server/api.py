"""FastAPI 服务入口（server/api.py）。

- POST /api/query：SSE 流式问答（F02→F03/F04→F05→F06）
- GET /api/health：健康检查
- GET /api/dicts：朝代/战争类型标准词典（F01 筛选下拉数据源，RAGv3）
- 基础限流（无登录公开接口，按 IP 每分钟配额）

启动方式：
    python scripts/run_server.py            # 或
    E:/anaconda/envs/AI_Agent/python.exe -m uvicorn server.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config.settings import get_settings
from contracts.request import QueryRequest
from contracts.sse import ErrorCode
from server.runtime import build_runtime
from server.sse import run_query, sse_format

app = FastAPI(title="中国历代战争史 RAG 问答", version="ragv2")

# CORS：开发阶段允许本地前端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_settings = get_settings()

# 启动时加载运行时（版本 = 最新一致快照/索引；失败时 app.state.runtime=None）
_runtime = None
_load_error = None
try:
    _runtime = build_runtime(_settings)
except Exception as e:  # noqa: BLE001
    _load_error = str(e)

app.state.runtime = _runtime
app.state.load_error = _load_error
app.state.settings = _settings


def _load_dicts_payload(rt) -> dict:
    """读取当前快照的筛选词典（dicts.json）并转成 F01 需要的结构。

    返回 {"status": "ok", "version": 快照版本, "dynasty": [...], "event_type": [...],
          "sources": {...}}；运行时不完整/词典缺失时 status=error。
    """
    if rt is None:
        return {"status": "error", "message": "runtime not loaded"}
    try:
        import json
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
    payload = _load_dicts_payload(app.state.runtime)
    if payload.get("status") == "error":
        from fastapi.responses import JSONResponse

        return JSONResponse(payload, status_code=503)
    return payload


def _load_demo_examples(rt) -> dict:
    """读取 F08 演示示例题清单（由 scripts/gen_demo_examples.py 生成并入库）。

    清单来自已审核题库（reviewed=True 且评分非 incorrect），前端不再硬编码示例题。
    """
    if rt is None:
        return {"status": "error", "message": "runtime not loaded"}
    try:
        import json

        path = rt.settings.data_dir / "eval" / rt.version / "demo_examples.json"
        if not path.exists():
            return {"status": "error", "message": f"示例题清单不存在: {path}"}
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "ok"
        return data
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"读取示例题失败: {e}"}


@app.get("/api/demo/examples")
def demo_examples():
    """F08 演示模式：示例题清单（含类别标签、能力标注与实测时延）。"""
    payload = _load_demo_examples(app.state.runtime)
    if payload.get("status") == "error":
        from fastapi.responses import JSONResponse

        return JSONResponse(payload, status_code=503)
    return payload

# ---- 基础限流（进程内滑动窗口）----
class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - 60
        self.hits[key] = [t for t in self.hits[key] if t > window_start]
        if len(self.hits[key]) >= self.per_minute:
            return False
        self.hits[key].append(now)
        return True


_rate_limiter = RateLimiter(_settings.rate_limit_per_minute)


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/health")
def health():
    return {
        "status": "ok" if app.state.runtime else "error",
        "version": app.state.runtime.version if app.state.runtime else None,
        "vector_available": bool(app.state.runtime.text.vector_available) if app.state.runtime else False,
        "llm_available": app.state.runtime.generate.llm.available if app.state.runtime else False,
        "load_error": app.state.load_error,
        "meta": app.state.runtime.meta if app.state.runtime else None,
    }


@app.post("/api/query")
async def query(req: Request):
    """SSE 问答流。请求体见 contracts.request.QueryRequest。"""
    rt = app.state.runtime
    if rt is None:
        return StreamingResponse(
            _err_stream(app.state.load_error or "runtime not loaded"),
            media_type="text/event-stream",
        )
    # 限流
    if not _rate_limiter.allow(_client_key(req)):
        return StreamingResponse(
            _err_stream("rate limited, 请稍后再试", ErrorCode.INVALID_REQUEST),
            media_type="text/event-stream",
        )
    try:
        payload = await req.json()
        q = QueryRequest.from_dict(payload)
    except Exception as e:  # noqa: BLE001
        return StreamingResponse(
            _err_stream(f"请求格式不合法: {e}", ErrorCode.INVALID_REQUEST),
            media_type="text/event-stream",
        )
    # 校验必填
    if not q.question or not q.session_id:
        return StreamingResponse(
            _err_stream("session_id 与 question 必填", ErrorCode.INVALID_REQUEST),
            media_type="text/event-stream",
        )

    async def gen():
        async for frame in run_query(rt, q):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")


def _err_stream(message: str, code: ErrorCode = ErrorCode.INTERNAL):
    import json
    sid = "error"

    def gen():
        payload = {"type": "error", "session_id": sid, "stage": None,
                   "data": {"error_code": code.value, "message": message}}
        yield sse_format(payload)
        payload2 = {"type": "done", "session_id": sid, "stage": None,
                    "data": {"finish_reason": "cancelled", "model_used": ""}}
        yield sse_format(payload2)

    return gen()


# ---- 同源托管（RAGv5 D8）：把前端构建产物一并发出，浏览器只访问一个地址 ----
# 必须在所有 /api 路由注册之后挂载：mount("/") 会兜住未匹配路径，先注册的 /api/* 优先生效。
_dist_dir = Path(str(_settings.frontend_dist or ""))
if _dist_dir.is_dir() and (_dist_dir / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="frontend")
    print(f"[api] 同源托管已启用：{_dist_dir}（浏览器直接访问 / 即可，无需另起前端服务）")
else:
    print(f"[api] 未启用同源托管：未发现前端产物 {_dist_dir}"
          f"（需先 `cd frontend && npm run build`，或用 Vite 开发服务器联调）")
