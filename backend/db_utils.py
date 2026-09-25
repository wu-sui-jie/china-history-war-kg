from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    Event,
    EventEventRelation,
    EventOrganizationRel,
    EventPersonRelation,
    EventPlaceRelation,
    Organization,
    Person,
    Place,
    UserInfo,
    db,
)
from node_property_mapping import API_TO_COLUMN, neo4j_props
# outbox 的操作类型常量：写路径与重放路径必须用同一套取值，否则重放会走错分支
# （例如把删除当成 upsert，把已经删掉的节点又建回图谱）。
from sync_compensation import OP_NODE_CREATE, OP_NODE_DELETE, OP_NODE_UPDATE

from logging_util import get_logger

# 口令策略的唯一实现（第 13 轮复核整改 §2.1）：数值与判定都在 password_policy 里，
# 注册 / 改密码 / 首管命令 / 前端提示共用同一份口径。这里转出两个常量只是为了
# 兼容既有引用（`from db_utils import MIN_PASSWORD_LENGTH`），新代码请直接引用该模块。
from password_policy import (  # noqa: F401  （转出以兼容既有引用）
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    password_problem,
)

logger = get_logger(__name__)


def _enqueue_compensation(node_type, node_id, node_name, properties, error):
    """把一次失败的 Neo4j 同步登记为待补偿任务**并自行提交**。

    **第 13 轮起优先用同事务路径**（`_stage_sync_job` + 一次 commit）；本函数只保留给
    "事后补记"的场景（例如导入脚本在事务外发现对不上时）。
    """
    from sync_compensation import enqueue_failed_sync

    return enqueue_failed_sync(node_type, node_id, node_name, properties, error)


def _stage_sync_job(node_type, node_id, operation, node_name, properties,
                    *, from_name=None, error=""):
    """把待补偿任务挂到**当前事务**上（不提交）；返回 job。

    这是事务型 outbox 的入口（文档第六节第 4 条）。调用方必须在
    `db.session.commit()` 之前调用它，让"业务修改 + 待办"落进同一次原子提交。
    """
    from sync_compensation import stage_job

    return stage_job(node_type, node_id, operation, node_name, properties,
                     from_name=from_name, error=error)


def _try_sync_now(job, node_type, node_id, *, graph_key, name, properties,
                  from_name=None):
    """提交后立刻尝试把这次修改同步到 Neo4j；返回 `(是否成功, 错误说明, neo4j_id)`。

    成功 → 结单；失败 → 记错误并排下一次重试时刻（退避落库）。
    **这条路径不再是"唯一的同步手段"**：任务已经和业务修改一起提交了，
    这里只是让绝大多数请求不必等后台 worker（用户体验与原先完全一致）。
    """
    import sync_compensation as sc

    ok, message = sc.apply_job(job)
    if ok:
        sc.mark_job_done(job)
        return True, "", getattr(job, "neo4j_element_id", None)
    sc.mark_job_failed(job, message)
    return False, message, None


# 由数据库维护、**不接受外部指定**的列（第 14 轮审计 P2-5）。
#
# 它们都是"真实的列"，所以"只按列名过滤"挡不住：editor 可以在 create_node 的请求体里
# 塞 `id` 指定主键，塞 `created_at` 伪造入库时间。
#   - id：自增序列被污染；更麻烦的是 `graph_key = "<Type>:<id>"` 是**稳定键**，
#     删除后 id 会被复用，旧的 pending 任务就可能指向另一个新节点；
#   - neo4j_id：图谱侧标识由同步流程回写，外部填了只会让对账错位；
#   - created_at：质检报表按它统计"最近新增"，可伪造即报表不可信。
MANAGED_COLUMNS = frozenset({"id", "neo4j_id", "created_at"})


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
        # 口令强度是注册路径也要过的门（第 13 轮整改）：只在改密码时管长度，等于
        # "弱口令只要一开始就设好，就永远不用改"。
        problem = self._password_policy_problem(password)
        if problem:
            return {"code": 400, "msg": problem}

        # 先查一次存在性作为**可读的**第一道（第 14 轮审计 P2-6）：
        # 唯一索引是并发下的真正防线（下面那个 IntegrityError），但它给不出"哪个账号重复"。
        # 而且索引可能因为存量重复数据而**没建上**（见 app.py 的启动迁移），
        # 那时没有这一步就等于重复账号不再被拦。
        if db.session.query(UserInfo.id).filter_by(account=account).first() is not None:
            return {"code": 409, "msg": "该账号已存在，请换一个或直接登录"}

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
            # account 的唯一约束兜住并发注册（"先查后插"之间存在竞态）
            db.session.rollback()
            # 409（冲突）而不是 500：这是**客户端**用错，报成服务端故障会让监控误判
            return {"code": 409, "msg": "该账号已存在，请换一个或直接登录"}
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

    # ---- token 撤销（第 13 轮整改，文档第五节）----
    @staticmethod
    def token_status(payload):
        """token 的当前状态，返回结构化字典（第 13 轮复核：供 RAG 的 introspect 使用）。

        ```
        {"active": bool, "reason": str, "user_id": ..., "role": str,
         "token_version": int | None, "disabled": bool}
        ```

        两件事 JWT 自己验不了，必须回库查：

        - **账号是否被禁用**：禁用后即便 token 还没过期也必须立刻拒绝，
          否则"封号"要等最长 7 天才真正生效；
        - **token 版本是否一致**：改密码 / 强制下线时 `token_version` +1，
          旧 token 里的 `ver` 就此对不上。这是没有黑名单时最省事的撤销手段——
          不需要存一堆已撤销的 jti，只需要一个整数。

        用户不存在（被删号）也按不可用处理：token 里带着的 user_id 已经没有任何
        对应的账号，继续放行等于给一个已删除的身份开权限。

        原因字符串只用于服务端日志：回给客户端的话术统一是"登录已失效"，
        不透露是"密码改过"还是"账号被停用"（前者会泄露账号状态）。

        **`check_token_usable` 与 RAG 的 introspect 接口共用本方法**：RAG 的撤销判定
        必须与本服务自己的守卫逐条一致，否则会出现"旧后端拒绝、RAG 却放行"的裂缝，
        而那种裂缝只在改密码/封号之后才显形，最难发现。
        """
        from jwt_util import token_version_of

        user_id = (payload or {}).get("user_id")
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        if user is None:
            return {"active": False, "reason": "账号不存在", "user_id": user_id,
                    "role": "", "token_version": None, "disabled": False}
        current = int(getattr(user, "token_version", 1) or 1)
        # 角色取库里的当前值而不是 token 里的历史值：管理员的降权要立刻反映到
        # 下游（角色声明只是签发那一刻的快照）。
        status = {
            "active": True,
            "reason": "",
            "user_id": user.id,
            "role": getattr(user, "role", "") or "viewer",
            "token_version": current,
            "disabled": bool(getattr(user, "disabled", False)),
        }
        if status["disabled"]:
            status["active"] = False
            status["reason"] = "账号已被停用"
        elif token_version_of(payload) != current:
            status["active"] = False
            status["reason"] = (f"token 版本过期（token={token_version_of(payload)} "
                                f"当前={current}）")
        return status

    @staticmethod
    def check_token_usable(payload):
        """校验 token 是否仍然可用。返回 `(是否可用, 原因)`。

        判定规则全部在 `token_status` 里（两者共用同一份实现，口径不会分叉）。
        """
        status = DbUtil.token_status(payload)
        return status["active"], status["reason"]

    @staticmethod
    def bump_token_version(user_id):
        """把 token_version +1（强制该用户所有已签发的 token 失效）。

        返回新版本号；用户不存在返回 None。**在改密码、封号、踢人时调用**——
        这些动作的共同点是"意图让旧凭证立刻作废"，而后端此刻已经没有别的手段
        表达这件事（JWT 是无状态的）。
        """
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        if user is None:
            return None
        user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        db.session.commit()
        return user.token_version

    @staticmethod
    def set_user_disabled(user_id, disabled):
        """启用/停用账号；停用时同时 +1 token_version，让在途 token 立刻失效。"""
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        if user is None:
            return None
        user.disabled = bool(disabled)
        if disabled:
            # 只置 disabled 也能拦住后续请求（check_token_usable 每次都查库），
            # 再 +1 是纵深防御：将来若某条路径漏查 disabled，版本号仍会挡住旧 token。
            user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        db.session.commit()
        return user.to_dict()

    def change_password(self, user_id, old_password, new_password):
        """改密码：校验旧口令后落库，并 +1 token_version。

        返回 `{"code": ..., "msg": ...}`（与 add_user 同一口径，路由层直接回给前端）。
        口令长度下限与文档一致（10–64）：过短的口令在限流之外几乎没有成本。
        """
        user = db.session.get(UserInfo, user_id) if user_id is not None else None
        if user is None:
            return {"code": 404, "msg": "用户不存在"}
        if not self._password_matches(user, old_password or ""):
            return {"code": 403, "msg": "原密码不正确"}
        problem = self._password_policy_problem(new_password)
        if problem:
            return {"code": 400, "msg": problem}

        user.password = generate_password_hash(new_password)
        # 改密码的语义是"把别人手里的凭证全作废"：不加这一步，旧 token 还能用到自然过期，
        # 而"改密码"正是用户发现密码泄露时唯一能做的动作。
        user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        db.session.commit()
        logger.info(f"账号 {user.account}（id={user.id}）已修改密码，token 版本升到 {user.token_version}")
        return {"code": 200, "msg": "密码已修改，请重新登录"}

    @staticmethod
    def _password_matches(user, password: str) -> bool:
        stored = getattr(user, "password", "") or ""
        if not password or not stored:
            return False
        if stored == password:
            return True
        return check_password_hash(stored, password)

    @staticmethod
    def _password_policy_problem(password: str):
        """口令强度检查；合规返回 None。

        判定实现在 `password_policy.password_problem`（唯一口径，第 13 轮复核整改 §2.1）。
        保留这个静态方法是为了不动既有调用点（注册与改密码两处），它只做转发。
        """
        return password_problem(password)

    @staticmethod
    def _get_model_by_type(node_type: str):
        return {
            "Event": Event,
            "Place": Place,
            "Organization": Organization,
            "Person": Person,
        }.get(node_type)

    # 按名回退查找/删除/改名（`_find_neo4j_by_name` / `_delete_neo4j_by_name` /
    # `_update_neo4j_by_name`）已在第 13 轮整改中删除：它们是为了在 `neo4j_id` 缺失时
    # "按名字猜一个节点"而存在的，而按名字定位在**同名节点有多个**时无法判断该动哪一个，
    # 正是文档第六节第 3 条 C 描述的"更新变成新建 / 改错对象"的来源。
    # 现在写路径一律按稳定图谱键定位（见 graph_key.py），历史遗留节点由
    # `model_search.upsert_node` 的"认领同名无键节点"步骤平滑接管，不再需要这种兜底。

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

            valid_columns = {c.name for c in model.__table__.columns} - MANAGED_COLUMNS
            filtered_data = {k: v for k, v in mapped_data.items() if k in valid_columns}
            new_node = model(**filtered_data)
            db.session.add(new_node)
            # flush 而不是 commit：先拿到自增主键（graph_key 要用），但**不提交**——
            # 业务行与下面的待办必须落进同一次提交（文档第六节第 4 条）。
            db.session.flush()

            job = _stage_sync_job(
                node_type, new_node.id, OP_NODE_CREATE, new_node.name, filtered_data,
            )
            db.session.commit()

            neo4j_sync_success, neo4j_error_msg, neo4j_id = _try_sync_now(
                job, node_type, new_node.id, graph_key=job.graph_key,
                name=new_node.name, properties=None,
            )

            return {
                "code": 200,
                "msg": "创建成功" if neo4j_sync_success else f"创建成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": new_node.id,
                    "type": node_type,
                    "name": new_node.name,
                    "graph_key": job.graph_key,
                    "neo4j_id": neo4j_id,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                },
            }
        except Exception:
            db.session.rollback()
            # 完整异常只进日志：`str(e)` 常带 SQL、列名与库文件路径（第 13 轮复核第七节）。
            # 调用方（blueprints/node.py）会把 code 透传成 HTTP 状态，因此这里只需给安全文案。
            logger.exception("创建节点失败：type=%s name=%s", node_type, name)
            return {"code": 500, "msg": "创建失败，请稍后重试"}

    @staticmethod
    def update_node(node_type: str, node_id: int, new_name: str, properties: dict = None):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = db.session.get(model, node_id)
            if not node:
                return {"code": 404, "msg": "节点不存在"}

            # 名称要从**归一化后的属性**里取（第 14 轮审计 P1-4 根因 a）。
            #
            # 前端从来不发 `name`：它按各实体的字段名发 `EventName` / `PersonName` /
            # `OrgName` / `geo_name`（useNodeCrudPage 按 nameField 组装）。原实现只读
            # `data.get("name")`，于是 new_name 恒为 None，一路作为 job.node_name 传到
            # `SET n.name = $name`，把图谱里的 name 写成 null（等价于删除）；
            # 而 SQLite 那边因为下面的归一化循环会把名字救回来，所以**只有图谱坏掉、
            # 主存储看不出来**。
            normalized = DbUtil._normalize_payload(node_type, properties or {})
            valid_columns = ({column.name for column in model.__table__.columns}
                             - MANAGED_COLUMNS)
            # 与 create_node 同一口径：先归一化，再按真实列过滤
            # （id / created_at / neo4j_id 这类由库维护，不接受外部指定）
            mapped_properties = {key: value for key, value in normalized.items()
                                 if key in valid_columns}
            effective_name = new_name or mapped_properties.get("name")
            if not (isinstance(effective_name, str) and effective_name.strip()):
                # 宁可 400，也不要把图谱写成无名节点——写坏了只能靠重导或手工补
                return {"code": 400, "msg": "缺少节点名称"}
            effective_name = effective_name.strip()

            old_name = node.name
            node.name = effective_name

            for mapped_key, value in mapped_properties.items():
                if hasattr(node, mapped_key):
                    setattr(node, mapped_key, value)
            # 属性快照用"这次提交的这一份"，且键名必须是 **SQLite 列名**（根因 b）：
            # 重放路径走 `neo4j_props()`，它按列名取值——用 API 键名做快照时全部取到
            # None 再被 `if v is not None` 过滤掉，属性一个都同步不过去，
            # 而函数却返回 sync_status: success。
            snapshot = dict(mapped_properties)
            snapshot.setdefault("name", effective_name)
            job = _stage_sync_job(
                node_type, node_id, OP_NODE_UPDATE, effective_name, snapshot,
                from_name=old_name if old_name != effective_name else None,
            )
            db.session.commit()

            neo4j_sync_success, neo4j_error_msg, _neo4j_id = _try_sync_now(
                job, node_type, node_id, graph_key=job.graph_key,
                name=effective_name, properties=None, from_name=job.from_name,
            )

            return {
                "code": 200,
                "msg": "更新成功" if neo4j_sync_success else f"更新成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    "type": node_type,
                    "name": effective_name,
                    "graph_key": job.graph_key,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception:
            db.session.rollback()
            logger.exception("更新节点失败：type=%s id=%s", node_type, node_id)
            return {"code": 500, "msg": "更新失败，请稍后重试"}

    @staticmethod
    def _relation_models_for(node_type: str):
        """返回所有**引用了该实体类型**的关系 ORM 与其外键列名。

        Event 是四种关系里都出现的一端（事件-事件出现两次），其余三类各被一种关系引用。
        这里刻意写成显式的数据映射，不做字符串拼表名——表名拼错会静默变成"什么都没删"。
        """
        if node_type == "Event":
            return [
                (EventEventRelation, "event_a_id"),
                (EventEventRelation, "event_b_id"),
                (EventPlaceRelation, "event_id"),
                (EventPersonRelation, "event_id"),
                (EventOrganizationRel, "event_id"),
            ]
        return {
            "Place": [(EventPlaceRelation, "place_id")],
            "Person": [(EventPersonRelation, "person_id")],
            "Organization": [(EventOrganizationRel, "org_id")],
        }.get(node_type, [])

    @staticmethod
    def delete_node(node_type: str, node_id: int):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = db.session.get(model, node_id)
            if not node:
                return {"code": 404, "msg": "节点不存在"}

            # 删除不再读 node.neo4j_id：定位改走稳定图谱键（`<Type>:<SQLite 主键>`），
            # 它不依赖图谱是否被重建过。neo4j_id 仅作为观测信息由重放路径回写。
            node_name = node.name

            # 先删引用本节点的关系行，再删实体行。
            #
            # 顺序不能反：外键已在每个连接上开启（app.py 的连接钩子），被引用的实体行
            # 先删会直接报 FOREIGN KEY constraint failed。存量库的关系表没有
            # ON DELETE CASCADE（DDL 建表时就写死了，create_all 不会改建好的表），
            # 所以级联删除必须在业务层显式做，不能指望数据库。
            #
            # 与实体行同一次提交：中途失败时整体回滚，不会出现"关系删了、节点还在"
            # 或"节点删了、关系还在"的中间态。
            removed_relations = 0
            for relation_model, fk_column in DbUtil._relation_models_for(node_type):
                removed_relations += relation_model.query.filter(
                    getattr(relation_model, fk_column) == node_id
                ).delete(synchronize_session=False)

            # 先删主存储（SQLite）并**与待办同一次提交**：主存储删除失败时整体回滚、
            # 不动 Neo4j，不会留下「Neo4j 已删、SQLite 还在」的永久不一致。
            #
            # 删除现在也进 outbox（第 13 轮整改）：原先明确不补偿删除（理由是"按名重放删除
            # 有歧义"），于是删除失败会永久留在图谱里，而且没有任何记录。有了稳定图谱键
            # 之后这个歧义消失了——`Event:123` 只可能指向一个节点，重放删除是明确的。
            db.session.delete(node)
            job = _stage_sync_job(node_type, node_id, OP_NODE_DELETE, node_name, None)
            db.session.commit()

            neo4j_sync_success, neo4j_error_msg, _neo4j_id = _try_sync_now(
                job, node_type, node_id, graph_key=job.graph_key, name=node_name,
                properties=None,
            )

            return {
                "code": 200,
                "msg": "删除成功" if neo4j_sync_success else f"删除成功（Neo4j同步删除失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    # 连带删除的关系条数：删除是级联的，前端要让操作者知道"这一下删掉的不止一个节点"
                    "removed_relations": removed_relations,
                    "graph_key": job.graph_key,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception:
            db.session.rollback()
            logger.exception("删除节点失败：type=%s id=%s", node_type, node_id)
            return {"code": 500, "msg": "删除失败，请稍后重试"}

    @staticmethod
    def update_node_properties(node_id: int, node_type: str, properties: dict):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return {"code": 400, "msg": f"未知节点类型: {node_type}"}

            node = db.session.get(model, node_id)
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
            job = _stage_sync_job(
                node_type, node_id, OP_NODE_UPDATE, node.name, mapped_properties,
            )
            db.session.commit()

            neo4j_sync_success, neo4j_error_msg, _neo4j_id = _try_sync_now(
                job, node_type, node_id, graph_key=job.graph_key, name=node.name,
                properties=None,
            )

            return {
                "code": 200,
                "msg": "属性更新成功" if neo4j_sync_success else f"属性更新成功（Neo4j同步失败: {neo4j_error_msg}）",
                "data": {
                    "id": node_id,
                    "graph_key": job.graph_key,
                    "sync_status": "success" if neo4j_sync_success else "failed",
                    "sync_error": neo4j_error_msg or None,
                },
            }
        except Exception:
            db.session.rollback()
            logger.exception("更新节点属性失败：type=%s id=%s", node_type, node_id)
            return {"code": 500, "msg": "属性更新失败，请稍后重试"}

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
        except Exception:
            # 这里**不再吞掉异常**：原先返回 `{"total": 0, "records": [], "error": str(e)}`，
            # 于是查询失败在客户端表现为"列表是空的"、在服务端日志里没有任何痕迹，
            # 而且 `str(e)` 还会带着 SQL 与库路径回到前端（第 13 轮复核第七节）。
            # 改为记日志后向上抛，由路由的 `server_error` 收成 HTTP 500 +
            # 同一份空列表形状（前端不会在 `data.records` 上崩，但能知道"这是失败"）。
            logger.exception("查询节点列表失败：page=%s size=%s type=%s",
                             current, limit, node_type)
            raise


    @staticmethod
    def get_node_detail_sqlite(node_id: int, node_type: str):
        try:
            model = DbUtil._get_model_by_type(node_type)
            if not model:
                return None
            node = db.session.get(model, node_id)
            if node:
                result = node.to_dict()
                result["type"] = node_type
                return result
            return None
        except Exception:
            return None
