"""四个图谱子页的默认视图节点上限（历史战争 / 参战势力 / 历史人物 / 战争地点）。

背景：这四个页面默认不带任何筛选条件，过去把 Cypher 的 `LIMIT 200`（关系行数）当成
规模控制，实际落到画布上的是 177~204 个节点，力导向布局明显卡顿。改为在 Python 侧按
**实体节点数**截断到 100（见 `model_search.DEFAULT_VIEW_NODE_LIMIT`）。

顺带钉住两条容易被改回去的口径：

1. **有筛选就不截断**——用户搜索名称或选关系类型时，要看的就是特定的那部分实体；
2. **超限时返回体带 `truncated`**——前端靠它提示"只展示了前 N 个"，否则用户会以为
   这张图就这么大。

用例不走真 Neo4j：`conftest` 已经把 `py2neo.Graph` 换成空实现，这里再用假 graph 注入
记录，就能让"路由 → 句柄 → 截断 → JSON"整条链路真的跑一遍。
"""

from __future__ import annotations

import pytest

from model_search import DEFAULT_VIEW_NODE_LIMIT, cap_default_view_graph, neo4j_db

# 每个端点：路由、记录键（事件节点与对端节点）、对端标签
ENDPOINTS = [
    ("/api/graph/event_event", "e2", "Event"),
    ("/api/graph/event_organization", "o", "Organization"),
    ("/api/graph/event_person", "p", "Person"),
    ("/api/graph/event_place", "p", "Place"),
]

# 路由 → 句柄方法名：路由层用例与句柄层用例共用，避免两处各写一份对照表
METHOD_BY_ENDPOINT = {
    "/api/graph/event_event": "get_event_event_relations",
    "/api/graph/event_organization": "get_event_organization_relations",
    "/api/graph/event_person": "get_event_person_relations",
    "/api/graph/event_place": "get_event_place_relations",
}


# ---------------------------------------------------------------- 假 Neo4j 对象


class FakeNode:
    """够用的节点替身：`model_search` 只用到 identity / labels / 属性遍历。"""

    def __init__(self, identity: int, name: str, labels=("Event",)):
        self.identity = identity
        self.labels = set(labels)
        self._props = {"name": name}

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]

    def __iter__(self):
        return iter(self._props)


class FakeRelation:
    """属性为空即可：关系类型取自 `type(r).__name__`（见下面的工厂）。"""

    def __init__(self, start, end):
        self.start_node = start
        self.end_node = end

    def items(self):
        return {}.items()


def make_relation(start, end, rel_type: str):
    """造一个"类名即关系类型"的关系对象，对应 py2neo 的真实行为。"""
    return type(rel_type, (FakeRelation,), {})(start, end)


class FakeResult:
    def __init__(self, records):
        self._records = records

    def data(self):
        return self._records


class FakeGraph:
    """无论什么 Cypher 都返回同一批记录：本用例考的是截断，不是查询本身。"""

    def __init__(self, records):
        self._records = records

    def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return FakeResult(self._records)


def build_records(pair_count: int, peer_key: str, peer_label: str):
    """造 `pair_count` 组「事件 ↔ 对端」记录，每组产生 2 个新节点、1 条关系。"""
    records = []
    for index in range(pair_count):
        event = FakeNode(10000 + index, f"事件{index}", ("Event",))
        peer = FakeNode(20000 + index, f"对端{index}", (peer_label,))
        record = {"e": event, peer_key: peer, "r": make_relation(event, peer, "顺承关系")}
        if peer_key == "e2":
            record = {"e1": peer, "e2": event, "r": make_relation(peer, event, "顺承关系")}
        records.append(record)
    return records


def attach_fake_graph(monkeypatch, records):
    """把假 graph 挂到全局句柄上，`monkeypatch` 负责用例结束后还原。"""
    import blueprints.graph as graph_mod

    monkeypatch.setattr(graph_mod.neo4j_db_handle, "graph", FakeGraph(records))
    return graph_mod


def make_handle(records):
    """绕过 `__init__`（它要连真库）造一个句柄，直接测四个查询方法。"""
    handle = object.__new__(neo4j_db)
    handle.graph = FakeGraph(records)
    return handle


# ---------------------------------------------------------------- 纯函数


def test_超过上限时只保留前N个节点并丢弃悬空连线():
    nodes = [{"id": i, "name": f"n{i}", "type": "Event"} for i in range(150)]
    lines = [{"from": i, "to": i + 1, "text": "顺承关系"} for i in range(149)]

    result = cap_default_view_graph({"nodes": nodes, "lines": lines})

    assert len(result["nodes"]) == DEFAULT_VIEW_NODE_LIMIT
    assert result["truncated"] is True
    assert result["node_limit"] == DEFAULT_VIEW_NODE_LIMIT
    # 149 条连线里只有两端都在前 100 个节点内的才该留下
    assert len(result["lines"]) == DEFAULT_VIEW_NODE_LIMIT - 1
    kept_ids = {node["id"] for node in result["nodes"]}
    assert all(line["from"] in kept_ids and line["to"] in kept_ids for line in result["lines"])


def test_未超上限时不截断也不改动数据():
    graph = {"nodes": [{"id": 1}, {"id": 2}], "lines": [{"from": 1, "to": 2}]}

    result = cap_default_view_graph(graph)

    assert result["nodes"] == graph["nodes"]
    assert result["lines"] == graph["lines"]
    assert result["truncated"] is False


@pytest.mark.parametrize("name_filter,rel_type", [("淝水之战", ""), ("", "顺承关系"), ("淝水之战", "顺承关系")])
def test_带筛选条件时再大也不截断(name_filter, rel_type):
    """搜索/关系筛选是用户主动收窄范围，不该再被默认上限截一刀。"""
    nodes = [{"id": i, "name": f"n{i}"} for i in range(150)]

    result = cap_default_view_graph({"nodes": nodes, "lines": []}, name_filter, rel_type)

    assert len(result["nodes"]) == 150
    assert result["truncated"] is False


# ---------------------------------------------------------------- 四个查询方法


@pytest.mark.parametrize("endpoint,peer_key,peer_label", ENDPOINTS)
def test_四个查询方法默认视图按上限截断(endpoint, peer_key, peer_label):
    handle = make_handle(build_records(130, peer_key, peer_label))

    result = getattr(handle, METHOD_BY_ENDPOINT[endpoint])()

    # 130 组记录 = 260 个节点，截断后应正好是上限
    assert len(result["nodes"]) == DEFAULT_VIEW_NODE_LIMIT
    assert result["truncated"] is True


@pytest.mark.parametrize("endpoint,peer_key,peer_label", ENDPOINTS)
def test_四个查询方法带名称筛选时不截断(endpoint, peer_key, peer_label):
    handle = make_handle(build_records(130, peer_key, peer_label))

    result = getattr(handle, METHOD_BY_ENDPOINT[endpoint])("事件1")

    assert len(result["nodes"]) == 260
    assert result["truncated"] is False


# ---------------------------------------------------------------- 路由链路


@pytest.mark.parametrize("endpoint,peer_key,peer_label", ENDPOINTS)
def test_端点默认视图返回上限与截断标记(client, make_user, auth, monkeypatch, endpoint, peer_key, peer_label):
    attach_fake_graph(monkeypatch, build_records(130, peer_key, peer_label))
    user = make_user(f"user-{peer_key}", "viewer")

    payload = client.get(endpoint, headers=auth(user)).get_json()

    assert payload["code"] == 200
    data = payload["data"]
    assert len(data["nodes"]) == DEFAULT_VIEW_NODE_LIMIT
    assert data["truncated"] is True
    assert data["node_limit"] == DEFAULT_VIEW_NODE_LIMIT
    ids = {node["id"] for node in data["nodes"]}
    assert all(line["from"] in ids and line["to"] in ids for line in data["lines"])


def test_端点带名称筛选时返回全部并标明未截断(client, make_user, auth, monkeypatch):
    attach_fake_graph(monkeypatch, build_records(130, "e2", "Event"))
    user = make_user("user-filtered", "viewer")

    payload = client.get("/api/graph/event_event?name=事件1", headers=auth(user)).get_json()

    data = payload["data"]
    assert len(data["nodes"]) == 260
    assert data["truncated"] is False
