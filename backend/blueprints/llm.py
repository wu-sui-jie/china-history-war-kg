"""大模型相关路由（P2-1 收官：从 app.py 按业务分组迁出）。

三条都受 require_write_role 保护（会消耗 LLM 配额）：旧问答（同步与 SSE 流式）与文本抽取。
SSE 那条直接返回 Response(generator, mimetype='text/event-stream')，不要包成 jsonify。
**URL 与行为逐字未变**。
"""

import time
import traceback
import uuid

from flask import Blueprint, Response, g, jsonify, request

from common_utils import brief_error
from logging_util import get_logger
from roles import require_write_role

import llm_pipeline

llm_bp = Blueprint("llm", __name__)
logger = get_logger(__name__)


@llm_bp.route('/api/ai/inference', methods=['POST', 'GET'])
@require_write_role
def ai_inference():
    """智能问答推理接口

    请求参数:
        POST/GET: question(用户问题)

    处理流程:
        1. 实体提取: 从问题中识别历史实体
        2. 图谱查询: 从Neo4j查询实体关系
        3. 规则推理: 应用规则推导隐含关系
        4. 大模型生成: 生成自然语言回答

    响应:
        - success: true/false
        - answer: AI回答内容
        - kg_data: 知识图谱可视化数据
        - entities: 识别到的实体列表
        - process_time: 处理耗时
    """
    try:
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': '请求参数不能为空'
                }), 400

            user_question = data.get('question', '')
        else:
            user_question = request.args.get('question', '')

        if not user_question or len(user_question.strip()) == 0:
            return jsonify({
                'success': False,
                'error': '问题不能为空'
            }), 400

        request_id = str(uuid.uuid4())[:8]
        logger.info(f"[{request_id}] 收到大模型推理请求: '{user_question}'")

        # 推理引擎与实体提取器优先用 before_request 建好的进程内单例；
        # 单例缺失（如启动时初始化失败）时按请求临时建一份，与原先一致。
        if not hasattr(g, 'rule_llm_integration'):
            logger.info(f"[{request_id}] 初始化规则推理模块")
            try:
                from inference.rule_llm_integration import RuleLLMIntegration
                g.rule_llm_integration = RuleLLMIntegration(
                    rule_file_path='rules/rule_base.json',
                    model_name='deepseek-r1:7b',
                    max_depth=3
                )
            except Exception as init_err:
                logger.warning(f"[{request_id}] 初始化规则推理模块失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '系统初始化失败，请稍后再试',
                    'answer': '抱歉，推理系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        if not hasattr(g, 'entity_extractor'):
            logger.info(f"[{request_id}] 初始化实体提取器")
            try:
                from entity_extract.extractor import Extractor
                g.entity_extractor = Extractor()
            except Exception as init_err:
                logger.warning(f"[{request_id}] 初始化实体提取器失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '实体提取器初始化失败，请稍后再试',
                    'answer': '抱歉，地名识别系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        payload, status = llm_pipeline.run_inference(
            g.rule_llm_integration, g.entity_extractor, user_question, request_id
        )
        return jsonify(payload), status

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.info(f"处理推理请求时出错: {error_type} - {error_msg}")
        traceback.print_exc()

        return jsonify({
            'success': False,
            'error': f'请求处理错误: {error_type}',
            'error_detail': error_msg,
            'answer': f"抱歉，系统无法处理您的请求。请检查输入格式是否正确，或稍后再试。",
            'kg_data': {'nodes': [], 'lines': []}
        }), 500


@llm_bp.route('/api/ai/inference/stream', methods=['POST'])
@require_write_role
def ai_inference_stream():
    """智能问答推理接口 - SSE流式输出版本

    处理流程:
        1. 实体提取: 从问题中识别历史实体
        2. 图谱查询: 从Neo4j查询实体关系
        3. 规则推理: 应用规则推导隐含关系
        4. 大模型生成: 流式生成自然语言回答

    响应: SSE (Server-Sent Events) 流
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求参数不能为空'}), 400

        user_question = data.get('question', '')
        if not user_question or len(user_question.strip()) == 0:
            return jsonify({'success': False, 'error': '问题不能为空'}), 400

        request_id = str(uuid.uuid4())[:8]
        logger.info(f"[{request_id}] 收到SSE推理请求: '{user_question}'")

        # 获取推理引擎和实体提取器（同上：单例优先，缺失时按请求临时建一份）
        if not hasattr(g, 'rule_llm_integration') or not hasattr(g, 'entity_extractor'):
            try:
                from inference.rule_llm_integration import RuleLLMIntegration
                from entity_extract.extractor import Extractor
                g.rule_llm_integration = RuleLLMIntegration(
                    rule_file_path='rules/rule_base.json',
                    model_name='deepseek-r1:7b',
                    max_depth=30
                )
                g.entity_extractor = Extractor()
            except Exception as init_err:
                logger.warning(f"[{request_id}] 初始化失败: {str(init_err)}")
                return jsonify({'success': False, 'error': '系统初始化失败'}), 500

        # 在生成器外部捕获Flask上下文对象，避免在生成器内访问g
        rule_engine = g.rule_llm_integration
        entity_ext = g.entity_extractor

        return Response(
            llm_pipeline.stream_inference(rule_engine, entity_ext, user_question, request_id),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache',
                     'Connection': 'keep-alive',
                     'X-Accel-Buffering': 'no'}
        )

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.warning(f"SSE请求处理错误: {error_type} - {error_msg}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'请求处理错误: {error_msg}'}), 500


@llm_bp.route('/api/extract/entities-events', methods=['POST'])
@require_write_role
def extract_entities_events():
    """
    文本实体与事件识别接口 - 完整版

    请求参数(JSON):
        - text: 用户输入的文本内容

    响应:
        - code: 200(成功) / 400(参数错误) / 500(服务器错误)
        - data:
            - entities: {places, organizations, persons} 识别到的实体及其属性
            - events: 识别到的事件及其属性
            - relations: {event_place, event_person, event_organization, event_event} 关系
            - process_time: 处理耗时
    """
    try:
        # silent=True：畸形 JSON 也让 get_json 返回 None，走下面的 400 分支给 JSON 响应。
        # 不加 silent 时 Flask 直接抛 400，客户端拿到的是 HTML 错误页而不是 {code,msg}。
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"code": 400, "msg": "请求参数不能为空", "data": {}}), 400

        text = data.get('text', '').strip()
        if not text:
            return jsonify({"code": 400, "msg": "文本内容不能为空", "data": {}}), 400

        if len(text) > 1000:
            return jsonify({"code": 400, "msg": "文本内容过长，请限制在1000字符以内", "data": {}}), 400

        start_time = time.time()

        try:
            from war_extraction.extractors.entity_extractor import EntityExtractor
            from war_extraction.extractors.event_extractor import EventExtractor
            from war_extraction.extractors.relation_extractor import RelationExtractor
            from war_extraction.utils import EntityClassifier, Normalizer
        except ImportError as import_err:
            logger.warning(f"导入提取器模块失败: {import_err}")
            return jsonify({
                "code": 500,
                "msg": f"提取器模块导入失败: {brief_error(import_err)}",
                "data": {}
            }), 500

        # 初始化LLM客户端
        try:
            llm = llm_pipeline.OllamaAdapter("deepseek-r1:7b")
            logger.info("[提取] 使用本地Ollama模型: deepseek-r1:7b")
        except Exception as llm_err:
            logger.warning(f"LLM客户端初始化失败: {llm_err}")
            return jsonify({
                "code": 500,
                "msg": f"LLM客户端初始化失败: {brief_error(llm_err)}",
                "data": {}
            }), 500

        # 使用优化的单次抽取方案
        logger.info(f"[提取] 开始单次综合抽取，文本长度: {len(text)}")

        # 第 4 个返回值是本次抽取的诊断信息（阶段成败、部分失败原因），见 llm_pipeline
        entities, event_result, relations, diagnostics = llm_pipeline.extract_all_optimized(llm, text)

        logger.info(f"[提取] 抽取完成: {len(entities.places)}地点, {len(entities.organizations)}组织, {len(entities.persons)}人物, {len(event_result.events)}事件")
        logger.info(f"[提取] 关系: {len(relations.event_place_relations)}事件-地点, {len(relations.event_person_relations)}事件-人物, {len(relations.event_organization_relations)}事件-组织, {len(relations.event_event_relations)}事件-事件")

        response_data = llm_pipeline.serialize_extraction_result(
            entities, event_result, relations, time.time() - start_time
        )

        # 部分阶段/分段失败：结果照常返回（局部成功仍然有用），但要如实带上失败说明——
        # 否则用户会把"少了一半关系"当成完整结果，把模型故障当成"文本里没写"。
        partial_errors = diagnostics.get("partial_errors") or []
        if partial_errors:
            response_data["partial_errors"] = partial_errors
            logger.warning("[提取] 本次有 %s 处阶段失败，结果可能不完整", len(partial_errors))

        return jsonify({
            "code": 200,
            "msg": "识别完成",
            "data": response_data
        })

    except llm_pipeline.ExtractionUnavailable as unavailable:
        # 所有阶段都没成功（典型是 Ollama 没起）：这里必须是 5xx——包成 200 的"识别完成"
        # 等于把故障说成"这段文本没有实体"（第 6 轮审核 M1）。
        logger.warning(f"文本实体识别整体失败: {unavailable}")
        return jsonify({
            "code": 500,
            "msg": f"识别服务当前不可用: {brief_error(unavailable, 300)}",
            "data": {}
        }), 500
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.warning(f"文本实体识别失败: {error_type} - {error_msg}")
        traceback.print_exc()
        return jsonify({
            "code": 500,
            "msg": f"识别失败（{error_type}）: {brief_error(e)}",
            "data": {}
        }), 500
