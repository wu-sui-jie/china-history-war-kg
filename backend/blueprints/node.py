"""节点增删改查与节点查询路由（P2-1 收官：从 app.py 按业务分组迁出）。

含四类写接口（create/update/delete_node、update_properties，均受 require_write_role 保护）
与只读的节点/关系类型查询。**URL 与行为逐字未变**。
"""

from flask import Blueprint, jsonify, request

from db_handle import neo4j_db_handle
from db_utils import DbUtil
from logging_util import get_logger
from roles import require_write_role

node_bp = Blueprint("node", __name__)
logger = get_logger(__name__)


@node_bp.route('/api/find_node_page', methods=['POST'])
def find_list():
    """
    节点列表查询 - 改为从SQLite查询
    支持分页、名称搜索和类型过滤
    """
    try:
        current = int(request.json.get('pageNum', 1))
        limit = int(request.json.get('pageSize', 10))
        name_query = request.json.get('name', '')
        node_type = request.json.get('node_type', '')

        # 使用DbUtil从SQLite查询
        result = DbUtil.find_node_page(current, limit, name_query, node_type if node_type else None)

        return jsonify({
            "code": 200,
            "data": result
        })
    except Exception as e:
        logger.warning(f"查询节点列表失败: {e}")
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": {"total": 0, "records": []}
        })


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
    try:
        data = request.json
        node_type = data.get("type")
        name = data.get("name")
        # 提取除type和name外的其他属性
        properties = {k: v for k, v in data.items() if k not in ['type', 'name']}

        result = DbUtil.create_node(node_type, name, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"创建节点失败: {str(e)}"
        })


@node_bp.route('/update_node', methods=['POST'])
@require_write_role
def update_node():
    """
    更新节点 - 更新SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_type = data.get("type")
        node_id = data.get("id")
        new_name = data.get("name")
        properties = {k: v for k, v in data.items() if k not in ['type', 'id', 'name']}

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node(node_type, node_id, new_name, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"更新节点失败: {str(e)}"
        })


@node_bp.route('/delete_node', methods=['POST'])
@require_write_role
def delete_node():
    """
    删除节点 - 删除SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_type = data.get("type")
        node_id = data.get("id")

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.delete_node(node_type, node_id)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"删除节点失败: {str(e)}"
        })


@node_bp.route('/api/node/detail', methods=['GET'])
def get_node_detail():
    """
    获取节点详情 - 优先从SQLite读取，如不存在则从Neo4j读取
    """
    try:
        node_id = request.args.get('id')
        node_type = request.args.get('type')

        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

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
            return jsonify({
                "code": 404,
                "msg": "节点不存在"
            })

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": node_detail
        })

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@node_bp.route('/api/node/update_properties', methods=['POST'])
@require_write_role
def update_node_properties():
    """
    更新节点属性 - 更新SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_id = data.get("id")
        node_type = data.get("type")
        properties = data.get("properties", {})

        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

        if not properties or not isinstance(properties, dict):
            return jsonify({
                "code": 400,
                "msg": "节点属性格式不正确"
            })

        # 从properties中提取type
        if not node_type and 'type' in properties:
            node_type = properties['type']

        if not node_type:
            return jsonify({
                "code": 400,
                "msg": "无法确定节点类型"
            })

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node_properties(node_id, node_type, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


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
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


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
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


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
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@node_bp.route('/api/node/relations', methods=['GET'])
def get_node_relations():
    try:
        node_id = request.args.get('id')
        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

        result = neo4j_db_handle.get_node_relations(node_id)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@node_bp.route('/api/node/by_type', methods=['GET'])
def get_nodes_by_type():
    try:
        node_type = request.args.get('type')
        if not node_type:
            return jsonify({
                "code": 400,
                "msg": "节点类型不能为空"
            })

        result = neo4j_db_handle.get_nodes_by_type(node_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@node_bp.route('/api/node/by_relationship', methods=['GET'])
def get_nodes_by_relationship():
    try:
        rel_type = request.args.get('type')
        if not rel_type:
            return jsonify({
                "code": 400,
                "msg": "关系类型不能为空"
            })

        result = neo4j_db_handle.get_nodes_by_relationship(rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@node_bp.route('/api/node/search_by_name', methods=['GET'])
def search_nodes_by_name():
    try:
        search_text = request.args.get('name')
        limit = request.args.get('limit', 100, type=int)

        if not search_text:
            return jsonify({
                "code": 400,
                "msg": "搜索文本不能为空"
            })

        result = neo4j_db_handle.search_nodes_by_name(search_text, limit)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })
