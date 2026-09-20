"""F09 延伸：规则推理固化（docs/RAG_v2/RAG规则推理移植-需求与设计.md）。

在快照构建之后、索引构建之前，对 `relations.json` 应用规则库（`data/rules/rule_base.json`），
把推理结果固化成 `inferred_relations.json`（与 relations.json 同构 + 推理标记）。

口径：

- **反向规则**：`事件 --[原始关系]--> X` ⇒ `X --[推理关系]--> 事件`
  （`direction: reverse` 时交换两端；forward 保持原方向）；
- **复合规则**：沿同一关系类型走 `path_length` 步（`A --r--> B --r--> C`），产出跨越中间节点的
  新关系；同一 `(起点, 终点)` 只产一条（与旧系统一致）；
- 跳过 `pending_review=True` 的关系（与在线加载口径一致）；
- 每行都带 `inferred/rule_id/rule_name/derived_from/derived_from_rows`，使引用与证据可回指
  到规则与原始行——这是"推理关系必须能与原始关系区分"的落地（需求分析 §4.4）；
- 输出按 `(rule_id, source, relation, target)` 稳定排序、UTF-8 + LF 落盘，重复构建字节一致；
- 单规则产出上限（默认 20000）防路径爆炸，截断写入报告。

与旧实现的两处差异见设计文档 §2.1：3 步路径本次实现；war_020（3 步包含链）在当前快照上
0 命中属数据事实（包含关系仅 28 条边、构不成 3 步链），报告里单列原因。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from contracts.inference import (
    CONFIDENCE_ORDER,
    DEFAULT_CONFIDENCE,
    InferenceAction,
    InferenceCondition,
    InferenceReport,
    InferenceRule,
    InferredRelation,
)
from lib.json_io import read_json, write_json

MAX_PER_RULE_DEFAULT = 20000
# 默认规则库：RAG/data/rules/rule_base.json（本文件在 RAG/data/snapshot/ 下，故上溯两级）
_DEFAULT_RULES_FILE = Path(__file__).resolve().parent.parent / "rules" / "rule_base.json"


def load_rules(path: Path) -> list[InferenceRule]:
    """读规则库（结构对齐旧系统 rule_base.json：condition / inference / description）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"规则库应为列表：{path}")
    rules: list[InferenceRule] = []
    for item in raw:
        condition = item.get("condition") or {}
        inference = item.get("inference") or {}
        if not condition.get("relation") or not inference.get("relation"):
            raise ValueError(f"规则缺少 condition.relation 或 inference.relation：{item}")
        rules.append(InferenceRule(
            rule_id=str(item.get("rule_id", "")),
            name=str(item.get("name", "")),
            condition=InferenceCondition(
                relation=str(condition["relation"]),
                composite=bool(condition.get("composite", False)),
                path_length=int(condition.get("path_length", 1) or 1),
            ),
            inference=InferenceAction(
                relation=str(inference["relation"]),
                direction=str(inference.get("direction", "forward")),
                transitive=bool(inference.get("transitive", False)),
            ),
            description=str(item.get("description", "")),
        ))
    return rules


def _confidence_of(row: dict) -> str:
    value = row.get("confidence")
    return value if value in CONFIDENCE_ORDER else DEFAULT_CONFIDENCE


def _lowest_confidence(rows: list[dict]) -> str:
    """复合规则取路径上最低的一档（不做放大）。"""
    order = {c: i for i, c in enumerate(CONFIDENCE_ORDER)}
    return min((_confidence_of(r) for r in rows), key=lambda c: order.get(c, 1))


def _origin_rows(rows: list[dict]) -> list[list]:
    """溯源链：[[source_row_id, legacy_table], ...]（缺行号的来源不写，避免假可追溯）。"""
    out: list[list] = []
    for row in rows:
        rid = row.get("source_row_id")
        if rid is None:
            continue
        pair = [rid, row.get("legacy_table") or ""]
        if pair not in out:
            out.append(pair)
    return out


def _names(entities: dict[str, dict], entity_id: str, fallback: str = "") -> tuple[str, str]:
    ent = entities.get(entity_id) or {}
    return ent.get("name") or fallback, ent.get("type") or ""


def apply_reverse_rules(
    relations: list[dict],
    rules: list[InferenceRule],
    entities: dict[str, dict],
    version: str,
    max_per_rule: int = MAX_PER_RULE_DEFAULT,
) -> tuple[list[InferredRelation], dict[str, int], list[str]]:
    """反向规则：原始关系 1:1 映射为反转后的推理关系（同一规则超过上限即截断）。

    返回 (推理行, 每规则产出数, 被截断的规则 id)。反向规则理论上不会爆炸（产出 ≤ 原始行数），
    上限在这里同样生效，保证"单规则上限"对所有规则口径一致。
    """
    by_relation: dict[str, list[InferenceRule]] = defaultdict(list)
    for rule in rules:
        if not rule.condition.composite:
            by_relation[rule.condition.relation].append(rule)

    produced: dict[str, int] = defaultdict(int)
    truncated: list[str] = []
    full: set[str] = set()
    out: list[InferredRelation] = []
    for row in relations:
        if row.get("pending_review"):
            continue
        for rule in by_relation.get(str(row.get("relation")), ()):
            if rule.rule_id in full:
                continue
            s_id = str(row.get("source_entity_id") or "")
            t_id = str(row.get("target_entity_id") or "")
            if not s_id or not t_id:
                continue
            s_name, _ = _names(entities, s_id, row.get("source_name") or "")
            t_name, _ = _names(entities, t_id, row.get("target_name") or "")
            if rule.inference.direction == "reverse":
                s_id, t_id = t_id, s_id
                s_name, t_name = t_name, s_name
            out.append(InferredRelation(
                source_entity_id=s_id, source_name=s_name,
                relation=rule.inference.relation,
                target_entity_id=t_id, target_name=t_name,
                confidence=_confidence_of(row),
                rule_id=rule.rule_id, rule_name=rule.name,
                derived_from=str(row.get("relation") or ""),
                derived_from_rows=_origin_rows([row]),
                composite=False,
                source_version=version,
            ))
            produced[rule.rule_id] += 1
            if produced[rule.rule_id] >= max_per_rule:
                full.add(rule.rule_id)
                truncated.append(rule.rule_id)
    return out, dict(produced), truncated


def _walk_chains(
    index: dict[str, dict[str, list[dict]]],
    relation: str,
    start_id: str,
    steps: int,
) -> Iterator[list[dict]]:
    """沿同一关系类型走 steps 步，必要时产出每条路径的行序列（防环、防重复节点）。"""
    def rec(node_id: str, chain: list[dict], visited: frozenset) -> Iterator[list[dict]]:
        if len(chain) == steps:
            yield chain
            return
        for row in index.get(relation, {}).get(node_id, ()):
            nxt = str(row.get("target_entity_id") or "")
            if not nxt or nxt in visited:
                continue
            yield from rec(nxt, chain + [row], visited | {nxt})

    yield from rec(start_id, [], frozenset({start_id}))


def apply_composite_rules(
    relations: list[dict],
    rules: list[InferenceRule],
    entities: dict[str, dict],
    version: str,
    max_per_rule: int = MAX_PER_RULE_DEFAULT,
) -> tuple[list[InferredRelation], dict[str, int], list[str], dict[str, str]]:
    """复合规则：N 步同关系链 ⇒ 跨越中间节点的新关系。

    返回 (推理行, 每规则产出数, 被截断的规则 id, 每规则备注)。
    """
    index: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in relations:
        if row.get("pending_review"):
            continue
        index[str(row.get("relation"))][str(row.get("source_entity_id"))].append(row)

    out: list[InferredRelation] = []
    produced: dict[str, int] = defaultdict(int)
    truncated: list[str] = []
    notes: dict[str, str] = {}

    for rule in rules:
        if not rule.condition.composite:
            continue
        relation = rule.condition.relation
        steps = max(1, rule.condition.path_length)
        seen_pairs: set[tuple[str, str]] = set()
        hit_limit = False

        for start_id in list(index.get(relation, {}).keys()):
            for chain in _walk_chains(index, relation, start_id, steps):
                end_id = str(chain[-1].get("target_entity_id") or "")
                if not end_id:
                    continue
                pair = (start_id, end_id)
                if pair in seen_pairs:
                    continue        # 同一 (起点, 终点) 只产一条
                seen_pairs.add(pair)

                s_id, t_id = start_id, end_id
                s_name, _ = _names(entities, s_id, chain[0].get("source_name") or "")
                t_name, _ = _names(entities, t_id, chain[-1].get("target_name") or "")
                if rule.inference.direction == "reverse":
                    s_id, t_id = t_id, s_id
                    s_name, t_name = t_name, s_name
                middle_ids = [str(row.get("target_entity_id") or "") for row in chain[:-1]]
                path = []
                for mid in middle_ids:
                    name, etype = _names(entities, mid)
                    path.append({"entity_id": mid, "name": name, "type": etype})

                out.append(InferredRelation(
                    source_entity_id=s_id, source_name=s_name,
                    relation=rule.inference.relation,
                    target_entity_id=t_id, target_name=t_name,
                    confidence=_lowest_confidence(chain),
                    rule_id=rule.rule_id, rule_name=rule.name,
                    derived_from=f"{relation}链",
                    derived_from_rows=_origin_rows(chain),
                    composite=True,
                    path=path,
                    source_version=version,
                ))
                produced[rule.rule_id] += 1
                if produced[rule.rule_id] >= max_per_rule:
                    hit_limit = True
                    break
            if hit_limit:
                break

        if hit_limit:
            truncated.append(rule.rule_id)
            notes[rule.rule_id] = f"达到单规则上限 {max_per_rule} 条，已截断"
        elif produced[rule.rule_id] == 0:
            notes[rule.rule_id] = f"{steps} 步 {relation}链在当前快照无命中"

    return out, dict(produced), truncated, notes


def build_inferred_relations(
    snapshot_dir: Path,
    rules_path: Optional[Path] = None,
    max_per_rule: int = MAX_PER_RULE_DEFAULT,
    dry_run: bool = False,
) -> InferenceReport:
    """对快照应用规则并落盘 `inferred_relations.json` + `inference_report.json`。

    dry_run=True 时只统计与返回报告，不写任何文件（用于规则改动前的产出预估）。
    """
    snapshot_dir = Path(snapshot_dir)
    rules_path = Path(rules_path) if rules_path else _DEFAULT_RULES_FILE
    version = snapshot_dir.name

    rules_raw = rules_path.read_bytes()
    rules = load_rules(rules_path)
    entities = {e["entity_id"]: e for e in read_json(snapshot_dir / "entities.json")}
    relations = read_json(snapshot_dir / "relations.json")
    pending = sum(1 for r in relations if r.get("pending_review"))

    reverse_rows, reverse_count, reverse_truncated = apply_reverse_rules(
        relations, rules, entities, version, max_per_rule)
    composite_rows, composite_count, composite_truncated, notes = apply_composite_rules(
        relations, rules, entities, version, max_per_rule)
    truncated = list(dict.fromkeys(reverse_truncated + composite_truncated))
    for rule_id in truncated:
        notes.setdefault(rule_id, f"达到单规则上限 {max_per_rule} 条，已截断")

    # 去重（同一 (源, 关系, 目标, 规则) 只留一条，覆盖原始重复行引起的重复推理）
    seen: set[tuple] = set()
    merged: list[InferredRelation] = []
    for row in reverse_rows + composite_rows:
        key = (row.source_entity_id, row.relation, row.target_entity_id, row.rule_id)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)

    # 稳定排序：重复构建字节一致（发布证据/哈希对账依赖）
    merged.sort(key=lambda r: (r.rule_id, r.source_entity_id, r.relation, r.target_entity_id))

    out_path = snapshot_dir / "inferred_relations.json"
    if not dry_run:
        write_json(out_path, [row.to_dict() for row in merged])
    output_sha256 = hashlib.sha256(
        out_path.read_bytes() if out_path.exists() else
        json.dumps([row.to_dict() for row in merged], ensure_ascii=False,
                   indent=2).encode("utf-8")
    ).hexdigest()

    per_rule = []
    for rule in rules:
        count = reverse_count.get(rule.rule_id, 0) + composite_count.get(rule.rule_id, 0)
        per_rule.append({
            "rule_id": rule.rule_id,
            "name": rule.name,
            "produced": count,
            "note": notes.get(rule.rule_id, ""),
        })

    report = InferenceReport(
        version=version,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        rules_file=str(rules_path),
        rules_sha256=hashlib.sha256(rules_raw).hexdigest(),
        input_relation_count=len(relations),
        skipped_pending_review=pending,
        output_count=len(merged),
        output_sha256=output_sha256,
        per_rule=per_rule,
        truncated_rules=truncated,
    )
    if not dry_run:
        write_json(snapshot_dir / "inference_report.json", report.to_dict())
    return report
