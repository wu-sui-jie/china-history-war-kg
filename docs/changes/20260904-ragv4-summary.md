# RAGv4 变更总结：F10 问答效果评测落地（2026-09-04）

> 后续评审轮（题库审核 + 人工评分 + 可复现性修复）见
> [20260913-ragv4-review-summary.md](20260913-ragv4-review-summary.md)。

- 日期：2026-09-04
- 阶段：RAGv4（F10 问答效果评测，质量闭环）
- 状态：代码 + 题库初版 + 首轮基线跑通完成；**题库人工审核 / 人工评分待续**
- 需求与任务：`docs/features/10-evaluation.md`、`docs/RAG_v1/后续阶段规划.md` 第二节

## 一、新增内容

### 代码

- `evaluation/`（F10 离线评测包）：`bank.py`（题库 schema/读写/校验）、`chain.py`
  （进程内问答链运行器 + 检索配置）、`metrics.py`（覆盖率指标 + verdict 归因）、
  `grading.py`（人工评分模板/读回/统计）、`report.py`（markdown 报告 +
  双通道对比 + 失败清单）、`cli.py`（check-bank/run/report）、`README.md`；
- `scripts/run_evaluation.py`：薄 CLI 封装；
- `scripts/gen_draft_bank.py`：题库草稿生成（数据 grounded，锚点来自快照）；
- `server/text/searcher.py`、`server/text/__init__.py`：`keyword_mode` 可选参数
  （默认 and_or = 原行为，F10 用于拆解 AND/OR 差异）；
- `tests/`（补齐 RAGv2 复核遗留的"测试预留目录"）：`test_bank.py`、
  `test_metrics.py`、`test_keyword_mode.py`、`test_chain_smoke.py`；
  本机 `python -m pytest tests -q` → 24 passed（数据资产缺失用例自动 skip）；
- `scripts/review_bank.py`：人工审核/评分工作流（bank-export/apply、peek、verify、
  scores-export/apply，支持 .xlsx 读取），工作表在 `data/eval/<v>/review/`。

### 数据资产（题库入库；运行产物不入库）

- `data/eval/20260904_v2/questions.jsonl` + `questions.meta.json`：39 条黄金问答
  初版（main 28 / long_rewrite 4 / filter_loss 4 / refusal 3），全部
  `reviewed=False`（draft-0）；
- 人工审核首轮修正（本轮）：`source_ref` 全量改为 `relations#<legacy表名>:<行号>` 可
  回溯写法；E02 要点去掉数据不支持的"两战皆以少胜多"，改为 impact 可锚定表述；
  M11/M12/M13/M14 要点改为关系行可锚定表述；L/F 专项 gold_notes 补事实要点；
  `verify` 事实词级核对 39/39 通过；
- `.gitignore`：新增 `data/eval/*/runs/` 忽略运行痕迹。

### 首轮基线产物（本机，不入库）

- `data/eval/20260904_v2/runs/run_20260913_reviewed/`：meta.json + traces.jsonl（87 条）
  + report.md + scoring_template.jsonl + scores.jsonl（39 条人工评分已合并）。

## 二、文档变更

- 新增 `docs/RAG_v1/RAGv4-开发说明.md`（阶段开发说明 + 首轮结果 + 评分说明）；
- `docs/RAG_v1/README.md`、`docs/README.md`：阶段索引与功能状态同步；
- `docs/features/10-evaluation.md`：状态更新为已完成（RAGv4）；
- `RAG/README.md`：新增 evaluation/、data/eval/ 与 tests/ 状态（RAGv4 已补用例）；
- `scripts/README.md`：补充 run_evaluation.py / gen_draft_bank.py。

## 三、首轮基线要点

| 项目 | 结果 |
| --- | --- |
| 一条命令重复运行 | `run` → report.md + scoring_template.jsonl（39 条）✅ |
| 报告区分失败 | 四类桶（答案错/引用错/检索失败/无证据）✅ |
| 双通道 vs 纯文本 | main 28 题 ok 24/28 双通道与纯文本相当；text-only 回答覆盖均值 66.1% 略高于 dual 61.9%（图谱挤占文本名额，待人工评分校验）◐ |
| 长改写专项 | 4 题 `text-only-and` 全部召回 0 并拒答；OR 兜底可找回 ✅（问题复现） |
| 筛选损耗专项 | F01 开 event_type 筛选：文本命中 30→1 条（raw/evidence 被剔除），top-k 召回 100%→0%；F02–F04 事件卡文本够用、未显性掉覆盖率 |
| 应拒答专项 | 3 题全部未拒答（should_refuse_answered），拒答规则覆盖不足 ✅（新发现） |

新发现（自动归因 + 数据核对）：
- **F02 朝代过滤器子串误判**：单字别名（秦/楚/代/商）命中"秦为什么…/楚军/朝代/
  商朝"等普通词 → 误加 dynasty 筛选 → 图谱+文本双 0 拒答（B01/E01/R05/T03）；
- 拒答文案对"有实体命中但属性/信息不存在"的问题无兜底；
- **关系行 source_row_id 跨 legacy 表重复**（6437 个号重复）：F03 证据 ID
  `graph_{source_row_id}` 同一次检索可能重复；题库 source_ref 已改为
  `relations#<legacy表名>:<行号>` 全限定写法，建议 RAGv5 同步修证据 ID。

## 四、边界与后续

- 答案/引用正确性未人工评分；题库未审核（draft-0）；
- 离线回答器 + 关键词模式跑通；真实 LLM / 向量接入后沿用题库重跑（RAGv5）；
- 优化项（F02 精度、F04 长改写召回、拒答补强）建议按评测结果在 RAGv5 排期。

## 五、回归与验证

- 对拍：eval 驱动 vs `server.sse.run_query`（赤壁之战的主帅是谁？）图/文/融合证据
  数量与引用一致（仅 SSE 回放去换行）；
- `python -m pytest tests -q` → 21 passed；
- `keyword_mode` 默认路径零变化；涉及模块 `py_compile` 通过；
- 建议随 RAGv3 同批或独立提交：`evaluation/`、`scripts/run_evaluation.py`、
  `scripts/gen_draft_bank.py`、`server/text/*` 两处改动、题库 jsonl + meta、
  `.gitignore` 与本文档引用的全部 md。
