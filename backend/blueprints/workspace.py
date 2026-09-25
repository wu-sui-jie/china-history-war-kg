"""数据运营与只读报表路由（P2-1 收官：从 app.py 按业务分组迁出）。

仪表盘、数据集与版本、图谱质检、实体详情、时间轴、地图、修复工单——都是"看数据"的接口，
实现体在 report_builders.py 里，这里只做参数解析与响应包装。**URL 未变**；
错误口径按第 13 轮复核第七节迁移：统一 JSON + 正确 HTTP 状态 + 不外发异常原文
（改动前这里一律是 `HTTP 200 + code 500 + msg=str(e)`，stderr 里全是库内部信息）。
"""

from flask import Blueprint, jsonify, request

import api_errors
from logging_util import get_logger
from report_builders import (
    build_dashboard_overview,
    build_dataset_overview,
    build_dataset_versions,
    build_entity_detail,
    build_map_overview,
    build_quality_report,
    build_quality_workbench,
    build_timeline_overview,
)

workspace_bp = Blueprint("workspace", __name__)
logger = get_logger(__name__)


@workspace_bp.route('/api/dashboard/overview', methods=['GET'])
def get_dashboard_overview():
    """首页仪表盘接口。"""
    try:
        return jsonify({"code": 200, "data": build_dashboard_overview()})
    except Exception as e:
        return api_errors.server_error("加载首页数据失败", e, data={})


@workspace_bp.route('/api/dataset/overview', methods=['GET'])
def get_dataset_overview():
    """数据集中心接口。"""
    try:
        return jsonify({"code": 200, "data": build_dataset_overview()})
    except Exception as e:
        return api_errors.server_error("加载数据集概览失败", e, data={})


@workspace_bp.route('/api/dataset/versions', methods=['GET'])
def dataset_versions():
    try:
        return jsonify({"code": 200, "data": build_dataset_versions()})
    except Exception as e:
        return api_errors.server_error("加载数据版本失败", e, data=[])


@workspace_bp.route('/api/dataset/version_detail', methods=['GET'])
def dataset_version_detail():
    try:
        version_id = request.args.get("id", "")
        versions = build_dataset_versions()
        detail = next((item for item in versions if item["id"] == version_id), versions[0] if versions else {})
        return jsonify({"code": 200, "data": detail})
    except Exception as e:
        return api_errors.server_error("加载版本详情失败", e, data={})


@workspace_bp.route('/api/quality/report', methods=['GET'])
def get_quality_report():
    """图谱质检接口。"""
    try:
        return jsonify({"code": 200, "data": build_quality_report()})
    except Exception as e:
        return api_errors.server_error("加载质检报告失败", e, data={})


@workspace_bp.route('/api/quality/workbench', methods=['GET'])
def get_quality_workbench():
    """数据修复工作台接口。"""
    try:
        return jsonify({"code": 200, "data": build_quality_workbench()})
    except Exception as e:
        return api_errors.server_error("加载修复工作台失败", e, data={})


@workspace_bp.route('/api/entity/detail', methods=['GET'])
def get_entity_detail():
    """实体详情页聚合接口。"""
    try:
        node_type = request.args.get('type', '')
        node_id = request.args.get('id', type=int)
        if not node_type or not node_id:
            return jsonify(api_errors.error_payload(400, "type 和 id 不能为空", data={})), 400

        data = build_entity_detail(node_type, node_id)
        if not data:
            return jsonify(api_errors.error_payload(404, "实体不存在", data={})), 404

        return jsonify({"code": 200, "data": data})
    except Exception as e:
        return api_errors.server_error("加载实体详情失败", e, data={})


@workspace_bp.route('/api/timeline/overview', methods=['GET'])
def get_timeline_overview():
    """时间轴页面接口。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        participant = request.args.get('participant', '')
        return jsonify({"code": 200, "data": build_timeline_overview(keyword, dynasty, participant)})
    except Exception as e:
        return api_errors.server_error("加载时间轴概览失败", e, data={})


@workspace_bp.route('/api/timeline/events', methods=['GET'])
def get_timeline_events():
    """时间轴事件列表。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        participant = request.args.get('participant', '')
        event_type = request.args.get('event_type', '')
        only_issues = request.args.get('only_issues', '0') == '1'
        data = build_timeline_overview(keyword, dynasty, participant)
        events = data.get("events", [])
        if event_type:
            events = [item for item in events if item.get("event_type") == event_type]
        if only_issues:
            events = [item for item in events if item.get("quality_flags", {}).get("timeline_problems")]
        data["events"] = events
        data["event_types"] = sorted({item.get("event_type") for item in events if item.get("event_type")})
        return jsonify({"code": 200, "data": data})
    except Exception as e:
        return api_errors.server_error("加载时间轴事件失败", e, data={"events": []})


@workspace_bp.route('/api/map/events', methods=['GET'])
def get_event_map():
    """历史地图视图数据。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        return jsonify({"code": 200, "data": build_map_overview(keyword, dynasty)})
    except Exception as e:
        return api_errors.server_error("加载地图数据失败", e, data={"points": []})


@workspace_bp.route('/api/repair/issues', methods=['GET'])
def get_repair_issues():
    """修复工作台问题列表。"""
    try:
        workbench = build_quality_workbench()
        issue_type = request.args.get('type', '')
        issues = workbench.get("issue_queue", [])
        if issue_type:
            issues = [item for item in issues if item.get("issue_type") == issue_type]
        return jsonify({"code": 200, "data": {"summary": workbench.get("summary", {}), "issues": issues}})
    except Exception as e:
        return api_errors.server_error("加载修复工单失败", e, data={"issues": []})
