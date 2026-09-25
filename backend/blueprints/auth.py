"""认证与用户相关路由（P2-1 收官：从 app.py 按业务分组迁出）。

分组依据是"谁在用"：登录注册、账号信息、用户管理（改角色）、菜单与权限。
**URL 与行为逐字未变**——迁移只动了装饰器（@app.route → @auth_bp.route），
由 67 请求快照对照 + backend/tests 的 32 例兜底。

注意：全局鉴权（before_request）与 initialize_entity_extractor 仍留在 app 级，
蓝图不重复实现鉴权——它们对所有蓝图一视同仁。
"""

from flask import Blueprint, g, jsonify, request

import login_guard
from db_utils import DbUtil
from jwt_util import encode
from logging_util import get_logger
from roles import (
    ADMIN_MENU_IDS,
    ROLE_RANKS,
    WRITE_ROLES,
    _menu_allowed,
    require_admin,
)

auth_bp = Blueprint("auth", __name__)
logger = get_logger(__name__)


def _rate_limited(scope: str, key: str):
    """被限流时返回 429 响应，否则 None。

    HTTP 用 429 而不是沿用登录失败那套 "200 + code 403"：限流是**另一种状态**，
    客户端要据此决定"等一会再试"而不是"密码错了"。响应里带 `retry_after`，
    前端不必猜要等多久（同时照 HTTP 惯例给出 Retry-After 头）。
    """
    allowed, wait = login_guard.check(scope, key)
    if allowed:
        return None
    logger.warning("登录被限流：维度=%s 标识=%s 建议等待=%ss", scope, key, wait)
    response = jsonify({
        "code": 429,
        "msg": "尝试过于频繁，请稍后再试",
        "retry_after": wait,
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(wait)
    return response


@auth_bp.route('/api/login', methods=['POST'])
def login():
    """用户登录接口

    请求参数(JSON):
        - account: 用户账号
        - password: 用户密码

    响应:
        - code: 200(成功) / 403(失败) / 429(限流)
        - data: JWT Token(成功时返回)
        - msg: 错误信息(失败时返回)

    第 13 轮整改加了两条：**限流**（同 IP 与同账号两把尺子，见 login_guard）
    与**账号停用检查**。HTTP 状态与响应形状刻意未变（仍是 200 + code 403）——
    前端按 `code` 分支，改动响应契约会把登录页一起打坏；限流是新状态，用 429 表达。
    """
    params = request.get_json(silent=True) or {}
    account = (params.get("account") or "").strip()
    ip = login_guard.client_ip(request)

    # 两个维度都先查（各自的计数口径见 login_guard 的模块文档）：
    # IP 挡"一台机器撒网"，账号挡"盯着一个账号慢慢试"
    limited = _rate_limited("ip", ip)
    if limited:
        return limited
    if account:
        limited = _rate_limited("account", account)
        if limited:
            return limited

    # IP 维度：每次尝试都计数（成功也算）——限流的对象是请求频率本身，
    # 只计失败的话"每次都成功的刷量脚本"完全不消耗预算。
    login_guard.record_attempt("ip", ip)

    handler = DbUtil()
    user = handler.authentication(params)
    if user and bool(getattr(user, "disabled", False)):
        # 停用账号即便口令正确也不放行：否则"封号"只是一句提示
        logger.warning("已停用账号尝试登录：account=%s ip=%s", account, ip)
        login_guard.record_failure("account", account)
        user = None

    if user:
        # 只清账号维度：清 IP 会让"用自己的合法账号登录一次"变成重置配额的手段
        login_guard.record_success("account", account)
        # 生成JWT Token。role 一并签发（第 12 轮审查 P1-1）：RAG 服务端验签后能拿到角色，
        # 不必回查旧库；角色变更后旧 token 里的 role 会滞后，所以它只用于收敛界面这类
        # 低风险判断——写权限与管理员判断仍由本服务每次请求实时查库（见 DbUtil.get_role）。
        # token_version 必须一并带上（第 13 轮整改）：它让"改密码/封号后旧 token 立刻失效"
        # 成为可能，漏传会退化成 1，与库里版本不符时旧 token 立刻失效（fail-closed）。
        token = encode(user.id, user.role or "viewer",
                       token_version=getattr(user, "token_version", 1) or 1)
        logger.info("登录成功：account=%s ip=%s", account, ip)
        return jsonify({
            "code": 200,
            "data": token
        })

    # 失败记在账号维度（IP 那次已在上面的 record_attempt 里计过，不重复计）
    login_guard.record_failure("account", account)
    logger.warning("登录失败：account=%s ip=%s", account, ip)
    return jsonify({
        "code": 403,
        "msg": "用户名或密码错误"
    })


@auth_bp.route('/api/user/password', methods=['POST'])
def change_password():
    """本人修改密码。请求体：{"old_password": ..., "new_password": ...}

    改完 **token_version +1**，本 token 与其它设备上的 token 一起失效——
    "改密码"正是用户发现口令泄露时唯一能做的动作，旧凭证如果还能用到自然过期，
    这个动作就等于没做。前端收到 200 后应清掉本地 token 并回到登录页。
    """
    data = request.get_json(silent=True) or {}
    result = DbUtil().change_password(
        getattr(g, "user_id", None),
        data.get("old_password") or "",
        data.get("new_password") or "",
    )
    if result.get("code") == 200:
        logger.info("用户 id=%s 修改了自己的密码", getattr(g, "user_id", None))
    return jsonify(result), (result.get("code") if result.get("code") != 200 else 200)


@auth_bp.route('/api/userinfo', methods=['GET', 'POST'])
def userinfo():
    handler = DbUtil()
    result = handler.find_user(getattr(g, "user_id", None))
    return jsonify({
        "code": 200,
        "data": result
    })


@auth_bp.route('/api/sign_in', methods=['POST'])
def sign_in():
    """自由注册。

    `ALLOW_SELF_REGISTRATION=false` 时关闭（第 13 轮整改，文档第十一节）：
    公网部署下"任何人都能建号"意味着任何人都能拿到 viewer 身份调接口、
    消耗模型配额。关闭后新建账号走管理员引导命令（backend/create_admin.py）。
    """
    if not login_guard.registration_allowed():
        logger.warning("注册接口已被关闭（ALLOW_SELF_REGISTRATION=false）：ip=%s",
                       login_guard.client_ip(request))
        return jsonify({
            "code": 403,
            "msg": "系统已关闭自助注册，请联系管理员开通账号",
        }), 403
    # 注册同样限流：否则它就是一个"批量建号"的入口，比爆破登录更直接。
    # 注册成功也算一次尝试——被滥用的正是"成功建号"这个动作本身。
    ip = login_guard.client_ip(request)
    limited = _rate_limited("ip", ip)
    if limited:
        return limited
    login_guard.record_attempt("ip", ip)

    data = request.get_json(silent=True)
    handler = DbUtil()
    result = handler.add_user(data)
    if result.get("code") == 200:
        # 新注册账号的 token_version 必然是默认值 1（add_user 用列默认值落库），
        # 这里显式写 1 而不是回查一次：形状更清楚，也少一次查询。
        token = encode(result["data"]["user_id"], "viewer", token_version=1)
        logger.info("自助注册成功：account=%s ip=%s", (data or {}).get("account"), ip)
        return jsonify({
            "code": 200,
            "data": token,
            "msg": "注册成功"
        })
    return jsonify(result), (result.get("code") if result.get("code") != 200 else 200)


@auth_bp.route('/api/admin/users', methods=['GET'])
@require_admin
def admin_list_users():
    """用户列表：id / 账号 / 昵称 / 角色。"""
    return jsonify({"code": 200, "data": DbUtil.list_users()})


@auth_bp.route('/api/admin/users/<int:user_id>/role', methods=['POST'])
@require_admin
def admin_update_user_role(user_id):
    """修改用户角色。请求体：{"role": "admin" | "editor" | "viewer"}

    两条防呆：

    ① 不允许改自己的角色。否则最后一个管理员可以把自己降级成 viewer，之后没人能
       再提权（只能回到手写 SQL），系统等于失管；
    ② 角色值必须落在白名单里。UserInfo.role 直接决定能不能写、下发哪些菜单，
       不接受任意字符串。

    生效时机：写接口的 403 是每次请求实时查库的，改完立刻生效；**菜单是登录时下发的**，
    被改的人需要重新登录（或重新触发 loadMenus）才会看到菜单变化。
    """
    if user_id == getattr(g, "user_id", None):
        return jsonify({"code": 403, "msg": "不能修改自己的角色，请让另一位管理员操作"}), 403

    data = request.get_json(silent=True) or {}
    role = str(data.get("role", "")).strip()
    if role not in ROLE_RANKS:
        return jsonify({
            "code": 400,
            "msg": "角色取值非法，只允许：%s" % "/".join(sorted(ROLE_RANKS, key=ROLE_RANKS.get)),
        }), 400

    previous_role = (DbUtil.find_user(user_id) or {}).get("role") or ""
    updated = DbUtil.set_user_role(user_id, role)
    if updated is None:
        return jsonify({"code": 404, "msg": "用户不存在"}), 404

    # 记住旧角色：提权/降权的审计价值主要在"从什么变成了什么"，
    # 只记新角色时，事后翻日志分不清是"新提权"还是"重复提交同一值"。
    logger.info("管理员 %s 把账号 %s（id=%s）的角色从 %s 改为 %s",
                getattr(g, "user_id", None), updated.get("account"), user_id,
                previous_role or "-", role)
    return jsonify({"code": 200, "msg": "角色已更新", "data": updated})


@auth_bp.route('/api/admin/users/<int:user_id>/status', methods=['POST'])
@require_admin
def admin_set_user_status(user_id):
    """停用 / 启用账号。请求体：{"disabled": true|false}

    停用会同时把 token_version +1，让该账号在其它设备上的在途 token 立刻失效
    （只置 disabled 也能拦住后续请求，因为 `DbUtil.check_token_usable` 每次都查库；
    再 +1 是纵深防御：将来若某条路径漏查 disabled，版本号仍会挡住旧 token）。

    与改角色同样的防呆：**不允许停用自己**。否则最后一个管理员可以把自己停掉，
    之后没人能再启用任何账号，系统彻底失管。
    """
    if user_id == getattr(g, "user_id", None):
        return jsonify({"code": 403, "msg": "不能停用自己的账号，请让另一位管理员操作"}), 403

    data = request.get_json(silent=True) or {}
    raw = data.get("disabled")
    if not isinstance(raw, bool):
        return jsonify({"code": 400, "msg": "disabled 必须是布尔值 true/false"}), 400

    updated = DbUtil.set_user_disabled(user_id, raw)
    if updated is None:
        return jsonify({"code": 404, "msg": "用户不存在"}), 404

    logger.info("管理员 %s 把账号 %s（id=%s）设置为 %s",
                getattr(g, "user_id", None), updated.get("account"), user_id,
                "停用" if raw else "启用")
    return jsonify({"code": 200, "msg": "账号已停用" if raw else "账号已启用", "data": updated})


@auth_bp.route('/user/menu', methods=['GET'])
def get_menu():
    """获取系统菜单。"""
    menu_data = [
        {
            "id": "/workspace/dashboard",
            "icon": "layui-icon-home",
            "title": "首页仪表盘"
        },
        {
            "id": "/knowledge/timeline",
            "icon": "layui-icon-date",
            "title": "历史时间轴"
        },
        {
            "id": "/knowledge/map",
            "icon": "layui-icon-location",
            "title": "历史地图视图"
        },
        {
            "id": "/knowledge",
            "icon": "layui-icon-set",
            "title": "知识图谱",
            "children": [
                {
                    "id": "/knowledge/graph",
                    "icon": "layui-icon-find-fill",
                    "title": "战争关系图"
                },
                {
                    "id": "/knowledge/graph/event",
                    "icon": "layui-icon-flag",
                    "title": "历史战争"
                },
                {
                    "id": "/knowledge/graph/organization",
                    "icon": "layui-icon-group",
                    "title": "参战势力"
                },
                {
                    "id": "/knowledge/graph/place",
                    "icon": "layui-icon-location",
                    "title": "战争地点"
                },
                {
                    "id": "/knowledge/graph/person",
                    "icon": "layui-icon-user",
                    "title": "历史人物"
                },
                {
                    "id": "/knowledge/inference",
                    "icon": "layui-icon-engine",
                    "title": "历史问答助手"
                },
                {
                    "id": "/knowledge/rag",
                    "icon": "layui-icon-chat",
                    "title": "RAG 智能问答"
                },
                {
                    "id": "/knowledge/text-extract",
                    "icon": "layui-icon-read",
                    "title": "文本实体识别"
                },
                {
                    "id": "/knowledge/relation-analysis",
                    "icon": "layui-icon-chart",
                    "title": "关系分析"
                },
                {
                    "id": "/knowledge/search",
                    "icon": "layui-icon-search",
                    "title": "全局搜索"
                },
                {
                    "id": "/knowledge/entity-detail",
                    "icon": "layui-icon-read",
                    "title": "实体详情页"
                }
            ]
        },
        {
            "id": "/workspace/manage",
            "icon": "layui-icon-console",
            "title": "数据运营",
            "children": [
                {
                    "id": "/workspace/dataset",
                    "icon": "layui-icon-template-1",
                    "title": "数据集中心"
                },
                {
                    "id": "/knowledge-list",
                    "icon": "layui-icon-fonts-code",
                    "title": "数据维护"
                },
                {
                    "id": "/workspace/quality",
                    "icon": "layui-icon-vercode",
                    "title": "图谱质检"
                },
                {
                    "id": "/workspace/dataset-versions",
                    "icon": "layui-icon-list",
                    "title": "数据版本管理"
                }
            ]
        },
        {
            # 仅管理员：用户管理（改角色）。刻意放在顶层而不是塞进任何业务分组——
            # 它是系统管理动作，和"看数据/改数据"不是一类。
            "id": "/admin/users",
            "icon": "layui-icon-user",
            "title": "用户管理"
        }
    ]

    # 角色裁剪（三级）：viewer 看不到数据运营组，editor 看不到用户管理。
    # 写权限的真正防线是 require_write_role / require_admin 的 403，这里不下发菜单是
    # 第二层——让不该看到的人界面上就没有入口，而不是点了才被拒。
    # 注意：编辑器级菜单（如文本实体识别，会消耗 LLM 配额）也在这里裁剪，
    # 对应的路由 meta 与接口鉴权要一起改，三处口径见 backend/README 的角色职责表。
    role = DbUtil.get_role(getattr(g, "user_id", None))
    if role != "admin":
        menu_data = [m for m in menu_data if m.get("id") not in ADMIN_MENU_IDS]
    if role not in WRITE_ROLES:
        menu_data = [m for m in menu_data if m.get("id") != "/workspace/manage"]
    for group in menu_data:
        if isinstance(group.get("children"), list):
            group["children"] = [child for child in group["children"]
                                 if _menu_allowed(child.get("id"), role)]

    return jsonify({
        "code": 200,
        "data": menu_data
    })


@auth_bp.route('/user/permission', methods=['GET'])
def get_permission():
    """返回前端菜单权限占位数据。"""
    return jsonify({
        "code": 200,
        "data": []
    })
