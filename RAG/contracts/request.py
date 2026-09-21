"""查询请求 / F02 内部输出 / 实体纠正（docs/data-contract.md）。

- QueryRequest：F01 前端发来的请求体。
- F02Output：F02 在后端生成、仅内部链路传递（rewritten_question / question_type /
  entities / candidates）。
- CorrectedEntity / CorrectionAction：用户手动纠正实体。
- EntityCandidate：候选实体（供前端实体卡与纠正）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from contracts.base import BaseModel
from contracts.question import QuestionType


class RequestValidationError(ValueError):
    """请求参数不合法（类型/长度/数量超限）。

    API 层把它映射成 4xx（400/413），在调用外部模型之前就拒绝，
    避免"超大输入 → 高内存 + 高 embedding/LLM 成本"（2026-09-15 审核 P0-2）。
    """


@dataclass
class _Limits(BaseModel):
    """请求尺寸边界；默认值取自 config.defaults，可被 Settings 覆盖。"""

    question_max_chars: int = 500
    session_id_max_chars: int = 128
    history_content_max_chars: int = 4000
    history_max_items: int = 40
    corrections_max_items: int = 20
    filters_max_items: int = 20
    filter_value_max_chars: int = 64

    @staticmethod
    def from_settings(settings: Any = None) -> "_Limits":
        if settings is None:
            from config import defaults as _d

            return _Limits(
                question_max_chars=_d.QUESTION_MAX_CHARS,
                session_id_max_chars=_d.SESSION_ID_MAX_CHARS,
                history_content_max_chars=_d.HISTORY_CONTENT_MAX_CHARS,
                history_max_items=_d.HISTORY_MAX_ITEMS,
                corrections_max_items=_d.CORRECTIONS_MAX_ITEMS,
                filters_max_items=_d.FILTERS_MAX_ITEMS,
                filter_value_max_chars=_d.FILTER_VALUE_MAX_CHARS,
            )
        return _Limits(
            question_max_chars=int(getattr(settings, "question_max_chars",
                                           _DEFAULTS.question_max_chars)),
            session_id_max_chars=int(getattr(settings, "session_id_max_chars",
                                             _DEFAULTS.session_id_max_chars)),
            history_content_max_chars=int(getattr(settings, "history_content_max_chars",
                                                  _DEFAULTS.history_content_max_chars)),
            history_max_items=int(getattr(settings, "history_max_items",
                                          _DEFAULTS.history_max_items)),
            corrections_max_items=int(getattr(settings, "corrections_max_items",
                                              _DEFAULTS.corrections_max_items)),
            filters_max_items=int(getattr(settings, "filters_max_items",
                                          _DEFAULTS.filters_max_items)),
            filter_value_max_chars=int(getattr(settings, "filter_value_max_chars",
                                               _DEFAULTS.filter_value_max_chars)),
        )


_DEFAULTS = _Limits()


def _as_str(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequestValidationError(f"{field_name} 必须是字符串，实际 {type(value).__name__}")
    return value


def _check_len(field_name: str, value: str, limit: int) -> str:
    if len(value) > limit:
        raise RequestValidationError(f"{field_name} 长度 {len(value)} 超过上限 {limit}")
    return value


def _check_list(field_name: str, value: Any, limit: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestValidationError(f"{field_name} 必须是数组，实际 {type(value).__name__}")
    if len(value) > limit:
        raise RequestValidationError(f"{field_name} 条数 {len(value)} 超过上限 {limit}")
    return value


# correction 条目允许出现的字段（未列出的字段会被拒绝，见 P1-2）
_CORRECTION_FIELDS = frozenset({
    "action", "source_entity_id", "replacement_entity_id", "entity_id",
    "entity_type", "name", "original", "replacement",
})


def resource_id_alias(action: "CorrectionAction", entity_id: Optional[str],
                      replacement_entity_id: Optional[str]) -> Optional[str]:
    """兼容别名的落库口径：`entity_id` 仅在 add 上等价于 `replacement_entity_id`。

    replace/remove 不允许含混的 entity_id（构造前已拒绝），因此这里只在 add 上回填，
    目的是让"读 entity_id 的旧调用方"仍能拿到目标实体 ID。
    """
    if action is CorrectionAction.ADD:
        return replacement_entity_id or entity_id
    return None


def _clean_str_list(field_name: str, value: Any, limits: "_Limits") -> list:
    """筛选值数组：元素必须是字符串、去空白、限个数与单值长度（并去重保序）。"""
    out: list[str] = []
    for i, item in enumerate(_check_list(field_name, value, limits.filters_max_items)):
        text = _as_str(item, f"{field_name}[{i}]").strip()
        if not text:
            continue
        _check_len(f"{field_name}[{i}]", text, limits.filter_value_max_chars)
        if text not in out:
            out.append(text)
    return out


class CorrectionAction(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass
class HistoryTurn(BaseModel):
    role: str  # user | assistant
    content: str


@dataclass
class CorrectedEntity(BaseModel):
    """用户对识别实体的一次纠正指令（2026-09-16 工作单 P1-2 / P1-3 重构）。

    为什么要拆成"源 ID / 目标 ID"两个字段：一个 `entity_id` 同时表示
    "被替换的实体"和"替换成的新实体"，同名不同朝代时后端无法判断用户选中的是哪一个，
    最终只能按名字取同名列表第一项——用户选了"西汉"却被改到"战国"。

    字段与动作的对应（契约层强制，不符即 400）：

    | 动作 | 定位源实体 | 定位目标实体 | 其他 |
    | --- | --- | --- | --- |
    | add | 不适用 | `replacement_entity_id` 或兼容别名 `entity_id`（可选） | `name` + `entity_type` 必填 |
    | replace | `source_entity_id` 或 `original`（必填其一） | `replacement_entity_id` 或 `replacement`（必填其一） | `entity_type` 可选 |
    | remove | `source_entity_id` 或 `original`（必填其一） | 不适用 | `entity_type` 可选 |

    兼容策略（显式声明，非"静默接受"）：`original` / `replacement` 是无 ID 时的名称降级路径；
    `entity_id` 仅作为 add 的目标 ID 别名存在，replace/remove 传它会直接 400。
    未列出的字段一律拒绝（返回 400 并列出字段名），不允许"看起来传了但没人读"。
    """

    action: CorrectionAction
    source_entity_id: Optional[str] = None       # replace/remove：被替换/移除的实体
    replacement_entity_id: Optional[str] = None  # add/replace：用户选中的新实体
    entity_id: Optional[str] = None              # 兼容别名：add 时等价于 replacement_entity_id
    entity_type: Optional[str] = None            # add 必填；replace/remove 可选
    name: Optional[str] = None                   # add 必填（标准名）
    original: Optional[str] = None               # replace/remove 的名称降级路径
    replacement: Optional[str] = None            # add/replace 的名称降级路径


@dataclass
class EntityRef(BaseModel):
    """F02 entities 列表中的一项 / 前端识别结果展示。"""

    name: str
    type: Optional[str] = None            # 事件/人物/组织/地点/朝代
    standard_name: Optional[str] = None   # 归一后标准名
    confidence: Optional[str] = None      # high/medium/low
    entity_id: Optional[str] = None       # 快照中标准实体 id（F09 输出后填充）
    dynasty: Optional[str] = None


@dataclass
class CandidateOption(BaseModel):
    name: str
    standard_name: str
    confidence: Optional[str] = None
    dynasty: Optional[str] = None
    event_type: Optional[str] = None
    entity_id: Optional[str] = None


@dataclass
class EntityCandidate(BaseModel):
    mention: str
    entity_type: Optional[str] = None
    options: List[CandidateOption] = field(default_factory=list)


@dataclass
class Filters(BaseModel):
    dynasty: List[str] = field(default_factory=list)      # 空 = 不过滤
    event_type: List[str] = field(default_factory=list)


@dataclass
class QueryRequest(BaseModel):
    session_id: str
    question: str
    history: List[HistoryTurn] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    corrected_entities: List[CorrectedEntity] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict, settings: Any = None) -> "QueryRequest":
        """从请求 dict 构造（嵌套 dict → dataclass 转换，避免直接 ** 展开时
        filters/corrected_entities/history 仍是 dict）。

        同时做**边界与类型校验**（2026-09-15 审核 P0-2）：字段超长/超量、类型不符、
        未知枚举一律抛 RequestValidationError，由 API 层在调用外部模型前返回 4xx。
        历史实现直接 `HistoryTurn(**h)`，多余字段会抛 TypeError，形状错误的
        filters 会把 dict 键当成参数名——报错信息对调用方毫无指导意义。
        """
        if not isinstance(d, dict):
            raise RequestValidationError("请求体必须是 JSON 对象")
        limits = _Limits.from_settings(settings)

        session_id = _check_len(
            "session_id",
            _as_str(d.get("session_id"), "session_id"),
            limits.session_id_max_chars,
        )
        question = _check_len(
            "question",
            _as_str(d.get("question"), "question"),
            limits.question_max_chars,
        )
        if not session_id:
            raise RequestValidationError("session_id 必填")
        if not question.strip():
            raise RequestValidationError("question 必填")

        history: List[HistoryTurn] = []
        for i, item in enumerate(_check_list("history", d.get("history"),
                                             limits.history_max_items)):
            if isinstance(item, HistoryTurn):
                turn = item
            elif isinstance(item, dict):
                role = _as_str(item.get("role"), f"history[{i}].role")
                if role not in ("user", "assistant"):
                    raise RequestValidationError(
                        f"history[{i}].role 必须是 user/assistant，实际 {role!r}")
                turn = HistoryTurn(
                    role=role,
                    content=_check_len(
                        f"history[{i}].content",
                        _as_str(item.get("content"), f"history[{i}].content"),
                        limits.history_content_max_chars,
                    ),
                )
            else:
                raise RequestValidationError(f"history[{i}] 必须是对象")
            history.append(turn)

        raw_filters = d.get("filters") or {}
        if not isinstance(raw_filters, dict):
            raise RequestValidationError("filters 必须是对象")
        filters = Filters(
            dynasty=_clean_str_list("filters.dynasty", raw_filters.get("dynasty"), limits),
            event_type=_clean_str_list("filters.event_type",
                                       raw_filters.get("event_type"), limits),
        )

        corrections: List[CorrectedEntity] = []
        for i, item in enumerate(_check_list("corrected_entities",
                                             d.get("corrected_entities"),
                                             limits.corrections_max_items)):
            if not isinstance(item, dict):
                raise RequestValidationError(f"corrected_entities[{i}] 必须是对象")
            action_raw = item.get("action")
            # 先确认是字符串：数组/对象会让 CorrectionAction(...) 抛 TypeError，
            # 那是个未转换的 500，而不是可解释的 400（第四轮复核 P1-2）。
            if not isinstance(action_raw, str):
                raise RequestValidationError(
                    f"corrected_entities[{i}].action 必须是字符串 add/replace/remove，"
                    f"实际 {type(action_raw).__name__}")
            try:
                action = CorrectionAction(action_raw)
            except ValueError as e:
                raise RequestValidationError(
                    f"corrected_entities[{i}].action 必须是 add/replace/remove，"
                    f"实际 {action_raw!r}") from e

            def _opt(key: str) -> Optional[str]:
                text = _as_str(item.get(key), f"corrected_entities[{i}].{key}").strip()
                if text:
                    _check_len(f"corrected_entities[{i}].{key}", text,
                               limits.filter_value_max_chars)
                return text or None

            # 未知字段直接拒绝（P1-2 第 4 条）：显式列出而不是默默忽略
            unknown = sorted(set(item) - _CORRECTION_FIELDS)
            if unknown:
                raise RequestValidationError(
                    f"corrected_entities[{i}] 含未声明字段：{', '.join(unknown)}；"
                    f"允许的字段为 {', '.join(sorted(_CORRECTION_FIELDS))}")

            source_entity_id = _opt("source_entity_id")
            replacement_entity_id = _opt("replacement_entity_id")
            entity_id = _opt("entity_id")
            entity_type = _opt("entity_type")
            name = _opt("name")
            original = _opt("original")
            replacement = _opt("replacement")

            # 动作语义校验（P1-2 / P1-3）：缺定位字段一律 400，不允许静默 no-op，
            # 也不允许用另一个动作的字段蒙混过关（add 不再接受 replacement 顶替 name）。
            if action is CorrectionAction.ADD:
                if not name:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 add 时必须提供 name（标准名）")
                if not entity_type:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 add 时必须提供 entity_type")
                if original or source_entity_id:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 add 时不应出现 original/source_entity_id"
                        f"（新增实体没有“源实体”）")
                if replacement and replacement != name:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 add 的标准名请用 name，"
                        f"replacement 只属于 replace 动作")
                # 兼容别名只在 add 上有定义
                if entity_id and not replacement_entity_id:
                    replacement_entity_id = entity_id
                entity_id = replacement_entity_id
                replacement = None
                original = None
            elif action is CorrectionAction.REPLACE:
                if not (source_entity_id or original):
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 replace 时必须提供 source_entity_id"
                        f"（或 original 名称降级）来定位被替换实体")
                if not (replacement_entity_id or replacement):
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 replace 时必须提供 replacement_entity_id"
                        f"（或 replacement 名称降级）来定位替换目标")
                if entity_id:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 replace 时请用 source_entity_id / "
                        f"replacement_entity_id 区分源与目标，不再接受含混的 entity_id")
                if name:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 replace 时不接受 name，"
                        f"请用 replacement（替换目标的标准名）")
                name = None
            else:  # REMOVE
                if not (source_entity_id or original):
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 remove 时必须提供 source_entity_id"
                        f"（或 original 名称降级）来定位要移除的实体")
                if entity_id:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 remove 时请用 source_entity_id，"
                        f"不再接受含混的 entity_id")
                if name or replacement or replacement_entity_id:
                    raise RequestValidationError(
                        f"corrected_entities[{i}] 为 remove 时不接受 name/replacement/"
                        f"replacement_entity_id（移除动作没有“目标实体”）")
                name = None
                replacement = None
                replacement_entity_id = None

            corrections.append(CorrectedEntity(
                action=action,
                source_entity_id=source_entity_id,
                replacement_entity_id=replacement_entity_id,
                entity_id=resource_id_alias(action, entity_id, replacement_entity_id),
                entity_type=entity_type,
                name=name,
                original=original,
                replacement=replacement,
            ))

        return QueryRequest(
            session_id=session_id,
            question=question,
            history=history,
            filters=filters,
            corrected_entities=corrections,
        )


@dataclass
class F02Output(BaseModel):
    rewritten_question: str = ""
    question_type: QuestionType = None  # type: ignore[assignment]  # 内部链路一定填充
    entities: List[EntityRef] = field(default_factory=list)
    candidates: List[EntityCandidate] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    # 问句中自动识别到的朝代（仅用于排序加权，**不作为硬过滤**）。
    # 显式筛选（F01 下拉）走 filters.dynasty，保持硬过滤；两者分开是 2026-09-13
    # 审核后修复：硬过滤会把"被问到的朝代"连同事件本身一起剔除（如问"商朝"时
    # 鸣条之战属夏，被整题清空而拒答）。详见 docs/CHANGELOG.md（RAGv4 系统问题 1）。
    dynasty_bias: List[str] = field(default_factory=list)
    # 是否走了 F02 的 LLM 兜底（词典完全未命中 → 模型抽实体，RAGv5 §4.5）。
    # 仅作可观测性：默认关闭；开启后进入 entities 事件与评测 trace，便于核对是否误触发。
    llm_entity_used: bool = False
    # 同名多实体时，是否由"问句里提到的朝代"选定（偏好而非硬过滤，RAGv5 2026-09-14）。
    # 例：问"西汉的井陉之战"→ 候选含战国/西汉两条，命中西汉那条并前置。
    # 仅作可观测性：进入 entities 事件与评测 trace；候选集合不变，页面仍可纠正。
    dynasty_disambiguated: bool = False

    def to_dict(self, skip_none: bool = True) -> dict:
        d = super().to_dict(skip_none)
        if isinstance(d.get("question_type"), QuestionType):
            d["question_type"] = d["question_type"].value
        return d
