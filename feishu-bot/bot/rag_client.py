"""RAG HTTP 客户端（开发文档 5.4）。

只调一个服务、只用同步 httpx：worker 是线程模型，阻塞等待最省心，
不需要把事件循环桥接进来（开发文档第四节的并发模型决策）。

超时口径是**有意的层层截断**，不是"与 LLM 超时对齐"：
    机器人 25s（RAG_QUERY_TIMEOUT） < 非流式接口 30s（QUERY_JSON_TIMEOUT_SECONDS）
      < RAG 的 LLM 单次超时 60s × 最多 3 次尝试
模型真的跑满预算时，飞书通道**注定**拿到超时降级卡片，而不是慢慢等出长回答。
任何一方改这几个数值，都要重新审视这张关系表（开发文档 5.4 / 十二-3）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# 触发降级卡片的错误码（开发文档 5.3）。
#
# 第 14 轮审计 P3-6：这个集合原先**没有任何引用**，真正决定降级文案的是
# `knowledge_qa._degraded_reply` 里一句硬编码判断，于是「往集合里加错误码」会静默无效。
# 现在它就是判定集（timeout 另走一档文案），并补上此前漏掉的 `rate_limited`：
# 429 在机器人看来同样是「服务暂不可用、稍后再试」，不是内部故障。
DEGRADED_CODES = frozenset({"timeout", "server_busy", "internal", "llm_timeout",
                            "llm_unavailable", "bad_response", "transport",
                            "rate_limited"})
# 只有用户侧参数错误才提示"问题过长或不支持"
USER_FACING_CODES = frozenset({"invalid_request"})


class RagError(RuntimeError):
    """RAG 调用失败。`code` 为 RAG 的 error_code，或本端定义的传输层码。"""

    def __init__(self, code: str, message: str = "", *, status: int | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.status = status

    @property
    def is_payload_too_large(self) -> bool:
        """413：历史组装出的内部问题（开发文档风险 8），绝不能对用户说"问题过长"。"""
        return self.code == "payload_too_large" or self.status == 413

    @property
    def is_invalid_request(self) -> bool:
        return self.code in USER_FACING_CODES or self.status == 400


class RagClient:
    """RAG 的 HTTP 客户端（/api/query/json、/api/health、/api/demo/examples）。"""

    def __init__(self, base_url: str, *, bot_api_key: str = "", query_timeout: float = 25.0,
                 connect_timeout: float = 5.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.bot_api_key = bot_api_key
        self.query_timeout = query_timeout
        self._http = client or httpx.Client(
            timeout=httpx.Timeout(connect=connect_timeout, read=query_timeout,
                                  write=query_timeout, pool=connect_timeout),
            follow_redirects=False,
        )

    # ---- 底层 ----
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.bot_api_key:
            headers["X-Bot-Key"] = self.bot_api_key
        return headers

    def _error_from_response(self, resp: httpx.Response) -> RagError:
        """把非 2xx 响应翻译成 RagError（尽量取 RAG 的 error_code）。"""
        code = ""
        message = ""
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                code = str(payload.get("error_code") or "")
                message = str(payload.get("message") or "")
        except Exception:  # noqa: BLE001 - 非 JSON 错误页（反代 502 等）
            message = (resp.text or "")[:200]
        if not code:
            code = {401: "unauthorized", 413: "payload_too_large",
                    429: "rate_limited", 503: "internal", 504: "timeout"}.get(
                        resp.status_code, "internal")
        return RagError(code, message or f"HTTP {resp.status_code}", status=resp.status_code)

    # ---- 业务 ----
    def query(self, question: str, history: list[dict], session_key: str) -> dict:
        """POST /api/query/json，返回响应里的 data 字段。

        超时/非 2xx → RagError（由技能层转降级卡片），不在这里吞掉。
        """
        body: dict[str, Any] = {
            "session_id": session_key,
            "question": question,
            "history": history or [],
        }
        started = time.monotonic()
        try:
            resp = self._http.post(f"{self.base_url}/api/query/json", json=body,
                                   headers=self._headers())
        except httpx.TimeoutException as e:
            raise RagError("timeout", f"等待 RAG 超过 {self.query_timeout:g}s：{e}") from e
        except httpx.HTTPError as e:
            raise RagError("transport", f"RAG 不可达：{e}") from e

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            err = self._error_from_response(resp)
            log.warning("RAG 返回错误：status=%s code=%s body_bytes=%d elapsed_ms=%d",
                        resp.status_code, err.code, len(resp.content), elapsed_ms)
            raise err

        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise RagError("bad_response", f"响应不是 JSON：{e}") from e
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise RagError("bad_response", "响应缺少 status=ok")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RagError("bad_response", "响应缺少 data 对象")
        data["_elapsed_ms"] = elapsed_ms          # 观测用；技能层记录日志
        return data

    def health(self) -> dict:
        """GET /api/health。启动探测用；失败不阻塞启动（开发文档 5.4）。"""
        try:
            resp = self._http.get(f"{self.base_url}/api/health", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "message": str(e)}

    def supports_json_query(self) -> bool:
        """探测 RAG 是否提供 `/api/query/json`（即跑的是含本接口的版本）。

        为什么需要：非流式接口是机器人唯一的问答入口。若 `RAG_BASE_URL` 指向一个
        **改动前启动的旧实例**，health 是正常的、路由却不存在——用户只会收到
        "服务暂不可用"，日志里也看不出所以然（我曾亲手踩这个坑）。启动时探一次，
        把"地址没错、版本太旧"这个结论直接写进日志。

        探测手段：发一个空请求体的 POST。路由存在 → 参数校验拒绝（400/413/429）；
        路由不存在 → 404/405（FastAPI 的同源托管把未匹配路径交给 StaticFiles，
        对 POST 会返回 405）。不触发任何检索或模型调用，代价可忽略。
        """
        try:
            resp = self._http.post(f"{self.base_url}/api/query/json", json={},
                                   headers=self._headers())
        except Exception as e:  # noqa: BLE001 - 连不上不代表"不支持"，两类原因要分开说
            log.warning("探测 /api/query/json 失败（连不上，无法判断版本）：%s", e)
            return False
        if resp.status_code in (400, 413, 422, 429):
            return True
        if resp.status_code in (401, 403):
            # 第 14 轮审计 P3-7：401/403 说明**路由存在、身份被拒**——最常见的原因是
            # 机器人侧没配 / 配错 X-Bot-Key（RAG 在 jwt 档下要求它，见 P2-20）。
            # 原先把 401 归到下面那条"该地址可能不是 RAG 服务"，运维会朝反方向修。
            log.error("探测 /api/query/json 被拒（HTTP %s）：这是身份问题，不是地址问题——"
                      "请检查 feishu-bot/.env 的 RAG_BOT_API_KEY 与 RAG 侧 "
                      "RAG_BOT_API_KEY 是否同值（jwt 档下必须配）", resp.status_code)
            return False
        if resp.status_code in (404, 405):
            log.warning("该 RAG 未提供 /api/query/json（HTTP %s）：很可能是改动前启动的"
                        "旧实例，请用当前代码重启 RAG 服务", resp.status_code)
        else:
            log.warning("探测 /api/query/json 未得到预期响应（HTTP %s）：该地址可能不是 "
                        "RAG 服务，或中间有反代/网关拦截", resp.status_code)
        return False

    def demo_examples(self) -> list[dict]:
        """GET /api/demo/examples；失败返回空列表（按钮区不渲染，不阻塞问答）。"""
        try:
            resp = self._http.get(f"{self.base_url}/api/demo/examples", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("示例题清单获取失败（按钮区不渲染）：%s", e)
            return []
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            log.warning("示例题清单不可用：%s", payload.get("message") if isinstance(payload, dict)
                        else payload)
            return []
        examples = payload.get("examples")
        return [e for e in examples if isinstance(e, dict)] if isinstance(examples, list) else []

    def close(self) -> None:
        self._http.close()


class DemoExamplesCache:
    """示例问题按钮的题目缓存（P1-3）：启动预取 + 每 1h 刷新，接口失败保留旧值。

    放在 rag_client 里而不是单独模块：它本质是"带缓存的 RAG 只读调用"，
    与限流、鉴权、超时口径共享同一个客户端。
    """

    def __init__(self, client: RagClient, *, count: int = 3, refresh_seconds: float = 3600.0,
                 failure_retry_seconds: float = 300.0, enabled: bool = True, clock=time.time):
        self.client = client
        self.count = max(0, int(count))
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        # 失败后的重试窗口：比成功时短（接口恢复了不该等一小时），但必须**有**窗口，
        # 否则每次组卡都会同步重打一次 HTTP（见 refresh 的负缓存说明）
        self.failure_retry_seconds = min(max(1.0, float(failure_retry_seconds)),
                                        self.refresh_seconds)
        self.enabled = enabled
        self.clock = clock
        self._questions: list[str] = []
        # None = 从未取过（不能写 0.0：那要靠"真实时钟远大于刷新窗口"才成立，
        # 注入假时钟就变成"启动后先假装缓存是新鲜的"）
        self._fetched_at: float | None = None
        # 上一次刷新是否失败：窗口按它取档，而不是按"当前有没有题目"
        self._last_fetch_failed = False
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> list[str]:
        """取（并按需刷新）示例题。失败时保留上一次结果，不清空。

        **负缓存**：守卫只看"刷新窗口是否过期"，不看"有没有题目"。早先要求缓存非空
        （`fresh and self._questions`），于是接口失败或返回空列表时守卫永远不成立，
        每次组卡都同步重打一次 `GET /api/demo/examples`——端点不可用时最坏吃满
        connect 5s + read 25s 的客户端超时，直接叠加在用户等待时间上。
        现在成功与失败两条路径都记刷新时刻，窗口按**上一次刷新是否失败**取档：
        成功后按 `refresh_seconds`（默认 1h）刷，失败后按 `failure_retry_seconds`
        （默认 5min）再试。

        窗口按"上次是否失败"而不是"当前有没有题目"取档，是因为后者会让
        "留有旧题目、本次刷新失败"的情形仍等满一小时——接口恢复了按钮区却迟迟不更新
        （审核报告第三节方案 A）。失败时旧题目照旧展示（`_questions` 不动），
        只是下次刷新提前到 5 分钟后。
        """
        if not self.enabled or self.count == 0:
            return []
        with self._lock:
            window = (self.failure_retry_seconds if self._last_fetch_failed
                      else self.refresh_seconds)
            if (not force and self._fetched_at is not None
                    and self.clock() - self._fetched_at < window):
                return list(self._questions)
            examples = self.client.demo_examples()
            if examples:
                questions = [str(e.get("question") or "") for e in examples]
                self._questions = [q for q in questions if q][: self.count]
                log.info("示例题已刷新（%d 条）", len(self._questions))
                self._last_fetch_failed = False
            else:
                # 失败不清空：接口抖一下不该让按钮区消失（空结果同样进负缓存窗口）
                self._last_fetch_failed = True
            self._fetched_at = self.clock()
            return list(self._questions)

    def questions(self) -> list[str]:
        return self.refresh()
