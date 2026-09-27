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

import ast
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


def test_残缺关系的丢弃也要计数():
    """
    空事件名 / 空关系名这一支原先**无痕丢弃**：`continue` 掉了但不计数，与同函数 docstring
    承诺的"并计数——悬空边数要能被体检脚本与质量报告看见"直接矛盾。它们不是"悬空"
    （对不上事件名单），但同样是**被丢掉的边**，该被看见——这正是本项目反复吃亏的模式。
    """
    relations = RelationExtractionResult(event_place_relations=[
        EventPlaceRelation(EventName="在名单里", relation="", modern_name="某地", evidence="x"),
        EventPlaceRelation(EventName="", relation="主战场", modern_name="某地", evidence="x"),
    ])
    kept, stats = offline_main.cleanup_relation_conflicts(relations, valid_event_names={"在名单里"})
    assert kept.event_place_relations == [], "残缺关系不该留在产物里"
    assert stats["dangling_relations_dropped"]["empty_name_or_relation"] == 2
    # 与"悬空"分开记：混在一起就分不清"名称对不上"与"字段残缺"
    assert stats["dangling_relations_dropped"]["event_place_relations"] == 0


# ---------------------------------------------------------------- C1 发布过滤丢整类关系

def _publish_fixture_relations(relations):
    """按发布拆分的入参构造：事件要能过 `is_publishable_event`（与上面那条端到端用例同款）。"""
    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult(events=[
        Event(EventName="牧野之战", EventType="统一战争", DynastyName="商", StartDate="前1046年",
              Place="牧野", Aggressor="周军", Defender="商军", Result="周胜",
              source_text="周武王率周军与商军战于牧野。"),
        Event(EventName="牧野之战役", EventType="战争", DynastyName="商", StartDate="前1046年",
              Place="牧野", Aggressor="周军", Defender="商军", Result="周胜",
              source_text="周武王率周军与商军战于牧野。"),
    ])
    return entities, events, RelationExtractionResult(event_event_relations=relations)


def test_包含与条件关系能进发布子集():
    """
    五个规范事件-事件关系类型里，`包含关系` 与 `条件关系` 原先**永远进不了 published**：
    兜底是 `return relation == "并列关系"`。实测 raw 有 `包含关系` 28 条 + `条件关系` 1 条，
    published 里 0 条——而 published 是 SQLite/Neo4j/RAG 的输入，不是"少"，是"没有"。
    """
    entities, events, relations = _publish_fixture_relations([
        _rel("牧野之战", "牧野之战役", "包含关系", "牧野之战是牧野之战役的一部分"),
        _rel("牧野之战", "牧野之战役", "条件关系", "如果有牧野之战，才有牧野之战役"),
    ])
    published, _candidate, _stats = offline_main.split_publishable_outputs(entities, events, relations)
    published_types = {rel.relation for rel in published[2].event_event_relations}
    assert "包含关系" in published_types
    assert "条件关系" in published_types


def test_证据为空的包含与条件关系仍被挡():
    """
    放开的是"类型"，不是"证据"：同一个类型、同样的事件对，证据为空必须仍被挡在 published 外。
    """
    entities, events, relations = _publish_fixture_relations([
        _rel("牧野之战", "牧野之战役", "包含关系", ""),
        _rel("牧野之战", "牧野之战役", "条件关系", ""),
    ])
    published, candidate, _stats = offline_main.split_publishable_outputs(entities, events, relations)
    assert published[2].event_event_relations == []
    assert len(candidate[2].event_event_relations) == 2


def test_放宽的只有包含与条件两类():
    """
    C1 的改动**只**给 `包含关系`/`条件关系` 开了口子：`因果关系` 仍要求强因果词、
    `顺承关系` 仍要求两个事件名都在证据里。不然"修一个漏放行"会变成"整体放水"，
    而这类放宽会静默地让发布子集变样。
    """
    entities, events, relations = _publish_fixture_relations([
        # 有证据、但没有强因果词 → 仍不该进发布子集
        _rel("牧野之战", "牧野之战役", "因果关系", "牧野之战在前，牧野之战役在后"),
        # 有证据、但两个事件名没同时出现 → 仍不该进发布子集
        _rel("牧野之战", "牧野之战役", "顺承关系", "随后周军继续东进"),
    ])
    published, candidate, _stats = offline_main.split_publishable_outputs(entities, events, relations)
    assert published[2].event_event_relations == []
    assert len(candidate[2].event_event_relations) == 2


def test_置信门槛不再替枚举外的类型兜底(normalizer):
    """
    第三阶段 D1：`is_high_confidence_event_event_relation` 的兜底原先写成
    `return bool(evidence.strip())`，而 docstring 说"**枚举外的类型**：证据非空即放行"。

    **那句话描述的是一个永远不会发生的行为**：`published` 分流处先调 `_relation_enum_ok`，
    枚举外的类型（`主战场`）在到达置信门槛之前就被挡掉。契约与实现不符 + "安全"完全依赖
    调用点的 `and` 顺序——下一个人调换顺序时，那句注释还会替改动背书。
    现在兜底收窄成它真正负责的两类，枚举外一律 `False`。

    这条必须直接断言**函数自己的返回值**：走发布分流看不出来——`_relation_enum_ok`
    无论顺序如何都会把枚举外类型挡在 `published` 外，两种实现的分流结果一模一样。
    """
    foreign = _rel("牧野之战", "牧野之战役", "主战场", "牧野之战是牧野之战役的主战场")
    assert offline_main._is_high_confidence_event_event_relation(foreign, normalizer) is False


def test_置信门槛对包含与条件关系仍按证据非空放行(normalizer):
    """收窄兜底**不能顺手收紧已放行的两类**：那两类的门槛（证据非空）是 C1 定的口径。"""
    for relation in ("包含关系", "条件关系"):
        assert offline_main._is_high_confidence_event_event_relation(
            _rel("牧野之战", "牧野之战役", relation, "有证据"), normalizer) is True
        assert offline_main._is_high_confidence_event_event_relation(
            _rel("牧野之战", "牧野之战役", relation, ""), normalizer) is False


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


def test_诊断为空时清洗统计仍要落到顶层metadata(tmp_path):
    """
    `text_meta`（清洗统计）与 `extraction_diagnostics` 原先是**同一次挂载**，于是
    `run.diagnostics` 为空时那次 `return` 把它们一起挡掉：`text_meta` 没挂上 →
    `_artifact_body` 的 `pop("text_cleaning", None)` 取到 None → 顶层
    `metadata.text_cleaning` **静默变成 null**（"看起来正常、实际没记录"）。

    当前 `run_extraction` 恒返回带键的 diagnostics，所以这只是潜在缺陷——但这条链
    有一个无提示的失效口，钉住它比等它发生便宜。
    """
    entities = EntityExtractionResult(places=[PlaceEntity(geo_name="牧野")])
    events = EventExtractionResult(
        events=[Event(EventName="牧野之战", EventType="统一战争", DynastyName="商",
                      Place="牧野", Aggressor="周军", Defender="商军", Result="周胜")])

    class _RunWithoutDiagnostics:
        diagnostics = {}

    offline_main._attach_extraction_diagnostics(
        events, _RunWithoutDiagnostics(), {"text_cleaning": {"enabled": True, "soft_line_breaks_merged": 2}})
    assert "extraction_diagnostics" not in (events.metadata or {}), "没有诊断就不该凭空造一个"

    result_dir = offline_main.save_results(
        "样例", entities, events, RelationExtractionResult(),
        input_file=Path("x.txt"), text_length=10, output_base=tmp_path,
        llm_meta={"model": "stub", "api_base": "stub"},
    )
    metadata = json.loads((result_dir / "9_final_all.json").read_text(encoding="utf-8"))["metadata"]
    assert metadata["text_cleaning"]["soft_line_breaks_merged"] == 2, "清洗统计不该因为诊断为空而变成 null"


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


# ---------------------------------------------------------------- B1：source_offset 仍未接线

#: 允许出现 `source_offset` 的唯一生产文件（它声明并赋值这个字段）。
_OFFSET_DECLARING_FILE = Path("war_extraction") / "core" / "extraction_runner.py"

#: 接线时要一起改的地方（用例变红时会打印出来，避免下一个人只改代码）。
_OFFSET_WIRING_CHECKLIST = (
    "① 本用例 + `test_分段带原文偏移`（用例名与断言要改成「已接线」的口径）；"
    "② 指南 §1.16 的「暂未接线」那段；③ README 的「仍未接线」条；"
    "④ 产物形状变了 → 走整改方案 §3.5 的 7 步同步清单（导入脚本/前端/文档一起改）"
)


def _read_offset_usages(path: Path) -> list:
    """
    源码里对 `source_offset` 的**读取**位置（按 AST，不误报注释与字符串）。

    `main.py` 的注释、`text_cleaner.py` 的说明都提到这个名字，文本匹配会误报，
    所以逐类语法节点看：属性访问（`chunk.source_offset`）、关键字实参（`ChunkExtraction(source_offset=…)`）、
    局部名，以及 `getattr(obj, "source_offset")` 这种**字符串取法**——
    最后一种只认 `getattr`/`setattr`/`hasattr` 的实参，不把任意同名字符串常量算进来
    （否则 `RUN_MODE = "source_offset"` 这种无关常量会让守卫自己报假警）。

    **按 `utf-8-sig` 读**：仓库里有带 BOM 的文件（`backend/models.py`），
    用 `utf-8` 读会把 BOM 留在文本里，`ast.parse` 直接抛 `SyntaxError`
    ——那时守卫会从"检查代码"变成"报一个与主题无关的解析错"。
    """
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "source_offset":
            lines.append(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg == "source_offset":
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "source_offset":
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _is_string_attr_call(node, "source_offset"):
            lines.append(node.lineno)
    return sorted(set(lines))


def _is_string_attr_call(node, name: str) -> bool:
    """`getattr(x, "名字")` / `setattr` / `hasattr` 这类按字符串取名的调用。"""
    func = node.func
    func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if func_name not in {"getattr", "setattr", "hasattr", "delattr"}:
        return False
    return any(isinstance(arg, ast.Constant) and arg.value == name for arg in node.args)


def test_source_offset仍未接线():
    """
    第二轮 B1 选的是"如实记为未接线"，而不是"接进产物"。**这个状态只靠文档措辞维持**，
    所以这里加一条机械守卫：生产代码里除 `extraction_runner.py`（声明 + 两处赋值）外，
    不许有任何地方碰 `source_offset`。

    为什么需要它：下一个人写新功能（比如"人工抽检导出"）时不会知道它没接线，
    很可能直接读 `chunk.source_offset`，然后在产物里找不到（chunk 级信息从不序列化）；
    反过来，真接线时也没有任何提示"该改哪几处文档与哪条用例"。

    **接线时不要删这条用例了事**——按 `_OFFSET_WIRING_CHECKLIST` 列的四处一起改
    （那份清单会打印在下方的断言消息里）。
    """
    module_root = Path(__file__).resolve().parents[1]
    repo_root = module_root.parent
    scanned = []
    for rel in ("main.py", "evaluate.py"):
        scanned.append(module_root / rel)
    for pattern in ("war_extraction/**/*.py", "tools/*.py"):
        scanned.extend(module_root.glob(pattern))
    for pattern in ("backend/**/*.py", "RAG/**/*.py"):
        scanned.extend(repo_root.glob(pattern))

    offenders = {}
    for path in scanned:
        if not path.is_file():
            continue
        if path.resolve() == (module_root / _OFFSET_DECLARING_FILE).resolve():
            continue
        # `war_extraction/**` 里只有那一个文件可以出现；其余非测试生产代码一律不许
        if path.name.startswith("test_"):
            continue
        lines = _read_offset_usages(path)
        if lines:
            offenders[str(path.relative_to(repo_root))] = lines

    assert offenders == {}, (
        f"`source_offset` 在下面这些生产文件里被读到了：{offenders}。\n"
        "它目前**未接线**（chunk 级信息不进产物），所以读它等于读一个产物里不存在的字段。\n"
        f"真要接线，请同时改：{_OFFSET_WIRING_CHECKLIST}"
    )


def test_source_offset的声明与赋值仍在原处():
    """
    反面确认：守卫的前提是"那个字段确实存在、也确实在赋值"。若哪天 `extraction_runner.py`
    里连声明都不见了，上一条用例会因为"没人读它"而**继续通过**——那才是真的丢了能力。
    """
    module_root = Path(__file__).resolve().parents[1]
    lines = _read_offset_usages(module_root / _OFFSET_DECLARING_FILE)
    assert len(lines) >= 3, f"声明 + 两处赋值应当在，实际只找到 {lines}"


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
