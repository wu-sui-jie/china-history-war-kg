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
    注册接口一律建 viewer；存量账号（role 为空）按 admin 处理，
    避免升级后把原有账号锁成只读。
    """
    __tablename__ = 'UserInfo'

    id = db.Column(db.Integer, primary_key=True)
    account = db.Column(db.String(255), unique=True)
    password = db.Column(db.String(255))
    name = db.Column(db.String(255))
    role = db.Column(db.String(32), default='viewer')

    def to_dict(self):
        return {
            'id': self.id,
            'account': self.account,
            'name': self.name,
            'role': self.role or 'admin'
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

class EventEventRelation(db.Model):
    """事件-事件关系表"""
    __tablename__ = 'event_event_relations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_a_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    event_b_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
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
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    org_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
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
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    place_id = db.Column(db.Integer, db.ForeignKey('places.id'), nullable=False)
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
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    event_name = db.Column(db.String(255))
    person_name = db.Column(db.String(255))
    relation_type = db.Column(db.String(100), nullable=False)
    neo4j_event_id = db.Column(db.Integer)
    neo4j_person_id = db.Column(db.Integer)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())
