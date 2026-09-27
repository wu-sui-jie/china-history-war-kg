"""产物必须自证来源，且哈希可自校验。

**这组用例保护的是"事后能不能说清指标为什么变"这件事。** 现有 `9_final_all.json` 的
metadata 里没有 `model` / `api_base` / `git_commit` / 产物哈希（见整改方案 2.5），
于是"指标变了"永远分不清是模型变了、提示词变了还是评估器变了。这里钉四件事：

1. `artifact_digest` 是**可自校验**的：字段值不依赖它自己，读回来抠掉再算一遍必然相等；
2. `save_results` 真的把模型/端点/温度/提交号/哈希写进了 metadata；
3. `merge_events` 与 `cleanup_events` 不再丢掉事件的诊断 metadata
   （丢了会让质量报告的诊断项恒等，现存 `published/quality_report.json` 就是 881/881/0/0）；
4. `evaluate.py` 的评估 metadata 把"被评估产物的版本"与"评估器的版本"**分组**记录——
   原先只写评估时的派生版本，对旧产物重跑就会把它标成新提示词版本。
"""

import json
from pathlib import Path

import pytest

import main as offline_main
from war_extraction.config import PROMPT_VERSION
from war_extraction.models import (
    EntityExtractionResult,
    Event,
    EventExtractionResult,
    PlaceEntity,
    RelationExtractionResult,
)
from war_extraction.processors import JsonToExcelConverter, ResultMerger
from war_extraction.utils.provenance import artifact_digest, file_sha256


def _sample_results(tmp_path: Path, name: str = "样例"):
    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult(
        events=[Event(EventName="牧野之战", Place="牧野")],
        metadata={"identified_event_count": 3, "final_event_count": 1, "postprocess_filtered_count": 2},
    )
    relations = RelationExtractionResult()
    result_dir = offline_main.save_results(
        name, entities, events, relations,
        input_file=Path("data/样例.txt"), text_length=12,
        output_base=tmp_path, llm_meta={"model": "stub", "api_base": "https://stub"},
    )
    return result_dir


# ------------------------------------------------------------------ 哈希可自校验

def test_产物哈希可以自校验(tmp_path):
    """`artifact_sha256` 不覆盖它自己，所以读回来重算必须得到同一个值。"""
    result_dir = _sample_results(tmp_path)
    payload = json.loads((result_dir / "9_final_all.json").read_text(encoding="utf-8"))

    recorded = payload["metadata"]["artifact_sha256"]
    assert recorded
    assert artifact_digest(payload) == recorded, "抠掉该字段后重算得到不同哈希，说明哈希覆盖了自己"

    # 内容变了哈希必须跟着变，否则它没有鉴别力
    payload["entities"]["places"].append({"geo_name": "涿鹿"})
    assert artifact_digest(payload) != recorded


def test_哈希与文件排版无关(tmp_path):
    """规范化用 sort_keys + 紧凑分隔符，所以换一次缩进不会让哈希漂移。"""
    payload = {"metadata": {}, "entities": {"places": []}, "relations": {}}
    same_content_reordered = {"relations": {}, "entities": {"places": []}, "metadata": {}}
    assert artifact_digest(payload) == artifact_digest(same_content_reordered)


# ------------------------------------------------------------------ metadata 字段

def test_产物metadata记录生成环境(tmp_path):
    """模型、端点、温度、seed、提交号、哈希都要在，缺项即这次记录没做到。"""
    result_dir = _sample_results(tmp_path)
    metadata = json.loads((result_dir / "9_final_all.json").read_text(encoding="utf-8"))["metadata"]

    assert metadata["model"] == "stub"
    assert metadata["api_base"] == "https://stub"
    assert metadata["prompt_version"] == PROMPT_VERSION
    assert isinstance(metadata["temperature"], dict) and metadata["temperature"]
    assert "seed" in metadata, "seed 未设置也要显式记 None，而不是漏记这个键"
    assert "git_commit" in metadata
    assert metadata["artifact_sha256"]


def test_published产物也带同样的自证信息(tmp_path):
    result_dir = _sample_results(tmp_path)
    metadata = json.loads(
        (result_dir / "published" / "final.json").read_text(encoding="utf-8")
    )["metadata"]
    assert metadata["model"] == "stub"
    assert metadata["publish_stage"] == "published"
    assert metadata["artifact_sha256"]


def test_分步中间产物不再写出(tmp_path):
    """1_places.json … 8_events.json 与聚合文件的对应字段逐字节一致，属纯重复。"""
    result_dir = _sample_results(tmp_path)
    for index, suffix in enumerate(
        ["1_places", "2_persons", "3_organizations", "4_event_place_relations",
         "5_event_person_relations", "6_event_organization_relations",
         "7_event_event_relations", "8_events"],
        start=1,
    ):
        assert not (result_dir / f"{suffix}.json").exists(), f"第 {index} 个分步产物又被写出来了"
    # 权威聚合产物与质量报告仍在
    assert (result_dir / "9_final_all.json").is_file()
    assert (result_dir / "10_quality_report.json").is_file()


# ------------------------------------------------------------------ 诊断 metadata 不再丢

def test_merge_events保留诊断metadata():
    """段间合并要按段汇总诊断计数——丢了它质量报告的事件诊断项就恒等。"""
    chunks = [
        EventExtractionResult(
            events=[Event(EventName="A")],
            metadata={"identified_event_count": 2, "final_event_count": 1,
                      "postprocess_filtered_count": 1, "postprocess_merged_count": 0},
        ),
        EventExtractionResult(
            events=[Event(EventName="B")],
            metadata={"identified_event_count": 3, "final_event_count": 2,
                      "postprocess_filtered_count": 1, "postprocess_merged_count": 1},
        ),
    ]
    merged = ResultMerger.merge_events(chunks)
    assert merged.metadata["identified_event_count"] == 5
    assert merged.metadata["final_event_count"] == 3
    assert merged.metadata["postprocess_filtered_count"] == 2
    assert merged.metadata["merged_from_chunks"] == 2


def test_merge_events_没有metadata时不凭空造数字():
    """没有诊断信息就保持空 dict，不猜一个"看起来正常"的值。"""
    merged = ResultMerger.merge_events([EventExtractionResult(events=[Event(EventName="A")])])
    assert merged.metadata == {}


def test_cleanup_events把丢掉的事件计入后处理过滤数():
    events = EventExtractionResult(
        events=[Event(EventName="牧野之战", Place="牧野"),
                Event(EventName="涿鹿之战", Place="涿鹿")],
        metadata={"identified_event_count": 4, "final_event_count": 2,
                  "postprocess_filtered_count": 0},
    )
    # 「只有概括、没有具体战事」的事件会被丢掉，计数必须跟着走
    events.events[1].source_text = "原文仅提及事件名称"
    cleaned = offline_main.cleanup_events(events)

    assert [e.EventName for e in cleaned.events] == ["周武王灭商牧野之战"], (
        "「原文仅提及事件名称」的那条应被丢掉，留下的那条按别名表规范化"
    )
    assert cleaned.metadata["final_event_count"] == 1
    assert cleaned.metadata["postprocess_filtered_count"] == 1
    assert cleaned.metadata["missing_event_count"] == 3


def test_质量报告的事件诊断项不再恒等(tmp_path):
    """`identified_event_count` 与 `postprocess_filtered_count` 要能读出真实差异。"""
    result_dir = _sample_results(tmp_path)
    report = json.loads((result_dir / "10_quality_report.json").read_text(encoding="utf-8"))
    diagnostics = report["diagnostics"]
    assert diagnostics["identified_event_count"] == 3
    assert diagnostics["final_event_count"] == 1
    assert diagnostics["postprocess_filtered_count"] == 2


# ------------------------------------------------------------------ Excel 只依赖聚合产物

def test_excel只靠聚合产物就能生成(tmp_path):
    """批次目录里只有 9_final_all.json 时也要能出全部表格（原先会 FileNotFoundError）。"""
    import shutil

    source = Path(__file__).resolve().parents[1] / "output" / "中国历代战争简史" / "9_final_all.json"
    if not source.is_file():
        pytest.skip("没有产物文件，跳过（output/ 不入库）")

    batch = tmp_path / "批次"
    batch.mkdir()
    shutil.copy(source, batch / "9_final_all.json")
    JsonToExcelConverter().convert_all(batch, "冒烟")

    produced = sorted(p.name for p in (batch / "excel").iterdir())
    assert len(produced) == 9, produced
    assert "冒烟_全部数据.xlsx" in produced


def test_缺少聚合产物时给出明确错误(tmp_path):
    with pytest.raises(FileNotFoundError, match="9_final_all.json"):
        JsonToExcelConverter().convert_all(tmp_path, "冒烟")


# ------------------------------------------------------------------ 评估 metadata 分组

def test_评估metadata分开发评估的产物与评估器(tmp_path):
    """对旧产物跑评估时，不能把旧产物的版本串写成当前派生版本。"""
    import evaluate as evaluate_module

    pred_path = tmp_path / "old.json"
    pred_path.write_text(json.dumps({
        "metadata": {"prompt_version": "prompt-v1-旧产物", "extraction_version": "extraction-v1-旧产物"},
        "entities": {}, "events": {}, "relations": {},
    }), encoding="utf-8")
    pred_data = json.loads(pred_path.read_text(encoding="utf-8"))

    config_path = Path(__file__).resolve().parents[1] / "config" / "eval_config.json"
    metadata = evaluate_module.build_eval_metadata(pred_path, pred_data, config_path, {})

    # 被评估产物那一组：写的是**产物自己**的版本
    assert metadata["predictions"]["prompt_version"] == "prompt-v1-旧产物"
    assert metadata["predictions"]["sha256"] == file_sha256(pred_path)
    # 评估器那一组：写的是**本次运行**的代码版本
    assert metadata["evaluator"]["prompt_version"] == PROMPT_VERSION
    assert metadata["evaluator"]["eval_config_sha256"] == file_sha256(config_path)
    # 标注文件指纹也在，便于"换成哪份 gold 了"可查
    assert metadata["evaluator"]["annotation_files"]


def test_全缓存重放时继承上一版产物的model_served(tmp_path):
    """
    缓存重放是文档推荐的**免费路径**（规则类改动一律先干跑），但它一次模型调用都不发生
    → `model_served` 是 `None`。直接写 None 就会把产物的"模型自证"抹掉一次，而这份产物是要
    发布进知识库的（下游 `current_dataset.json` 也抄它的 metadata）。
    所以：**没有调用时继承上一版记的值并注明来源；有调用时以本次为准。**
    """
    import json as _json

    from main import _with_inherited_model_served

    result_dir = tmp_path / "batch"
    result_dir.mkdir()

    assert _with_inherited_model_served(result_dir, {"model_served": None}) == {"model_served": None}, \
        "没有上一版产物时保持 None（那是诚实的'未知'）"

    (result_dir / "9_final_all.json").write_text(
        _json.dumps({"metadata": {"model_served": "deepseek-flash"}}), encoding="utf-8")

    inherited = _with_inherited_model_served(
        result_dir, {"model": "deepseek-flash", "model_served": None})
    assert inherited["model_served"] == "deepseek-flash", "全缓存命中时继承上一版"
    assert inherited["model_served_inherited_from"].endswith("9_final_all.json"), \
        "来源要写清楚，不能让它冒充成本次自证"

    fresh = _with_inherited_model_served(result_dir, {"model_served": "另一个模型"})
    assert fresh["model_served"] == "另一个模型" and "model_served_inherited_from" not in fresh, \
        "本次有调用就不继承"
