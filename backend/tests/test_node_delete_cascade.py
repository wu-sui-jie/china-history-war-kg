"""实体删除的关系级联与外键约束（审查报告 P1-2）。

修的是两件事：
1. `PRAGMA foreign_keys` 默认是 **关** 的，models.py 里声明的 ForeignKey 因此只是一句
   注释——删掉一个已被关系引用的事件，关系表会留下指向不存在 id 的孤儿行；
2. 即便外键打开，存量库的关系表 DDL 里没有 ON DELETE CASCADE（`create_all()` 不会改建好
   的表），所以级联删除必须由 `DbUtil.delete_node` 在业务层显式完成。

这两条各自独立，任何一条单独修都不够：只开外键 → 删除直接报 FOREIGN KEY constraint failed
（从"留脏数据"变成"删不掉"）；只做业务层删除 → 绕过接口的写入仍能造出孤儿行。
因此下面既有"业务层级联"的用例，也有"数据库层真的拦得住"的用例。
"""

import pytest

from db_utils import DbUtil
from models import (
    Event,
    EventEventRelation,
    EventOrganizationRel,
    EventPersonRelation,
    EventPlaceRelation,
    Organization,
    Person,
    Place,
    db,
)

BUSINESS_MODELS = (
    EventEventRelation,
    EventPlaceRelation,
    EventPersonRelation,
    EventOrganizationRel,
    Event,
    Place,
    Organization,
    Person,
)


@pytest.fixture(autouse=True)
def _clean_business_tables(_app_context):
    """知识表在用例前后都清空：级联用例断言的是"计数归零"，不能被上一条用例的数据污染。

    删除顺序与生产同口径（先关系后实体）：外键已打开，先删实体行会报约束错误。
    """
    def _purge():
        for model in BUSINESS_MODELS:
            model.query.delete(synchronize_session=False)
        db.session.commit()

    _purge()
    yield
    _purge()


def _make_graph():
    """造一个"事件被四种关系同时引用"的最小图，返回各实体。"""
    event_a = Event(name="测试-甲事件")
    event_b = Event(name="测试-乙事件")
    place = Place(name="测试-地点")
    person = Person(name="测试-人物")
    org = Organization(name="测试-组织")
    db.session.add_all([event_a, event_b, place, person, org])
    db.session.commit()

    db.session.add_all([
        EventEventRelation(event_a_id=event_a.id, event_b_id=event_b.id,
                           event_a_name=event_a.name, event_b_name=event_b.name,
                           relation_type="顺承"),
        EventPlaceRelation(event_id=event_a.id, place_id=place.id,
                           event_name=event_a.name, place_name=place.name,
                           relation_type="发生地"),
        EventPersonRelation(event_id=event_a.id, person_id=person.id,
                            event_name=event_a.name, person_name=person.name,
                            relation_type="参与"),
        EventOrganizationRel(event_id=event_a.id, org_id=org.id,
                             event_name=event_a.name, org_name=org.name,
                             relation_type="参战"),
    ])
    db.session.commit()
    return event_a, event_b, place, person, org


def test_外键约束默认已打开(_app_context):
    """连接钩子必须逐连接打开外键；关着的话下面的数据库层用例会假通过。"""
    from sqlalchemy import text

    assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_删除事件会连带删除四类关系(_app_context):
    event_a, _event_b, _place, _person, _org = _make_graph()

    result = DbUtil.delete_node("Event", event_a.id)

    assert result["code"] == 200, result
    # 事件-事件关系里本事件出现在 a 端一次 → 合计 4 条关系被级联删除
    assert result["data"]["removed_relations"] == 4
    assert Event.query.filter_by(id=event_a.id).first() is None
    for model in (EventEventRelation, EventPlaceRelation,
                  EventPersonRelation, EventOrganizationRel):
        assert model.query.count() == 0, f"{model.__name__} 残留了孤儿关系"


def test_删除地点人物组织各自连带删除自己的关系(_app_context):
    event_a, _event_b, place, person, org = _make_graph()

    assert DbUtil.delete_node("Place", place.id)["data"]["removed_relations"] == 1
    assert DbUtil.delete_node("Person", person.id)["data"]["removed_relations"] == 1
    assert DbUtil.delete_node("Organization", org.id)["data"]["removed_relations"] == 1

    assert EventPlaceRelation.query.count() == 0
    assert EventPersonRelation.query.count() == 0
    assert EventOrganizationRel.query.count() == 0
    # 事件本身没有被牵连删除，此刻它已不再被任何关系引用
    assert Event.query.filter_by(id=event_a.id).first() is not None


def test_删除事件不影响指向其他事件的关系(_app_context):
    """级联只删"引用被删节点"的关系，不是清空整张关系表。"""
    event_a, event_b, place, _person, _org = _make_graph()
    # 再补一条"只涉及乙事件"的关系：删除甲事件后它必须还在
    other_place = Place(name="测试-另一地点")
    db.session.add(other_place)
    db.session.commit()
    db.session.add(EventPlaceRelation(event_id=event_b.id, place_id=other_place.id,
                                      event_name=event_b.name, place_name=other_place.name,
                                      relation_type="发生地"))
    db.session.commit()

    assert DbUtil.delete_node("Event", event_a.id)["code"] == 200

    assert EventPlaceRelation.query.count() == 1
    leftover = EventPlaceRelation.query.first()
    assert leftover.event_id == event_b.id
    assert leftover.place_id == other_place.id
    assert place.id is not None       # 甲事件那条关系已删，地点表本身不受影响


def test_数据库层拦得住指向不存在实体的关系(_app_context):
    """数据库级防线：绕过 DbUtil 直接插入孤儿关系行必须失败。

    业务层删除只覆盖管理台这条路；导入脚本、人工 SQL、以后新增的批处理都可能绕过它。
    外键关闭时下面这条会静默写入成功——所以它同时也是"外键真的开着"的证据。
    """
    from sqlalchemy.exc import IntegrityError

    orphan = EventPlaceRelation(event_id=999999, place_id=999999,
                                relation_type="发生地")
    db.session.add(orphan)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
