# RAGv4 开发说明：F10 问答效果评测（质量闭环）

- 阶段：RAGv4（F10 评测）
- 状态：已完成 ✅（题库 `reviewed-2`（39/39 通过）；答案/引用评分 39/39 已完成；
  审核由 AI 代理执行 + 4 题复评，见 §四之二）
- 完成时间：2026-09-04
- 范围：`docs/README.md` 建议开发顺序第 3 条"质量闭环"——建立可重复运行的评测闭环：
  黄金问答集（题库）、召回标注口径、客观指标与失败归因、双通道 vs 纯文本对比、
  人工评分体系与逐条复核导出。任务定义见 [后续阶段规划.md](后续阶段规划.md)
  "二、RAGv4"。
- 前置：RAGv3 收口（Git 固化）已完成；数据版本 `20260904_v2`；无 LLM key
  （F06 走离线摘要回答器）。
- 需求文档：[F10](../features/10-evaluation.md)；本阶段无独立"规划分析"
  （任务分析已在 [后续阶段规划.md](后续阶段规划.md) 二）。

## 一、本阶段做了什么

新增**离线评测包**（`evaluation/`）与**题库初版**（39 条，全部 `reviewed=False`，
标注版本 `reviewed-2`），把"评测可一条命令重复运行 → 自动出报告 → 导出评分模板 →
人工评分回填 → 报告更新"整条质量闭环打通；并完成首轮基线跑通与错误归因。

### 交付的功能点

1. **题库管理（`evaluation/bank.py`）**
   - 黄金问答集 schema：id/suite/category/question/filters/answerable/
     expected_entities/expected_docs/gold_notes/source_ref/reviewed/reviewer/
     annotation_version；JSONL + meta 读写、结构校验；
   - 四个套件：`main`（主基线）、`long_rewrite`（长改写问题专项）、
     `filter_loss`（事件类型筛选原文损耗专项）、`refusal`（应拒答专项）；
   - 题库初版生成脚本 `scripts/gen_draft_bank.py`：从快照数据（事件卡/实体/
     关系）选锚点出题，gold_notes 与 source_ref 全部数据可溯源（不凭空编造）；
     生成后以 `data/eval/<version>/questions.jsonl` 为唯一维护入口，人工审核直接
     编辑该文件（`scripts/gen_draft_bank.py` 只负责首次草稿）。
2. **评测运行器（`evaluation/chain.py`）**
   - 在进程内按检索配置复跑 RAGv2 问答链，事件顺序与 `server/sse.py`
     非缓存路径一致（同一批底层函数、同拒答硬规则）；差异仅两处：
     绕过回答缓存 + 通道/关键词模式可配置；
   - 检索配置：`dual`（生产口径双通道）、`text-only`（单文本通道）、
     `text-only-and` / `text-only-or`（关键词纯 AND/纯 OR，供专项拆解）；
   - `filter_loss` 套件自动展开 filters-on / filters-off 两个 variant，
     量化开筛选前后的召回损耗；
   - 输出逐条结构化 trace：F02 实体/改写、图谱证据、文本证据（含全文）、
     融合后证据、引用、拒答原因、回答、分阶段耗时与客观指标。
3. **客观指标与归因（`evaluation/metrics.py`）**
   - 基于题库 `expected_entities` 标注词的覆盖率：实体命中 / 图谱命中 /
     文本 top-k 召回 / 融合携带（文本）/ 引用携带（文本）/ 回答覆盖；
   - 约定：拒答记录的回答覆盖一律记 0（拒答文案只是机械复述问句）；
   - 自动归因 verdict：ok / retrieval_fail（检索失败）/ fusion_cut（相关文本被
     排序或裁剪挤出）/ answer_miss（证据携带但回答未引用）/ refused / correct_refusal /
     should_refuse_answered（应拒答却作答）。
4. **报告（`evaluation/report.py`）**
   - 主套件客观指标表（按配置）+ 双通道 vs 纯文本逐题对照 + 对比结论；
   - 专项套件逐题逐配置逐 variant 展开；自动失败样例分四类列出（F10 验收第 2 条）；
   - 人工评分回填后合并：评分分布表、失败清单、应拒答题核验表（F10 验收第 3、4 条）。
5. **人工评分（`evaluation/grading.py`）**
   - 评分模板导出（默认 `dual` 配置、filter_loss 取 filters-on 主形态）；
     每行含问题、系统回答、引用明细、gold_notes、评分栏位；
   - 取值与规则见本文档附录一（与 F10 功能文档一致）。
6. **命令行（`evaluation/cli.py` + `scripts/run_evaluation.py`）**
   - `check-bank`（题库结构校验 + 标注词词典命中提示）、
     `run`（复跑 + 报告 + 模板）、`report`（人工评分合并更新，默认最近一次 run）；
   - 默认强制离线回答器保证可复现；`--llm` 放开已配置模型（回答变随机，仅探测用）。
7. **后端小改动（`server/text/searcher.py`、`server/text/__init__.py`）**
   - `TextSearcher.search_keyword()` 与 `text.search()` 增加可选 `keyword_mode`
     （and_or 默认 = 原生产行为；and/or 供评测拆解 AND 失效面），默认路径零变化。
8. **自动化测试（`tests/`，补齐 RAGv2 复核遗留的预留目录）**
   - `test_bank.py`（题库结构/套件分布/往返读写）、`test_metrics.py`（覆盖指标与
     verdict 归因，含"拒答时回答覆盖记 0"）、`test_keyword_mode.py`（keyword_mode
     回归 + 长改写 AND 失效证据）、`test_chain_smoke.py`（eval 驱动与
     `server.sse.run_query` 对拍、text-only 通道开关）；
   - 数据资产缺失的用例自动 skip；本机全量 `python -m pytest tests -q` → 50 passed
     （2026-09-13 增补：`test_query_determinism.py` 跨进程确定性、`test_dynasty_filter.py`
     朝代识别精确性、`test_evidence_ids.py` 证据 ID 唯一性）。
9. **人工审核/评分工作流工具（`scripts/review_bank.py`）**
   - `bank-export` 导出 Excel 友好的审核表（含只读列 `data_digest` 与自动核对结论）；
     `bank-apply` 回填（空单元格不覆盖、`reviewed=通过` 才置审核通过、支持 .xlsx）；
   - `peek --id <题号>`：打印 source_ref 指向的完整数据（关系行含完整 evidence）
     与索引中含标注词的原文片段，供逐条深查；
   - `verify`：**事实词级**自动核对（年份/数字 + 词典可识别的实体词），核对范围 =
     source_ref 指向（实体行全文/关系行）+ 标注词命中实体行及其**全部关系行** +
     索引片段；输出 `bank_verify.csv`（通过/存疑 + 缺口清单），并合并进审核表；
   - `scores-export` / `scores-apply`：答案/引用人工评分表的导出与回填。
   - 工具口径见附录二；本轮据此把全部 39 条核对为"事实词齐"，并修正了草稿中
     数据不支持或未锚定的表述（见第四节）。
10. **产物目录约定**
   - 题库（人工维护资产）入库：`data/eval/<version>/questions.jsonl` + `questions.meta.json`；
   - 运行痕迹/报告/评分中间件：`data/eval/<version>/runs/`（.gitignore 已忽略）；
   - 人工审核工作表：`data/eval/<version>/review/`（中间态，结论回填题库后即完成使命）。

## 二、功能 ↔ 文件/文件夹映射

| 交付 | 目录/文件 | 说明 |
| --- | --- | --- |
| 题库初版 | `data/eval/20260904_v2/questions.jsonl`、`questions.meta.json` | 39 条、4 套件、reviewed-2（39/39 通过） |
| 题库生成 | `scripts/gen_draft_bank.py` | 数据 grounded 草稿生成 |
| 题库 schema | `evaluation/bank.py` | GoldQuestion/QuestionBank/校验 |
| 运行器 | `evaluation/chain.py` | EvalConfig + run_question/run_batch |
| 指标与归因 | `evaluation/metrics.py` | 覆盖率 + verdict 分类 |
| 报告 | `evaluation/report.py` | markdown 报告 + 对比结论 + 失败清单 |
| 评分 | `evaluation/grading.py` | 模板导出/读回/统计 |
| CLI | `evaluation/cli.py`、`scripts/run_evaluation.py` | check-bank/run/report |
| 包说明 | `evaluation/README.md` | 模块与用法 |
| F04 参数化 | `server/text/searcher.py`、`server/text/__init__.py` | keyword_mode（默认不变） |
| 自动化测试 | `tests/`（bank / metrics / keyword_mode / chain_smoke / query_determinism / dynasty_filter / evidence_ids / f02_regression） | pytest 50 用例（数据缺失自动 skip） |
| 人工审核工具 | `scripts/review_bank.py` | 审核/评分表导出回填、peek、verify（附录二） |

## 三、首轮基线跑通结果（**历史存档**：run_20260913_reviewed，annotation_version=reviewed-1）

> 本节记录 RAGv4 首轮跑通（含 F02/F03 修复前后过程）的历史证据；**当前正式基线**见
> §四之二与 [RAGv4-阶段工作总结.md](RAGv4-阶段工作总结.md) §5（`run_20260913_postaudit`，
> annotation_version=reviewed-2，评分 0 correct/23 partial/16 incorrect）。
> 下列 §三.3 的指标表是**修复前**数字（ok 24/28），保留用于对比"指标提升来自哪些修复"。

环境：Python 3.11（AI_Agent），无 LLM key（离线回答器），数据版本 20260904_v2，
run_id `run_20260913_reviewed`（产物在
`data/eval/20260904_v2/runs/`，命令见该目录 `meta.json`）。

### 1. 一条命令可重复运行（F10 验收第 1 条）

```bash
python scripts/run_evaluation.py check-bank   # 结构校验 39 条通过
python scripts/run_evaluation.py run          # 87 条评测记录（题目×配置×variant）
```

`run` 结束即产出：`report.md`（自动报告）+ `scoring_template.jsonl`（39 条评分模板）；
重复执行结果确定（离线回答器确定性 + 绕过缓存）。

### 2. 报告区分失败类别（F10 验收第 2 条，自动归因 + 人工评分合并）

- 自动归因把候选失败圈成四类桶（检索失败 / 无证据 / 引用错误候选 / 答案错误候选），
  见报告"自动归因失败样例"；
- 人工评分回填后，报告按 correct/incorrect 等给出最终"人工判定失败清单"
  （含答案错与引用错），并单列应拒答题核验表。

### 3. 双通道 vs 纯文本（F10 验收第 3 条，自动口径）

主套件 28 题（默认 variant）：

| 配置 | 实体命中 | 图谱命中 | 文本top-k召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dual | 42.6% | 75.3% | 83.3% | 78.6% | 57.1% | 59.2% | 24/28 |
| text-only | 42.6% | - | 83.3% | 83.3% | 58.3% | 63.4% | 24/28 |

- 逐题对照见报告（`## 双通道 vs 纯文本`），本 run 无"纯文本独能覆盖而双通道不能"
  的题目；但 text-only 的回答覆盖均值略高于 dual（R02/R03/R04/M04 等题
  text-only 更高）——图谱证据在融合阶段挤占了文本名额（fusion_cut 类候选），
  是否值得需人工对引用价值评分后再定（已在报告/下一步留作 F05 调参依据）。

### 4. 专项口径（对齐 RAGv2 复核遗留问题）

- **长改写问题（long_rewrite 套件）**：4 题在 `text-only-and` 下文本 top-k 召回
  全部 0 且被拒答（verdict=refused）；`dual`/`text-only-or` 均能找回相关文本。
  证明 RAGv2 复核结论成立：**AND 优先几乎必然失败，OR 兜底承载全部召回**。
- **事件类型筛选损耗（filter_loss 套件）**：开 `event_type` 筛选后 raw/evidence
  片段整批剔除的现象被量化。例：F01（长平之战的交战过程，筛选"战略要地守卫"）
  文本 top-k 召回从 filters-off 100% 掉到 filters-on 0%，命中条数从 30 条（含
  raw/event_card）降到 1 条（仅 event_card）；F02/F03/F04 覆盖率未掉是因为事件卡
  片文本本身够用——**原文（raw/evidence）损耗被确认，影响程度因题而异**。
- **应拒答（refusal 套件）**：3 题系统全部未拒答（verdict=should_refuse_answered），
  说明当前拒答规则只覆盖"无证据/无共享词"两类，**对"有实体命中但问题属性不成立"
  的越界问题无拒答能力**，建议下一步补充（见五）。

### 5. 与生产链路一致性

- 对拍验证：同一问题（"赤壁之战的主帅是谁？"）用 eval 驱动与 `server.sse.run_query`
  分别跑，graph/text/fusion 证据数量与引用逐条一致（差异仅 SSE 回放去掉换行）。
- `run_server` 冒烟回归不受影响（`keyword_mode` 默认参数不变）。

## 四、题库结构与质量说明（reviewed-2 终态；诚实说明）

- 39 条：main 28 + long_rewrite 4 + filter_loss 4 + refusal 3；类别覆盖
  single_entity 14 / relation 6 / event_event 1 / comparison 1 / timeline 3 /
  background 11 / other 3。
- **全部 `reviewed=False`**：出题内容与 gold_notes 均来自快照数据（事件卡字段/实体
  行/关系行），但"是否可用当前链路稳定作答、标注词是否合理、黄金要点是否准确完整"
  仍需人工逐条审核；评分说明见附录一。审核中可直接修改 jsonl 并重新 `run`。
- **本轮题库修正（人工审核首轮反馈驱动）**：
  1. `source_ref` 全量改为可回溯写法（关系行必须 `relations#<legacy表名>:<行号>`），
     并把事件行缺失的 `action` 等字段补进 `data_digest`、放宽截断长度——此前提炼摘要
     截断导致审核人误判"数据里没有"（如 廉颇/周瑜/破釜沉舟/荆州 实际都存在）；
  2. `verify` 事实词级核对（39/39 通过），修正了下列未锚定/数据不支持的表述：
     E02"两战皆以少胜多"→改为"曹操均为一方并深刻改变格局（官渡以少胜多奠定统一北方、
     赤壁奠定三国鼎立）"；M11 补 垓下之战（relation 977/1023）；M12/M13/M14 要点改为
     关系行可锚定的表述；L/F 专项的 gold_notes 由"纯口径说明"补为"要点+口径"。
- 首轮已暴露的真实系统问题（也是题库价值所在）：
  1. **F02 朝代筛选机制缺陷（两轮修复，2026-09-13 终态）**：问句朝代识别原为
     "按字典别名子串匹配 + 当作硬过滤"。第一轮修精度（只留长度≥2、不与实体 mention
     重叠的术语），第二轮按审核报告改**语义**：自动识别的朝代写入
     `F02Output.dynasty_bias`，F03/F04 仅将其用于**排序前置（软偏置）**，不再剔除
     结果；显式 F01 筛选保持硬过滤。原因是硬过滤会把"被问到的朝代"连同事件本身剔除
     （鸣条之战属夏，问"与商朝的关系"即被清空）；审核实测即使给词典补 `商朝→商`
     别名，硬过滤也会把 E01 再次打回拒答，故必须改语义而非补词典。
     回归守护：`tests/test_f02_regression.py`（四题不拒答 + 补别名后不拒答）、
     `tests/test_dynasty_filter.py`；B01/E01/R05/T03 复测通过（题库 reviewed-2）；
  2. **F04 AND/OR 差距**：见专项一，长改写问题的相关文本召回完全依赖 OR 兜底；
  3. **拒答缺口**：见专项三，实体命中 + 无关属性的问题无法被拒答；
  4. **关系行 source_row_id 跨 legacy 表重复**：`relations.json` 17,700 行里
     `source_row_id` 只在各表（event_person_relations / event_place_relations /
     event_event_relations / event_organization_rel）内唯一，实测 6437 个号跨表重复；
     而 F03 证据 ID 为 `graph_{source_row_id}`（`server/graph/search.py`），同一次检索
     若同时命中不同表的同号行，evidence_id 会重复（违背"证据 ID 唯一"口径）；
     题库 source_ref 因此规定必须写成 `relations#<legacy表名>:<行号>`。
     **已于 2026-09-13 修复**：证据 ID 改为 `graph_{legacy表名}_{行号}`（含哈希回退），
     `data-contract` 已同步格式说明，`tests/test_evidence_ids.py` 验证全量行 ID 唯一。
  5. **跨进程非确定性（已修复）**：`dictionary_matcher` 的实体名排序
     `sorted(set(...), key=len, reverse=True)` 在等长词上按 set 迭代顺序，受
     `PYTHONHASHSEED` 影响 → 两次 run 的 `R02`/`E02` 证据顺序与回答顺序不一致，
     破坏"评测可重复运行"验收；已改为并列按词本身排序（最长优先不变），
     同类修复 `classifier.extract_dynasty_filter`；回归测试
     `tests/test_query_determinism.py`，两次独立进程 run 内容级一致。
     该修复触及 F02 两处代码，超出原 RAGv4 范围但为验收必需，已记录于此。

### 四之二、首轮审核与评分结果（2026-09-13）

- **题库审核**：委托 AI 代理按 [REVIEW_GUIDE.md](../../data/eval/20260904_v2/review/REVIEW_GUIDE.md)
  执行，首轮 **35 条通过 / 4 条保留**（B01、E01、R05、T03 —— 题目与数据成立，拒答源于
  F02 误判，保留作缺陷复现样本）；审核过程未改动题目内容，仅回填 `reviewed`；
  期间采纳建议为 `T01` 补标注词 `前260年`、`B03` 补 `急于求成`。
  **随后完成 F02/F03 修复并复测，4 题转为通过 → 题库 `reviewed-2`（39/39 全部通过）。**
- **答案/引用评分（39/39，最终版）**：答案 0 correct / 23 partial / 16 incorrect；
  引用 28 supported / 3 unrelated / 8 unsupported（修复前为 25/3/11）。**仍无一条
  correct**，根因是本次 run 用离线摘要回答器（输出为关系清单+截断），不能作为最终
  问答质量结论；证据侧不差：28 条引用可支持结论，多数题属"检索到位、回答未用"。
  4 题复评结论：B01 partial/supported、E01 incorrect/supported、R05 incorrect/supported、
  T03 incorrect/unsupported（复评口径沿用委托审核报告 §4.1）。
  **复评出处**：4 题的原始记录为 `run_20260913_f2fixed/scores.jsonl` 的评分列 +
  `notes`（`reviewer=ZCode 复评（F02 修复后）`），没有单独的复评工作表；如需正式
  口径请人工复核这 4 条。评分对象 run（`run_20260913_143352`）的痕迹已删除，
  其评分依据以归档的 `review/scores_review_filled.csv` 为准。
- 审核报告：`data/eval/20260904_v2/review/review_report.md`；本轮变更总结见
  [../changes/20260913-ragv4-review-summary.md](../changes/20260913-ragv4-review-summary.md)。
- 正式基线 run：`run_20260913_postaudit`（第三方审核整改后重跑 + 评分合并）；
  历史基线 `run_20260913_f2fixed`（F02/F03 修复版）、`run_20260913_reviewed`
  （确定性修复版）保留对照。审核整改后 B03/L03 两题输出因"硬过滤→软偏置"变化，
  已按同口径复评（B03 incorrect→partial）；其余 37 条评分沿用。
- **复审轮整改（R1–R7，2026-09-13 同日）**：审核报告文末"整改验收"确认 T1–T8 通过，
  另提 R1–R7，均已处理：R1 采用方案②（文档如实写明文本侧偏置不生效：F04 返回序会被
  F05 按相关分重排；图谱侧生效）；R2 §三 改标历史基线；R3 失败桶补截断提示；
  R4 朝代后缀补"代"（唐代/宋代/清代，误命中风险已复核）；R5 删除易误用的旧函数名；
  R6 题库条目级 `annotation_version` 同步为 reviewed-2（`bank-apply` 今后两者同改）；
  R7 证据 ID 哈希回退断言加强 + 小样本口径说明。R1 方案①（偏置折进 `_score`）未采用，
  列为可选优化（会扰动融合排序与基线）。
- **收口轮残余项（A1–A6，同日）**：A1 文本层 docstring 补"返回序会被 F05 重排覆盖"；
  A2 阶段总结 5 处旧基线/旧函数名改齐；A3 补"代"后缀正反例测试；A4 偏置跨度去重
  （"清朝末年"→`['清朝']`）；A5 审核附属 6 文件归档到 `review/audit/`；A6 `test_bank`
  注释与恒真断言修正。处理后 `pytest` 全绿，与基线 87 条 0 差异。
- **第四轮（B1–B5，同日）**：B1 根 README 用例数补齐；B2 `EvalConfig` 增 `mode`
  （keyword/vector/hybrid）并透传 `text.search`（v5 向量/hybrid 对照的前置），并补
  `test_text_mode_passthrough_and_autodowngrade`（无向量自动降级关键词）；
  B3/B4/B5 为 v5 规划说明的事实性与命名修正（incorrect 全集 16 条、对照配置名、
  `--no-embeddings` 参数、14 条出处、四轮表述、audit_decisions 契约措辞）。
- **第五轮（C1–C3 / R1–R2 / C1'，同日）**：B1–B5 验收通过；v4 唯一残留 C1' 已修
  （`understand.py` docstring 由"已调用 deepseek 兜底"改为"**预留，尚未实现**"，
  `server/query/README.md` 同步）；C1（F02 兜底需新实现）、C2（生产侧文本模式接线：
  settings/sse/runtime）、C3（缓存键纳入 mode）、R1（hybrid 分数量纲与融合口径）、
  R2（LLM 评测随机性需多次取样）已并入 [RAGv5-规划说明.md](RAGv5-规划说明.md)。
- **边界**：本次审核（含 4 题复评）为 AI 代理执行（`reviewer` 已如实标注），
  非自然人审核；如需正式口径，建议对审核报告 §5.2 的 6 项存疑由人复核。

## 五、边界与未做事项

1. **评测只覆盖离线回答器 + 关键词通道**：无 LLM key、向量索引占位——向量模式、
   真实模型效果不在本 run 范围（RAGv5 接入后沿用同一题库重跑即可对比）；
   人工评分已按离线回答器形态完成（0 correct/23 partial/16 incorrect），
   语义质量结论待 LLM 接入后复评。
2. **题库审核已完成但非自然人**：`reviewed-2`（39/39 通过；4 题在 F02 修复后复测通过）；
   4 条未通过项与审核报告 §5.2 的 6 项存疑需人工最终确认；expected_docs 仍有留空、
   部分标注词（夏桀/208年/三国鼎立/秦朝）词典未命中（check-bank 有提示，属文本层标注）。
3. **F02/F03 已修复并复测**（详见第四节与
   [../changes/20260913-ragv4-review-summary.md](../changes/20260913-ragv4-review-summary.md)）；
   朝代软偏置目前**只有图谱侧实际生效**，文本侧仅是检索返回序、会被 F05 相关分重排覆盖
   （如实说明；如需文本侧生效见审核 R1 方案①）。
4. **未对在线服务做其它结构性改动**：除 F04 keyword_mode 可选参数、F02 朝代识别
   精确化、F03 证据 ID 带表名、以及确定性排序修复外无改动。
5. **耗时指标未进报告正文**：trace 存有分阶段耗时（stage_ms），报告未做汇总展示
   （避免小样本误导），需要时可从 traces.jsonl 二次统计。
6. **多审核人一致性**：评分说明已定（附录一）；"两评委背靠背 + 不一致仲裁"流程
   未落地，需在实际评分时约定。
7. **自动核对 ≠ 人工审核**：`verify` 只能证明"要点与标注词在数据范围内有支撑"，
   不能替审核人判断题目价值、口径取舍与要点表述是否恰当（F10 要求人工审核）。

## 六、如何复现

```bash
# 环境：Python 3.11（本机 E:/anaconda/envs/AI_Agent），数据 20260904_v2
cd RAG

# 1) 题库校验（结构 + 标注词词典命中提示 + source_ref 可回溯性）
python scripts/run_evaluation.py check-bank

# 2) 全量评测（39 题 × 配置 × variant，离线回答器；产物含报告+评分模板）
python scripts/run_evaluation.py run
#    只跑主套件：  python scripts/run_evaluation.py run --suites main

# 3) 人工评分后回填（编辑评分表后另存，执行下面命令）
python scripts/run_evaluation.py report --run data/eval/20260904_v2/runs/<run_id> \
    --scores <评分文件.jsonl>

# 人工审核工作流（附录二）
python scripts/review_bank.py verify                       # 事实词级自动核对 → bank_verify.csv
python scripts/review_bank.py bank-export --out data/eval/20260904_v2/review/bank_review.csv
python scripts/review_bank.py peek --id R02                # 逐题查看完整数据与原文片段
python scripts/review_bank.py bank-apply --sheet <审核后的表> --reviewer <姓名>
python scripts/review_bank.py scores-export --run <run目录> --out <评分表.csv>
python scripts/review_bank.py scores-apply --run <run目录> --sheet <评分表.csv> --reviewer <姓名>

# 题库重新生成草稿（一般不需要；维护走 questions.jsonl 人工编辑）
python scripts/gen_draft_bank.py --version 20260904_v2
```

评测记录读取：`data/eval/<version>/runs/<run_id>/traces.jsonl` 每行一个完整现场
（question_id/config/variant/understand/graph/text/fused/answer/citations/auto），
失败归因与二次统计可直接基于该文件。

## 七、下一阶段（RAGv5 相关铺垫）

1. **题库**：审核已完成（reviewed-2，39/39 通过）；首批通过题目
   可供 F08 示例选型（注意剔除 4 条保留项与评分 `incorrect` 的题）；如需正式"人工
   审核"口径，请人工复核审核报告 §5.2 的 6 项存疑。
2. **F04 长改写召回策略**（AND 失效兜底）、**拒答缺口补充**（应拒答却作答 3 题）、
   文本检索对"关系型问句"的召回（E01 文本仅 1 条）等优化项，依评测结果排期；
   F02/F03 已在评审轮修复完毕。
3. 真实 LLM / 向量索引接入后，用同一题库重跑形成跨配置效果对比（RAGv5）；
   重点复测审核报告 §4.3(4) 的 14 条"检索到位、回答未用"题——这批题是判断
   RAG 真实水平的关键样本（离线摘要形态下无法体现语义作答能力）。

## 附录一：人工评分说明

答案正确性：

| 评分 | 含义 |
| --- | --- |
| correct | 答案正确且依据充分 |
| partial | 答案方向正确但信息不完整/有次要瑕疵 |
| incorrect | 答案错误 |
| unknown_answer | 应拒答时正确拒答（题库 answerable=False 且系统拒答） |

引用正确性：

| 评分 | 含义 |
| --- | --- |
| supported | 引用可验证且支持答案 |
| unrelated | 引用存在但与结论无关 |
| unsupported | 关键结论没有引用支持 |

每条评分记录含：问题、系统答案、引用明细、gold_notes、评分、备注；
导出模板字段见 `scoring_template.jsonl`。评分前建议先就 gold_notes 口径达成一致；
答案文本过长时可先在模板中只读 `system_answer` + `citations_text` 两列复核。

## 附录二：人工审核工作流与自动核对口径

> 交给第三方模型/他人代审的完整任务书：`data/eval/<version>/review/REVIEW_GUIDE.md`
> （自包含，只涉及 `bank_review.csv` 与 `scores_review_*.csv` 两个文件；含产出格式、
> 判断规则、存疑处理与自检清单）。

分两轮，互不阻塞：

1. **题库审核**（`bank_review.csv`，一行一题）
   - 看 `question/category`：问句是否自然无歧义、分类是否贴切；
   - 看 `data_digest`（source_ref 数据切片，截断版）与 `auto_verify`（自动核对结论）：
     核对 `expected_entities`（答对时回答/引用里必须出现的实体词）与
     `gold_notes`（参考要点）是否有数据支撑；
   - 拿不准的行用 `peek --id <题号>` 看完整数据与检索原文；
   - 结论填最后一列 `reviewed=通过`（留空=存疑/待定），回填 `bank-apply`。
   - `source_ref` 一般只读；发现指向错行可直接改（回填支持覆盖）。
2. **答案/引用人工评分**（`scores_*_<run_id>.csv`，一行一条系统回答）
   - 对照 `gold_notes` 与 `expected_entities`，填 `answer_correctness` /
     `citation_correctness` / `notes` / `reviewer`（取值见附录一）；
   - 回填 `scores-apply`，再用 `report --scores` 合并进评测报告。

自动核对（`verify`）口径与边界：

- **事实词级**而非整句匹配：gold_notes 是改写摘要，逐字匹配必然失败；只抽取
  年份/数字（如"前260年""208年"）与词典可识别的实体词作为核对项；
- **核对范围**：source_ref 指向的实体行全文/关系行 + 标注词命中实体行及其全部关系行
  + 索引中含标注词的检索片段（跨书、跨片段）；
- **结论含义**：通过 = "事实词都能在数据范围内找到支撑"，不等于"题目已人工审核通过"；
  表述性文字（如"重要将领"）不作事实核对，由审核人判断；
- 已知局限：检索片段范围较宽，可能把"别处出现过该词"当成支撑；对动词性细节
  （如"离间""以少胜多"）需审核人结合 `peek` 的完整原文确认。
