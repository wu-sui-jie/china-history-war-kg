"""节点写路径的数据完整性。

## 编辑节点会把图谱的 name 写没，且属性全部静默丢弃

两个根因叠在同一个接口上，都会**静默**损坏图谱数据：

1. **name 取错字段**：`update_node` 只读 `data.get("name")`，而前端从来不发它——
   前端按各实体的字段名发 `EventName` / `PersonName` / `OrgName` / `geo_name`。
   于是 new_name 恒为 None，一路作为 `job.node_name` 传到
   `SET n.name = $name`，把图谱属性写成 null（等价于删除）。
   SQLite 那边因为归一化循环会把名字救回来，所以**只有图谱坏掉、主存储看不出来**。
2. **属性快照键名错**：快照用的是 API 键名，而重放路径 `neo4j_props()` 按
   **SQLite 列名**取值 → 全部取到 None 再被 `if v is not None` 过滤掉，
   属性一个都同步不过去，函数却返回 `sync_status: success`。

## 库维护的列不该接受外部指定

`id` / `created_at` / `neo4j_id` 都是真实的列，所以"只按列名过滤"挡不住它们：
editor 能指定主键（污染自增序列，并与 `graph_key = "Type:id"` 的稳定键语义冲突——
id 复用后旧的 pending 任务可能指向新节点），也能伪造入库时间影响质检报表。

## 这组用例怎么测

走**真接口**（`client.post("/update_node", ...)`）而不是直接调 DbUtil：
缺陷就发生在"路由传了什么给 DbUtil"这一段上，绕过它测不到。
另外用 conftest 里那个假的 py2neo（Graph.run 返回空）保证不连真图数据库——
图谱侧的写入由 `job.properties_json` + `neo4j_props()` 的往返断言覆盖。
"""

from __future__ import annotations

import json

import pytest


def _job_for(node_id: int):
    """取这个节点最新的一条 outbox 任务。

    按 node_id 过滤而不是取"最后一条"：conftest 不清 outbox 表，
    取全局最后一条会串到别的用例留下的任务上（首次运行就踩到了）。
    """
    from models import Neo4jSyncJob

    return (Neo4jSyncJob.query.filter_by(node_id=node_id)
            .order_by(Neo4jSyncJob.id.desc()).first())


def test_编辑节点不会把图谱的_name_写没(client, make_user, auth):
    """根因 a：前端发的是 EventName，不是 name。"""
    from models import Event, db

    event = Event(name="长平之战", dynasty="战国")
    db.session.add(event)
    db.session.commit()

    editor = make_user("editor-1", "editor")
    response = client.post("/update_node", json={
        "type": "Event", "id": event.id,
        "EventName": "长平之战（改）", "Dynasty": "秦",
    }, headers=auth(editor))

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()["data"]
    assert payload["name"] == "长平之战（改）", "名称必须从前端实际发送的字段里取"
    assert json.loads(_job_for(event.id).properties_json)["name"] == "长平之战（改）"

    db.session.expire_all()
    assert db.session.get(Event, event.id).name == "长平之战（改）"


def test_缺少名称时返回_400_而不是把图谱写坏(client, make_user, auth):
    """拿不到有效名称时宁可拒绝：写坏了要靠重导或手工补。"""
    from models import Event, db

    event = Event(name="赤壁之战", dynasty="东汉")
    db.session.add(event)
    db.session.commit()
    editor = make_user("editor-1", "editor")

    response = client.post("/update_node", json={"type": "Event", "id": event.id},
                           headers=auth(editor))

    assert response.status_code == 400
    assert "名称" in response.get_json()["msg"]
    db.session.expire_all()
    assert db.session.get(Event, event.id).name == "赤壁之战", "被拒绝的请求不该改动数据"
    assert _job_for(event.id) is None, "被拒绝的请求不该留下 outbox 任务"


def test_属性快照的键名能被重放路径认出来(client, make_user, auth):
    """根因 b：快照必须用 SQLite 列名，`neo4j_props()` 才取得到值。

    这是最关键的一条——它同时钉住了"属性确实会同步过去"：
    快照若用 API 键名（如 `{"name": ..., "Dynasty": "秦"}`），`neo4j_props()` 按列名
    取值会全部落空，重放等于什么都没写，而接口回的是 sync_status: success。
    """
    from models import Event, db
    from node_property_mapping import COLUMN_TO_NEO4J, neo4j_props

    event = Event(name="官渡之战", dynasty="东汉", result="曹操胜")
    db.session.add(event)
    db.session.commit()
    editor = make_user("editor-1", "editor")

    client.post("/update_node", json={
        "type": "Event", "id": event.id,
        "EventName": "官渡之战", "Dynasty": "曹魏", "Result": "曹操大胜",
    }, headers=auth(editor))

    snapshot = json.loads(_job_for(event.id).properties_json)
    assert "dynasty" in snapshot, f"快照键名应是列名：{sorted(snapshot)}"
    assert "Dynasty" not in snapshot

    props = neo4j_props("Event", snapshot)
    # 断言用映射表推出 Neo4j 侧键名，而不是硬编码：口径变了用例要跟着变，
    # 但"值必须能取到"这件事不变。
    dynasty_key = COLUMN_TO_NEO4J["Event"]["dynasty"]
    result_key = COLUMN_TO_NEO4J["Event"]["result"]
    assert props.get(dynasty_key) == "曹魏", f"重放路径取不到朝代：{props}"
    assert props.get(result_key) == "曹操大胜", f"重放路径取不到结果：{props}"


def test_不接受外部指定的主键与入库时间(client, make_user, auth):
    """`id` / `created_at` / `neo4j_id` 由库维护，请求体里塞了也要被丢掉。"""
    from models import Event, db

    editor = make_user("editor-1", "editor")
    response = client.post("/create_node", json={
        "type": "Event", "EventName": "淝水之战",
        "id": 99999, "neo4j_id": 4242, "created_at": "1970-01-01 00:00:00",
    }, headers=auth(editor))

    assert response.status_code == 200, response.get_json()
    created_id = response.get_json()["data"]["id"]
    assert created_id != 99999, "请求体不能指定主键（会污染自增序列并让 graph_key 语义错位）"

    created = db.session.get(Event, created_id)
    assert created.neo4j_id != 4242, "图谱标识由同步流程回写，不接受外部指定"
    assert str(created.created_at) != "1970-01-01 00:00:00", "入库时间不能被伪造"


def test_更新时同样丢弃这些列(client, make_user, auth):
    from models import Event, db

    event = Event(name="牧野之战", dynasty="商")
    db.session.add(event)
    db.session.commit()
    original_created = event.created_at
    editor = make_user("editor-1", "editor")

    client.post("/update_node", json={
        "type": "Event", "id": event.id, "EventName": "牧野之战（改）",
        "created_at": "1970-01-01 00:00:00", "neo4j_id": 777,
    }, headers=auth(editor))

    db.session.expire_all()
    updated = db.session.get(Event, event.id)
    assert updated.name == "牧野之战（改）"
    assert updated.created_at == original_created, "入库时间不该被请求体改动"


@pytest.mark.parametrize("bad_name", ["", "   "])
def test_空名称一律拒绝(client, make_user, auth, bad_name):
    """空串与纯空白都算"没有名称"——否则图谱上会留下一个看不见名字的节点。"""
    from models import Event, db

    event = Event(name="城濮之战")
    db.session.add(event)
    db.session.commit()
    editor = make_user("editor-1", "editor")

    response = client.post("/update_node", json={
        "type": "Event", "id": event.id, "EventName": bad_name,
    }, headers=auth(editor))

    assert response.status_code == 400
