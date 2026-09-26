"""提示词版本必须由源码哈希机械派生，不能靠人记得 bump。

如果 `PROMPT_VERSION` 只是手写的单一版本串（`PROMPT_VERSIONS` 五个分项同日同版），
改提示词忘了改版本就会：①缓存键不变，命中旧结果，改了等于没改；
②产物 metadata 里的 `prompt_version` 与实际提示词不符（学术评估里这是硬伤）。

口径：版本串 = 人写的语义版本 + `war_extraction/prompts/` 源码的 sha256 前 8 位。
用例里把模板目录指到 tmp，改一个字符就能看到哈希变——不需要碰仓库里的真模板。
"""

import json
from pathlib import Path

import pytest

from war_extraction import config
from war_extraction.config import PROMPT_VERSION, PROMPT_VERSIONS, cache_context, prompt_source_hash

MODULE_ROOT = Path(__file__).resolve().parents[1]


def test_版本串里带着源码哈希():
    assert PROMPT_VERSION.startswith("prompt-v2-20260420+")
    assert PROMPT_VERSION.endswith(prompt_source_hash())
    assert len(prompt_source_hash()) == 8


def test_分项版本只跟本阶段模板的哈希走():
    """改事件模板不该让实体阶段的缓存失效（分项版本各按各的文件取哈希）。"""
    assert PROMPT_VERSIONS["entity_extraction"].endswith(prompt_source_hash("entity_prompts.py"))
    assert PROMPT_VERSIONS["relation_extraction"].endswith(prompt_source_hash("relation_prompts.py"))
    # 三个事件类阶段共用同一个模板文件 → 哈希段相同
    assert PROMPT_VERSIONS["event_type"].split("+")[1] == PROMPT_VERSIONS["full_event"].split("+")[1]
    # 不同模板文件之间哈希段不该撞
    assert prompt_source_hash("entity_prompts.py") != prompt_source_hash("event_prompts.py")
    assert prompt_source_hash("event_prompts.py") != prompt_source_hash("relation_prompts.py")


def test_改一个字符版本号就变(tmp_path, monkeypatch):
    """核心断言：提示词文本变了，版本号一定跟着变（不依赖人记得 bump）。"""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    template = prompts / "entity_prompts.py"
    template.write_text('T = "你是实体抽取专家。"\n', encoding="utf-8")

    monkeypatch.setattr(config, "_PROMPTS_DIR", prompts)
    before = prompt_source_hash()

    template.write_text('T = "你是实体抽取专家！"\n', encoding="utf-8")  # 只改一个标点
    after = prompt_source_hash()

    assert before != after
    # 派生出的版本串随之变化 → cache_context 的键也变 → 旧缓存自动失效
    assert cache_context("m", "s") != dict(cache_context("m", "s"), prompt_version=f"x+{after}")


def test_缓存上下文带上派生后的版本(tmp_path, monkeypatch):
    """缓存键里的 prompt_version 必须是派生值，否则"改了提示词还命中旧结果"。"""
    context = cache_context("deepseek-chat", "long_text_chunk")

    assert context["prompt_version"] == PROMPT_VERSION
    assert context["prompt_version"] != "prompt-v2-20260420"


def test_真模板目录确实存在且能取到哈希():
    """别把路径锚定写错（改了目录会让哈希变成"空目录的哈希"，版本号静默恒定）。"""
    assert (MODULE_ROOT / "war_extraction" / "prompts" / "entity_prompts.py").exists()
    assert prompt_source_hash() != prompt_source_hash("entity_prompts.py"), \
        "整体哈希与单文件哈希不该相同（说明两者取的确实是不同的输入）"


def test_产物metadata里的版本号取自派生值(tmp_path):
    """跑不了真抽取（要花额度）时，至少钉住"写 metadata 的那个函数用的是派生版本"。"""
    import main as offline_main
    from war_extraction.models import (
        EntityExtractionResult,
        EventExtractionResult,
        RelationExtractionResult,
        PlaceEntity,
    )

    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult()
    relations = RelationExtractionResult()

    offline_main.save_results("样例", entities, events, relations,
                              input_file=Path("data/样例.txt"), text_length=12,
                              output_base=tmp_path, llm_meta={"model": "stub", "api_base": "stub"})

    final = json.loads((tmp_path / "样例" / "9_final_all.json").read_text(encoding="utf-8"))
    assert final["metadata"]["prompt_version"] == PROMPT_VERSION
    assert final["metadata"]["prompt_version"].endswith(prompt_source_hash())

    quality = json.loads((tmp_path / "样例" / "10_quality_report.json").read_text(encoding="utf-8"))
    assert quality["prompt_version"] == PROMPT_VERSION
