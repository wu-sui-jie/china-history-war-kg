"""认证与用户相关路由（P2-1 收官：从 app.py 按业务分组迁出）。

分组依据是"谁在用"：登录注册、账号信息、用户管理（改角色）、菜单与权限。
**URL 与行为逐字未变**——迁移只动了装饰器（@app.route → @auth_bp.route），
由 67 请求快照对照 + backend/tests 的 32 例兜底。

注意：全局鉴权（before_request）与 initialize_entity_extractor 仍留在 app 级，
蓝图不重复实现鉴权——它们对所有蓝图一视同仁。
"""

from flask import Blueprint, g, jsonify, request

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


@auth_bp.route('/api/login', methods=['POST'])
def login():
    """用户登录接口
    
    请求参数(JSON):
        - account: 用户账号
        - password: 用户密码
    
    响应:
        - code: 200(成功) / 403(失败)
        - data: JWT Token(成功时返回)
        - msg: 错误信息(失败时返回)
    """
    params = request.get_json()
    handler = DbUtil()
    # 验证用户账号密码
    user = handler.authentication(params)
    if user:
        # 生成JWT Token
        token = encode(user.id)
        return jsonify({
            "code": 200,
            "data": token
        })
    else:
        return jsonify({
            "code": 403,
            "msg": "用户名或密码错误"
        })


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
    data = request.get_json()
    handler = DbUtil()
    result = handler.add_user(data)
    if result.get("code") == 200:
        token = encode(result["data"]["user_id"])
        return jsonify({
            "code": 200,
            "data": token,
            "msg": "注册成功"
        })
    return jsonify(result)


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
