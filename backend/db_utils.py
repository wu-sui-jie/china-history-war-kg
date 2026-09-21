from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from models import Event, Organization, Person, Place, UserInfo, db


class DbUtil:
    @staticmethod
    def init_app(app):
        db.init_app(app)
        with app.app_context():
            db.create_all()
            print("SQLite schema initialized")

    def authentication(self, params):
        account = (params or {}).get("account", "")
        password = (params or {}).get("password", "")
        user = UserInfo.query.filter(UserInfo.account == account).first()
        if not user or not password:
            return None

        stored_password = user.password or ""
        if stored_password == password:
            user.password = generate_password_hash(password)
            db.session.commit()
            return user

        if check_password_hash(stored_password, password):
            return user

        return None

    def find_user(self, user_id):
        user = UserInfo.query.get(user_id)
        return user.to_dict() if user else None

    def add_user(self, data):
        exists = UserInfo.query.filter(UserInfo.account == data.get("account")).all()
        if exists:
            return {"code": 500, "msg": "用户账号已存在"}

        account = (data or {}).get("account", "").strip()
        password = (data or {}).get("password", "")
        name = (data or {}).get("name", "").strip()
        if not account or not password or not name:
            return {"code": 400, "msg": "用户名、昵称和密码不能为空"}

        params = UserInfo(
            account=account,
            name=name,
            password=generate_password_hash(password)
        )
        db.session.add(params)
        db.session.commit()
        return {
            "code": 200,
            "data": {
                "user_id": params.id
            }
        }

    @staticmethod
    def _get_model_by_type(node_type: str):
        return {
            "Event": Event,
            "Place": Place,
            "Organization": Organization,
            "Person": Person,
        }.get(node_type)

    @staticmethod
    def _field_mapping_by_type(node_type: str):
        return {
            "Event": {
                "EventName": "name",
                "EventType": "event_type",
                "StartDate": "start_date",
                "EndDate": "end_date",
                "DynastyName": "dynasty",
                "Place": "place",
                "Aggressor": "aggressor",
                "Defender": "defender",
                "KeyPersons": "person",
                "Action": "action",
                "Result": "result",
                "TroopSize": "scale",
                "Impact": "impact",
                "source_text": "source",
                "Remark": "remark",
                "relations": "relations",
            },
            "Place": {
                "geo_name": "name",
                "DynastyName": "dynasty",
                "Province": "province",
                "City": "city",
                "District_County": "district",
                "Specific_location": "specific_location",
                "Specific_Location": "specific_location",
                "modern_name": "modern_name",
                "ModernName": "modern_name",
                "longitude": "longitude",
                "latitude": "latitude",
                "coord_source": "coord_source",
                "coord_confidence": "coord_confidence",
                "coord_note": "coord_note",
            },
            "Organization": {
                "OrgName": "name",
                "OrgType": "org_type",
                "DynastyName": "dynasty",
                "Description": "description",
                "Remark": "remark",
            },
            "Person": {
                "PersonName": "name",
                "DynastyName": "dynasty",
                "OrgName": "org",
                "Role": "role",
                "Remark": "remark",
            },
        }.get(node_type, {})

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
        if node_type == "Event":
            return {
                "EventType": data.get("event_type"),
                "StartDate": data.get("start_date"),
                "EndDate": data.get("end_date"),
                "DynastyName": data.get("dynasty"),
                "Place": data.get("place"),
                "Aggressor": data.get("aggressor"),
                "Defender": data.get("defender"),
                "KeyPersons": data.get("person"),
                "Action": data.get("action"),
                "Result": data.get("result"),
                "TroopSize": data.get("scale"),
                "Impact": data.get("impact"),
                "source_text": data.get("source"),
                "relations": data.get("relations"),
                "Remark": data.get("remark"),
            }
        if node_type == "Place":
            return {
                "geo_name": data.get("name"),
                "modern_name": data.get("modern_name"),
                "DynastyName": data.get("dynasty"),
                "Province": data.get("province"),
                "City": data.get("city"),
                "District_County": data.get("district"),
                "Specific_location": data.get("specific_location"),
                "longitude": data.get("longitude"),
                "latitude": data.get("latitude"),
                "coord_source": data.get("coord_source"),
                "coord_confidence": data.get("coord_confidence"),
                "coord_note": data.get("coord_note"),
            }
        if node_type == "Organization":
            return {
                "OrgName": data.get("name"),
                "OrgType": data.get("org_type"),
                "DynastyName": data.get("dynasty"),
                "Description": data.get("description"),
                "Remark": data.get("remark"),
            }
        if node_type == "Person":
            return {
                "PersonName": data.get("name"),
                "DynastyName": data.get("dynasty"),
                "OrgName": data.get("org"),
                "Role": data.get("role"),
                "Remark": data.get("remark"),
            }
        return {}

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
                else:
                    results = neo4j_handle.search_nodes_by_name(old_name, limit=1)
                    if results and results.get("nodes"):
                        neo4j_id = results["nodes"][0]["id"]
                        neo4j_handle.update_node(node_type, neo4j_id, new_name)
                        if normalized_properties:
                            neo4j_handle.update_node_properties(
                                neo4j_id,
                                {k: v for k, v in DbUtil._neo4j_props(node_type, {"name": new_name, **normalized_properties}).items() if v is not None},
                            )
                        node.neo4j_id = neo4j_id
                        db.session.commit()
            except Exception:
                pass

            return {
                "code": 200,
                "msg": "更新成功",
                "data": {
                    "id": node_id,
                    "type": node_type,
                    "name": new_name,
                    "sync_status": "success",
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
            neo4j_sync_success = False

            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                if neo4j_id:
                    neo4j_handle.delete_node(node_type, neo4j_id)
                    neo4j_sync_success = True
                else:
                    results = neo4j_handle.search_nodes_by_name(node_name, limit=1)
                    if results and results.get("nodes"):
                        neo4j_handle.delete_node(node_type, results["nodes"][0]["id"])
                        neo4j_sync_success = True
            except Exception:
                pass

            db.session.delete(node)
            db.session.commit()
            return {
                "code": 200,
                "msg": "删除成功" if neo4j_sync_success else "删除成功（Neo4j同步删除失败）",
                "data": {
                    "id": node_id,
                    "sync_status": "success" if neo4j_sync_success else "failed",
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

            try:
                from model_search import neo4j_db

                neo4j_handle = neo4j_db()
                neo4j_id = getattr(node, "neo4j_id", None)
                if neo4j_id:
                    neo4j_handle.update_node_properties(
                        neo4j_id,
                        {k: v for k, v in DbUtil._neo4j_props(node_type, mapped_properties).items() if v is not None},
                    )
            except Exception:
                pass

            return {"code": 200, "msg": "属性更新成功", "data": {"id": node_id, "sync_status": "success"}}
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
