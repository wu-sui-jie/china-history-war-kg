"""F09 战争类型 / 朝代归一与词典生成。

原则（对应 F09 需求与 README 设计决策）：
- 战争类型：旧数据 27 类大部分语义互不相同（镇压叛乱 vs 农民起义 vs 边境防御…），
  “无依据的合并”反而会破坏筛选。因此本层策略是：
  1. 只做“配置化的低风险同义归一”（EVENT_TYPE_SYNONYM_MAP，默认仅文档性映射，可随 F10 调整）；
  2. 保留全部不同类型；
  3. 把语义存疑/泛化的类型（战争/议和/政治事件等非战争类）列入 REVIEW_NEEDED_TYPES，
     写进治理报告“待人工确认”，不静默改动。
- 朝代：对“上古传说战争被误标为夏”这类明显数据问题做有依据归一（_is_ancient_legend），
  归入“上古”并在报告记录 data_issue；其余朝代原样保留。人物/组织/地点的 dynasty
  信息更弱，不做推断，保持原样。

**朝代归一表的位置（决策 10.1 第 7 项，本轮只落"位置"这一步）**

"不强行统一取值、改为统一映射"的决策里，**权威映射表已定在**
`war_extraction/utils/vocabulary.py` 的 `DYNASTY_ALIASES`（连同 `normalize_dynasty()`），
backend 已改为引用它（见 `backend/dynasty_data.py` 的 `DYNASTY_CORRECTIONS`）。

RAG 侧**本轮刻意不改动**，原因是接入方式还没定：本模块的运行环境里**没有**
`war_extraction` 依赖（`requirements.txt` 里没有，全仓 RAG 代码对它的引用为零），
直接 `import` 会把抽取链拖进 RAG 的部署依赖；而"把表复制一份"正是要消除的那种重复。
两条候选接入路径，需在**重建快照时**一并决定，不要单独改这里：

1. 治理阶段把 `DYNASTY_ALIASES` 生成为快照内的一个数据文件（沿用 F09 现有做法：
   表随快照发布，运行时不跨模块 import）；
2. 给 RAG 加 `war_extraction` 依赖（要评估它的安装体积与版本约束）。

在此之前，RAG 的朝代取值仍是产物原文写法，与 backend 的归一结果**不一致**——
排查"某朝代查不到数据"时要记得这一点（`describe_dialect_gaps` 只覆盖 backend 内部各表）。
"""

from __future__ import annotations

# 低风险同义归一映射：旧写法 → 标准写法。
# 本层不做强合并，此处仅作为“可配置归一表”存在，供后续根据 F10 评测调整。
# 例如将来若需把 "军阀混战" 并入 "军阀割据"，在此加一行即可并重跑治理。
EVENT_TYPE_SYNONYM_MAP: dict[str, str] = {}

# 语义存疑 / 泛化 / 疑似非战争的类型：不自动改，列报告待人工确认
REVIEW_NEEDED_EVENT_TYPES = ("战争", "议和", "政治事件")

#: 标准事件类型种子：`dicts.json` 的 `event_type_standard` 由它生成（`governance._build_dicts`）。
#
# **为什么要有这份种子。** 原先词典是 `sorted(event_type_counts)`——等于"本版数据里出现过
# 的取值"，于是**随数据漂**：数据里的动作词（`交战`，模型把 `Action` 抄进了 `EventType`）
# 会被收进来，而权威表里有、本版数据没出现的类型（`政治事件`/`议和`）反而从词典里消失。
# 词典的职责是"标准类型表"（筛选下拉与索引分词都读它），不该由数据分布决定。
#
# **与抽取侧权威表的关系。** 内容 = `entity-event-relation/war_extraction/utils/vocabulary.py`
# 的 `EVENT_TYPES`，**逐项相同**。本模块运行时不 import 它（RAG 没有 war_extraction 依赖，
# 见本文件顶部说明），同步靠守卫钉住：
# `entity-event-relation/tests/test_enum_synchronization.py::test_RAG事件类型种子与权威表一致`。
# 这就是 F09 既有的"表随快照发布、运行时不跨模块 import"的做法。
EVENT_TYPE_SEED = {
    # 提示词枚举（抽取侧可标出的全集）
    "农民起义", "贵族叛乱", "军阀割据", "少数民族反叛",
    "边境防御", "首都保卫战", "战略要地守卫",
    "统一战争", "开疆拓土", "对外远征", "镇压叛乱",
    "皇位争夺", "藩镇混战", "党争军事化",
    "诸侯联盟", "军事同盟", "联军讨伐",
    "宫廷政变", "军事改革", "兵变", "割据政权建立",
    "突围战",
    # 兜底值（提示词原话：不确定则填"战争"）
    "战争",
    # 实际产出、已收敛进权威表的类型
    "叛乱", "军阀混战", "诸侯争霸", "伏击战",
    "战略进攻", "政治事件", "议和", "追击战",
}

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
