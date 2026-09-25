"""数据重导不得动账号（审查报告 P0-1）。

修复前 `JsonToSqliteImporter.run()` 调 `db.drop_all()` + `create_all()`，而 UserInfo 与
知识表共用同一个 SQLAlchemy metadata——于是"换一份抽取数据集重导"会顺手删光所有账号、
口令哈希与角色，管理员账号消失后只能重新注册 viewer 再手工改 SQLite 才能恢复。

这里钉三件事：
1. `run()` 不再调用 `drop_all`（用打桩把"被调用"变成失败，而不是靠读源码）；
2. 导入前后账号数量与角色**逐条相同**；
3. 真的执行到了导入（实体被写进去），否则上面两条会因为"什么都没干"而假通过。

用例直接跑完整 `run()`，不是只测其中某个函数——P0 级事故出在 `run()` 的编排上，
只测 `clear_migration_tables()` 漏得掉。
"""

import json

import pytest

from models import Event, Organization, Person, Place, UserInfo, db


@pytest.fixture()
def importer(tmp_path, monkeypatch):
    """构造一个喂给临时 JSON 的导入器，并把会写仓库目录的两个副作用切掉。"""
    import import_json_to_sqlite as mod

    payload = {
        "entities": {
            "places": [{"geo_name": "测试-地点甲", "modern_name": "今测试市"}],
            "organizations": [{"OrgName": "测试-组织甲", "OrgType": "国家"}],
            "persons": [{"PersonName": "测试-人物甲"}],
        },
        "events": [{"EventName": "测试-事件甲", "EventType": "战争"}],
        "relations": {
            "event_event_relations": [],
            "event_place_relations": [
                {"EventName": "测试-事件甲", "geo_name": "测试-地点甲", "relation": "发生地"}
            ],
            "event_person_relations": [
                {"EventName": "测试-事件甲", "PersonName": "测试-人物甲", "relation": "参与"}
            ],
            "event_organization_relations": [
                {"EventName": "测试-事件甲", "OrgName": "测试-组织甲", "relation": "参战"}
            ],
        },
    }

    from pathlib import Path

    path = Path(tmp_path) / "final.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    instance = mod.JsonToSqliteImporter(source_path=path)
    # 这两个副作用会往 backend/data/ 写快照：与本用例的断言无关，切掉
    monkeypatch.setattr(instance, "_save_dataset_meta", lambda: None)
    monkeypatch.setattr(instance, "_save_legacy_processed_snapshot", lambda: None)
    return instance


def test_导入不再调用_drop_all(importer, monkeypatch):
    """把 drop_all 换成"一被调用就失败"：它回来了就说明 P0-1 又复发。"""
    def _forbidden():
        pytest.fail("导入脚本又调用了 db.drop_all()——这会连带删除 UserInfo（P0-1）")

    monkeypatch.setattr(db, "drop_all", _forbidden)

    importer.run(confirm=True)

    assert Event.query.filter_by(name="测试-事件甲").first() is not None


def test_导入前后账号与角色逐条不变(importer):
    """建三个不同角色的账号，跑一次完整导入，逐条比对账号与角色。"""
    accounts = [("留存-admin", "admin"), ("留存-editor", "editor"), ("留存-viewer", "viewer")]
    for account, role in accounts:
        db.session.add(UserInfo(account=account, name=account, password="x", role=role))
    db.session.commit()

    before = {(u.account, u.role) for u in UserInfo.query.all()}
    assert before == set(accounts)

    importer.run(confirm=True)

    after = {(u.account, u.role) for u in UserInfo.query.all()}
    assert after == before, "导入改动了账号或角色"


def test_不带_yes_时拒绝执行且不改动任何数据(importer):
    db.session.add(UserInfo(account="留存-admin", name="留存-admin", password="x", role="admin"))
    db.session.commit()

    with pytest.raises(SystemExit) as excinfo:
        importer.run(confirm=False)

    # 提示里要说清"账号不受影响"，否则操作者会以为又要重建整库
    assert "UserInfo" in str(excinfo.value)
    assert "drop_all" not in str(excinfo.value)
    assert UserInfo.query.count() == 1


def test_导入会清掉上一批知识数据(importer):
    """重导的语义仍然是"替换知识数据"，只是不再碰账号——旧数据必须被清掉。"""
    db.session.add(Event(name="上一批-事件"))
    db.session.commit()

    importer.run(confirm=True)

    assert Event.query.filter_by(name="上一批-事件").first() is None
    assert Event.query.filter_by(name="测试-事件甲").first() is not None
    assert Place.query.filter_by(name="测试-地点甲").first() is not None
    assert Person.query.filter_by(name="测试-人物甲").first() is not None
    assert Organization.query.filter_by(name="测试-组织甲").first() is not None
