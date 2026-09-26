# data/snapshot

**归属功能：F09 数据快照与知识库治理（离线）。**

从旧项目只读数据导出 RAG 所需内容，做实体消歧、孤立节点统计、战争类型/朝代归一、
别名与词典生成，输出**带版本号的干净图谱快照 + 治理报告**，供 F03 在线图谱检索与
F11 文本索引使用。本层不依赖旧服务是否启动，只读旧 SQLite 与原文文本。

## 职责（子模块）

| 文件 | 说明 |
| --- | --- |
| `export.py` | 只读导出旧 sqlite 的 events/places/persons/organizations/4 类关系，以及原文文本（utf-8）。 |
| `alias.py` | 构建实体别名词典（地点现代名、人名/组织名简写等），供名称归一。 |
| `normalize.py` | 战争类型同义归一、朝代归一（data_issue 带前后明细）、生成标准词典与事件类型映射表。 |
| `field_map.py` | 生成关系-事件卡片字段映射表（按 relation×目标类型细分，供 F05 冲突判定）。 |
| `isolate.py` | 孤立节点统计与标记（保留、不删除）。 |
| `governance.py` | 编排上述步骤，产出快照目录 + `governance_report.json` 治理报告。 |
| `apply_audit.py` | **RAGv2 新增**：人工审核决定回填（add_alias/remove_alias/merge），输出新版本快照 + audit_applied 明细。 |
| `inference.py` | **2026-09-20 新增（P2）**：规则推理固化——对快照应用 `data/rules/rule_base.json` 的 20 条规则，产出 `inferred_relations.json` + `inference_report.json`（入口 `scripts/build_inferred_relations.py`）。 |

## 产物（每版本一个目录 `data/snapshot/<YYYYMMDD_vN>/`）

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 版本号、来源清单、生成时间、各步参数。 |
| `entities.json` | 干净实体列表（EntityNode，见 contracts/governance.py）。 |
| `relations.json` | 干净关系列表（RelationEdge，含 source_row_id 可追溯）。 |
| `event_cards.json` | 事件卡片语料：**含结构化字段**（aggressor/defender/persons/place/action/result/impact/scale，data-contract L239）+ description 富文本。 |
| `evidence_corpus.json` | 关系证据语料（事件-地点关系 evidence 原文）。 |
| `relation_card_field_map.json` | 关系-事件卡片字段映射表（data-contract L406-419），按 (relation, 目标类型) 细分，F05 field_vs_triple 判定读取。 |
| `dicts.json` | 词典（标准战争类型、事件类型映射、朝代别名、现代地名映射）。 |
| `governance_report.json` | 治理报告：治理前后对比、孤立节点数、重复名称数、归一映射、data_issues（含处理前/后明细）、待人工项。 |
| `audit/` | 待人工审核项（如名称歧义组、映射建议）。 |
| `inferred_relations.json` | 规则推理边（沿快照一并加载）：与 `relations.json` 同构 + `inferred`/`rule_id`/`rule_name`/`derived_from`/`derived_from_rows` 标记；由 `scripts/build_inferred_relations.py` 用 `data/rules/rule_base.json` 的 20 条规则生成（设计见 [../../docs/RAG_v2.md](../../docs/RAG_v2.md) 第六节）。 |
| `inference_report.json` | 规则推理构建报告：规则文件哈希、逐规则产出与跳过原因、产物 SHA256。 |

## 版本与一致性

- 版本号格式 `YYYYMMDD_vN`，由 `lib/versions.py` 管理。
- 每次治理生成新版本目录；F11 用同一版本号构建索引，保证 `source_version` 一致。
- `source_version` 字段 = 快照版本号。

## 设计决策（重要）

1. **不修改旧代码**：只读 `backend/database`(sqlite) + `entity-event-relation/data/*.txt`。
2. **孤立节点全部保留**，标 `is_isolated: true`，不删除（F09 硬性要求）。
3. **不做静默自动实体合并**：初版对"同名不同事件"（如两个"神农斧隧之战"）保留独立行，
   在报告中列为**名称歧义组**待人工审核；只做低风险别名归一到标准名（词典），
   不修改原始记录、不删除。
4. **战争类型归一**：只合并明显同义/无信息写法（可配置表），"叛乱/镇压叛乱/农民起义"
   等语义不同的类型不合并；无信息兜底类型（战争/其他）保留并在报告中披露分布。
5. **朝代"夏"疑点**：上古传说战争被标为"夏"疑似数据错误；归一逻辑将明显"上古"战争归
   "上古"，其余保留，并在报告中记录该数据质量问题，不静默覆盖。
6. **coordinates**：旧 places 坐标覆盖为 0；保留坐标字段，报告记录覆盖率为 0，供地图降级依据。

## 运行

```bash
python scripts/export_snapshot.py --version 20260904_v1   # 可选 --version，默认取当天 vN
```

## 验收对照

- F09 验收标准：运行时不依赖旧服务 ✓；报告含治理前后对比 ✓；来源/证据/置信度可追溯 ✓；
  孤立节点全部保留 ✓；人工审核记录存在(初版为待人工项清单) ✓；版本号 ✓；
  战争类型词典与事件类型映射已生成 ✓。

## 边界 / 不做什么

- 不做高风险自动补边（relation extraction）——默认关闭，需人工审核后开启。
- 不做实体自动 merge 落库——只在报告中列歧义组。
- 不负责文本切分/向量索引（那是 data/index，F11）。
