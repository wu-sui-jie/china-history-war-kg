"""SQLite → Neo4j 双写的补偿队列（事务型 outbox，文档第六节）。

## 为什么需要

节点创建/修改/删除都是"先提交 SQLite，再调 Neo4j"。Neo4j 失败时原先只在响应里带一句
`sync_status=failed`（第 12 轮审查 P1-3）：没有记录、没有重试、没有补偿。后果是
**管理台列表与图谱长期不一致，而且没人知道差在哪一条**——`scripts/consistency_check.py`
只能发现数量差，说不出是哪几个节点、差的是属性还是关系。

## 事务型 outbox（第 13 轮整改的核心变化）

    BEGIN
      改业务表
      stage_job(...)        ← 任务与业务修改在同一个事务里
    COMMIT
    → 再尝试立即写 Neo4j；成功就把任务标 done，失败就留着等后台重放

原先的顺序是"提交业务 → 调 Neo4j → 失败后才记一条"。差别只在**提交与记账的先后**，
但这一条差别决定了崩溃窗口：

| 崩溃时机 | 原先 | 现在 |
| --- | --- | --- |
| SQLite 提交后、Neo4j 调用前 | 不一致且无记录 | 任务已在表里，会被重放 |
| Neo4j 成功后、标 done 前 | —— | 重放一次，幂等（MERGE by graph_key） |

## 定位方式：稳定图谱键，不是名字也不是内部 id

任务里存 `graph_key = "Event:123"`（见 graph_key.py），重放一律 `MERGE (n {graph_key})`。
理由见该模块；简述：按名字定位会在同名节点里选错，且改名会被当成新建；
按 `id(n)` 定位则不抗重建（重导/恢复备份后 id 全变）。

## 覆盖的操作

`node_create` / `node_update` / `node_delete` 三种，都带**属性快照**与
（删除、改名时需要的）**原 Neo4j id 与原名字**。原先只有一种 "upsert"：
更新失败重放时按新名字创建/合并，会留下旧节点并造出重复。

**关系（四类）目前不进本队列**，由 `sync_sqlite_to_neo4j.py` 的全量/增量同步承担。
纳入 outbox 需要先给关系行一个稳定键（关系没有主键可对标，要用两端 graph_key +
关系类型拼），那是独立的一次重构。

## 重试策略

退避序列 30 秒 → 2 分钟 → 10 分钟 → 1 小时，之后转 `abandoned` 等人工处理
（**不无限重试**：一个持续失败的节点重试一万次也不会成功，只会把日志与队列塞满）。
`next_retry_at` 落在库里而不是内存里，所以进程重启不会让退避归零。
"""

from __future__ import annotations

import json
import time

from sqlalchemy import text

from graph_key import graph_key_for
from logging_util import get_logger

logger = get_logger(__name__)

# 单条任务的最大尝试次数：超过即标 abandoned，等人工处理
MAX_ATTEMPTS = 5
# 指数退避（秒）：第 n 次失败后等 BACKOFF_SECONDS[min(n, len-1)] 再试。
# 取值与文档第六节第 6 条一致：30 秒 → 2 分钟 → 10 分钟 → 1 小时。
BACKOFF_SECONDS = (30.0, 120.0, 600.0, 3600.0)

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_ABANDONED = "abandoned"
STATUS_CANCELLED = "cancelled"

OP_NODE_CREATE = "node_create"
OP_NODE_UPDATE = "node_update"
OP_NODE_DELETE = "node_delete"
NODE_OPERATIONS = (OP_NODE_CREATE, OP_NODE_UPDATE, OP_NODE_DELETE)

# 数据重导代数在 app_meta 里的键名（文档第六节第 3 条 F）
DATASET_GENERATION_KEY = "dataset_generation"

_MODEL_TABLE_DDL_HINT = (
    "neo4j_sync_jobs 表不存在时，启动一次后端（python app.py）会由 create_all 自动建表"
)

# 需要补列的列定义（第 13 轮整改给老库加 operation/graph_key 等）。
# 和 bot/db.py 的加列迁移同一思路：create_all 不会给已存在的表补列。
_COLUMN_MIGRATIONS = {
    "neo4j_sync_jobs": (
        ("graph_key", "VARCHAR(64)"),
        ("operation", "VARCHAR(24) NOT NULL DEFAULT 'node_create'"),
        ("from_name", "VARCHAR(255)"),
        ("neo4j_element_id", "VARCHAR(64)"),
        ("dataset_version", "INTEGER"),
        ("next_retry_at", "FLOAT"),
    ),
}


def ensure_sync_jobs_table():
    """建表（若缺失）并补列（若为老库）。

    `db.create_all()` 在应用启动时已经建好；这个函数给独立脚本用（它们不一定走 app 的
    启动路径），让"调用补偿队列"不必依赖"先启动过后端"。
    """
    from models import AppMeta, Neo4jSyncJob, db

    try:
        Neo4jSyncJob.__table__.create(bind=db.engine, checkfirst=True)
        AppMeta.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ 补偿队列表检查失败（{exc}）；{_MODEL_TABLE_DDL_HINT}")
        raise

    # 老库补列（列名与取值见 models.Neo4jSyncJob 的注释）
    for table, columns in _COLUMN_MIGRATIONS.items():
        have = {row[1] for row in db.session.execute(text(f"PRAGMA table_info({table})"))}
        for name, ddl in columns:
            if have and name not in have:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                logger.info(f"已为老库补列：{table}.{name}")
    # 存量行补 graph_key：它们都是"某节点的待补偿任务"，按同样的规则就能算出稳定键
    db.session.execute(text(
        "UPDATE neo4j_sync_jobs SET graph_key = node_type || ':' || node_id "
        "WHERE graph_key IS NULL"
    ))
    db.session.commit()


# ---- 数据重导代数（文档第六节第 3 条 F）----


def current_dataset_version() -> int:
    """取当前数据重导代数；没有记录时返回 1。"""
    from models import AppMeta

    row = AppMeta.query.filter_by(key=DATASET_GENERATION_KEY).first()
    try:
        return int(row.value) if row is not None and row.value else 1
    except (TypeError, ValueError):
        return 1


def bump_dataset_version() -> int:
    """把代数 +1 并**作废所有未完成的任务**；返回新代数。

    数据重导会整体替换知识数据，节点 id 可能变化，此时旧的待补偿任务指向的是上一批数据。
    继续重放它们会把上一批数据重新写进图谱（甚至与新数据混在一起）——
    所以重导的正确动作是"先作废旧任务，再重导"，而不是让它们排队等着被重放。
    """
    from models import AppMeta, Neo4jSyncJob, db

    row = AppMeta.query.filter_by(key=DATASET_GENERATION_KEY).first()
    version = current_dataset_version() + 1
    if row is None:
        db.session.add(AppMeta(key=DATASET_GENERATION_KEY, value=str(version)))
    else:
        row.value = str(version)
    cancelled = db.session.query(Neo4jSyncJob).filter_by(status=STATUS_PENDING).update(
        {"status": STATUS_CANCELLED,
         "last_error": f"数据重导（第 {version} 代）已作废本任务"},
        synchronize_session=False,
    )
    db.session.commit()
    logger.warning(f"⚠️ 数据重导代数升至 {version}，已作废 {cancelled} 条待补偿任务")
    return version


# ---- 写侧：与业务修改同事务 ----


def stage_job(node_type: str, node_id, operation: str, name: str,
              properties: dict | None, *, from_name: str | None = None,
              error: str = ""):
    """把一条待办挂到**当前事务**上（不提交）；返回 job 对象。

    调用方必须在 `db.session.commit()` 之前调用它，这样"业务修改 + 待办"是一次原子提交
    （见模块文档）。同 (graph_key) 的 pending 任务会被**合并**而不是新增：一次网络抖动
    引发的批量失败不该留下几十条指向同一节点的任务，重放时对同一个节点反复写——
    而我们要的只是"它还没同步上"这一个事实。合并时 `operation` 取最新一次动作，
    因为 outbox 重放的是**当前应有的状态**。

    本函数不吞异常：它跑在正常业务事务里，写不进去应当让整个事务回滚
    （半提交的业务数据比"没有这条待办"更糟）。
    """
    from models import Neo4jSyncJob, db

    graph_key = graph_key_for(node_type, node_id)
    if operation not in NODE_OPERATIONS:
        raise ValueError(f"未知的补偿操作：{operation!r}")

    job = (Neo4jSyncJob.query
           .filter_by(graph_key=graph_key, status=STATUS_PENDING)
           .order_by(Neo4jSyncJob.id.desc())
           .first())
    payload = json.dumps(properties or {}, ensure_ascii=False)
    if job is not None:
        job.operation = operation
        job.node_name = name
        job.from_name = from_name if from_name is not None else job.from_name
        job.properties_json = payload
        job.last_error = str(error or "")
        job.attempts = (job.attempts or 0) + 1
        if job.attempts >= MAX_ATTEMPTS:
            job.status = STATUS_ABANDONED
        return job

    job = Neo4jSyncJob(
        node_type=node_type, node_id=node_id, node_name=name,
        graph_key=graph_key, operation=operation, from_name=from_name,
        properties_json=payload, last_error=str(error or ""),
        attempts=1, status=STATUS_PENDING,
        dataset_version=current_dataset_version(),
    )
    db.session.add(job)
    # DEBUG 而不是 WARNING：stage_job 现在是**每次写入的常规步骤**，紧接着就会尝试立即同步，
    # 绝大多数情况下任务马上就结单。在正常路径上打 WARNING 只会让日志里到处是"待重放"，
    # 真正需要人看的失败（mark_job_failed）反而被淹没。
    logger.debug(f"已登记补偿任务：{operation} {graph_key}（{name}）")
    return job


def enqueue_failed_sync(node_type: str, node_id: int, node_name: str,
                        properties: dict | None, error: str) -> int | None:
    """把一次失败的同步落成待办并**自己提交**；返回任务 id（失败则 None）。

    保留它是为了兼容"事后记账"的调用场景与既有脚本。**新代码请走 `stage_job`**：
    同事务写入不会留下"业务已提交、待办没写进去"的崩溃窗口。

    本函数绝不向上抛异常：它可能在"主流程已经失败"的路径上被调用，再冒泡一个异常
    只会盖住原始的 Neo4j 错误，让排障更难。
    """
    from models import db

    try:
        job = stage_job(node_type, node_id, OP_NODE_UPDATE, node_name, properties, error=error)
        db.session.commit()
        return job.id
    except Exception as exc:  # noqa: BLE001 - 记账失败不能盖住原始错误
        db.session.rollback()
        logger.error(f"❌ 补偿队列写入失败（原始错误：{error}）：{exc}")
        return None


def mark_job_done(job) -> None:
    """立即写入成功：结单。"""
    from models import db

    job.status = STATUS_DONE
    job.last_error = ""
    job.next_retry_at = None
    db.session.commit()


def mark_job_failed(job, error: str) -> bool:
    """立即写入失败：记错误、排下一次重试时刻；返回是否已转 abandoned。

    退避**落库**（`next_retry_at`）而不是留在内存：否则进程一重启，所有失败任务的
    退避就归零，一个持续失败的节点会被每一轮重放反复冲击。
    """
    from models import db

    job.attempts = (job.attempts or 0) + 1
    job.last_error = str(error)
    abandoned = job.attempts >= MAX_ATTEMPTS
    if abandoned:
        job.status = STATUS_ABANDONED
        job.next_retry_at = None
        logger.error(f"❌ 同步连续失败 {job.attempts} 次，转人工处理："
                     f"{job.operation} {job.graph_key} 原因：{error}")
    else:
        job.next_retry_at = time.time() + BACKOFF_SECONDS[min(job.attempts - 1,
                                                             len(BACKOFF_SECONDS) - 1)]
        logger.warning(f"⚠️ 本次同步失败，已留待重放（第 {job.attempts} 次）："
                       f"{job.operation} {job.graph_key} 原因：{error}")
    db.session.commit()
    return abandoned


# ---- 重放 ----


def pending_jobs(limit: int = 50):
    """待补偿任务列表（新→旧，跳过已放弃/已作废的）。"""
    from models import Neo4jSyncJob

    return (Neo4jSyncJob.query
            .filter_by(status=STATUS_PENDING)
            .order_by(Neo4jSyncJob.id.desc())
            .limit(max(1, int(limit)))
            .all())


def due_jobs(limit: int = 50, *, now: float | None = None):
    """到期可重放的任务（`next_retry_at` 已过或为空）；新→旧。"""
    from models import Neo4jSyncJob, db

    stamp = time.time() if now is None else now
    return (Neo4jSyncJob.query
            .filter(Neo4jSyncJob.status == STATUS_PENDING)
            .filter((Neo4jSyncJob.next_retry_at.is_(None))
                    | (Neo4jSyncJob.next_retry_at <= stamp))
            .order_by(Neo4jSyncJob.id.desc())
            .limit(max(1, int(limit)))
            .all())


def jobs_summary() -> dict:
    """各状态计数 + 最老 pending 的年龄：给 health/管理台一个"还差多少"的总体数字。

    `oldest_pending_age_seconds` 用 SQLite 的 julianday 在库里算：`created_at` 是
    server_default 写进去的字符串，取回 Python 再解析要处理时区与格式两种不确定，
    而"积压多久了"正是运维最需要一眼看到的那个数字（文档第六节的监控项）。
    """
    from models import db

    try:
        rows = db.session.execute(
            text("SELECT status, COUNT(*) FROM neo4j_sync_jobs GROUP BY status")
        ).fetchall()
        oldest = db.session.execute(text(
            "SELECT (julianday('now') - julianday(MIN(created_at))) * 86400.0 "
            "FROM neo4j_sync_jobs WHERE status = 'pending'"
        )).fetchone()
    except Exception:  # noqa: BLE001 - 表还没建时不该让调用方崩
        db.session.rollback()
        return {"pending": 0, "done": 0, "abandoned": 0, "cancelled": 0,
                "oldest_pending_age_seconds": None, "table_missing": True}
    counts = {row[0]: row[1] for row in rows}
    age = oldest[0] if oldest else None
    return {
        "pending": counts.get(STATUS_PENDING, 0),
        "done": counts.get(STATUS_DONE, 0),
        "abandoned": counts.get(STATUS_ABANDONED, 0),
        "cancelled": counts.get(STATUS_CANCELLED, 0),
        "oldest_pending_age_seconds": None if age is None else round(float(age), 1),
        "dataset_version": current_dataset_version(),
    }


def apply_job(job) -> tuple[bool, str]:
    """把一条任务写进 Neo4j；返回 `(是否成功, 说明)`。

    三类操作都是**幂等**的：upsert 按图谱键 MERGE、删除在节点不存在时也算完成。
    因此"写完但没来得及标 done 就崩溃"不会造成重复写入，重放一次即可。

    成功时**在本函数里就回写** `neo4j_id`（文档第六节第 3 条 B）：这一条以前是缺的，
    重试成功了但 `SQLite.neo4j_id` 还是空，后续更新与删除仍无法可靠定位节点。
    回写与"标 done"是否在同一次提交里不影响正确性——回写只是补上缺失的 id，
    重复执行没有副作用。
    """
    from node_property_mapping import neo4j_props

    operation = getattr(job, "operation", None) or OP_NODE_CREATE
    graph_key = getattr(job, "graph_key", None) or graph_key_for(job.node_type, job.node_id)
    node_type = job.node_type

    try:
        from model_search import neo4j_db

        handle = neo4j_db()
        if operation == OP_NODE_DELETE:
            handle.delete_node_by_graph_key(node_type, graph_key)
            return True, ""

        props = {k: v for k, v in neo4j_props(
            node_type, json.loads(job.properties_json or "{}")).items() if v is not None}
        node_id, _is_new = handle.upsert_node(node_type, graph_key, job.node_name, props)
        if node_id is None:
            return False, "Neo4j 未返回节点 id"
        if operation == OP_NODE_UPDATE and job.from_name and job.from_name != job.node_name:
            # 改名后的残留清理：只动"没有 graph_key"的历史节点，不误伤新体系里同名的其他节点
            handle.drop_legacy_node_without_graph_key(node_type, job.from_name)
        _write_back(job, node_id)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _write_back(job, node_id) -> None:
    """重放成功后回写 neo4j_id / neo4j_element_id。

    这一条以前是缺的（文档第六节第 3 条 B）：重试成功了，但 `SQLite.neo4j_id` 还是空，
    后续的更新与删除仍然无法可靠定位 Neo4j 节点，于是同一节点会被反复重放。
    回写时**不覆盖已有的非空 neo4j_id**：只在原值为空时补上，避免把主存储里正确的
    id 覆盖成一个刚重建的节点 id。
    """
    from models import db

    job.neo4j_element_id = str(node_id) if node_id is not None else None
    model = _model_for(job.node_type)
    if model is not None:
        row = db.session.get(model, job.node_id)
        if row is not None and getattr(row, "neo4j_id", None) in (None, ""):
            row.neo4j_id = node_id


def _model_for(node_type: str):
    """按类型取 ORM 模型；未知类型返回 None（老任务可能来自已删掉的类型）。"""
    from models import Event, Organization, Person, Place

    return {"Event": Event, "Place": Place,
            "Organization": Organization, "Person": Person}.get(node_type)


def retry_job(job) -> tuple[bool, str]:
    """重放单条任务（`apply_job` 的兼容入口）。"""
    return apply_job(job)


def retry_pending(limit: int = 50, *, only_due: bool = False) -> dict:
    """重放一批待补偿任务，返回统计。逐条独立：一条失败不影响后面的
    （否则一个坏节点会永久堵住整个队列）。

    `only_due=True` 时只处理 `next_retry_at` 已到期的——后台循环用它，
    免得刚失败的任务在每一轮里被反复冲击。人工重放（CLI/API）用默认的
    全量口径，运营想立刻再试一次时不该被退避挡着。
    """
    from models import db

    stats = {"attempted": 0, "succeeded": 0, "failed": 0, "abandoned": 0}
    jobs = due_jobs(limit=limit) if only_due else pending_jobs(limit=limit)
    for job in jobs:
        stats["attempted"] += 1
        ok, message = apply_job(job)
        if ok:
            job.status = STATUS_DONE
            job.last_error = ""
            job.next_retry_at = None
            # 与 apply_job 里的返回值保持一条路径：回写也要发生在结单的同一次提交里
            job.attempts = (job.attempts or 0) + 1
            stats["succeeded"] += 1
            logger.info(f"✅ 补偿同步成功：{job.operation} {job.graph_key}"
                        f"（{job.node_name}）")
        else:
            db.session.rollback()
            job.attempts = (job.attempts or 0) + 1
            job.last_error = message
            stats["failed"] += 1
            if job.attempts >= MAX_ATTEMPTS:
                job.status = STATUS_ABANDONED
                job.next_retry_at = None
                stats["abandoned"] += 1
                logger.error(
                    f"❌ 补偿同步已达上限（{MAX_ATTEMPTS} 次），转人工处理："
                    f"{job.operation} {job.graph_key}（{job.node_name}）原因：{message}"
                )
            else:
                job.next_retry_at = time.time() + BACKOFF_SECONDS[
                    min(job.attempts - 1, len(BACKOFF_SECONDS) - 1)]
        db.session.commit()
    return stats
