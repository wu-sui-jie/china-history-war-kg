"""菜单的角色裁剪（第 6 轮审核 H4）。

`/user/menu` 是三级裁剪里的第二层（第一层是后端接口的 403，第三层是前端路由 meta）：
- viewer：看不到「数据运营」整组（不能改数据），也看不到「文本实体识别」（会消耗 LLM 配额）
  与「用户管理」；
- editor：看得到数据运营与文本实体识别，看不到「用户管理」；
- admin：全都能看到。

未知角色必须按最小权限处理（fail-closed）：`ROLE_RANKS.get(role, -1)` 拿不到分级时不得放行。
"""

import pytest


@pytest.mark.parametrize(
    "role,expect_hidden",
    [
        # P2-8：/knowledge/inference 的接口与路由都按 editor 卡，菜单必须一致
        ("viewer", {"/admin/users", "/workspace/manage", "/knowledge/text-extract",
                    "/knowledge/inference"}),
        ("editor", {"/admin/users"}),
        ("admin", set()),
    ],
)
def test_菜单按角色裁剪(client, make_user, auth, user_ids, role, expect_hidden):
    user = make_user(f"{role}-1", role)

    response = client.get("/user/menu", headers=auth(user))
    assert response.status_code == 200

    ids = user_ids(response.get_json())
    for menu_id in expect_hidden:
        assert menu_id not in ids, f"{role} 不该看到 {menu_id}"

    # 只读页面三个角色都要有：裁剪只针对"改数据"与"管理员动作"
    assert {"/workspace/dashboard", "/knowledge/timeline", "/knowledge/rag"} <= ids


def test_数据运营组的子项整体消失(client, make_user, auth, user_ids):
    viewer = make_user("viewer-1", "viewer")
    editor = make_user("editor-1", "editor")

    viewer_ids = user_ids(client.get("/user/menu", headers=auth(viewer)).get_json())
    editor_ids = user_ids(client.get("/user/menu", headers=auth(editor)).get_json())

    group_items = {"/workspace/dataset", "/knowledge-list", "/workspace/quality"}
    assert not (group_items & viewer_ids), "viewer 不该看到数据运营的任何子项"
    assert group_items <= editor_ids, "editor 应看到数据运营组"


def test_未知角色按最小权限处理(client, make_user, auth, user_ids):
    """库里存了白名单外的角色值（历史脏数据）时不能放行——它不在 ROLE_RANKS 里。"""
    user = make_user("weird-1", "superuser")

    ids = user_ids(client.get("/user/menu", headers=auth(user)).get_json())

    assert "/admin/users" not in ids
    assert "/workspace/manage" not in ids
    assert "/knowledge/text-extract" not in ids
    assert "/knowledge/inference" not in ids
    assert "/workspace/dashboard" in ids


def test_角色为空的历史账号按_viewer_处理(client, make_user, auth, user_ids):
    """空角色不再兜底成 admin（否则一次写库遗漏就是静默提权）。"""
    user = make_user("legacy-1", "viewer")

    from models import UserInfo, db

    row = db.session.get(UserInfo, user.id)
    row.role = None   # 模拟迁移漏掉的历史行
    db.session.commit()
    db.session.refresh(row)

    ids = user_ids(client.get("/user/menu", headers=auth(user)).get_json())
    assert "/admin/users" not in ids
    assert "/workspace/manage" not in ids
    # 用户列表也要与鉴权同口径显示成 viewer，不能显示成"管理员"
    admin = make_user("admin-1", "admin")
    listed = {item["id"]: item for item in
              client.get("/api/admin/users", headers=auth(admin)).get_json()["data"]}
    assert listed[user.id]["role"] == "viewer"
