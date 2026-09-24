"""C-1 的另一半：桩 LLM 驱动**两个入口**，比对共享阶段。

配套用例在 `entity-event-relation/tests/test_orchestration_single_entry.py`（共享编排自己的
行为 + "任何一侧都不得自建阶段循环"的结构断言）。这一份放在 backend 侧，因为这里才能同时
导入 backend 的 `llm_pipeline` 与离线链路的 `main`——backend 的 conftest 已经替掉了
py2neo 与开发库，导入链在本套用例里是通的；反过来在抽取链那侧导入 backend 会真的去连
Neo4j，把两套环境搅在一起。

**能验的**：两个入口走同一份编排——调用次数、阶段顺序、以及"钩子之前的两个阶段"的提示词
逐字符相同（这两个阶段两边本就没有分歧）。

**不能验的（如实记下）**：C-1 验收里写的"两边逐字段 diff 为空"在本设计下不成立——合并时
选的语义是"后处理各自保留"，两边最终产出必然不同；而共享边界自己的输出又已被钩子改过
（离线链路的实体回填就发生在边界内）。所以这里只钉"共享阶段一致"，结构漂移由上面那份
AST 断言堵住。
"""

import json
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[2] / "entity-event-relation"
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
        raise AssertionError("桩 LLM 收到不认识的提示词：%s" % prompt[:120])

    def stages(self):
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


@pytest.fixture
def offline_main():
    import main as offline_main_module
    return offline_main_module


@pytest.fixture
def backend_llm():
    import llm_pipeline
    llm = _StubLLM()
    # 只取提示词：结果由 backend 自己断言（见 tests/test_extract_replay.py）
    return llm, llm_pipeline


def test_两个入口的阶段调用序列相同(backend_llm, offline_main, tmp_path):
    backend_stub, llm_pipeline = backend_llm
    llm_pipeline.extract_all_optimized(backend_stub, TEXT)

    offline_stub = _StubLLM()
    sample = tmp_path / "sample.txt"
    sample.write_text(TEXT, encoding="utf-8")
    offline_main.process_single_file(sample, offline_stub, enable_split=False,
                                     read_cache=False, write_cache=False)

    assert backend_stub.stages() == offline_stub.stages(), (
        "两个入口的阶段调用序列不一致：\n  backend: %s\n  离线链路: %s"
        % (backend_stub.stages(), offline_stub.stages()))
    assert len(backend_stub.prompts) == len(offline_stub.prompts) >= 3


def test_钩子之前的两个阶段提示词逐字符相同(backend_llm, offline_main, tmp_path):
    """实体阶段与事件阶段在钩子之前，两边口径本就一致——逐字符比对必须在关系阶段前停下。"""
    backend_stub, llm_pipeline = backend_llm
    llm_pipeline.extract_all_optimized(backend_stub, TEXT)

    offline_stub = _StubLLM()
    sample = tmp_path / "sample.txt"
    sample.write_text(TEXT, encoding="utf-8")
    offline_main.process_single_file(sample, offline_stub, enable_split=False,
                                     read_cache=False, write_cache=False)

    compared = 0
    for index, (a, b) in enumerate(zip(backend_stub.prompts, offline_stub.prompts)):
        if RELATION_MARKER in a:
            break
        assert a == b, "第 %d 次调用的提示词两边不一致（该阶段本该口径相同）" % (index + 1)
        compared += 1
    assert compared >= 2, "至少要比到实体、事件两个阶段"
    assert len(backend_stub.prompts) > compared, "两个入口都应走到关系阶段"


def test_关系阶段的实体名列表来自各自的后处理(backend_llm, offline_main):
    """关系阶段的提示词按各自口径：离线链路在事件阶段后回填/清洗过实体。"""
    backend_stub, llm_pipeline = backend_llm
    llm_pipeline.extract_all_optimized(backend_stub, TEXT)

    offline_stub = _StubLLM()
    offline_main._process_one_shot(TEXT, offline_stub, "single_file",
                                  read_cache=False, write_cache=False)

    backend_relation = [p for p in backend_stub.prompts if RELATION_MARKER in p]
    offline_relation = [p for p in offline_stub.prompts if RELATION_MARKER in p]
    assert backend_relation and offline_relation
    for prompt in (backend_relation[0], offline_relation[0]):
        assert "牧野" in prompt and "周武王" in prompt
