"""
SQLAlchemy数据模型定义

功能说明:
    - 定义SQLite数据库表结构（ORM模型）
    - 实体表：Event（事件）、Place（地点）、Organization（组织）、Person（人物）
    - 关系表：Event-Event、Event-Organization、Event-Place、Event-Person
    - 用户表：UserInfo
    - 提供to_dict()方法用于序列化

数据库架构:
    - SQLite + WAL模式
    - 支持高并发读写
    - 通过neo4j_id字段与Neo4j关联
"""

# 导入 Flask-SQLAlchemy
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _base_with_meta(model_obj, payload: dict):
    payload['id'] = model_obj.id
    payload['neo4j_id'] = getattr(model_obj, 'neo4j_id', None)
    payload['created_at'] = getattr(model_obj, 'created_at', None)
    return payload


class UserInfo(db.Model):
    """
    用户信息表模型

    role 是权限角色：admin / editor 可写，viewer 只读。
    注册接口一律建 viewer；角色值由管理员的用户管理页下发，落库前过白名单。
    role 为空的历史行按 **viewer** 处理（与 DbUtil.get_role 同口径）——默认取最小权限，
    空值只让人少看几个页面，不会让人多写几个接口。

    token_version / disabled 是"让还没过期的 token 提前失效"的两个开关：
    JWT 在过期前一直有效，改密码或封号后旧凭证仍然能用到自然过期。改这两列后
    签发时带的 `ver` 与库里的取值不再一致（或账号已被禁用），旧 token 立即失效。
    """
    __tablename__ = 'UserInfo'

    id = db.Column(db.Integer, primary_key=True)
    account = db.Column(db.String(255), unique=True)
    password = db.Column(db.String(255))
    name = db.Column(db.String(255))
    role = db.Column(db.String(32), default='viewer')
    # 每次"强制下线"（改密码、封号、管理员踢人）+1；token 里的 ver 与它不符即拒绝。
    # 默认 1 而不是 0：0 与"空值"在 SQLite 里容易混，用一个非零起点省掉一类歧义。
    token_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    # 禁用账号：即便 token 未过期也一律拒绝，且不接受登录。
    disabled = db.Column(db.Boolean, nullable=False, default=False, server_default='0')

    def to_dict(self):
        return {
            'id': self.id,
            'account': self.account,
            'name': self.name,
            # 必须与 DbUtil.get_role 的兜底一致：否则用户管理页会把一个空角色账号
            # 显示成"管理员"，而接口实际按 viewer 放行——界面与权限对不上。
            'role': self.role or 'viewer',
            'disabled': bool(self.disabled),
        }


# ==================== 数据迁移后的存储 ====================

class Event(db.Model):
    """
    事件实体表 - 基于事件抽取文档规范

    核心字段（文档要求）：
    - name: 对应 EventName（事件名称）
    - event_type: 对应 EventType（事件类型枚举）
    - start_date: 对应 StartDate（开始时间）
    - end_date: 对应 EndDate（结束时间）
    - dynasty: 对应 DynastyName（所属朝代）
    - place: 对应 Place（发生地点，多个用顿号隔开）
    - aggressor: 对应 Aggressor（发起方）
    - defender: 对应 Defender（防守方）
    - person: 对应 Person（涉及人物）
    - action: 对应 Action（主要行为：交战、擒杀等）
    - result: 对应 Result（事件结果）
    - scale: 对应 Scale（规模：兵力、时间、地理范围、伤亡）
    - impact: 对应 Impact（历史影响）
    - source: 对应 source（来源文献）
    - relations: 对应 Relations（与其他事件关系：因果、顺承、并列、包含、条件）
    - remark: 对应 Remark（备注）
    """
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    neo4j_id = db.Column(db.Integer, index=True)

    # 核心字段（文档规范）
    name = db.Column(db.String(255), nullable=False)  # EventName: 事件名称
    event_type = db.Column(db.String(100))  # EventType: 事件类型（农民起义、统一战争等）
    start_date = db.Column(db.String(50))  # StartDate: 开始时间
    end_date = db.Column(db.String(50))  # EndDate: 结束时间
    dynasty = db.Column(db.String(50))  # DynastyName: 所属朝代
    place = db.Column(db.String(255))  # Place: 发生地点
    aggressor = db.Column(db.String(255))  # Aggressor: 发起方
    defender = db.Column(db.String(255))  # Defender: 防守方
    person = db.Column(db.String(255))  # Person: 涉及人物（顿号隔开）
    action = db.Column(db.String(50))  # Action: 主要行为（交战、擒杀等）
    result = db.Column(db.Text)  # Result: 事件结果
    scale = db.Column(db.String(255))  # Scale: 规模描述
    impact = db.Column(db.Text)  # Impact: 历史影响
    source = db.Column(db.String(255))  # source: 来源文献
    relations = db.Column(db.String(255))  # Relations: 与其他事件关系
    remark = db.Column(db.Text)  # Remark: 备注

    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

    def to_dict(self):
        return _base_with_meta(self, {
            'EventName': self.name,
            'EventType': self.event_type,
            'StartDate': self.start_date,
            'EndDate': self.end_date,
            'DynastyName': self.dynasty,
            'Place': self.place,
            'Aggressor': self.aggressor,
            'Defender': self.defender,
            'KeyPersons': self.person,
            'Action': self.action,
            'Result': self.result,
            'TroopSize': self.scale,
            'Impact': self.impact,
            'source_text': self.source,
            'relations': self.relations,
            'Remark': self.remark,
        })


class Place(db.Model):
    """
    地点实体表
    """
    __tablename__ = 'places'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    neo4j_id = db.Column(db.Integer, index=True)
    name = db.Column(db.String(255), nullable=False)
    modern_name = db.Column(db.String(255))
    dynasty = db.Column(db.String(100))
    province = db.Column(db.String(100))
    city = db.Column(db.String(100))
    district = db.Column(db.String(100))
    specific_location = db.Column(db.String(255))
    longitude = db.Column(db.Float)
    latitude = db.Column(db.Float)
    coord_source = db.Column(db.String(100))
    coord_confidence = db.Column(db.String(50))
    coord_note = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

    def to_dict(self):
        return _base_with_meta(self, {
            'geo_name': self.name,
            'modern_name': self.modern_name,
            'DynastyName': self.dynasty,
            'Province': self.province,
            'City': self.city,
            'District_County': self.district,
            'Specific_location': self.specific_location,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'coord_source': self.coord_source,
            'coord_confidence': self.coord_confidence,
            'coord_note': self.coord_note,
        })


class Organization(db.Model):
    """
    组织实体表 - 严格基于事件抽取文档规范

    核心字段（文档要求）：
    - name: 对应 OrgName（组织名称）
    - org_type: 对应 OrgType（组织类型：国家、部落、起义军、联盟、地方势力、中央政权）
    - dynasty: 对应 DynastyName（所属朝代）

    可选扩展字段：
    - description: 组织简介
    - remark: 备注
    """
    __tablename__ = 'organizations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    neo4j_id = db.Column(db.Integer, index=True)

    # 核心字段（文档规范）
    name = db.Column(db.String(255), nullable=False)  # OrgName: 组织名称
    org_type = db.Column(db.String(50))  # OrgType: 组织类型
    dynasty = db.Column(db.String(50))  # DynastyName: 所属朝代

    # 可选扩展字段
    description = db.Column(db.Text)  # 组织简介
    remark = db.Column(db.Text)  # 备注

    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

    def to_dict(self):
        return _base_with_meta(self, {
            'OrgName': self.name,
            'OrgType': self.org_type,
            'DynastyName': self.dynasty,
            'Description': self.description,
            'Remark': self.remark,
        })


class Person(db.Model):
    """
    人物实体表
    """
    __tablename__ = 'persons'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    neo4j_id = db.Column(db.Integer, index=True)
    name = db.Column(db.String(255), nullable=False)
    dynasty = db.Column(db.String(100))
    org = db.Column(db.String(255))
    role = db.Column(db.String(255))
    remark = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

    def to_dict(self):
        return _base_with_meta(self, {
            'PersonName': self.name,
            'DynastyName': self.dynasty,
            'OrgName': self.org,
            'Role': self.role,
            'Remark': self.remark,
        })


# ==================== 关系表 ====================
#
# 四张关系表的外键全部声明 ondelete='CASCADE'：删掉一个事件/地点/人物/组织时，
# 引用它的关系行由 SQLite 一并删除，不会留下指向不存在 id 的孤儿行。
#
# 注意这只对**新建的表**生效（DDL 在 CREATE TABLE 时写入）——`create_all()` 不会去改
# 已存在的表。存量库的外键没有这条子句，靠的是 DbUtil.delete_node 在业务层先删关系行
# 再删实体行（配合 app.py 的连接钩子里 `PRAGMA foreign_keys=ON`，删错顺序会直接报错
# 而不是静默留下脏数据）。两条路径见 backend/tests/test_node_delete_cascade.py。

class EventEventRelation(db.Model):
    """事件-事件关系表"""
    __tablename__ = 'event_event_relations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_a_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    event_b_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    event_a_name = db.Column(db.String(255))
    event_b_name = db.Column(db.String(255))
    relation_type = db.Column(db.String(100), nullable=False)
    neo4j_event_a_id = db.Column(db.Integer)
    neo4j_event_b_id = db.Column(db.Integer)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


class EventOrganizationRel(db.Model):
    """事件-组织关系表"""
    __tablename__ = 'event_organization_rel'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    org_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    event_name = db.Column(db.String(255))
    org_name = db.Column(db.String(255))
    relation_type = db.Column(db.String(100), nullable=False)
    neo4j_event_id = db.Column(db.Integer)
    neo4j_org_id = db.Column(db.Integer)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


class EventPlaceRelation(db.Model):
    """事件-地点关系表"""
    __tablename__ = 'event_place_relations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    place_id = db.Column(db.Integer, db.ForeignKey('places.id', ondelete='CASCADE'), nullable=False)
    event_name = db.Column(db.String(255))
    place_name = db.Column(db.String(255))
    modern_name = db.Column(db.String(255))
    relation_type = db.Column(db.String(100), nullable=False)
    evidence = db.Column(db.Text)
    source_type = db.Column(db.String(50))
    confidence = db.Column(db.String(50))
    neo4j_event_id = db.Column(db.Integer)
    neo4j_place_id = db.Column(db.Integer)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


class EventPersonRelation(db.Model):
    """事件-人物关系表"""
    __tablename__ = 'event_person_relations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete='CASCADE'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id', ondelete='CASCADE'), nullable=False)
    event_name = db.Column(db.String(255))
    person_name = db.Column(db.String(255))
    relation_type = db.Column(db.String(100), nullable=False)
    neo4j_event_id = db.Column(db.Integer)
    neo4j_person_id = db.Column(db.Integer)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


# ==================== 双写补偿（outbox） ====================
#
# SQLite 是主存储，Neo4j 是可视图谱；写节点/属性时先提交 SQLite、再调 Neo4j。
# 只回一句 sync_status=failed 是不够的——没有重试、没有记录，
# 管理台列表与图谱会长期不一致，而且**没人知道差在哪一条**（质检只能发现数量差）。
#
# 这张表就是那个"待办清单"：同步失败时落一条记录，重试成功后标记完成。
# 用 SQLite 而不是内存队列：进程重启（部署、崩溃）不该丢掉待补偿的写入。
class Neo4jSyncJob(db.Model):
    """一条待补偿的 Neo4j 同步任务（事务型 outbox，文档第六节）。

    ## 它承担的角色

    "业务修改"与"这条待办"必须在**同一次 SQLite 提交**里落库（见
    `sync_compensation.stage_job`）：崩溃发生在提交之后时，任务还在表里，
    后台 worker 会把它补上；若改成"先提交业务、再调 Neo4j、失败后才记一条"，
    提交后崩溃就会永久不一致且无人知晓。

    表名保留 `neo4j_sync_jobs`（文档里建议的名字是 `neo4j_outbox`）：改名要迁移存量行，
    而它的职责与文档描述的是同一件事——改名带来的收益只是名字更好听。

    ## operation 覆盖的六种动作

    `node_create` / `node_update` / `node_delete`（关系四类见下方边界说明）。
    不能只留一种 "upsert"：更新失败重放时按新名字创建/合并，**会留下旧节点并造出重复**，
    因为任务里既没有原 Neo4j id 也没有操作类型。这两样都必须记下来。

    ## 边界（如实说）

    **关系（四类）目前不进本队列**：关系的双写由 `sync_sqlite_to_neo4j.py` 的全量/增量
    同步承担。要把它纳入 outbox 需要先给关系行一个稳定图谱键（关系没有主键可对标，
    得用两端 graph_key + 关系类型拼），那是一次独立的重构；当前先用
    "节点级 outbox + 关系靠全量同步兜底"这个组合，并在文档里标明。
    """
    __tablename__ = 'neo4j_sync_jobs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    node_type = db.Column(db.String(32), nullable=False)     # Event / Place / ...
    node_id = db.Column(db.Integer, nullable=False)          # SQLite 主键
    node_name = db.Column(db.String(255))                    # 落库时的名字（重放时定位用）
    # 稳定图谱键 `<Type>:<id>`（见 graph_key.py）。重放一律按它定位，不再按名字猜：
    # 按名字定位在"同名节点有多个"时无法判断该动哪一个，改名后又会被当成新建。
    graph_key = db.Column(db.String(64), index=True)
    # node_create / node_update / node_delete
    operation = db.Column(db.String(24), nullable=False, default='node_create', index=True)
    # 变更**之前**的名字。重命名重放时要用它清理旧节点（否则旧名字的节点会留在图谱里）
    from_name = db.Column(db.String(255))
    # 最后一次成功写入时的 Neo4j 内部 id：只作观测与排障，不再作为定位依据
    neo4j_element_id = db.Column(db.String(64))
    # 属性快照（JSON 字符串）：重放时按当时的属性重放，而不是重新读一遍当前行——
    # 后者会把"之后的修改"也一起写过去，掩盖掉这次到底差了什么。
    properties_json = db.Column(db.Text)
    last_error = db.Column(db.Text)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    # 数据版本（文档第六节第 3 条 F）：知识数据重导后旧任务必须作废，否则会把上一批
    # 数据重新写进图谱。取值为"重导代数"（见 sync_compensation.current_dataset_version）。
    dataset_version = db.Column(db.Integer)
    # 下次可重试时刻（unix 秒，指数退避）。未到期的不参与重放，避免失败任务占满每一轮。
    next_retry_at = db.Column(db.Float, index=True)
    # pending（待补偿） / done（已补齐） / abandoned（超过上限，需人工处理）
    # + cancelled（数据重导等原因作废，不再重放）
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())

    def to_dict(self):
        return {
            'id': self.id,
            'node_type': self.node_type,
            'node_id': self.node_id,
            'node_name': self.node_name,
            'graph_key': self.graph_key,
            'operation': self.operation,
            'attempts': self.attempts,
            'status': self.status,
            'dataset_version': self.dataset_version,
            'next_retry_at': self.next_retry_at,
            'last_error': self.last_error,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None,
        }


class AppMeta(db.Model):
    """极小的键值表：放"整库级"的少量状态。

    第一个用途是**数据重导代数**（`dataset_generation`）：每次从 JSON 重导知识数据时 +1，
    补偿队列据此判断哪些任务属于上一批数据、该作废（文档第六节第 3 条 F）。
    单独开一张表而不是塞进某个业务表：它是全库级的元信息，挂在任何实体上都不合适；
    而为一个整数引入 Redis 也不合适。
    """
    __tablename__ = 'app_meta'

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(255))
    updated_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())
