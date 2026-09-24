"""角色常量与鉴权装饰器（P2-1 收官：从 app.py 抽出，供蓝图与 app 共用）。

原先这些定义在 app.py 里，路由搬进蓝图后会形成"蓝图 import app / app import 蓝图"的循环，
因此把与请求处理无关的这部分独立出来。**口径不变**——三处一致的要求见
backend/README 的角色职责表：本模块（接口鉴权）、get_menu（菜单裁剪，在 blueprints/auth.py）、
前端路由 meta（frontend/src/router）。
"""

import functools

from flask import g, jsonify

from db_utils import DbUtil

# 可写数据的角色；注册接口一律建 viewer（只读）
WRITE_ROLES = {"admin", "editor"}

# 合法角色与其强度（admin ⊃ editor ⊃ viewer）。角色值一律按白名单校验后再落库，
# 不接受任意字符串——UserInfo.role 会直接决定接口能不能写、菜单下发哪些。
ROLE_RANKS = {"viewer": 0, "editor": 1, "admin": 2}

# 仅管理员可见的顶层菜单 id（目前只有用户管理）。
ADMIN_MENU_IDS = {"/admin/users"}

# 需要 editor 及以上才可见的菜单 id。「文本实体识别」与「历史问答助手」都会调用大模型
# 消耗配额，只读账号不该有入口——与路由 meta.requiresRole='editor'、接口的
# require_write_role 是同一口径，三处要一起改（见 backend/README 的角色职责表）。
EDITOR_MENU_IDS = {"/knowledge/text-extract"}


def _menu_allowed(menu_id, role):
    """分组内的菜单项是否对当前角色可见（按 ROLE_RANKS 分级比对）。"""
    if menu_id in EDITOR_MENU_IDS and ROLE_RANKS.get(role, -1) < ROLE_RANKS["editor"]:
        return False
    return True


def require_write_role(view):
    """写接口鉴权：只读角色（viewer）不允许改数据。"""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):

        role = DbUtil.get_role(getattr(g, "user_id", None))
        if role not in WRITE_ROLES:
            return jsonify({
                "code": 403,
                "msg": "当前账号为只读权限，无法执行该操作"
            }), 403
        return view(*args, **kwargs)
    return wrapper


def require_admin(view):
    """管理员接口鉴权（用户管理这类"改角色"的动作专用）。

    刻意不复用 `require_write_role`：editor 若能改角色，权限体系会被 editor 自己打散
    （把自己升成 admin）。角色分级留在服务端判断，不靠界面藏入口。
    """
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        role = DbUtil.get_role(getattr(g, "user_id", None))
        if role != "admin":
            return jsonify({
                "code": 403,
                "msg": "仅管理员可执行该操作"
            }), 403
        return view(*args, **kwargs)
    return wrapper
