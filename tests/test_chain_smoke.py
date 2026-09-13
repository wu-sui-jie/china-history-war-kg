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
    """EvalConfig.mode 透传到 text 检索；无向量索引时 vector 自动降级为 keyword。

    守护 v5 的 vector/hybrid 对照前提（第四轮审核 B2）：若 mode 未透传或降级失效，
    本用例会失败。
    """
    from dataclasses import replace

    from evaluation.chain import run_question, CONFIG_DEFAULT

    cfg = replace(CONFIG_DEFAULT, mode="vector")
    tr = _run(run_question(runtime, cfg, "介绍一下长平之战。",
                           filters={}, expected_names=["长平之战"]))
    # 当前索引无向量（embeddings 占位）→ resolve_mode 自动降级关键词
    assert tr["text"]["mode"] == "keyword"
    assert tr["text"]["n"] > 0


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
