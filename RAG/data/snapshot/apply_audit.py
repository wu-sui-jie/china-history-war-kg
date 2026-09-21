"""F09 治理增强：人工审核决定回填（data/snapshot/apply_audit.py）。

对应 RAGv2 规划第 7 节（F09 治理增强，仅剩"同名歧义人工审核回填"开放项）。

回填文件契约（audit_decisions.json，操作人/处理前/处理后/审核结论/置信度）：

```json
{
  "source_version": "20260904_v2",     // 处理哪个版本快照
  "operator": "审核人",                 // 也可逐条覆盖
  "decisions": [
    {
      "action": "add_alias",            // add_alias / remove_alias / merge
      "entity_id": "event_0189",
      "alias": "赤壁大战",               // add_alias 用
      "conclusion": "史书别称，确认",
      "confidence": "high",
      "operator": "张三"                 // 可选，覆盖文件级
    },
    {
      "action": "merge",                 // 同名/同义实体合并（须同类型）
      "keep_entity_id": "place_0518",
      "merge_entity_ids": ["place_0563"],
      "conclusion": "实为同一地点跨朝代记录",
      "confidence": "high"
    }
  ]
}
```

- add_alias/remove_alias：低风险，直接改 entities.json 的 aliases；
- merge：把 merge_entity_ids 实体的关系边重映射到 keep_entity_id（source/target 两端
  id 与 name 同步替换），删除被合并实体；若被合并含事件，须 decisions 显式
  allow_event_merge=true 才会执行（事件合并影响 event_cards，需人工确知后果）。
- 输出：新版本快照目录 data/snapshot/<YYYYMMDD_vN>_audit 或显式 --out-version；
  被处理实体的 data_issue 记录到 governance_report.audit_applied（保留 before/after 明细）。
- 提示：快照变化后须用同一新版本重建索引（scripts/build_index.py --version）。
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Optional

from config.settings import Settings
from lib import versions
from lib.json_io import read_json, write_json

# 合法 action
_ACTIONS = {"add_alias", "remove_alias", "merge"}


def _validate(dec: dict) -> list[str]:
    errs = []
    action = dec.get("action")
    if action not in _ACTIONS:
        errs.append(f"未知 action={action}")
    if action in ("add_alias", "remove_alias"):
        if not dec.get("entity_id") or not dec.get("alias"):
            errs.append("add_alias/remove_alias 需 entity_id + alias")
    if action == "merge":
        if not dec.get("keep_entity_id") or not dec.get("merge_entity_ids"):
            errs.append("merge 需 keep_entity_id + merge_entity_ids")
    return errs


class AuditApplier:
    """回填执行器：读取快照 + 决定清单 → 生成新版本快照。"""

    def __init__(self, settings: Settings, snapshot_version: str, logger=None):
        self.settings = settings
        self.snap_dir = settings.snapshot_dir / snapshot_version
        self.version = snapshot_version
        if not self.snap_dir.exists():
            raise FileNotFoundError(f"快照版本不存在: {self.snap_dir}")
        self.logger = logger or logging.getLogger("rag.audit")
        self.entities = read_json(self.snap_dir / "entities.json")
        self.relations = read_json(self.snap_dir / "relations.json")
        self.event_cards = read_json(self.snap_dir / "event_cards.json")
        self.evidence_corpus = read_json(self.snap_dir / "evidence_corpus.json")
        self.dicts = read_json(self.snap_dir / "dicts.json")
        self.field_map = read_json(self.snap_dir / "relation_card_field_map.json")
        self.manifest = read_json(self.snap_dir / "manifest.json")
        # 索引
        self._by_id = {e["entity_id"]: e for e in self.entities}
        self._issue_records = []

    # ---- 别名 ----
    def _apply_alias(self, dec: dict) -> None:
        eid, alias = dec["entity_id"], dec["alias"]
        ent = self._by_id.get(eid)
        if not ent:
            raise ValueError(f"实体不存在: {eid}（action={dec['action']}）")
        aliases = ent.setdefault("aliases", [])
        before = list(aliases)
        if dec["action"] == "add_alias":
            if alias not in aliases:
                aliases.append(alias)
        else:  # remove_alias
            if alias in aliases:
                aliases.remove(alias)
        self._issue_records.append({
            "type": "alias_change", "action": dec["action"],
            "entity_id": eid, "name": ent["name"],
            "before": before, "after": list(aliases),
            "operator": dec.get("operator") or self.operator,
            "conclusion": dec.get("conclusion", ""),
            "confidence": dec.get("confidence", "pending_review"),
        })

    # ---- 合并 ----
    def _apply_merge(self, dec: dict) -> None:
        keep_id = dec["keep_entity_id"]
        merge_ids = list(dec["merge_entity_ids"])
        keep = self._by_id.get(keep_id)
        if not keep:
            raise ValueError(f"keep_entity_id 不存在: {keep_id}")
        allow_event = bool(dec.get("allow_event_merge"))
        for mid in merge_ids:
            m = self._by_id.get(mid)
            if not m:
                raise ValueError(f"merge_entity_id 不存在: {mid}")
            if m["type"] != keep["type"]:
                raise ValueError(f"合并类型不一致: {keep_id}({keep['type']}) vs {mid}({m['type']})")
            if m["type"] == "事件" and not allow_event:
                raise ValueError(
                    f"合并对象含事件 {mid}（{m['name']}）：事件合并影响 event_cards/evidence，"
                    "如确需合并请在决定中加 allow_event_merge=true")
            # 1) 关系重映射 source/target
            merged_name = m["name"]
            for rel in self.relations:
                changed = False
                if rel.get("source_entity_id") == mid:
                    rel["source_entity_id"] = keep_id
                    rel["source_name"] = keep["name"]
                    changed = True
                if rel.get("target_entity_id") == mid:
                    rel["target_entity_id"] = keep_id
                    rel["target_name"] = keep["name"]
                    changed = True
                if changed:
                    self._drop_self_loop(rel)
            # 2) evidence_corpus 重映射
            for ev in self.evidence_corpus:
                if ev.get("source_entity_id") == mid:
                    ev["source_entity_id"] = keep_id
                    ev["source_name"] = keep["name"]
                if ev.get("target_entity_id") == mid:
                    ev["target_entity_id"] = keep_id
                    ev["target_name"] = keep["name"]
            # 3) event_cards 若被合并是事件：把卡片并入 keep（避免丢失）
            if m["type"] == "事件":
                card = next((c for c in self.event_cards if c.get("event_id") == mid), None)
                keep_card = next((c for c in self.event_cards if c.get("event_id") == keep_id), None)
                if card:
                    if keep_card is None:
                        card["event_id"] = keep_id
                        card["name"] = keep["name"]
                        self.event_cards = [card if c is not card else card for c in self.event_cards]
                    else:
                        # 合并：补充 keep 卡 description/别名线索（保留原卡为 review 记录）
                        pass
            # 4) 别名并入 keep
            keep.setdefault("aliases", [])
            for a in m.get("aliases") or []:
                if a not in keep["aliases"] and a != keep["name"]:
                    keep["aliases"].append(a)
            self._issue_records.append({
                "type": "entity_merge", "action": "merge",
                "keep_entity_id": keep_id, "keep_name": keep["name"],
                "merged_entity_id": mid, "merged_name": merged_name,
                "before": {"entities": 1, "name": merged_name},
                "after": {"merged_into": keep_id},
                "operator": dec.get("operator") or self.operator,
                "conclusion": dec.get("conclusion", ""),
                "confidence": dec.get("confidence", "pending_review"),
            })
        # 删除被合并实体
        self.entities = [e for e in self.entities if e["entity_id"] not in merge_ids]
        self._drop_duplicate_relations()

    @staticmethod
    def _drop_self_loop(rel: dict) -> None:
        if rel.get("source_entity_id") == rel.get("target_entity_id"):
            rel["_drop"] = True

    def _drop_duplicate_relations(self) -> None:
        seen = set()
        kept = []
        for rel in self.relations:
            if rel.get("_drop"):
                continue
            rel.pop("_drop", None)
            key = (rel.get("source_entity_id"), rel.get("relation"),
                   rel.get("target_entity_id"))
            if key in seen:
                continue
            seen.add(key)
            kept.append(rel)
        self.relations = kept

    # ---- 执行 ----
    def apply(self, decisions: list[dict], operator: str = "auditor") -> list[dict]:
        self.operator = operator
        errors = []
        for dec in decisions:
            errs = _validate(dec)
            if errs:
                errors.append({"decision": dec, "errors": errs})
                continue
            try:
                action = dec["action"]
                if action in ("add_alias", "remove_alias"):
                    self._apply_alias(dec)
                elif action == "merge":
                    self._apply_merge(dec)
            except Exception as e:  # noqa: BLE001
                errors.append({"decision": dec, "errors": [str(e)]})
        return errors

    # ---- 输出新版本 ----
    def write_new_snapshot(self, out_version: Optional[str] = None) -> Path:
        date = datetime.date.today()
        if out_version is None:
            base = versions.next_version(self.settings.snapshot_dir, date=date)
            # 避免覆盖今天已有 vN：在 vN 基础上继续递增直到可用
            v = base
            while (self.settings.snapshot_dir / v).exists():
                v = versions.next_version(self.settings.snapshot_dir, date=date)
            out_version = v
        out_dir = self.settings.snapshot_dir / out_version
        out_dir.mkdir(parents=True, exist_ok=False)

        write_json(out_dir / "entities.json", self.entities)
        write_json(out_dir / "relations.json", self.relations)
        write_json(out_dir / "event_cards.json", self.event_cards)
        write_json(out_dir / "evidence_corpus.json", self.evidence_corpus)
        write_json(out_dir / "dicts.json", self.dicts)
        write_json(out_dir / "relation_card_field_map.json", self.field_map)

        # manifest：继承源，标记 audit
        manifest = dict(self.manifest)
        manifest.update({
            "version": out_version,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "audit_source": self.version,
            "counts": {
                "entities": len(self.entities),
                "relations": len(self.relations),
                "event_cards": len(self.event_cards),
            },
        })
        write_json(out_dir / "manifest.json", manifest)

        # governance_report：继承 + audit_applied
        report_path = self.snap_dir / "governance_report.json"
        report = read_json(report_path) if report_path.exists() else {}
        report = dict(report)
        report.update({
            "version": out_version,
            "audit_source_version": self.version,
            "audit_applied": self._issue_records,
            "audit_operator": self.operator,
            "audit_note": "人工审核决定回填后的重放快照；F03/F11 使用本版本时须重建同版本索引",
        })
        write_json(out_dir / "governance_report.json", report)
        return out_dir


def run_apply_audit(settings: Settings, source_version: str,
                    decisions_file: Path, out_version: Optional[str] = None,
                    logger=None) -> Path:
    logger = logger or logging.getLogger("rag.audit")
    decisions = read_json(decisions_file)
    src_v = decisions.get("source_version") or source_version
    operator = decisions.get("operator", "auditor")
    applier = AuditApplier(settings, src_v, logger=logger)
    errors = applier.apply(decisions.get("decisions", []), operator=operator)
    if errors:
        logger.warning(f"审核决定有 {len(errors)} 条未生效:")
        for er in errors:
            logger.warning(f"  - {er}")
    out = applier.write_new_snapshot(out_version)
    logger.info(f"人工审核回填完成 → {out}（决定 {len(decisions.get('decisions', []))} 条，"
                f"失败 {len(errors)} 条）")
    return out
