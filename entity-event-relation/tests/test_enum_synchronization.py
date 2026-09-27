"""枚举目录的**跨模块同步**必须真的同步。

**为什么要有这组用例。** `OrgType`/`Role`/关系名的取值散落在至少五处：抽取提示词枚举、
`war_extraction/utils/vocabulary.py`（权威表）、后端导入白名单、RAG 的 `field_map`、
前端图谱下拉。改一处不改另一处**不会报错**，只表现为"抽出来了但用户点不到"或
"某一列被静默改写"：

- 前端组织下拉原先只有 8 项、缺 `参战方`，而产物里有 207 行 `参战方`；
- `import_json_to_sqlite` 的 OrgType 白名单原先只有 6 项，产物里的
  `军事势力`（181 行）、`军队`（117 行）被静默改写成 `地方势力`；
- RAG 的地点关系集合有 20 项、提示词只有 10 项。

所以这组用例**直接读下游那份文件**，把它们与权威表逐项比对——下游改了权威表没改，
或者反过来，都在这里变红。用文本解析而不是 import：这些是前端与另一模块的文件，
按 import 引入会把测试绑死在它们的运行环境上。
"""

import ast
import re
from pathlib import Path

import pytest

from war_extraction.utils.vocabulary import (
    ALL_RELATION_TYPES,
    EVENT_EVENT_RELATION_TYPES,
    EVENT_ORGANIZATION_RELATION_TYPES,
    EVENT_PERSON_RELATION_TYPES,
    EVENT_PLACE_RELATION_TYPES,
    ORG_TYPES,
    ROLES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTITY_GRAPH_VUE = REPO_ROOT / "frontend" / "src" / "views" / "knowledge" / "graph" / "EntityGraph.vue"
FIELD_MAP_PY = REPO_ROOT / "RAG" / "data" / "snapshot" / "field_map.py"
IMPORT_SCRIPT = REPO_ROOT / "backend" / "import_json_to_sqlite.py"


def _js_string_array(source: str, group: str) -> set:
    """
    从 EntityGraph.vue 的 `GRAPH_CONFIGS` 里取出某个组的 `relTypes` 字符串集合。

    按花括号计数取组块，而不是用正则去框——最后一组的结尾没有逗号、
    而且数组本身可能跨多行，正则很容易框错（框到别的组或框不完）。
    """
    start = source.find(f"\n  {group}: {{")
    assert start != -1, f"没有在 EntityGraph.vue 里找到 {group} 组"
    depth = 0
    block_start = source.index("{", start)
    for index in range(block_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                block = source[block_start:index + 1]
                break
    else:
        raise AssertionError(f"{group} 组的括号没有闭合")
    array_match = re.search(r"relTypes:\s*\[(.*?)\]", block, re.S)
    assert array_match, f"{group} 组没有 relTypes"
    return set(re.findall(r"'([^']+)'", array_match.group(1)))


def _python_set(module_path: Path, name: str) -> set:
    """从某个 .py 文件里取出 `NAME = {...}` 的字符串集合（AST 解析，不 import）。"""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Set):
                    return {element.value for element in node.value.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)}
    raise AssertionError(f"{module_path} 里没有找到集合 {name}")


def _load_rag_field_map() -> dict:
    """
    按**文件路径**加载 RAG 的 `field_map.py` 并取它导出的名字。

    与 `_python_set` 同一出发点：不 `import data.snapshot.field_map`——那要 RAG 包在
    `sys.path` 上（两个模块各有自己的运行环境），会把本用例绑到 RAG 的运行方式上。
    这里只执行这一个文件（它没有任何 import），拿到函数对象后直接调，比纯文本解析结实。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_rag_field_map_under_test", FIELD_MAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        name: getattr(module, name)
        for name in ("_rules_for", "build_field_map", "T_ORG", "T_PLACE", "T_EVENT", "T_PERSON")
    }


# ---------------------------------------------------------------- 前端图谱下拉

@pytest.mark.parametrize(
    "group,expected",
    [
        ("event", EVENT_EVENT_RELATION_TYPES),
        ("place", EVENT_PLACE_RELATION_TYPES),
        ("organization", EVENT_ORGANIZATION_RELATION_TYPES),
        ("person", EVENT_PERSON_RELATION_TYPES),
    ],
)
def test_前端图谱下拉与权威表一致(group, expected):
    if not ENTITY_GRAPH_VUE.is_file():
        pytest.skip("前端目录不存在，跳过")
    source = ENTITY_GRAPH_VUE.read_text(encoding="utf-8")
    assert _js_string_array(source, group) == set(expected), (
        f"{group} 组的关系下拉与权威表不一致：产物里有、下拉点不到（或反之）"
    )


def test_前端组织组包含参战方():
    """这一条是整改方案 3.3 明确点名的症状：产物里 207 行 `参战方`，前端下拉里没有。"""
    if not ENTITY_GRAPH_VUE.is_file():
        pytest.skip("前端目录不存在，跳过")
    source = ENTITY_GRAPH_VUE.read_text(encoding="utf-8")
    assert "参战方" in _js_string_array(source, "organization")


# ---------------------------------------------------------------- RAG field_map

def test_RAG地点关系集合覆盖权威表():
    if not FIELD_MAP_PY.is_file():
        pytest.skip("RAG 目录不存在，跳过")
    assert set(EVENT_PLACE_RELATION_TYPES) <= _python_set(FIELD_MAP_PY, "_PLACE_RELS"), (
        "RAG 的 _PLACE_RELS 少了权威表里的关系名 → 该关系在问答里会被过滤掉"
    )


def test_RAG组织关系集合覆盖权威表():
    if not FIELD_MAP_PY.is_file():
        pytest.skip("RAG 目录不存在，跳过")
    covered = (
        _python_set(FIELD_MAP_PY, "_ORG_AGGRESSOR_RELS")
        | _python_set(FIELD_MAP_PY, "_ORG_DEFENDER_RELS")
        | _python_set(FIELD_MAP_PY, "_ORG_OTHER_RELS")
    )
    assert set(EVENT_ORGANIZATION_RELATION_TYPES) <= covered


def test_RAG人物关系集合覆盖权威表():
    if not FIELD_MAP_PY.is_file():
        pytest.skip("RAG 目录不存在，跳过")
    covered = _python_set(FIELD_MAP_PY, "_PERSON_RELS") | _python_set(FIELD_MAP_PY, "_PERSON_SIDE_ROLE_RELS")
    assert set(EVENT_PERSON_RELATION_TYPES) <= covered


def test_RAG事件事件关系集合覆盖规范类型():
    if not FIELD_MAP_PY.is_file():
        pytest.skip("RAG 目录不存在，跳过")
    assert set(EVENT_EVENT_RELATION_TYPES) <= _python_set(FIELD_MAP_PY, "_EVENT_EVENT_RELS")


def test_RAG丢弃串类关系指挥所而不是错配到组织字段():
    """
    `指挥所` 是**地点**关系名，模型曾把它写进事件-组织关系（实测 1 条，
    已登记为 `KNOWN_ENUM_EXCEPTIONS` 且决定不加进组织关系枚举）。所以 RAG 的组织侧
    **必须拒收**它：`(指挥所, 组织)` 不能归进 organizations 字段去比——
    归进去等于承认"组织是某场战争的指挥所"这个错语义，还会在 F05 里报出假的字段冲突。

    这一条此前没有任何断言：`test_RAG组织关系集合覆盖权威表` 只查了"权威表 ⊆ RAG 集合"
    这**一个方向**，RAG 侧多收或被误配都测不出来。
    """
    if not FIELD_MAP_PY.is_file():
        pytest.skip("RAG 目录不存在，跳过")
    field_map = _load_rag_field_map()

    assert field_map["_rules_for"]("指挥所", field_map["T_ORG"]) is None, "组织侧不该接收 `指挥所`"
    place_rule = field_map["_rules_for"]("指挥所", field_map["T_PLACE"])
    assert place_rule is not None, "它的正当归属是地点侧"
    assert place_rule["card_field"] == "place"

    # 映射表里仍要有一行（不能让 F05 因为缺行崩），但必须是"不参与冲突判定"
    rows = {row["relation"]: row
            for row in field_map["build_field_map"]({("指挥所", field_map["T_ORG"])})["mapping"]}
    assert rows["指挥所"]["group"] == "unknown"
    assert rows["指挥所"]["method"] == "none"
    assert rows["指挥所"]["card_field"] is None


# ---------------------------------------------------------------- 后端导入白名单

def test_后端组织类型白名单取自权威表():
    """导入脚本不许再硬编码一份 6 值白名单：那会把 305 行组织静默改写成"地方势力"。"""
    if not IMPORT_SCRIPT.is_file():
        pytest.skip("backend 目录不存在，跳过")
    source = IMPORT_SCRIPT.read_text(encoding="utf-8")
    assert "ORG_TYPES" in source, "导入脚本没有引用权威表，可能又抄了一份硬编码白名单"
    assert 'valid_types = set(ORG_TYPES)' in source


def test_后端角色归一取自权威表():
    """
    在线侧不再自带角色白名单：它的 `VALID_ROLES` 生产代码零引用，已删除，
    角色归一改为共用 `war_extraction/utils/vocabulary.py` 的权威表（决策 10）。
    """
    source = (REPO_ROOT / "backend" / "llm_pipeline.py")
    if not source.is_file():
        pytest.skip("backend 目录不存在，跳过")
    text = source.read_text(encoding="utf-8")
    assert "from war_extraction.utils.vocabulary import normalize_role" in text
    assert "VALID_ROLES = " not in text, "又长出一份独立的角色白名单了"


# ---------------------------------------------------------------- 朝代归一表

DYNASTY_DATA_PY = REPO_ROOT / "backend" / "dynasty_data.py"
RAG_NORMALIZE_PY = REPO_ROOT / "RAG" / "data" / "snapshot" / "normalize.py"


def _load_backend_dynasty_data():
    """按文件路径加载 backend 的 `dynasty_data.py`（它只 import 权威表，无其他依赖）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_backend_dynasty_data_under_test", DYNASTY_DATA_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_朝代归一表只有一处权威实现():
    """
    决策 10.1 第 7 项说"归一交给一张 backend 与 RAG 共用的映射表"，而此前两边各一套：
    backend 自带 `DYNASTY_CORRECTIONS`、RAG 的 `normalize.py` 只有一条"上古"规则，
    于是同一个朝代名在两条链路里是两个值。表已定位置在权威表，这里钉住三件事：

    1. 权威表在 `war_extraction/utils/vocabulary.py`，且导出了 `normalize_dynasty`；
    2. backend **真的从权威表取**，不是又抄一份（逐项比对值，不只看 import）；
    3. RAG 侧本轮只落"位置 + 接入路径"的决策（它没有 war_extraction 依赖，
       接入方式待重建快照时决定）——这条防止下一个人以为"已经统一了"。
    """
    from war_extraction.utils import vocabulary

    assert "DYNASTY_ALIASES" in vocabulary.__all__
    assert "normalize_dynasty" in vocabulary.__all__
    # 归一结果不参与校验：`DYNASTY_REFERENCE` 只是参考分布
    assert vocabulary.normalize_dynasty("清朝") == ("清", True)
    assert vocabulary.normalize_dynasty("蒙古") == ("蒙古", False), "认不出来要保留原值，不猜"

    if not DYNASTY_DATA_PY.is_file():
        pytest.skip("backend 目录不存在，跳过")
    source = DYNASTY_DATA_PY.read_text(encoding="utf-8")
    assert "from war_extraction.utils.vocabulary import DYNASTY_ALIASES" in source
    assert "DYNASTY_CORRECTIONS = dict(DYNASTY_ALIASES)" in source, (
        "backend 又长出一份抄写的朝代归一表——改一处漏一处就是从这里开始的"
    )
    backend = _load_backend_dynasty_data()
    assert backend.DYNASTY_CORRECTIONS == dict(vocabulary.DYNASTY_ALIASES), "两边内容必须逐项相等"

    if RAG_NORMALIZE_PY.is_file():
        rag_source = RAG_NORMALIZE_PY.read_text(encoding="utf-8")
        assert "war_extraction/utils/vocabulary.py" in rag_source, (
            "RAG 的 normalize.py 要写明权威表在哪、以及本轮不改它的理由"
        )
        assert "重建快照" in rag_source


# ---------------------------------------------------------------- 表本身的自洽

def test_关系类别集合互不重叠到自相矛盾():
    """四类关系名允许有交集（`发起方` 在组织与人物两侧都出现过），但不该出现空集合。"""
    for name, group in (
        ("event-event", EVENT_EVENT_RELATION_TYPES),
        ("event-place", EVENT_PLACE_RELATION_TYPES),
        ("event-organization", EVENT_ORGANIZATION_RELATION_TYPES),
        ("event-person", EVENT_PERSON_RELATION_TYPES),
    ):
        assert group, f"{name} 为空"
    assert ALL_RELATION_TYPES >= EVENT_EVENT_RELATION_TYPES | EVENT_PLACE_RELATION_TYPES


def test_枚举表覆盖产物里的实际取值():
    """权威表要覆盖"已经在用的取值"，否则 schema 校验层会把它们挪出发布子集。"""
    # 这五个是整改方案 3.3 点名的"产物里有、白名单里没有"
    assert {"军事势力", "军队", "革命组织", "方国"} <= set(ORG_TYPES)
    assert {"关键人物", "监军", "首领", "领袖"} <= set(ROLES)


#: RAG 的标准词典（治理后的取值表）。与 `field_map.py` 的"接收集合"不是一回事：
#: 那个决定"哪些关系名能进哪个字段"，这个决定"哪些事件类型是标准类型"。
RAG_DICTS = REPO_ROOT / "RAG" / "data" / "snapshot" / "20260915_v1" / "dicts.json"
DEFAULT_PRED = Path(__file__).resolve().parents[1] / "output" / "中国历代战争简史" / "9_final_all.json"


def test_产物事件类型都落在RAG标准词典里():
    """
    **E6 的预防性守卫**（阶段三）：产物里出现的 `EventType` 必须都能在 RAG 的标准词典里
    找到，否则那一类事件在问答与筛选里"点不到"——而这类漂移**不报错**，只表现为数字变了。

    为什么现在加：实测产物用了 **27 个**非空取值，与 RAG 词典（`event_type_standard`，27 项）
    **逐项相同**，权威表则多出 3 个尚未出现的取值（`党争军事化`/`军事同盟`/`军事改革`）。
    也就是说"对齐"这件事当前成立，风险在重跑之后——那时这 3 个若真的出现，用例会变红并提示
    同步 RAG 词典与 `RAG/docs/data-contract.md` 的"27 类"口径（方向已定：以权威表为准改词典）。

    产物与 RAG 快照都不入库，缺任一就跳过（CI 里跑不到，本地跑得到）。
    """
    if not (RAG_DICTS.is_file() and DEFAULT_PRED.is_file()):
        pytest.skip("缺少 RAG 词典或产物，跳过")
    import json

    standard = set(json.loads(RAG_DICTS.read_text(encoding="utf-8"))["event_type_standard"])
    payload = json.loads(DEFAULT_PRED.read_text(encoding="utf-8"))
    block = payload.get("events") or {}
    events = block if isinstance(block, list) else (block.get("events") or [])
    used = {(event.get("EventType") or "").strip() for event in events
            if (event.get("EventType") or "").strip()}

    # 已登记的例外要扣掉，**与门禁扣减共用同一份表**（`artifact_health_check.KNOWN_ENUM_EXCEPTIONS`）——
    # 否则会出现"门禁认例外、守卫不认"的两套口径。当前登记的 `交战` 是模型把 `Action` 的值
    # 抄进了 `EventType`（那两条的 Action 也都是「交战」），判定（2026-09-27）是不加进枚举与词典：
    # 它是动作词，不是事件类型。
    from tools.artifact_health_check import KNOWN_ENUM_EXCEPTIONS

    registered = {key.partition("/")[2] for key in KNOWN_ENUM_EXCEPTIONS
                  if key.startswith("EventType/")}
    missing = sorted(used - standard - registered)
    assert not missing, (
        f"产物里有 {len(missing)} 个 EventType 不在 RAG 标准词典里：{missing}。\n"
        "该决定「扩词典」还是「改数据」——方向已定：以权威表"
        "（`war_extraction/utils/vocabulary.py`）为准扩 RAG 词典，"
        "并同步 `RAG/docs/data-contract.md` 的「27 类」口径。"
    )
