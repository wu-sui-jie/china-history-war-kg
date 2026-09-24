"""SSE / 非流式一致性回归（开发文档 10.2，对应需求 G6 的 P0 回归口径）。

同一问题分别调 `POST /api/query`（解析 SSE 流）与 `POST /api/query/json`，
断言两者一致：

- `answer` 流逐帧拼接 == `answer_md`（严格相等，含换行与空白）；
- `citations` 全等、`panel` 全等；
- `finish_reason` 与 `truncated` 一致。

为什么必须有：非流式接口的价值全在"与流式同一份编排、结果天然一致"。
一旦聚合实现与流式实现分叉，飞书通道与网页通道会给出不同答案，
而**网页侧完全看不出来**——只有这个脚本能发现。

两类题都要有（开发文档 10.2）：
- 绕开缓存的题：比对真实生成链路；
- 命中缓存的题：验证回放路径（缓存命中时非流式接口同样要能聚合出完整结果）。

用法：
    python feishu-bot/scripts/consistency_check.py --base-url http://127.0.0.1:8000
    python feishu-bot/scripts/consistency_check.py --version 20260915_v1 --limit 5
    python feishu-bot/scripts/consistency_check.py --question "介绍一下长平之战。"
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BOT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BOT_ROOT.parent
RAG_ROOT = REPO_ROOT / "RAG"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_questions(args) -> list[str]:
    """取题库：显式 --question > 演示清单（与卡片按钮同源，最贴近线上问法）> 评测题库。"""
    if args.question:
        return list(args.question)
    if args.questions_file:
        path = Path(args.questions_file)
        if not path.exists():
            raise SystemExit(f"题库文件不存在：{path}")
        if path.suffix == ".jsonl":
            return [json.loads(line)["question"] for line in path.read_text(encoding="utf-8")
                    .splitlines() if line.strip() and json.loads(line).get("question")]
        data = _read_json(path)
        if isinstance(data, dict) and "examples" in data:
            return [e["question"] for e in data["examples"] if e.get("question")]
        raise SystemExit(f"无法识别题库格式：{path}")

    versions = [args.version] if args.version else sorted(
        (p.name for p in (RAG_ROOT / "data" / "eval").glob("*") if p.is_dir()), reverse=True)
    for version in versions:
        demo = RAG_ROOT / "data" / "eval" / version / "demo_examples.json"
        if demo.exists():
            data = _read_json(demo)
            questions = [e["question"] for e in data.get("examples", []) if e.get("question")]
            if questions:
                print(f"[i] 题库来源：{demo.relative_to(REPO_ROOT)}（数据版本 {version}）")
                return questions
        questions_file = RAG_ROOT / "data" / "eval" / version / "questions.jsonl"
        if questions_file.exists():
            questions = [json.loads(line)["question"]
                         for line in questions_file.read_text(encoding="utf-8").splitlines()
                         if line.strip()]
            if questions:
                print(f"[i] 题库来源：{questions_file.relative_to(REPO_ROOT)}（数据版本 {version}）")
                return questions
    raise SystemExit("未找到题库：请用 --question 或 --questions-file 显式指定")


def parse_sse(text: str) -> dict:
    """把 SSE 响应解析成与 /api/query/json 相同形状的结果（逐帧拼接 answer）。

    `: ping` 心跳注释行按行过滤（开发文档 5.1 的口径：注释不污染事件流）。
    """
    answer_parts: list[str] = []
    citations: list = []
    conflicts: list = []
    panel: dict = {}
    finish_reason = ""
    truncated = False
    cache_hit = False
    error: dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: "):])
        etype, data = event.get("type"), event.get("data")
        if etype == "answer":
            answer_parts.append((data or {}).get("delta") or "")
        elif etype == "citations":
            citations = (data or {}).get("citations") or []
            conflicts = (data or {}).get("conflicts") or []
        elif etype == "panel":
            panel = data if isinstance(data, dict) else {}
        elif etype == "done":
            finish_reason = (data or {}).get("finish_reason") or ""
            truncated = bool((data or {}).get("truncated"))
            cache_hit = bool((data or {}).get("cache_hit"))
        elif etype == "error":
            error = data if isinstance(data, dict) else {}

    return {"answer_md": "".join(answer_parts), "citations": citations,
            "conflicts": conflicts, "panel": panel, "finish_reason": finish_reason,
            "truncated": truncated, "cache_hit": cache_hit, "error": error}


@dataclass
class Comparison:
    question: str
    round_name: str
    ok: bool
    diffs: list[str] = field(default_factory=list)
    sse_cache_hit: bool | None = None
    json_cache_hit: bool | None = None
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return {"question": self.question, "round": self.round_name, "ok": self.ok,
                "diffs": self.diffs, "sse_cache_hit": self.sse_cache_hit,
                "json_cache_hit": self.json_cache_hit, "elapsed_ms": self.elapsed_ms}


def compare(sse: dict, js: dict) -> list[str]:
    """逐字段比对，返回差异说明列表（空 = 一致）。"""
    diffs: list[str] = []
    if sse["answer_md"] != js["answer_md"]:
        diffs.append(
            f"answer 不一致：SSE {len(sse['answer_md'])} 字符 vs JSON "
            f"{len(js['answer_md'])} 字符"
            f"（首个差异位置 {_first_diff(sse['answer_md'], js['answer_md'])}）")
    for key in ("citations", "conflicts", "panel"):
        if sse[key] != js[key]:
            diffs.append(f"{key} 不一致：SSE {_digest(sse[key])} vs JSON {_digest(js[key])}")
    if sse["finish_reason"] != js["finish_reason"]:
        diffs.append(f"finish_reason 不一致：{sse['finish_reason']!r} vs "
                     f"{js['finish_reason']!r}")
    if bool(sse["truncated"]) != bool(js["truncated"]):
        diffs.append(f"truncated 不一致：{sse['truncated']} vs {js['truncated']}")
    if sse["error"] and not js["error"]:
        diffs.append(f"SSE 报了错但没有对应的 JSON 错误：{sse['error']}")
    return diffs


def _first_diff(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return f"{len(text)} 字符/{text[:60]}…" if len(text) > 60 else text


def call_sse(client: httpx.Client, base_url: str, body: dict, headers: dict) -> dict:
    resp = client.post(f"{base_url}/api/query", json=body, headers=headers)
    resp.raise_for_status()
    return parse_sse(resp.text)


def call_json(client: httpx.Client, base_url: str, body: dict, headers: dict) -> dict:
    resp = client.post(f"{base_url}/api/query/json", json=body, headers=headers)
    if resp.status_code >= 400:
        # 非流式接口的失败用 HTTP 状态表达；这里不抛异常，交给比对逻辑报出来
        return {"answer_md": "", "citations": [], "conflicts": [], "panel": {},
                "finish_reason": "", "truncated": False, "cache_hit": False,
                "error": resp.json() if resp.headers.get("content-type", "").startswith(
                    "application/json") else {"message": resp.text[:200]},
                "http_status": resp.status_code}
    return resp.json().get("data", {})


def run_comparison(client: httpx.Client, args, question: str, *, suffix: str,
                   round_name: str) -> Comparison:
    """跑一轮比对：先 SSE 再 JSON（顺序有意固定）。

    顺序固定的原因：SSE 先跑时大概率是未命中（真链路），紧随其后的 JSON 会命中
    刚写入的缓存——一轮里同时覆盖"真链路"与"回放路径"两种情况。
    """
    body = {"session_id": args.session_id, "question": question + suffix, "history": []}
    if args.history_file:
        body["history"] = _read_json(Path(args.history_file))
    headers = {"X-Bot-Key": args.bot_key} if args.bot_key else {}
    started = time.monotonic()
    sse = call_sse(client, args.base_url, body, headers)
    js = call_json(client, args.base_url, body, headers)
    elapsed = int((time.monotonic() - started) * 1000)

    if js.get("http_status"):
        return Comparison(question=question, round_name=round_name, ok=False,
                          diffs=[f"非流式接口返回 HTTP {js['http_status']}：{js.get('error')}"],
                          sse_cache_hit=sse.get("cache_hit"), elapsed_ms=elapsed)

    diffs = compare(sse, js)
    return Comparison(question=question, round_name=round_name, ok=not diffs, diffs=diffs,
                      sse_cache_hit=sse.get("cache_hit"),
                      json_cache_hit=js.get("cache_hit"), elapsed_ms=elapsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SSE / 非流式接口一致性回归")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--version", default="", help="数据版本（用于定位题库）")
    parser.add_argument("--question", action="append", default=[],
                        help="显式指定问题，可重复")
    parser.add_argument("--questions-file", default="", help="题库文件（json/jsonl）")
    parser.add_argument("--limit", type=int, default=5, help="最多比对多少题")
    parser.add_argument("--session-id", default="consistency-check",
                        help="调用用的 session_id（不参与缓存键，仅作标识）")
    parser.add_argument("--bot-key", default="", help="配置了 RAG_BOT_API_KEY 时必填")
    parser.add_argument("--history-file", default="",
                        help="带历史的请求体片段（JSON 数组），用于验证多轮一致性")
    parser.add_argument("--bypass-cache", action="store_true",
                        help="追加一次性后缀强制未命中，比对真实链路（会轻微改变问题文本）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（CI 用）")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    questions = load_questions(args)[: max(1, args.limit)]
    suffix = ""
    if args.bypass_cache:
        suffix = f"（一致性校验 {time.strftime('%H%M%S')}）"
        print(f"[i] 已追加一次性后缀绕过缓存：{suffix}")

    results: list[Comparison] = []
    headers = {"X-Bot-Key": args.bot_key} if args.bot_key else {}
    with httpx.Client(timeout=httpx.Timeout(args.timeout, connect=10.0),
                      headers=headers) as client:
        for i, question in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] {question}")
            try:
                result = run_comparison(client, args, question, suffix=suffix,
                                        round_name="bypass" if suffix else "normal")
            except Exception as e:  # noqa: BLE001 - 网络/解析异常都算这一题失败
                result = Comparison(question=question,
                                    round_name="bypass" if suffix else "normal",
                                    ok=False, diffs=[f"调用异常：{e}"])
            results.append(result)
            mark = "OK " if result.ok else "FAIL"
            cache = f"sse命中={result.sse_cache_hit} json命中={result.json_cache_hit}"
            print(f"    {mark} ({result.elapsed_ms}ms, {cache})")
            for diff in result.diffs:
                print(f"    - {diff}")

    failed = [r for r in results if not r.ok]
    if args.json:
        print(json.dumps({"total": len(results), "failed": len(failed),
                          "results": [r.as_dict() for r in results]},
                         ensure_ascii=False, indent=2))
    print(f"\n合计 {len(results)} 题，一致 {len(results) - len(failed)} 题，"
          f"不一致 {len(failed)} 题")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
