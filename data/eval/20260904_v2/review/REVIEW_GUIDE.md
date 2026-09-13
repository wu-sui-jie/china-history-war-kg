# F10 题库审核与答案评分任务说明（委托审核用）

> 本文件是**交给代为审核的模型/人员**的完整任务书。审核只涉及同目录下的两个文件，
> 不需要阅读其他文档（需要复核数据时用文中给出的命令即可）。
>
> ⚠️ 状态（2026-09-13）：本轮委托审核**已完成**，报告见同目录 `review_report.md`，
> 回填表 `bank_review_filled.csv` / `scores_review_filled.csv` 已在此留存。
> 文中示例命令里的 `run_20260913_143352` 是当时的评分对象 run（痕迹已清理）；
> 当前正式基线为 `data/eval/20260904_v2/runs/run_20260913_postaudit`。
> 本文件保留作下一轮审核（如真实 LLM 接入后复评）的任务书模板。

## 0. 任务背景（一句话）

这是「中国历代战争史 RAG 问答系统」F10 评测的两个审核表：一是**题库本身**是否成立
（第一轮），二是**系统回答质量**人工评分（第二轮）。系统的知识库只有两本书的治理数据
（中国历代战争简史 / 中国战争史地图集，数据版本 `20260904_v2`），**系统只能依据知识库
作答**。

## 1. 输入文件与产出

**输入（只读，不要修改）**

| 文件 | 内容 |
| --- | --- |
| `bank_review.csv` | 第一轮：题库审核表，39 行（一行一题），utf-8-BOM 编码，可用 Excel/WPS/文本编辑器打开 |
| `scores_review_20260913_143352.csv` | 第二轮：答案/引用评分表，39 行（一行一条系统回答） |

**产出（写到同目录，文件名固定）**

| 文件 | 内容 |
| --- | --- |
| `bank_review_filled.csv` | 与输入同列同序；只填/改第 2 节规定的列，其余原样复制 |
| `scores_review_filled.csv` | 与输入同列同序；只填第 3 节规定的 4 列 |
| `review_report.md` | 审核报告：结论摘要、存疑清单（逐条给理由）、改写过的题目（如有） |

**硬性约束**

1. 不得改动输入文件；不得改动列名、列顺序、行数、`id/qid`；
2. `reviewed` 列只允许两种值：留空、`通过`（不要写别的）；
3. 评分列取值必须落在规定枚举内（见第 3 节），否则视为无效产出；
4. **不得用模型自身的历史知识作答或补充数据**。所有判断以 CSV 内的
   `data_digest`、`auto_verify`、`source_ref` 及下方命令查到的数据为准；
5. 拿不准就**留空并列入存疑清单**，禁止"猜一个通过"。

## 2. 第一轮：题库审核（`bank_review.csv`）

### 2.1 列说明

| 列 | 谁用 | 说明 |
| --- | --- | --- |
| `id` / `suite` / `category` | 只读 | 题号、套件（main 主基线 / long_rewrite 长改写 / filter_loss 筛选损耗 / refusal 应拒答）、题型 |
| `question` | 可改 | 问题原文。自然、无歧义、指向唯一事件/人物 |
| `dynasty` / `event_type` | 可改 | 该题附带的筛选条件（`;` 分隔），一般留空；**filter_loss 套件（F01–F04）的 `event_type` 不要清空** |
| `answerable` | 可改 | 以知识库为唯一依据：有据可答=`True`；确实无据=`False` |
| `expected_entities` | 可改 | "答对时回答/引用里必须出现"的实体词（`;` 分隔，每词≥2 字）。不要放泛词 |
| `expected_docs` | 可改 | 答案原文所在书（`doc01_中国历代战争简史` / `doc02_中国战争史地图集`），拿不准可留空 |
| `gold_notes` | 可改 | 参考要点（判分依据）。允许是改写摘要，但其中每个**关键事实词**（年份/数字/实体名）都须能在数据中找到支撑 |
| `source_ref` | 只读（除非发现指错） | 数据出处：`event_XXXX / person_XXXX` 等实体 id，或 `relations#<表名>:<行号>` |
| `data_digest` | 只读 | 该题 source_ref 的数据切片（截断版） |
| `auto_verify` / `auto_verify_detail` | 只读 | 机器事实词级核对结论（当前 39 条全部"通过"） |
| `reviewed` | **要填** | 认可填 `通过`；存疑留空 |

### 2.2 逐行怎么审（三步）

1. 读 `question` 与 `gold_notes`，确认"问的是什么、要点是什么"；
2. 对照 `data_digest`（必要时用 §4 命令查完整数据），确认：
   - `expected_entities` 每个词确实是"答对必须出现"的关键词；
   - `gold_notes` 的关键事实词有数据支撑；
3. 在 `reviewed` 填结论。机器已核对过的 21 行（见 2.3 表）若无异常直接填 `通过`。

### 2.3 需要额外判断的行

**A 类：必须拍板（5 行）**

| 行 | 情况 | 判断规则 |
| --- | --- | --- |
| `B01`、`E01`、`R05`、`T03` | 问句含"秦为什么…/商朝/楚军/朝代"等词，会触发 F02 朝代别名误判，导致系统误拒答（属**系统缺陷**，非题目写错） | 二选一：①**保留原题**作缺陷复现样本 → `reviewed` 留空，并在报告中注明"保留待系统修复后复测"；②**改写问句**避开触发词（如"长平之战的起因是什么？"）→ 改写后需确认新问句的 `expected_entities` 仍成立，再填 `通过`。建议选 ① 并记录 |
| `E02` | "官渡之战和赤壁之战有什么相似之处？"的要点此前有一句"两战皆以少胜多"，知识库不支持 | 现要点已改为"曹操均为一战一方；官渡以少胜多、奠定统一北方基础；赤壁奠定三国鼎立基础"。确认该表述与 `data_digest` 一致 → 填 `通过`；若你认为相似点不成立，填 `question` 为更贴切问法并说明 |

**B 类：扫一眼确认表述（13 行）**

| 行 | 关注点 |
| --- | --- |
| `M11`、`M12`、`M13`、`M14`、`R06` | 人物题要点已改成可回溯到关系行的表述（如 M11 含"垓下之战阵亡"），确认与 `data_digest` 一致 |
| `T02` | 要点中"东汉末年"的措辞（数据只写"东汉"，属改写，可接受） |
| `L01`–`L04`、`F01`–`F04` | 这 8 条要点含"要点：…"+"口径：…"两部分；确认"要点"部分正确（口径部分是评测设计说明，不用改） |

**其余 21 行**：`auto_verify=通过` 且无异常时，直接填 `通过`。

### 2.4 什么情况填"留空（存疑）"

- 问题有歧义、指向不唯一；
- `expected_entities` 里有"答对不必出现"或"太泛"的词；
- `gold_notes` 的关键事实词在数据里找不到支撑（可用 §4 命令复核）；
- 你依据知识库无法判断题目是否可答。

## 3. 第二轮：答案/引用评分（`scores_review_20260913_143352.csv`）

每行 = 某题在评测中的一条系统回答。只填 4 列：

| 列 | 取值 | 定义 |
| --- | --- | --- |
| `answer_correctness` | `correct` | 回答与 `gold_notes` 要点一致、信息正确完整、有引用支撑 |
| | `partial` | 方向正确但漏关键要点（如只说了年份没说主帅），或有个别小错 |
| | `incorrect` | 核心答错，或**没答出来**（`answerable=True` 却 `finish_reason=refused` 也算） |
| | `unknown_answer` | 仅用于 `answerable=False` 且系统**正确拒答** |
| `citation_correctness` | `supported` | 引用真实可查、且确实支撑该结论 |
| | `unrelated` | 引用了但与结论无关 |
| | `unsupported` | 关键结论没有任何引用支撑 |
| `notes` | 文字 | 一句话理由（尤其填非 correct/supported 时必须写） |
| `reviewer` | 文字 | 评分人/模型标识 |

**判分方法**：只看两样东西——`system_answer`（系统回答）与 `citations_text`（引用明细），
对照 `gold_notes`/`expected_entities` 判断。**不要**用模型自身的历史知识给回答"补分"。

**特殊行处理规则**

| 行 | 情况 | 建议评分 |
| --- | --- | --- |
| `B01`、`E01`、`R05`、`T03` | `answerable=True` 但 `finish_reason=refused`（F02 误判导致拒答） | `answer_correctness=incorrect`、`citation_correctness=unsupported`，notes 写"F02 朝代别名误判导致拒答" |
| `X01`、`X02`、`X03` | `answerable=False` 但系统没有拒答 | `answer_correctness=incorrect`（未正确拒答）、`citation_correctness=unrelated`，notes 写"应拒答却作答" |
| `L01`–`L04`、`F01`–`F04` | 长改写/筛选专项 | 按正常标准评分；F 系列每题的 `variant` 列标明这次回答是"带筛选（filters-on）"还是"不带筛选（filters-off）"，评分时看该行自己的内容即可 |

**评分保守原则**：引用明细无法核对到数据时不要给 `supported`；`partial` 与 `incorrect`
之间拿不准时选更严格的等级，并在 `notes` 说明理由。

## 4. 复核数据用的命令（有 shell 权限时用，没有就跳过）

在 `RAG/` 目录（`F:\python\python_space\china-war\RAG`）下执行：

```bash
# 查看某题 source_ref 指向的完整数据 + 索引中含标注词的原文片段
python scripts/review_bank.py peek --id R02

# 重新跑事实词级自动核对（生成 bank_verify.csv；结论同时体现在审核表 auto_verify 列）
python scripts/review_bank.py verify
```

若没有 shell 权限：只依据 CSV 里的 `data_digest` / `auto_verify_detail` 判断，
仍然无法确认的行列入存疑清单，不要猜。

## 5. 完成后自检清单

- [ ] 输出 3 个文件（`bank_review_filled.csv`、`scores_review_filled.csv`、`review_report.md`）；
- [ ] 输入文件未被修改；列名/列序/行数/`id` 未变；
- [ ] `reviewed` 只有"通过"或空；评分列只在枚举内；
- [ ] 非 `correct`/`supported` 的评分行都有 `notes` 理由；
- [ ] 存疑清单逐条给出"为什么无法判断"，并注明依据了哪个字段；
- [ ] 报告中列明：A 类 5 行的处理结论（保留/改写/待定）。

## 6. 回填命令（由接收方执行，审核模型不必执行）

```bash
# 题库结论回填（支持 .csv/.xlsx）
python scripts/review_bank.py bank-apply --sheet <bank_review_filled.csv> --reviewer <审核标识>
# 评分回填并合并进报告
python scripts/review_bank.py scores-apply --run data/eval/20260904_v2/runs/run_20260913_143352 \
    --sheet <scores_review_filled.csv> --reviewer <审核标识>
python scripts/run_evaluation.py report --run data/eval/20260904_v2/runs/run_20260913_143352 \
    --scores data/eval/20260904_v2/runs/run_20260913_143352/scores.jsonl
```
