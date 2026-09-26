"""F02 LLM 兜底的守护用例。

覆盖三条口径：
1. 词典完全未命中 + 开关打开 → 走兜底，entities 非空且标注 llm_entity_used=True；
2. 兜底客户端失败/返回垃圾 → 降级为词典结果（空实体），**不抛错**、标签仍为 False；
3. 开关关闭（或词典有命中）→ 一次都不调用客户端；
另验证解析器的容错（markdown 包裹、非法 JSON、类型过滤）与结果缓存（同题只调一次）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.query import prompts as q_prompts
from server.query.dictionary_matcher import DictionaryMatcher, load_jieba
from server.query.understand import QuestionUnderstanding

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (SNAP / "entities.json").exists() or not (SNAP / "dicts.json").exists(),
    reason="本地快照缺失（data/ 不入 Git）",
)


class FakeFallback:
    """替代 EntityFallbackClient：可控返回、记录调用次数。"""

    def __init__(self, result=None, error: bool = False):
        self.result = result if result is not None else []
        self.error = error
        self.calls = 0
        self.available = True

    def extract(self, question: str) -> list[dict]:
        self.calls += 1
        if self.error:
            raise RuntimeError("模拟兜底调用失败")
        return self.result


@pytest.fixture(scope="module")
def matcher():
    load_jieba(SNAP)          # 实体名入 jieba（与 server.query.load_understanding 同口径）
    return DictionaryMatcher(SNAP)


def _qu(matcher, fallback, enabled=True):
    return QuestionUnderstanding(matcher, llm_client=fallback, enable_llm=enabled)


# 词典一定命中不到的问句（虚构实体）
UNKNOWN_Q = "请问青岚关之战是谁指挥的？"


def test_fallback_used_when_dictionary_misses(matcher):
    fb = FakeFallback([{"name": "青岚关之战", "type": "事件"}])
    out = _qu(matcher, fb).understand(UNKNOWN_Q)
    assert out.llm_entity_used is True
    assert [e.name for e in out.entities] == ["青岚关之战"]
    assert out.entities[0].confidence == "low"
    assert fb.calls == 1


def test_fallback_failure_degrades_silently(matcher):
    """兜底抛异常 → 返回空实体、不报错、标签 False（调用方按未命中处理）。"""
    fb = FakeFallback(error=True)
    out = _qu(matcher, fb).understand(UNKNOWN_Q)
    assert out.llm_entity_used is False
    assert out.entities == []


def test_fallback_not_called_when_disabled(matcher):
    fb = FakeFallback([{"name": "青岚关之战", "type": "事件"}])
    out = _qu(matcher, fb, enabled=False).understand(UNKNOWN_Q)
    assert out.llm_entity_used is False
    assert fb.calls == 0


def test_fallback_not_called_when_dictionary_hits(matcher):
    """词典命中时不应触发兜底（默认路径零额外调用、零额外延迟）。"""
    fb = FakeFallback([{"name": "某某", "type": "事件"}])
    out = _qu(matcher, fb).understand("介绍一下长平之战。")
    assert fb.calls == 0
    assert out.llm_entity_used is False
    assert out.entities, "词典应命中长平之战"


def test_fallback_result_cached_per_question(matcher):
    fb = FakeFallback([{"name": "青岚关之战", "type": "事件"}])
    qu = _qu(matcher, fb)
    qu.understand(UNKNOWN_Q)
    qu.understand(UNKNOWN_Q)
    assert fb.calls == 1, "同一问句只应调用一次（进程内缓存）"


# ---- 解析器容错 ----

def test_parse_entities_plain_json():
    assert q_prompts.parse_entities('{"entities":[{"name":"赤壁之战","type":"事件"}]}') == [
        {"name": "赤壁之战", "type": "事件"}]


def test_parse_entities_markdown_wrapped_and_prose():
    assert q_prompts.parse_entities('好的：\n```json\n{"entities":[{"name":"周瑜","type":"人物"}]}\n```') == [
        {"name": "周瑜", "type": "人物"}]


def test_parse_entities_filters_bad_type_and_dedups():
    text = json.dumps({"entities": [
        {"name": "周瑜", "type": "人物"},
        {"name": "周瑜", "type": "人物"},
        {"name": "某物", "type": "武器"},     # 非法类型 → 丢弃
        {"name": "", "type": "事件"},          # 空名 → 丢弃
    ]}, ensure_ascii=False)
    assert q_prompts.parse_entities(text) == [{"name": "周瑜", "type": "人物"}]


def test_parse_entities_bad_json_returns_empty():
    assert q_prompts.parse_entities("这不是 JSON") == []
    assert q_prompts.parse_entities("") == []
    assert q_prompts.parse_entities('{"entities": "不是列表"}') == []


def test_should_fallback_conditions():
    assert q_prompts.should_fallback(dictionary_hits=0, enable_llm=True, llm_client=object()) is True
    assert q_prompts.should_fallback(dictionary_hits=1, enable_llm=True, llm_client=object()) is False
    assert q_prompts.should_fallback(dictionary_hits=0, enable_llm=False, llm_client=object()) is False
    assert q_prompts.should_fallback(dictionary_hits=0, enable_llm=True, llm_client=None) is False
