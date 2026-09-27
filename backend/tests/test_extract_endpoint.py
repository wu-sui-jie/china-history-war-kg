"""抽取接口的成功/失败口径。

编排层若把三个阶段（实体/事件/关系）的异常全部 `warning` 掉继续跑，模型侧不可用时
接口就会给出 **HTTP 200 + code 200 + "识别完成" + 全空结果**——把故障说成"这段
文本没有实体"，是最坏的一种错。正确口径：
- 一个阶段都没成功 → `ExtractionUnavailable`，接口 5xx；
- 部分阶段失败 → 结果照常返回（局部成功仍有用），但响应体带 `partial_errors`。

失败分支的 HTTP 状态必须与响应体的 code 一致（不能 HTTP 200 包 body 500/400）。

这里不打真模型：`extract_all_optimized` 与云端客户端（`DeepSeekClient`）都由用例替换。
阶段统计本身另有一组直接调用 `llm_pipeline.extract_all_optimized` 的用例（用假 LLM 客户端）。
"""

import json

import pytest

import llm_pipeline
import war_extraction.core
from jwt_util import encode

# 一条通过实体抽取的模型输出（假客户端的第一答复）
ENTITY_JSON_ONE_PLACE = '{"places": [{"geo_name": "赤壁"}], "organizations": [], "persons": []}'


class FakeLLM:
    """假的大模型客户端。

    按顺序消费 `answers`；用完后若给了 `error` 就抛它，否则回一个空 JSON。
    三个抽取器都只通过 `llm.call(prompt, temperature=…, json_mode=…)` 取结果，
    所以这个形状足够驱动整条编排链（不需要任何网络）。
    """

    def __init__(self, answers=None, error=None):
        self.answers = list(answers or [])
        self.error = error
        self.calls = 0

    def call(self, prompt, temperature=0.1, json_mode=False):
        self.calls += 1
        if self.answers:
            return self.answers.pop(0)
        if self.error is not None:
            raise self.error
        return "{}"


# ---------------------------------------------------------------- 编排层（直调）


def test_所有阶段都失败时抛_ExtractionUnavailable():
    llm = FakeLLM(error=ConnectionError("DeepSeek API 连接失败（网络不可达）"))

    with pytest.raises(llm_pipeline.ExtractionUnavailable) as excinfo:
        llm_pipeline.extract_all_optimized(llm, "赤壁之战发生于公元 208 年。")

    assert "DeepSeek API 连接失败" in str(excinfo.value), "真实原因要带给调用方"
    assert excinfo.value.reasons, "失败明细可供接口与日志使用"


def test_部分阶段失败时返回结果并带_partial_errors():
    """实体阶段成功、事件阶段失败：结果能用（不能整体作废），但必须如实标注不完整。"""
    llm = FakeLLM(answers=[ENTITY_JSON_ONE_PLACE], error=ConnectionError("模型中途抽风"))

    entities, _event_result, _relations, diagnostics = llm_pipeline.extract_all_optimized(
        llm, "赤壁之战发生于公元 208 年。")

    assert [p.geo_name for p in entities.places] == ["赤壁"]
    assert diagnostics["stage_ok"]["entity"] == 1
    assert diagnostics["stage_ok"]["event"] == 0
    assert diagnostics["partial_errors"], "部分失败必须留下说明"
    assert any("事件抽取" in item for item in diagnostics["partial_errors"])


# ------------------------------------------------- 载荷完整性（阶段四：悬空边与跨类型冲突）

# 按提示词里的阶段标记选回答，而不是"第几次调用给什么"。编排调整调用顺序时
# 这种用例不会跟着变红（那种红是假红）；真出了错，错的也是内容而不是顺序。
_STAGE_MARKERS = (
    ("entity", "实体抽取专家"),
    ("event_identify", "事件识别专家"),
    ("event_full", "事件信息抽取专家"),
    ("relation", "事件关系抽取专家"),
)

_ENTITIES = {
    "places": [{"geo_name": "牧野", "modern_name": "今河南新乡", "DynastyName": "周朝",
                "source_text": "战于牧野"}],
    # "商" 故意在两张表里都出现：它既不像人名也不像组织名（标记词都命不中），
    # 所以走的是"同一名字进了两张实体表"那条互斥规则，不是分类器规则。
    "organizations": [{"OrgName": "周军", "OrgType": "军队", "DynastyName": "周朝"},
                      {"OrgName": "商", "OrgType": "政权", "DynastyName": "商朝"}],
    "persons": [{"PersonName": "周武王", "DynastyName": "周朝", "Role": "君主"},
                {"PersonName": "商", "DynastyName": "商朝", "Role": "君主"}],
}

_EVENT_IDENTIFY = {"events": [{"id": "E1", "name": "牧野之战", "time": "公元前1046年",
                               "location": "牧野", "parties": "周军、商军",
                               "evidence": "战于牧野"}]}

_EVENT_FULL = {"events": [{
    "EventName": "牧野之战", "EventType": "战争", "StartDate": "公元前1046年",
    "EndDate": "公元前1046年", "DynastyName": "周朝", "Place": "牧野",
    "Aggressor": "周军", "Defender": "商", "Result": "周胜", "Commanders": "周武王",
    "Action": "交战", "source_text": "战于牧野",
}]}

_RELATIONS = {
    "event_place_relations": [
        {"EventName": "牧野之战", "relation": "主战场", "modern_name": "牧野", "evidence": "战于牧野"},
        # 目标 "朝歌" 不在地点表里 → 悬空的目标端
        {"EventName": "牧野之战", "relation": "主战场", "modern_name": "朝歌", "evidence": "战于牧野"},
    ],
    "event_person_relations": [
        {"EventName": "牧野之战", "PersonName": "周武王", "relation": "统帅", "evidence": "周武王率师"},
        # 目标 "商军" 不在人物表里（抽取器自己就没把它当人）→ 这条边没有落点
        {"EventName": "牧野之战", "PersonName": "商军", "relation": "参与者", "evidence": "商军倒戈"},
        # 事件端 "癸卯之战" 不在事件表里 → 悬空的事件端
        {"EventName": "癸卯之战", "PersonName": "周武王", "relation": "统帅", "evidence": "周武王率师"},
    ],
    "event_organization_relations": [
        {"EventName": "牧野之战", "OrgName": "周军", "relation": "发起方", "evidence": "周武王率师"},
        {"EventName": "牧野之战", "OrgName": "商", "relation": "参战方", "evidence": "商军倒戈"},
    ],
    "event_event_relations": [
        {"EventName_A": "牧野之战", "EventName_B": "商灭夏之战", "relation": "顺承关系",
         "evidence": "此后"},
    ],
}


class StageFakeLLM:
    """按阶段标记回罐头回答；记下调用过的阶段名，供"环节没跑"这类断言用。"""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.stages = []

    def call(self, prompt, temperature=0.1, max_retries=3, json_mode=False):
        for stage, marker in _STAGE_MARKERS:
            if marker in prompt:
                self.stages.append(stage)
                if stage == "event_full":
                    stage = "event_full" if "event_full" in self.answers else "event_identify"
                if stage not in self.answers:
                    raise AssertionError(f"用例没有给 {stage} 阶段的罐头回答")
                return json.dumps(self.answers[stage], ensure_ascii=False)
        raise AssertionError("提示词里没有阶段标记，无法判断这是哪一步")


@pytest.fixture()
def stage_llm():
    return StageFakeLLM({
        "entity": _ENTITIES,
        "event_identify": _EVENT_IDENTIFY,
        "event_full": _EVENT_FULL,
        "relation": _RELATIONS,
    })


def _node_names(payload):
    """载荷里能当图谱节点的名字（与前端 graphData 建节点用的是同一批字段）。"""
    names = {e["EventName"] for e in payload["events"]}
    names |= {p["geo_name"] for p in payload["entities"]["places"]}
    names |= {o["OrgName"] for o in payload["entities"]["organizations"]}
    names |= {p["PersonName"] for p in payload["entities"]["persons"]}
    return names


def _relation_ends(payload):
    """载荷里每条关系的两端（前端 graphData 的 from/to 就是这两个值）。"""
    ends = []
    for r in payload["relations"]["event_place"]:
        # 前端取的是 `rel.PlaceName || rel.modern_name`，而 EventPlaceRelation 没有
        # PlaceName 字段（序列化出来是 null），所以实际连线用的是 modern_name
        ends.append(("event_place", r["EventName"], r["PlaceName"] or r["modern_name"]))
    for r in payload["relations"]["event_person"]:
        ends.append(("event_person", r["EventName"], r["PersonName"]))
    for r in payload["relations"]["event_organization"]:
        ends.append(("event_organization", r["EventName"], r["OrgName"]))
    for r in payload["relations"]["event_event"]:
        ends.append(("event_event", r["EventName_A"], r["EventName_B"]))
    return ends


def test_跨类型同名不会同时进人物表与组织表(stage_llm):
    """P2-2：同一个名字出现在两张实体表里，页面上是"同一个人又是组织"。"""
    entities, _events, _relations, _diagnostics = llm_pipeline.extract_all_optimized(
        stage_llm, "公元前1046年，周武王率师与商军战于牧野。")

    persons = [p.PersonName for p in entities.persons]
    orgs = [o.OrgName for o in entities.organizations]
    assert "商" in persons, "人物先收集，所以留在人物表"
    assert "商" not in orgs, "已经在人物表里的名字不能再进组织表"
    assert set(persons) & set(orgs) == set(), "两张表不能有同名条目"
    assert set(orgs) == {"周军"}


def test_载荷里没有悬空边(stage_llm):
    """P2-1：关系两端都必须能在载荷自己的表里找到落点。

    前端 `KgGraph` 会把两端找不到节点的连线**静默滤掉**，于是"关系列表里有、
    图谱上没有"——用户看到的是两套结果。这里保证载荷本身就没有这种边。
    """
    entities, event_result, relations, _diagnostics = llm_pipeline.extract_all_optimized(
        stage_llm, "公元前1046年，周武王率师与商军战于牧野。")
    payload = llm_pipeline.serialize_extraction_result(entities, event_result, relations, 0.0)

    nodes = _node_names(payload)
    dangling = [(kind, a, b) for kind, a, b in _relation_ends(payload)
                if a not in nodes or b not in nodes]

    assert dangling == [], "悬空边不该出现在载荷里：%s" % dangling
    # 罐头里的三类悬空边都被剔了；留下的都是两端有落点的（含从事件字段派生出来的那些）
    assert "商军" not in {r["PersonName"] for r in payload["relations"]["event_person"]}
    assert payload["relations"]["event_event"] == []
    assert all(r["EventName"] == "周武王灭商牧野之战"
               for kind, rows in payload["relations"].items() for r in rows
               if kind.startswith("event_") and kind != "event_event")


def test_剔除数量进诊断与日志(stage_llm):
    """剔除不能是静默的：丢了多少、为什么丢要有通道（离线侧同样要求）。

    `cross_type_conflict` 只记 1 条：罐头里的 "商军" 在**抽取器自己的分桶**里就被
    处理掉了（`ORG_OVERRIDES`），没进到钩子；钩子自己抓到的是 "商" 两张表都有这一条。
    这也说明在线侧的互斥是第二道防线，第一道在抽取器里。
    """
    _entities, _events, _relations, diagnostics = llm_pipeline.extract_all_optimized(
        stage_llm, "公元前1046年，周武王率师与商军战于牧野。")

    dropped = diagnostics["post_cleanup_dropped"]
    assert dropped["cross_type_conflict"] == 1, "商：人物表已有，组织表不再收"
    assert dropped["dangling_target"] == 3, "朝歌 / 商军 / 商 三条目标端找不到"
    assert dropped["dangling_event"] == 2, "癸卯之战 + 商灭夏之战两条事件端找不到"


# ---------------------------------------------------------------- 接口层（HTTP）


@pytest.fixture()
def extract_env(monkeypatch):
    """装上假的 LLM 客户端与编排器，返回"让编排器返回什么"的开关。

    桩的目标是**接口真正 lookup 的那一处**：`blueprints/llm.py` 在函数内
    `from war_extraction.core import DeepSeekClient`，所以按模块属性替换
    `war_extraction.core.DeepSeekClient` 才能拦住它——换成 `llm_pipeline` 上的名字
    是拦不住的（接口已经不看那里了），会静默地打真模型、真烧配额。
    """
    state = {"result": None, "error": None}

    monkeypatch.setattr(war_extraction.core, "DeepSeekClient", lambda *a, **k: object())

    def fake_extract(llm, text):
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

    monkeypatch.setattr(llm_pipeline, "extract_all_optimized", fake_extract)
    return state


def _empty_result(partial_errors=None):
    from war_extraction.models import EntityExtractionResult, EventExtractionResult, RelationExtractionResult

    return (
        EntityExtractionResult(places=[], organizations=[], persons=[]),
        EventExtractionResult(events=[]),
        RelationExtractionResult(),
        {"stage_ok": {"entity": 1, "event": 1, "relation": 0},
         "partial_errors": list(partial_errors or [])},
    )


def test_整体不可用时返回_5xx_而不是假的识别完成(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["error"] = llm_pipeline.ExtractionUnavailable(["实体抽取失败: 连接被拒绝"])

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 500, "不能让客户端以为成功了"
    payload = response.get_json()
    assert payload["code"] == 500
    assert "不可用" in payload["msg"]
    assert payload["data"] == {}


def test_部分失败时_200_且响应体带_partial_errors(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["result"] = _empty_result(["事件抽取失败: 模型超时"])

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200
    assert payload["data"]["partial_errors"] == ["事件抽取失败: 模型超时"]
    assert payload["data"]["summary"]["event_count"] == 0


def test_全部成功时响应体不带_partial_errors(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["result"] = _empty_result()

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 200
    assert "partial_errors" not in response.get_json()["data"]


def test_接口把云端客户端交给编排层(client, make_user, auth, monkeypatch):
    """阶段一：在线抽取必须用云端 DeepSeek，不是本地 Ollama。

    判据是"编排层收到的那个客户端，就是接口刚建的那个"——只断言请求成功是抓不住回退的：
    回退成 `OllamaAdapter` 时桩不会被调用，接口照样返回 200（编排层被替掉了，不会真调模型）。
    """
    editor = make_user("editor-1", "editor")
    made = []

    class FakeCloudClient:
        def __init__(self, *args, **kwargs):
            made.append(self)

        def call(self, *args, **kwargs):  # pragma: no cover - 编排层被替换，走不到这里
            raise AssertionError("用例不该真调模型")

    monkeypatch.setattr(war_extraction.core, "DeepSeekClient", FakeCloudClient)
    seen = {}

    def fake_extract(llm, text):
        seen["llm"] = llm
        return _empty_result()

    monkeypatch.setattr(llm_pipeline, "extract_all_optimized", fake_extract)

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 200
    assert made, "接口没有构造 cloud client——是不是又回退到本地 Ollama 了？"
    assert seen["llm"] is made[0], "交给编排层的必须是接口刚建的那个客户端"


def test_异常字符串不回显内部细节(client, make_user, auth, extract_env):
    """异常原文可能带路径/连接串；回给客户端的只有第一行且截断。"""
    editor = make_user("editor-1", "editor")
    extract_env["error"] = RuntimeError(
        "第一行原因\nTraceback (most recent call last):\n  File \"C:/secret/path.py\", line 1")

    payload = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                          headers=auth(editor)).get_json()

    assert "第一行原因" in payload["msg"]
    assert "Traceback" not in payload["msg"]
    assert "C:/secret/path.py" not in payload["msg"]


@pytest.mark.parametrize("body,expected_msg", [
    ({}, "请求参数不能为空"),
    ({"text": "   "}, "文本内容不能为空"),
    ({"text": "战" * 1001}, "文本内容过长"),
])
def test_参数错误返回_400_且带状态码(client, make_user, auth, body, expected_msg):
    editor = make_user("editor-1", "editor")

    response = client.post("/api/extract/entities-events", json=body, headers=auth(editor))

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == 400
    assert expected_msg in payload["msg"]


def test_畸形_JSON_返回_JSON_而不是_HTML_错误页(client, make_user, auth):
    editor = make_user("editor-1", "editor")

    response = client.post("/api/extract/entities-events", data="{不是 JSON",
                           content_type="application/json", headers=auth(editor))

    assert response.status_code == 400
    assert response.is_json, "get_json(silent=True) 的意义就是这里给 JSON 而不是 Flask 的 HTML 页"
    assert response.get_json()["code"] == 400


def test_未授权请求不会触达抽取逻辑(client, make_user, extract_env):
    """拦住未授权的请求要发生在调模型之前——否则配额已经被烧掉了。"""
    viewer = make_user("viewer-1", "viewer")
    extract_env["error"] = AssertionError("不该走到编排层")

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers={"Token": encode(viewer.id)})

    assert response.status_code == 403
