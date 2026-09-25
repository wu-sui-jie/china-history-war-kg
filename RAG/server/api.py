"""FastAPI 服务入口（server/api.py）。

- POST /api/query：SSE 流式问答（F02→F03/F04→F05→F06）
- POST /api/query/json：非流式问答（消费同一条编排路径，供飞书机器人等非浏览器调用方）
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
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config.settings import Settings, get_settings
from contracts.query_json import QueryJsonResult
from lib.logging_util import setup_rag_logging
from contracts.request import QueryRequest, RequestValidationError
from contracts.sse import ErrorCode, FinishReason
from server import auth, introspection
from server.runtime import Runtime, build_runtime
from server.sse import run_query, sse_format, shutdown_sync_pool, sync_pool_stats


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
    # 凭证撤销查询器（第 13 轮复核）：未配 URL/密钥时它的 enabled 为 False，
    # 请求路径上直接跳过（见 _revocation_rejection）。
    app.state.introspector = introspection.from_settings(settings)
    if app.state.introspector.enabled:
        logger.info("凭证撤销查询已启用：%s（缓存 %ss，失败策略 %s）",
                    app.state.introspector.url, app.state.introspector.ttl_seconds,
                    app.state.introspector.fail_mode)
    if runtime is not None:
        logger.info("runtime 就绪：数据版本 %s | 索引 %s | 向量 %s | LLM %s",
                    runtime.version, runtime.index_dir.name,
                    "可用" if runtime.text.vector_available else "不可用",
                    "已配置" if runtime.generate.llm.available else "未配置")
    else:
        logger.error("runtime 加载失败：%s", load_error)
    try:
        yield
    finally:
        # 必须 await：AsyncOpenAI 的关闭是 coroutine，同步调用会抛 RuntimeError 并被吞掉，
        # 连接池实际不会释放（第四轮复核 P0-3）。
        # 收尾顺序由 Runtime.shutdown 保证（第五轮审核 P0-3）：先停收同步任务并撤销排队、
        # 有上限地等待在途任务，再关闭外部客户端，避免在途任务用到已关闭的客户端。
        if runtime is not None:
            await runtime.shutdown()
        else:
            # runtime 构建失败时没有 Runtime 对象，仍要回收可能已创建的同步池
            shutdown_sync_pool()
        # 撤销查询器的连接池也要关（它可能已经发过请求）
        introspector = getattr(app.state, "introspector", None)
        if introspector is not None:
            await introspector.aclose()


app = FastAPI(title="中国历代战争史 RAG 问答", version="ragv5", lifespan=lifespan)

_settings_boot = get_settings()

# 统一日志（RAG-5）：给 rag.* 命名空间挂 handler（控制台 + logs/server.log 滚动）。
# 放在模块级而不是 lifespan 里——加载期的日志（下面的 CORS 提示、静态托管判断）才不至于丢掉。
setup_rag_logging(_settings_boot.log_dir)
logger = logging.getLogger("rag.api")

# CORS 启动门禁（第五轮审核 R5-5）：默认 * 只适用于本地开发。
# 显式生产档（RAG_REQUIRE_ACTIVE_VERSION=true）下若仍是 *，任意站点都能从浏览器调用
# 公开问答接口——没有登录，限流也只按来源 IP 计，等于把配额与模型成本开放出去。
# 这里直接失败而不是打日志：漏配的默认行为必须是拒绝，而不是静默放开。
_cors_problem = _settings_boot.cors_startup_problem()
if _cors_problem:
    raise RuntimeError(f"CORS 配置被拒绝：{_cors_problem}")
_cors_warning = _settings_boot.cors_warning()
if _cors_warning:
    logger.warning("注意：%s", _cors_warning)

# 身份校验启动门禁（第 12 轮审查 P1-1）。
# 说的与配的不一致（要校验却没密钥）→ 启动即失败：那等于服务起来后每个问答都 401。
# 未开启校验时只告警，不阻断——"nginx 认证 + 只监听回环"是合法的内网部署方式，
# 一刀切成启动失败会把可用部署判成坏配置。告警同时会出现在 /api/health.warnings 里。
_auth_problem = _settings_boot.auth_startup_problem()
if _auth_problem:
    raise RuntimeError(f"鉴权配置被拒绝：{_auth_problem}")
_auth_warning = _settings_boot.auth_warning()
if _auth_warning:
    logger.warning("注意：%s", _auth_warning)

# 凭证撤销查询的启动门禁（第 13 轮复核）。取值非法（如 fail_mode 拼错）会让"后端不可用
# 时到底放不放行"这件事由默认值默默决定——那是安全取舍，不能由拼写错误决定，因此启动即失败。
# 未启用或只配一半只告警：服务仍能正常工作，但"撤销延迟到 token 到期"这条边界必须可见。
_revocation_problem = _settings_boot.revocation_startup_problem()
if _revocation_problem:
    raise RuntimeError(f"凭证撤销配置被拒绝：{_revocation_problem}")
_revocation_warning = _settings_boot.revocation_warning()
if _revocation_warning:
    logger.warning("注意：%s", _revocation_warning)

app.add_middleware(
    CORSMiddleware,
    # 配置校验保证非空（显式空值会被拒绝，见 Settings.validate）；这里不再 `or ["*"]`——
    # 那会把"显式写空的错误配置"静默变成通配符（第五轮整改复核 B8）
    allow_origins=list(_settings_boot.cors_allow_origins),
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
            return {"status": "error", "message": f"dicts.json 不存在: {_public_text(dicts_path)}"}
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


def _validate_demo_examples(data: dict, rt) -> str | None:
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
        return {"demo_ready": False, "demo_error": f"示例题清单不存在: {_public_text(path)}"}
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
            return {"status": "error", "message": f"示例题清单不存在: {_public_text(path)}"}
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
    return _public_text(getattr(app.state, "load_error", None))


# 服务器绝对路径脱敏（RAG-4）：路径本身不是漏洞，但会白送部署结构与用户名，
# 公开接口没必要回传。只做替换，不改变错误语义。
_RAG_ROOT = str(Path(__file__).resolve().parents[1])
_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"'）)]+|/(?:home|opt|Users|root|var|srv|tmp)/[^\s\"'）)]*)"
)


def _public_text(value):
    """把对外响应文本里的服务器绝对路径收敛为占位符。"""
    if value is None:
        return value
    text = str(value)
    if not text:
        return text
    if _RAG_ROOT:
        text = text.replace(_RAG_ROOT, "<RAG_ROOT>")
    return _ABS_PATH_RE.sub("<path>", text)


def _rate_limiter() -> RateLimiter:
    """取当前限流器；lifespan 未跑（测试/嵌入式挂载）时按设置惰性创建。"""
    limiter = getattr(app.state, "rate_limiter", None)
    if limiter is None:
        settings: Settings = app.state.settings
        limiter = RateLimiter(settings.rate_limit_per_minute,
                              max_keys=settings.rate_limit_max_keys)
        app.state.rate_limiter = limiter
    return limiter


def _resolve_identity(request: Request) -> tuple[Optional[dict], Optional[str]]:
    """尝试解析请求身份，返回 (identity, 失败原因)。

    `identity` 非空 = 已通过 **服务端验签**（不是浏览器自述的 uid）；失败原因只用于日志，
    不回给客户端签名细节（不给攻击者做指纹）。

    **配了密钥就尝试验签，与 RAG_AUTH_MODE / RAG_REQUIRE_AUTH 无关**：开关只决定
    "验不过时拦不拦"。两者分开是有意的——只配密钥没开开关（多半是想开但漏了配置）时，
    服务应当照常工作并给出告警，而不是把所有请求判成未认证；此时带上有效 token 的请求
    仍能拿到身份，方便平滑迁移。

    `iss` / `aud` 的期望值来自配置（第 13 轮整改）：只在验签层校验签名是不够的，
    同一把密钥被别的服务共用时，别人的 token 也能过签名校验。
    """
    settings: Settings = request.app.state.settings
    if not getattr(settings, "jwt_secret", ""):
        return None, None
    try:
        return auth.identity_from_headers(
            request.headers, settings.jwt_secret,
            issuer=getattr(settings, "jwt_issuer", auth.ISSUER_DEFAULT),
            audience=getattr(settings, "jwt_audience", auth.AUDIENCE_DEFAULT),
        ), None
    except auth.AuthError as exc:
        return None, exc.reason


def _auth_rejection(request: Request) -> Optional[JSONResponse]:
    """SSE 通道的准入判定：`RAG_REQUIRE_AUTH=true` 时身份不合法即 401，否则 None。

    未开启校验时不拦，但**能验出的身份照样放进请求上下文**——这样"先配密钥观察一段时间、
    确认无误再打开开关"是一条可行的迁移路径，而不是只能一次性切换。
    """
    settings: Settings = request.app.state.settings
    identity, reason = _resolve_identity(request)
    if identity is not None:
        # 身份放进请求上下文（而不是信任浏览器 postMessage 来的 uid）：
        # 后续要按用户收敛会话/配额时，读这里就是服务端确认过的事实。
        request.state.identity = identity
        return None
    if not getattr(settings, "require_auth", False):
        return None
    logger.warning("拒绝未认证请求：path=%s 原因=%s", request.url.path, reason)
    return _json_error(401, "未认证：该接口要求旧系统签发的有效登录凭证",
                       ErrorCode.UNAUTHORIZED)


def _introspector() -> introspection.Introspector:
    """取当前撤销查询器；lifespan 未跑（测试/嵌入式挂载）时按设置惰性创建。"""
    client = getattr(app.state, "introspector", None)
    if client is None:
        client = introspection.from_settings(app.state.settings)
        app.state.introspector = client
    return client


async def _revocation_rejection(request: Request) -> Optional[JSONResponse]:
    """凭证撤销检查（第 13 轮复核）：验签通过之后，再问一次后端"这张凭证还作不作数"。

    为什么必须有这一步：验签只能证明"这是旧后端签的"，证明不了"它还该被承认"。
    停用账号、改密码之后旧后端已经拒绝该 token，而本服务此前会一直放行到 token 自然
    过期（默认 7 天）——安全动作只在一半系统上生效。

    **只对"已经验签通过"的请求生效**（`request.state.identity` 非空）：没有身份的请求
    本来就不经过 JWT 这条路（例如飞书机器人走 X-Bot-Key），不该被这里拦下；而
    "配了密钥但还没开 require_auth"的迁移期也不受影响——那种请求拿不到身份，
    归 `_auth_rejection` / `_json_endpoint_rejection` 的既有规则处理。

    判定结论在 TTL 内复用（见 server/introspection.py），因此"撤销生效延迟"的上界
    就是 `RAG_INTROSPECT_TTL_SECONDS`；未配置查询时本函数不拦任何请求，
    该边界由 /api/health 的 warnings 报出来。
    """
    if getattr(request.state, "identity", None) is None:
        return None
    client = _introspector()
    if not client.enabled:
        return None

    token = auth.extract_token(request.headers)
    if not token:
        return None

    verdict = await client.check(token)
    if verdict.active:
        return None

    identity = request.state.identity
    logger.warning("拒绝已失效的凭证：user_id=%s path=%s 原因=%s",
                   identity.get("user_id"), request.url.path, verdict.reason or "已撤销")
    # 话术与旧后端逐字一致：同一个"凭证失效"在两条通道上应该只有一种说法
    return _json_error(401, "登录已失效，请重新登录", ErrorCode.UNAUTHORIZED)


def _client_key(request: Request) -> str:
    """限流来源标识。

    X-Forwarded-For 只有在显式开启信任（RATE_LIMIT_TRUST_FORWARDED_FOR）时才采用；
    开启后仍可限定可信代理（RATE_LIMIT_TRUSTED_PROXIES），未列入的直连方所带 XFF 一律忽略。

    **取哪一段与后端保持同一口径**（第 14 轮审计 P1-3）：原实现取 XFF 的第一段，
    而 nginx 用的是 `$proxy_add_x_forwarded_for`——"客户端自带值在前、真实地址追加在后"，
    所以第一段恰好是攻击者可控的那一段，换一个伪造值就换一个限流桶。
    现在优先用 nginx 覆盖下发的 `X-Real-IP`，退而取 XFF 的**最右段**。
    """
    settings: Settings = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    if not getattr(settings, "rate_limit_trust_forwarded_for", False):
        return peer
    trusted = list(getattr(settings, "rate_limit_trusted_proxies", []) or [])
    if trusted and peer not in trusted:
        return peer
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        chain = [part.strip() for part in fwd.split(",") if part.strip()]
        if chain:
            return chain[-1]
    return peer


@app.get("/api/health")
def health():
    """健康与发布可追溯信息。

    第四轮复核 P0-4：健康接口必须能让验收证据反向定位到唯一源码与配置。
    为此补齐 source_dirty / release_id / config_fingerprint / artifact_manifest_sha256
    与 demo_ready；同时在活跃版本未显式固定时给出告警字段，避免"看起来正常但换了数据"。

    第五轮整改复核 B8：**schema 不依赖运行态**——runtime 加载失败时也要返回与正常态
    完全相同的键（`cache` / `sync_pool` / `rate_limit` 等为零值对象），否则监控在最需要
    观测的失败时刻反而拿到不同字段。

    配置一律读**运行期**的 `app.state.settings`（lifespan 写入，与 `_runtime()` /
    `_rate_limiter()` 同一取法），模块级的 `_settings_boot` 只作为"没跑 lifespan 时"的兜底。
    原先这里直接读模块级对象，于是"health 报的口径"与"请求实际用的口径"可能来自两个
    不同对象——正常启动下两者取值相同，这个分叉只在测试里替换 settings 时才显形，
    而它对"撤销查询到底开没开"这类安全口径是致命的。
    """
    settings = getattr(app.state, "settings", None) or _settings_boot
    from server.generate.cache import empty_cache_stats

    rt = _runtime()
    meta = (rt.meta if rt else {}) or {}
    # 向量链路的分解状态（第 12 轮审查 P2-2）。runtime 加载失败时也返回同形状的零值，
    # 与上面 B8 的口径一致——监控在最需要观测的时刻不该拿到不同的键。
    vector_status = rt.text.vector_status() if rt else {
        "artifact_ready": False, "embedding_client_configured": False,
        "embedding_probe_ok": None, "last_vector_error": "",
        "effective_text_mode": "", "vector_error_count": 0, "declared_available": False,
    }
    payload = {
        "status": "ok" if rt else "error",
        "version": rt.version if rt else None,
        # vector_available 是**启动时的能力声明**（制品可加载 + 客户端已装配），
        # 不代表查询期真的可用：查询向量化是网络调用。要看真伪请读下面的 vector 对象。
        "vector_available": bool(rt.text.vector_available) if rt else False,
        "vector": vector_status,
        # 身份校验的当前口径（第 12 轮审查 P1-1）：让运维一眼看出"这个部署到底验不验身份"，
        # 而不是靠读环境变量文件推断。`jwt_configured` 只说配了密钥，不泄露密钥本身。
        "auth": {
            # 模式是运维真正要看的那个字段（第 13 轮整改）：`required=false` 既可能是
            # "nginx 在把关"，也可能是"根本没人在把关"，只有模式能区分这两者。
            "mode": (getattr(settings, "auth_mode", "") or "disabled"),
            "required": bool(getattr(settings, "require_auth", False)),
            "jwt_configured": bool(getattr(settings, "jwt_secret", "")),
            "bot_key_configured": bool((getattr(settings, "bot_api_key", "") or "").strip()),
            "token_header": "Token",
            "accepts_bearer": True,
            # 凭证撤销查询（第 13 轮复核 / 复核整改 §2.7、§2.9）：
            #   policy=enforced  已启用查询，撤销生效延迟上界 = max_delay_seconds（=TTL）
            #   policy=delayed   未启用：旧 token 到自然过期前仍可用（要么显式接受，要么漏配）
            #   policy=not-applicable  非 jwt 档，本服务不验签，也就没有这个问题
            # 顺带给出 last_ok_at / last_failure_at：backend 重启期间"它恢复了吗"是运维最想知道的。
            "revocation": ({
                "enabled": _introspector().enabled,
                "policy": settings.revocation_policy,
                "max_delay_seconds": settings.revocation_max_delay_seconds,
                "delayed_revocation_accepted": bool(settings.allow_delayed_revocation),
                **_introspector().snapshot(),
            } if settings.require_auth else {
                "enabled": False, "policy": "not-applicable",
                "reason": "本服务未验签（非 jwt 档），不做凭证撤销查询",
            }),
        },
        "llm_available": rt.generate.llm.available if rt else False,
        "load_error": _load_error(),
        "meta": rt.meta if rt else None,
        "index_version": rt.index_dir.name if rt else None,
        "git_commit": meta.get("git_commit", ""),
        "source_dirty": meta.get("source_dirty"),
        "release_id": meta.get("release_id", ""),
        "config_fingerprint": meta.get("config_fingerprint", ""),
        "artifact_manifest_sha256": meta.get("artifact_manifest_sha256", ""),
        "version_selection": meta.get("version_selection", ""),
        # 同源托管产物的构建模式（integration / standalone / disabled，见 RAG-9）
        "frontend_mode": _frontend_mode,
        "cache": rt.generate.cache.stats() if rt else empty_cache_stats(),
        "rate_limit": _rate_limiter().stats(),
        "sync_pool": sync_pool_stats(),
    }
    payload.update(_demo_status(rt))
    warnings: list[str] = []
    if rt and not meta.get("active_version_pinned"):
        warnings.append(
            "数据版本未显式固定（RAG_ACTIVE_VERSION / --version 未设置）："
            "当前按目录扫描选择最新一致版本，重启可能切换数据"
        )
    if settings.cors_allows_any_origin:
        # 能走到这里说明要么是开发态、要么已显式 ALLOW_PUBLIC_CORS=true；
        # 仍要在 health 里留痕，避免"公开 CORS"成为看不见的既成事实
        warnings.append(settings.cors_warning() or "")
    if settings.auth_warning():
        # 未启用服务端身份校验（第 12 轮审查 P1-1）：不阻断启动，但必须让运维看得见——
        # 否则"知道 /rag/ 地址就能调"会成为一条没有任何留痕的既成事实。
        warnings.append(settings.auth_warning())
    if settings.bot_channel_warning():
        # jwt 档 + 空 Bot Key = 飞书机器人每问必 401（第 14 轮审计 P2-20）。
        # 机器人侧只会说"RAG 不可用"，这条告警是唯一能把方向指对的地方。
        warnings.append(settings.bot_channel_warning())
    if settings.revocation_warning():
        # 凭证撤销边界（第 13 轮复核）：验签通过 ≠ 仍然有效。未启用撤销查询时，
        # "停用账号/改密码后旧 token 在自然过期前仍可调本服务"这件事必须在运行中的
        # 服务上可见——文档里写一句"注意边界"是不够的。
        warnings.append(settings.revocation_warning())
    if vector_status["embedding_probe_ok"] is False:
        # 声明可用、查询期却失败：这正是本次要消掉的假阳性，必须显式告警而不是静默降级
        warnings.append(
            "向量声明可用但查询期失败，已自动降级关键词："
            f"{vector_status['last_vector_error'] or '（无错误原文）'}；"
            "请检查 EMBEDDING_API_KEY 与 EMBEDDING_BASE_URL"
        )
    if warnings:
        payload["warnings"] = warnings
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

    **顺序：鉴权 → 撤销检查 → runtime → 限流 → 参数校验 → 执行**（第 13 轮复核整改 §2.10）。
    原先这里先查 runtime 再鉴权，于是 runtime 加载失败时**匿名请求**会先拿到
    503 与脱敏后的加载错误——服务状态与内部故障信息泄露给了未认证的人；
    而 `/api/query/json` 是反过来的（先鉴权），两条通道口径不一致。
    现在两条通道顺序一致：未认证的请求在任何情况下都先得到 401，
    也看不到 runtime / load_error（它不该知道这台机器的数据加载得怎么样）。
    """
    # 身份校验（第 12 轮审查 P1-1）：开启 RAG_REQUIRE_AUTH 后，SSE 与 JSON 两条通道
    # 一视同仁地要求可信身份。放在限流之前——未认证的请求不该消耗配额，
    # 也不该从响应耗时上得到任何信息。
    rejection = _auth_rejection(req)
    if rejection is not None:
        return rejection
    # 凭证撤销检查（第 13 轮复核）：验签通过不等于仍然有效，见 _revocation_rejection。
    rejection = await _revocation_rejection(req)
    if rejection is not None:
        return rejection

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


# ---- 非流式问答（POST /api/query/json）：本改动是 RAG 侧唯一的增量 ----
#
# 消费**同一个** run_query 生成器（server/sse.py），逐帧解析后按事件类型聚合，
# 不复制任何编排逻辑——这是"两条通道结果天然一致"（P0 回归口径）的实现方式。
# 聚合规则见 feishu-bot/docs/开发文档.md 6.2，响应契约见 contracts/query_json.py。

# 聚合期间出现 error 事件（含聚合超时）时的 HTTP 状态码映射。
# 编排内错误统一 500 并透传事件的 error_code；超时单独 504（客户端等不到结果，
# 与服务端内部故障是两回事，调用方据此决定"重试"还是"降级提示"）。
_ERROR_HTTP_STATUS = {
    ErrorCode.TIMEOUT.value: 504,
    ErrorCode.SERVER_BUSY.value: 500,
    ErrorCode.INTERNAL.value: 500,
}

# 聚合超时注入的 error 事件（与 SSE 通道 _timeout_frames 的文案口径保持一致）
def _timeout_error(deadline: float) -> dict:
    return {"error_code": ErrorCode.TIMEOUT.value,
            "message": f"生成超过 {deadline:.0f}s 上限，已中止"}


class _FrameAggregator:
    """把 run_query 的 SSE 事件聚合为一份完整结果（开发文档 6.2 的逐事件规则）。

    设计成"只收集、不判断"的纯对象：不涉及 I/O、不抛异常，
    因此可以用假帧序列直接单测（含 cache_hit 分支、error+done、超时中断）。
    """

    def __init__(self) -> None:
        self._answer_parts: list[str] = []
        self._citations: list = []
        self._conflicts: list = []
        self._panel: dict = {}
        self._finish_reason = ""
        self._model_used = ""
        self._cache_hit = False
        self._truncated = False
        self._error: dict | None = None

    def feed(self, event: dict) -> None:
        """处理一帧事件。未知类型一律忽略（session_start/status/entities/... 都不参与聚合）。"""
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        data = event.get("data")

        if etype == "answer":
            delta = (data or {}).get("delta")
            if isinstance(delta, str):
                # 增量拼接保真：不做 strip/换行归一，拼回结果必须与原文严格相等
                self._answer_parts.append(delta)
        elif etype == "citations":
            payload = data or {}
            self._citations = payload.get("citations") or []
            self._conflicts = payload.get("conflicts") or []
        elif etype == "panel":
            # panel 事件的 payload 本身就是 PanelData 字典
            self._panel = data if isinstance(data, dict) else {}
        elif etype == "done":
            payload = data or {}
            self._finish_reason = payload.get("finish_reason") or ""
            self._model_used = payload.get("model_used") or ""
            self._cache_hit = bool(payload.get("cache_hit", False))
            self._truncated = bool(payload.get("truncated", False))
        elif etype == "error":
            payload = data or {}
            # 只记第一个错误：后续事件（done=failed）只用来取终态
            if self._error is None:
                self._error = {
                    "error_code": str(payload.get("error_code") or ErrorCode.INTERNAL.value),
                    "message": str(payload.get("message") or ""),
                }

    @property
    def has_terminal(self) -> bool:
        """是否已拿到终态（done 帧或 error 事件）。用于区分"编排正常走完"与"中途死掉"。"""
        return bool(self._finish_reason) or self._error is not None

    def note_timeout(self, deadline: float) -> None:
        """聚合超时：等价于收到一个 error(timeout) 事件。

        注意不在这里伪造 done 帧：超时轮没有终态（finish_reason 保持空），
        与 SSE 通道"未送出正文即 failed"的处理不同——非流式接口直接以 HTTP 504 回绝，
        finish_reason 不会出现在响应里，不存在误判空间。
        """
        if self._error is None:
            self._error = _timeout_error(deadline)

    def result(self) -> QueryJsonResult:
        return QueryJsonResult(
            answer_md="".join(self._answer_parts),
            citations=list(self._citations),
            conflicts=list(self._conflicts),
            panel=self._panel,
            finish_reason=self._finish_reason,
            model_used=self._model_used,
            cache_hit=self._cache_hit,
            truncated=self._truncated,
            error=self._error,
        )


def _parse_frame(frame: str) -> dict | None:
    """把 run_query 的一帧字符串解析回事件 dict；非 data 帧（心跳注释等）返回 None。"""
    if not isinstance(frame, str) or not frame.startswith("data: "):
        return None
    try:
        payload = json.loads(frame[len("data: "):].strip())
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


async def _collect(rt, q: QueryRequest, deadline: float) -> QueryJsonResult:
    """消费 run_query 直到终态或超时，返回聚合结果。

    超时行为：`agen.aclose()` 会走 run_query 的 finally 分支，取消未完成的 gen_task
    （LLM 生成），与 SSE 客户端断连时的回收路径完全一致，不泄漏任务；
    超时轮不写缓存（finish_reason 不是 normal/refused，既有缓存卫生逻辑自动覆盖）。
    """
    agg = _FrameAggregator()
    deadline_at = time.monotonic() + max(0.0, float(deadline))
    agen = run_query(rt, q)
    try:
        while True:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                agg.note_timeout(deadline)
                break
            try:
                frame = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                # 等待下一帧超过剩余预算：整体超时（非流式没有心跳窗口的概念）
                agg.note_timeout(deadline)
                break
            except Exception as e:  # noqa: BLE001
                # 生成器自身抛错。run_query 内部已把编排异常转成 error 事件，
                # 走到这里说明异常逃出了它的 except（例如收尾时 aclose 出问题）——
                # 已有终态时当收尾噪音忽略，否则记为内部错误，不静默返回空答案。
                if not agg.has_terminal:
                    agg.feed({"type": "error",
                              "data": {"error_code": ErrorCode.INTERNAL.value,
                                       "message": f"编排异常: {e}"}})
                break
            event = _parse_frame(frame)
            if event is not None:
                agg.feed(event)
    finally:
        # aclose 触发 run_query 的 finally（取消 LLM 任务）；已被取消的生成器会直接返回，
        # 但异常路径仍要吞掉——收尾失败不应盖住真实结果（与 _stream_with_heartbeat 一致）
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass
    return agg.result()


def _bot_key_problem(request: Request, settings: Settings) -> str | None:
    """可选共享密钥校验（开发文档 6.4）；未配置 RAG_BOT_API_KEY 时返回 None（不校验）。

    比较用 `secrets.compare_digest`（常量时间），避免"按字符逐位比较"从响应时间上
    泄漏密钥前缀。
    """
    expected = (getattr(settings, "bot_api_key", "") or "").strip()
    if not expected:
        return None
    provided = request.headers.get("x-bot-key") or ""
    if secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return None
    return "X-Bot-Key 校验失败：该接口已启用共享密钥，请带上正确的请求头"


def _json_endpoint_rejection(request: Request, settings: Settings) -> Optional[JSONResponse]:
    """非流式接口的准入判定：**JWT 身份 与 Bot Key 满足其一即可**。

    为什么不是"必须 JWT"：飞书机器人没有用户身份（它是服务到服务的调用方），
    旧后端也不会给它签发 token——要求 JWT 等于把机器人这条路堵死。
    两条身份来源各自对应一类调用方，任一通过即放行：

      - 浏览器（主应用 iframe）：走 JWT，身份可追到具体账号；
      - 飞书机器人：走 X-Bot-Key 共享密钥，代表"这个部署里的机器人"。

    两个都没配时（内网默认）不校验，与改造前一致；此时 health 的 warnings 会给出提示。

    判定顺序（每一档的后果都写清楚，避免"配了一半"变成谁都进不来）：
      1. 带有效 JWT → 放行（身份进请求上下文）；
      2. 配了 Bot Key 且头值正确 → 放行；
      3. 配了 Bot Key 但头值不对 → 401。这是**硬要求**：改造前就是这么判的，
         不能因为顺手加了 JWT 支持就让配好的 Bot Key 形同虚设；
      4. 都没配（或只配了密钥而调用方没带 token、且未开 require_auth）→ 放行。
         只配密钥没开开关时放行是有意的：那多半是"想开但漏了开关"，服务应照常工作并告警，
         而不是把所有请求判成未认证。开了 require_auth 则没有这一档（下面直接 401）。
    """
    require_auth = bool(getattr(settings, "require_auth", False))
    bot_key_configured = bool((getattr(settings, "bot_api_key", "") or "").strip())

    identity, _reason = _resolve_identity(request)
    if identity is not None:
        request.state.identity = identity
        return None

    if bot_key_configured:
        problem = _bot_key_problem(request, settings)
        if problem is None:
            return None
        return _json_error(401, problem, ErrorCode.UNAUTHORIZED)

    if require_auth:
        return _json_error(
            401,
            "未认证：该接口要求旧系统签发的有效登录凭证，或正确的 X-Bot-Key 共享密钥",
            ErrorCode.UNAUTHORIZED,
        )
    # 既没配共享密钥、也没强制身份：内网默认，与改造前一致
    return None


@app.post("/api/query/json")
async def query_json(req: Request):
    """非流式问答：一次调用取完整结果（飞书机器人等非浏览器调用方）。

    前置检查与 /api/query 完全一致（runtime → 限流 → 请求体尺寸 → QueryRequest 校验），
    只是把 SSE 流换成聚合结果；失败同样在返回体之前用 4xx/5xx 表达，
    调用方无需解析任何流式协议即可区分参数错误、限流与超时。
    """
    settings: Settings = app.state.settings

    # 鉴权放在最前：未通过校验的请求不应消耗限流配额，也不该暴露运行态信息。
    # 非流式通道接受两类身份——JWT（浏览器）或 X-Bot-Key（飞书机器人），见函数说明。
    rejection = _json_endpoint_rejection(req, settings)
    if rejection is not None:
        return rejection
    # 撤销检查只对走 JWT 的调用方生效（机器人没有用户身份，也不该被这条拦住）
    rejection = await _revocation_rejection(req)
    if rejection is not None:
        return rejection

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

    result = await _collect(rt, q, settings.query_json_timeout_seconds)
    if result.error:
        code = result.error["error_code"]
        try:
            error_code = ErrorCode(code)
        except ValueError:
            # 编排层理论上只产出 timeout / server_busy / internal；出现未知码时
            # 按 internal 上报，但 HTTP 状态仍取映射默认值 500，不静默当成功
            error_code = ErrorCode.INTERNAL
        return _json_error(_ERROR_HTTP_STATUS.get(code, 500),
                           result.error["message"] or "问答失败", error_code)

    # skip_none：error 为空时该字段不出现在响应里
    return {"status": "ok", "data": result.to_dict()}


def _frame_type(frame: str) -> str:
    """取 SSE 帧的事件类型（超时终态判定用）。

    第六轮复核 D1：旧实现用子串 `'"type": "answer"' in frame` 判断"是否已送出正文"，
    而正文是用户可控/模型可控内容——只要回答里出现该字面量（例如讲解 JSON 格式），
    超时终态就会被误判成 interrupted。这里解析帧本身取 `type` 字段。
    """
    if not frame.startswith("data: "):
        return ""
    try:
        payload = json.loads(frame[len("data: "):].strip())
    except Exception:  # noqa: BLE001
        return ""
    return str(payload.get("type") or "") if isinstance(payload, dict) else ""


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
            if _frame_type(frame) == "answer":
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
def _detect_frontend_mode(dist_dir: Path) -> tuple[str, str]:
    """判断同源托管产物的构建模式，返回 (mode, 依据说明)。

    `dist/` 只有一份，`build`（base=/）与 `build:integration`（base=/rag/）互相覆盖，
    误用哪种都会让页面白屏且控制台只有 404。构建时写 `dist/build-mode.txt` 作为显式标记；
    旧产物没有标记时回退看 index.html 的资源前缀。
    """
    marker = dist_dir / "build-mode.txt"
    try:
        if marker.is_file():
            mode = (marker.read_text(encoding="utf-8").splitlines() or [""])[0].strip()
            if mode in ("integration", "standalone"):
                return mode, f"build-mode.txt={mode}"
    except OSError:
        pass
    try:
        html = (dist_dir / "index.html").read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return "unknown", f"读不到 index.html: {exc}"
    if "/rag/assets/" in html:
        return "integration", "index.html 引用 /rag/assets/"
    return "standalone", "index.html 引用 /assets/（无 build-mode.txt 标记）"


if _dist_dir.is_dir() and (_dist_dir / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    class _SecurityHeadersStaticFiles(StaticFiles):
        """同源托管的前端产物：补基础安全响应头（RAG-3）。

        /rag/* 若被第三方站点 iframe 嵌入，等于替对方消耗限流配额与模型成本；
        X-Frame-Options / CSP frame-ancestors 直接堵掉这条路。
        只加 frame-ancestors，不限制 script-src 等，避免影响前端自身。
        """

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            return response

    app.mount("/", _SecurityHeadersStaticFiles(directory=str(_dist_dir), html=True), name="frontend")
    _frontend_mode, _frontend_mode_why = _detect_frontend_mode(_dist_dir)
    logger.info("同源托管已启用：%s（构建模式 %s，依据：%s）",
                _dist_dir, _frontend_mode, _frontend_mode_why)
    if _frontend_mode == "integration":
        logger.info("产物是并入模式（base=/rag/）：只能经 /rag/ 前缀访问；直接开服务根路径会白屏")
    else:
        logger.warning("产物是独立模式（base=/）：经反代 /rag/ 访问会白屏——"
                       "并入入口请用 `npm run build:integration` 重新构建")
else:
    _frontend_mode = "disabled"
    logger.warning("未启用同源托管：未发现前端产物 %s（需先 `cd frontend && npm run build`，"
                   "或用 Vite 开发服务器联调）", _dist_dir)
