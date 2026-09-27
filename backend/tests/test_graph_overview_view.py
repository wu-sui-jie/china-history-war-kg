"""战争关系图首页（总览）的取样口径。

背景：总览页原先走 `MATCH (n) RETURN n LIMIT 50`——无标签全表扫描按**节点 id 顺序**
返回，取到的是最早入库的一批节点。实测本库 id 0..49 全是 Place，而地点与地点之间没有
关系（边只有 事件-地点、事件-人物、事件-组织、事件-事件），于是画面上是 50 个孤立点、
0 条关系。用户的原话是"只有地点，叫什么总览"。

改为 `get_overview_graph()` 后要钉住四件事，任何一条被改回去都会重现上面的症状：

1. **四类实体都在**（配额表覆盖四类，且总和 = `DEFAULT_VIEW_NODE_LIMIT`，与四个子页同口径）；
2. **取样按"关系多"排**，不按 id —— 假图里把"id 最小的一批地点"混进去，断言它们不会
   因为 id 小就被优先选中；
3. **三类邻居只从锚点事件的一阶邻居里取**，因此每个节点都连得上锚点（画面上没有孤立点）；
4. **同一条边只留一条**：`MATCH (n)-[r]-(m)` 是无向匹配，每条边会回来两次。

用例不走真 Neo4j：假图按 Cypher 文本分派（总览要跑多条查询，一条假记录喂不动）。
"""

from __future__ import annotations

import pytest

from model_search import (
    DEFAULT_VIEW_NODE_LIMIT,
    OVERVIEW_TYPE_QUOTAS,
    neo4j_db,
)


# ---------------------------------------------------------------- 假 Neo4j 对象


class FakeNode:
    """够用的节点替身：`model_search` 只用到 identity / labels / 属性遍历 / get。"""

    def __init__(self, identity: int, name: str, labels=("Event",), props=None):
        self.identity = identity
        self.labels = set(labels)
        self._props = {"name": name}
        self._props.update(props or {})

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]

    def __iter__(self):
        return iter(self._props)


class FakeRelation:
    """关系替身。属性为空，但要像 py2neo 的 Relationship 那样**可迭代、可做 `in` 判断**：
    `model_search` 里有 `if 'relation_type' in rel` 和 `for key, value in rel.items()`。"""

    def __init__(self, start, end):
        self.start_node = start
        self.end_node = end

    def items(self):
        return {}.items()

    def keys(self):
        return []

    def __iter__(self):
        return iter({})

    def __contains__(self, key):
        return False


def make_relation(start, end, rel_type: str):
    """造一个"类名即关系类型"的关系对象，对应 py2neo 的真实行为。"""
    return type(rel_type, (FakeRelation,), {})(start, end)


class FakeResult:
    def __init__(self, records):
        self._records = records

    def data(self):
        return self._records


class RoutingGraph:
    """按 Cypher 文本分派的假图，并记录每次查询的参数。"""

    def __init__(self, anchors, neighbours, relations):
        self._anchors = anchors
        self._neighbours = neighbours
        self._relations = relations
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher: str, **params):  # noqa: ANN003
        self.calls.append((cypher, params))
        if "MATCH (e:Event)-[r]-()" in cypher:
            # 真 Cypher 里有 LIMIT $limit，替身也要守约，否则配额断言测不出东西
            limit = int(params.get("limit") or len(self._anchors))
            return FakeResult([{"e": node} for node in self._anchors[:limit]])
        if "MATCH (a:Event)-[]-(n:" in cypher:
            for label, nodes in self._neighbours.items():
                if f"`{label}`" in cypher:
                    limit = int(params.get("limit") or len(nodes))
                    return FakeResult([{"n": node} for node in nodes[:limit]])
            return FakeResult([])
        if "MATCH (n)-[r]-(m)" in cypher:
            return FakeResult([{"r": rel} for rel in self._relations])
        raise AssertionError(f"未预期的 Cypher：{cypher.strip()[:60]}")

    def params_for(self, marker: str) -> dict:
        for cypher, params in self.calls:
            if marker in cypher:
                return params
        raise AssertionError(f"没有查询包含 {marker!r}")


def make_handle(graph):
    """绕过 `__init__`（它要连真库）造一个句柄。"""
    handle = object.__new__(neo4j_db)
    handle.graph = graph
    return handle


@pytest.fixture()
def overview_graph():
    """锚点数与三类邻居都比配额多，用来验证"按配额截断"。

    地点里故意塞一个 id 极小（id=0）的节点：旧实现就是靠 id 顺序取到它的。
    """
    anchors = [FakeNode(9000 + i, f"事件{i}", ("Event",), {"graph_key": f"Event:{i}"}) for i in range(40)]
    neighbours = {
        "Place": [FakeNode(0, "id最小的地点", ("Place",), {"graph_key": "Place:1"})]
        + [FakeNode(100 + i, f"地点{i}", ("Place",), {"graph_key": f"Place:{i + 2}"}) for i in range(40)],
        "Person": [FakeNode(500 + i, f"人物{i}", ("Person",), {"graph_key": f"Person:{i}"}) for i in range(40)],
        "Organization": [FakeNode(700 + i, f"势力{i}", ("Organization",), {"graph_key": f"Org:{i}"}) for i in range(20)],
    }
    relation = make_relation(anchors[0], neighbours["Place"][0], "主战场")
    return RoutingGraph(anchors, neighbours, [relation, relation])


# ---------------------------------------------------------------- 配额口径


def test_四类配额之和等于默认视图上限():
    """总览与四个子页同一口径：都是 DEFAULT_VIEW_NODE_LIMIT 个实体节点。"""
    assert sum(quota for _, quota in OVERVIEW_TYPE_QUOTAS) == DEFAULT_VIEW_NODE_LIMIT


def test_配额覆盖四类实体():
    assert {label for label, _ in OVERVIEW_TYPE_QUOTAS} == {"Event", "Place", "Person", "Organization"}
    assert all(quota > 0 for _, quota in OVERVIEW_TYPE_QUOTAS)


# ---------------------------------------------------------------- 取样结果


def test_总览四类实体都在且不超过上限(overview_graph):
    result = make_handle(overview_graph).get_overview_graph()

    types = {node["type"] for node in result["nodes"]}
    assert types == {"Event", "Place", "Person", "Organization"}
    assert len(result["nodes"]) == DEFAULT_VIEW_NODE_LIMIT
    assert result["node_limit"] == DEFAULT_VIEW_NODE_LIMIT
    assert result["truncated"] is True
    # 邻居在假图里都比配额多，因此各类应正好取满配额
    for label, quota in OVERVIEW_TYPE_QUOTAS:
        got = len([node for node in result["nodes"] if node["type"] == label])
        assert got == quota, f"{label} 取了 {got} 个，配额是 {quota}"


def test_邻居只从锚点的一阶邻居里取(overview_graph):
    """画面上不该有孤立点：三类邻居都来自锚点事件的一阶扩展。"""
    make_handle(overview_graph).get_overview_graph()

    quotas = dict(OVERVIEW_TYPE_QUOTAS)
    for label, _ in OVERVIEW_TYPE_QUOTAS:
        if label == "Event":
            continue
        params = overview_graph.params_for(f"`{label}`")
        # 邻居查询必须带上锚点集合，否则就退化成"按 id 全表取样"
        assert params.get("anchor_ids"), f"{label} 的邻居查询没有限定锚点集合"
        assert params["limit"] == quotas[label], f"{label} 没有按配额取"


def test_不按节点id取样(overview_graph):
    """id=0 的地点与锚点之间没有关系，就不该因为 id 小被选中。"""
    result = make_handle(overview_graph).get_overview_graph()

    # 假图的邻居查询不做"与锚点有边"的过滤（真库由 Cypher 保证），这里只钉住
    # "取哪一批由查询参数决定、不由 id 顺序决定"：排序表达式里必须带 graph_key。
    for cypher, _ in overview_graph.calls:
        if "ORDER BY" in cypher:
            assert "graph_key" in cypher, f"取样排序没带 graph_key（会退化成按 id 排）：{cypher.strip()[:80]}"
    assert result["nodes"], "取样结果不该为空"


def test_同一条边只保留一条(overview_graph):
    """无向匹配会把每条边回来两次（含反向），去重后应只剩一条。"""
    result = make_handle(overview_graph).get_overview_graph()

    assert len(result["lines"]) == 1
    assert {(line["from"], line["to"]) for line in result["lines"]} == {(9000, 0)}


def test_锚点事件按配额取(overview_graph):
    make_handle(overview_graph).get_overview_graph()

    params = overview_graph.params_for("MATCH (e:Event)-[r]-()")
    assert params["limit"] == dict(OVERVIEW_TYPE_QUOTAS)["Event"]


def test_查询异常时返回空图而不是抛给接口():
    class BrokenGraph:
        def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("neo4j 掉了")

    result = make_handle(BrokenGraph()).get_overview_graph()

    assert result == {"nodes": [], "lines": []}


# ---------------------------------------------------------------- 路由链路


def test_端点不带筛选走总览(client, make_user, auth, monkeypatch, overview_graph):
    import blueprints.graph as graph_mod

    monkeypatch.setattr(graph_mod.neo4j_db_handle, "graph", overview_graph)
    user = make_user("user-overview", "viewer")

    payload = client.post("/search_name_kg", json={}, headers=auth(user)).get_json()

    assert payload["code"] == 200
    assert payload["graph_mode"] == "overview"
    assert len(payload["data"]["nodes"]) == DEFAULT_VIEW_NODE_LIMIT


def test_端点带名称时仍走聚焦路径(client, make_user, auth, monkeypatch):
    """带筛选的语义没变：走 search_nodes_by_name，而不是总览取样。"""
    import blueprints.graph as graph_mod

    seen = {}

    def fake_search(name, limit=100):
        seen["name"] = name
        return {"nodes": [{"id": 1, "name": name, "type": "Event"}], "lines": []}

    monkeypatch.setattr(graph_mod.neo4j_db_handle, "search_nodes_by_name", fake_search)
    user = make_user("user-focus", "viewer")

    payload = client.post("/search_name_kg", json={"name": "秦"}, headers=auth(user)).get_json()

    assert payload["graph_mode"] == "focused"
    assert seen["name"] == "秦"
