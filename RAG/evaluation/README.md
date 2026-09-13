# evaluation（F10 问答效果评测，RAGv4）

**归属功能：F10 问答效果评测（质量闭环，离线运行）。**

在进程内按检索配置复跑 RAGv2 问答链（F02→F03/F04→F05→F06），对黄金问答集逐条
产出结构化 trace 与客观指标，生成可人工评分的模板与 markdown 报告。评测只自动化
客观指标（召回/命中/耗时/失败归因）；答案正确性与引用正确性以**人工评分**为准。

## 模块

| 模块 | 职责 |
| --- | --- |
| `bank.py` | 题库（黄金问答集）schema、JSONL 读写、结构校验（含人工审核字段） |
| `chain.py` | 进程内问答链运行器 + 检索配置（双通道/单文本/关键词 AND/OR；`EvalConfig.mode` 支持 keyword/vector/hybrid，无向量时自动降级关键词），与 `server/sse.py` 同口径 |
| `metrics.py` | 客观指标：实体命中、图谱命中、文本 top-k 召回、融合携带、引用携带、回答覆盖与四类失败归因 |
| `grading.py` | 人工评分模板导出/读回（答案正确性 + 引用正确性，逐条复核） |
| `report.py` | markdown 报告：指标统计、双通道 vs 纯文本对比、失败样例、人工评分合并 |
| `cli.py` | 命令行入口（check-bank / run / report） |

## 用法（从 RAG/ 根目录）

```bash
# 1) 校验题库结构（含标注词在词典中的命中提示）
python scripts/run_evaluation.py check-bank

# 2) 一次命令复跑问答链并产出报告 + 评分模板
#    默认跑全部套件（main / long_rewrite / filter_loss / refusal）
python scripts/run_evaluation.py run
#    只跑主套件
python scripts/run_evaluation.py run --suites main

# 3) 人工评分后合并进报告
python scripts/run_evaluation.py report --run data/eval/20260904_v2/runs/<run_id> \
    --scores data/eval/20260904_v2/runs/<run_id>/scores.jsonl
#    不带 --run 时默认汇总最近一次 run
python scripts/run_evaluation.py report --scores <scores.jsonl>
```

默认强制关闭真实 LLM（无 key 也保证离线摘要回答器确定性可复现）；`run --llm` 可
放开已配置的 LLM（回答变为随机，仅作效果探测用）。

## 运行产物

- `run` 生成 `data/eval/<version>/runs/<run_id>/`：`meta.json`（版本/配置/命令）、
  `traces.jsonl`（每行 = 题目 × 配置 × variant 的完整现场）、`report.md`（自动报告）、
  `scoring_template.jsonl`（人工评分模板）；
- 题库（人工维护资产）在 `data/eval/<version>/questions.jsonl` 与其
  `questions.meta.json`，由 `scripts/gen_draft_bank.py` 从快照数据生成草稿，
  之后直接编辑 jsonl 审核；运行产物不入库（.gitignore 已忽略 `data/eval/*/runs/`）。

## 约定与口径

1. 覆盖统计基于题库 `expected_entities` 标注词（可短于实体标准名）；拒答时"回答覆盖"
   一律记 0（拒答文案只是机械复述问句）。
2. 评测驱动绕过回答缓存（缓存键不区分检索通道，跨配置共享缓存会污染对比结论）；
   与生产链路的差异：绕过缓存 + 通道/取词/名额可配（`graph_top_k` 默认取
   `settings.query_top_k_graph`，与生产同源；`text_top_k`/`fusion_limit` 显式参数化）。
3. 人工评分取值见 `grading.py`：答案 correct/partial/incorrect/unknown_answer；
   引用 supported/unrelated/unsupported。
4. 问句自动识别到的朝代是**软偏置**（`F02Output.dynasty_bias`，不剔除结果）；
   只有显式 filters（F01 下拉）才是硬过滤。偏置在 F03 图谱侧生效（策略前重排节点，
   可经 top_k 影响证据集合），文本侧仅为 F04 返回序、会被 F05 相关分重排覆盖
   （如实说明，见 `docs/features/02-entity-linking.md`）。
5. 题库审核状态见 `annotation_version`（当前 `reviewed-2`：39/39 通过）。首轮
   委托审核后 4 条（B01/E01/R05/T03）曾保留作 F02 缺陷复现样本，F02 修复后
   复测通过；委托审核任务书见 `data/eval/<v>/review/REVIEW_GUIDE.md`。
