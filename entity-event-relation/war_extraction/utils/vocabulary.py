"""
枚举取值的**权威表**：OrgType / Role / EventType / 四类关系名。

**为什么要有这个模块。** 同一批取值原先散落在至少五处，而且互不相同：抽取提示词的枚举、
`Normalizer.FRONTEND_RELATION_TYPES`、评估器的归一表、RAG 的 `field_map`、前端图谱下拉。
后果是"抽出来了但下游点不到"或"改一处不改另一处"，而这些不一致**都不会报错**：

- 前端组织关系下拉只有 8 项、缺 `参战方`，而产物里 207 行是 `参战方`；
- RAG 的地点关系集合有 20 项、提示词只有 10 项；
- `import_json_to_sqlite` 的 OrgType 白名单只有 6 项，产物里的 `军事势力`（181 行）、
  `军队`（117 行）、`革命组织`（5 行）、`方国`（2 行）被静默改写成 `地方势力`。

**本表的取值口径**（两部分取并集，不发明新语义）：

1. 提示词枚举里的值——模型被告知可以产出的；
2. 产物里实际出现、且下游（RAG `field_map` / 前端）已经能处理的值。

所以本表是"**已在使用的关系名与实体类型**的收敛"，不是一次重新设计。任何**新**取值都必须
先加进这里，再走一遍同步清单（提示词枚举 → 本表 → 下游镜像 → 文档），否则会被
`schema 校验层`判为枚举外、不进发布子集——这是防漂移的闸门，不是装饰。

**不包含 `DynastyName`。** 按已定决策（整改方案 10.1 第 7 项），朝代**不强行统一取值**：
产物保留原文写法，归一交给映射表。所以朝代只由 `DYNASTY_REFERENCE` 提供"参考分布"，
不参与枚举校验。
"""
from __future__ import annotations

from typing import FrozenSet, Optional, Tuple

__all__ = [
    "EVENT_EVENT_RELATION_TYPES",
    "EVENT_PLACE_RELATION_TYPES",
    "EVENT_ORGANIZATION_RELATION_TYPES",
    "EVENT_PERSON_RELATION_TYPES",
    "ALL_RELATION_TYPES",
    "RELATION_TYPES_BY_CATEGORY",
    "ORG_TYPES",
    "ROLES",
    "EVENT_TYPES",
    "DYNASTY_REFERENCE",
    "DYNASTY_ALIASES",
    "normalize_role",
    "normalize_org_type",
    "normalize_event_type",
    "normalize_dynasty",
    "relation_type_allowed",
]

# --------------------------------------------------------------------- 事件-事件关系（5）

EVENT_EVENT_RELATION_TYPES: FrozenSet[str] = frozenset({
    "因果关系", "顺承关系", "并列关系", "包含关系", "条件关系",
})

# --------------------------------------------------------------------- 事件-地点关系（20）
#
# 前 10 项是提示词枚举；后 10 项是产物里实际出现、RAG `field_map._PLACE_RELS` 已经在处理的值。
# 它们同属"地点在本场战争中的角色"这一类，语义不分叉，所以收敛进允许集合，
# 而不是把它们判成枚举外丢掉（判成枚举外只会让数据从发布子集里消失，问题依旧存在）。

EVENT_PLACE_RELATION_TYPES: FrozenSet[str] = frozenset({
    # 提示词枚举
    "主战场", "次要战场", "出发地", "目的地", "途经地",
    "驻防地", "指挥所", "补给地", "战略要地", "议和地点",
    # 实际使用、下游已支持
    "退守地", "登陆地", "会师地", "撤退地", "出边地",
    "集结地", "会师地点", "逃亡地", "伏击地", "终点",
})

# --------------------------------------------------------------------- 事件-组织关系（9）

EVENT_ORGANIZATION_RELATION_TYPES: FrozenSet[str] = frozenset({
    "发起方", "防守方", "支援方", "同盟方", "投降方",
    "被俘方", "议和方", "调停方", "参战方",
})

# --------------------------------------------------------------------- 事件-人物关系（19）
#
# 与地点同一口径：提示词枚举 11 项 ∪ 产物实际使用且 RAG 已支持的 8 项。
# 注意 `参与方` 与提示词的 `参与者` 不是一回事：实体侧 Role 用 `参战者`、
# 关系侧用 `参与者`，两者都保留（历史口径，改名会打断下游）。

EVENT_PERSON_RELATION_TYPES: FrozenSet[str] = frozenset({
    # 提示词枚举
    "统帅", "将领", "谋士", "君主", "使者", "参与者",
    "俘虏", "阵亡", "投降", "叛变", "可汗",
    # 实际使用、下游已支持
    "被俘", "防守方统帅", "向导", "监督", "发起方", "防守方", "同盟方", "支援方",
})

RELATION_TYPES_BY_CATEGORY = {
    "event-event": EVENT_EVENT_RELATION_TYPES,
    "event-place": EVENT_PLACE_RELATION_TYPES,
    "event-organization": EVENT_ORGANIZATION_RELATION_TYPES,
    "event-person": EVENT_PERSON_RELATION_TYPES,
}

ALL_RELATION_TYPES: FrozenSet[str] = frozenset(
    value for group in RELATION_TYPES_BY_CATEGORY.values() for value in group
)

# --------------------------------------------------------------------- 组织类型（10）
#
# 6 项来自提示词枚举，后 4 项是产物里实际产出的（`军事势力`181 / `军队`117 /
# `革命组织`5 / `方国`2），原先被 `import_json_to_sqlite` 静默改写成 `地方势力`。

ORG_TYPES: FrozenSet[str] = frozenset({
    "国家", "部落", "起义军", "联盟", "地方势力", "中央政权",
    "军事势力", "军队", "革命组织", "方国",
})

# --------------------------------------------------------------------- 人物角色（10）
#
# 提示词枚举 6 项 ∪ 产物里实际出现且按第 5 项决策扩散进来的 4 项
# （`关键人物`7 / `监军`1 / `首领`1 / `领袖`1）。提示词枚举必须同步扩到同样的集合，
# 否则模型不会稳定产出这些值——枚举只加在下游校验里等于没加。

ROLES: FrozenSet[str] = frozenset({
    "统帅", "将领", "谋士", "君主", "使者", "参战者",
    "关键人物", "监军", "首领", "领袖",
})

# --------------------------------------------------------------------- 事件类型（29）
#
# 提示词枚举 21 项 ∪ 实际产出里提示词没列到的 8 项（`叛乱`/`军阀混战`/`诸侯争霸`/
# `伏击战`/`战略进攻`/`政治事件`/`议和`/`追击战`）。
# `战争` 是提示词指定的兜底值（"不确定则填战争"），必须在集合里。
# 注意：RAG 侧治理后的标准词典是 27 项（`RAG/data/snapshot/*/dicts.json`），
# 它等于"产物里实际出现的非空取值"，本表是 27 ∪ 提示词独有的 2 项
# （`党争军事化`/`军事同盟`）——即"模型可能产出 + 下游已收录"。

EVENT_TYPES: FrozenSet[str] = frozenset({
    # 提示词枚举
    "农民起义", "贵族叛乱", "军阀割据", "少数民族反叛",
    "边境防御", "首都保卫战", "战略要地守卫",
    "统一战争", "开疆拓土", "对外远征", "镇压叛乱",
    "皇位争夺", "藩镇混战", "党争军事化",
    "诸侯联盟", "军事同盟", "联军讨伐",
    "宫廷政变", "军事改革", "兵变", "割据政权建立",
    # 兜底值（提示词原话：不确定则填"战争"）
    "战争",
    # 实际产出、RAG 标准词典已收录
    "伏击战", "军阀混战", "叛乱", "战略进攻", "政治事件", "议和", "诸侯争霸", "追击战",
})

# --------------------------------------------------------------------- 朝代（仅参考 + 归一映射）
#
# 按第 7 项决策：**不强行统一取值**，产物保留原文写法（`清朝`/`蒙古`/`元末明初`/`不详`…），
# 归一交给下面这张权威映射表。`DYNASTY_REFERENCE` 只是"提示词枚举 + 后端白名单"的并集，
# 用途只有一个：让体检脚本能报出"产物里有多少种写法、其中多少种不在枚举内"，
# **不参与数据校验**（拿它拦数据会把真实写法判成枚举外、挪出发布子集）。

DYNASTY_REFERENCE: FrozenSet[str] = frozenset({
    "夏", "商", "西周", "春秋", "战国", "秦", "西汉", "东汉",
    "三国", "魏", "蜀", "吴", "西晋", "东晋", "南北朝",
    "隋", "唐", "五代十国", "北宋", "南宋", "辽", "西夏", "金", "元", "明", "清",
    "上古", "原始社会", "父系氏族社会",
})

#: **朝代归一的唯一权威表**（写法 → 规范写法）。
#:
#: 决策第 7 项：产物保留原文写法，归一交给"一张 backend 与 RAG 共用的映射表"，位置就定在
#: 本模块——与 `ORG_TYPES`/`ROLES`/`EVENT_TYPES` 同一处，下游本来就在这里取枚举。
#: 放在这里的另一个理由：依赖方向本就是 backend → war_extraction（见决策第 10 项），
#: 反过来会让抽取链被 Web 运行环境绑架。
#:
#: 内容来源是 backend `dynasty_data.DYNASTY_CORRECTIONS`（逐字搬过来，行为不变），
#: 所以 `商汤`/`夏朝`/`清朝` 这类"带朝字或错写"的写法被收敛到抽取口径的简称。
#: **`汉朝 → 西汉`、`宋朝 → 北宋` 是历史遗留的粗口径**（东汉/南宋也会被这样收敛），
#: 本轮只做搬家、不改语义——要精细化得连着产物与前端筛选一起评估。
DYNASTY_ALIASES = {
    "商汤": "商", "商朝": "商", "夏朝": "夏", "周朝": "西周",
    "秦朝": "秦", "汉朝": "西汉", "隋朝": "隋", "唐朝": "唐",
    "宋朝": "北宋", "辽朝": "辽", "金朝": "金", "元朝": "元",
    "明朝": "明", "清朝": "清",
}


# --------------------------------------------------------------------- 归一 helper
#
# 口径统一为"**匹配不上就保留原值**"，不做子串猜测、也不一律抹成某个默认值：
# 猜一个（在线侧 `_normalize_role` 的 `if role in name`）会把"曹操"猜成"君主"，
# 抹成默认值则会把"关键人物"这类真实信息彻底抹掉。保留原值 + 由体检脚本与
# schema 校验层如实计数，才能让"枚举该不该再扩"变成一个有数据支撑的决定。


def _normalize_against(value, allowed: FrozenSet[str], aliases: Optional[dict] = None) -> Tuple[str, bool]:
    """返回 (归一后的值, 是否命中枚举)。空值原样返回且算未命中。"""
    text = (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value).strip())
    if not text:
        return text, False
    if aliases:
        text = aliases.get(text, text)
    if text in allowed:
        return text, True
    return text, False


#: 角色别名：同一职能的常见写法收敛到枚举值。"参战者/参与者"是历史并存的两套口径，
#: 实体侧用 `参战者`，这里把 `参与者` 收进来，避免同一个人在两处显示成不同角色。
_ROLE_ALIASES = {
    "参与者": "参战者",
    "参战人员": "参战者",
    "将领/统帅": "统帅",
}

#: 组织类型别名：只收敛明显同义的写法，不猜。
_ORG_TYPE_ALIASES = {
    "政权": "国家",
    "王朝": "国家",
    "部落联盟": "部落",
    "部族": "部落",
    "起义军势力": "起义军",
    "割据势力": "地方势力",
}


def normalize_role(value) -> Tuple[str, bool]:
    """人物角色归一。返回 (值, 是否命中枚举)；未命中时**保留原值**。"""
    return _normalize_against(value, ROLES, _ROLE_ALIASES)


def normalize_org_type(value) -> Tuple[str, bool]:
    """组织类型归一。返回 (值, 是否命中枚举)；未命中时**保留原值**。"""
    return _normalize_against(value, ORG_TYPES, _ORG_TYPE_ALIASES)


def normalize_event_type(value) -> Tuple[str, bool]:
    """
    事件类型归一。返回 (值, 是否命中枚举)。

    空值按提示词的兜底口径落在 `战争`——提示词里写着"不确定则填'战争'"，
    而产物里那 4 条 `EventType = null` 会让 RAG 侧 `or "战争"` 兜底、
    污染它自己的字典统计，所以这里就补上，别留给下游猜。
    """
    text, hit = _normalize_against(value, EVENT_TYPES)
    if not text:
        return "战争", True
    return text, hit


def normalize_dynasty(value) -> Tuple[str, bool]:
    """
    朝代写法归一。返回 (值, 是否命中已知口径)；未命中时**保留原值**。

    与另外三个归一函数的区别：**结果不参与任何校验**（`DYNASTY_REFERENCE` 只是参考分布，
    不是白名单，见上面那段说明）。这里的 `hit` 只表示"这个写法认得出来"，
    不代表"合法"。用途是让 backend 的展示/问答侧与 RAG 的治理侧取到同一个规范写法，
    而不是各自维护一份映射。
    """
    return _normalize_against(value, DYNASTY_REFERENCE, DYNASTY_ALIASES)


def relation_type_allowed(relation_type: str, category: str = None) -> bool:
    """
    关系名是否在允许集合内。给 `category` 时按该类别的集合判定，否则按四类合集判定。
    """
    text = (relation_type or "").strip()
    if not text:
        return False
    if category:
        allowed = RELATION_TYPES_BY_CATEGORY.get(category)
        if allowed is not None:
            return text in allowed
    return text in ALL_RELATION_TYPES
