"""LLM 流水线：模型调用、文本抽取链与问答编排（P2-1 第二步）。

拆出范围（原先全在 app.py，合计约 1000 行）：

- `OllamaAdapter`：本地 Ollama 调用适配（重试 + 剥离 Markdown 代码块）。
- 抽取链：`extract_all_optimized`（分段 → 实体 → 事件 → 关系，含去重与字段归一）
  与它依赖的三个归一化函数（朝代、角色、事件名）。
- 问答编排：`run_inference`（同步）与 `stream_inference`（SSE 流式，产出 `data: {...}` 帧）。
- 进程内单例：实体提取器与规则推理引擎（懒建 + 锁 + 双重检查）。
- `init_user_dict`：从 `data/data.json` 生成 jieba 用户词典。

**仍留在 app.py 的**：`@app.route` 处理函数本体、请求参数校验与错误响应包装。
路由蓝图（Blueprints）是后面的独立一步，因此本模块只暴露普通函数——不依赖
`flask.request` / `flask.g`，也不 import app（会形成循环）。需要数据库时走
`db_handle.neo4j_db_handle` 单例句柄。

行为兼容性：帧格式、日志文案、错误分类与响应体字段一律照搬，只改承载位置。
"""

import json
import os
import re
import threading
import time
import traceback

from db_handle import neo4j_db_handle
from dynasty_data import DYNASTY_CORRECTIONS, VALID_DYNASTIES
from eer_path import ensure_eer_on_path
from logging_util import get_logger

# EER 未正式打包（P2-4），导入 src.* 之前必须先注入路径
ensure_eer_on_path()

from inference.rule_llm_integration import DYNASTY_SCOPE_MAP  # noqa: E402
from src.models import (  # noqa: E402
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult,
)
from src.extractors.entity_extractor import EntityExtractor  # noqa: E402
from src.extractors.event_extractor import EventExtractor  # noqa: E402
from src.extractors.relation_extractor import RelationExtractor  # noqa: E402
from src.core.text_splitter import TextSplitter  # noqa: E402

logger = get_logger(__name__)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


# ================== 模型调用适配 ==================


class OllamaAdapter:
    """使用本地Ollama模型的适配器"""

    def __init__(self, model_name="deepseek-r1:7b"):
        import ollama
        self.model = model_name
        self.ollama = ollama

    def call(self, prompt: str, temperature: float = 0.1, max_retries: int = 3, json_mode: bool = False) -> str:
        """调用Ollama模型"""
        import json
        for attempt in range(max_retries):
            try:
                response = self.ollama.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": temperature},
                    stream=False,
                    keep_alive="10m"
                )
                content = response['message']['content']

                # 清理可能的Markdown代码块
                if "```json" in content:
                    start = content.find("```json") + 7
                    end = content.find("```", start)
                    if end > start:
                        content = content[start:end].strip()
                elif "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    if end > start:
                        content = content[start:end].strip()

                return content
            except Exception as e:
                logger.warning(f"Ollama调用失败，重试 {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1)
                else:
                    raise


# ================== 字段归一化 ==================


def _to_str(value):
    """将LLM返回的list/其他类型统一转为字符串，避免Pydantic类型校验失败"""
    if value is None:
        return None
    if isinstance(value, list):
        return "、".join(str(v) for v in value if v)
    return str(value)


def _normalize_dynasty(name):
    """校验并纠正朝代名称，将LLM输出的错误朝代名（如人名"商汤"）纠正为正确朝代"""
    if not name:
        return name
    name = str(name).strip()
    if not name:
        return name

    # 1. 精确匹配合法朝代
    if name in VALID_DYNASTIES:
        return name

    # 2. 查找已知错误映射
    if name in DYNASTY_CORRECTIONS:
        return DYNASTY_CORRECTIONS[name]

    # 3. 模糊匹配：如果名称包含某个合法朝代，提取该朝代
    #    例如 "商汤" 包含 "商"，"汉武帝" 包含 "汉"（但汉需要特殊处理）
    for dynasty in VALID_DYNASTIES:
        if dynasty in name and len(dynasty) >= 1:
            return dynasty

    return name


# 合法角色列表（来自entity-event-relation提示词模板）
VALID_ROLES = ["统帅", "将领", "谋士", "君主", "使者", "参战者"]

# 常见错误角色名 → 正确角色名 映射
_ROLE_CORRECTIONS = {
    "king": "君主", "emperor": "君主", "ruler": "君主", "monarch": "君主", "sovereign": "君主",
    "queen": "君主", "prince": "君主", "lord": "君主",
    "general": "将领", "commander": "统帅", "marshal": "统帅",
    "strategist": "谋士", "advisor": "谋士", "counselor": "谋士",
    "envoy": "使者", "emissary": "使者", "messenger": "使者",
    "soldier": "参战者", "warrior": "参战者", "fighter": "参战者",
    "首领": "统帅", "头领": "统帅", "主帅": "统帅", "主将": "将领",
    "将军": "将领", "军师": "谋士", "谋臣": "谋士", "大臣": "参战者",
    "将领": "将领", "统帅": "统帅", "谋士": "谋士", "君主": "君主",
}


def _normalize_role(name):
    """校验并纠正角色名称，将LLM输出的错误角色名（如英文"king"）纠正为中文"""
    if not name:
        return name
    name = str(name).strip()
    if not name:
        return name

    # 1. 精确匹配合法角色
    if name in VALID_ROLES:
        return name

    # 2. 查找已知错误映射（不区分大小写）
    lower_name = name.lower()
    if lower_name in _ROLE_CORRECTIONS:
        return _ROLE_CORRECTIONS[lower_name]

    # 3. 模糊匹配：如果名称包含某个合法角色
    for role in VALID_ROLES:
        if role in name:
            return role

    # 4. 默认返回"参战者"
    return "参战者"


def _normalize_event_name(event_name, source_text=""):
    """校验事件名称，确保名称出现在原文中或符合命名规范"""
    if not event_name:
        return event_name
    event_name = str(event_name).strip()
    if not event_name:
        return event_name

    # 如果事件名称直接出现在原文中，认为是正确的
    if source_text and event_name in source_text:
        return event_name

    # 检查是否符合"XX之战"、"XX之战"等规范命名
    if re.search(r'[一-龥]+之战', event_name):
        return event_name

    # 如果不符合规范，尝试从原文中提取正确的事件名称
    # 常见模式：XX之战、XX之役、XX大战
    if source_text:
        patterns = [
            r'([一-龥]{2,6}之战)',
            r'([一-龥]{2,6}之役)',
            r'([一-龥]{2,6}大战)',
            r'([一-龥]{2,6}会战)',
        ]
        for pattern in patterns:
            match = re.search(pattern, source_text)
            if match:
                return match.group(1)

    return event_name


# ================== 文本抽取链 ==================


class ExtractionUnavailable(RuntimeError):
    """抽取链整体不可用（所有阶段、所有分段都没成功过一次）。

    单段/单阶段失败继续跑，是为了"局部失败不整体作废"；但如果每一段都失败还照旧返回，
    接口就会给出 HTTP 200 + code 200 + "识别完成" + 全空结果——Ollama 宕机时看起来像
    "这段文本里没有实体"，这是最坏的一种错（假成功）。所以全失败时抛本异常，
    由接口按 5xx 处理，把真实原因说出去。
    """

    def __init__(self, reasons):
        self.reasons = list(reasons)
        summary = "；".join(self.reasons[:3]) or "抽取服务不可用"
        if len(self.reasons) > 3:
            summary += "（另有 %d 条同类错误）" % (len(self.reasons) - 3)
        super().__init__(summary)


def extract_all_optimized(llm, text: str):
    """
    使用entity-event-relation模块的原始抽取器和模板
    - EntityExtractor: 实体抽取（ENTITY_EXTRACTION_PROMPT）
    - EventExtractor: 事件抽取（EVENT_IDENTIFICATION_PROMPT + FULL_EVENT_PROMPT）
    - RelationExtractor: 关系抽取（RELATION_EXTRACTION_PROMPT）
    支持长文本分段处理，自动合并去重

    返回 `(entities, event_result, relations, diagnostics)`：
    `diagnostics["partial_errors"]` 是"失败了但没让整次抽取作废"的阶段/分段错误说明，
    接口把它带进响应体，界面据此提示"结果可能不完整"。
    全部阶段都失败时抛 `ExtractionUnavailable`，不返回空结果。
    """

    # 文本分段处理（减小分段，减少LLM调用次数）
    try:
        splitter = TextSplitter(chunk_size=1200, overlap=150)
        chunks = splitter.split(text)
    except Exception:
        chunks = [(0, len(text), text)]

    # 初始化抽取器
    entity_extractor = EntityExtractor(llm)
    event_extractor = EventExtractor(llm)
    relation_extractor = RelationExtractor(llm)

    # 阶段成败计数与失败原因：用来区分"这段没有实体"（成功、结果为空）与
    # "模型根本没答上来"（失败）。只看结果是否为空是分不出来的。
    stage_ok = {"entity": 0, "event": 0, "relation": 0}
    partial_errors = []

    def fail(stage, exc, chunk_start, chunk_end):
        message = "[%s-%s] %s阶段失败: %s" % (chunk_start, chunk_end, stage, exc)
        logger.warning("[提取] %s", message)
        partial_errors.append(message)

    # 累积结果容器
    all_places = []
    all_orgs = []
    all_persons = []
    all_events = []
    all_event_place_rels = []
    all_event_person_rels = []
    all_event_org_rels = []
    all_event_event_rels = []

    seen_places = set()
    seen_orgs = set()
    seen_persons = set()
    seen_events = set()

    for chunk_start, chunk_end, chunk_text in chunks:
        if not chunk_text.strip():
            continue

        logger.info(f"[提取] 处理文本段: {chunk_start}-{chunk_end} ({len(chunk_text)}字)")

        # ========== 第1阶段：实体抽取 ==========
        try:
            chunk_entities = entity_extractor.extract(chunk_text)
            stage_ok["entity"] += 1
            logger.info(f"[提取] 实体抽取完成: {len(chunk_entities.places)}地点, {len(chunk_entities.organizations)}组织, {len(chunk_entities.persons)}人物")
        except Exception as e:
            fail("实体抽取", e, chunk_start, chunk_end)
            chunk_entities = EntityExtractionResult()

        # 收集实体（去重）
        chunk_place_names = []
        for p in chunk_entities.places:
            name = _to_str(p.geo_name) or ""
            name = name.strip()
            if name and name not in seen_places:
                seen_places.add(name)
                # 类型安全处理 + 朝代校验
                p.geo_name = name
                p.modern_name = _to_str(p.modern_name)
                p.DynastyName = _normalize_dynasty(_to_str(p.DynastyName))
                p.Province = _to_str(p.Province)
                p.City = _to_str(p.City)
                p.District_County = _to_str(p.District_County)
                p.Specific_location = _to_str(p.Specific_location)
                p.source_text = _to_str(p.source_text)
                all_places.append(p)
            if name:
                chunk_place_names.append(name)

        chunk_org_names = []
        for o in chunk_entities.organizations:
            name = _to_str(o.OrgName) or ""
            name = name.strip()
            if name and name not in seen_orgs:
                seen_orgs.add(name)
                o.OrgName = name
                o.OrgType = _to_str(o.OrgType)
                o.DynastyName = _normalize_dynasty(_to_str(o.DynastyName))
                o.source_text = _to_str(o.source_text)
                all_orgs.append(o)
            if name:
                chunk_org_names.append(name)

        chunk_person_names = []
        for p in chunk_entities.persons:
            name = _to_str(p.PersonName) or ""
            name = name.strip()
            if name and name not in seen_persons:
                seen_persons.add(name)
                p.PersonName = name
                p.DynastyName = _normalize_dynasty(_to_str(p.DynastyName))
                p.OrgName = _to_str(p.OrgName)
                p.Role = _normalize_role(_to_str(p.Role))
                p.Note = _to_str(p.Note)
                p.source_text = _to_str(p.source_text)
                all_persons.append(p)
            if name:
                chunk_person_names.append(name)

        # ========== 第2阶段：事件抽取 ==========
        try:
            chunk_event_result = event_extractor.extract(chunk_text, chunk_entities)
            stage_ok["event"] += 1
            logger.info(f"[提取] 事件抽取完成: {len(chunk_event_result.events)}事件")
        except Exception as e:
            fail("事件抽取", e, chunk_start, chunk_end)
            chunk_event_result = EventExtractionResult()

        # 收集事件（去重，类型安全处理）
        chunk_event_names = []
        for ev in chunk_event_result.events:
            event_name = _to_str(ev.EventName) or ""
            event_name = event_name.strip()
            if not event_name or event_name in seen_events:
                continue
            seen_events.add(event_name)
            # 类型安全处理所有字段 + 朝代校验 + 事件名称校验
            ev.EventName = _normalize_event_name(event_name, _to_str(ev.source_text) or chunk_text)
            ev.EventType = _to_str(ev.EventType)
            ev.StartDate = _to_str(ev.StartDate)
            ev.EndDate = _to_str(ev.EndDate)
            ev.DynastyName = _normalize_dynasty(_to_str(ev.DynastyName))
            ev.Place = _to_str(ev.Place)
            ev.Aggressor = _to_str(ev.Aggressor)
            ev.Defender = _to_str(ev.Defender)
            ev.Allies = _to_str(ev.Allies)
            ev.Commanders = _to_str(ev.Commanders)
            ev.KeyPersons = _to_str(ev.KeyPersons)
            ev.Result = _to_str(ev.Result)
            ev.TroopSize = _to_str(ev.TroopSize)
            ev.Duration = _to_str(ev.Duration)
            ev.GeographicScope = _to_str(ev.GeographicScope)
            ev.Casualties = _to_str(ev.Casualties)
            ev.Impact = _to_str(ev.Impact)
            ev.source = _to_str(ev.source)
            ev.source_text = _to_str(ev.source_text)
            all_events.append(ev)
            chunk_event_names.append(event_name)

        if not chunk_event_names:
            logger.info(f"[提取] 该段未识别到事件，跳过关系抽取")
            continue

        # ========== 第3阶段：关系抽取 ==========
        try:
            chunk_relations = relation_extractor.extract(
                chunk_text,
                chunk_event_result.events,
                place_list="、".join(chunk_place_names),
                org_list="、".join(chunk_org_names),
                person_list="、".join(chunk_person_names),
            )
            stage_ok["relation"] += 1
            logger.info(f"[提取] 关系抽取完成: {len(chunk_relations.event_place_relations)}事件-地点, "
                  f"{len(chunk_relations.event_person_relations)}事件-人物, "
                  f"{len(chunk_relations.event_organization_relations)}事件-组织, "
                  f"{len(chunk_relations.event_event_relations)}事件-事件")
        except Exception as e:
            fail("关系抽取", e, chunk_start, chunk_end)
            chunk_relations = RelationExtractionResult()

        # 收集关系（类型安全处理）
        for r in chunk_relations.event_place_relations:
            ename = _to_str(r.EventName) or ""
            pname = _to_str(r.modern_name) or ""
            if ename and pname:
                r.EventName = ename
                r.modern_name = pname
                r.relation = _to_str(r.relation) or "发生地"
                r.evidence = _to_str(r.evidence) or ""
                all_event_place_rels.append(r)

        for r in chunk_relations.event_person_relations:
            ename = _to_str(r.EventName) or ""
            pname = _to_str(r.PersonName) or ""
            if ename and pname:
                r.EventName = ename
                r.PersonName = pname
                r.relation = _to_str(r.relation) or "参与者"
                r.evidence = _to_str(r.evidence) or ""
                all_event_person_rels.append(r)

        for r in chunk_relations.event_organization_relations:
            ename = _to_str(r.EventName) or ""
            oname = _to_str(r.OrgName) or ""
            if ename and oname:
                r.EventName = ename
                r.OrgName = oname
                r.relation = _to_str(r.relation) or "参战方"
                r.evidence = _to_str(r.evidence) or ""
                all_event_org_rels.append(r)

        for r in chunk_relations.event_event_relations:
            ea = _to_str(r.EventName_A) or ""
            eb = _to_str(r.EventName_B) or ""
            if ea and eb:
                r.EventName_A = ea
                r.EventName_B = eb
                r.relation = _to_str(r.relation) or "关联"
                r.evidence = _to_str(r.evidence) or ""
                all_event_event_rels.append(r)

    entities = EntityExtractionResult(
        places=all_places,
        organizations=all_orgs,
        persons=all_persons
    )
    event_result = EventExtractionResult(events=all_events)
    relations = RelationExtractionResult(
        event_place_relations=all_event_place_rels,
        event_person_relations=all_event_person_rels,
        event_organization_relations=all_event_org_rels,
        event_event_relations=all_event_event_rels
    )

    # 一个阶段都没成功过 = 模型/服务这一侧整体不可用（不是"这段文本没内容"）。
    # 此时返回空结果会被接口包装成 code 200 "识别完成"，直接掩盖故障。
    if not any(stage_ok.values()):
        raise ExtractionUnavailable(partial_errors or ["所有分段都未识别到任何内容"])

    diagnostics = {
        "stage_ok": dict(stage_ok),
        "partial_errors": partial_errors,
    }
    return entities, event_result, relations, diagnostics


# ================== 抽取结果序列化 ==================


def _serialize_place(place):
    return {
        "geo_name": place.geo_name,
        "modern_name": place.modern_name,
        "DynastyName": place.DynastyName,
        "Province": place.Province,
        "City": place.City,
        "District_County": place.District_County,
        "Specific_location": place.Specific_location,
        "source_text": place.source_text
    }


def _serialize_org(org):
    return {
        "OrgName": org.OrgName,
        "OrgType": org.OrgType,
        "DynastyName": org.DynastyName,
        "source_text": org.source_text
    }


def _serialize_person(person):
    return {
        "PersonName": person.PersonName,
        "DynastyName": person.DynastyName,
        "OrgName": person.OrgName,
        "Role": person.Role,
        "Note": person.Note,
        "source_text": person.source_text
    }


def _serialize_event(event):
    return {
        "EventName": event.EventName,
        "EventType": event.EventType,
        "StartDate": event.StartDate,
        "EndDate": event.EndDate,
        "DynastyName": event.DynastyName,
        "Place": event.Place,
        "Aggressor": event.Aggressor,
        "Defender": event.Defender,
        "Allies": event.Allies,
        "Result": event.Result,
        "Commanders": event.Commanders,
        "KeyPersons": event.KeyPersons,
        "Action": event.Action,
        "TroopSize": event.TroopSize,
        "Duration": event.Duration,
        "GeographicScope": event.GeographicScope,
        "Casualties": event.Casualties,
        "source": event.source,
        "Impact": event.Impact,
        "Remark": event.Remark,
        "relations": [{"type": r.type, "to": r.to, "evidence": r.evidence} for r in event.relations],
        "source_text": event.source_text
    }


def _serialize_relation(rel, rel_type):
    base = {
        "relation_type": rel_type,
        "evidence": getattr(rel, "evidence", None),
    }
    if rel_type == "event_place":
        base.update({
            "EventName": rel.EventName,
            "PlaceName": getattr(rel, "PlaceName", None),
            "modern_name": getattr(rel, "modern_name", None),
            "relation": rel.relation,
        })
    elif rel_type == "event_person":
        base.update({
            "EventName": rel.EventName,
            "PersonName": rel.PersonName,
            "relation": rel.relation,
        })
    elif rel_type == "event_organization":
        base.update({
            "EventName": rel.EventName,
            "OrgName": rel.OrgName,
            "relation": rel.relation,
        })
    elif rel_type == "event_event":
        base.update({
            "EventName_A": rel.EventName_A,
            "EventName_B": rel.EventName_B,
            "relation": rel.relation,
        })
    return base


def serialize_extraction_result(entities, event_result, relations, process_time):
    """把抽取结果打包成 /api/extract/entities-events 的 data 段。"""
    return {
        "entities": {
            "places": [_serialize_place(p) for p in entities.places],
            "organizations": [_serialize_org(o) for o in entities.organizations],
            "persons": [_serialize_person(p) for p in entities.persons],
        },
        "events": [_serialize_event(e) for e in event_result.events],
        "relations": {
            "event_place": [_serialize_relation(r, "event_place") for r in relations.event_place_relations],
            "event_person": [_serialize_relation(r, "event_person") for r in relations.event_person_relations],
            "event_organization": [_serialize_relation(r, "event_organization") for r in relations.event_organization_relations],
            "event_event": [_serialize_relation(r, "event_event") for r in relations.event_event_relations],
        },
        "summary": {
            "place_count": len(entities.places),
            "organization_count": len(entities.organizations),
            "person_count": len(entities.persons),
            "event_count": len(event_result.events),
            "relation_count": (
                len(relations.event_place_relations) +
                len(relations.event_person_relations) +
                len(relations.event_organization_relations) +
                len(relations.event_event_relations)
            ),
        },
        "process_time": round(process_time, 2)
    }


# ================== 问答编排 ==================


def run_inference(rule_engine, entity_extractor, question, request_id):
    """同步问答：跑一遍规则推理并生成回答，返回 (响应体 dict, HTTP 状态码)。

    错误分档与用户可读文案原样保留（连接失败 / 超时 / 模型不可用 / 资源不足 / 格式错误）。
    """
    logger.info(f"[{request_id}] 开始处理问题...")
    start_time = time.time()

    try:
        result = rule_engine.process_question(
            question=question,
            entity_extractor=entity_extractor,
            neo4j_db=neo4j_db_handle
        )

        process_time = result.get('process_time', 0)
        entities = result.get('query_entities') or result.get('entities', [])

        logger.info(f"[{request_id}] 问题处理完成，耗时: {process_time:.2f}秒, 识别到 {len(entities)} 个实体")

        kg_data = result.get('kg_data', {'nodes': [], 'lines': []})
        node_count = len(kg_data.get('nodes', []))
        line_count = len(kg_data.get('lines', []))
        logger.info(f"[{request_id}] 生成的知识图谱数据: {node_count} 个节点, {line_count} 条关系")

        relations_text = result.get('context', '未找到相关关系数据')

        return {
            'success': True,
            'answer': result.get('answer', '抱歉，无法回答这个问题'),
            'kg_data': kg_data,
            'entities': entities,
            'query_entities': result.get('query_entities', entities),
            'process_time': process_time,
            'relations_text': relations_text
        }, 200

    except Exception as process_err:
        error_type = type(process_err).__name__
        error_msg = str(process_err)

        logger.info(f"[{request_id}] 处理问题时出错: {error_type} - {error_msg}")
        traceback.print_exc()

        user_message = "抱歉，处理您的问题时遇到了技术问题。"

        if "ConnectionRefused" in error_type or "ConnectionError" in error_type:
            user_message = "抱歉，无法连接到知识库服务器，请检查数据库连接。"
        elif "TimeoutError" in error_type:
            user_message = "抱歉，查询超时，请尝试简化您的问题或稍后再试。"
        elif "ollama" in error_msg.lower():
            user_message = "抱歉，大模型服务暂时不可用，请稍后再试。"
        elif "memory" in error_msg.lower() or "cuda" in error_msg.lower():
            user_message = "抱歉，系统资源不足，请稍后再试。"
        elif "invalid" in error_msg.lower() or "syntax" in error_msg.lower():
            user_message = "抱歉，您的问题格式可能有误，请尝试用不同方式提问。"

        return {
            'success': False,
            'error': f'处理问题时出错: {error_type}',
            'error_detail': error_msg,
            'answer': user_message,
            'kg_data': {'nodes': [], 'lines': []},
            'process_time': time.time() - start_time
        }, 500


def _sse(payload: dict) -> str:
    """SSE 帧。json.dumps 用默认分隔符，与拆分前的内联写法逐字节一致。"""
    return f"data: {json.dumps(payload)}\n\n"


def stream_inference(rule_engine, entity_extractor, question, request_id):
    """SSE 问答：逐帧产出 `data: {...}`（帧序、字段与拆分前一致）。

    调用方直接把本生成器交给 flask.Response(mimetype='text/event-stream')。
    """
    try:
        # 发送开始信号
        yield _sse({'status': 'start', 'request_id': request_id})

        # ========== 步骤1: 实体提取 ==========
        yield _sse({'status': 'extracting', 'message': '正在识别实体...'})

        start_time = time.time()

        # 检测问题类型
        dynasty_scope = rule_engine._detect_dynasty_event_scope(question)
        if dynasty_scope:
            extracted_entities = []
            query_entities = [dynasty_scope["display"]]
            event_detail_query = False
            participant_event_query = False
        else:
            extracted_entities = entity_extractor.extract_entities(question)
            query_entities = rule_engine._extract_query_entities(question, extracted_entities)
            event_detail_query = rule_engine._looks_like_event_detail_query(question, extracted_entities)
            participant_event_query = rule_engine._looks_like_participant_event_query(question)

        # 过滤实体
        if dynasty_scope:
            entities = query_entities.copy()
        else:
            entities = rule_engine._filter_relevant_entities(extracted_entities, question, neo4j_db_handle)

        extract_time = time.time() - start_time
        logger.info(f"[{request_id}] 实体提取完成，耗时: {extract_time:.2f}秒，实体: {entities}")

        # 发送实体信息
        yield _sse({'status': 'entities', 'entities': entities, 'query_entities': query_entities, 'extract_time': round(extract_time, 2)})

        if not entities:
            yield _sse({'status': 'done', 'answer': '抱歉，无法从问题中识别出实体。', 'kg_data': {'nodes': [], 'lines': []}})
            return

        # ========== 步骤2: 查询知识图谱 ==========
        yield _sse({'status': 'querying', 'message': '正在查询知识图谱...'})

        query_start = time.time()

        # 查询实体信息
        all_entity_info = []
        entity_info_map = {}

        if dynasty_scope:
            all_entity_info = rule_engine._get_dynasty_event_infos(neo4j_db_handle, dynasty_scope["dynasties"])
            logger.info(f"[{request_id}] 朝代查询: {dynasty_scope['dynasties']}, 找到 {len(all_entity_info)} 条事件")
            for info in all_entity_info:
                entity_info_map[info['id']] = info
        elif event_detail_query:
            event_names = [entity for entity in entities if any(suffix in entity for suffix in ["之战", "战役", "起义", "叛乱"])]
            all_entity_info = rule_engine._get_event_detail_infos(neo4j_db_handle, event_names or entities)
            for info in all_entity_info:
                entity_info_map[info['id']] = info
        elif participant_event_query:
            participant_names = query_entities or entities
            all_entity_info = rule_engine._get_participant_event_infos(neo4j_db_handle, participant_names)
            for info in all_entity_info:
                entity_info_map[info['id']] = info
        else:
            for entity in entities:
                query = """
                MATCH (n)
                WHERE n.name = $entity
                RETURN n
                LIMIT 1
                """
                results = neo4j_db_handle.graph.run(query, entity=entity).data()

                if results:
                    node = results[0]['n']
                    info = {
                        'id': node.identity,
                        'name': node['name'],
                        'type': list(node.labels)[0] if node.labels else '',
                        'properties': {k: v for k, v in node.items()}
                    }
                    all_entity_info.append(info)
                    entity_info_map[info['id']] = info
                else:
                    # 朝代别名回退：如"周朝"→查DynastyName为"西周"/"东周"的事件
                    dynasty_values = DYNASTY_SCOPE_MAP.get(entity)
                    if dynasty_values:
                        dynasty_query = """
                        MATCH (n:Event)
                        WHERE n.dynasty IN $dynasties
                        RETURN n
                        ORDER BY coalesce(n.start_date, ''), n.name
                        LIMIT 20
                        """
                        dynasty_results = neo4j_db_handle.graph.run(dynasty_query, dynasties=dynasty_values).data()
                        for result in dynasty_results:
                            node = result['n']
                            info = {
                                'id': node.identity,
                                'name': node['name'],
                                'type': list(node.labels)[0] if node.labels else 'Event',
                                'properties': {k: v for k, v in node.items()}
                            }
                            all_entity_info.append(info)
                            entity_info_map[info['id']] = info
                        if dynasty_results:
                            logger.info(f"[{request_id}] 朝代别名回退: '{entity}' → {dynasty_values}, 找到 {len(dynasty_results)} 条事件")
                            continue

                    # 模糊查询
                    fuzzy_query = """
                    MATCH (n)
                    WHERE n.name CONTAINS $entity OR $entity CONTAINS n.name
                    RETURN n
                    LIMIT 5
                    """
                    fuzzy_results = neo4j_db_handle.graph.run(fuzzy_query, entity=entity).data()
                    for result in fuzzy_results:
                        node = result['n']
                        info = {
                            'id': node.identity,
                            'name': node['name'],
                            'type': list(node.labels)[0] if node.labels else '',
                            'properties': {k: v for k, v in node.items()}
                        }
                        all_entity_info.append(info)
                        entity_info_map[info['id']] = info

        if not all_entity_info:
            yield _sse({'status': 'done', 'answer': f'抱歉，在知识库中找不到与{entities}相关的实体信息。', 'kg_data': {'nodes': [], 'lines': []}})
            return

        # 查询实体关系
        all_relationships = []
        for info in all_entity_info:
            relationships = rule_engine.get_entity_relationships(info['id'], neo4j_db_handle)
            all_relationships.extend(relationships)

        # 搜索实体间路径
        all_paths = []
        if len(all_entity_info) >= 2 and not (dynasty_scope or event_detail_query or participant_event_query):
            processed_entity_pairs = set()
            for i in range(len(all_entity_info)):
                for j in range(i+1, len(all_entity_info)):
                    entity1_id = all_entity_info[i]['id']
                    entity2_id = all_entity_info[j]['id']
                    if entity1_id == entity2_id:
                        continue
                    entity_pair = tuple(sorted([entity1_id, entity2_id]))
                    if entity_pair in processed_entity_pairs:
                        continue
                    processed_entity_pairs.add(entity_pair)
                    paths = rule_engine.search_paths_between_entities(entity1_id, entity2_id, neo4j_db_handle)
                    all_paths.extend(paths)

        # 应用推理规则
        inferred_relationships = rule_engine.apply_inference_rules(all_relationships)
        original_relationships = all_relationships.copy()
        all_relationships.extend(inferred_relationships)

        query_time = time.time() - query_start
        logger.info(f"[{request_id}] 图谱查询完成，耗时: {query_time:.2f}秒")

        # 发送查询结果
        yield _sse({'status': 'queried', 'entity_count': len(all_entity_info), 'relation_count': len(all_relationships), 'query_time': round(query_time, 2)})

        # ========== 步骤3: 流式生成回答 ==========
        yield _sse({'status': 'generating', 'message': '正在生成回答...'})

        generate_start = time.time()

        # 构建提示词
        answer_entities = entities
        question_type = "general"
        answer_metadata = {}
        if dynasty_scope:
            question_type = "dynasty_event_list"
            answer_metadata["dynasty_scope"] = dynasty_scope
            answer_entities = [f"{dynasty_scope['display']}（仅限 DynastyName 属于 {', '.join(dynasty_scope['dynasties'])} 的战争事件）"]
        elif event_detail_query:
            question_type = "event_detail"
        elif participant_event_query:
            question_type = "participant_event_list"

        # 构建提示词
        if question_type == "dynasty_event_list":
            user_prompt = rule_engine._build_dynasty_event_list_prompt(
                question=question,
                dynasty_scope=answer_metadata.get("dynasty_scope", {}),
                event_info=all_entity_info
            )
        elif question_type == "event_detail":
            user_prompt = rule_engine._build_event_detail_prompt(question, all_entity_info)
        elif question_type == "participant_event_list":
            user_prompt = rule_engine._build_participant_event_prompt(question, all_entity_info)
        else:
            user_prompt = rule_engine._build_inference_prompt(
                question=question,
                entities=answer_entities,
                entity_info=all_entity_info,
                relationships=all_relationships,
                paths=all_paths
            )

        # 设置system提示词
        if question_type in {"dynasty_event_list", "participant_event_list"}:
            system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件清单回答枚举类问题。
必须完整覆盖清单中的每一条事件；不得自行删减、合并、补充清单外事件；不得把枚举题回答成单个"最准确答案"。"""
        elif question_type == "event_detail":
            system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件记录回答事件详情问题。
只能使用记录中的字段作答；字段缺失时说明图谱未提供；不得补充图谱外史实。"""
        else:
            system_prompt = """你是一位专业的中国历史战争研究与事件关系分析专家，精通中国历代战争事件、参战势力、战役过程及其历史背景。
你的职责是：
1. 只回答用户提出的原始问题，不要重复或引用我给你的指令内容
2. 不要在回答中说"根据提供的信息"或"基于图谱数据"等引导语
3. 所有回答必须基于知识图谱提供的事实，不要编造不存在的关系
4. 答案应直接、简洁，不要添加不必要的解释

重要: 不要将指令或提示词本身视为用户问题的一部分。用户的原始问题已在提示词开头明确标出。"""

        # 使用ollama流式调用
        import ollama

        full_answer = ""
        try:
            stream = ollama.chat(
                model='deepseek-r1:7b',
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                stream=True,
                keep_alive="10m",
                options={"temperature": 0.1}
            )

            for chunk in stream:
                if chunk['message']['content']:
                    content = chunk['message']['content']
                    full_answer += content
                    # 发送内容块
                    yield _sse({'status': 'content', 'content': content})

        except Exception as llm_err:
            logger.warning(f"[{request_id}] LLM调用失败: {str(llm_err)}")
            yield _sse({'status': 'error', 'message': f'大模型调用失败: {str(llm_err)}'})
            return

        generate_time = time.time() - generate_start
        total_time = time.time() - start_time
        logger.info(f"[{request_id}] 回答生成完成，耗时: {generate_time:.2f}秒，总耗时: {total_time:.2f}秒")

        # ========== 步骤4: 构建可视化数据 ==========
        kg_data = rule_engine._convert_to_visual_data(all_entity_info, original_relationships, all_paths)

        # 格式化关系上下文
        context = rule_engine._format_relations_for_context(original_relationships, inferred_relationships)

        # 发送完成信号
        yield _sse({'status': 'done', 'kg_data': kg_data, 'relations_text': context, 'entities': entities, 'query_entities': query_entities, 'process_time': round(total_time, 2)})

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.info(f"[{request_id}] SSE处理出错: {error_type} - {error_msg}")
        traceback.print_exc()
        yield _sse({'status': 'error', 'message': f'处理出错: {error_msg}'})


# ================== 进程内单例 ==================

_shared_entity_extractor = None
_shared_rule_llm_integration = None
# 单例初始化锁：无锁的 check-then-set 在 Flask 多线程下会重复建实例，
# 后建的覆盖先建的、先建的那份仍在被别的线程使用。
_singleton_init_lock = threading.Lock()


def get_shared_extractors():
    """返回 (实体提取器, 规则推理引擎)；某一个建失败就是 None，不影响另一个。

    原来的写法是模块级全局变量 + before_request 里直接赋值；状态集中到这里以后，
    调用方（app.py 的钩子）只负责把它挂到 flask.g 上，不再各自维护全局。
    """
    global _shared_entity_extractor, _shared_rule_llm_integration

    if _shared_entity_extractor is None or _shared_rule_llm_integration is None:
        with _singleton_init_lock:
            # 拿到锁后重新判断：可能已被另一个线程初始化好
            if _shared_entity_extractor is None:
                try:
                    from entity_extract.extractor import Extractor
                    _shared_entity_extractor = Extractor()
                    # 加载已知实体到提取器，用于规则匹配快速提取
                    try:
                        _shared_entity_extractor.load_known_entities(neo4j_db_handle)
                        logger.info("实体提取器已初始化，并加载了已知实体")
                    except Exception as load_err:
                        logger.warning("加载已知实体失败，将使用纯模型提取: %s", load_err)
                except Exception as e:
                    logger.warning("初始化实体提取器失败: %s", e)

            if _shared_rule_llm_integration is None:
                try:
                    from inference.rule_llm_integration import RuleLLMIntegration
                    _shared_rule_llm_integration = RuleLLMIntegration(
                        rule_file_path='rules/rule_base.json',
                        model_name='deepseek-r1:7b',
                        max_depth=30
                    )
                    logger.info("规则推理模块已初始化")
                except Exception as e:
                    logger.warning("初始化规则推理模块失败: %s", e)

    return _shared_entity_extractor, _shared_rule_llm_integration


# ================== jieba 用户词典 ==================


def init_user_dict():
    """从data.json提取地名实体并创建历史地名词典文件"""
    dict_path = os.path.join(BACKEND_DIR, 'historical_places.txt')
    data_json_path = os.path.join(BACKEND_DIR, 'data', 'data.json')

    try:
        if not os.path.exists(data_json_path):
            logger.info(f"警告：data.json文件不存在: {data_json_path}")
            return

        with open(data_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        logger.info(f"从data.json中提取实体，共有{len(data)}条记录")

        entities = set()
        entity_types = set()
        relation_types = set()

        for item in data:
            if "实体名称" in item and item["实体名称"]:
                entities.add(item["实体名称"])

            if "关联实体" in item and item["关联实体"]:
                associated_entities = item["关联实体"].split("、")
                for entity in associated_entities:
                    entities.add(entity)

            if "实体类型" in item and item["实体类型"]:
                entity_types.add(item["实体类型"])

            if "关联实体类型" in item and item["关联实体类型"]:
                entity_types.add(item["关联实体类型"])

            if "实体关系" in item and item["实体关系"]:
                relation_types.add(item["实体关系"])

        dynasties = [
            "秦朝", "汉朝", "西汉", "东汉", "三国", "魏国", "蜀国", "吴国",
            "晋朝", "西晋", "东晋", "南北朝", "隋朝", "唐朝", "五代十国",
            "宋朝", "北宋", "南宋", "辽朝", "金朝", "元朝", "明朝", "清朝", "民国"
        ]

        for dynasty in dynasties:
            entities.add(dynasty)

        dict_content = []

        for entity in entities:
            dict_content.append(f"{entity} 10 ns")

        for entity_type in entity_types:
            dict_content.append(f"{entity_type} 10 n")

        for relation in relation_types:
            dict_content.append(f"{relation} 10 v")

        with open(dict_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(dict_content))

        logger.info(f"已成功生成历史地名词典文件: {dict_path}")
        logger.info(f"词典包含 {len(entities)} 个实体名称, {len(entity_types)} 个实体类型, {len(relation_types)} 个关系类型")

    except Exception as e:
        logger.warning(f"创建历史地名词典文件失败: {str(e)}")
        traceback.print_exc()
