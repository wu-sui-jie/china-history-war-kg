"""第二轮收口改动的回归保护（核验遗留项 P0-2 / P0-3 / P1-6 / P1-10 / P2-12 / P2-19）。

这些改动有一个共同点：**它们只在"出错时"才有区别**，所以没有用例的话，改坏了下一次
重跑才会发现——而重跑是全额付费。逐条对应核验文档里的编号：

| 用例 | 核验项 | 钉住的回归 |
| --- | --- | --- |
| `test_枚举外的事件事件关系保留而不再无痕消失` | P0-2 | 枚举外类型留在产物里（进候选区），`quarantine` 给计数 |
| `test_悬空边计数覆盖四类关系` | A4 | 事件-事件被丢掉的悬空边也要计数 |
| `test_清洗统计落在顶层metadata` | P1-6② | `metadata.text_cleaning` 在顶层，不在 `events.metadata` |
| `test_清洗开关可以显式关掉` | P1-10 | 配置里写 `false` 必须真的关掉 |
| `test_分段带原文偏移` | P2-19 | `source_offset` 是**原文**坐标（清洗会改变长度） |
| `test_基线指纹覆盖提示词文件` | P2-12 | 提示词目录按 `.py` 取指纹，不再是空字典 |
| `test_基线记录能区分两次冻结` | P2-14 | `run_id` + 微秒时间戳 |
| `test_产物记录实际服务的模型` | P0-3 | `model` 与 `model_served` 分开记 |
"""

import json
from pathlib import Path

import pytest

import main as offline_main
from war_extraction.config import CONFIG_VERSION, SCHEMA_VERSION
from war_extraction.core.text_cleaner import clean_text_with_mapping
from war_extraction.models import (
    EntityExtractionResult,
    Event,
    EventEventRelation,
    EventExtractionResult,
    EventPlaceRelation,
    PlaceEntity,
    RelationExtractionResult,
)
from war_extraction.utils import Normalizer
from war_extraction.utils.provenance import generation_metadata
from war_extraction.utils.relation_rules import reduce_event_event_relations


@pytest.fixture(scope="module")
def normalizer():
    return Normalizer()


def _rel(name_a, name_b, relation, evidence="x"):
    return EventEventRelation(EventName_A=name_a, relation=relation, EventName_B=name_b, evidence=evidence)


# ---------------------------------------------------------------- P0-2 枚举外类型

def test_枚举外的事件事件关系保留而不再无痕消失(normalizer):
    """
    `主战场` 被模型写进事件-事件关系时：**保留**在产物里 + `quarantine` 计数。

    原先直接 `continue` 丢掉，后果是数据无痕消失（既不在 raw、也不在候选区，
    `moved_to_candidate_by_enum` 也看不到），与另外三类关系"raw 保留 + 发布拆分进候选区"不一致。
    """
    quarantine = []
    reduced = reduce_event_event_relations(
        normalizer, [_rel("甲战", "乙战", "主战场")], quarantine=quarantine)
    assert [rel.relation for rel in reduced] == ["主战场"]
    assert len(quarantine) == 1


def test_枚举外的事件事件关系会进候选区(tmp_path):
    """端到端：raw 保留 → `split_publishable_outputs` 把它挪进候选区（不进 published）。"""
    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult(events=[
        Event(EventName="牧野之战", EventType="统一战争", DynastyName="商", StartDate="前1046年",
              Place="牧野", Aggressor="周军", Defender="商军", Result="周胜",
              source_text="周武王率周军与商军战于牧野。"),
        Event(EventName="牧野之战役", EventType="战争", DynastyName="商", StartDate="前1046年",
              Place="牧野", Aggressor="周军", Defender="商军", Result="周胜",
              source_text="周武王率周军与商军战于牧野。"),
    ])
    # `顺承关系` 要进 published 还得满足"两个事件名都在证据里"（`is_high_confidence_...`），
    # 所以证据要写成含两个名字的原句；枚举外那条只看类型，与证据无关。
    relations = RelationExtractionResult(event_event_relations=[
        _rel("牧野之战", "牧野之战役", "主战场", "牧野之战之后就是牧野之战役"),
        _rel("牧野之战", "牧野之战役", "顺承关系", "牧野之战之后就是牧野之战役"),
    ])
    published, candidate, stats = offline_main.split_publishable_outputs(entities, events, relations)

    published_rels = published[2].event_event_relations
    candidate_rels = candidate[2].event_event_relations
    assert [rel.relation for rel in published_rels] == ["顺承关系"], "枚举外的不该进发布子集"
    assert "主战场" in [rel.relation for rel in candidate_rels], "枚举外的必须出现在候选区里"
    assert stats["enum_out"]["event_event_relations"] == 1, "拆分统计要能数出它"


def test_悬空边计数覆盖四类关系():
    """事件-事件的端点过滤原先在收敛函数内部做，"丢了多少"没有通道（报告里恒为 0）。"""
    relations = RelationExtractionResult(event_event_relations=[
        _rel("在名单里", "不在名单", "顺承关系"),
        _rel("在名单里", "也不在名单", "顺承关系"),
    ])
    _relations, stats = offline_main.cleanup_relation_conflicts(
        relations, valid_event_names={"在名单里"})
    assert stats["dangling_relations_dropped"]["event_event_relations"] == 2


def test_悬空边计数含三类实体关系():
    relations = RelationExtractionResult(
        event_place_relations=[EventPlaceRelation(EventName="不在名单", relation="主战场",
                                                  modern_name="某地", evidence="x")],
    )
    _relations, stats = offline_main.cleanup_relation_conflicts(
        relations, valid_event_names={"在名单里"})
    assert stats["dangling_relations_dropped"]["event_place_relations"] == 1


# ---------------------------------------------------------------- P1-6 / P2-19 产物形状

def _sample_artifact(tmp_path: Path):
    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult(
        events=[Event(EventName="牧野之战", EventType="统一战争", DynastyName="商",
                      Place="牧野", Aggressor="周军", Defender="商军", Result="周胜")],
        metadata={"text_cleaning": {"enabled": True, "soft_line_breaks_merged": 3}},
    )
    return offline_main.save_results(
        "样例", entities, events, RelationExtractionResult(),
        input_file=Path("x.txt"), text_length=10, output_base=tmp_path,
        llm_meta={"model": "deepseek-flash", "model_served": "deepseek-flash",
                  "thinking_mode": "disabled", "api_base": "stub"},
    )


def test_清洗统计落在顶层metadata(tmp_path):
    """`text_cleaning` 描述的是**输入文本**，不该塞在 `events.metadata` 下。"""
    result_dir = _sample_artifact(tmp_path)
    for path in (result_dir / "9_final_all.json",
                 result_dir / "published" / "final.json",
                 result_dir / "candidate" / "final.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["metadata"]["text_cleaning"]["soft_line_breaks_merged"] == 3, path
        assert "text_cleaning" not in (payload["events"].get("metadata") or {}), path


def test_产物记录实际服务的模型(tmp_path):
    """
    `model` 是请求名、`model_served` 是服务端实际服务名——两者可能不同
    （实测 `deepseek-chat` 被路由到 `deepseek-flash`），只记请求名等于自证错信息。
    """
    result_dir = _sample_artifact(tmp_path)
    metadata = json.loads((result_dir / "9_final_all.json").read_text(encoding="utf-8"))["metadata"]
    assert metadata["model"] == "deepseek-flash"
    assert metadata["model_served"] == "deepseek-flash"
    assert metadata["thinking_mode"] == "disabled"


def test_未发生调用时model_served如实为None():
    """全部命中缓存时没有响应可读，`model_served` 记 None——不拿请求名冒充。"""
    metadata = generation_metadata(llm_meta={"model": "deepseek-flash", "api_base": "stub"})
    assert metadata["model"] == "deepseek-flash"
    assert metadata["model_served"] is None


def test_缓存键含配置与schema哈希():
    """改了别名表或 pydantic 模型之后缓存必须失效，这两个版本串是载体。"""
    assert CONFIG_VERSION.startswith("config-v1-20260420+")
    assert SCHEMA_VERSION.startswith("schema-v1-20260420+")


# ---------------------------------------------------------------- P1-10 清洗开关

def test_清洗开关可以显式关掉():
    """
    原实现的条件是 `isinstance(got, type(value)) and got`，`and got` 让 `false` 被当成
    "没配"跳过——配置里写"不清洗"，跑起来照样清洗。这类"配置不生效"最难查。
    """
    from war_extraction.core.text_cleaner import load_cleaning_rules
    import tempfile

    config = Path(tempfile.mkdtemp()) / "text_cleaning.json"
    config.write_text(json.dumps({
        "merge_soft_line_breaks": False,
        "strip_invisible_chars": False,
        "ocr_fixes": {},
    }), encoding="utf-8")
    rules = load_cleaning_rules(config)
    assert rules["merge_soft_line_breaks"] is False
    assert rules["strip_invisible_chars"] is False

    _cleaned, stats, _mapping = clean_text_with_mapping("上句\n下句\u200b", rules)
    assert stats["soft_line_breaks_merged"] == 0
    assert stats["invisible_chars_removed"] == 0


def test_清洗规则缺项用默认值():
    from war_extraction.core.text_cleaner import load_cleaning_rules
    import tempfile

    config = Path(tempfile.mkdtemp()) / "text_cleaning.json"
    config.write_text(json.dumps({"ocr_fixes": {}}), encoding="utf-8")
    rules = load_cleaning_rules(config)
    assert rules["merge_soft_line_breaks"] is True
    assert rules["ocr_fixes"] == {}


# ---------------------------------------------------------------- P2-19 原文坐标

def test_分段带原文偏移():
    """
    清洗会改变长度（删不可见字符、合并折断的换行、替换错字），所以"清洗后文本的坐标"
    与"原文的坐标"是两回事。`source_offset` 必须是**原文**坐标，否则回原文定位就对不上。
    """
    raw = "第一章 上古\n\n甲句在此被折断\n继续。\n\n第二章 夏商\n\n乙句目军来。"
    cleaned, stats, mapping = clean_text_with_mapping(raw, {"strip_invisible_chars": True,
                                                           "merge_soft_line_breaks": True,
                                                           "ocr_fixes": {"目军": "日军"}})
    assert stats["soft_line_breaks_merged"] > 0, "这个夹具必须真的发生了清洗，否则测不到偏移"

    # 第二轮起始处在原文里的位置 = mapping 给的偏移
    cleaned_index = cleaned.find("第二章")
    original_index = mapping.to_original(cleaned_index)
    assert raw[original_index:original_index + 3] == "第二章"
    assert original_index != cleaned_index, "清洗改变了长度，两个坐标本来就不该相等"


def test_无映射时偏移退化为原值():
    """`--no-clean` 下清洗后文本就是原文，退化是正确的（映射传 None 时位置即原文位置）。"""
    _cleaned, _stats, mapping = clean_text_with_mapping("无变化的文本。", {
        "strip_invisible_chars": True, "merge_soft_line_breaks": True, "ocr_fixes": {}})
    assert mapping.to_original(3) == 3
    assert mapping.to_original(0) == 0


# ---------------------------------------------------------------- P2-12 / P2-14 基线

def test_基线指纹覆盖提示词文件():
    """提示词目录下全是 `.py`，而 `_dir_fingerprints` 的默认后缀是 `*.json` → 冻了个空字典。"""
    from tools.freeze_baseline import _DIR_PATTERNS, _dir_fingerprints

    assert _DIR_PATTERNS["prompts"] == ("*.py",)
    assert _DIR_PATTERNS["config"] == ("*.json",)
    prompts_dir = Path(__file__).resolve().parents[1] / "war_extraction" / "prompts"
    fingerprints = _dir_fingerprints(prompts_dir, kind="prompts")
    assert fingerprints, "提示词指纹不能是空的"
    assert all(name.endswith(".py") for name in fingerprints)


def test_基线指纹不给kind或patterns就报错():
    """不允许"忘给后缀"这类静默失败：宁可抛异常，也不要冻一个空字典。"""
    from tools.freeze_baseline import _dir_fingerprints

    with pytest.raises(ValueError):
        _dir_fingerprints(Path(__file__).resolve().parents[1] / "config")


def test_基线记录能区分两次冻结(tmp_path):
    """两份 baseline.json 的 `frozen_at` 曾落在同一秒，无法自证是两次独立运行。"""
    from tools.freeze_baseline import build_record, _DIR_PATTERNS  # noqa: F401

    record = build_record(tmp_path / "不存在.json", None, "说明")
    assert "run_id" in record and record["run_id"] is None, "字段要存在，由调用方填"
    assert "frozen_at_epoch" in record
