"""图谱可视化与检索路由。

覆盖全图检索（search_name_kg）、四类子页面关系图、单节点一阶子图、关系分析与全局搜索。
**URL 未变**；错误口径：统一 JSON + 正确 HTTP 状态 + 不外发异常原文
（不要回 `HTTP 200 + code 500 + msg=str(e)`，详见 api_errors 的模块文档）。
"""

from flask import Blueprint, jsonify, request

import api_errors
from db_handle import neo4j_db_handle
from logging_util import get_logger
from report_builders import build_global_search

graph_bp = Blueprint("graph", __name__)
logger = get_logger(__name__)


@graph_bp.route('/search_name_kg', methods=['POST'])
def search_name():
    """
    知识图谱搜索 - 从Neo4j读取（可视化展示）
    保持原样，前端可视化仍从Neo4j读取
    """
    data, error = api_errors.json_body()
    if error:
        return error
    entity = data.get('name', '')
    node_type = data.get('node_type', '')
    rel_type = data.get('rel_type', '')

    try:
        if not entity and not node_type and not rel_type:
            # 不带筛选 = 战争关系图首页：走"四类均衡取样"的总览。
            # `get_overview_graph` 的文档写了为什么不能再用 `MATCH (n) RETURN n LIMIT N`。
            #
            # 这里曾有一个 `load_all` 分支配 `MAX_LOAD_ALL_NODES = 3000` 的拦截：本库有
            # 9184 个节点，拦截恒定命中，"加载全部节点"按钮永远只是弹一句"已按限量加载"。
            # 而全量渲染也不是可行路线——四个子页实测 200 个节点就会让力导向布局卡顿。
            # 所以整条全量分支连同按钮一起下线：要看某一块，用名称/关系筛选聚焦。
            json_data = neo4j_db_handle.get_overview_graph()
        else:
            if entity and node_type and not rel_type:
                json_data = neo4j_db_handle.search_by_name_and_type(entity, node_type)
                logger.info("名称+类型组合查询")
            elif entity and rel_type:
                json_data = neo4j_db_handle.search_by_name_and_relation(entity, rel_type)
                logger.info(f"组合查询：名称+关系 {entity} + {rel_type}")
            elif entity:
                json_data = neo4j_db_handle.search_nodes_by_name(entity)
                logger.info(f"按实体名称'{entity}'搜索")
            elif node_type:
                json_data = neo4j_db_handle.get_nodes_by_type(node_type)
                logger.info(f"按节点类型'{node_type}'筛选")
            elif rel_type:
                json_data = neo4j_db_handle.get_nodes_by_relationship(rel_type)
                logger.info(f"按关系类型'{rel_type}'筛选")

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": json_data,
            # 本次实际用的加载方式：前端据此区分"总览取样"与"按条件聚焦"两种语义。
            # 取样规模不再由后端按节点总数临时决定——那会让同一页在不同数据规模下换口径。
            "graph_mode": "focused" if (entity or node_type or rel_type) else "overview",
        })
    except Exception as e:
        # 完整堆栈由 server_error 记进日志（它内部就是 logger.exception，
        # 不要再加一次 traceback.print_exc()）；响应里只有安全文案 + request_id
        return api_errors.server_error("图谱搜索失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/graph/event_event', methods=['GET'])
def get_event_event_graph():
    """
    获取事件-事件关系图（关联战争子页面）
    只展示战争事件之间的关联关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')

        result = neo4j_db_handle.get_event_event_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return api_errors.server_error("加载事件关系图失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/graph/event_organization', methods=['GET'])
def get_event_organization_graph():
    """
    获取事件-组织关系图（参战势力子页面）
    展示参战势力与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')

        result = neo4j_db_handle.get_event_organization_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return api_errors.server_error("加载势力关系图失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/graph/event_person', methods=['GET'])
def get_event_person_graph():
    """
    获取事件-人物关系图（相关人物子页面）
    展示相关人物与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')

        result = neo4j_db_handle.get_event_person_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return api_errors.server_error("加载人物关系图失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/graph/event_place', methods=['GET'])
def get_event_place_graph():
    """
    获取事件-地点关系图（发生地点子页面）
    展示发生地点与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')

        result = neo4j_db_handle.get_event_place_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return api_errors.server_error("加载地点关系图失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/graph/node_context', methods=['GET'])
def get_node_context_graph():
    """获取单个实体的一阶关系子图，用于从实体详情页跳转到图谱时聚焦。"""
    try:
        graph_id = request.args.get('graph_id', type=int)
        name = request.args.get('name', '')

        if graph_id is None and name:
            search_result = neo4j_db_handle.search_nodes_by_name(name, limit=1)
            nodes = search_result.get('nodes', []) if search_result else []
            if nodes:
                graph_id = nodes[0].get('id')

        if graph_id is None:
            # 参数不足是 400（改前是 HTTP 200 + code 400）：前者才是"客户端用错了接口"
            # 的标准表达，也让前端/反代/监控能按状态码分流。
            return jsonify(api_errors.error_payload(400, "缺少可定位的图谱节点",
                                                    data={"nodes": [], "lines": []})), 400

        result = neo4j_db_handle.get_node_relations(graph_id)
        return jsonify({"code": 200, "msg": "success", "data": result})
    except Exception as e:
        return api_errors.server_error("加载节点子图失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/relation-analysis/query', methods=['GET', 'POST'])
def relation_analysis_query():
    """关系分析查询：支持实体名称、类型和一跳/二跳深度。"""
    try:
        data = request.get_json() if request.method == 'POST' else request.args
        name = data.get('name', '')
        node_type = data.get('type', '')
        try:
            depth = int(data.get('depth', 1) or 1)
        except (TypeError, ValueError):
            # 非法 depth 改前会被下面的兜底 except 收成 500 + str(e)（"invalid literal for
            # int()"），把一次参数错误说成服务器故障——排查方向一开始就是错的
            return jsonify(api_errors.error_payload(
                400, "depth 必须是整数", data={"nodes": [], "lines": []})), 400
        rel_type = data.get('rel_type', '')

        if rel_type:
            graph_data = neo4j_db_handle.search_by_name_and_relation(name, rel_type, limit=120)
        elif name and node_type:
            graph_data = neo4j_db_handle.search_by_name_and_type(name, node_type, limit=120)
        elif name:
            search_result = neo4j_db_handle.search_nodes_by_name(name, limit=1)
            nodes = search_result.get("nodes", [])
            graph_data = neo4j_db_handle.get_node_relations(nodes[0]["id"]) if nodes else {"nodes": [], "lines": []}
        else:
            graph_data = {"nodes": [], "lines": []}

        if depth >= 2 and graph_data.get("nodes"):
            merged_nodes = {item["id"]: item for item in graph_data["nodes"]}
            merged_lines = {f"{item['from']}-{item['to']}-{item.get('text','')}": item for item in graph_data.get("lines", [])}
            for node in list(graph_data["nodes"])[:8]:
                sub_graph = neo4j_db_handle.get_node_relations(node["id"])
                for sub_node in sub_graph.get("nodes", []):
                    merged_nodes[sub_node["id"]] = sub_node
                for line in sub_graph.get("lines", []):
                    merged_lines[f"{line['from']}-{line['to']}-{line.get('text','')}"] = line
            graph_data = {"nodes": list(merged_nodes.values()), "lines": list(merged_lines.values())}

        return jsonify({"code": 200, "data": graph_data})
    except Exception as e:
        return api_errors.server_error("关系分析失败", e, data={"nodes": [], "lines": []})


@graph_bp.route('/api/search/global', methods=['GET'])
def global_search():
    try:
        keyword = request.args.get("keyword", "")
        return jsonify({"code": 200, "data": build_global_search(keyword)})
    except Exception as e:
        return api_errors.server_error("全局搜索失败", e, data=[])
