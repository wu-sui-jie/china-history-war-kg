"""派生关系必须绑定**具体证据**与**具体实体**。

**为什么要有这组用例。** 从事件字段反向派生关系这条链路上，原实现有三类"无条件扩张"，
后果是"有关系的事件平均 17.5 条关系、极值 188 条"，而且 62%~72% 的关系共用同一条
`source_text` 当证据：

1. `Place` 字段的**第 0 个地点一律"主战场"**（无需任何证据）；
2. 整段文本里出现"议和"就给该事件的**所有**组织挂上"议和方"；
3. 人名里含"王/帝/君/公/侯"就判"君主"、含"谋"判"谋士"、含"使"判"使者"
   ——`王翦` 会被判成国君。

现在的口径：线索词必须与实体名**同句**出现；名称只按**称号结尾**判君主/可汗；
判不出来就不生成该条关系（宁缺勿错）。
"""

import pytest

from war_extraction.extractors.relation_extractor import RelationExtractor
from war_extraction.models import Event


@pytest.fixture(scope="module")
def extractor():
    return RelationExtractor(None)


def _event(**kwargs) -> Event:
    return Event(**kwargs)


# ---------------------------------------------------------------- 句级证据

def test_证据取实体名所在的那一句(extractor):
    event = _event(EventName="长平之战", Place="长平",
                   source_text="秦攻韩，取上党。赵括率军至长平。秦将白起围赵军。")
    assert extractor._entity_sentence("长平", event) == "赵括率军至长平。"
    assert extractor._entity_sentence("上党", event) == "秦攻韩，取上党。"


def test_实体名不在原文片段里时返回空串(extractor):
    event = _event(EventName="长平之战", Place="长平", source_text="赵军大败。")
    assert extractor._entity_sentence("上党", event) == ""


# ---------------------------------------------------------------- 地点关系

def test_主战场要求同句有交战线索(extractor):
    event = _event(EventName="长平之战", Place="长平", source_text="秦赵战于长平，赵军大败。")
    assert "主战场" in extractor._derive_place_relation_types("长平", "秦赵战于长平，赵军大败。", True)


def test_无交战线索的地点不再无条件成为主战场(extractor):
    """修掉的那一处：`Place` 第 0 个地点原先一律"主战场"，没有任何证据要求。"""
    sentence = "赵括自幼熟读兵书"
    assert extractor._derive_place_relation_types("长平", sentence, True) == []


def test_只有第一个有交战线索的地点算主战场(extractor):
    first = extractor._derive_place_relation_types("长平", "秦赵战于长平。", True)
    second = extractor._derive_place_relation_types("上党", "秦攻上党，取之。", False)
    assert "主战场" in first and "次要战场" not in first
    assert "次要战场" in second and "主战场" not in second


def test_议和地点要求地名与议和同句(extractor):
    assert "议和地点" in extractor._derive_place_relation_types(
        "渑池", "两国于渑池会盟，约以和亲。", True)
    # 整段别处出现"议和"不算：这是原先"给所有地点挂议和地点"的修法
    assert "议和地点" not in extractor._derive_place_relation_types("荥阳", "驻军荥阳。", True)


# ---------------------------------------------------------------- 组织关系

def _org_relations(extractor, source_text: str, aggressor: str = "", defender: str = "",
                   allies: str = "") -> list:
    """走派生入口：句子由 `_entity_sentence` 从 source_text 里取，才测得到"同句"这件事。"""
    event = _event(EventName="某战", Place="某地", Aggressor=aggressor or None,
                   Defender=defender or None, Allies=allies or None, source_text=source_text)
    _place, org_rels, _person = extractor._derive_event_entity_relations_from_events(
        [event], "", f"{aggressor}、{defender}、{allies}", "")
    return org_rels


def test_议和方只挂给同句出现的组织(extractor):
    """修掉的那一处：整段出现"议和"原先会给该事件的**所有**组织都挂上议和方。"""
    relations = _org_relations(extractor, "秦军攻赵。赵军遣使议和。",
                               aggressor="秦军", defender="赵军")
    pairs = {(rel.OrgName, rel.relation) for rel in relations}
    assert ("赵军", "议和方") in pairs
    assert ("秦军", "议和方") not in pairs, "秦军那一句里没有议和，不该挂议和方"


def test_投降方只挂给同句出现的组织(extractor):
    relations = _org_relations(extractor, "齐军降于秦。燕军闻讯而退。",
                               defender="齐军", allies="燕军")
    pairs = {(rel.OrgName, rel.relation) for rel in relations}
    assert ("齐军", "投降方") in pairs
    assert ("燕军", "投降方") not in pairs


# ---------------------------------------------------------------- 人物关系

def test_谋士不再由名称里的字推断(extractor):
    """名字里含"谋"不等于谋士：原实现用 `"谋" in person_name` 直接判。"""
    assert "谋士" not in extractor._derive_person_relation_types("参与者", "张谋", "")
    assert "谋士" in extractor._derive_person_relation_types("参与者", "张谋", "张谋献策。")


def test_君主按称号结尾判定而不是按名称里的字(extractor):
    """`王翦` 是名将、不是国君；`周文王`/`汉武帝` 才是。"""
    assert "君主" not in extractor._derive_person_relation_types("统帅", "王翦", "")
    assert "君主" in extractor._derive_person_relation_types("统帅", "周文王", "")
    assert "君主" in extractor._derive_person_relation_types("统帅", "汉武帝", "")


def test_可汗按称号结尾判定(extractor):
    assert "可汗" in extractor._derive_person_relation_types("统帅", "木华黎可汗", "")
    assert "可汗" not in extractor._derive_person_relation_types("统帅", "木华黎", "")


def test_使者不再由名称里的字推断(extractor):
    assert "使者" not in extractor._derive_person_relation_types("参与者", "李使", "")
    assert "使者" in extractor._derive_person_relation_types("参与者", "李使", "李使出使秦国。")


# ---------------------------------------------------------------- 端到端量级

def test_派生关系带句级证据而不是整段文本(extractor):
    source = "秦攻韩，取上党。赵括率军至长平，与秦军战。秦将白起围赵军，赵军降。"
    events = [_event(EventName="长平之战", Place="长平、上党", Aggressor="秦军", Defender="赵军",
                     Commanders="白起、赵括", source_text=source)]
    place_rels, org_rels, person_rels = extractor._derive_event_entity_relations_from_events(
        events, "长平、上党", "秦军、赵军", "白起、赵括")

    relations = place_rels + org_rels + person_rels
    assert relations, "应该派生出关系"
    shared = [rel for rel in relations if (rel.evidence or "").strip() == source.strip()]
    assert len(shared) < len(relations), "不该所有关系共用同一段整文本当证据"
    assert any("长平" in (rel.evidence or "") for rel in place_rels)


def test_派生关系量级回到合理范围(extractor):
    """同一份小样本上，派生关系数不该爆到"每个实体每种关系都来一遍"。"""
    events = [_event(EventName="长平之战", Place="长平", Aggressor="秦军", Defender="赵军",
                     Commanders="白起", KeyPersons="赵括",
                     source_text="秦赵战于长平，白起率秦军大败赵军。")]
    place_rels, org_rels, person_rels = extractor._derive_event_entity_relations_from_events(
        events, "长平", "秦军、赵军", "白起、赵括")
    assert len(place_rels) + len(org_rels) + len(person_rels) <= 8, "派生关系数明显偏多，检查是否又有无条件扩张"


def test_允许名单为空时不限制实体(extractor):
    """`main.enrich_relations_from_events` 之外的空列表语义：不做限制（原行为）。"""
    events = [_event(EventName="某战", Place="某地", Aggressor="某军",
                     source_text="某军战于某地。")]
    place_rels, org_rels, _person_rels = extractor._derive_event_entity_relations_from_events(events)
    assert place_rels and org_rels


# ---------------------------------------------------------------- 指挥官字段派生的关系名

def test_指挥官字段派生将领而不是统帅(extractor):
    """
    `Commanders` 是**双方主要指挥官列表**，关系枚举里 `统帅` 是"最高指挥官"（单数语义）——
    把列表里的每个人都派生成 `统帅` 与两边的定义都冲突。

    阶段三 E2 依据 B 组抽样判定改成 `将领`：产物里 3016 条 `统帅` 有 2767 条（91.7%）来自这条规则，
    而抽样里规则派生的 `统帅` 例 **10/10 被判错**（"此役统帅为刘曜，石生并非统帅"、
    "不设元帅，郭子仪为九节度使之一"、"张孝忠系从征节度使，统帅应为朱滔"）——
    判据给出的正确说法都是"将领"。
    """
    events = [_event(EventName="长平之战", Commanders="白起、赵括",
                     source_text="秦将白起围赵军，赵括率军出战。")]
    _place, _org, person_rels = extractor._derive_event_entity_relations_from_events(
        events, "", "", "白起、赵括")

    relations = {(rel.PersonName, rel.relation) for rel in person_rels}
    assert ("白起", "将领") in relations
    assert ("赵括", "将领") in relations
    assert not [name for name, relation in relations if relation == "统帅"], \
        "`Commanders` 不该派生 `统帅`（`统帅` 只留给原文明确写出的最高指挥者）"


def test_同一个人不再同时是统帅和将领(extractor):
    """
    旧实现下同一个（事件, 人物）会同时挂 `统帅`（字段映射）与 `将领`（"率兵/统兵"线索），
    实测旧产物里这类自相矛盾有 **385 条**；改名后两条来源同名，收敛成一条。
    """
    events = [_event(EventName="长平之战", Commanders="白起",
                     source_text="秦将白起统兵围赵军。")]
    _place, _org, person_rels = extractor._derive_event_entity_relations_from_events(
        events, "", "", "白起")
    relations = [rel.relation for rel in person_rels if rel.PersonName == "白起"]
    assert len(relations) == len(set(relations)), f"同一人物挂了重复/冲突的关系名: {relations}"
    assert "统帅" not in relations
