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
