"""提权链路：注册只读 → 管理员提权 → 立刻生效 → 降权立刻失效。

写接口的 403 是**每次请求实时查库**判定的，所以角色改完立刻生效（菜单要重新登录才变，
那是另一回事）。这条链路必须由常驻用例守住，不能只靠一次性脚本。

用 `/create_node` 作为写接口样本：它同时受 `require_write_role` 保护，
且在没有 Neo4j 的测试环境里仍会返回 200（图谱同步失败不影响接口层判定）。
"""


def test_注册接口一律建_viewer(client, auth):
    """注册不能自带角色：`/api/sign_in` 只认账号/昵称/口令，角色由管理员的用户管理页下发。"""
    response = client.post("/api/sign_in", json={
        "account": "self-registered",
        "name": "自注册用户",
        # 口令下限是 10 位：登录限流只能压低爆破速率，
        # 真正决定成本的是口令的搜索空间，所以注册这条路径也要过强度检查。
        "password": "pw-12345678",
        # 故意塞一个 role：接口必须忽略它，不能被请求体提权
        "role": "admin",
    })
    assert response.status_code == 200
    assert response.get_json()["code"] == 200

    from models import UserInfo, db

    created = UserInfo.query.filter(UserInfo.account == "self-registered").one()
    assert created.role == "viewer", "注册接口不能被请求体里的 role 影响"

    # 用自己的 token 试写：只读必须被拒
    assert client.post("/create_node", json={"type": "Person", "name": "甲"},
                       headers=auth(created)).status_code == 403


def test_viewer_写接口被拒_editor_放行(client, make_user, auth):
    editor = make_user("editor-1", "editor")
    viewer = make_user("viewer-1", "viewer")

    viewer_response = client.post("/create_node", json={"type": "Person", "name": "甲"},
                                  headers=auth(viewer))
    assert viewer_response.status_code == 403
    assert viewer_response.get_json()["code"] == 403

    editor_response = client.post("/create_node", json={"type": "Person", "name": "甲"},
                                  headers=auth(editor))
    assert editor_response.status_code == 200, "editor 应当通过写权限门"


def test_提权与降权对同一_token_立刻生效(client, make_user, auth):
    """改完角色无需重新登录：写接口每次请求实时查库。"""
    admin = make_user("admin-1", "admin")
    target = make_user("target-1", "viewer")
    target_headers = auth(target)   # 提权前后用同一个 token

    assert client.post("/create_node", json={"type": "Person", "name": "乙"},
                       headers=target_headers).status_code == 403

    promoted = client.post(f"/api/admin/users/{target.id}/role",
                           json={"role": "editor"}, headers=auth(admin))
    assert promoted.status_code == 200
    assert client.post("/create_node", json={"type": "Person", "name": "乙"},
                       headers=target_headers).status_code == 200, "提权后应立刻可写"

    demoted = client.post(f"/api/admin/users/{target.id}/role",
                          json={"role": "viewer"}, headers=auth(admin))
    assert demoted.status_code == 200
    assert client.post("/create_node", json={"type": "Person", "name": "丙"},
                       headers=target_headers).status_code == 403, "降权后应立刻被拒"


def test_旧问答接口也限_editor(client, make_user, auth, monkeypatch):
    """历史问答助手会消耗 LLM 配额，与文本实体识别同口径。

    `run_inference` 被替换成假实现：这里考的是权限门在不在，不去真调模型
    （CI 没有 Ollama，本机有也不该让单测依赖它）。
    """
    import llm_pipeline

    called = []

    def fake_run_inference(rule_engine, entity_extractor, question, request_id):
        called.append(question)
        return ({"success": True, "answer": "假回答", "kg_data": {"nodes": [], "lines": []}}, 200)

    monkeypatch.setattr(llm_pipeline, "run_inference", fake_run_inference)

    editor = make_user("editor-1", "editor")
    viewer = make_user("viewer-1", "viewer")

    for path in ("/api/ai/inference", "/api/ai/inference/stream"):
        assert client.post(path, json={"question": "赤壁之战"},
                           headers=auth(viewer)).status_code == 403, f"{path} 应拦 viewer"
    assert not called, "被拦下的请求不该触达推理逻辑"

    assert client.post("/api/ai/inference", json={"question": "赤壁之战"},
                       headers=auth(editor)).status_code == 200


def test_畸形JSON按_400_返回而不是HTML错误页(client, make_user, auth):
    editor = make_user("editor-1", "editor")

    response = client.post("/api/extract/entities-events", data="{不是 JSON",
                           content_type="application/json", headers=auth(editor))

    assert response.status_code == 400
    assert response.is_json, "get_json(silent=True) 的意义就是这里给 JSON 而不是 Flask 的 HTML 页"
