"""参考集工具链的口径保护（`tools/annotation_io.py` 等）。

**为什么要有这组用例。** 参考集是**指标的分母**，而围绕它的四个工具
（稳定 ID、证据回填、新旧 diff、判定实验抽样）都建立在两条口径上：

1. **稳定 ID 只跟"名称 + 朝代"（关系是三元组）绑定**——把某条的地点/时间改对了，ID 不该变；
   否则 diff 会把"改对了一处"显示成"删一条 + 加一条"，而"分母变了"就再也看不出来。
2. **记账字段不算内容变更**（`annotation_id` / `evidence_*` / `locate_status`）——
   否则每次重跑回填都会显示成"全表都改了"。

这两条一旦漂了，后果不是报错而是**误读**（把标注改动当成模型退化），正是这个项目吃过学费的那类坑
（`data/annotations/README.md` §四）。用例只构造小样例，不读真实参考集与产物（它们不入库）。
"""

import json
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.annotation_io import (  # noqa: E402
    ID_PREFIXES,
    event_core,
    event_key,
    load_annotations,
    relation_key,
    stable_id,
)
from tools.assign_annotation_ids import build_index, report  # noqa: E402
from tools.diff_annotation_sets import compare  # noqa: E402
from war_extraction.utils.normalizer import Normalizer  # noqa: E402
from war_extraction.utils.value_parsing import event_identity_key  # noqa: E402

NORMALIZER = Normalizer()


def _event(**kwargs):
    row = {"EventName": "牧野之战", "DynastyName": "商", "StartDate": "前1046年", "Place": "牧野"}
    row.update(kwargs)
    return row


# ------------------------------------------------------------------ 稳定 ID 的不变量

def test_同一条记录的稳定ID只跟名称与朝代绑定():
    """
    改地点、改时间、改结果都不该换 ID——它们是**会被改对的字段**；
    名称或朝代变了才是另一条记录。
    """
    base = _event()
    same = _event(Place="牧野、朝歌", StartDate="前1046年（另说）", Result="周胜")
    assert event_core(base, NORMALIZER) == event_core(same, NORMALIZER)
    assert (stable_id("evt", event_core(base, NORMALIZER))
            == stable_id("evt", event_core(same, NORMALIZER)))

    renamed = _event(EventName="牧野之役")
    other_dynasty = _event(DynastyName="西周")
    assert event_core(base, NORMALIZER) != event_core(renamed, NORMALIZER)
    assert event_core(base, NORMALIZER) != event_core(other_dynasty, NORMALIZER)


def test_稳定ID与行序无关():
    """插入/删除别的行不该让某条记录的 ID 变（序号式 ID 会整体位移，那是 diff 的灾难）。"""
    ids_one = [stable_id("evt", event_core(_event(EventName=name), NORMALIZER))
               for name in ("甲战", "乙战", "丙战")]
    ids_two = [stable_id("evt", event_core(_event(EventName=name), NORMALIZER))
               for name in ("新战", "甲战", "乙战", "丙战", "末战")]
    assert ids_two[1:4] == ids_one


def test_整数年份不崩():
    """
    人工标注里的年份有整数写法（`StartDate: 618`）。`event_identity_key` 原先直接
    `(start_date or "").strip()` → `AttributeError`，而它只在**读参考集**时被触发，
    于是"读标注崩掉"会被误当成"标注有问题"。这条钉住那次加固。
    """
    key = event_identity_key(NORMALIZER, "玄武门之变", "唐", 626, None)
    assert key[2] == "626"
    assert event_identity_key(NORMALIZER, "玄武门之变", "唐", None, None)[2] == ""


def test_定位键的格式与抽样表一致():
    """`event|归一名|朝代|时间|地点` 与 `类别|事件|关系|目标`——人工表与 `--compare` 都按它认记录。"""
    key = event_key(NORMALIZER, "牧野之战", "商", "前1046年", "牧野")
    parts = key.split("|")
    assert parts[0] == "event" and len(parts) == 5
    assert parts[1] == NORMALIZER.normalize_event_name("牧野之战"), "第二段是归一名（走别名表）"
    assert parts[2:] == ["商", "前1046年", "牧野"]
    assert relation_key("event_person_relations", "牧野之战", "统帅", "周武王") \
        == "event_person_relations|牧野之战|统帅|周武王"


# ------------------------------------------------------------------ 冲突检测

def _gold(events=None, places=None, organizations=None, relations=None, persons=None):
    return {
        "events": events or [], "places": places or [], "persons": persons or [],
        "organizations": organizations or [],
        "relations": {label: (relations or {}).get(label, []) for label in
                      ("event-place", "event-org", "event-person", "event-event")},
    }


def test_同一条写两遍与同名两场要分开报():
    """
    两类问题含义不同：**同一个归一键出现多行**＝同一条写了两遍（要删）；
    **同一个稳定 ID 多行但归一键不同**＝同名不同年代/地点（合法，但要人确认）。
    混在一起报，C 组就不知道该删还是该留。
    """
    gold = _gold(
        organizations=[
            {"OrgName": "汉", "DynastyName": "西汉"},
            {"OrgName": "汉", "DynastyName": "西汉"},      # 同一条写了两遍
            {"OrgName": "汉", "DynastyName": "东汉"},      # 同名不同朝代：ID 相同、归一键不同
        ],
    )
    result = report(build_index(gold))["organizations"]
    assert result["rows"] == 3
    assert result["duplicate_rows"] == 1, "同归一键的第二行算重复行"
    assert result["same_id_multiple_rows"] == 1, "西汉/东汉的『汉』是同一个稳定 ID"


def test_事件的身份含时间与地点():
    """事件归一键与全项目的身份定义一致（名称 + 朝代 + 起始时间 + 首个地点）。"""
    gold = _gold(events=[_event(), _event(Place="朝歌")])
    result = report(build_index(gold))["events"]
    assert result["rows"] == 2 and result["duplicate_rows"] == 0, "地点不同 → 不是重复行"
    assert result["same_id_multiple_rows"] == 1, "但稳定 ID 相同（名称+朝代）→ 要人看一眼"


# ------------------------------------------------------------------ 新旧 diff

def _dir(tmp_path: Path, gold: dict) -> Path:
    target = tmp_path / "ann"
    target.mkdir(parents=True, exist_ok=True)
    (target / "sample_events.json").write_text(
        json.dumps({"events": gold["events"]}, ensure_ascii=False), encoding="utf-8")
    (target / "sample_entities.json").write_text(json.dumps(
        {"places": gold["places"], "persons": gold["persons"],
         "organizations": gold["organizations"]}, ensure_ascii=False), encoding="utf-8")
    (target / "sample_relations.json").write_text(
        json.dumps(gold["relations"], ensure_ascii=False), encoding="utf-8")
    return target


def test_diff分出新增删除与字段变更(tmp_path):
    old = _dir(tmp_path / "old", _gold(events=[_event(), _event(EventName="鸣条之战")]))
    new = _dir(tmp_path / "new", _gold(events=[
        _event(Result="周胜"),                    # 同一场：补了字段 → 字段变更
        _event(EventName="牧野之役"),              # 换了名称 → 一删一增（名称是身份的一部分）
    ]))
    result = compare(load_annotations(old), load_annotations(new))
    assert len(result["changed"]) == 1 and result["changed"][0]["字段"] == {"Result": [None, "周胜"]}
    assert len(result["added"]) == 1, "换名称＝新记录"
    assert len(result["removed"]) == 1, "旧集里那条『鸣条之战』在新集没有了"


def test_diff把记账字段排除在字段变更之外(tmp_path):
    """
    回填证据、写 ID 都会改行内容。若把它们算成"字段变更"，那么"跑一遍回填"就会显示成
    "全表都改了"，真正的标注改动反而被淹没。
    """
    old_row = _event()
    new_row = dict(_event(), annotation_id="evt-123", evidence="战于牧野", evidence_start=1,
                   evidence_end=5, locate_status="唯一命中")
    old = _dir(tmp_path / "old", _gold(events=[old_row]))
    new = _dir(tmp_path / "new", _gold(events=[new_row]))
    result = compare(load_annotations(old), load_annotations(new))
    assert result["changed"] == [] and result["added"] == [] and result["removed"] == []
    assert result["unchanged"] == 1


def test_前缀表与文件常量没被改坏():
    """工具之间靠这些常量对齐（前缀进 ID、文件名单必须是参考集那三个）。"""
    assert ID_PREFIXES["events"] == "evt" and ID_PREFIXES["relations"] == "rel"
    for name in ("sample_entities.json", "sample_events.json", "sample_relations.json"):
        assert name.endswith(".json")


# ------------------------------------------------------------------ 草稿核验表

def test_草稿核验表的列与留空():
    """
    草稿表是人工的**工作台**：`核验结论`/`修正内容`/`判据`/`备注` 四列必须留空
    （填表人一眼看出哪些行还没处理），`定位键` 与 `annotation_id` 必须已经填好
    （成稿后才能引用与 diff）。

    原文不在仓库里（受版权约束、已 gitignore），所以缺原文时跳过——这条在本地跑得到。
    """
    from tools.build_draft_annotation_table import REBUILD_COLUMNS, build_rows
    from tools.backfill_annotation_evidence import Locator
    from tools.build_draft_annotation_table import DEFAULT_BOOK

    if not Path(DEFAULT_BOOK).is_file():
        pytest.skip("原文不在本地，跳过")

    payload = {
        "events": {"events": [{
            "EventName": "牧野之战", "EventType": "战争", "DynastyName": "商",
            "StartDate": "前1046年", "Place": "牧野", "Aggressor": "周军", "Defender": "商军",
            "Result": "周胜", "source_text": "周武王率诸侯之师东进，与商纣王的军队战于牧野。",
        }]},
        "entities": {"places": [{"geo_name": "牧野", "DynastyName": "商", "modern_name": None}],
                     "persons": [], "organizations": []},
        "relations": {"event_place_relations": [{"EventName": "牧野之战", "relation": "主战场",
                                                "modern_name": "牧野", "evidence": "战于牧野"}]},
    }
    rows = build_rows(payload, ("商",), Locator(Path(DEFAULT_BOOK)))
    assert rows, "应该切出记录"
    for row in rows:
        # 公开列必须正好是 REBUILD_COLUMNS；另外两个是内部字段（只进 JSON 旁车，落盘工具靠它）
        assert set(REBUILD_COLUMNS) <= set(row)
        assert set(row) - set(REBUILD_COLUMNS) == {"_payload", "_attribute"}
        assert row["核验结论"] == row["修正内容"] == row["判据"] == row["备注"] == ""
        assert row["annotation_id"] and row["定位键"]
        assert row["_payload"], "旁车要带原始记录"


# ------------------------------------------------------------------ 核验表汇总与 IAA

def test_核验表汇总把没填的单独报出来():
    """
    "没填"不能混进分母：它既不是保留也不是删除。这一步单列出来，才能一眼看出"还有多少没判"。
    草稿保留率的分母是"保留 + 删除"（+ 修改/新增单列），与 B 组的抽样精确率不是一个口径。
    """
    from tools.summarize_rebuild_table import summarize

    rows = [
        {"分层": "事件:明", "核验结论": "保留", "修正内容": ""},
        {"分层": "事件:明", "核验结论": "删除", "修正内容": ""},
        {"分层": "关系:事件-人物", "核验结论": "修改", "修正内容": "relation=将领；Result=周胜"},
        {"分层": "关系:事件-人物", "核验结论": "新增", "修正内容": ""},
        {"分层": "事件:明", "核验结论": "", "修正内容": ""},
    ]
    result = summarize(rows)
    assert result["rows"] == 5
    assert result["unfilled"] == 1
    assert (result["kept"], result["deleted"], result["modified"], result["added_by_human"]) == (1, 1, 1, 1)
    assert result["draft_keep_rate"] == 0.5, "分母是 保留+删除"
    assert result["changed_fields"] == {"relation": 1, "Result": 1}


def test_IAA按定位键配对并列出不一致():
    """两人的表可能行数不同（有人补了新增行），所以按定位键配对、并分开报"只在一边有"。"""
    from tools.summarize_rebuild_table import agreement

    a = [{"定位键": "event|甲", "核验结论": "保留"},
         {"定位键": "event|乙", "核验结论": "删除"},
         {"定位键": "event|丙", "核验结论": "保留"}]
    b = [{"定位键": "event|甲", "核验结论": "保留"},
         {"定位键": "event|乙", "核验结论": "保留"},
         {"定位键": "event|丁", "核验结论": "保留"}]
    result = agreement(a, b)
    assert result["shared_keys"] == 2 and result["agreed"] == 1
    assert result["agreement_rate"] == 0.5
    assert result["only_in_a"] == 1 and result["only_in_b"] == 1
    assert result["disagreements"][0] == {"定位键": "event|乙", "甲": "删除", "乙": "保留"}


# ------------------------------------------------------------------ dev / test 切分

def test_切分让关系跟着head事件走(tmp_path):
    """
    **同一场战役的事件与它的关系必须落在同一边**（否则测试集里的关系在评估时因为事件不在
    而根本不进分母——旧参考集就是这个毛病的一种）。head 不在事件表的关系**不猜边**，单独报出来。
    """
    from tools.split_annotation_set import split, write_side

    gold = {
        "events": [{"EventName": "明战", "DynastyName": "明"},
                   {"EventName": "唐战", "DynastyName": "唐"}],
        "places": [{"geo_name": "某地", "DynastyName": "明"}],
        "persons": [{"PersonName": "甲", "DynastyName": "唐"}],
        "organizations": [],
        "relations": {
            "event-person": [{"head": "明战", "relation": "统帅", "tail": "甲"},   # 跟 head 走 → dev
                             {"head": "唐战", "relation": "统帅", "tail": "甲"},   # → test
                             {"head": "无此事件", "relation": "统帅", "tail": "甲"}],  # 不猜边
            "event-place": [], "event-org": [], "event-event": [],
        },
    }
    (events, entities, relations), orphan = split(gold, ("明",))
    assert [row["EventName"] for row in events["dev"]] == ["明战"]
    assert [row["EventName"] for row in events["test"]] == ["唐战"]
    assert len(relations["dev"]["event-person"]) == 1
    assert len(relations["test"]["event-person"]) == 1
    assert orphan == 1, "head 不在事件表的关系既不该进 dev 也不该进 test"
    assert [row["geo_name"] for row in entities["dev"]["places"]] == ["某地"]

    write_side(tmp_path, "dev", events, entities, relations)
    written = json.loads((tmp_path / "dev" / "sample_events.json").read_text(encoding="utf-8"))
    assert [row["EventName"] for row in written["events"]] == ["明战"]


# ------------------------------------------------------------------ 核验表落盘

def _sidecar():
    return {"records": [
        {"分层": "事件:明", "定位键": "event|甲|明",
         "_payload": {"EventName": "甲战", "Place": "甲地"}, "_attribute": None},
        {"分层": "事件:明", "定位键": "event|乙|明",
         "_payload": {"EventName": "乙战"}, "_attribute": None},
        {"分层": "关系:事件-人物", "定位键": "event_person_relations|甲战|统帅|丙",
         "_payload": {"head": "甲战", "relation": "统帅", "tail": "丙"},
         "_attribute": "event_person_relations"},
    ]}


def test_落盘工具按四档结论落成稿():
    """保留照抄、删除丢掉、修改覆盖字段、新增整条由修正内容拼出来——四档都必须机械成立。"""
    from tools.apply_rebuild_table import apply

    rows = [
        {"分层": "事件:明", "定位键": "event|甲|明", "核验结论": "修改", "修正内容": "Place=乙地"},
        {"分层": "事件:明", "定位键": "event|乙|明", "核验结论": "删除", "修正内容": ""},
        {"分层": "关系:事件-人物", "定位键": "event_person_relations|甲战|统帅|丙",
         "核验结论": "保留", "修正内容": ""},
        {"分层": "事件:明", "定位键": "event|丙|明", "核验结论": "新增",
         "修正内容": "EventName=丙战；DynastyName=明"},
    ]
    records, stats = apply(_sidecar(), rows)
    assert [row["EventName"] for row in records["events"]] == ["甲战", "丙战"], "乙被删、丙是新增"
    assert records["events"][0]["Place"] == "乙地", "修正内容要覆盖原字段"
    assert len(records["relations"]["event_person_relations"]) == 1
    assert stats["结论/（未填）"] == 0 and stats["新增（人工补漏）"] == 1


def test_落盘工具把没填的当保留但报数():
    """
    "没填"不等于"判过了"。工具按保留落盘（否则整表落不出来），但必须把这个数报出来——
    统计里有它，报告里也点名。这条钉住"别把没填读成保留"。
    """
    from tools.apply_rebuild_table import apply

    rows = [{"分层": "事件:明", "定位键": "event|甲|明", "核验结论": "", "修正内容": ""}]
    records, stats = apply(_sidecar(), rows)
    assert stats["结论/（未填）"] == 1
    assert [row["EventName"] for row in records["events"]] == ["甲战"]


def test_落盘工具对空修正内容的新增行不落空记录():
    """新增行必须写清字段；只填"新增"不填内容时跳过并计数，不能落一条空记录进参考集。"""
    from tools.apply_rebuild_table import apply

    rows = [{"分层": "事件:明", "定位键": "event|丁|明", "核验结论": "新增", "修正内容": ""}]
    records, stats = apply(_sidecar(), rows)
    assert records["events"] == []
    assert stats["新增行但修正内容为空（跳过）"] == 1


# ------------------------------------------------------------------ 仲裁落地与合并去重

def test_仲裁结论覆盖核验表里的原始判定():
    """
    双人复核抽样的那部分由仲裁逐条给结论，它**覆盖**核验表里甲的原始判定。三种都要钉住：
    改判（保留→修改，字段被覆盖）、翻案（删除→保留，记录回到成稿）、反过来的（保留→删除，去掉）。
    不给 arbitration 时一律按表里的判定走——**没喂仲裁就等于仲裁没发生**，
    2026-09-27 那版成稿正是漏了这一步（60 条仲裁结论没落地）。
    """
    from tools.apply_rebuild_table import apply

    table = [
        {"分层": "事件:明", "定位键": "event|甲|明", "核验结论": "保留", "修正内容": ""},
        {"分层": "事件:明", "定位键": "event|乙|明", "核验结论": "删除", "修正内容": ""},
        {"分层": "事件:明", "定位键": "event|丙|明", "核验结论": "保留", "修正内容": ""},
    ]
    sidecar = {"records": [
        {"分层": "事件:明", "定位键": f"event|{name}|明", "_payload": {"EventName": f"{name}战"},
         "_attribute": None}
        for name in ("甲", "乙", "丙")
    ]}

    records, _ = apply(sidecar, table)
    assert [r["EventName"] for r in records["events"]] == ["甲战", "丙战"], "不喂仲裁就按表的判定"

    arbitration = {"event|甲|明": ("修改", "Place=甲地"),
                   "event|乙|明": ("保留", ""),
                   "event|丙|明": ("删除", "")}
    records, stats = apply(sidecar, table, arbitration)
    assert [r["EventName"] for r in records["events"]] == ["甲战", "乙战"], "乙被仲裁翻案保留、丙被删"
    assert records["events"][0]["Place"] == "甲地", "仲裁的修正要覆盖字段"
    assert stats["仲裁改判了表的结论"] == 3


def test_合并多个子集时同一条记录只留一份():
    """
    跨朝代的同名行会同时出现在多个子集的草稿里，两个子集都判「保留」时同一条就被写了两遍
    （2026-09-27 那版成稿有 15 条这样的完全重复行，连 annotation_id 都一样）。
    落成后必须按记录身份去重——**内容全等**才算重复。
    """
    from tools.apply_rebuild_table import apply, dedupe

    rows = [{"分层": "实体:地点", "定位键": "entity:place|兰州|唐|兰州市",
             "核验结论": "保留", "修正内容": ""}] * 2
    sidecar = {"records": [{"分层": "实体:地点", "定位键": "entity:place|兰州|唐|兰州市",
                            "_payload": {"geo_name": "兰州", "DynastyName": "唐", "modern_name": "兰州市"},
                            "_attribute": None}]}

    records, _ = apply(sidecar, rows)
    assert len(records["places"]) == 2, "落成阶段会写两遍（这是真实发生过的事）"
    conflicts, stats = dedupe(records)
    assert len(records["places"]) == 1 and conflicts == []
    assert stats["places 完全重复（已去重）"] == 1


def test_同身份不同内容要当冲突报出来():
    """
    同一个身份（名称+朝代）而内容不同，**不是重复**——那是"两个子集判得不一样"。
    工具保留首条、但必须把它报成冲突：悄悄留一条会把"口径不一致"埋掉。
    """
    from tools.apply_rebuild_table import dedupe

    records = {
        "events": [], "persons": [], "organizations": [], "relations": {},
        "places": [{"geo_name": "叶赫军", "DynastyName": "明", "modern_name": "", "OrgType": "军队"},
                   {"geo_name": "叶赫军", "DynastyName": "明", "modern_name": "", "OrgType": "军事势力"}],
    }
    conflicts, stats = dedupe(records)
    assert len(records["places"]) == 1 and records["places"][0]["OrgType"] == "军队", "保留首条"
    assert len(conflicts) == 1 and conflicts[0]["身份"] == ("叶赫军", "明", "")
    assert stats["places 同身份内容冲突（保留首条）"] == 1


def test_切分时被引用的实体跟随关系的边():
    """
    实体不能只按自己的朝代归边。关系的边跟随**头事件**，而目标实体可能不属于同一朝代——
    「明子集里的战事，参战方是一个唐时才有的政权」是正当行。只按朝代切，dev 那条关系就指向
    一个落在 test 的实体，结构自检报「tail 不在对应名单」——那种红是**切分造成的假问题**，
    会盖住真正的结构问题。跨边被引用的实体同时出现在两边不构成泄漏（评估用名称集合）。
    """
    from tools.split_annotation_set import split

    gold = {
        "events": [{"EventName": "明战", "DynastyName": "明"},
                   {"EventName": "唐战", "DynastyName": "唐"}],
        "places": [{"geo_name": "兰州", "DynastyName": "唐", "modern_name": "兰州市"}],
        "persons": [], "organizations": [],
        "relations": {"event-place": [{"head": "明战", "relation": "主战场", "tail": "兰州"}],
                      "event-org": [], "event-person": [], "event-event": []},
    }
    (events, entities, relations), orphan = split(gold, ("明",))
    assert [e["EventName"] for e in events["dev"]] == ["明战"]
    assert [e["EventName"] for e in events["test"]] == ["唐战"]
    assert len(relations["dev"]["event-place"]) == 1
    assert [p["geo_name"] for p in entities["dev"]["places"]] == ["兰州"], "被 dev 的关系引用，要跟到 dev"
    assert [p["geo_name"] for p in entities["test"]["places"]] == ["兰州"], "唐那一边也还要有它"
    assert orphan == 0


def test_多份仲裁表合并时后给的覆盖前面的():
    """
    后续的人工判定要**另存补录文件**，不能改 IAA 那一轮的原始仲裁记录——否则事后没法回答
    "IAA 的一致率是怎么算出来的"。两份之间同名键由后面的覆盖，合并顺序要确定。
    """
    import csv
    import tempfile
    from pathlib import Path

    from tools.apply_rebuild_table import load_arbitrations

    header = ["定位键", "分层", "甲", "乙", "仲裁结论", "仲裁修正", "仲裁判据"]
    with tempfile.TemporaryDirectory() as tmp:
        first, second = Path(tmp) / "原始仲裁.csv", Path(tmp) / "人工判定补录.csv"
        for path, rows in (
            (first, [["event|甲|明", "事件:明", "保留", "删除", "删除", "", "原始那轮的结论"],
                     ["event|乙|明", "事件:明", "删除", "保留", "保留", "", "原始那轮的结论"]]),
            (second, [["event|甲|明", "事件:明", "保留", "未抽到", "修改", "Place=乙地", "补录改判"]]),
        ):
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)

        merged, counts = load_arbitrations([first, second])
    assert [count for _path, count in counts] == [2, 1], "两份各读多少行要报出来"
    assert merged["event|甲|明"] == ("修改", "Place=乙地"), "后给的覆盖同名键"
    assert merged["event|乙|明"] == ("保留", ""), "只在前一份里的键原样保留"


# ------------------------------------------------------------------ 多子集当 dev + 切分说明

def _split_gold():
    """三个子集各一条事件的小样（明 / 唐 / 秦汉各一族），用来验多子集划分。"""
    return {
        "events": [{"EventName": "明战", "DynastyName": "明"},
                   {"EventName": "唐战", "DynastyName": "唐"},
                   {"EventName": "秦战", "DynastyName": "秦"}],
        "places": [{"geo_name": "明地", "DynastyName": "明"},
                   {"geo_name": "唐地", "DynastyName": "唐"}],
        "persons": [], "organizations": [],
        "relations": {"event-place": [{"head": "明战", "relation": "主战场", "tail": "明地"},
                                      {"head": "唐战", "relation": "主战场", "tail": "唐地"}],
                      "event-org": [], "event-person": [], "event-event": []},
    }


def test_dev可以给多个子集():
    """
    dev/test 不是固定"明当 dev"，`--dev` 可给多个子集（写法如 `--dev 唐 秦汉`）。
    这条口径会因**样本量**而调整：dev 越大，"指标区间不重叠"才判得动。
    子集名要展开成朝代取值（秦汉 = 秦 + 西汉 + 东汉），否则秦与西汉的事件会被切到 test 去。
    """
    from tools.split_annotation_set import SUBSET_DYNASTIES, split

    dev_dynasties = tuple(d for name in ("唐", "秦汉") for d in SUBSET_DYNASTIES[name])
    (events, entities, relations), _orphan = split(_split_gold(), dev_dynasties)
    assert [row["EventName"] for row in events["dev"]] == ["唐战", "秦战"], "秦汉要在 dev 里"
    assert [row["EventName"] for row in events["test"]] == ["明战"]
    assert [row["geo_name"] for row in entities["dev"]["places"]] == ["唐地"]
    assert [row["geo_name"] for row in entities["test"]["places"]] == ["明地"]


def test_dev不能给全部子集():
    """
    test 的意义是"只看一次"。dev 给了全部子集 → test 为空 → 那套纪律就没了，
    工具必须**拒绝**而不是照切（拒绝要带非零退出码，脚本链里才拦得住）。
    """
    import subprocess

    from tools.split_annotation_set import MODULE_ROOT

    result = subprocess.run(
        [sys.executable, str(MODULE_ROOT / "tools" / "split_annotation_set.py"),
         "--annotations", str(MODULE_ROOT / "data" / "annotations" / "v2"),
         "--dev", "明", "唐", "秦汉",
         "--output", str(MODULE_ROOT / "evaluation" / "test_draft_check" / "_should_not_exist")],
        capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2, "拒绝时要有非零退出码"
    assert "不能给全部子集" in (result.stdout or "")
    assert not (MODULE_ROOT / "evaluation" / "test_draft_check" / "_should_not_exist").exists(), \
        "拒绝了就不该写出任何东西"


def test_两侧条数与切分说明对得上(tmp_path):
    """
    切分说明（`split_manifest.json`）是"记录 ③：按什么切、边界在哪"的落档——"指标为什么变了"
    有一半的可能是"dev/test 换边了"。所以它的数字必须**与实际写出的文件一致**，
    否则这份说明本身就是误导。
    """
    from tools.split_annotation_set import build_manifest, split, write_side

    (events, entities, relations), orphan = split(_split_gold(), ("唐",))
    for side in ("dev", "test"):
        write_side(tmp_path, side, events, entities, relations)
    manifest = build_manifest(("唐",), ("唐",), events, entities, relations, orphan)

    assert manifest["development 子集"] == ["唐"] and manifest["development 朝代"] == ["唐"]
    for side in ("dev", "test"):
        written_events = json.loads(
            (tmp_path / side / "sample_events.json").read_text(encoding="utf-8"))["events"]
        written_entities = json.loads(
            (tmp_path / side / "sample_entities.json").read_text(encoding="utf-8"))
        written_relations = json.loads(
            (tmp_path / side / "sample_relations.json").read_text(encoding="utf-8"))
        counts = manifest["两侧条数"][side]
        assert counts["事件"] == len(written_events)
        assert counts["实体"] == sum(len(rows) for rows in written_entities.values())
        assert counts["关系"] == sum(len(rows) for rows in written_relations.values())


def test_冻结指纹同时给两个行尾口径(tmp_path):
    """
    `.gitattributes` 是 `* text=auto eol=lf`，**新克隆拿到的文件是 LF**；而 Windows 上这些
    JSON/CSV 由写入端用文本模式写出、工作区里是 CRLF。两个口径的 sha256 不同
    （实测 v2 三份文件全部不同），所以冻结记录只记一个，会让"按记录核对"在别的机器上
    得出假的「文件变了」。两个都记，并说明哪个是跨机器口径。
    """
    import hashlib

    from tools.freeze_baseline import _dir_fingerprints

    crlf = chr(13) + chr(10)
    lf = chr(10)
    body_lf = '{"x": 1}' + lf
    (tmp_path / "a.json").write_bytes((body_lf.replace(lf, crlf)).encode("utf-8"))

    fingerprints = _dir_fingerprints(tmp_path, kind="annotations")
    entry = fingerprints["a.json"]
    assert set(entry) == {"sha256", "sha256_lf"}, "两个口径都要记"
    assert entry["sha256"] != entry["sha256_lf"], "CRLF 与 LF 的哈希不同——否则这条口径没有意义"
    assert entry["sha256_lf"] == hashlib.sha256(body_lf.encode("utf-8")).hexdigest(), \
        "sha256_lf 必须是行尾归一（CRLF→LF）后的哈希，别的机器靠它核对"


# ------------------------------------------------------------------ 子集产物合并

def test_合并产物把三族列表首尾相接并逐份记来源(tmp_path):
    """
    参考集的 dev 横跨两个子集（唐 + 秦汉），而抽取是一份文本一份产物 → 评估要一份合并后的预测。
    合并必须**逐份记来源**（路径、sha256、模型、条数）：拿拼接出来的预测去评 dev，
    事后要能说清它由哪几次运行产出，否则评估结论没有归属。
    """
    import json as _json

    from tools.merge_extraction_outputs import build_metadata, merge

    def payload(name, events, places):
        return {
            "metadata": {"extracted_at": "2026-09-27", "model": "deepseek-flash",
                         "model_served": "deepseek-flash"},
            "entities": {"places": places, "organizations": [], "persons": []},
            "events": {"events": events, "metadata": {}},
            "relations": {"event_place_relations": [{"EventName": name}],
                          "event_organization_relations": [],
                          "event_person_relations": [], "event_event_relations": []},
            "quality_report": {"counts": {"events": len(events)}},
        }

    paths = []
    for idx, name in enumerate(("唐产物", "秦汉产物")):
        path = tmp_path / f"{idx}.json"
        path.write_text(_json.dumps(payload(name, [{"EventName": name}], [{"geo_name": name}]),
                                    ensure_ascii=False), encoding="utf-8")
        paths.append(path)
    payloads = [_json.loads(p.read_text(encoding="utf-8")) for p in paths]

    merged = merge(payloads)
    assert [e["EventName"] for e in merged["events"]["events"]] == ["唐产物", "秦汉产物"]
    assert [p["geo_name"] for p in merged["entities"]["places"]] == ["唐产物", "秦汉产物"]
    assert len(merged["relations"]["event_place_relations"]) == 2

    metadata = build_metadata(payloads, paths)
    assert len(metadata["merged_from"]) == 2
    assert metadata["merged_from"][0]["model_served"] == "deepseek-flash"
    assert metadata["merged_from"][0]["counts"] == {"events": 1, "entities": 1, "relations": 1}
    assert all(len(e["sha256"]) == 64 for e in metadata["merged_from"]), "要记来源文件的哈希"


def test_合并产物把各份一致的来源字段提到顶层(tmp_path):
    """
    `evaluate.py` 的 metadata 读**顶层**的 `prompt_version` / `model`：不提上去，
    "这次评估用的是哪套提示词、哪个模型"在报告里就是 None——而那正是"指标必须与口径一起记"要记的。
    各份**不一致**时不提（混着两套提示词的合并产物，写哪一个都是错的）。
    """
    import json as _json

    from tools.merge_extraction_outputs import build_metadata

    def payload(prompt_version, model="deepseek-flash"):
        return {"metadata": {"prompt_version": prompt_version, "model": model,
                             "model_served": model},
                "entities": {"places": [], "organizations": [], "persons": []},
                "events": {"events": [{"EventName": "甲"}], "metadata": {}},
                "relations": {k: [] for k in ("event_place_relations",
                                              "event_organization_relations",
                                              "event_person_relations", "event_event_relations")}}

    paths = []
    for idx, pv in enumerate(("prompt-v2-A", "prompt-v2-A")):
        path = tmp_path / f"same{idx}.json"
        path.write_text(_json.dumps(payload(pv), ensure_ascii=False), encoding="utf-8")
        paths.append(path)
    same = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    meta = build_metadata(same, paths)
    assert meta["prompt_version"] == "prompt-v2-A", "各份一致就提到顶层"
    assert meta["model_served"] == "deepseek-flash"
    assert len(meta["merged_from"]) == 2, "逐份明细仍要保留"

    mixed_paths = []
    for idx, pv in enumerate(("prompt-v2-A", "prompt-v2-B")):
        path = tmp_path / f"mixed{idx}.json"
        path.write_text(_json.dumps(payload(pv), ensure_ascii=False), encoding="utf-8")
        mixed_paths.append(path)
    mixed = [json.loads(p.read_text(encoding="utf-8")) for p in mixed_paths]
    mixed_meta = build_metadata(mixed, mixed_paths)
    assert "prompt_version" not in mixed_meta, "不一致就不写——宁缺勿错"
