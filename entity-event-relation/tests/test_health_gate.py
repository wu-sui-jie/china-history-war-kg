"""产物体检的**门禁项**必须是个能当门禁用的数字（第二轮 A2）。

**为什么要单独一个文件。** `KNOWN_ENUM_EXCEPTIONS` 登记了"已确认、暂不打算修"的枚举外取值
（当前唯一一条是 `event_organization_relations/指挥所`），但门禁项原先只是四类 `outside_enum`
**直接求和、不减例外**，于是「枚举外关系名」**恒 ≥ 1**。而这张表上方写着"一个永远非零的门禁项
会让下一个人反复去查同一件事"——门禁自己违背了这句话：`--baseline` 比对照永远看到同一个 1，
真实的新增枚举外取值就混在这个常量里看不出来。

这里钉三件事：

1. 已登记的例外被扣掉（门禁值落到 0）；
2. **未登记**的取值一条都不扣（门禁值 +1，不能被减法吞掉）；
3. 已消失的例外不扣成负数（否则"旧例外消失 + 新问题出现"会互相抵消）。

第二轮之后又加了一组**事件字段级**门禁（第三阶段 D3）：字段占位词、攻守方里的非组织值、
`Result` 的弱值。它们与上面那组有一个共同要求：**旧基线里没有这一节时不能判成回归**——
旧报告生成于这些检查加入之前，把它当成"基线是 0"会让每个新门禁项一建就是红的。
所以这里也钉住"基线缺项 → 跳过并点名"。

但"跳过"只是不误报，它还带来一个**反向的坑**：如果入库的基线**永远**缺那几节，
那几项就等于没有门禁——`--baseline` 每次都输出"本次不比较"，谁也不会注意。
D3 刚落地时正是这个状态（入库的只有 `health_check_before.json`）。所以这里再钉一条：
**入库基线 `health_check_after.json` 必须让每一个门禁项都取得到值**；
以后再加门禁项却忘了更新它，用例直接变红并给出重生成命令。

用例只构造报告字典、直接调 `_GATE_PATHS` 的取值函数，不读全量产物（`output/` 不入库）；
只有端到端那条写一份临时产物文件走 `build_report`。
"""

import json
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.artifact_health_check import (  # noqa: E402
    KNOWN_ENUM_EXCEPTIONS,
    RELATION_CATEGORIES,
    _GATE_PATHS,
    build_report,
    compare_with_baseline,
)

BASELINE_HEALTH = MODULE_ROOT / "evaluation" / "baseline" / "health_check_before.json"
#: 入库的**当前**基线（D3 之后、含事件字段级那几节）。`health_check_before.json` 是
#: 加这些检查**之前**的报告，按设计不含那几节，所以不能拿它当"当前基线"。
BASELINE_HEALTH_AFTER = MODULE_ROOT / "evaluation" / "baseline" / "health_check_after.json"
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"

#: 门禁项的名字（报告里的标签），取值函数从这里取，避免用例重复写死索引
_GATE_BY_LABEL = dict(_GATE_PATHS)
OUTSIDE_ENUM_RELATIONS = "枚举外关系名"


def _report_with(outside: dict) -> dict:
    """只填门禁项要读的那部分：四类关系的 `outside_enum`。"""
    return {
        "enum_values": {
            category: {"total": 0, "outside_enum": dict(outside) if category == "event_organization_relations" else {}}
            for category in RELATION_CATEGORIES
        },
    }


def test_已登记的例外被扣掉():
    """`指挥所` 是已登记的例外，门禁值应为 0——否则它恒 ≥ 1，新问题混在常量里看不出来。"""
    gate = _GATE_BY_LABEL[OUTSIDE_ENUM_RELATIONS]
    assert "event_organization_relations/指挥所" in KNOWN_ENUM_EXCEPTIONS, "这条依赖登记了 `指挥所`"
    assert gate(_report_with({"指挥所": 1})) == 0


def test_未登记的枚举外取值一条都不扣():
    """
    扣减必须**按键匹配**，不能写成 `len(KNOWN_ENUM_EXCEPTIONS)` 硬减：
    硬减会在将来新增真实问题时把不该扣的也扣掉（新增的取值没登记，却被减法一并吞了）。
    """
    gate = _GATE_BY_LABEL[OUTSIDE_ENUM_RELATIONS]
    assert gate(_report_with({"指挥所": 1, "某新关系": 3})) == 1


def test_已消失的例外不扣成负数():
    """报告里没有 `指挥所` 时不该再扣——否则"旧例外消失 + 新问题出现"会互相抵消。"""
    gate = _GATE_BY_LABEL[OUTSIDE_ENUM_RELATIONS]
    assert gate(_report_with({})) == 0
    # 门禁数的是"枚举外取值的种数"，两种未登记的取值就是 2
    assert gate(_report_with({"某新关系": 2, "另一个新关系": 5})) == 2


def test_旧版体检报告没有例外表也能比对照():
    """
    `evaluation/baseline/health_check_before.json` 是加 `known_enum_exceptions` **之前**生成的，
    没有那个键。门禁取值函数不能因此报错——比对照是拿新旧两份报告互比。
    """
    if not BASELINE_HEALTH.is_file():
        pytest.skip("没有历史体检报告，跳过")
    report = json.loads(BASELINE_HEALTH.read_text(encoding="utf-8"))
    assert "known_enum_exceptions" not in report, "这条用例的前提是那份报告早于例外表"
    assert _GATE_BY_LABEL[OUTSIDE_ENUM_RELATIONS](report) == 0


def test_现实产物上门禁为0():
    """现唯一一条枚举外关系名是已登记的例外，所以对真实产物取门禁值必须是 0。"""
    if not DEFAULT_PRED.is_file():
        pytest.skip("没有产物（output/ 不入库），跳过")
    report = build_report(DEFAULT_PRED)
    assert _GATE_BY_LABEL[OUTSIDE_ENUM_RELATIONS](report) == 0


# ------------------------------------------------- 第三阶段 D3：事件字段级门禁

PARTY_GATE = "攻守方非组织值"
WEAK_RESULT_GATE = "Result 弱值或占位"
PLACEHOLDER_GATE = "事件字段写占位词"
SUPREME_GATE = "同一事件多条统帅"


def _minimal_artifact(tmp_path: Path, aggressor: str) -> dict:
    """
    最小产物：一条事件、一条组织、一条人物。`aggressor` 控制攻守方填什么。

    只放检查要读的节：`entities` / `events` / 空 `relations`。`build_report` 里
    `check_annotations` 读的是仓库里的真实标注（那是只读的固定输入）。
    """
    payload = {
        "events": {"events": [{
            "EventName": "牧野之战", "EventType": "战争", "DynastyName": "商",
            "StartDate": "前1046年", "Place": "牧野",
            "Aggressor": aggressor, "Defender": "商军", "Result": "周胜",
            "source_text": "周武王率诸侯之师与商军战于牧野。",
        }]},
        "entities": {
            "places": [{"geo_name": "牧野", "DynastyName": "商"}],
            "persons": [{"PersonName": "周武王", "DynastyName": "商"}],
            "organizations": [{"OrgName": "商军", "OrgType": "军队", "DynastyName": "商"}],
        },
        "relations": {},
    }
    path = tmp_path / "artifact.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_攻守方填人名会进门禁(tmp_path):
    """
    验收口径（工作单 D3）：构造一条"攻守方填人名"的事件，该项 +1。

    为什么必须进**门禁**而不是只报数：阶段三 E1/E3 要改的正是这类形态，
    而它不依赖参考集，所以这就是"改完有没有变少"的验收线。
    """
    report = build_report(_minimal_artifact(tmp_path, aggressor="周武王"))
    gate = _GATE_BY_LABEL[PARTY_GATE]
    assert report["party_not_org"]["person_as_party"] == 1, report["party_not_org"]
    assert report["party_not_org"]["samples"][0]["kind"] == "person"
    assert gate(report) == 1
    # 反面：填组织时这一项为 0，不然门禁恒非零、看不出变化
    clean = build_report(_minimal_artifact(tmp_path / "clean", aggressor="周军"))
    assert gate(clean) == 0


def test_同名的朝代与国名不算地点混入(tmp_path):
    """
    `唐`/`魏`/`秦`/`契丹` 这类名字**同时在**地点表与组织表里。把它们算成"地点名混入"
    会让这一项被朝代名灌满（实测 366 处），而那既不是噪声也不是填错。
    所以口径是"在地点名单里、且**不在**组织名单里"才算。
    """
    path = _minimal_artifact(tmp_path, aggressor="周武王")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entities"]["organizations"].append({"OrgName": "唐", "OrgType": "国家"})
    payload["entities"]["places"].append({"geo_name": "唐"})
    payload["events"]["events"][0]["Aggressor"] = "唐"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report = build_report(path)
    assert report["party_not_org"]["place_as_party"] == 0, report["party_not_org"]
    assert _GATE_BY_LABEL[PARTY_GATE](report) == 0


def test_Result弱值进门禁(tmp_path):
    """工作单 D3 第 3 项：复用 `publish_rules.json` 的 `weak_result_tokens`。"""
    path = _minimal_artifact(tmp_path, aggressor="周军")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"]["events"][0]["Result"] = "周军获胜，暂时控制局势"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report = build_report(path)
    assert report["weak_result"]["weak_token"] == 1
    assert _GATE_BY_LABEL[WEAK_RESULT_GATE](report) == 1


def test_字段占位词进门禁(tmp_path):
    """工作单 D3 第 1 项：把原先只是信息项的占位词计数升为门禁，按字段分列。"""
    path = _minimal_artifact(tmp_path, aggressor="周军")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"]["events"][0].update({"TroopSize": "不详", "Casualties": "未知"})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report = build_report(path)
    assert report["placeholder_as_value"]["placeholder_rows"]["TroopSize"] == 1
    assert _GATE_BY_LABEL[PLACEHOLDER_GATE](report) == 2


#: 旧报告的骨架：**没有** D3 新加的三节。枚举那几节的形状按门禁取值函数要读的键给全，
#: 不然对照会在**报告侧**先抛 `KeyError`（那是真错误，与"基线缺项"不同）。
_OLD_REPORT = {
    "enum_values": {
        **{category: {"outside_enum": {}} for category in RELATION_CATEGORIES},
        "OrgType": {"outside_enum": {}}, "Role": {"outside_enum": {}},
        "EventType": {"outside_enum": {}, "empty": 0},
    },
    "dangling_relations": {category: {"normalized_dangling": 0} for category in RELATION_CATEGORIES},
    "duplicates": {}, "residual_years": {"count": 0}, "direction_vs_time": {"count": 0},
    "evidence_reuse": {"evidence_empty": 0},
    "annotations": {},
}


def _new_report(party=0, weak=0, placeholders=None, supreme=0):
    report = dict(_OLD_REPORT)
    report["placeholder_as_value"] = {"placeholder_rows": placeholders or {"TroopSize": 0}}
    report["party_not_org"] = {"person_as_party": party, "place_as_party": 0}
    report["weak_result"] = {"undecidable": weak}
    report["supreme_commander_claims"] = {"events_with_multiple": supreme}
    return report


def test_基线缺项时跳过而不是判为变差():
    """
    **A2 那个坑的第二次预防。** 门禁项的语义是"不应比上一版更多"，而旧基线里没有这些节——
    若把"基线取不到值"当成 0，新门禁项第一次对照就全红（当前实测值 1897 / 255 / 26 / 698），
    于是下一个人只能把门禁删掉。这里要求：跳过 + 点名，且不产生任何回归项。
    """
    regressions, skipped = compare_with_baseline(
        _new_report(party=255, weak=26, supreme=698), dict(_OLD_REPORT))
    assert regressions == []
    assert {label for label, _value in skipped} == {
        PARTY_GATE, WEAK_RESULT_GATE, PLACEHOLDER_GATE, SUPREME_GATE}


def test_两边都有这项时照常比增减():
    """跳过只针对"基线缺项"；两边都测过就必须比——不然新门禁等于没建。"""
    baseline = _new_report(party=255, weak=26, supreme=698)
    regressions, skipped = compare_with_baseline(
        _new_report(party=256, weak=26, supreme=698), baseline)
    assert skipped == []
    assert [(label, before, now) for label, before, now in regressions] == [(PARTY_GATE, 255, 256)]
    assert compare_with_baseline(_new_report(party=255, weak=26, supreme=698), baseline)[0] == []


def test_入库基线能取到全部门禁项():
    """
    **"跳过"的反向保证。** 入库基线必须让每一个门禁项都取得到值，否则那一项在
    `--baseline` 里永远输出"本次不比较"，**静默失去门禁作用**——D3 刚落地时就是这样
    （入库的只有 `health_check_before.json`，它没有 `party_not_org`/`weak_result` 两节）。

    这条用例的作用：以后再加门禁项却忘了重新生成入库基线，用例直接变红并给出命令。
    """
    if not BASELINE_HEALTH_AFTER.is_file():
        pytest.skip("没有入库基线（health_check_after.json），跳过")
    baseline = json.loads(BASELINE_HEALTH_AFTER.read_text(encoding="utf-8"))
    missing = []
    for label, getter in _GATE_PATHS:
        try:
            getter(baseline)
        except (KeyError, TypeError):
            missing.append(label)
    assert missing == [], (
        f"入库基线取不到这些门禁项要读的节：{missing}。\n"
        "重生成（对着当前产物跑一次即可）：\n"
        "  python tools/artifact_health_check.py "
        "--json evaluation/baseline/health_check_after.json"
    )


def test_入库基线自带说明与产物指纹():
    """
    入库的基线是一份**冻结的快照**，事后没人能从数字反推它读的是哪一版产物——
    "两份基线看着一样、其实一份来自改前产物"这个项目已经付过一次学费
    （整改方案附录 B 第 26 条：产物指纹与数值必须一起记；附录 A 里还有两条同类）。

    所以要求两件事：① 报告里带 `source_sha256`，且**与磁盘上那份产物对得上**
    （对不上＝基线过期，该重新生成）；② 带一句 `--note` 说明来历。

    产物与 `output/` 都不入库，所以产物不在时跳过指纹校验——那种环境下这份基线只能当
    "某个批次产物的报告"看，这正是要写 note 的原因。
    """
    if not BASELINE_HEALTH_AFTER.is_file():
        pytest.skip("没有入库基线（health_check_after.json），跳过")
    baseline = json.loads(BASELINE_HEALTH_AFTER.read_text(encoding="utf-8"))
    assert baseline.get("note"), (
        "入库基线必须带 --note（说明它对应哪份产物、为什么与上一份并排放着）"
    )
    assert baseline.get("source_sha256"), "报告必须记产物指纹"
    if DEFAULT_PRED.is_file():
        from war_extraction.utils.provenance import file_sha256

        assert baseline["source_sha256"] == file_sha256(DEFAULT_PRED), (
            "基线里的产物指纹与磁盘上的产物不一致：这份基线已经过期，"
            "重新生成后再用（命令见 test_入库基线能取到全部门禁项 的断言消息）"
        )
