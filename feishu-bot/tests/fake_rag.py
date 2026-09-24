"""假 RAG：按真实事件序列回放预录 SSE 帧的最小 HTTP 服务（开发文档 10.2）。

用途：机器人全链路（收消息 → 调 RAG → 组卡片 → 发送）不依赖真实 RAG 与飞书。
它以 `http.server` 起在随机端口上，实现三个只读接口：
- `POST /api/query/json`：返回与 SSE 回放**同源**的聚合结果（同一份用例数据）；
- `POST /api/query`：把同一份数据按 SSE 帧吐出来（可选，便于人肉比对）；
- `GET /api/health`、`GET /api/demo/examples`。

为什么用同一份用例数据生成两条通道的响应：这样"假 RAG"本身就体现了
"非流式 = 流式聚合"的关系，集成测试断言的卡片内容也就与真实链路同形。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# ---- 用例数据 ----


@dataclass
class Case:
    """一次问答的完整结果（两条通道共用）。"""

    answer_md: str = "# 长平之战\n\n秦赵决战于长平 [1]。"
    citations: list[dict] = field(default_factory=lambda: [
        {"index": 1, "evidence_id": "e1", "kind": "graph_triple",
         "title": "长平之战—主战场→长平", "snippet": ""},
    ])
    conflicts: list[dict] = field(default_factory=list)
    panel: dict = field(default_factory=lambda: {
        "entity_cards": [{"entity_id": "ev1", "type": "事件", "name": "长平之战",
                          "dynasty": "战国", "start_date": "前262年", "end_date": "前260年",
                          "aggressor": "秦军", "defender": "赵军"}],
        "subgraph": {"nodes": [{"id": "n1", "type": "事件", "name": "长平之战"},
                               {"id": "n2", "type": "地点", "name": "长平"}],
                     "edges": [{"source": "n1", "target": "n2", "relation": "主战场"}]},
        "timeline": {"groups": [{"label": "战国", "items": [
            {"event_id": "ev1", "name": "长平之战", "start_date": "前260年"}]}]},
        "map_points": [{"place_id": "p1", "name": "长平", "modern_name": "山西高平"}],
    })
    finish_reason: str = "normal"
    model_used: str = "fake-model"
    cache_hit: bool = False
    truncated: bool = False

    def as_data(self) -> dict:
        return {"answer_md": self.answer_md, "citations": self.citations,
                "conflicts": self.conflicts, "panel": self.panel,
                "finish_reason": self.finish_reason, "model_used": self.model_used,
                "cache_hit": self.cache_hit, "truncated": self.truncated}

    def as_sse_frames(self) -> list[str]:
        """按开发文档第十节的事件主干顺序产出帧（answer 分片模拟打字机）。"""
        def frame(type_: str, data: Any = None) -> str:
            return "data: " + json.dumps(
                {"type": type_, "session_id": "fake", "stage": None, "data": data},
                ensure_ascii=False) + "\n\n"

        frames = [frame("session_start", None), frame("status", {"stage": "entity_linking"})]
        frames.append(frame("entities", {"entities": [], "candidates": []}))
        frames.append(frame("status", {"stage": "graph_search"}))
        frames.append(frame("graph_results", {"evidence": [], "hit_entities": []}))
        frames.append(frame("status", {"stage": "text_search"}))
        frames.append(frame("text_results", {"evidence": [], "mode": "hybrid"}))
        frames.append(frame("status", {"stage": "fusion"}))
        frames.append(frame("fusion", {"evidence_count": len(self.citations)}))
        frames.append(frame("status", {"stage": "generating"}))
        # 按 30 字符切片，模拟增量
        text = self.answer_md
        for i in range(0, len(text), 30):
            frames.append(frame("answer", {"delta": text[i:i + 30]}))
        frames.append(frame("citations", {"citations": self.citations,
                                          "conflicts": self.conflicts}))
        frames.append(frame("panel", self.panel))
        frames.append(frame("done", {"finish_reason": self.finish_reason,
                                     "model_used": self.model_used,
                                     "cache_hit": self.cache_hit,
                                     **( {"truncated": True} if self.truncated else {})}))
        return frames


class FakeRag:
    """进程内的假 RAG 服务。"""

    def __init__(self, *, case: Case | None = None, status: int = 200,
                 error_code: str = "internal", error_message: str = "假的失败",
                 delay: float = 0.0, examples: list[str] | None = None,
                 health: dict | None = None):
        self.case = case or Case()
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.delay = delay
        self.examples = examples if examples is not None else ["介绍一下涿鹿之战。",
                                                              "长平之战是谁打的？",
                                                              "白起有哪些战绩？"]
        self.health_payload = health or {"status": "ok", "version": "20260915_v1",
                                        "llm_available": True, "vector_available": True}
        self.requests: list[dict] = []      # 记录收到的请求体，供断言
        self.server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----
    @property
    def base_url(self) -> str:
        assert self.server is not None, "请先 start()"
        host, port = self.server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def start(self) -> "FakeRag":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):        # 静音访问日志
                pass

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):                # noqa: N802
                if outer.delay:
                    import time
                    time.sleep(outer.delay)
                if self.path.startswith("/api/health"):
                    self._send_json(200, outer.health_payload)
                elif self.path.startswith("/api/demo/examples"):
                    self._send_json(200, {"status": "ok", "version": "20260915_v1",
                                          "examples": [{"id": f"E{i}", "question": q,
                                                        "category": "c",
                                                        "capability": "both"}
                                                       for i, q in enumerate(outer.examples)]})
                else:
                    self._send_json(404, {"status": "error", "message": "not found"})

            def do_POST(self):               # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:            # noqa: BLE001
                    body = {}
                outer.requests.append({"path": self.path, "body": body,
                                       "headers": dict(self.headers)})
                if outer.delay:
                    import time
                    time.sleep(outer.delay)

                if outer.status >= 400:
                    self._send_json(outer.status, {
                        "status": "error", "error_code": outer.error_code,
                        "message": outer.error_message})
                    return
                if self.path.startswith("/api/query/json"):
                    # 与真实 RAG 一致：参数不合法在编排前就 400（空请求体也不例外，
                    # 机器人侧靠这个状态码探测"该地址是否提供非流式接口"）
                    if not str(body.get("session_id") or "").strip() \
                            or not str(body.get("question") or "").strip():
                        self._send_json(400, {"status": "error",
                                              "error_code": "invalid_request",
                                              "message": "session_id/question 必填"})
                        return
                    self._send_json(200, {"status": "ok", "data": outer.case.as_data()})
                elif self.path.startswith("/api/query"):
                    frames = "".join(outer.case.as_sse_frames()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Content-Length", str(len(frames)))
                    self.end_headers()
                    self.wfile.write(frames)
                else:
                    self._send_json(404, {"status": "error", "message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "FakeRag":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
