# server/fusion（F05）

**归属功能：F05 检索结果融合与重排 + panel 数据唯一装配方（在线）。**

## 职责（文件）

| 文件 | 说明 |
| --- | --- |
| `fusion.py` | 合并图谱+文本证据、按 (subject,relation,object) 去重、按问题类型加权分配名额 + 文本保底、图谱多值组内裁剪、分配 citation_index。 |
| `conflict.py` | 结构化冲突判定：different_object（仅 exact 单值组）+ field_vs_triple（读 relation_card_field_map.json，字段缺失跳过，同字段去重只报一条）。 |
| `panel_builder.py` | 装配 PanelData：entity_cards / subgraph（1 跳）/ timeline（按朝代分组）/ map_points（有坐标地点）。 |

## 设计要点（RAGv2 规划第 5 节 + RAGv1 数据特征）

1. **多值关系 ≠ 冲突**：different_object 只对 exact 单值组（发起方/防守方，来自 field map
   group）生效；"统帅/将领"等多值并列是多个事实行，不判冲突。
2. **persons 缺失跳过**：卡片无字段/为空不判 field_vs_triple（"卡片没写"≠"不一致"）。
3. **panel 唯一装配方**：graph/text 层不拼 subgraph，F07 只消费本模块 panel 输出。
4. 关系问题（relation/event_event）图谱权重 0.7+，背景问题（background）文本权重 0.75，
   未列入类型的按 0.5 兜底；融合后证据上限 `FUSION_LIMIT=18`。

## 输入 / 输出

- 输入：GraphResult + TextResult + QuestionType + 快照（event_cards/field map）
- 输出：FusionOutput（evidence + citation_index + conflicts）+ PanelData

## 边界

- 文本类证据（raw_text/evidence）不参与自动冲突判定（data-contract 约定）。
- 冲突的语义级识别（非结构化）待 F10 真实案例后再做。
