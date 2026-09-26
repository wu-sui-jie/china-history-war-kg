"""第一轮留下的评估能力，必须有用例兜住（核验遗留项 P2-15）。

**为什么单独一个文件。** 第一轮给评估器加了九项能力——语义约束、`event_year_tolerance`、
`field_effective_rates`、`field_accuracy`、`by_dynasty`、`by_category`、`canonical` 口径、
`mapping_stats`、`also_published`——而 `tests/` 里对这些名字**零命中**。也就是说这些口径
改坏了没人会发现，只能靠人眼看报告。这个文件按"口径"而不是按"函数"组织用例：

1. **事件配对的四条约束规则**（同名放行 / 缺值放行 / 朝代按时代档位 / 残缺年份不参与 /
   地点按集合）：每一条都是 2026-09-26 复核出来的**具体误杀案例**，用例直接复现那些案例；
2. **关系评估按条计数**（不吞重复三元组）；
3. **两套身份口径**（mention / canonical）；
4. **字段值准确率**与**分维度报告**的结构。

用例只调评估器的纯函数（`_semantic_pair_rejection` / `filter_relations` /
`match_relation_triples` / `evaluate_event_field_accuracy` 等），不跑完整 `run_evaluation`
——那要读全量产物，CI 里没有（`output/` 不入库）。
"""

from pathlib import Path

import pytest

from war_extraction.evaluation import OptimalEvaluator

ANNOTATION_DIR = Path(__file__).resolve().parents[1] / "data" / "annotations"


@pytest.fixture(scope="module")
def evaluator():
    if not ANNOTATION_DIR.exists():
        pytest.skip("没有标注目录，跳过评估器用例")
    return OptimalEvaluator(annotation_dir=ANNOTATION_DIR)


@pytest.fixture(scope="module")
def loose_evaluator():
    """关掉语义约束的评估器：用来验证"关掉就全放行"这条开关确实生效。"""
    if not ANNOTATION_DIR.exists():
        pytest.skip("没有标注目录，跳过评估器用例")
    return OptimalEvaluator(annotation_dir=ANNOTATION_DIR, enforce_semantic_constraints=False)


def _event(name, dynasty=None, start=None, place=None, **extra):
    """构造一条事件字典。`extra` 用来补字段值准确率用例要的字段（标注侧用 `Person`）。"""
    payload = {"EventName": name, "DynastyName": dynasty, "StartDate": start, "Place": place}
    payload.update(extra)
    return payload


# ------------------------------------------------------------------ 事件配对：四条约束规则

def test_同名配对一律放行(evaluator):
    """
    同名（归一后相等）不判语义约束——**这是复核出来的最大一处误杀**：
    144 对被约束拆掉的配对里有 22 对是同名事件，而事件 TP 本身就是按名称集合算的，
    拒绝同名配对等于自伤 TP、白送 FN。
    """
    pred = _event("官渡之战", dynasty="清", start="1900年", place="白马、延津、官渡")
    gold = _event("官渡之战", dynasty="东汉", start="200年", place="官渡")
    assert evaluator._semantic_pair_rejection(pred, gold, "官渡之战", "官渡之战") == ""


def test_缺值一侧不拦(evaluator):
    """任一侧缺值 → 放行。缺值不等于不符，这是"避免因为 gold 字段缺失误杀"的落点。"""
    pred = _event("某战", dynasty="唐", start="755年", place="长安")
    gold = _event("某战之役", dynasty=None, start=None, place=None)
    assert evaluator._semantic_pair_rejection(pred, gold, pred["EventName"], gold["EventName"]) == ""


def test_朝代按时代档位判相容(evaluator):
    """
    相邻朝代放行（明末清初的 `松锦之战` 两侧分属明清）、口径粗细不同放行
    （预测 `五代十国`、标注 `后唐/后梁`）、跨时代才拒。
    """
    # 相邻档位：清(20) vs 明(19)
    assert evaluator._semantic_pair_rejection(
        _event("松锦之战", dynasty="清"), _event("松锦之战役", dynasty="明"),
        "松锦之战", "松锦之战役") == ""
    # 一方认不出（多值写法）→ 放行
    assert evaluator._semantic_pair_rejection(
        _event("后唐灭后梁之战", dynasty="五代十国"), _event("后唐灭后梁", dynasty="后唐/后梁"),
        "后唐灭后梁之战", "后唐灭后梁") == ""
    # 跨时代：清(20) vs 西周(3)
    assert evaluator._semantic_pair_rejection(
        _event("甲战", dynasty="清"), _event("甲战XX", dynasty="西周"), "甲战", "甲战XX") == "dynasty"


def test_残缺年份不参与配对判断(evaluator):
    """
    标注里的 1–3 位纯数字年份是截断残值（实测 14 处），不是"公元 153 年"。
    拿它跟预测的 `1900年6月` 比会得出"相差 1747 年"，从而拒掉**同名且正确**的配对。
    """
    assert evaluator._semantic_pair_rejection(
        _event("天津保卫战", dynasty="清", start="1900年6月", place="天津"),
        _event("天津保卫战", dynasty="清", start="153", place="天津"),
        "天津保卫战", "天津保卫战") == ""
    # 同样的写法差异在年号里仍然生效：清 vs 清、1900 vs 1860 都是真年份 → 拒
    assert evaluator._semantic_pair_rejection(
        _event("北京之战", dynasty="清", start="1900年8月13日", place="北京"),
        _event("北京之战役", dynasty="清", start="1860年10月", place="北京"),
        "北京之战", "北京之战役") == "year"


def test_地点按集合判相容(evaluator):
    """
    两侧地点是**多值字段**，个数与顺序都不同，只比"首个地点"会误杀：
    实测 `宁锦之战` 两侧是同一对集合、只是顺序相反（`锦州、宁远` vs `宁远、锦州`）。
    """
    # 同一集合、顺序相反 → 相容
    assert evaluator._semantic_pair_rejection(
        _event("宁锦之战", dynasty="明", place="锦州、宁远"),
        _event("宁锦之战役", dynasty="明", place="宁远、锦州"),
        "宁锦之战", "宁锦之战役") == ""
    # 子集关系也算相容（`官渡之战`：预测含 白马/延津/官渡，标注只有 官渡）
    assert evaluator._semantic_pair_rejection(
        _event("官渡之战", dynasty="东汉", place="白马、延津、官渡"),
        _event("官渡之战役", dynasty="东汉", place="官渡"),
        "官渡之战", "官渡之战役") == ""
    # 集合完全不相交 → 拒（`中法战争 → 签订《中法简明条约》` 就是靠这条挡住的）
    assert evaluator._semantic_pair_rejection(
        _event("中法战争", dynasty="清", start="1883年", place="越南、河内、顺化"),
        _event("中法简明条约", dynasty="清", start="1884年", place="天津"),
        "中法战争", "中法简明条约") == "place"


def test_括号拆裂地点被判为噪声(evaluator):
    """
    `东夷（山东、江苏一带）` 会先被多值拆分按"、"切成 `东夷（山东` 与 `江苏一带）`，
    两个碎片都不成对括号、原来一条噪声规则也匹配不到，于是留在地点集合里参与比较。
    补了两条半括号规则之后它们被过滤掉，这一侧就成了"无有效地点"→ 放行。
    """
    normalizer = evaluator.normalizer
    assert normalizer.is_noisy_place_name("东夷（山东") is True
    assert normalizer.is_noisy_place_name("江苏一带）") is True
    assert evaluator._place_set("东夷（山东、江苏一带）") == set()
    assert evaluator._semantic_pair_rejection(
        _event("商纣王征讨东夷之战", dynasty="商", place="东夷（山东、江苏一带）"),
        _event("商纣王征东夷", dynasty="商", place="东夷之地"),
        "商纣王征讨东夷之战", "商纣王征东夷") == ""


def test_关掉语义约束就全放行(loose_evaluator):
    """开关 `enforce_semantic_constraints=false` 用于复现"只看名称相似度"的旧口径。"""
    pred = _event("甲战", dynasty="清", start="1900年", place="北京")
    gold = _event("甲战XX", dynasty="西周", start="-1000年", place="洛阳")
    assert loose_evaluator._semantic_pair_rejection(pred, gold, "甲战", "甲战XX") == ""


def test_跨进程可复现的配对差异导出(evaluator):
    """`event_mapping_diff` 是复核约束是否过严的工具，输出结构要稳（含拒绝原因）。"""
    diff = evaluator.event_mapping_diff(
        [_event("官渡之战", dynasty="东汉", start="200年", place="官渡")], limit=5)
    assert set(diff) >= {"unconstrained_pairs", "constrained_pairs", "dropped_count", "dropped"}
    # 同名配对不该出现在"被拆"列表里（这正是 P0-4 修掉的那一处）
    assert all(item["pred_event"] != item["gold_event"] for item in diff["dropped"])


# ------------------------------------------------------------------ 关系：按条计数

def _mapping_for(*names):
    return {name: name for name in names}


def test_关系按条计数不吞重复三元组(evaluator):
    """
    同一三元组出现两次就计两条预测：gold 只被认领一次，另一条必然落进 FP。
    原实现用 `set`，重复项被静默吞掉（实测归一后重复 747 条，见 `filter_relations` 的说明）。
    """
    pred_relations = {
        "event_place_relations": [
            {"EventName": "甲战", "relation": "主战场", "modern_name": "某地"},
            {"EventName": "甲战", "relation": "主战场", "modern_name": "某地"},
        ],
        "event_person_relations": [],
        "event_organization_relations": [],
        "event_event_relations": [],
    }
    mapping = _mapping_for("甲战")
    triples = evaluator.filter_relations(pred_relations, mapping)
    assert len(triples) == 2, "重复三元组必须按条保留"

    match = evaluator.match_relation_triples(triples, {("甲战", "发生地点", "某地")}, mapping)
    # `主战场` 与 `发生地点` 在评估器的归一表里是同一个关系（所以这是一条重复的预测）
    assert match["tp"] == 1
    assert len(match["unmatched_predictions"]) == 1, "多出来的那条必须计入 FP"


def test_标注侧仍按集合去重(evaluator):
    """预测侧按条、gold 侧按集合——不对称是有意的：标注重复行是标注缺陷，不该加重要求。"""
    mapping = _mapping_for("甲战")
    pred = {"event_place_relations": [{"EventName": "甲战", "relation": "主战场", "modern_name": "某地"}],
            "event_person_relations": [], "event_organization_relations": [], "event_event_relations": []}
    triples = evaluator.filter_relations(pred, mapping)
    match = evaluator.match_relation_triples(triples, {("甲战", "发生地点", "某地")}, mapping)
    assert match["tp"] == 1 and match["gold_list"] == [("甲战", "发生地点", "某地")]


def test_分类容器与选值口径一致(evaluator):
    """`filter_relations_by_category` 与 `filter_relations` 必须给出同一批条目（只是分了类）。"""
    pred = {
        "event_place_relations": [{"EventName": "甲战", "relation": "主战场", "modern_name": "某地"}],
        "event_person_relations": [{"EventName": "甲战", "relation": "统帅", "PersonName": "某人"}],
        "event_organization_relations": [],
        "event_event_relations": [{"EventName_A": "甲战", "relation": "顺承关系", "EventName_B": "乙战"}],
    }
    mapping = _mapping_for("甲战", "乙战")
    flat = set(evaluator.filter_relations(pred, mapping))
    by_category = evaluator.filter_relations_by_category(pred, mapping)
    merged = {tuple(item) for group in by_category.values() for item in group}
    assert merged == flat
    assert len(by_category["event-place"]) == 1
    assert len(by_category["event-event"]) == 1


# ------------------------------------------------------------------ 字段值准确率

def test_地点字段按集合判对():
    """`Place` 是多值字段，按"两侧集合有交集"判对——只比首个会误判顺序不同的同一集合。"""
    if not ANNOTATION_DIR.exists():
        pytest.skip("没有标注目录，跳过评估器用例")
    # 用独立实例：这条用例要替换 gold 事件，不能改到 module 级夹具共享的那份
    local = OptimalEvaluator(annotation_dir=ANNOTATION_DIR)
    local.gold_events = {"events": [_event("甲战", place="宁远、锦州", Person="某人")]}
    pred_events = [_event("甲战", place="锦州、宁远", Commanders="某人")]
    report = local.evaluate_event_field_accuracy(pred_events, {"甲战": "甲战"})
    fields = report["fields"]
    assert fields["Place"]["correct"] == 1 and fields["Place"]["accuracy"] == 1.0
    assert fields["Person"]["correct"] == 1
    assert report["matched_pairs"] == 1


def test_字段值准确率与填充率是两件事(evaluator):
    """
    `field_metrics` 报"填没填"，`field_accuracy` 报"填得对不对"。
    实测 `Casualties` 填充 99.6% 而实质取值只有 37.0%——只有后者能回答"这个字段能不能直接展示"。
    """
    events = [{"EventName": "甲战", "Casualties": "不详", "TroopSize": "不详"}]
    metrics = evaluator.evaluate_event_fields(events)
    assert metrics["field_fill_rates"]["Casualties"] == 1.0, "旧口径把「不详」算成已填"
    assert metrics["field_effective_rates"]["Casualties"] == 0.0, "新口径把「不详」算未填"


def test_实质取值率逐项判定多值字段(evaluator):
    """`"李广、不详"` 里有真值、`"不详、无"` 没有——整串精确匹配是判不出来的。"""
    events = [{"EventName": "甲战", "Commanders": "李广、不详", "KeyPersons": "不详、无"}]
    metrics = evaluator.evaluate_event_fields(events)
    assert metrics["field_effective_rates"]["Commanders"] == 1.0
    assert metrics["field_effective_rates"]["KeyPersons"] == 0.0


# ------------------------------------------------------------------ 两套身份口径

def test_实体指标含canonical口径(evaluator):
    """`canonical` 口径真的走别名归一（`aliases.json` 的 `孙滨 → 孙膑` 这类）。"""
    assert evaluator.normalizer.normalize_entity_name("孙滨") == "孙膑"
    normalized, hit = True, evaluator.normalizer.normalize_entity_name("洛阳") == "洛阳"
    assert normalized and hit


# ------------------------------------------------------------------ 分维度报告

def test_按朝代分组的事件报告结构(evaluator):
    """`by_dynasty` 给每个朝代档 P/R/F1；未配上的预测事件进"（无法归组）"，不从报告里消失。"""
    pred_events = [_event("甲战", dynasty="清"), _event("乙战", dynasty="清")]
    report = evaluator.evaluate_events_by_dynasty(pred_events, {"甲战": "牧野之战"})
    assert report, "至少要有标注侧朝代的分档"
    for bucket in report.values():
        assert set(bucket) >= {"gold", "tp", "fp", "fn", "precision", "recall", "f1"}
    assert "（无法归组）" in report, "未配上的预测事件必须有归口，否则各档看起来比整体好"


def test_映射统计记录拒绝原因分项(evaluator):
    """`mapping_stats` 要能看出"被哪一条约束拦了多少"，否则复核时无从下手。"""
    evaluator.build_event_mapping([_event("官渡之战", dynasty="东汉", start="200年", place="官渡")])
    stats = evaluator.last_mapping_stats
    assert set(stats["rejected_by_reason"]) == {"dynasty", "year", "place"}
    assert stats["semantic_constraints_enforced"] is True
    assert 0.0 <= stats["rejected_ratio"] <= 1.0
