"""F09 战争类型 / 朝代归一与词典生成。

原则（对应 F09 需求与 README 设计决策）：
- 战争类型：旧数据 27 类大部分语义互不相同（镇压叛乱 vs 农民起义 vs 边境防御…），
  “无依据的合并”反而会破坏筛选。因此初版策略是：
  1. 只做“配置化的低风险同义归一”（EVENT_TYPE_SYNONYM_MAP，默认仅文档性映射，可随 F10 调整）；
  2. 保留全部不同类型；
  3. 把语义存疑/泛化的类型（战争/议和/政治事件等非战争类）列入 REVIEW_NEEDED_TYPES，
     写进治理报告“待人工确认”，不静默改动。
- 朝代：对“上古传说战争被误标为夏”这类明显数据问题做有依据归一（_is_ancient_legend），
  归入“上古”并在报告记录 data_issue；其余朝代原样保留。人物/组织/地点的 dynasty
  信息更弱，不做推断，保持原样。
"""

from __future__ import annotations

# 低风险同义归一映射：旧写法 → 标准写法。
# 初版不做强合并，此处仅作为“可配置归一表”存在，供后续根据 F10 评测调整。
# 例如将来若需把 "军阀混战" 并入 "军阀割据"，在此加一行即可并重跑治理。
EVENT_TYPE_SYNONYM_MAP: dict[str, str] = {}

# 语义存疑 / 泛化 / 疑似非战争的类型：不自动改，列报告待人工确认
REVIEW_NEEDED_EVENT_TYPES = ("战争", "议和", "政治事件")

# 明显属于上古传说时代的关键词（朝代被误标为“夏”时的判断依据）
_ANCIENT_HINTS = (
    "神农", "黄帝", "炎帝", "蚩尤", "涿鹿", "阪泉", "尧", "舜", "禹",
    "丹水", "斧隧", "三苗", "部落", "约四五千年前", "上古",
)


def normalize_event_type(raw: str | None) -> str | None:
    """战争类型归一：命中配置映射则替换，否则原样返回（含空值）。"""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    return EVENT_TYPE_SYNONYM_MAP.get(raw, raw)


def _is_ancient_legend(ev: dict) -> bool:
    """判断事件是否属于上古传说（朝代被误标为“夏”的主要依据）。"""
    name = ev.get("name") or ""
    start = ev.get("start_date") or ""
    return any(hint in name or hint in start for hint in _ANCIENT_HINTS)


def normalize_dynasty(ev: dict) -> tuple[str | None, dict]:
    """事件朝代归一。

    返回 (标准朝代, 备注dict)。当事件被误标为"夏"但命中上古传说特征时，
    归为"上古"并返回 data_issue 记录（含处理前/后完整字段快照，满足人工审核可追溯）；
    否则原样返回。
    """
    raw = (ev.get("dynasty") or "").strip() or None
    if raw is None:
        return None, {}
    if raw == "夏" and _is_ancient_legend(ev):
        issue = {
            "type": "dynasty_correction",
            "from": raw,
            "to": "上古",
            "reason": "上古传说战争被误标为夏(按名称/时间特征判定)",
            "source_row_id": ev.get("source_row_id"),
            "name": ev.get("name"),
            # 人工审核要求第 5 条：处理前内容 / 处理后内容 / 审核结论
            "before": {
                "dynasty": raw,
                "start_date": ev.get("start_date"),
                "event_type": ev.get("event_type"),
            },
            "after": {"dynasty": "上古"},
            "audit_status": "auto_applied_pending_review",
        }
        return "上古", issue
    return raw, {}
