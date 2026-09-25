"""节点增删改查与节点查询路由（P2-1 收官：从 app.py 按业务分组迁出）。

含四类写接口（create/update/delete_node、update_properties，均受 require_write_role 保护）
与只读的节点/关系类型查询。**URL 与行为逐字未变**；第 12 轮审查 P1-3 追加了双写补偿的
查看与重试两个接口（`/api/sync/*`，见文件末尾）。
"""

from flask import Blueprint, g, jsonify, request

from api_errors import error_payload, json_body, server_error
from db_handle import neo4j_db_handle
from db_utils import DbUtil
from logging_util import get_logger
from roles import require_write_role

node_bp = Blueprint("node", __name__)
logger = get_logger(__name__)


def _result_response(result: dict):
    """把 `DbUtil` 的 `{code, msg, data}` 结果转成响应，**HTTP 状态取同一个 code**。

    DbUtil 的写方法用 code 表达业务结果（200 / 400 / 404 / 500），而路由此前一律回
    HTTP 200——于是"节点不存在"在监控、反代与前端重试逻辑眼里都是**成功**，
    只能靠读 body 才能分辨（第 13 轮复核第七节第 5 条的状态码表）。

    前端两条分支都能显示后端文案（`useNodeCrudPage` 既有 `res.code !== 200` 分支，
    也有读 `error.response.data.msg` 的 catch 分支），因此这一步对用户可见行为无影响。
    """
    code = result.get("code")
    status = code if isinstance(code, int) and 100 <= code <= 599 else 200
    return jsonify(result), status


@node_bp.route('/api/find_node_page', methods=['POST'])
def find_list():
    """
    节点列表查询 - 改为从SQLite查询
    支持分页、名称搜索和类型过滤
    """
    data, error = json_body()
    if error:
        # 列表接口的失败也要保持形状：前端在 data.records 上取值，给 null 会直接崩。
        error[0].get_json()["data"] = {"total": 0, "records": []}
        return error

    try:
        try:
            current = int(data.get('pageNum', 1))
            limit = int(data.get('pageSize', 10))
        except (TypeError, ValueError):
            # 改前非整数分页参数会被兜底 except 收成 500（内部异常），
            # 一次参数错误被说成服务器故障，排查方向一开始就是错的
            return jsonify(error_payload(400, "pageNum / pageSize 必须是整数",
                                         data={"total": 0, "records": []})), 400
        name_query = data.get('name', '')
        node_type = data.get('node_type', '')

        # 使用DbUtil从SQLite查询
        result = DbUtil.find_node_page(current, limit, name_query, node_type if node_type else None)

        return jsonify({
            "code": 200,
            "data": result
        })
    except Exception as e:
        return server_error("查询节点列表失败", e, data={"total": 0, "records": []})


@node_bp.route('/create_node', methods=['POST'])
@require_write_role
def create_node():
    """创建节点接口
    
    请求参数(JSON):
        - type: 节点类型(Event/Place/Organization/Person)
        - name: 节点名称
        - 其他属性字段
    
    处理流程:
        1. 接收前端传来的节点数据
        2. 调用DbUtil.create_node创建SQLite记录
        3. 自动同步到Neo4j
    
    响应:
        - code: 200(成功) / 500(失败)
        - msg: 操作结果信息
    """
    data, error = json_body()
    if error:
        return error

    try:
        node_type = data.get("type")
        name = data.get("name")
        # 提取除type和name外的其他属性
        properties = {k: v for k, v in data.items() if k not in ['type', 'name']}

        result = DbUtil.create_node(node_type, name, properties)
        return _result_response(result)

    except Exception as e:
        return server_error("创建节点失败", e)


@node_bp.route('/update_node', methods=['POST'])
@require_write_role
def update_node():
    """
    更新节点 - 更新SQLite，异步同步到Neo4j
    """
    data, error = json_body()
    if error:
        return error

    try:
        node_type = data.get("type")
        node_id = data.get("id")
        new_name = data.get("name")
        properties = {k: v for k, v in data.items() if k not in ['type', 'id', 'name']}

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node(node_type, node_id, new_name, properties)
        return _result_response(result)

    except Exception as e:
        # 改前这里既没有日志、HTTP 又是 200：失败在客户端表现为"成功但没变"，
        # 在服务端日志里也没有任何痕迹（第 13 轮复核第七节第 3 条）。
        return server_error("更新节点失败", e)


@node_bp.route('/delete_node', methods=['POST'])
@require_write_role
def delete_node():
    """
    删除节点 - 删除SQLite，异步同步到Neo4j
    """
    data, error = json_body()
    if error:
        return error

    try:
        node_type = data.get("type")
        node_id = data.get("id")

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.delete_node(node_type, node_id)
        return _result_response(result)

    except Exception as e:
        # 改前这里既没有日志、HTTP 又是 200：失败在客户端表现为"成功但没变"，
        # 在服务端日志里也没有任何痕迹（第 13 轮复核第七节第 3 条）。
        return server_error("删除节点失败", e)


@node_bp.route('/api/node/detail', methods=['GET'])
def get_node_detail():
    """
    获取节点详情 - 优先从SQLite读取，如不存在则从Neo4j读取
    """
    try:
        node_id = request.args.get('id')
        node_type = request.args.get('type')

        if not node_id:
            return jsonify(error_payload(400, "节点ID不能为空")), 400

        # 尝试从SQLite获取
        if node_type:
            sqlite_detail = DbUtil.get_node_detail_sqlite(int(node_id), node_type)
            if sqlite_detail:
                return jsonify({
                    "code": 200,
                    "msg": "success",
                    "data": sqlite_detail
                })

        # 如SQLite不存在，从Neo4j获取（兼容历史数据）
        node_detail = neo4j_db_handle.get_node_detail(node_id)
        if not node_detail:
            return jsonify(error_payload(404, "节点不存在")), 404

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": node_detail
        })

    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node/update_properties', methods=['POST'])
@require_write_role
def update_node_properties():
    """
    更新节点属性 - 更新SQLite，异步同步到Neo4j
    """
    data, error = json_body()
    if error:
        return error

    try:
        node_id = data.get("id")
        node_type = data.get("type")
        properties = data.get("properties", {})

        if not node_id:
            return jsonify(error_payload(400, "节点ID不能为空")), 400

        if not properties or not isinstance(properties, dict):
            return jsonify(error_payload(400, "节点属性格式不正确")), 400

        # 从properties中提取type
        if not node_type and 'type' in properties:
            node_type = properties['type']

        if not node_type:
            return jsonify(error_payload(400, "无法确定节点类型")), 400

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node_properties(node_id, node_type, properties)
        return _result_response(result)

    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node_types', methods=['GET'])
def get_node_types():
    try:
        types = neo4j_db_handle.get_node_types()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/relationship_types', methods=['GET'])
def get_relationship_types():
    try:
        types = neo4j_db_handle.get_relationship_types()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/relationship_types_by_Event', methods=['GET'])
def get_relationship_types_by_Event():
    try:
        types = neo4j_db_handle.get_relationship_types_by_Event()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node/relations', methods=['GET'])
def get_node_relations():
    try:
        node_id = request.args.get('id')
        if not node_id:
            return jsonify(error_payload(400, "节点ID不能为空")), 400

        result = neo4j_db_handle.get_node_relations(node_id)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node/by_type', methods=['GET'])
def get_nodes_by_type():
    try:
        node_type = request.args.get('type')
        if not node_type:
            return jsonify(error_payload(400, "节点类型不能为空")), 400

        result = neo4j_db_handle.get_nodes_by_type(node_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node/by_relationship', methods=['GET'])
def get_nodes_by_relationship():
    try:
        rel_type = request.args.get('type')
        if not rel_type:
            return jsonify(error_payload(400, "关系类型不能为空")), 400

        result = neo4j_db_handle.get_nodes_by_relationship(rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return server_error("操作失败", e)


@node_bp.route('/api/node/search_by_name', methods=['GET'])
def search_nodes_by_name():
    try:
        search_text = request.args.get('name')
        limit = request.args.get('limit', 100, type=int)

        if not search_text:
            return jsonify(error_payload(400, "搜索文本不能为空")), 400

        result = neo4j_db_handle.search_nodes_by_name(search_text, limit)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return server_error("操作失败", e)


# ==================== 双写补偿（第 12 轮审查 P1-3）====================
# Neo4j 写失败时落一条待补偿记录（见 sync_compensation.py），这里给出查看与重试入口。
# 受 require_write_role 保护而不是 require_admin：它只影响图谱侧的可见性，
# 与 editor 已有的写节点权限是同一类动作，不需要额外提权。

@node_bp.route('/api/sync/pending', methods=['GET'])
@require_write_role
def sync_pending():
    """待补偿的 Neo4j 同步任务列表 + 各状态计数。

    同时给出 `summary`，让界面/运维一眼看出"还差多少"，不必自己数列表长度
    （列表有 limit，长度不等于总数）。
    """
    from sync_compensation import jobs_summary, pending_jobs

    limit = request.args.get("limit", 50, type=int)
    jobs = pending_jobs(limit=limit)
    return jsonify({
        "code": 200,
        "data": {
            "summary": jobs_summary(),
            "jobs": [job.to_dict() for job in jobs],
        },
    })


@node_bp.route('/api/sync/retry', methods=['POST'])
@require_write_role
def sync_retry():
    """重放一批待补偿任务。请求体可选：{"limit": 50}

    返回逐条结果统计。逐条独立，一条失败不影响后面的——否则一个持续失败的节点
    会永久堵住整个队列。
    """
    from sync_compensation import MAX_ATTEMPTS, retry_pending

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 50)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "limit 必须是整数"}), 400

    stats = retry_pending(limit=limit)
    logger.info("补偿同步重试：%s（由 user_id=%s 触发）", stats, getattr(g, "user_id", None))
    return jsonify({
        "code": 200,
        "msg": f"重试完成：成功 {stats['succeeded']} / 失败 {stats['failed']}"
               + (f"（其中 {stats['abandoned']} 条已达上限 {MAX_ATTEMPTS} 次，转人工处理）"
                  if stats["abandoned"] else ""),
        "data": stats,
    })
