"""评测运行器（evaluation.chain）集成冒烟测试。

- eval 驱动与生产编排 server.sse.run_query 对拍（证据数量一致）；
- 通道开关生效（text-only 不产图谱证据）；
- 单题可产出完整 trace。
数据快照/索引不入 Git，缺失时自动跳过。

运行：在 RAG/ 根目录执行  python -m pytest tests/test_chain_smoke.py -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from config.settings import get_settings
from server.runtime import build_runtime

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"
IDX = ROOT / "data" / "index" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (SNAP / "entities.json").exists() or not (IDX / "chunks_fts.db").exists(),
    reason="本地快照/索引缺失（data/ 不入 Git）",
)


@pytest.fixture(scope="module")
def runtime():
    s = get_settings()
    # 评测统一离线回答器：确定性
    s.llm_base_url = ""
    s.llm_api_key = ""
    s.fallback_llm_base_url = ""
    s.fallback_llm_api_key = ""
    rt = build_runtime(s, "20260904_v2")
    yield rt


def _run(coro):
    return asyncio.run(coro)


def test_dual_trace_structure(runtime):
    from evaluation.chain import run_question, CONFIG_DEFAULT

    tr = _run(run_question(runtime, CONFIG_DEFAULT, "介绍一下长平之战。",
                           filters={}, expected_names=["长平之战", "白起"]))
    assert tr["graph"]["enabled"] is True
    assert tr["understand"]["question_type"] in (
        "single_entity", "background", "relation", "timeline", "unknown")
    assert tr["answer"]["text"]
    assert "finish_reason" in tr["answer"]
    assert tr["auto"]["expected_total"] == 2
    # 长平之战 为已标注且有据问题：不应无依据拒答
    assert tr["refusal"] is None


def test_text_only_disables_graph(runtime):
    from evaluation.chain import run_question, CONFIG_TEXT_ONLY

    tr = _run(run_question(runtime, CONFIG_TEXT_ONLY, "赤壁之战产生了怎样的影响？",
                           filters={}, expected_names=["赤壁之战"]))
    assert tr["graph"]["enabled"] is False
    assert tr["graph"]["n"] == 0
    assert tr["text"]["n"] > 0
    assert tr["fused"]["n"] > 0


def test_text_mode_passthrough_and_autodowngrade(runtime):
    """EvalConfig.mode 透传到文本检索（RAGv5 T3 起向量已可用）。

    两条断言覆盖两件事：
    1. mode="vector" 且向量后端可用 → 实际执行 vector 并返回结果；
    2. 向量不可用（无密钥/集合缺失）→ resolve_mode 自动降级关键词（纯函数，见下一条用例）。
    """
    from dataclasses import replace

    from evaluation.chain import run_question, CONFIG_DEFAULT

    cfg = replace(CONFIG_DEFAULT, mode="vector")
    tr = _run(run_question(runtime, cfg, "介绍一下长平之战。",
                           filters={}, expected_names=["长平之战"]))
    if runtime.text.vector_available:
        assert tr["text"]["mode"] == "vector"
        assert tr["text"]["n"] > 0
    else:
        # 本机未构建向量/未配密钥时，仍应降级关键词并保持有结果
        assert tr["text"]["mode"] == "keyword"
        assert tr["text"]["n"] > 0


def test_hybrid_mode_passthrough(runtime):
    """hybrid 模式在两个通道之间做融合；向量不可用时降级关键词。"""
    from evaluation.chain import run_question, CONFIG_HYBRID

    tr = _run(run_question(runtime, CONFIG_HYBRID, "介绍一下赤壁之战。",
                           filters={}, expected_names=["赤壁之战"]))
    expect = "hybrid" if runtime.text.vector_available else "keyword"
    assert tr["text"]["mode"] == expect
    assert tr["text"]["n"] > 0


def test_resolve_mode_autodowngrade_unit():
    """向量不可用时 vector/hybrid 一律降级 keyword（不依赖本地索引的纯函数守护）。"""
    from server.text.scoring import resolve_mode

    assert resolve_mode("vector", False) == "keyword"
    assert resolve_mode("hybrid", False) == "keyword"
    assert resolve_mode("vector", True) == "vector"
    assert resolve_mode("hybrid", True) == "hybrid"
    assert resolve_mode("bogus", True) == "keyword"


def test_parity_with_server_run_query(runtime):
    """对拍：同一问题 eval 驱动 vs 生产 sse.run_query 的证据数量应一致。"""
    from contracts.request import QueryRequest
    from evaluation.chain import run_question, CONFIG_DEFAULT
    from server.sse import run_query

    question = "赤壁之战的主帅是谁？"

    async def collect():
        tr = await run_question(runtime, CONFIG_DEFAULT, question,
                                filters={}, expected_names=["赤壁之战"])
        req = QueryRequest.from_dict({"session_id": "parity", "question": question})
        frames = []
        async for frame in run_query(runtime, req):
            frames.append(json.loads(frame[len("data: "):]))
        return tr, frames

    tr, frames = _run(collect())
    gres = [e for e in frames if e["type"] == "graph_results"][0]["data"]
    tres = [e for e in frames if e["type"] == "text_results"][0]["data"]
    fused = [e for e in frames if e["type"] == "fusion"][0]["data"]
    assert len(gres["evidence"]) == tr["graph"]["n"]
    assert len(tres["evidence"]) == tr["text"]["n"]
    assert fused["evidence_count"] == tr["fused"]["n"]
