# RAGv4（F10 问答效果评测）阶段工作总结（送审版）

- 阶段：RAGv4 = F10 问答效果评测（质量闭环）
- 状态：已完成（含首轮评审、系统修复与复测；待办仅剩 RAGv5 相关项，见第九节）
- 日期：2026-09-04 首版交付；2026-09-13 评审轮收口
- 数据版本：`20260904_v2`（快照 + 索引）；题库标注版本：`reviewed-2`
- 本文档用途：**自包含总结，可直接交给第三方模型做代码/数据/结论审查**；
  第九节给出可执行的审核清单

---

## 一、阶段目标与口径

按 `docs/README.md` 的"建议开发顺序"第 3 条与 `docs/RAG_v1/后续阶段规划.md` 第二节，
RAGv4 的目标是：建立**可重复运行的评测闭环**，持续验证检索与回答效果，输出量化证据与
错误归因，支撑"图谱+文本双通道 vs 纯文本 RAG"的对比结论。

四条验收与达成方式：

| F10 验收标准 | 达成方式 | 当前状态 |
| --- | --- | --- |
| 1. 评测可一条命令重复运行 | `python scripts/run_evaluation.py run` 复跑问答链 → run 目录 + 报告 + 评分模板 | ✅ 且已验证两次独立进程内容级一致 |
| 2. 报告区分答案错/引用错/检索失败/无证据 | 自动归因 verdict（4 类桶）+ 人工评分合并后的失败清单 | ✅ |
| 3. 输出双通道 vs 纯文本对比结论 | 报告"逐题对照表 + 对比结论"，配置含 dual / text-only / text-only-and / text-only-or | ✅ |
| 4. 人工评分可导出并逐条复核 | 评分模板导出（CSV/xlsx）→ 回填 → 报告合并 | ✅（首轮 39/39 已评，由 AI 代理执行，见第七节） |

评测口径（重要，审查时先看这里）：

- 评测**不评测模型自身知识**，只评测"能否依据知识库（两本书的治理数据）作答"；
- 答案/引用正确性是**人工评分**项；脚本只自动化客观指标（召回/命中/覆盖/耗时）与归因；
- `gold_notes` 是改写摘要而非原文引用，事实核对按**关键事实词**（年份/数字/实体名）进行；
- 拒答行的"回答覆盖"一律记 0（拒答文案只是机械复述问句，不能算覆盖）。

## 二、交付物清单

### 2.1 新增代码（`RAG/` 相对路径）

| 文件 | 职责 |
| --- | --- |
| `evaluation/bank.py` | 题库 schema（GoldQuestion/QuestionBank）、JSONL 读写、结构校验、审核字段（reviewed/reviewer/annotation_version） |
| `evaluation/chain.py` | 进程内问答链运行器：按 EvalConfig 复跑 F02→F03/F04→F05→F06，绕过回答缓存，输出结构化 trace |
| `evaluation/metrics.py` | 客观指标（实体命中/图谱命中/文本top-k/融合携带/引用携带/回答覆盖）与 verdict 四类归因 |
| `evaluation/report.py` | markdown 报告：指标表、双通道逐题对照、专项展开、失败样例、人工评分合并 |
| `evaluation/grading.py` | 人工评分模板导出/读回/统计（取值枚举校验） |
| `evaluation/cli.py` | 子命令 check-bank / run / report |
| `evaluation/README.md` | 包说明 |
| `scripts/run_evaluation.py` | 薄 CLI 封装（sys.path 对齐） |
| `scripts/gen_draft_bank.py` | 题库草稿生成（数据 grounded；**已加"存在即拒绝覆盖"保护**） |
| `scripts/review_bank.py` | 人工审核/评分工作流：bank-export/apply、peek、verify、scores-export/apply（支持 .csv/.xlsx） |
| `tests/`（8 个文件，50 用例） | bank / metrics / keyword_mode / chain_smoke / query_determinism / dynasty_filter / evidence_ids / f02_regression |

### 2.2 在线服务改动（6 个文件，均为最小改动）

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `server/text/searcher.py`、`server/text/__init__.py` | 新增可选 `keyword_mode`（默认 `and_or` = 原行为） | 评测需要拆解 AND/OR 差异 |
| `server/query/dictionary_matcher.py` | 实体名排序并列确定性（`key=lambda w: (-len(w), w)`）；新增 `dynasty_alias_map` | 修复跨进程非确定性 |
| `server/query/classifier.py` | 问句朝代识别收敛为 `extract_dynasty_mentions`（软偏置）：多字别名 + 单字朝代 + 朝/国/代 后缀，不与实体 mention 重叠，并列确定性 | 修复朝代别名子串误判；旧名 `extract_dynasty_filter` 已删除（审核 R5） |
| `server/query/understand.py` | 传别名映射与实体 mention 给上面的函数 | 同上 |
| `server/graph/search.py` | 证据 ID 由 `graph_{行号}` 改为 `graph_{legacy表名}_{行号}` | 修复跨表行号重复导致 ID 冲突 |

### 2.3 数据资产

| 路径 | 内容 | 是否入库 |
| --- | --- | --- |
| `data/eval/20260904_v2/questions.jsonl` + `questions.meta.json` | 题库 39 条（main 28 / long_rewrite 4 / filter_loss 4 / refusal 3），`annotation_version=reviewed-2`，39/39 已审核 | 入库 |
| `data/eval/20260904_v2/review/REVIEW_GUIDE.md` | 委托审核任务书（自包含） | 入库 |
| `data/eval/20260904_v2/review/review_report.md` | 受托方审核报告（含方法、逐行结论、存疑清单） | 入库 |
| `data/eval/20260904_v2/review/bank_review.csv` | 首轮审核输入表（原始空白表，已归档到 `logs/review-archive/`，审核结论以 filled 表为准） | 归档 |
| `data/eval/20260904_v2/review/bank_review_filled.csv` | 受托方回填的题库审核表（35 通过 / 4 保留） | 入库 |
| `data/eval/20260904_v2/review/scores_review_filled.csv` | 受托方回填的评分表（评分链路证据） | 入库 |
| `data/eval/20260904_v2/review/scores_review_20260913_143352.csv` | 评分对象 run 的评分表原件（该 run 的 traces 在清理中已删除，评分输出以本表为准） | 入库 |
| `data/eval/20260904_v2/review/audit/`（6 个文件） | 审核附属证据：`_citation_audit.txt`（534 条引用逐条核对明细）、`_citation_topics.txt`、`_entity_coverage.txt`、`_verify_citations.py`、`_fill_sheets.py`、`bank_review_input.csv`（首轮空白输入表） | 入库 |
| `data/eval/20260904_v2/runs/run_20260913_reviewed/` | 确定性修复后的重跑（**非评分依据版本**） | 不入库（.gitignore `data/eval/*/runs/`） |
| `data/eval/20260904_v2/runs/run_20260913_postaudit/` | **当前正式基线**：第三方审核整改后重跑 + 评分合并 | 不入库 |
| `data/eval/20260904_v2/runs/run_20260913_f2fixed/` | 上一版基线（F02/F03 修复版），保留对照 | 不入库 |

### 2.4 文档

| 文档 | 内容 |
| --- | --- |
| `docs/RAG_v1/RAGv4-开发说明.md` | 阶段开发说明：交付点、文件映射、验收对照、题库质量说明、§四之二 评审/复审结论、附录一评分说明、附录二审核工作流与自动核对口径 |
| `docs/changes/20260904-ragv4-summary.md` | 首版交付变更总结 |
| `docs/changes/20260913-ragv4-review-summary.md` | 评审轮总结：审核/评分结果 + 三项修复 + §六 第三方审核整改（T1–T8）+ §七 复审整改（R1–R7） |
| `docs/data-contract.md` | 补充证据 ID 格式（`graph_{legacy表名}_{行号}`，全局唯一） |
| `docs/features/02-entity-linking.md` | 朝代识别口径：软偏置（图谱侧生效、文本侧仅为返回序）+ 显式筛选硬过滤 |
| `docs/features/10-evaluation.md`、`docs/README.md`、`docs/RAG_v1/README.md`、`README.md`、`scripts/README.md`、`evaluation/README.md` | 状态与索引同步 |

## 三、设计要点（审查时容易质疑的地方，先说明）

1. **为什么评测驱动不复用 `server/sse.py::run_query`？**
   生产编排把"图谱+文本双通道"写死，无法产出"纯文本通道/纯 AND/纯 OR"对比配置；
   因此 `evaluation/chain.py` 复刻同一条链路（同一批底层函数、同一拒答硬规则），
   并用测试 `tests/test_chain_smoke.py::test_parity_with_server_run_query` 对拍
   （图/文/融合证据数量与 `run_query` 完全一致）。
2. **为什么绕过回答缓存？** 缓存键不区分检索通道，跨配置共享缓存会污染对比结论；
   评测每问每配置都走全量链路。
3. **指标为什么用"标注词子串覆盖"？** 与 RAGv2 复核遗留口径一致
   （"相关文本是否进入回答/引用"）；词级指标不区分"提到名字"和"给出事实"，
   这正是必须叠加人工评分的原因（报告里两类数据并列展示）。
4. **variant 机制**：filter_loss 套件的每道题展开 `filters-on`/`filters-off` 两种
   变体，才能量化"开 event_type 筛选后 raw/evidence 片段被整批剔除"的损耗。
5. **事实词级 verify**：`scripts/review_bank.py verify` 从 `gold_notes` 抽年份/数字/
   词典实体词，核对范围 = source_ref 指向 + 标注词命中实体行的全部关系行 + 索引片段；
   这是"机器能证明的"部分，不替代人工审核。
6. **朝代识别 = 软偏置，不做硬过滤**：问句里识别到的朝代写入 `F02Output.dynasty_bias`，
   显式 F01 筛选才硬过滤。硬过滤会把"被问到的朝代"连同事件剔除（问"商朝"时鸣条之战
   属夏 → 整题清空拒答）；改用偏置后，全库仅 B03/E01/L03 三题产出偏置。生效范围如实
   说明：**F03 图谱侧生效**（策略前重排节点，可经 `top_k` 影响证据集合）；**F04 文本侧
   仅为返回序**，随后被 F05 按相关分重排覆盖。

## 四、本轮发现并处理的系统问题（全部有复现与修复证据）

| # | 问题 | 证据 | 处理 |
| --- | --- | --- | --- |
| 1 | **F02 朝代筛选机制缺陷**（两轮修复）：问句朝代识别原为"按字典别名子串匹配 + 当作硬过滤"，硬过滤会把"被问到的朝代"连同事件剔除（B01/E01/R05/T03 由此拒答） | 评测 run 中 4 题为 `refused`；修复前主套件 ok 24/28；审核实测给词典补 `商朝→商` 后若仍硬过滤，E01 再次被打回拒答 | **终态**：自动识别朝代写入 `F02Output.dynasty_bias`，F03/F04 仅**排序前置（软偏置）**、不剔除结果；显式 F01 筛选仍硬过滤；恢复 `X朝/X国` 识别；回归守护 `test_f02_regression.py`（四题不拒答 + **补别名后仍不拒答**）；主套件 **ok 28/28** |
| 2 | **F04 长改写问题纯 AND 必然 0 命中**：相关文本召回完全依赖 OR 兜底 | 专项：4 题 `text-only-and` 召回 0% 且拒答，`dual`/`or` 均正常 | 已量化并报告；策略优化留 RAGv5 |
| 3 | **拒答缺口**：实体命中但"问题属性不存在"的越界问题不会被拒答 | X01–X03 全部 `should_refuse_answered` | 已记录，留 RAGv5 补规则 |
| 4 | **F03 证据 ID 跨 legacy 表重复**：`source_row_id` 只在各表内唯一（实测 6437 个号跨表重复），旧 ID `graph_{行号}` 会重复 | 数据核验 + 新测试 | 已修复为 `graph_{legacy表名}_{行号}`；`data-contract` 同步；全量 17,700 行 ID 唯一 |
| 5 | **跨进程非确定性**：`sorted(set(...), key=len, reverse=True)` 等长并列受 `PYTHONHASHSEED` 影响 → 两次 run 的 R02/E02 证据顺序/回答顺序不一致，破坏"可重复运行"验收 | 两次 run 对拍发现 | 已修复（并列按词序）；两次独立进程 run **87 条用例内容级 0 差异** |
| 6 | 筛选损耗（专项口径验证） | F01 `filters-on` 文本 top-k 召回 100%→0%、命中 30→1 条（仅 event_card） | 已量化；元数据补齐/放宽筛选留 RAGv5 |

## 五、结果数据（当前正式基线 `run_20260913_postaudit`，第三方审核整改后）

环境：Python 3.11（AI_Agent），**无 LLM key**（F06 走离线摘要回答器），
`text_mode=keyword`（向量索引为占位、未启用），题库 `reviewed-2`。

### 5.1 主套件客观指标（main，28 题）

| 配置 | n | 实体命中 | 图谱命中 | 文本top-k召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dual | 28 | 42.6% | 85.4% | 94.6% | 86.3% | 63.7% | 67.6% | 28/28 |
| text-only | 28 | 42.6% | - | 94.6% | 94.6% | 66.7% | 71.7% | 28/28 |

（对比：修复前 dual 为 42.6%/75.3%/83.3%/78.6%/57.1%/59.2%，ok 24/28。
差异分两轮来源：① F02/F03 修复让 4 题从拒答恢复，ok 24→28、"融合/召回"类指标抬升；
② 朝代识别改软偏置后 B03/L03 解除硬筛选，"引用携带(文本)"由 61.9%/64.9% 升至
63.7%/66.7%。注意：该项均值提升（+1.8pp）几乎全部来自 B03 一题的 2/4→4/4，
属小样本、不宜过度解读；其余维度与 ok/n 与上一版基线一致，无回归。）

### 5.2 专项结论

- **长改写（L01–L04）**：`text-only-and` 四题全部"文本 top-k 召回 0%、拒答"；
  `dual` 与 `text-only-or` 均能召回并覆盖。结论：**AND 优先策略对长句几乎失效，
  召回全部由 OR 兜底承担**（与 RAGv2 复核对齐）。
- **筛选损耗（F01–F04）**：F01 开 `event_type` 筛选后文本 top-k 召回 100%→0%
  （命中 30→1，仅剩 event_card 片段）；F02–F04 因事件卡文本本身够用而未掉覆盖率。
  结论：**原文（raw/evidence）在筛选下被整批剔除，影响程度因题而异**。
- **应拒答（X01–X03）**：三题全部未拒答（`should_refuse_answered`），
  当前拒答规则只覆盖"无证据/无共享词"两类。

### 5.3 人工评分布（39/39：首轮 AI 代理审核 + 4 题/2 题两轮复评）

| 维度 | 分布 |
| --- | --- |
| 答案正确性 | correct 0；partial 23；incorrect 16；unknown_answer 0 |
| 引用正确性 | supported 28；unrelated 3；unsupported 8 |

关键解释：**没有一条 correct** 是"离线摘要回答器"的形态所致（输出为关系清单 +
固定长度截断），不是检索失败；证据侧 28/39 引用可支持结论，多题为
"检索到位、回答未用"。语义质量结论应在接入真实 LLM 后复评。

## 六、验证证据与复现命令

```bash
cd F:/python/python_space/china-war/RAG          # Python 3.11（AI_Agent）

# 1) 单元/集成测试（50 用例；数据缺失用例自动 skip）
python -m pytest tests -q                        # 期望 50 passed

# 2) 题库校验（结构 + 标注词词典命中提示 + source_ref 可回溯性）
python scripts/run_evaluation.py check-bank      # 期望“结构校验通过（含 7 条提示）”

# 3) 一条命令复跑（39 题 × 配置 × variant = 87 条 trace）→ 报告 + 评分模板
python scripts/run_evaluation.py run

# 4) 报告与评分合并
python scripts/run_evaluation.py report --run data/eval/20260904_v2/runs/run_20260913_postaudit \
    --scores data/eval/20260904_v2/runs/run_20260913_postaudit/scores.jsonl

# 5) 审核/评分工作流（导出 → 人工或代理填写 → 回填）
python scripts/review_bank.py verify
python scripts/review_bank.py bank-export --out data/eval/20260904_v2/review/bank_review.csv
python scripts/review_bank.py peek --id R02
python scripts/review_bank.py bank-apply  --sheet <审核后的表> --reviewer <标识>
python scripts/review_bank.py scores-export --run <run目录> --out <评分表>
python scripts/review_bank.py scores-apply  --run <run目录> --sheet <评分表> --reviewer <标识>
```

已验证的关键结论（可复检）：

| 验证项 | 期望结果 |
| --- | --- |
| `pytest tests -q` | 50 passed（含 `test_f02_regression.py` 与 R4/A3 的"代"后缀正反例） |
| eval 驱动 vs `server.sse.run_query` 对拍（`tests/test_chain_smoke.py`） | 图谱/文本证据数量与融合条数一致 |
| 两次独立进程 run 内容级对拍（实体顺序/证据顺序/引用顺序/回答） | 0 差异（可复现性） |
| `tests/test_evidence_ids.py` | 17,700 行关系 ID 全量唯一；运行期检索 ID 唯一且带 legacy 表名 |
| `tests/test_dynasty_filter.py` | 单字朝代不再误判；多字/`X朝/X国/X代` 形式仍识别 |
| 审核整改后对拍（R4 后缀改动） | 与归档基线 `run_20260913_postaudit` **87 条内容级 0 差异**（整改不扰动评测行为） |
| 报告失败桶（R3） | 桶内超过 10 条时显示"（共 N 条，此处仅列前 10）" |
| 题库条目级版本（R6） | 39/39 条目 `annotation_version=reviewed-2`（与 meta 一致） |

## 七、审核链路与诚实边界（请审查模型重点关注）

1. **题库首轮审核由 AI 代理执行**：`reviewer=AI代理审核-ZCode(deepseek-v4-flash)`，
   产出 `review/review_report.md`；结论 35 通过 / 4 保留。F10 要求"人工审核"，
   **AI 代理审核与自然人审核不完全等价**，如需正式口径需人工复核
   （报告 §5.2 列的 6 项存疑：M11/T01/B03/M02/T02/R03）。
2. **4 题复评由本次开发代理完成**（`ZCode 复评（F02 修复后）`），沿用受托方
   报告 §4.1 的评分口径；这 4 题在修复前的评分（incorrect/unsupported）已作废替换。
3. **其余 35 条评分原样沿用**受托方结论，未做修改（已程序化校验：取值合法、
   非 correct/supported 行均有理由、上下文列未被篡改）。
   - 评分的**对象**是 `run_20260913_143352` 的输出（评分表 `scores_review_filled.csv`
     与其原件 `scores_review_20260913_143352.csv` 已随本总结归档到 `review/`）；
     该 run 的 traces 在评审前清理中已被删除，之后评分经 `scores-apply` 迁移到
     `run_20260913_reviewed`，再迁移到当前基线 `run_20260913_postaudit`（`f2fixed`
     为其上一版对照基线）。
   - 迁移的两处差异（**不影响判分**）：`E02` 仅证据顺序变化（集合相同）；
     `R02` 是**证据集合**变化——旧引用 18 条中的"目的地→樊口/途经地→华容道"等
     被换成"防守方→孙刘联军/发起方→孙刘联军"等（F05 稳定排序后按 `limit=18`
     截断，输入顺序变化会改变被保留的集合）。两种版本的回答都**没有给出主帅**
     （`incorrect`）且引用中都无"巨鹿之战/赤壁之战—统帅"这类关键证据
     （`unsupported`），故 R02 的评分结论仍成立。
4. **评测只覆盖离线回答器 + 关键词通道**：无真实 LLM、无向量检索，
   因此结论不能代表 RAG 的最终质量水平。
5. **未提交 Git**：全部改动仍在 RAG 仓库 `ragv3-frontend` 分支工作区（见第十节
   git 状态）；提交策略由用户决定。
6. **工作区内存在用户自建的 `new/` 目录**（飞书 Hermes 渠道化文档），与本阶段无关，
   未被改动。
7. **第三方功能审核与整改（2026-09-13 第二轮）**：审核报告见
   `docs/RAG_v1/RAGv4-阶段审核报告.md`（17 项复现验证 + 8 项待改）。T1–T8 已逐条整改：
   评分溯源归档、朝代硬筛选改软偏置（含"补别名不打回"回归）、报告类别列回填、
   失败桶按评分来源分桶、状态行同步、`graph_top_k` 与生产同源、证据 ID 测试共用被测
   函数、文档小疵修正。整改后重跑基线 `run_20260913_postaudit`（87 条内容级可复现），
   B03/L03 因软偏置变化按同口径复评（B03 incorrect→partial）。
8. **复审轮（同一审核报告的"整改验收"节）**：T1–T8 复核通过，另提 R1（文本侧偏置
   实际不生效）+ R2–R7（小疵）。已处理：R1 采纳审核推荐的**方案②（如实改文档口径）**——
   图谱侧偏置生效、文本侧仅为返回序且被 F05 相关分重排覆盖；R2 开发说明 §三 标注为
   历史基线并指向当前基线；R3 失败桶补"共 N 条，仅列前 10"；R4 朝代后缀补"代"
   （唐代/宋代/清代，误命中风险已验证）；R5 删除易误用的旧函数名；R6 题库条目级
   `annotation_version` 同步为 reviewed-2；R7 证据 ID 哈希回退断言加强 + 小样本说明。
   R1 方案①（偏置折进文本 `_score`）会改变融合排序、需重跑基线，未采用，留作后续可选优化。
9. **收口轮残余项（A1–A6）**：已全部处理——A1 两处文本层 docstring 补"返回序会被 F05
   按 `_score` 重排覆盖"注释；A2 阶段总结 5 处旧基线/旧函数名改齐；A3 补"代"后缀正反例
   测试（清代/宋代/时代/近代）；A4 重叠跨度去重（"清朝末年"→`['清朝']`，不再追加"清"）；
   A5 审核附属 6 文件归档到 `review/audit/` 并更新 `review_report` 指针（durable，不受
   `logs/` 忽略影响）；A6 `tests/test_bank.py` 注释更新 + 恒真断言改为 `category in CATEGORIES`。
   处理后 `pytest` 全绿，临时 run 与基线 `run_20260913_postaudit` **87 条 0 差异**。
10. **第四轮（B1–B5）**：验收确认 A1–A6 全部落地；B1 根 README 用例数补齐（→50）；
    B2 给 `EvalConfig` 加 `mode`（keyword/vector/hybrid）并透传（v5 向量对照前置，
    附无向量自动降级测试）；B3–B5 修正 v5 规划说明的 incorrect 全集（16 条）、
    对照配置名、`--no-embeddings` 参数、14 条出处与四轮表述；处理后 **50 passed**、
    基线 0 差异。
11. **第五轮（C1–C3 / R1–R2 / C1'）**：B1–B5 验收通过；v4 侧唯一残留 **C1'** 已修——
    `server/query/understand.py` docstring 由"已调用 deepseek 兜底"改为"**预留，尚未实现**"
    （`server/query/README.md` 同步）；v5 规划的 3 处任务缺口（C1 F02 兜底需新实现、
    C2 生产侧文本模式接线、C3 缓存键纳入 mode）与 2 处风险（R1 hybrid 分数量纲、
    R2 LLM 评测随机性）已补进 [RAGv5-规划说明.md](RAGv5-规划说明.md)。

## 八、给审查模型的审核清单（建议按序执行）

| # | 审核点 | 怎么审 | 通过标准 |
| --- | --- | --- | --- |
| 1 | 代码是否真的能跑 | `python -m pytest tests -q`；`python scripts/run_evaluation.py run` | 50 passed；生成 87 条 trace + report.md |
| 2 | 评测驱动是否与生产链路一致 | 读 `evaluation/chain.py` 与 `server/sse.py` 对照；跑 `tests/test_chain_smoke.py::test_parity_with_server_run_query` | 调用序列一致（仅绕过缓存 + 通道/名额可配，`graph_top_k` 默认取 settings）；对拍通过 |
| 3 | 客观指标是否有定义漏洞 | 读 `evaluation/metrics.py`；抽查 2–3 条 trace 手工重算覆盖 | 指标定义与报告口径一致；拒答行回答覆盖为 0 |
| 4 | 四类失败归类是否成立 | 读 `evaluation/metrics.py::classify` + 报告"自动归因失败样例" | 分类规则与 F10 表述可对应（人工评分兜底答案错/引用错） |
| 5 | 题库是否数据 grounded | `python scripts/review_bank.py verify`；抽查 `peek --id M01`/`R02` | 事实词级 39/39 通过；source_ref 可回溯（实体 id 或 `relations#表:行号`） |
| 6 | 评分是否真实、未被篡改 | 比对 `review/review_report.md`、`runs/run_20260913_postaudit/scores.jsonl` 与归档的 filled 表 | 39 条齐全；取值合法；6 条复评（4 + B03/L03）有说明；33 条与受托方结论一致 |
| 7 | 修复是否真的修好了 | 读三处 diff：`classifier.extract_dynasty_mentions`（原 `extract_dynasty_filter`，已删）、`graph.search._triple_from_row`（+`evidence_id_of`）、`dictionary_matcher.match` 排序；跑对应测试 | 代码与测试一致；F02 四题不再拒答 |
| 8 | 是否可复现 | 连续跑两次 `run`（不同 run-id）对拍 trace 内容 | 0 差异 |
| 9 | 契约与文档是否同步 | `docs/data-contract.md`（证据 ID）、`docs/features/02-entity-linking.md`（筛选口径） | 与新代码一致 |
| 10 | 朝代偏置语义（审核 T2） | 读 `classifier.extract_dynasty_mentions` / `understand.py` / `graph.search` / `text.searcher`；跑 `tests/test_f02_regression.py`（含"补别名后仍不拒答"） | 自动识别朝代只进 `dynasty_bias`（排序），不剔除结果；四题不拒答 |
| 11 | 评分溯源（审核 T1） | `data/eval/<v>/review/` 下应有 `bank_review_filled.csv`、`scores_review_filled.csv`、`scores_review_20260913_143352.csv`；对照 `runs/run_20260913_postaudit/scores.jsonl` | 39 行评分与工作表一致；4 题复评有出处说明 |
| 12 | 诚实性 | 检查第七节各条是否与仓库实际一致（尤其"AI 代理审核"标注、未提交 Git、离线回答器、审核轮整改） | 无夸大/无隐瞒 |

## 九、未做与后续（RAGv5）

1. 真实 LLM 接入后重跑本题库（`llm_used=true`），重点复测"检索到位、回答未用"的题
   （多数 partial 有望转 correct）；
2. 向量索引重建并启用向量/混合检索（当前占位）；
3. 拒答规则补强（应拒答却作答 3 题）；F04 长改写召回策略（AND 失效兜底）；
   筛选场景下 raw/evidence 元数据补齐或放宽筛选项；
4. F08 演示模式（示例题从本题库 `reviewed-2` 通过题中选型）；
5. 审核报告的 6 项存疑需人工确认（正式口径）；
6. **R1 方案①（可选）**：把问句朝代偏置折进文本 `_score`，让文本侧最终排序也受偏置
   影响——会改变融合排序、需重跑基线并复核 B03/L03；当前采用方案②（如实说明文本侧
   不生效），如需启用建议做成默认关闭的显式开关后再评估。

## 十、附录：文件树与 Git 状态

```text
RAG/
├── evaluation/                 # 新增：F10 评测包（bank/chain/metrics/report/grading/cli/README）
├── scripts/
│   ├── run_evaluation.py       # 新增：评测 CLI
│   ├── gen_draft_bank.py       # 新增：题库草稿（带防覆盖保护）
│   └── review_bank.py          # 新增：审核/评分工作流
├── tests/                      # 新增：8 个测试文件，50 用例（含 f02_regression、朝代后缀正反例）
├── contracts/request.py        # 改：F02Output 增 dynasty_bias（软偏置）
├── server/
│   ├── text/{searcher,__init__}.py     # 改：keyword_mode 可选参数 + 朝代软偏置排序
│   ├── query/{classifier,dictionary_matcher,understand}.py  # 改：朝代识别语义 + 排序确定性
│   ├── graph/{search,__init__}.py      # 改：证据 ID 带表名（evidence_id_of）+ 软偏置排序
│   └── sse.py                          # 改：传偏置、缓存键含偏置
├── data/eval/20260904_v2/
│   ├── questions.jsonl(.meta)  # 题库（reviewed-2）
│   ├── review/{REVIEW_GUIDE.md, review_report.md, bank_review_filled.csv,
│   │           scores_review_filled.csv, scores_review_20260913_143352.csv}
│   └── runs/{run_20260913_reviewed, run_20260913_f2fixed, run_20260913_postaudit}  # 不入库
└── docs/
    ├── RAG_v1/{RAGv4-开发说明.md, RAGv4-阶段工作总结.md（本文）, RAGv4-阶段审核报告.md}
    ├── changes/{20260904-ragv4-summary.md, 20260913-ragv4-review-summary.md}
    ├── data-contract.md、features/{02-entity-linking,04-text-retrieval,10-evaluation}.md、
    │   README.md、README.md(根)、scripts/README.md
```

Git（RAG 仓库 `ragv3-frontend` 分支，**未提交**，2026-09-13 实测，含 A/B/第五轮整改后）：

- `M`（已跟踪被修改）**21 个文件**：`.gitignore`、根 `README.md`、`contracts/request.py`、
  `scripts/README.md`、`server/` 9 个（query 4：classifier/dictionary_matcher/understand/README、
  text 2、graph 2、sse 1）、`docs/` 8 个（README、data-contract、features 4：02/04/08/10、
  RAG_v1/README、后续阶段规划）；
- `??`（新增未跟踪）**13 项**：`evaluation/`、`tests/`、`data/eval/`、`scripts/` 3 个、
  `docs/RAG_v1/` 4 份（开发说明/工作总结/审核报告/v5 规划说明）、`docs/changes/` 2 份，
  以及用户自建 `new/`（飞书渠道化文档，与本阶段无关）。
