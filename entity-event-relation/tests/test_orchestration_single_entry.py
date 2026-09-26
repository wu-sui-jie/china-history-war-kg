"""抽取编排只有一份实现，两个入口都走它。

"分段循环 + 三阶段调用 + 失败诊断"只能有一份实现：编排收在
`war_extraction.core.extraction_runner.run_extraction`，backend 的
`llm_pipeline.extract_all_optimized` 与离线链路的 `main.py`（`process_long_text` /
`process_single_file`）各自只留后处理。一旦长出第二份平行实现，两边就会漂移——
"实体对象被当 `place_list` 传进提示词"就是这个形状的 bug。

这组用例钉三件事：

1. **结构**：两个入口都不许再自己调阶段（`*.extract(...)`）——漂移的根源就是"又长出第二个循环"；
2. **共享边界的前两阶段逐字符相同**：桩 LLM 驱动两个入口，实体阶段与事件阶段的提示词必须
   一字不差（这两阶段在钩子之前，两边口径本就一致）；
3. **关系阶段的提示词按各自口径**：离线链路在实体阶段后会回填/清洗实体，名称列表因此变长——
   这是"后处理各自保留"的定义，不是漂移。

**做不到的那一半（如实记下）。** "两边逐字段 diff 为空"在这套设计下**不可能**逐字段成立：
合并边界时选的语义是"后处理各自保留"，两边最终产出本就不同（backend 逐字段归一 + 按名字
去重、离线链路 enrich/cleanup/finalize）。能逐字段比的是**共享边界自己的输出**，
而它已经被钩子改过（离线链路的实体回填就发生在边界内）。所以这里退一步钉住"共享阶段的
调用序列与提示词一致"，再靠"两个入口都不得自建循环"把结构漂移堵死。
"""

import ast
import json
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_ROOT.parent

if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

ENTITY_MARKER = "实体抽取专家"
IDENTIFY_MARKER = "事件识别专家"
FULL_EVENT_MARKER = "事件信息抽取专家"
RELATION_MARKER = "事件关系抽取专家"

TEXT = ("公元前1046年，周武王率诸侯之师东进，与商纣王的军队战于牧野。"
        "商军阵前倒戈，商纣王大败，商朝灭亡，周武王建立周朝。")


class _StubLLM:
    """按提示词角色返回固定 JSON 的假客户端，同时记下每一次提示词。"""

    model = "stub"

    def __init__(self):
        self.prompts = []

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        self.prompts.append(prompt)
        if FULL_EVENT_MARKER in prompt:
            return json.dumps({"events": [{
                "EventName": "牧野之战", "EventType": "战争", "StartDate": "公元前1046年",
                "Place": "牧野", "Aggressor": "周武王", "Defender": "商纣王",
            }]}, ensure_ascii=False)
        if IDENTIFY_MARKER in prompt:
            return json.dumps({"events": [{"id": "E1", "name": "牧野之战",
                                           "time": "公元前1046年", "location": "牧野",
                                           "parties": "周武王/商纣王"}]}, ensure_ascii=False)
        if ENTITY_MARKER in prompt:
            return json.dumps({"places": [{"geo_name": "牧野"}],
                               "organizations": [{"OrgName": "周军"}],
                               "persons": [{"PersonName": "周武王"}]}, ensure_ascii=False)
        if RELATION_MARKER in prompt:
            return json.dumps({
                "event_place_relations": [{"EventName": "牧野之战", "modern_name": "牧野",
                                           "relation": "主战场", "evidence": "战于牧野"}],
                "event_person_relations": [{"EventName": "牧野之战", "PersonName": "周武王",
                                            "relation": "统帅", "evidence": "周武王"}],
                "event_organization_relations": [],
                "event_event_relations": [],
            }, ensure_ascii=False)
        raise AssertionError(f"桩 LLM 收到不认识的提示词：{prompt[:120]}")

    def stages(self):
        """把记下的提示词翻译成阶段序列。"""
        labels = []
        for prompt in self.prompts:
            if FULL_EVENT_MARKER in prompt:
                labels.append("event_full")
            elif IDENTIFY_MARKER in prompt:
                labels.append("event_identify")
            elif ENTITY_MARKER in prompt:
                labels.append("entity")
            elif RELATION_MARKER in prompt:
                labels.append("relation")
            else:
                labels.append("unknown")
        return labels


def _extract_calls(path: Path) -> list:
    """找出源码里所有 `xxx.extract(...)` 调用（按 AST，不误报注释与字符串）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "extract":
                found.append(getattr(node, "lineno", -1))
    return found


def test_两个入口都不再自建阶段循环():
    """漂移的根源是"又长出第二个循环"：任何一侧都不许自己调 .extract()。

    两侧各建一份循环的话，backend/llm_pipeline.py 与 entity-event-relation/main.py
    会各出现 3 处 `*_extractor.extract(...)`（这条断言会列出 6 行）；
    阶段调用只应存在于共享编排里那 3 处。
    """
    for rel in ("backend/llm_pipeline.py", "entity-event-relation/main.py"):
        lines = _extract_calls(REPO_ROOT / rel)
        assert lines == [], (
            f"{rel} 里又出现了自己的阶段调用（第 {lines} 行）——"
            "编排必须走 war_extraction.core.extraction_runner.run_extraction"
        )


def test_共享编排才是阶段调用的唯一位置():
    """反面确认：阶段调用确实在共享编排里，不是被谁删掉了。"""
    lines = _extract_calls(MODULE_ROOT / "war_extraction" / "core" / "extraction_runner.py")
    assert len(lines) == 3, f"共享编排应有三处阶段调用，实际 {lines}"


def test_共享编排的阶段顺序与钩子位置():
    """一段文本里：实体 → 事件 → （实体钩子）→ 关系 → （段钩子），逐段如此。

    钩子相对于"模型调用"的位置要一起钉住：实体钩子在**事件阶段之后**才跑（它要用事件字段
    回填实体）。只记钩子自己的先后顺序是不够的——把它整体挪到事件阶段之前，那条断言照样过。
    """
    from war_extraction.core.extraction_runner import run_extraction

    llm = _StubLLM()
    trace = []
    seen_at_hook = {}

    def on_entities_ready(_text, entities, _events):
        trace.append("entities_ready")
        # 此刻模型调用应该已经发生到"事件阶段"为止，且还没有关系阶段
        seen_at_hook["stages"] = llm.stages()
        return entities

    def on_events_ready(_text, events):
        trace.append("events_gate")
        return list(events)

    def on_chunk_done(_text, entities, events, relations):
        trace.append("chunk_done")
        return entities, events, relations

    run = run_extraction(llm, TEXT, chunk_size=len(TEXT) + 1, overlap=0,
                         on_entities_ready=on_entities_ready,
                         on_events_ready=on_events_ready,
                         on_chunk_done=on_chunk_done)

    assert llm.stages()[0] == "entity"
    assert run.diagnostics["chunks"] == 1
    assert trace == ["entities_ready", "events_gate", "chunk_done"], trace
    # 实体钩子跑的时候，事件阶段已经调过了（identify + 完整事件两问），关系阶段还没开始
    assert seen_at_hook["stages"][0] == "entity"
    assert "relation" not in seen_at_hook["stages"], seen_at_hook["stages"]
    assert "event_identify" in seen_at_hook["stages"], seen_at_hook["stages"]
    assert run.diagnostics["stage_ok"] == {"entity": 1, "event": 1, "relation": 1}
    assert run.partial_errors == []
    assert run.chunks[0].ok is True
    assert [p.geo_name for p in run.chunks[0].entities.places] == ["牧野"]


def test_事件门钩子返回空则跳过关系阶段():
    """backend 用它省掉"本段事件全是旧事件"时的关系调用。"""
    from war_extraction.core.extraction_runner import run_extraction

    llm = _StubLLM()
    run = run_extraction(llm, TEXT, chunk_size=len(TEXT) + 1, overlap=0,
                         on_events_ready=lambda _t, _events: [])

    assert "relation" not in llm.stages()
    assert run.diagnostics["stage_ok"] == {"entity": 1, "event": 1, "relation": 0}
    assert run.chunks[0].relations.event_place_relations == []


def test_单阶段失败只作废该阶段并留下诊断(monkeypatch):
    """失败策略（统一口径第 2 条）：按阶段继续，失败进 partial_errors，ok 变 False。"""
    from war_extraction.core import extraction_runner

    llm = _StubLLM()
    monkeypatch.setattr(extraction_runner.EntityExtractor, "extract",
                        lambda self, text: (_ for _ in ()).throw(RuntimeError("模型抽风")))

    run = extraction_runner.run_extraction(llm, TEXT, chunk_size=len(TEXT) + 1, overlap=0)

    assert run.diagnostics["stage_ok"]["entity"] == 0
    assert run.diagnostics["stage_ok"]["event"] == 1, "实体阶段失败不该拖垮事件阶段"
    assert run.partial_errors and "实体抽取阶段失败" in run.partial_errors[0]
    assert run.chunks[0].ok is False, "有阶段失败就不能算整段成功（离线链路据此决定不写缓存）"
    assert run.chunks[0].entities.places == []


def test_分段缓存命中时不再调用模型(tmp_path, monkeypatch):
    """缓存命中走"还原 + 段钩子"，一次模型都不调；失败段不写缓存。"""
    from war_extraction.core.cache_manager import CacheManager
    from war_extraction.core.extraction_runner import run_extraction

    cache = CacheManager(cache_dir=str(tmp_path / "cache"))
    meta = {"stage": "test", "prompt_version": "test-v1"}

    first = _StubLLM()
    run_extraction(first, TEXT, chunk_size=len(TEXT) + 1, overlap=0,
                   cache=cache, cache_context_meta=meta, read_cache=True, write_cache=True)
    calls_after_first = len(first.prompts)

    second = _StubLLM()
    run = run_extraction(second, TEXT, chunk_size=len(TEXT) + 1, overlap=0,
                         cache=cache, cache_context_meta=meta, read_cache=True, write_cache=True)

    assert calls_after_first > 0
    assert second.prompts == [], "缓存命中时不该再调模型"
    assert run.chunks[0].from_cache is True
    assert run.diagnostics["cache_hits"] == 1
