"""双写补偿队列（审查报告 P1-3）。

修复前：节点创建/修改/属性更新都是"先提交 SQLite，再调 Neo4j"，Neo4j 失败时只在响应里
带一句 `sync_status=failed`——没有记录、没有重试、没有补偿。管理台列表与图谱从此长期
不一致，而且**没人知道差在哪一条**（质检只能发现数量差）。

这里钉四件事：
1. 失败会**落成持久化待办**（不是内存队列：进程重启不该丢掉待补偿的写入）；
2. 同一节点连续失败不堆记录（否则一次网络抖动留下几十条指向同一节点的任务）；
3. 重放用**当时的属性快照**，幂等，成功即结单；失败累计到上限转 `abandoned` 等人工；
4. 记账失败绝不盖住原始的 Neo4j 错误（那条错误才是排障要看的）。
"""

from sqlalchemy import text

import pytest

import sync_compensation as sc
from models import Event, Neo4jSyncJob, db


@pytest.fixture(autouse=True)
def _clean_jobs(_app_context):
    """队列在用例前后都清空。

    待办是**跨用例累积**的（它就是为"失败了先记着"设计的），不清的话
    "同一节点合并成一条"这类计数断言会被上一条用例留下的记录带偏。
    """
    def _purge():
        Neo4jSyncJob.query.delete(synchronize_session=False)
        db.session.commit()

    _purge()
    yield
    _purge()


def _count(status=None):
    q = Neo4jSyncJob.query
    if status:
        q = q.filter_by(status=status)
    return q.count()


def test_失败会落成待办(_app_context):
    job_id = sc.enqueue_failed_sync("Event", 42, "测试-事件", {"dynasty": "汉"}, "连接被拒")

    assert job_id is not None
    job = Neo4jSyncJob.query.get(job_id)
    assert (job.node_type, job.node_id, job.node_name) == ("Event", 42, "测试-事件")
    assert job.status == sc.STATUS_PENDING
    assert job.attempts == 1
    assert "连接被拒" in job.last_error
    # 属性快照要留着：重放的是"当时那次写"，不是之后可能又改过的当前行
    assert "汉" in job.properties_json


def test_同一节点连续失败不堆记录(_app_context):
    first = sc.enqueue_failed_sync("Event", 7, "同名事件", {}, "第一次失败")
    second = sc.enqueue_failed_sync("Event", 7, "同名事件", {}, "第二次失败")

    assert first == second, "同一节点的待办应该被合并，而不是堆成两条"
    assert _count(sc.STATUS_PENDING) == 1
    job = Neo4jSyncJob.query.get(first)
    assert job.attempts == 2
    assert "第二次失败" in job.last_error


def test_达到尝试上限转人工(_app_context):
    """持续失败的节点重试一万次也不会成功，只会把日志与队列塞满。"""
    job_id = None
    for i in range(sc.MAX_ATTEMPTS):
        job_id = sc.enqueue_failed_sync("Place", 9, "测试-地点", {}, f"第 {i} 次失败")

    job = Neo4jSyncJob.query.get(job_id)
    assert job.status == sc.STATUS_ABANDONED
    assert _count(sc.STATUS_PENDING) == 0
    assert _count(sc.STATUS_ABANDONED) == 1


def test_记账失败不抛异常(_app_context, monkeypatch):
    """它跑在主流程已经失败的路径上，再冒泡一个异常只会盖住原始的 Neo4j 错误。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("库里也不通")

    monkeypatch.setattr(db.session, "commit", _boom)

    assert sc.enqueue_failed_sync("Event", 1, "x", {}, "原始错误") is None


def test_重放成功即结单(_app_context, monkeypatch):
    job_id = sc.enqueue_failed_sync("Event", 3, "测试-事件", {"dynasty": "唐"}, "初次失败")

    calls = {}

    class _FakeNeo4j:
        def upsert_node(self, node_type, graph_key, name, props):
            calls["upsert"] = (node_type, graph_key, name, props)
            return 1001, True

        def delete_node_by_graph_key(self, node_type, graph_key):
            calls["deleted"] = (node_type, graph_key)
            return 1

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())

    stats = sc.retry_pending()

    assert stats == {"attempted": 1, "succeeded": 1, "failed": 0, "abandoned": 0}
    # 定位用的是稳定图谱键，不是名字也不是 Neo4j 内部 id
    assert calls["upsert"][:3] == ("Event", "Event:3", "测试-事件")
    # 属性按 Neo4j 侧的命名重放（列名 dynasty → 属性 DynastyName）
    assert calls["upsert"][3].get("DynastyName") == "唐"
    job = Neo4jSyncJob.query.get(job_id)
    assert job.status == sc.STATUS_DONE
    assert job.last_error == ""


def test_重放成功后回写_neo4j_id(_app_context, monkeypatch):
    """文档第六节第 3 条 B：重试成功但 neo4j_id 仍是空，后续更新/删除仍无法定位节点。"""
    event = Event(name="回写-测试事件", dynasty="汉")
    db.session.add(event)
    db.session.commit()

    sc.enqueue_failed_sync("Event", event.id, event.name, {"dynasty": "汉"}, "初次失败")

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            return 4242, True

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())

    sc.retry_pending()

    assert db.session.get(Event, event.id).neo4j_id == 4242, "重放成功必须把图谱 id 回写主存储"


def test_回写不覆盖已有的_neo4j_id(_app_context, monkeypatch):
    """主存储里的 id 若已存在（可能是重建前的正确值），回写不该把它改掉。"""
    event = Event(name="已有编号事件", dynasty="唐", neo4j_id=777)
    db.session.add(event)
    db.session.commit()
    sc.enqueue_failed_sync("Event", event.id, event.name, {}, "失败")

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            return 888, False

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())
    sc.retry_pending()

    assert db.session.get(Event, event.id).neo4j_id == 777


def test_删除任务重放走删除而不是重建(_app_context, monkeypatch):
    """删除不能补偿时，删除失败会永久留在图谱里（文档第六节第 3 条 D）。"""
    sc.stage_job("Event", 55, sc.OP_NODE_DELETE, "待删事件", None, error="初次失败")
    db.session.commit()

    calls = {}

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            calls["upsert"] = args
            return 1, True

        def delete_node_by_graph_key(self, node_type, graph_key):
            calls["delete"] = (node_type, graph_key)
            return 1

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())

    sc.retry_pending()

    assert calls.get("delete") == ("Event", "Event:55")
    assert "upsert" not in calls, "删除任务绝不能被重放成 upsert（那会把节点建回来）"


def test_改名任务重放会清理历史同名节点(_app_context, monkeypatch):
    """文档第六节第 3 条 C：改名重放若只按新名字 upsert，旧节点会留在图谱里。"""
    sc.stage_job("Event", 66, sc.OP_NODE_UPDATE, "长平之战", {}, from_name="长平战役",
                 error="失败")
    db.session.commit()

    calls = {}

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            return 1, False

        def drop_legacy_node_without_graph_key(self, node_type, name):
            calls["dropped"] = (node_type, name)
            return 1

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())
    sc.retry_pending()

    assert calls["dropped"] == ("Event", "长平战役")


def test_没改名时不清理同名节点(_app_context, monkeypatch):
    """只改属性、名字没变时不该去删"同名的历史节点"——那可能是另一个对象。"""
    sc.stage_job("Event", 67, sc.OP_NODE_UPDATE, "长平之战", {})
    db.session.commit()

    calls = {}

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            return 1, False

        def drop_legacy_node_without_graph_key(self, *args, **kwargs):
            calls["dropped"] = args
            return 0

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())
    sc.retry_pending()

    assert "dropped" not in calls


def test_重放失败会安排下次重试时刻(_app_context, monkeypatch):
    """退避落库而不是留在内存：进程重启不该让退避归零。"""
    sc.stage_job("Event", 4, sc.OP_NODE_CREATE, "测试-事件", {})
    db.session.commit()

    class _BrokenNeo4j:
        def upsert_node(self, *args, **kwargs):
            raise RuntimeError("Neo4j 仍不可用")

    monkeypatch.setattr("model_search.neo4j_db", lambda: _BrokenNeo4j())
    sc.retry_pending()

    job = Neo4jSyncJob.query.filter_by(graph_key="Event:4").one()
    assert job.next_retry_at is not None, "失败后必须给出下次可重试时刻"
    # 未到期的任务不参与后台重放（人工重放不受退避限制）
    assert sc.due_jobs(now=job.next_retry_at - 1) == []
    assert [j.id for j in sc.due_jobs(now=job.next_retry_at + 1)] == [job.id]


def test_重放失败累计到上限转_abandoned(_app_context, monkeypatch):
    job_id = sc.enqueue_failed_sync("Event", 44, "测试-事件", {}, "初次失败")

    class _BrokenNeo4j:
        def upsert_node(self, *args, **kwargs):
            raise RuntimeError("Neo4j 仍不可用")

    monkeypatch.setattr("model_search.neo4j_db", lambda: _BrokenNeo4j())

    # 已 try 1 次，再重试到上限（人工重放不受退避限制，所以每轮都能试）
    for _ in range(sc.MAX_ATTEMPTS - 1):
        stats = sc.retry_pending()
        assert stats["failed"] == 1

    job = Neo4jSyncJob.query.get(job_id)
    assert job.status == sc.STATUS_ABANDONED
    assert job.next_retry_at is None, "已放弃的任务不该再排重试"
    assert "仍不可用" in job.last_error


def test_重放逐条独立_一条失败不堵住后面(_app_context, monkeypatch):
    """否则一个持续失败的节点会永久堵住整个队列。"""
    sc.enqueue_failed_sync("Event", 11, "坏节点", {}, "失败")
    sc.enqueue_failed_sync("Event", 12, "好节点", {}, "失败")

    class _SelectiveNeo4j:
        def upsert_node(self, node_type, graph_key, name, props):
            if name == "坏节点":
                raise RuntimeError("只这个节点失败")
            return 1, True

    monkeypatch.setattr("model_search.neo4j_db", lambda: _SelectiveNeo4j())

    stats = sc.retry_pending()

    assert stats["attempted"] == 2
    assert stats["succeeded"] == 1 and stats["failed"] == 1
    assert Neo4jSyncJob.query.filter_by(node_name="好节点").first().status == sc.STATUS_DONE


def test_汇总计数与待办列表(_app_context):
    sc.enqueue_failed_sync("Event", 21, "甲", {}, "失败")
    sc.enqueue_failed_sync("Event", 22, "乙", {}, "失败")
    Neo4jSyncJob.query.filter_by(node_name="乙").one().status = sc.STATUS_DONE
    db.session.commit()

    assert sc.jobs_summary()["pending"] == 1
    assert sc.jobs_summary()["done"] == 1
    assert [job.node_name for job in sc.pending_jobs()] == ["甲"]


def test_表缺失时汇总不抛异常(_app_context):
    """独立脚本可能先于后端启动跑起来；这时"没有表"应等于"没有待办"，不是崩溃。"""
    db.session.execute(text("DROP TABLE neo4j_sync_jobs"))
    db.session.commit()
    try:
        summary = sc.jobs_summary()

        assert summary["pending"] == 0
        assert summary["table_missing"] is True
    finally:
        # 表是会话级共享的：不建回来会把后面所有用例一起带崩（本文件按定义顺序跑）
        sc.ensure_sync_jobs_table()


def test_创建节点失败时写接口会登记待办(_app_context, monkeypatch):
    """端到端：DbUtil.create_node 的 Neo4j 失败路径必须落到待办，而不是只回一句 failed。"""
    from db_utils import DbUtil

    class _BrokenNeo4j:
        def upsert_node(self, *args, **kwargs):
            raise RuntimeError("Neo4j 连接被拒绝")

    monkeypatch.setattr("model_search.neo4j_db", lambda: _BrokenNeo4j())

    result = DbUtil.create_node("Event", "补偿-测试事件", {"dynasty": "宋"})

    assert result["data"]["sync_status"] == "failed"
    job = Neo4jSyncJob.query.filter_by(node_name="补偿-测试事件").first()
    assert job is not None, "同步失败必须留下待补偿记录"
    assert job.status == sc.STATUS_PENDING
    assert "连接被拒绝" in job.last_error
    # 主存储照常写入：补偿队列不改变"SQLite 先提交"的语义
    assert Event.query.filter_by(name="补偿-测试事件").first() is not None


# ---- 事务型 outbox 与稳定图谱键（第 13 轮整改，文档第六节）----


def test_业务修改与待办同一次提交(_app_context, monkeypatch):
    """业务提交失败时，待办必须一起回滚——否则会留下"没有对应修改的补偿任务"。"""
    from db_utils import DbUtil

    class _BrokenNeo4j:
        def upsert_node(self, *args, **kwargs):
            raise RuntimeError("Neo4j 不可用")

    monkeypatch.setattr("model_search.neo4j_db", lambda: _BrokenNeo4j())
    # 让提交在写库那一瞬失败，模拟落库失败
    original_commit = db.session.commit
    monkeypatch.setattr(db.session, "commit",
                        lambda: (_ for _ in ()).throw(RuntimeError("磁盘满")))

    result = DbUtil.create_node("Event", "事务测试事件", {})

    monkeypatch.setattr(db.session, "commit", original_commit)
    db.session.rollback()
    assert result["code"] == 500
    assert Event.query.filter_by(name="事务测试事件").first() is None
    assert Neo4jSyncJob.query.count() == 0, "业务没写进去时不该留下待办"


def test_任务带稳定图谱键与操作类型(_app_context):
    """按名字/内部 id 定位都会选错对象；graph_key 是唯一稳定的定位依据。"""
    from db_utils import DbUtil

    result = DbUtil.create_node("Place", "长平", {"province": "山西"})
    node_id = result["data"]["id"]

    job = Neo4jSyncJob.query.filter_by(graph_key=f"Place:{node_id}").one()
    assert job.node_type == "Place"
    assert job.node_id == node_id
    assert job.operation == sc.OP_NODE_CREATE


def test_删除也会登记待办并带删除操作(_app_context):
    from db_utils import DbUtil

    created = DbUtil.create_node("Person", "白起", {})
    node_id = created["data"]["id"]
    Neo4jSyncJob.query.delete(synchronize_session=False)
    db.session.commit()

    DbUtil.delete_node("Person", node_id)

    job = Neo4jSyncJob.query.filter_by(graph_key=f"Person:{node_id}").one()
    assert job.operation == sc.OP_NODE_DELETE


def test_同一节点连续变更合并成一条_操作取最新(_app_context):
    """outbox 重放的是"当前应有的状态"，不是动作流水账。"""
    from db_utils import DbUtil

    created = DbUtil.create_node("Event", "合并测试", {})
    node_id = created["data"]["id"]
    Neo4jSyncJob.query.delete(synchronize_session=False)
    db.session.commit()

    DbUtil.update_node("Event", node_id, "合并测试-改名")
    DbUtil.update_node("Event", node_id, "合并测试-再改名")
    DbUtil.delete_node("Event", node_id)

    jobs = Neo4jSyncJob.query.filter_by(graph_key=f"Event:{node_id}").all()
    assert len(jobs) == 1, "同一节点的连续变更应合并成一条待办"
    assert jobs[0].operation == sc.OP_NODE_DELETE, "合并后应保留最新的动作"


def test_数据重导作废旧任务(_app_context):
    """文档第六节第 3 条 F：重导后重放旧任务会把上一批数据又写进图谱。"""
    sc.stage_job("Event", 900, sc.OP_NODE_CREATE, "上一批的节点", {})
    db.session.commit()
    assert sc.jobs_summary()["pending"] == 1
    before = sc.current_dataset_version()

    version = sc.bump_dataset_version()

    assert version == before + 1
    assert sc.jobs_summary()["pending"] == 0
    assert sc.jobs_summary()["cancelled"] == 1
    job = Neo4jSyncJob.query.filter_by(graph_key="Event:900").one()
    assert "重导" in job.last_error


def test_作废的任务不会被重放(_app_context, monkeypatch):
    sc.stage_job("Event", 901, sc.OP_NODE_CREATE, "已作废", {})
    db.session.commit()
    sc.bump_dataset_version()

    calls = []

    class _FakeNeo4j:
        def upsert_node(self, *args, **kwargs):
            calls.append(args)
            return 1, True

    monkeypatch.setattr("model_search.neo4j_db", lambda: _FakeNeo4j())

    assert sc.retry_pending()["attempted"] == 0
    assert calls == []


def test_汇总带最老待办年龄(_app_context):
    """运维最需要一眼看到的数字："积压多久了"。"""
    sc.stage_job("Event", 902, sc.OP_NODE_CREATE, "积压节点", {})
    db.session.commit()

    summary = sc.jobs_summary()

    assert summary["pending"] == 1
    assert summary["oldest_pending_age_seconds"] is not None
    assert summary["oldest_pending_age_seconds"] >= 0
