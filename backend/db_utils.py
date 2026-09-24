from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from models import Event, Organization, Person, Place, UserInfo, db
from node_property_mapping import API_TO_COLUMN, neo4j_props

from logging_util import get_logger

logger = get_logger(__name__)


class DbUtil:
    @staticmethod
    def init_app(app):
        db.init_app(app)
        with app.app_context():
            db.create_all()
            logger.info("SQLite schema initialized")

    def authentication(self, params):
        account = (params or {}).get("account", "")
        password = (params or {}).get("password", "")
        user = UserInfo.query.filter(UserInfo.account == account).first()
        if not user or not password:
            return None

        stored_password = user.password or ""
        if stored_password == password:
            # 存量明文口令：命中即就地升级为哈希，升级后不再走这条分支。
            logger.warning(f"⚠️ 账号 {account} 使用明文口令登录，已自动升级为哈希存储；请改用哈希后的口令。")
            user.password = generate_password_hash(password)
            db.session.commit()
            return user

        if check_password_hash(stored_password, password):
            return user

        return None

    @staticmethod
    def find_user(user_id):
        """按 id 取用户信息字典；不存在返回 None。

        与 get_role / list_users / set_user_role 一样是静态方法：它不使用实例状态
        （原先写成实例方法，`DbUtil.find_user(id)` 会报 "missing 1 required positional
        argument"，只能绕成 `DbUtil().find_user(id)`）。
        """
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        return user.to_dict() if user else None

    @staticmethod
    def get_role(user_id):
        """取用户角色。

        查不到返回空串（按未授权处理）；存量账号 role 为空时按 **viewer** 处理。
        原先回退到 admin（当时的理由是"避免引入角色模型后把原有账号锁成只读"），但角色
        迁移已给存量账号回填过取值，这个兜底只剩风险：任何一次写库遗漏（NULL/空串）都会
        变成静默提权。默认值取最小权限，空值最多让人少看几个页面，不会让人多写几个接口。
        没有应用上下文时同样返回空串——fail-closed，宁可拒绝也不放行。
        """
        if user_id is None:
            return ""
        try:
            user = db.session.get(UserInfo, user_id)
        except RuntimeError:
            return ""
        if user is None:
            return ""
        return user.role or "viewer"

    def add_user(self, data):
        data = data or {}
        account = (data.get("account") or "").strip()
        password = data.get("password") or ""
        name = (data.get("name") or "").strip()
        if not account or not password or not name:
            return {"code": 400, "msg": "用户名、昵称和密码不能为空"}

        # 注册用户一律只读（viewer）；需要写权限的账号由管理员改库中 role。
        params = UserInfo(
            account=account,
            name=name,
            password=generate_password_hash(password),
            role="viewer",
        )
        db.session.add(params)
        try:
            db.session.commit()
        except IntegrityError:
            # account 的唯一约束兜住并发注册（原先"先查后插"存在竞态）
            db.session.rollback()
            return {"code": 500, "msg": "用户账号已存在"}
        return {
            "code": 200,
            "data": {
                "user_id": params.id
            }
        }

    @staticmethod
    def list_users():
        """用户列表（id/账号/昵称/角色），按 id 升序。仅管理员接口调用。"""
        return [user.to_dict() for user in UserInfo.query.order_by(UserInfo.id.asc()).all()]

    @staticmethod
    def set_user_role(user_id, role):
        """改角色并提交；用户不存在返回 None。

        role 的角色白名单校验在路由层做（那里才有请求上下文与错误响应）；
        这里只负责落库。
        """
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        if user is None:
            return None
        user.role = role
        db.session.commit()
        return user.to_dict()

    @staticmethod
    def _get_model_by_type(node_type: str):
        return {
            "Event": Event,
            "Place": Place,
            "Organization": Organization,
            "Person": Person,
        }.get(node_type)

    @staticmethod
    def _find_neo4j_by_name(neo4j_handle, node_type, node_name):
        """按名回退查找 Neo4j 节点，返回 (唯一匹配或 None, 错误说明)。

        只在 SQLite 侧缺 neo4j_id 时才需要。同名同类型多于一个时返回说明而不返回匹配，
        由调用方「宁可不做」——取第一条可能动到另一个对象的节点。
        """
        results = neo4j_handle.search_nodes_by_name(node_name, limit=10)
        matches = [
            item for item in ((results or {}).get("nodes") or [])
            if item.get("type") == node_type
        ]
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            return None, (
                f"Neo4j 中有 {len(matches)} 个同名同类型节点（{node_type}/{node_name}），"
                "需人工核对后再处理（本次未改动 Neo4j）"
            )
        return None, "Neo4j 未找到对应节点"

    @staticmethod
    def _delete_neo4j_by_name(neo4j_handle, node_type, node_name):
        """按名回退删除：唯一匹配才删。返回 (是否成功, 错误说明)。"""
        match, error = DbUtil._find_neo4j_by_name(neo4j_handle, node_type, node_name)
        if match is None:
            return False, error
        neo4j_handle.delete_node(node_type, match["id"])
        return True, ""

    @staticmethod
    def _update_neo4j_by_name(neo4j_handle, node_type, node_name, new_name):
        """按名回退改名：唯一匹配才改。返回 (匹配到的 Neo4j 节点 id 或 None, 错误说明)。"""
        match, error = DbUtil._find_neo4j_by_name(neo4j_handle, node_type, node_name)
        if match is None:
            return None, error
        neo4j_handle.update_node(node_type, match["id"], new_name)
        return match["id"], ""

    @staticmethod
    def _field_mapping_by_type(node_type: str):
        """API 键 → SQLite 列名（映射表在 node_property_mapping，三处重复已收敛）。"""
        return API_TO_COLUMN.get(node_type, {})

    @staticmethod
    def _normalize_payload(node_type: str, data: dict):
        mapping = DbUtil._field_mapping_by_type(node_type)
        normalized = {}
        for key, value in (data or {}).items():
            mapped_key = mapping.get(key, key.lower())
            normalized[mapped_key] = value
        return normalized

    @staticmethod
    def _neo4j_props(node_type: str, data: dict):
        """SQLite 列名 → Neo4j 属性名（映射表在 node_property_mapping）。"""
        return neo4j_props(node_type, data)

    @staticmethod
    def create_node(node_type: str, name: str, properties: dict = None):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            data = {"name": name}
            if properties:
                data.update(properties)
            mapped_data = DbUtil._normalize_payload(node_type, data)

            valid_columns = {c.name for c in model.__table__.columns}
            filtered_data = {k: v for k, v in mapped_data.items() if k in valid_columns}
            new_node = model(**filtered_data)
            db.session.add(new_node)
            db.session.commit()

            neo4j_sync_success = False
            neo4j_error_msg = ""
            neo4j_id = None
            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                neo4j_node = neo4j_handle.create_node(node_type, new_node.name)
                if neo4j_node and hasattr(neo4j_node, "identity"):
                    neo4j_id = neo4j_node.identity
                    props = {k: v for k, v in DbUtil._neo4j_props(node_type, filtered_data).items() if v is not None}
                    if props:
                        neo4j_handle.update_node_properties(neo4j_id, props)
                    new_node.neo4j_id = neo4j_id
                    db.session.commit()
                    neo4j_sync_success = True
                else:
                    neo4j_error_msg = "Neo4j node create returned no identity"
            except Exception as sync_err:
                neo4j_error_msg = str(sync_err)
                db.session.rollback()

            return {
                "code": 200,
                "msg": "创建成功" if neo4j_sync_success else f"创建成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": new_node.id,
                    "type": node_type,
                    "name": new_node.name,
                    "neo4j_id": neo4j_id,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                },
            }
        except Exception as e:
            db.session.rollback()
            return {"code": 500, "msg": f"创建失败: {str(e)}"}

    @staticmethod
    def update_node(node_type: str, node_id: int, new_name: str, properties: dict = None):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = model.query.get(node_id)
            if not node:
                return {"code": 404, "msg": "节点不存在"}

            old_name = node.name
            node.name = new_name

            normalized_properties = DbUtil._normalize_payload(node_type, properties or {})
            for mapped_key, value in normalized_properties.items():
                if hasattr(node, mapped_key):
                    setattr(node, mapped_key, value)
            db.session.commit()

            neo4j_sync_success = False
            neo4j_error_msg = ""
            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                neo4j_id = getattr(node, "neo4j_id", None)
                if neo4j_id:
                    neo4j_handle.update_node(node_type, neo4j_id, new_name)
                    if normalized_properties:
                        neo4j_handle.update_node_properties(
                            neo4j_id,
                            {k: v for k, v in DbUtil._neo4j_props(node_type, {"name": new_name, **normalized_properties}).items() if v is not None},
                        )
                    neo4j_sync_success = True
                else:
                    # 回退按名查找只在 neo4j_id 缺失时才走；同名同类型多于一个时不动，
                    # 交人工核对——取第一条可能改到另一个对象的节点上。
                    found_id, find_error = DbUtil._update_neo4j_by_name(
                        neo4j_handle, node_type, old_name, new_name
                    )
                    if found_id is None:
                        neo4j_error_msg = find_error
                    else:
                        if normalized_properties:
                            neo4j_handle.update_node_properties(
                                found_id,
                                {k: v for k, v in DbUtil._neo4j_props(node_type, {"name": new_name, **normalized_properties}).items() if v is not None},
                            )
                        node.neo4j_id = found_id
                        db.session.commit()
                        neo4j_sync_success = True
            except Exception as sync_err:
                # 不再静默吞掉：把真实失败原因带回响应，避免不一致悄悄累积
                neo4j_error_msg = str(sync_err)

            return {
                "code": 200,
                "msg": "更新成功" if neo4j_sync_success else f"更新成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    "type": node_type,
                    "name": new_name,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception as e:
            db.session.rollback()
            return {"code": 500, "msg": f"更新失败: {str(e)}"}

    @staticmethod
    def delete_node(node_type: str, node_id: int):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = model.query.get(node_id)
            if not node:
                return {"code": 404, "msg": "节点不存在"}

            neo4j_id = getattr(node, "neo4j_id", None)
            node_name = node.name

            # 先删主存储（SQLite）并提交：主存储删除失败时直接返回错误、不动 Neo4j，
            # 不会留下「Neo4j 已删、SQLite 还在」的永久不一致。
            db.session.delete(node)
            db.session.commit()

            neo4j_sync_success = False
            neo4j_error_msg = ""
            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                if neo4j_id:
                    neo4j_handle.delete_node(node_type, neo4j_id)
                    neo4j_sync_success = True
                else:
                    # 仅在没有 neo4j_id 时才按名回退查找。同名同类型多于一个时宁可不动，
                    # 让人工核对后处理——取第一条可能删掉另一个对象的节点。
                    neo4j_sync_success, neo4j_error_msg = DbUtil._delete_neo4j_by_name(
                        neo4j_handle, node_type, node_name
                    )
            except Exception as sync_err:
                neo4j_error_msg = str(sync_err)

            return {
                "code": 200,
                "msg": "删除成功" if neo4j_sync_success else f"删除成功（Neo4j同步删除失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception as e:
            db.session.rollback()
            return {"code": 500, "msg": f"删除失败: {str(e)}"}

    @staticmethod
    def update_node_properties(node_id: int, node_type: str, properties: dict):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = model.query.get(node_id)
            if not node:
                return {"code": 404, "msg": "节点不存在"}

            updatable_fields = {
                "name", "dynasty", "modern_name", "province", "city", "district",
                "org", "role", "remark", "start_date", "end_date", "place",
                "aggressor", "defender", "result", "impact", "source", "scale",
                "action", "event_type", "person", "description", "specific_location",
                "relations", "longitude", "latitude", "coord_source", "coord_confidence", "coord_note",
            }
            mapped_properties = DbUtil._normalize_payload(node_type, properties)
            for mapped_key, value in mapped_properties.items():
                if mapped_key in updatable_fields and hasattr(node, mapped_key):
                    if mapped_key in {"longitude", "latitude"}:
                        value = None if value in (None, "") else float(value)
                    setattr(node, mapped_key, value)
            db.session.commit()

            neo4j_sync_success = False
            neo4j_error_msg = ""
            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                neo4j_id = getattr(node, "neo4j_id", None)
                if neo4j_id:
                    neo4j_handle.update_node_properties(
                        neo4j_id,
                        {k: v for k, v in DbUtil._neo4j_props(node_type, mapped_properties).items() if v is not None},
                    )
                    neo4j_sync_success = True
                else:
                    neo4j_error_msg = "节点没有 neo4j_id，无法同步到 Neo4j"
            except Exception as sync_err:
                neo4j_error_msg = str(sync_err)

            return {
                "code": 200,
                "msg": "属性更新成功" if neo4j_sync_success else f"属性更新成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception as e:
            db.session.rollback()
            return {"code": 500, "msg": f"属性更新失败: {str(e)}"}

    @staticmethod
    def find_node_page(current: int, limit: int, name_query: str = "", node_type: str = None):
        try:
            if node_type:
                model = DbUtil._get_model_by_type(node_type)
                if not model:
                    return {"total": 0, "records": []}

                q = model.query
                if name_query:
                    q = q.filter(model.name.contains(name_query))
                total = q.count()
                nodes = q.offset((current - 1) * limit).limit(limit).all()
                records = []
                for node in nodes:
                    node_dict = node.to_dict()
                    node_dict["type"] = node_type
                    records.append(node_dict)
                return {"total": total, "records": records}

            all_records = []
            total_count = 0
            for model in [Event, Place, Organization, Person]:
                q = model.query
                if name_query:
                    q = q.filter(model.name.contains(name_query))
                total_count += q.count()
                for node in q.all():
                    node_dict = node.to_dict()
                    node_dict["type"] = model.__name__
                    all_records.append(node_dict)

            start = (current - 1) * limit
            end = start + limit
            return {"total": total_count, "records": all_records[start:end]}
        except Exception as e:
            return {"total": 0, "records": [], "error": str(e)}

    @staticmethod
    def get_node_detail_sqlite(node_id: int, node_type: str):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return None
            node = model.query.get(node_id)
            if node:
                result = node.to_dict()
                result["type"] = node_type
                return result
            return None
        except Exception:
            return None
