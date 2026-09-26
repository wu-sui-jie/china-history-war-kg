"""部署冒烟（RAGv5）：一次跑通"页面可访问 + 接口可用 + 示例题稳定 + 缓存命中"。

检查项（任一失败 → 退出码非 0，并写入 JSON 报告）：
1. `GET /api/health` → status=ok，记录 version / text_mode / vector_available / llm_available；
2. `GET /` → 200 且返回 HTML（同源托管生效，D8）；
3. `GET /api/demo/examples` → status=ok 且示例题非空；
4. 逐条示例题 `POST /api/query`（SSE）→ done、回答非空、finish_reason=normal、
   证据条数满足 expect 下限，并记录首 thinking / 首正文 / 总耗时；
5. 重复第 1 条示例题（**同一进程**，缓存是进程内的）→ 必须命中缓存且回答一致；
6. `--check-rate-limit`（可选）→ 连续请求直到出现 error(invalid_request)，确认限流生效。

用法：
  python scripts/smoke_deploy.py --base http://127.0.0.1:8000
  python scripts/smoke_deploy.py --check-rate-limit --examples data/eval/20260904_v2/demo_examples.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from lib.json_io import write_json         # noqa: E402


def _stdout_encoding() -> str:
    """当前输出流的编码名（拿不到就按 UTF-8 处理）。"""
    enc = getattr(sys.stdout, "encoding", None)
    return (enc or "utf-8").lower()


def _output_symbols() -> tuple[str, str]:
    """选择跨平台安全的通过/失败前缀。

    Windows 默认控制台是 GBK，直接 print("✓") 会抛 UnicodeEncodeError 让脚本在
    健康检查阶段就崩掉——检查项没跑完，退出码也没有参考价值。这里先探测编码，
    不能编码就退回 ASCII 的 [OK]/[FAIL]；Linux/macOS（UTF-8）仍保留符号外观。
    """
    enc = _stdout_encoding()
    if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
        return "✓", "✗"
    try:
        "✓✗".encode(enc, errors="strict")
    except Exception:  # noqa: BLE001
        return "[OK]", "[FAIL]"
    return "✓", "✗"


def _safe_print(text: str) -> None:
    """按输出流编码安全打印：编码不了的字符替换为 ?，绝不因日志符号崩溃。"""
    enc = _stdout_encoding()
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode(enc, errors="replace").decode(enc, errors="replace") + "\n")
        sys.stdout.flush()


_OK_PREFIX, _FAIL_PREFIX = _output_symbols()


def _get(base: str, path: str, timeout: float = 30.0):
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8"), dict(resp.headers)


def _sse_query(base: str, question: str, session_id: str, timeout: float = 180.0) -> dict:
    """发一次 SSE 问答，返回汇总结果（不抛异常，错误记在结果里）。"""
    body = json.dumps({"session_id": session_id, "question": question}).encode("utf-8")
    req = urllib.request.Request(base + "/api/query", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "text/event-stream"})
    t0 = time.time()
    t_think = t_answer = None
    n_think = n_answer = 0
    answer_parts: list[str] = []
    citations = 0
    graph_evidence = 0
    text_evidence = 0
    finish_reason = ""
    error: dict | None = None
    cache_hit = False
    stage_seen: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                etype = payload.get("type")
                data = payload.get("data") or {}
                if etype == "status":
                    # 注意：stage 在 SSE **信封层**（payload["stage"]），不在 data 里
                    stage = payload.get("stage") or data.get("stage") or ""
                    stage_seen.append(stage)
                    if stage == "cache_hit":
                        cache_hit = True
                elif etype == "thinking":
                    if t_think is None:
                        t_think = time.time() - t0
                    n_think += 1
                elif etype == "answer":
                    if t_answer is None:
                        t_answer = time.time() - t0
                    n_answer += 1
                    answer_parts.append(data.get("delta") or "")
                elif etype == "citations":
                    citations = len(data.get("citations") or [])
                elif etype == "graph_results":
                    graph_evidence = len(data.get("evidence") or [])
                elif etype == "text_results":
                    text_evidence = len(data.get("evidence") or [])
                elif etype == "done":
                    finish_reason = data.get("finish_reason") or ""
                    if data.get("cache_hit"):
                        cache_hit = True   # done 事件里的 cache_hit 是第二重判据
                elif etype == "error":
                    error = data
    except Exception as e:  # noqa: BLE001
        error = {"error_code": type(e).__name__, "message": str(e)}
    return {
        "question": question,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "first_thinking_ms": int(t_think * 1000) if t_think else None,
        "first_answer_ms": int(t_answer * 1000) if t_answer else None,
        "answer_frames": n_answer,
        "thinking_frames": n_think,
        "citations": citations,
        "graph_evidence": graph_evidence,
        "text_evidence": text_evidence,
        "finish_reason": finish_reason,
        "cache_hit": cache_hit,
        "answer_len": len("".join(answer_parts)),
        "answer_prefix": "".join(answer_parts)[:80],
        "error": error,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGv5 部署冒烟")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    ap.add_argument("--examples", default="", help="示例题清单（默认 data/eval/<v>/demo_examples.json）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条示例题（0 = 全部）")
    ap.add_argument("--check-rate-limit", action="store_true", help="附加限流检查（放在最后）")
    ap.add_argument("--report", default="", help="报告输出路径（默认 logs/smoke_<ts>.json）")
    args = ap.parse_args()

    settings = get_settings()
    examples_path = Path(args.examples) if args.examples else None
    report: dict = {"base": args.base, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "checks": {}, "examples": [], "failures": []}

    def fail(msg: str) -> None:
        report["failures"].append(msg)
        _safe_print(f"  {_FAIL_PREFIX} {msg}")

    def ok(msg: str) -> None:
        _safe_print(f"  {_OK_PREFIX} {msg}")

    # 1) 健康检查
    _safe_print("[1] GET /api/health")
    try:
        status, body, _ = _get(args.base, "/api/health")
        health = json.loads(body)
        report["checks"]["health"] = {"http": status, **{k: health.get(k) for k in
                                     ("status", "version", "vector_available", "llm_available")}}
        report["checks"]["meta"] = health.get("meta")
        if health.get("status") == "ok":
            ok(f"status=ok version={health.get('version')} "
               f"vector={health.get('vector_available')} llm={health.get('llm_available')} "
               f"mode={(health.get('meta') or {}).get('text_mode')}")
        else:
            fail(f"health.status={health.get('status')} load_error={health.get('load_error')}")
    except Exception as e:  # noqa: BLE001
        fail(f"health 请求失败: {e}")

    # 2) 同源页面
    _safe_print("[2] GET /（同源托管）")
    try:
        status, body, headers = _get(args.base, "/")
        is_html = "<html" in body[:500].lower()
        report["checks"]["index_html"] = {"http": status, "is_html": is_html, "bytes": len(body)}
        if status == 200 and is_html:
            ok(f"返回 HTML（{len(body)} B）")
        else:
            fail(f"GET / 未返回 HTML（http={status}）")
    except Exception as e:  # noqa: BLE001
        fail(f"GET / 失败: {e}（未构建前端？先 cd frontend && npm run build）")

    # 3) 示例题清单
    _safe_print("[3] GET /api/demo/examples")
    examples: list[dict] = []
    try:
        status, body, _ = _get(args.base, "/api/demo/examples")
        data = json.loads(body)
        examples = data.get("examples") or []
        if examples_path and examples_path.exists():
            examples = json.loads(examples_path.read_text(encoding="utf-8")).get("examples") or examples
        report["checks"]["examples"] = {"http": status, "status": data.get("status"),
                                        "count": len(examples),
                                        "source_run": data.get("source_run")}
        if data.get("status") == "ok" and examples:
            ok(f"{len(examples)} 条示例题（来源 run={data.get('source_run')}）")
        else:
            fail(f"示例题不可用: {data.get('message')}")
    except Exception as e:  # noqa: BLE001
        fail(f"示例题接口失败: {e}")

    if args.limit:
        examples = examples[: args.limit]

    # 4) 逐条示例题
    for i, ex in enumerate(examples, start=1):
        q = ex.get("question") or ""
        _safe_print(f"[4.{i}] 示例题 {ex.get('id')}: {q}")
        res = _sse_query(args.base, q, f"smoke-{ex.get('id')}")
        res["id"] = ex.get("id")
        res["expect"] = ex.get("expect") or {}
        report["examples"].append(res)
        if res.get("error"):
            fail(f"{ex.get('id')} 返回 error: {res['error']}")
            continue
        problems = []
        if res["finish_reason"] != "normal":
            problems.append(f"finish_reason={res['finish_reason']}")
        if res["answer_len"] <= 0:
            problems.append("回答为空")
        if res["citations"] <= 0:
            problems.append("无引用")
        # 证据数达标：示例题的 expect 是题库实测得到的通道证据数下限
        # （graph_min/text_min），低于下限说明检索链路相对基线退化。
        expect = res.get("expect") or {}
        g_min = int(expect.get("graph_min") or 0)
        t_min = int(expect.get("text_min") or 0)
        if g_min and res["graph_evidence"] < g_min:
            problems.append(f"图谱证据 {res['graph_evidence']} < expect.graph_min={g_min}")
        if t_min and res["text_evidence"] < t_min:
            problems.append(f"文本证据 {res['text_evidence']} < expect.text_min={t_min}")
        if problems:
            fail(f"{ex.get('id')} {'；'.join(problems)}")
        else:
            ok(f"完成 {res['elapsed_ms']} ms（首思考 {res['first_thinking_ms']} / "
               f"首正文 {res['first_answer_ms']}，{res['answer_frames']} 帧，{res['citations']} 条引用，"
               f"图谱证据 {res['graph_evidence']} / 文本证据 {res['text_evidence']}）")

    # 5) 缓存命中（同进程重复提问）
    if examples:
        q = examples[0].get("question") or ""
        _safe_print(f"[5] 缓存命中复跑：{q}")
        res = _sse_query(args.base, q, "smoke-repeat")
        report["checks"]["cache_repeat"] = res
        first = report["examples"][0] if report["examples"] else {}
        if res.get("cache_hit") and res["answer_len"] == first.get("answer_len"):
            ok(f"命中缓存（{res['elapsed_ms']} ms，回答长度与首次一致）")
        else:
            fail(f"未命中缓存或回答不一致（cache_hit={res.get('cache_hit')}，"
                 f"len {res['answer_len']} vs {first.get('answer_len')}）")

    # 6) 限流（可选，放在最后）
    if args.check_rate_limit:
        _safe_print("[6] 限流检查（连续请求直到出现 'rate limited'）")
        hit = 0
        for i in range(1, 41):
            # 用空问题发请求：请求体会被拒（invalid_request），但**限流在其之前生效**，
            # 因此不会真的跑检索/LLM，可以廉价地把限流窗口打满。
            r = _sse_query(args.base, "", f"smoke-rl-{i}", timeout=30.0)
            err = r.get("error") or {}
            msg = str(err.get("message") or "")
            if "rate limited" in msg or "限流" in msg:
                hit = i
                break
        report["checks"]["rate_limit"] = {"attempts": hit or 40, "triggered": bool(hit)}
        if hit:
            ok(f"第 {hit} 次请求触发了限流（message={msg[:40]}）")
        else:
            fail("连续 40 次请求未触发限流（检查 RATE_LIMIT_PER_MINUTE 与代理 IP）")

    out_path = Path(args.report) if args.report else \
        settings.log_dir / f"smoke_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    # 显式写入退出码：只看 JSON 的调用方（CI / 运维脚本）不必再靠"failures 是否为空"推断结果
    report["exit_code"] = 1 if report["failures"] else 0
    report["passed"] = not report["failures"]
    write_json(out_path, report)

    _safe_print(f"\n报告: {out_path}")
    if report["failures"]:
        _safe_print(f"冒烟失败 {len(report['failures'])} 项：")
        for f in report["failures"]:
            _safe_print(f"  - {f}")
        return 1
    _safe_print(f"冒烟全部通过（{len(report['examples'])} 条示例题）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
