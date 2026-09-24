"""抽取链的提示词内容回归（EER-7 的"平行演化易漂移"）。

**为什么需要这组用例。** backend 的 `llm_pipeline.extract_all_optimized` 与离线链路的
`entity-event-relation/main.py` 是两份**平行实现**（EER-7 记的就是这个风险）。第 10 轮核对时
发现已经漂了：backend 调 `event_extractor.extract(chunk_text, chunk_entities)`，而
`EventExtractor.extract(text, place_list: str = "", ...)` 的第 2 个形参是**字符串**——
于是整份实体对象被渲染进提示词。实测两边的第 3 阶段提示词：

    修前：- 地点：places=[PlaceEntity(geo_name='牧野', modern_name=None, DynastyName=None, ...)]
    修后：- 地点：牧野、朝歌

这不会抛异常、接口也照常 200，只是让模型拿着一串 Python repr 去抽事件——**静默降级**。
`snapshot_responses.py` 覆盖不到它（该工具刻意跳过会调大模型的
`/api/extract/entities-events`），所以必须在提示词层面钉住。

用例用桩 LLM：不联网、不花额度、结果确定。
"""

import json

import pytest

#: 四个阶段的提示词角色句，用来识别桩 LLM 拿到的是哪一步
ENTITY_MARKER = "实体抽取专家"
IDENTIFY_MARKER = "事件识别专家"
FULL_EVENT_MARKER = "事件信息抽取专家"
RELATION_MARKER = "事件关系抽取专家"

_SAMPLE_EVENTS = [{"id": "E1", "name": "牧野之战", "time": "前1046年",
                   "location": "牧野", "parties": "周武王/商纣王"}]


class _StubLLM:
    """按提示词角色返回固定 JSON 的假客户端，同时记下每一次提示词。"""

    model = "stub"

    def __init__(self):
        self.prompts = []

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        self.prompts.append(prompt)
        if FULL_EVENT_MARKER in prompt:
            return json.dumps({"events": []}, ensure_ascii=False)
        if IDENTIFY_MARKER in prompt:
            return json.dumps({"events": _SAMPLE_EVENTS}, ensure_ascii=False)
        if ENTITY_MARKER in prompt:
            return json.dumps(
                {"places": [{"geo_name": "牧野"}, {"geo_name": "朝歌"}],
                 "organizations": [{"OrgName": "周"}],
                 "persons": [{"PersonName": "周武王"}]},
                ensure_ascii=False,
            )
        if RELATION_MARKER in prompt:
            return json.dumps({}, ensure_ascii=False)
        raise AssertionError(f"桩 LLM 收到不认识的提示词：{prompt[:120]}")


@pytest.fixture
def stub_run():
    from llm_pipeline import extract_all_optimized

    llm = _StubLLM()
    entities, events, relations, diagnostics = extract_all_optimized(llm, "周武王伐纣，战于牧野。")
    return llm, (entities, events, relations, diagnostics)


def _full_event_prompt(llm) -> str:
    """第 3 阶段（完整事件）的提示词——只有它吃 place_list/org_list/person_list。"""
    prompts = [p for p in llm.prompts if FULL_EVENT_MARKER in p]
    assert prompts, "没有走到第 3 阶段，桩 LLM 的返回或阶段标记需要调整"
    return prompts[0]


def _field_value(prompt: str, field: str) -> str:
    prefix = f"- {field}："
    for line in prompt.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()[len(prefix):]
    return ""


def test_full_event_stage_receives_entity_names(stub_run):
    """地点/组织/人物三个列表都要以**名称**形式进提示词。"""
    llm, _ = stub_run
    prompt = _full_event_prompt(llm)

    assert _field_value(prompt, "地点") == "牧野、朝歌"
    assert _field_value(prompt, "组织") == "周"
    assert _field_value(prompt, "人物") == "周武王"


def test_prompt_contains_no_python_object_repr(stub_run):
    """
    提示词里不许出现实体对象的 repr。

    这是本组用例的核心：修前的 bug 不报错、不改状态码，只是把
    `places=[PlaceEntity(geo_name='牧野', ...)]` 塞给模型，所以只能这样钉住。
    """
    llm, _ = stub_run
    prompt = _full_event_prompt(llm)

    for forbidden in ("PlaceEntity(", "EntityExtractionResult(", "OrganizationEntity(", "PersonEntity("):
        assert forbidden not in prompt, f"提示词里出现了 {forbidden} 的 repr——实体对象又被当成字符串参数传进去了"


def test_extraction_still_completes(stub_run):
    """顺带确认这次调用没把抽取链本身弄坏（阶段全过、诊断为空）。"""
    _, (entities, events, relations, diagnostics) = stub_run

    assert diagnostics["stage_ok"] == {"entity": 1, "event": 1, "relation": 1}
    assert diagnostics["partial_errors"] == []
    assert [p.geo_name for p in entities.places] == ["牧野", "朝歌"]
