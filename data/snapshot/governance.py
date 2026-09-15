"""F09 治理编排：把导出数据治理成带版本号的干净快照 + 治理报告。

主入口 `run_governance(settings, version, logger)`。
产物目录结构（见 data/snapshot/README.md）：
  data/snapshot/<YYYYMMDD_vN>/
    manifest.json  entities.json  relations.json  event_cards.json
    dicts.json  governance_report.json  audit/
"""

from __future__ import annotations

import datetime
import logging
from collections import Counter
from pathlib import Path

from config.settings import Settings, get_settings
from lib import versions
from lib.json_io import write_json

from data.snapshot import alias as alias_mod
from data.snapshot import export as export_mod
from data.snapshot import field_map as field_map_mod
from data.snapshot import isolate as isolate_mod
from data.snapshot import normalize as norm

# 实体前缀
PREFIX = {"事件": "event", "人物": "person", "组织": "org", "地点": "place"}


def _assign_entity_ids(entities: list[dict], events: list[dict]) -> tuple[list[dict], dict]:
    """为实体分配快照 id（event_0001 / place_0001 …），记录名称计数。"""
    counters: Counter = Counter()
    out = []
    # 先给 events 分配，再给其他实体分配
    for rec in list(events) + list(entities):
        etype = rec["type"]
        counters[etype] += 1
        rec["entity_id"] = f"{PREFIX[etype]}_{counters[etype]:04d}"
        rec["entity_type"] = etype  # 统一字段名：type/entity_type 并用
        out.append(rec)
    name_counter = Counter(r.get("name") for r in out)
    return out, {"by_type": {k: counters[k] for k in PREFIX}}


def _build_event_cards(events: list[dict]) -> list[dict]:
    """由事件富字段拼装事件卡片语料（供 F11 event_card 切分）。

    卡片同时输出两类字段：
    - 结构化字段（data-contract L239 要求）：event_id/name/dynasty/event_type/start_date/
      aggressor/defender/persons/place/action/result/impact/scale；
    - description 富文本：由上述字段拼接的可读全文（保留作切分与展示）。
    F05 的 field_vs_triple 冲突判定读取结构化字段，无需再解析 description。
    """
    cards = []
    for ev in events:
        name = ev.get("name") or ""
        fields = {
            "event_type": ev.get("event_type"),
            "dynasty": ev.get("dynasty"),
            "start_date": ev.get("start_date"),
            "place": ev.get("place"),
            "aggressor": ev.get("aggressor"),
            "defender": ev.get("defender"),
            "persons": ev.get("person"),
            "action": ev.get("action"),
            "result": ev.get("result"),
            "impact": ev.get("impact"),
            "scale": ev.get("scale"),
        }
        # description：只拼非空字段，保持可读
        label_map = [
            ("event_type", "类型"), ("dynasty", "朝代"), ("start_date", "时间"),
            ("place", "地点"), ("aggressor", "发起方"), ("defender", "防守方"),
            ("persons", "人物"), ("action", "经过"), ("result", "结果"),
            ("scale", "规模"), ("impact", "影响"),
        ]
        parts = [f"{label}：{fields[key]}" for key, label in label_map if fields.get(key)]
        if ev.get("source"):
            parts.append(f"原文：{ev['source']}")
        cards.append({
            "event_id": ev["entity_id"],
            "name": name,
            "type": "事件",
            "dynasty": fields["dynasty"],
            "event_type": fields["event_type"],
            "start_date": fields["start_date"],
            # 结构化字段（data-contract：事件卡包含参与方/地点/结果等）
            "aggressor": fields["aggressor"],
            "defender": fields["defender"],
            "persons": fields["persons"],
            "place": fields["place"],
            "action": fields["action"],
            "result": fields["result"],
            "impact": fields["impact"],
            "scale": fields["scale"],
            "description": "\n".join(parts),
            "source": f"events#{ev.get('source_row_id')}",
        })
    return cards


def _normalize_all(events: list[dict]) -> dict:
    """对事件做类型/朝代归一。返回 (data_issues, event_type 分布计数)。"""
    issues = []
    et_counter: Counter = Counter()
    for ev in events:
        # 朝代
        new_dynasty, issue = norm.normalize_dynasty(ev)
        if issue:
            issues.append(issue)
            ev["dynasty"] = new_dynasty
            ev["dynasty_original"] = issue["from"]
        # 事件类型（保留原始值便于报告追溯）
        raw_type = ev.get("event_type")
        std_type = norm.normalize_event_type(raw_type)
        if std_type is None:
            ev["event_type"] = None
        else:
            ev["event_type"] = std_type
            et_counter[std_type] += 1
        ev["event_type_raw"] = raw_type
    return {"data_issues": issues, "event_type_counts": dict(et_counter)}


def _build_relations(relations: list[dict], id_by_legacy: dict) -> list[dict]:
    """把关系 id 映射到快照实体 id，产出 RelationEdge 落盘结构。

    id_by_legacy: {(legacy_table, source_row_id): entity_id}
    映射不到的（如悬空外键）保留 raw 供报告，不产出关系。
    """
    out = []
    dangling = 0
    for rel in relations:
        s_eid = id_by_legacy.get((_src_table(rel), rel["source_id"]))
        t_eid = id_by_legacy.get((_tgt_table(rel), rel["target_id"]))
        if not s_eid or not t_eid:
            dangling += 1
            continue
        row = {
            "source_entity_id": s_eid,
            "source_name": rel.get("source_name"),
            "relation": rel["relation"],
            "target_entity_id": t_eid,
            "target_name": rel.get("target_name"),
            "confidence": _relation_confidence(rel),
            "source_type": "kg_relation",
            "source_row_id": rel["source_row_id"],
            "legacy_table": rel["legacy_table"],
            "pending_review": False,
        }
        # 地点关系带 evidence 原文 → 作为溯源 evidence（供 F11 evidence 语料）
        if rel.get("evidence"):
            row["evidence"] = rel["evidence"]
        if rel.get("source_type"):
            row["legacy_source_type"] = rel["source_type"]
        out.append(row)
    return out, dangling


def _src_table(rel: dict) -> str:
    hint = rel.get("source_type_hint")
    if hint == "事件":
        return "events"
    if hint == "地点":
        return "places"
    if hint == "人物":
        return "persons"
    if hint == "组织":
        return "organizations"
    return rel["legacy_table"]


def _tgt_table(rel: dict) -> str:
    hint = rel.get("target_type_hint")
    if hint == "事件":
        return "events"
    if hint == "地点":
        return "places"
    if hint == "人物":
        return "persons"
    if hint == "组织":
        return "organizations"
    return rel["legacy_table"]


def _relation_confidence(rel: dict) -> str:
    """置信度优先取旧表显式 confidence；缺失时保守默认 medium，避免高估。"""
    lc = rel.get("legacy_confidence")
    if lc in ("high", "medium", "low"):
        return lc
    return "medium"


def _write_audit_duplicates(out_dir: Path, dup_groups: list[dict]) -> None:
    audit_dir = out_dir / "audit"
    write_json(
        audit_dir / "duplicate_name_groups.json",
        [{"display_name": g["display_name"], "count": g["count"],
          "rows": [{"entity_id": r.get("entity_id"), "name": r.get("name"),
                    "type": r.get("type"), "dynasty": r.get("dynasty"),
                    "event_type": r.get("event_type")} for r in g["records"]]}
         for g in dup_groups],
    )


def run_governance(settings: Settings, version: str | None = None,
                   logger: logging.Logger | None = None) -> Path:
    logger = logger or logging.getLogger("rag.snapshot")
    version = version or versions.next_version(settings.snapshot_dir)
    if not versions.is_valid_version(version):
        raise ValueError(f"非法版本号: {version}（应为 YYYYMMDD_vN）")

    out_dir = settings.snapshot_dir / version
    if out_dir.exists():
        raise FileExistsError(f"快照目录已存在: {out_dir}（勿覆盖旧版本，请递增版本号）")
    out_dir.mkdir(parents=True, exist_ok=False)

    # 1) 导出
    logger.info("step1 导出旧数据…")
    exported = export_mod.export_snapshot(settings, out_dir, logger)
    raw_entities = exported["entity_records"]  # 数量
    # export 当前不返回原始记录列表，重新读一次原始便于治理：
    # （export_snapshot 只写 raw；治理实体需在内存中加工）
    # 因此这里直接从 sqlite 取原始记录由 governance 组装 —— 见下方直接调用 export_entities 等。
    con = export_mod._connect(settings.legacy_sqlite_path)
    try:
        entities_raw = export_mod.export_entities(con)
        events_raw = export_mod.export_events(con)
        relations_raw = export_mod.export_relations(con)
    finally:
        con.close()

    # 2) 归一事件（朝代/战争类型）
    logger.info("step2 归一化（战争类型/朝代）…")
    norm_info = _normalize_all(events_raw)
    # 3) 分配实体 id
    all_entities, id_counts = _assign_entity_ids(entities_raw, events_raw)
    # 4) 构建别名/词典
    logger.info("step3 构建别名与词典…")
    place_alias = alias_mod.build_place_aliases(
        [e for e in all_entities if e["type"] == "地点"])
    dup_groups = alias_mod.duplicate_name_groups(
        [e for e in all_entities if e["type"] in ("事件", "人物", "组织", "地点")])

    # id_by_legacy 用于关系映射
    id_by_legacy = {
        (e["legacy_table"], e["source_row_id"]): e["entity_id"] for e in all_entities
    }
    relations, dangling = _build_relations(relations_raw, id_by_legacy)

    # 5) 孤立统计
    logger.info("step4 孤立节点统计…")
    isolated_ids, isolated_stats = isolate_mod.compute_isolated(all_entities, relations)
    for e in all_entities:
        e["is_isolated"] = e["entity_id"] in isolated_ids

    # 6) 事件卡片 + 关系证据语料 + 关系-卡片字段映射表
    event_cards = _build_event_cards([e for e in all_entities if e["type"] == "事件"])
    evidence_corpus = _build_evidence_corpus(relations)
    # 目标实体类型从实体表反查（relations 行无冗余类型，保证映射表按真实类型对生成）
    entity_type_by_id = {e["entity_id"]: e["type"] for e in all_entities}
    relation_type_pairs = {
        (r["relation"], entity_type_by_id.get(r["target_entity_id"], "")) for r in relations
    }
    relation_card_field_map = field_map_mod.build_field_map(relation_type_pairs)

    # 7) 写盘
    logger.info("step5 写快照与报告…")
    manifest = {
        "version": version,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_sqlite": str(settings.legacy_sqlite_path),
        "source_raw_texts": [str(p) for p in settings.legacy_raw_texts],
        "counts": {
            "entities": len(all_entities),
            "events": sum(1 for e in all_entities if e["type"] == "事件"),
            "places": sum(1 for e in all_entities if e["type"] == "地点"),
            "persons": sum(1 for e in all_entities if e["type"] == "人物"),
            "organizations": sum(1 for e in all_entities if e["type"] == "组织"),
            "relations": len(relations),
            "evidence_corpus": len(evidence_corpus),
            "relation_card_field_map": len(relation_card_field_map["mapping"]),
            "raw_docs": len(exported["raw_docs"]),
        },
        "flags": {
            "governance_enable_relation_extraction": settings.governance_enable_relation_extraction,
        },
    }

    write_json(out_dir / "manifest.json", manifest)
    write_json(out_dir / "entities.json", [_strip_none(e) for e in all_entities])
    write_json(out_dir / "relations.json", relations)
    write_json(out_dir / "event_cards.json", [_strip_none(c) for c in event_cards])
    write_json(out_dir / "evidence_corpus.json", evidence_corpus)
    write_json(out_dir / "relation_card_field_map.json", relation_card_field_map)

    dicts = _build_dicts(all_entities, norm_info["event_type_counts"])
    write_json(out_dir / "dicts.json", dicts)

    # 8) 治理报告
    before_after = _before_after()
    report = {
        "version": version,
        "governed_at": manifest["generated_at"],
        "input": {
            "raw_entities": len(all_entities),
            "raw_events": manifest["counts"]["events"],
            "raw_relations_exported": exported["relation_records"],
        },
        "before_after": before_after,
        "isolated_nodes": isolated_stats,
        "name_duplicates": {
            "groups": len(dup_groups),
            "involved_records": sum(g["count"] for g in dup_groups),
            "detail_file": "audit/duplicate_name_groups.json",
        },
        "dangling_relation_refs": dangling,
        "event_type_distribution": norm_info["event_type_counts"],
        "review_needed_event_types": {
            t: norm_info["event_type_counts"].get(t, 0)
            for t in norm.REVIEW_NEEDED_EVENT_TYPES
        },
        "data_issues": norm_info["data_issues"],
        "dynasty_aliases": dicts["dynasty_aliases"],
        "audit": {
            "placeholder_note": "实体合并/补边等高风险操作需人工审核；初版无自动 merge，见 duplicate_name_groups.json",
        },
    }
    write_json(out_dir / "governance_report.json", report)
    _write_audit_duplicates(out_dir, dup_groups)

    logger.info(f"治理完成 → {out_dir}")
    logger.info(f"  实体 {manifest['counts']['entities']} | 关系 {len(relations)} | "
                f"孤立 {isolated_stats['isolated_total']} | 同名歧义组 {len(dup_groups)}")
    return out_dir


def _build_evidence_corpus(relations: list[dict]) -> list[dict]:
    """关系证据语料：由带 evidence 原文的关系行生成（供 F11 evidence 片段）。

    结构 = F09 关系证据短文本：关系 + 双方 + 原文依据。
    """
    out = []
    for rel in relations:
        ev = rel.get("evidence")
        if not ev or not str(ev).strip():
            continue
        sn = rel.get("source_name") or rel.get("source_entity_id")
        tn = rel.get("target_name") or rel.get("target_entity_id")
        out.append({
            "evidence_id": f"rel_ev_{rel['source_row_id']}",
            "relation": rel["relation"],
            "source_entity_id": rel["source_entity_id"],
            "source_name": sn,
            "target_entity_id": rel["target_entity_id"],
            "target_name": tn,
            "text": str(ev).strip(),
            "confidence": rel.get("confidence", "medium"),
            "legacy_table": rel.get("legacy_table"),
            "source_row_id": rel["source_row_id"],
        })
    return out


def _build_dicts(all_entities: list[dict], event_type_counts: dict) -> dict:
    """生成 dicts.json：标准战争类型、类型映射、朝代别名、现代地名映射。"""
    event_type_standard = sorted(event_type_counts)
    # 事件类型映射：旧 → 标准（此处标准=原样，未做自动合并；供后续人工增补）
    event_type_map = {t: t for t in event_type_standard}
    dynasty_set = sorted({e.get("dynasty") for e in all_entities if e.get("dynasty")})
    return {
        "event_type_standard": event_type_standard,
        "event_type_map": event_type_map,
        "dynasty_aliases": {d: d for d in dynasty_set},
        "place_modern_map": {
            e["name"]: (e.get("modern_name") or "") for e in all_entities
            if e["type"] == "地点" and e.get("modern_name")
        },
        "place_name_aliases": {
            std: aliases for std, aliases in alias_mod.build_place_aliases(
                [e for e in all_entities if e["type"] == "地点"]).items()
            if aliases
        },
    }


def _strip_none(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if v is not None}


def _before_after() -> dict:
    return {
        "note": "初版以只读导出+归一+词典为主；实体合并/补边等高影响治理项待人工审核后启用。"
                "治理前后对比以报告内孤立节点、重复名称、类型分布为准。"
    }
