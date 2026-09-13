# RAGv4 阶段审核报告（F10 评测，送改版）

- 阶段：RAGv4（F10 问答效果评测）
- 审核对象：[RAGv4-阶段工作总结.md](RAGv4-阶段工作总结.md)、[RAGv4-开发说明.md](RAGv4-开发说明.md)，
  以及两份文档引用的 `evaluation/` 包、`server/` 六处改动、`scripts/review_bank.py`、
  `tests/`、题库与两份 run 产物
- 审核日期：2026-09-13
- 审核环境：Python 3.11（E:/anaconda/envs/AI_Agent）、数据版本 `20260904_v2`、
  无 LLM key（离线摘要回答器）、`text_mode=keyword`
- 审核方法：读码 + 三次独立复跑 + 客观指标独立复算 + 归档工作表逐格比对（命令见附录 A）
- 总体结论：**阶段结论成立，核心数字全部可复现，未发现夸大或隐瞒**；
  待改 8 项，其中 T1、T2 建议优先处理，其余为轻微项
- 本文档用途：按"四、修改任务清单"逐条执行；每条含 证据 / 影响 / 修法 / 验收
- **复审轮（整改验收）见文末「复审轮：整改验收（2026-09-13）」**：T1–T8 已全部落地并复核，
  新基线 `run_20260913_postaudit`；复审另提 R1（中等）+ R2–R7（小疵）
- **第三轮（R1–R7 验收）见文末「第三轮：R1–R7 验收（2026-09-13 同日）」**：R1–R7 全部落地，
  评测行为零扰动（87 条与基线逐字段一致）；本轮另提 A1–A4（文档一致性 3 项 + 测试覆盖 1 项）
- **收口判断见文末「收口轮：v4 阶段剩余事项汇总（2026-09-13，只审不改）」**：v4 无阻塞性问题、
  可进入 RAGv5；剩余 A1–A6（文档/测试注释级）、v5 需继承的边界、以及"先提交 Git"的建议
- **第四轮（A1–A6 验收 + RAGv5 规划说明审核）见文末「第四轮：A1–A6 验收 + RAGv5 规划说明审核」**：
  A1–A6 全部落地、v4 零回归（49 用例、87 条与基线 0 差异）；v5 规划说明事实性核对 9 项通过，
  新提 B1–B5（其中 **B2 会影响 v5 的向量对照评测路径**，开工前需先补 `EvalConfig.mode`）
- **第五轮（v4 功能复核 + v5 规划深度分析）见文末「第五轮：v4 功能复核 + RAGv5 规划说明深度分析」**：
  B1–B5 全部落地、50 用例、与基线 0 差异；v5 规划另发现 **3 处任务缺口 C1–C3**（F02 LLM 兜底未实现、
  生产侧文本模式无接线、缓存键未含 mode）与 **2 处技术风险 R1–R2**（hybrid 分数量纲、LLM 评测随机性）

---

## 一、总体结论

四条 F10 验收标准都能对到可执行的实现与产物：一条命令复跑（`run` 产出 87 条 trace +
报告 + 评分模板）、报告区分失败类别（自动归因 + 人工评分合并）、双通道 vs 纯文本对比、
人工评分可导出复核。文档中所有可验证的数字（34 用例、87 条记录、39 题分布、主套件各项
覆盖率、17,700 行 / 6,437 个重复行号、评分分布 0/22/17 与 28/3/8、F01 筛选 100%→0%、
`verify` 39/39）我逐项复算，**结果与文档完全一致**。

三处修复（F02 朝代误判、F03 证据 ID、跨进程非确定性）在代码层面真实生效，默认路径零变化
（`keyword_mode` 默认 `and_or`、显式 filters 不过新规则、ID 带表名且有哈希回退）。

需要处理的问题集中在两类：

1. **可追溯性**：人工评分的原始依据 run（`run_20260913_143352`）未归档，`总结` 把
   `run_20260913_reviewed` 误标为"人工评分的原始依据"；R02/E02 两题的评分依据版本与归档
   版本不同；4 题复评没有归档工作表；filled 表实际放在被 `.gitignore` 忽略的 `logs/` 下。
   —— **结论数字不受影响，但第三方无法完整复演评分链路（T1）**。
2. **F02 朝代筛选的语义风险**：修复只是"不再误判"，底层"硬筛选会清空检索"的机制还在。
   我实测：**只要给词典补上"商朝"别名，E01 会立刻退回"图谱 0 / 文本 0 / 融合 0 / 直接拒答"**。
   这条必须在文档里留下警示，否则后续有人"顺手补词典"就会把已修好的题打回去（T2）。

其余 6 项为报告列空白（T3）、自动归因桶标签与数据来源不一致（T4）、陈旧状态行（T5）、
评测链路硬编码（T6）、测试守护强度（T7）、文档小疵（T8）。

## 二、验证结果（已复现的事实）

| # | 验证项 | 方法 | 结果 |
| --- | --- | --- | --- |
| 1 | 测试用例 | `python -m pytest tests -q` | **34 passed**，与文档"34 用例"一致 |
| 2 | 题库校验 | `python scripts/run_evaluation.py check-bank` | 结构通过，含**恰好 7 条**词典未命中提示 |
| 3 | 主套件指标 | 从 `traces.jsonl` 独立重算 | dual 42.6/85.4/94.6/86.3/61.9/67.6、ok 28/28；text-only 42.6/-/94.6/94.6/64.9/71.7 —— **与 `总结` §5.1 逐位一致** |
| 4 | 修复前基线 | 同上 | 42.6/75.3/83.3/78.6/57.1/59.2、ok 24/28，与两文档对比行一致 |
| 5 | 修复归因 | 逐题 diff 两 run 的 dual 输出 | 输出确有变化的**只有 B01/E01/R05/T03**，坐实"指标提升来自 F02 修复" |
| 6 | 评分真实性 | `scores.jsonl` ↔ 归档 filled 表逐格比对 | 39 行上下文列与评分列 **0 差异**；4 题复评标记齐全 |
| 7 | 评分分布 | 统计 | 0 correct / 22 partial / 17 incorrect；28 supported / 3 unrelated / 8 unsupported |
| 8 | 可复现性 | 两次独立进程全量 run 对拍 | **87 条记录 0 差异**（仅 `stage_ms` 不同） |
| 9 | 基线可复现 | 新跑 vs 归档 `run_20260913_f2fixed` | **0 差异**（归档基线在今天的代码上仍可逐字段重放） |
| 10 | 指标可审计 | 用 `traces.jsonl` 文本手工重算 6 个维度 | 84 条带标注词记录 **全部 0 差异** |
| 11 | 证据 ID 唯一性 | 直接统计 17,700 行关系 | 17,700 行、**6,437 个行号跨表重复**、新格式 ID 全量唯一 |
| 12 | 事实词核对 | `python scripts/review_bank.py verify` | **通过 39 / 存疑 0** |
| 13 | 筛选损耗 | F01 filters-on/off 逐条看 | on：n=1（仅 event_card）、覆盖 0/2；off：n=30（raw 10 / evidence 6 / event_card 14）、覆盖 2/2 |
| 14 | 长改写 AND 失效 | long_rewrite 套件 | 4 题 `text-only-and` 召回 0% 且拒答；`dual`/`text-only-or` 正常 |
| 15 | 应拒答缺口 | refusal 套件 | X01–X03 全部 `should_refuse_answered` |
| 16 | 生产链路一致性 | `chain.py` 与 `sse.py` 逐行对照 | 两条拒答硬规则、text 检索入参、`fusion limit=18`、`build_citations` 同口径；差异确实只有"绕过缓存 + 通道可配" |
| 17 | Git 状态 | `git status --porcelain` | 分支 `ragv3-frontend`、未提交、`new/` 为用户自建（计数有一处小错，见 T8） |

> 补充：第 9 项比文档要求更强（文档只要求"两次 run 对拍"，我额外验证了归档基线可重放，
> 说明归档后代码没有静默变化）。第 10 项说明审核清单第 3 条（手工重算覆盖）确实可执行。

## 三、已确认正确、请勿误改

1. **指标口径**：拒答行回答覆盖记 0、`ok` 只表示"覆盖标注词"而非"正确"，报告与文档都写明，
   不要因为"28/28 ok 但 0 correct 看起来矛盾"去改口径 —— 两者量的是不同东西。
2. **trace 文本证据截断长度**：`chain.py::_text_ev_compact(ev, limit=800)` 是审核可复算的
   前提之一（本次 84 条记录重算 0 差异）。若改小，审核清单第 3 条会失效；确实要改时请在
   报告里注明"复算需全量文本"。
3. **确定性排序**：`dictionary_matcher.match` 的 `key=lambda w: (-len(w), w)` 与
   `classifier.extract_dynasty_filter` 的同款排序是"可重复运行"验收的根基，请勿回退为
   `key=len, reverse=True`。
4. **证据 ID 格式**：`graph_{legacy表名}_{行号}`（缺行号时用含表名的哈希回退），不要退回纯行号。
5. **`citations` / `ask` 相关默认路径**：`server/text/search()` 的 `keyword_mode` 默认
   `and_or`，`search_keyword()` 的入参追加在末尾（位置参数兼容），勿把默认值改成 `and`/`or`。
6. **`gen_draft_bank.py` 的防覆盖保护**（`--force` 才允许覆盖，第 198-201 行）有效，勿删。
7. **AI 代理审核的标注**：`reviewer` 字段、`总结` §七的诚实边界，都与仓库实际一致，勿"美化"。

---

## 四、修改任务清单

### T1（建议优先，文档 + 归档）人工评分的溯源链补全

**证据**

- `data/eval/20260904_v2/review/review_report.md` 第 3 行写明评分对象是
  `scores_review_20260913_143352.csv`，对应 run `run_20260913_143352`；**该 run 的 traces 未归档**
  （`runs/` 下只有 `run_20260913_reviewed`、`run_20260913_f2fixed`）。
- 实测：`runs/run_20260913_reviewed/scores.jsonl` 与同目录 `traces.jsonl` 在 **R02、E02**
  两题上不一致；而与归档 filled 表 `logs/review-archive/review-20260913/scores_review_filled.csv`
  **39 行全字段 0 差异**（说明评分表是原样落地的，是"被评分的那份输出"没归档）。
- `RAGv4-阶段工作总结.md:70` 把 `run_20260913_reviewed` 标为"修复前基线（**人工评分的原始依据**）"，
  与 `changes/20260913-ragv4-review-summary.md` 的"正式基线 run = 修复确定性后重跑"矛盾。
- **R02 不是"仅顺序"差异**（`changes` 文档的表述偏轻）：旧引用 18 条中的
  `目的地→樊口 / 途经地→华容道 / 目的地→江陵` 被换成
  `防守方→孙刘联军 / 发起方→孙刘联军 / 投降方→孙刘联军`，回答首句随之变化；E02 才是同集合换序。
  机制：F05 稳定排序后按 `limit=18` 截断，输入顺序变化会改变**被保留的集合**。
- 4 题复评（B01/E01/R05/T03）的评分只存在于 `run_20260913_f2fixed/scores.jsonl`
  （`reviewer=ZCode 复评（F02 修复后）`、`notes` 有理由），没有对应工作表 →
  `总结` §八 审核清单第 6 条"与归档的 filled 表比对"对它们不可执行。
- filled 表位于 `RAG/logs/review-archive/review-20260913/`，而 `.gitignore:11` 忽略 `logs/`，
  它们**永远不会随提交入库**；`.gitignore` 自己的注释却写明"人工审核工作表放在
  `data/eval/<v>/review/`（需在编辑器可见，故不忽略）"。

**影响**：结论数字不变（R02 新引用同样没有主帅证据，`incorrect/unsupported` 仍成立），
但第三方无法完整复演评分链路；`总结` 的标注与事实不符。

**修法**

1. 改标注（必须）：`RAGv4-阶段工作总结.md:70` 改为
   "`run_20260913_reviewed`：确定性修复后的重跑（非评分依据版本）；人工评分的对象为
   `run_20260913_143352`（traces 未归档），评分经 `scores-apply` 原样迁移"。
2. 归档工作表：把 `bank_review_filled.csv`、`scores_review_filled.csv`、
   `originals/scores_review_20260913_143352.csv` 复制到 `data/eval/20260904_v2/review/`
   （该目录不被忽略）；保留 `logs/review-archive/` 原件。
3. 补 4 题复评出处：在 `RAGv4-开发说明.md` §四之二 或新 changes 文档里写明
   "4 题复评的原始记录为 `run_20260913_f2fixed/scores.jsonl` 的评分列 + `notes`，无独立工作表；
   如需正式口径请人工复核这 4 条"。
4. 在 `总结` §七 增补一句 R02/E02 的差异性质与"不影响判分"的理由（照抄本节证据即可）。

**验收**

- `grep -rn "143352" docs/ data/eval/*/review/` 各处说法一致，无"原始依据"误标；
- `ls data/eval/20260904_v2/review/` 含 filled 表，且 `git status` 能看到它们未被忽略；
- `总结` 里能读到 R02（集合变化）与 E02（换序）的区别说明。

---

### T2（建议优先，代码/数据设计）朝代硬筛选语义 —— 含"不要这么做"的守卫

**现状**

`server/query/classifier.py::extract_dynasty_filter`（第 75-108 行）只接受长度 ≥ 2 的术语，
18 个单字键不再参与，误判修好了。漏判面：词典 `dynasty_aliases` 缺"X朝"写法 ——
`dicts.json` 里以"朝"结尾的键只有 元朝/北朝/南北朝/南朝/唐朝/明朝/清朝/隋朝，因此
"秦朝/商朝/夏朝/周朝/宋朝/汉朝/晋朝"等自由文本问句不再自动加筛选。词典由 F09 生成：
`data/snapshot/governance.py:371-375` 用实体 `dynasty` 字段去重得到 `{d: d for d in dynasty_set}`，
没有手工别名表。

**关键实验（务必先看，我已实测）**

| 场景 | F02 filters | 图谱证据 | 文本证据 | 融合 | 是否拒答 | 文本覆盖 |
| --- | --- | --- | --- | --- | --- | --- |
| 现状（E01，不补别名） | `{'dynasty': []}` | 20 | 1 | 14 | 否 | 2/2 |
| 给词典补 `{"商朝":"商"}` 后 | `{'dynasty': ['商']}` | **0** | **0** | **0** | **是** | **0/2** |

原因：鸣条之战的实体 `dynasty` 是"夏"，问题问的是"与商朝的关系"，硬筛选把**被问事件本身
连同原文一起剔除**了。这与 B01/E01/R05/T03 原本的缺陷是**同一个机制**（不是单字键特有）——
所以"补词典"不是修复，而是把缺陷换个触发方式。

**修法（顺序不能反）**

1. **先修筛选语义**（二选一，推荐 a）：
   - a. 自动识别的朝代**只用于排序加权**，不作为硬过滤；来自 F01 下拉的显式 filters 保持硬过滤。
     改动点在 `server/query/understand.py`（区分"用户显式"与"问句自动识别"两类 filters）；
   - b. 保留硬过滤，但增加回退：筛选后证据为空（或覆盖显著低于不筛选）时，用不筛选重试并取较优结果
     —— 与 F04 的"AND 优先 / OR 兜底"同一思路。
   - 注意：`server/sse.py` 与 `evaluation/chain.py` 两条链路都要改，否则
     `tests/test_chain_smoke.py::test_parity_with_server_run_query` 对拍失败。
2. **再恢复识别**：在 `data/snapshot/governance.py::_build_dicts` 增加手工别名扩展并合并进
   `dynasty_aliases`（这样重建快照不会丢），建议最小集：
   `秦朝→秦、商朝→商、夏朝→夏、周朝→周、宋朝→宋、梁朝→梁、辽朝→辽、金朝→金、陈朝→陈、楚国→楚、燕国→燕、齐国→齐、秦国→秦`
   （请先确认目标朝代名在实体数据里存在，否则筛选会变成清空检索）。
3. **加端到端回归**：新增 `tests/test_f02_regression.py`，对 B01/E01/R05/T03 跑
   `chain.run_question`，断言 `refusal is None`、`text n > 0`、`fused n > 0`。
   现有测试只覆盖 `extract_dynasty_filter` 的返回值，**没有守护"四题不被再次打回拒答"**。

> 注意：改词典会让 `tests/test_dynasty_filter.py::test_single_char_dynasty_not_extracted`
> 的"鸣条之战与商朝的建立有什么关系？"断言失败 —— **这是正确的守卫，不要放宽断言**，
> 它提示的正是第 1 步尚未完成。

**验收**

- 四题端到端不拒答、文本与融合证据非 0；
- `python -m pytest tests -q` 全绿（含新增用例）；
- `check-bank` 的 7 条提示不被"消掉"（见 T8 关于标注词的说明）。

---

### T3（轻微，代码）报告"逐题对照表"的类别列恒为空

**证据**：`evaluation/report.py:218` 用 `d.get("category","")`，但 `evaluation/cli.py:198-208`
写 trace 记录时未带 `category`（实测记录键：`question_id / question / suite / variant / config /
answerable / expected_entities / filters / trace`）。结果 `report.md` 主套件对照表 28 行
"类别"列全空 —— 这张表正是 F10 验收第 3 条（双通道 vs 纯文本）的交付物。

**修法**（推荐 A）

- A. `evaluation/cli.py` 组装 rec 时补 `"category": q.category`；`report.py` 保留
  `d.get("category")` 并兼容旧 traces：`d.get("category") or (bank.by_id()[qid].category if bank else "")`。
- B. 只改 report，用 `run["bank"]` 回填（不动 traces 结构，但依赖 meta 里的 bank_path）。

向后兼容说明：`grading.build_template_rows`、`review_bank.py::scores_apply` 都按字段名 `.get` 读取，
追加 `category` 键是安全的（既有 `scores.jsonl` 不受影响）。

**验收**：重跑一次 `run`，`report.md` 对照表"类别"列非空；`pytest tests -q` 仍全绿。

---

### T4（轻微，代码）"自动归因失败样例"的桶标签与数据来源不一致

**证据**：`evaluation/report.py:28-33` 的 `_HUMAN_CATEGORY` 标签写"答案错误（**人工评分**
incorrect/partial + 相关文本缺失）""引用错误（**人工评分** unsupported/unrelated 等）"，
但桶内容在 `_failure_list`（report.py:349-364）只由自动 verdict 填充
（`answer_miss→answer_wrong`、`fusion_cut→citation_wrong`、`refused/should_refuse_answered→no_evidence`、
`retrieval_fail→retrieval_fail`）。本基线 dual 全部 ok，于是两桶显示"（无）"，而同页人工评分有
17 条 incorrect、8 条 unsupported。

**影响**：标题与来源不一致，容易被读成"系统没有答案错/引用错"（实际是"自动指标抓不到"）。

**修法**：把标签改成自动口径（如"答案错候选（answer_miss：证据携带但回答未覆盖标注词）"）；
或让这两个桶在有 `scores` 时改按人工评分分桶、无评分时退回自动候选并在标题标明来源。

**验收**：报告该节不再把"人工评分"写进自动桶标题；有评分时与"人工判定失败清单"口径一致。

---

### T5（轻微，文档）陈旧状态行同步

`RAGv4-开发说明.md` 正文与它自己的 §四之二互相矛盾（§四之二已是终态，前面没同步）：

| 位置 | 现状 | 应改为 |
| --- | --- | --- |
| `RAGv4-开发说明.md:4` | 状态：已完成 ✅（题库 `draft-0` 待人工审核；评分待人工进行） | reviewed-2（39/39）、39 条已评分 |
| `:18`、`:93` | draft-0 | reviewed-2 |
| `:109`（§三） | annotation_version=reviewed-1（35 通过/4 保留） | reviewed-2（39/39） |
| `:120` | "39 条待评分" | 已评分（0 correct/22 partial/17 incorrect） |
| `:164`（§四标题） | "题库 draft-0 结构与质量说明" | 终态标题 |
| `:230`（§五.2） | reviewed-1（35 通过 / 4 保留缺陷样本） | reviewed-2；4 条已复测通过 |
| `:279`（§七.1） | 首轮审核已完成（reviewed-1…） | 终态 |

其余同类：

| 位置 | 现状 | 应改为 |
| --- | --- | --- |
| `docs/README.md:111` | 质量闭环…◐（…人工审核/评分待续） | ✅（RAGv4 已完成，见同文件 52/126 行） |
| `README.md:165` | `RAGv4 \| F10 评测、F08 演示模式 \| 后续` | RAGv4 已完成；F08 移入 RAGv5 行（补 RAGv5 行） |
| `docs/RAG_v1/后续阶段规划.md:21` | ✅ 已执行（2026-09-04，题库 draft-0 与人工评分待续） | 已完成（题库 reviewed-2、评分完成） |
| `docs/RAG_v1/后续阶段规划.md` 头部 | 状态：◐ 部分执行（RAGv4 已开工并完成首轮） | ✅ RAGv4 完成；RAGv5 规划中 |

**验收**：`grep -rn "draft-0\|reviewed-1" docs/ README.md` 仅命中历史 changes 文档与
`review` 归档说明；`grep -rn "人工审核/评分待续\|待人工进行" docs/ README.md` 无命中。

---

### T6（轻微，代码）评测链路图谱 `top_k` 硬编码

**证据**：`evaluation/chain.py:165` 写死 `top_k=40`，`server/sse.py:149` 用
`top_k=settings.query_top_k_graph`（`config/defaults.py:55` 默认 40）。

**影响**：默认值下对拍通过；一旦有人调 `QUERY_TOP_K_GRAPH` 或 `.env`，评测口径与生产静默分叉，
且"对拍测试"只在默认值下有效；同时 `EvalConfig` 无法对比不同 `top_k`。

**修法**：给 `EvalConfig` 加字段 `graph_top_k: int = 0`（0 = 取 settings），调用处改为
`top_k=cfg.graph_top_k or runtime.settings.query_top_k_graph`；`text_top_k`/`fusion_limit`
已参数化，保持现状。

**验收**：`test_parity_with_server_run_query` 通过；把 settings 改成非 40 后对拍仍通过。

---

### T7（轻微，测试）证据 ID 回归测试复制了被测规则

**证据**：`tests/test_evidence_ids.py:37` 自行拼接 `f"graph_{table}_{rid}"`，与被测的
`server/graph/search.py:32 _triple_from_row` 是两份实现。

**影响**：改坏 ID 规则时该用例不会失败（它测的是数据资产），唯一守护是同文件第二个运行期用例。

**修法**：把 ID 生成抽成模块级纯函数（如 `server/graph/search.py::evidence_id_of(row)`），
让 `_triple_from_row` 与测试都调用它；或让测试构造 `GraphSearch` 实例后调用
`_triple_from_row` 断言前缀与唯一性。

**验收**：故意改错 ID 规则时，测试会红。

---

### T8（轻微，文档）小疵清单

1. `docs/data-contract.md:393` 的 conflicts 示例仍是 `"evidence_ids": ["graph_001", "card_001"]`，
   与同文件 201-210 行的新格式说明冲突 → 改成 `["graph_event_person_relations_772", "card_001"]` 之类。
2. `evaluation/cli.py:100` 注释写"关系行 id（relations#数字…）"，而 `_REF_RE` 实际要求
   `relations#<legacy表名>:<行号>` → 改注释。
3. `docs/features/04-text-retrieval.md` 未记录新增的 `keyword_mode` → 补一小段：
   默认 `and_or`（生产行为不变）、`and`/`or` 仅供 F10 拆解 AND 失效面。
4. `RAGv4-阶段工作总结.md:254` 的 git 统计"`M` 12 个文件"实测为 **15** 个
   （docs 6 + 根 README + `.gitignore` + `scripts/README` + `server` 6），未跟踪清单也没列
   `RAGv4-阶段工作总结.md` 自身 → 修正数字与清单。
5. `evaluation/README.md` 结论第 2 条"与生产链路的差异仅此一项 + 通道开关"，实际还有 T6 的
   `top_k` 取值方式（当前等价）→ 顺手补一句或修 T6 后一并更新。

---

## 五、建议执行顺序

1. **T1**（溯源补全）—— 纯文档与文件搬移，零代码风险，先把证据链补上。
2. **T2 第 1 步**（筛选语义：自动识别改软筛选或加权）—— 这是唯一可能影响线上检索行为的改动，
   改完必须先跑 `pytest` + 四题端到端回归，再考虑第 2 步补词典。
3. **T3、T4**（报告可读性）—— 小改动，改完重跑一次 `run` 即可看到效果。
4. **T5、T8**（文档同步）—— 批量 grep 一次性改干净。
5. **T6、T7**（口径与测试强度）—— 可与 T2 一并处理（都会碰 `chain.py` / `server/graph/`）。

改完建议整体验收一遍：

```bash
cd F:/python/python_space/china-war/RAG
E:/anaconda/envs/AI_Agent/python.exe -m pytest tests -q          # 期望全绿
E:/anaconda/envs/AI_Agent/python.exe scripts/run_evaluation.py check-bank
E:/anaconda/envs/AI_Agent/python.exe -m evaluation.cli run --run-id run_<新日期>_postfix
E:/anaconda/envs/AI_Agent/python.exe scripts/review_bank.py verify
# 再把新旧 run 的 traces 对拍（忽略 stage_ms）：应为 0 差异，除非 T2 有意改变了检索行为
```

## 六、本次审核未覆盖的范围

- 前端 F01/F07 与 `server/api.py`（本轮不涉及）；
- 真实 LLM 与向量通道（无 key、`embeddings.npy` 为占位），"离线回答器下 0 correct"的后续
  影响无法评估；
- `new/` 目录（飞书 Hermes 渠道化文档，与本阶段无关）；
- 数据治理侧的历史数据质量（只核对了本题库用到的锚点）。

本次审核**未修改任何被审代码与数据**：复跑产生的 run 目录已删除、`latest.txt` 已还原为
`run_20260913_f2fixed`、临时生成的 `bank_verify.csv` 已删除（可用 `verify` 随时重建）。

## 附录 A：复现命令

```bash
cd F:/python/python_space/china-war/RAG
PY=E:/anaconda/envs/AI_Agent/python.exe

# 1) 测试与题库校验
$PY -m pytest tests -q
$PY scripts/run_evaluation.py check-bank

# 2) 复跑（两次不同 run-id 后对拍：忽略 stage_ms 应 0 差异）
$PY -m evaluation.cli run --bank data/eval/20260904_v2/questions.jsonl --run-id run_a
$PY -m evaluation.cli run --bank data/eval/20260904_v2/questions.jsonl --run-id run_b
#    对比 data/eval/<v>/runs/run_a/traces.jsonl 与 run_b/traces.jsonl（去掉 stage_ms 字段）

# 3) 从 traces 复算指标（审核清单第 3 条）
#    用 trace.auto.coverage.names 与 trace.text/graph/fused/citations 的文本重算，
#    本次 84 条记录与 trace.auto.counts 完全一致

# 4) 事实词级核对（生成 bank_verify.csv，可随时删除）
$PY scripts/review_bank.py verify

# 5) 评分 ↔ 归档工作表比对
#    data/eval/<v>/runs/run_20260913_f2fixed/scores.jsonl
#    vs logs/review-archive/review-20260913/scores_review_filled.csv（39 行全字段应一致）

# 6) T2 实验（补别名会打回 E01，勿直接上线）
#    在内存里给 qu.matcher._dicts["dynasty_aliases"] 加 {"商朝":"商"} 后
#    用 evaluation.chain.run_question 跑 E01：图谱/文本/融合 归零并直接拒答
```

## 附录 B：关键数字对照表（文档值 vs 实测值）

| 项 | 文档值 | 实测值 | 复算方式 |
| --- | --- | --- | --- |
| pytest 用例 | 34 | 34 passed | 实跑 |
| check-bank 提示 | 7 条 | 7 条 | 实跑 |
| 评测记录 | 87 条（39 题） | 87 条 / 39 题 | 数 traces.jsonl |
| main/dual | 42.6/85.4/94.6/86.3/61.9/67.6，ok 28/28 | 一致 | trace.auto 重算 |
| main/text-only | 42.6/-/94.6/94.6/64.9/71.7，ok 28/28 | 一致 | trace.auto 重算 |
| 修复前 dual | 42.6/75.3/83.3/78.6/57.1/59.2，ok 24/28 | 一致 | trace.auto 重算 |
| 评分分布 | 0/22/17；28/3/8 | 一致 | 统计 scores.jsonl |
| 关系行 / 重复行号 | 17,700 / 6,437 | 一致 | 统计 relations.json |
| 复现性差异 | 0 | 0（含与归档基线对拍） | 逐字段 diff |
| 指标可复算性 | 未声明 | 84/84 一致 | 从 traces 手工重算 |
| verify 通过数 | 39/39 | 39/0 | 实跑 |
| F01 筛选损耗 | 100%→0%、30→1 条 | 一致 | 逐条看 filters-on/off |

---

# 复审轮：整改验收（2026-09-13）

> 复审对象：针对本报告 T1–T8 的整改提交（RAG 仓库 `ragv3-frontend` 分支工作区）。
> 复审方法：读码 + 目录/时间戳核对 + 44 用例实跑 + **两次独立进程复跑对拍** +
> 全库逐条 diff 新旧基线 + 指标独立复算 + 评分溯源逐格比对。
> 复审结论：**T1–T8 全部落地且可验证，未发现回归**；新发现 R1（中等）与 R2–R7（小疵）。
> 新基线：`run_20260913_postaudit`（取代 `run_20260913_f2fixed` 成为"当前正式基线"）。
> 复审使用的临时 run 目录与 `latest.txt` 已还原，仓库未留复核残留。

## 七、T1–T8 逐项验收

| 项 | 结论 | 复核证据 |
| --- | --- | --- |
| T1 溯源补全 | ✅ | `data/eval/20260904_v2/review/` 现有 `bank_review_filled.csv`、`scores_review_filled.csv`、`scores_review_20260913_143352.csv`（该目录不被 `.gitignore` 忽略）；33 条沿用评分与归档 filled 表**逐格 0 差异**；4 条一轮复评（B01/E01/R05/T03）+ 2 条二轮复评（B03/L03，`reviewer=ZCode 复评（软偏置改造后）`）均有标记与理由；39 条评分表上下文列与本 run 模板**0 差异**；changes 文档新增 §六 整改对照表，并写明 R02 是**证据集合变化**、E02 仅换序 |
| T2 硬筛选→软偏置 | ✅ | `F02Output.dynasty_bias` → F03/F04 排序，**不进 filters**；显式 F01 筛选仍硬过滤且与偏置去重（`understand.py`）；全库仅 3 题产出偏置（见 §二）；B01/R05/T03 的 bias 仍为 `[]`；新增 `tests/test_f02_regression.py`（四题不拒答 + **补词典别名后仍不拒答**的守护）；**未改治理词典**（避免污染 F01 下拉） |
| T3 类别列 | ✅ | 报告"逐题对照表"现显示 `background`/`relation`/`single_entity`/`event_event`/`timeline`/`comparison`；`report._category_of` 对旧 traces 用题库回填 |
| T4 失败桶来源 | ✅ | 报告首行写明"分桶来源：**人工评分**（已评分 39 条）"，桶内容为 `人工答案=partial；理由…`，与"人工判定失败清单"口径一致；无评分时退回自动候选并标注来源 |
| T5 陈旧状态行 | ⚠️ 基本完成 | 12 处已改；仅剩 `RAGv4-开发说明.md` §三 一处历史/现状混写（见 R2） |
| T6 图谱 top_k | ✅ | `EvalConfig.graph_top_k`（0 = 取 `runtime.settings.query_top_k_graph`，`chain.py:168`） |
| T7 测试守护 | ✅ | 抽出模块级纯函数 `server/graph/search.evidence_id_of()`，生产与测试共用；补确定性与哈希回退用例 |
| T8 文档小疵 | ✅ | `data-contract` conflicts 示例、`cli.py` 注释、`features/04` 的 `keyword_mode`、`evaluation/README` 差异说明均已改；git 计数"19 个文件 / 12 项未跟踪"**与实际一致**（复审实测 19 M / 12 ??） |

## 八、新基线实测数字（复审复算）

测试与校验：`pytest tests -q` → **44 passed**（原 34，新增 10）；`check-bank` → 结构通过 + **7 条**提示（未变）。

主套件（main，28 题）：

| 配置 | 实体命中 | 图谱命中 | 文本top-k召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dual | 42.6% | 85.4% | 94.6% | 86.3% | **63.7%**（原 61.9%） | 67.6% | 28/28 |
| text-only | 42.6% | - | 94.6% | 94.6% | **66.7%**（原 64.9%） | 71.7% | 28/28 |

其余维度与 ok/n 与旧基线完全一致 —— 整改**只带来引用携带的提升，无回归**。

评分（39 条）：0 correct / **23** partial / **16** incorrect；引用 28 supported / 3 unrelated / 8 unsupported。
来源分布：33 条沿用受托审核 + 4 条一轮复评 + 2 条二轮复评（B03 incorrect→partial、L03 复评说明更新）。

变化面（新基线 vs `run_20260913_f2fixed`，全 87 条逐条 diff）：

- 所有记录新增 `understand.dynasty_bias` 字段（结构性变化）；
- **实质变化只有 B03（dual/text-only 2 条）与 L03（dual、text-only-and、text-only-or 3 条）**；
- 偏置分布：`B03=['东晋']`、`E01=['商']`、`L03=['东晋','前秦']`，其余题一律 `[]`；
- B03：`filters` 由 `['东晋']` 变空、转为 bias，文本证据 7→30、引用携带 2/4→4/4；
- L03：`filters` 由 `['东晋','前秦']` 变空，图谱证据 16→24、文本 12→30、融合 16→18，覆盖计数不变；
- E01：`bias=['商']` 但 `filters` 仍为空、**不拒答** —— 原缺陷修复未被回退。

复现性：两次独立进程复跑 **87 条 0 内容差异**（仅 `stage_ms` 不同），且与归档 `run_20260913_postaudit` **逐字段一致**。
历史 run `run_20260913_f2fixed`（15:08/15:09）与 `run_20260913_reviewed`（14:59/15:00）未被覆盖。

## 九、复审新发现的问题

### R1（中等，代码/文档口径）文本侧 `dynasty_bias` 实际不生效

`server/text/searcher.py` 把命中朝代的片段前置，但 F05 随后按 `_score`（min-max 归一化、分数互不相同）
重排文本证据，偏置序被抹掉。以 B03（dual）的 trace 实证：

- tsearch 返回顺序的分数序列为 `0.69, 0.54, 0.41, 0.16, 0.11, 0.0, 0.0, 1.0…`（高分的 raw 片段被压到第 8 位）；
- 融合后文本证据为严格降序 `1.0, 0.972, 0.7261, 0.6909, 0.6517, 0.5403…`，偏置序消失。

图谱侧不受影响：图谱证据不带 score（`_ev_score` 回退 0.0），F05 的稳定排序保留偏置序，
且偏置在策略执行前已重排 `nodes`，可经 `top_k` 截断影响证据集合（L03 的图谱变化主要来自解除硬筛选）。

影响：`features/02`、`data-contract`、`evaluation/README` 里"F03/F04 把命中朝代的节点/片段**排到前面**"的表述，
对文本侧目前只是"tsearch 返回序"，最终排序仍由 F05 决定。

收口方式（二选一）：① 把偏置折进文本 `_score`（会改变融合排序，必须重跑基线并复核 B03/L03）；
② 在文档中如实写成"文本侧为先排序，最终排序由 F05 按相关分决定"。**推荐 ②**（低成本、不扰动基线）。

### R2（小，文档）开发说明 §三 历史与现状混写

`RAGv4-开发说明.md:110-111` 现写"题库 annotation_version=reviewed-2（39 条全部通过）"，但同句引用的 run
是 `run_20260913_reviewed`（其 `meta.json` 为 `reviewed-1`）；§三.3 的表仍是修复前数字
（42.6/75.3/83.3/78.6/57.1/59.2，ok 24/28）却挂着"F10 验收第 3 条"的标题，而当前基线在 §四之二。
建议把 §三 标为"首轮（历史）基线（run_20260913_reviewed，annotation_version=reviewed-1）"
并指向 §四之二 / 阶段总结 §5，或把该表换成 postaudit 数字。

### R3（小，代码）报告失败桶静默截断

`evaluation/report.py:422` 的 `bucket[:10]` 在改为人工评分分桶后开始吞条目：答案桶实际 **39 条只显示 10 条**
（B01…L01），引用桶 **11 条只显示 10 条**（X03 被丢）。建议补一行"（共 N 条，仅列前 10）"或对该小节去掉上限。

### R4（小，文档/代码不一致）单字朝代后缀注释与正则不符

`server/query/classifier.py:85`（docstring）与 `:118`（行内注释）都写后缀为"朝/国/**代**/王朝"，
而 `:122` 的正则为 `(王朝|朝|国)`。实测"清代/宋代/唐代"因此不产生偏置（仅偏置，无功能损害）。
建议补上"代"（复审已验证误命中风险低：会命中的都是"唐代/宋代/清代"这类正当写法，
"朝代/时代/近代/民国/六国/战国/中国"均不命中），或改注释与文档。

### R5（小，代码）旧名 `extract_dynasty_filter` 有误用风险

`server/query/classifier.py:134` 把旧名指向新函数，但名字里的 filter 会诱导调用方把返回值塞进
`Filters(dynasty=...)` —— 正是本次修掉的缺陷。当前仅测试使用，建议加"仅兼容测试、勿用于 filters"的
注释，或删除旧名并把两处测试调用改为 `extract_dynasty_mentions`。

### R6（小，数据卫生）题库条目级 `annotation_version` 仍为 `draft-0`

`questions.jsonl` 39 条的条目字段全是 `draft-0`，而 `questions.meta.json` 是 `reviewed-2`。
该字段无代码读取（报告用 meta 层），属死字段，但审计者打开 jsonl 会读到冲突值。
建议在 `bank_apply`/`save_bank` 时同步，或从 schema 移除。

### R7（nit，三项）

1. `tests/test_evidence_ids.py:51` 的哈希回退断言 `assert "h" in evidence_id_of(row3)` 偏弱
   —— 建议断言"以 `graph_` 开头 + 两次调用相同 + 与有行号版本不同"。
2. `RAGv4-阶段工作总结.md:130` 的括注"差异来自 F02 修复消除了 4 题的错误筛选"现在跨了两轮修复
   —— 引用携带 61.9%→63.7% 实际来自软偏置改造（B03），建议补一句说明。
3. 报告"引用携带(文本)"提升的题目仅 B03 一题，均值变化 +1.8pp 全部由它贡献；小样本下不必过度解读。

## 十、复审确认"不是问题"的点

1. **"朝代"一词不会被单字规则误判为朝代"代"**：该词是"朝"在前，正则要求单字后紧跟 朝/国/王朝，故不命中；
   T03 的 `dynasty_bias` 实测为 `[]`。
2. **前端安全**：`frontend/src/stores/session.ts:317` 只读 `data.entities`，entities 事件新增
   `dynasty_bias` 不影响前端，无需改动。
3. **缓存键稳定**：`cache.py:33` 用 `json.dumps(filters, sort_keys=True)`，偏置作为 `filters` 下的列表参与键；
   无偏置请求的键与改造前完全一致，旧缓存不污染。
4. **显式筛选仍硬过滤，且与偏置去重**：`understand.py` 先从 bias 中剔除已在 `filters.dynasty` 里的朝代。
5. **历史 run 未被覆盖**：`run_20260913_f2fixed` 与 `run_20260913_reviewed` 的文件时间戳与内容均未变。
6. **题库与词典未被扰动**：`check-bank` 提示仍 7 条，`questions.meta.json` 仍 reviewed-2，
   治理侧 `dicts.json` 未改（`X朝/X国` 识别改在代码里，未污染 F01 下拉数据）。

## 十一、复审结论与建议顺序

本轮整改**可以收口**：T1 使评分链路可追溯、T2 消除了"硬筛选清空检索"这一类缺陷并带回归守护、
T3/T4 让报告可读且口径自洽、T6/T7 提升了评测与测试的可靠性，全部有实测证据支撑，且未引入指标回归。

建议后续处理顺序：**R1（口径收口，二选一）→ R2（开发说明 §三 标注为历史）→ R3（报告桶截断提示）→
R4/R5（一行代码级）→ R6/R7（数据卫生与文案）**。R1 若选择方案 ①（偏置并入文本分），
必须重跑基线并复核 B03/L03 的评分类字段；选择方案 ② 则只改文档，不影响基线。

## 十二、复审未覆盖范围

未复核真实 LLM 与向量通道（环境无 key、`embeddings.npy` 仍为占位）；未复核前端渲染；
未对 `new/` 目录（飞书 Hermes 渠道化文档）做任何检查；B03/L03 的复评结论按其 notes 采信，
未逐条回溯到原始关系行（前两轮的 35 条已做过引用真实性回查）。

---

# 第三轮：R1–R7 验收（2026-09-13 同日）

> 验收对象：针对本文档"复审轮"所列 R1–R7 的整改提交。
> 验收方法：读码 + 44 用例实跑 + **两次独立进程复跑对拍** + 与归档基线逐字段 diff +
> 基线完整性/评分溯源复核 + R4 规则边界探测（15 正例 / 12 反例）。
> 验收结论：**R1–R7 全部落地**，本轮改动对题库评测行为**零扰动**（87 条与基线逐字段一致）；
> 新发现 A1–A4（均为文档一致性/测试覆盖级小项）。
> 基线：`run_20260913_postaudit` 保持为当前正式基线，其 `traces.jsonl`（15:39:30）与
> `scores.jsonl`（15:39:58）未被重写，仅 `report.md` 用新 `report.py` 重新生成。

## 十三、R1–R7 逐项验收

| 项 | 结论 | 复核证据 |
| --- | --- | --- |
| R1 文本侧偏置口径 | ✅（方案②） | `features/02`、`data-contract`、`evaluation/README` 三处均写明"F03 图谱侧生效（策略前重排节点，可经 top_k 影响证据集合）；F04 文本侧只是检索返回序，最终顺序由 F05 按相关分决定（当前不改变最终排序）"，并给出未来可选方案。未采用的方案①（偏置折进 `_score`）已注明需重跑基线，处理方式合理 |
| R2 开发说明 §三 混写 | ✅ | §三 标题改为"首轮基线跑通结果（**历史存档**：run_20260913_reviewed，annotation_version=reviewed-1）"，并加引用块指向 §四之二 / 阶段总结 §5 的当前基线；§三.3 表明确标注为"**修复前**数字（ok 24/28）" |
| R3 失败桶静默截断 | ✅ | `report.py:425` 仅在 `len(bucket) > 10` 时补"（共 N 条，此处仅列前 10；完整明细见同题的人工评分/自动归因表）"；重生成报告实测：答案桶"共 39 条"、引用桶"共 11 条" |
| R4 朝代后缀补"代" | ✅ | 正则改为 `([chars])(王朝|朝|国|代)` 并加解释注释；边界探测：15 个正例全部命中（清代/宋代/唐代/明代/周代/隋代/元代/金代/辽代/秦国/楚国/燕国/齐国/陈国/清朝末年），12 个反例全部不命中（朝代/时代/近代/民国/六国/中国/全国/万国/清明/交代/后代/古代/现代/世代）；原缺陷触发词"秦为什么""楚军"仍为 `[]`；**题库 87 条 trace 与基线逐字段一致**（无题目受后缀改动影响） |
| R5 旧名删除 | ✅ | `extract_dynasty_filter` 已从代码中彻底移除（全仓 `*.py` grep 为空），测试改用 `extract_dynasty_mentions` |
| R6 条目级版本 | ✅ | `questions.jsonl` 39/39 条目 `annotation_version=reviewed-2`（与 `questions.meta.json` 一致）；**并做了代码级防复发**：`review_bank.py:565-569` 在传入 `--annotation_version` 时同时写元数据与条目级 |
| R7 小疵 | ✅ | 哈希回退断言加强为"确定性 + 以 `graph_{表}_h` 前缀 + 与有行号版本不同"；阶段总结 §5.1 补"差异分两轮来源"并注明"引用携带提升（+1.8pp）几乎全部来自 B03 一题，属小样本、不宜过度解读" |

## 十四、本轮验证数据

- `pytest tests -q` → **44 passed**（无新增失败）；`check-bank` → 结构通过 + **7 条**提示。
- 两次独立进程复跑 → **87 条 0 内容差异**；与归档基线 `run_20260913_postaudit` 对拍 → **87 条 0 差异**
  （证明 R4 的后缀扩展未改变任何题库题目的检索结果）。
- 基线完整性：`run_20260913_postaudit/traces.jsonl` 4554881 字节 / 15:39:30、`scores.jsonl` 191870 字节 / 15:39:58，
  均未被本轮改动重写；`meta.json` 的 `generated_at=2026-09-13T15:39:30` 与 configs/suites 未变。
- 评分溯源复核：39 条已评分，答案 0 correct / **23** partial / **16** incorrect，引用 28 supported / 3 unrelated / 8 unsupported；
  来源分布 **33 沿用 + 4 一轮复评 + 2 二轮复评**，与文档一致。
- 报告（重生成）：主套件 dual 42.6/85.4/94.6/86.3/**63.7**/67.6（ok 28/28）、text-only 42.6/-/94.6/94.6/**66.7**/71.7（ok 28/28）；
  类别列非空；失败桶按人工评分分桶并带截断提示。

## 十五、本轮新发现（A1–A4）

### A1（小，文档与事实不符）"代码处补注释"未落地

`docs/changes/20260913-ragv4-review-summary.md` §七 的 R1 行写"（features/02、data-contract、
evaluation/README）；**代码处补注释**"，但 `server/text/searcher.py:75-76` 与 `server/text/__init__.py`
的 docstring 仍只写"只把命中朝代的片段排到前面（稳定排序，不剔除任何结果）"，没有"该顺序会被 F05
按 `_score` 重排覆盖"的说明（全仓 `server/text/*.py`、`server/query/*.py` 中无 F05 相关注释）。
建议补一行注释（这正是未来维护者最需要看到的一句），或改掉该处表述。

### A2（小，文档基线不一致）阶段总结仍有 4 处指向旧基线/旧函数名

`RAGv4-阶段工作总结.md`：

| 位置 | 现状 | 应改为 |
| --- | --- | --- |
| `:177-178`（§6 复现代码块） | `report --run …/run_20260913_f2fixed --scores …/run_20260913_f2fixed/scores.jsonl` | 指向 `run_20260913_postaudit`（同文件 74/122 行已声明它是当前正式基线） |
| `:215`（§七） | "再迁移到**当前基线** `run_20260913_f2fixed`" | 与 74/122 行冲突；应写 postaudit（f2fixed 现为上一版对照基线） |
| `:251`（审核清单第 6 条） | 比对 `runs/run_20260913_f2fixed/scores.jsonl`；"4 题复评有说明" | 指向 postaudit；复评为 **6 条**（4 + B03/L03） |
| `:257`（审核清单第 7 条） | 读 `classifier.extract_dynasty_filter` 的 diff | 该名已删除（R5）；应写 `extract_dynasty_mentions`（原 `extract_dynasty_filter`） |

### A3（小，测试覆盖）R4 新增的"代"后缀无测试守护

`tests/test_dynasty_filter.py` 的参数化用例只覆盖"朝/国"形式（注释也仍写"单字朝代 + 朝/国 后缀"），
没有"清代/唐代"这类正例与"近代/时代"这类反例。R4 是本轮唯一的**生产行为改动**，却只有外部探测覆盖；
若将来有人把 `代` 从正则里去掉，44 个用例仍会全绿。建议补：

```python
("清代的人口有多少？", ["清"]),
("宋代经济", ["宋"]),
("这是什么时代的事？", []),      # "时代"不是朝代指称
("近代史", []),                  # "近代"不是朝代指称
```

### A4（nit，设计说明）`dynasty_bias` 可能含同义两项

`extract_dynasty_mentions("清朝末年发生了什么", …)` → `['清朝', '清']`：多字别名路径给"清朝"、
单字后缀路径给"清"，两者是不同的标准名。当前只用于集合判定（无害，甚至更宽松），但若将来采纳
R1 的方案①（折进分数）会重复计权。建议在返回前按"同一朝代概念"去重，或在 docstring 注明该现象。

## 十六、第三轮确认"不是问题"的点与边界

1. **R4 无误伤**：12 个常见反例（朝代/时代/近代/民国/六国/中国/全国/万国/清明/交代/后代/古代/现代/世代）
   全部不命中；原两条缺陷触发词"秦为什么…""楚军…"仍为 `[]`；E01 仍为 `bias=['商']` 且不拒答。
2. **改文档不改行为**：本轮除 `classifier` 后缀与 `review_bank` 版本同步外无生产逻辑改动；
   87 条 trace 与基线逐字段一致，说明题库口径未受影响。
3. **基线未被污染**：`postaudit` 的 traces/scores 未被重写，报告重生成只反映 `report.py` 的展示层改动。
4. **口径三处一致**：`features/02`、`data-contract`、`evaluation/README` 对"图谱侧生效、文本侧不生效"
   的表述一致；未发现残留的"文本侧已生效"旧说法（`开发说明:189`、`总结:115` 的"F03/F04 排序前置"
   属机制描述，与三处口径不冲突，可选补一句文本侧说明）。

本轮边界：仍未覆盖真实 LLM/向量通道与前端渲染；A1–A4 均不影响已发布的任何结论数字。

**三轮累计**：T1–T8 与 R1–R7 均已整改验收；剩余事项见下方"收口轮"汇总（A1–A6）。

---

# 收口轮：v4 阶段剩余事项汇总（2026-09-13，只审不改）

> 说明：本轮**只做审核与归纳，未改动任何代码、数据或其他文档**。
> 结论：**v4 阶段无阻塞性问题，可以收口进入 RAGv5**。剩余 6 项（A1–A6）全部为
> 文档一致性 / 测试注释级，均不影响已发布的任何结论数字。
> 除这 6 项外，本次收口扫描未发现新的代码、数据或结论级问题。

## 十七、v4 剩余待办汇总（A1–A6）

| 项 | 内容 | 位置 | 代价 | 阻塞 v5 |
| --- | --- | --- | --- | --- |
| A1 | changes 文档声称"代码处补注释"但未落地：两处 docstring 没有"该顺序会被 F05 按 `_score` 重排覆盖"的说明 | `docs/changes/20260913-ragv4-review-summary.md` §七 R1 行；`server/text/searcher.py:75-76`、`server/text/__init__.py` | 一行注释或改表述 | 否 |
| A2 | 阶段总结 5 处未跟上新基线/已删除的函数名（详见第三轮 §十五） | `RAGv4-阶段工作总结.md` `:58`、`:177-178`、`:215`、`:251`、`:252` | 5 处文字 | 否 |
| A3 | R4 新增的"代"后缀无测试守护（参数化用例只覆盖"朝/国"，注释也仍写"朝/国 后缀"） | `tests/test_dynasty_filter.py:30-44` | 补 4 条用例 | 否 |
| A4 | `dynasty_bias` 可能含同义两项（"清朝末年"→`['清朝','清']`）；当前只用于集合判定（无害），但若将来采纳 R1 方案①（折进分数）会重复计权 | `server/query/classifier.py::extract_dynasty_mentions` | 去重一行或 docstring 注明 | 否 |
| **A5** | **审计链最后一环**：`review_report.md` §2（引用真实性）、§4.3(4)（"检索到位、回答未用"14 条）与"辅助脚本"段指向"同目录"的 5 个文件 —— `_citation_audit.txt`、`_citation_topics.txt`、`_entity_coverage.txt`、`_verify_citations.py`、`_fill_sheets.py` —— 实际只存在于**被 `.gitignore` 忽略**的 `logs/review-archive/review-20260913/`，durable 的 `data/eval/20260904_v2/review/` 中没有。"未发现编造引用"的直接明细证据即 `_citation_audit.txt`。同类的还有 `bank_review.csv`（空白输入表，可用 `scripts/review_bank.py bank-export` 再生成，损失不实质） | `data/eval/20260904_v2/review/`（缺 5 文件） | 复制 5 个小文件，或改 review_report 指针并注明 `logs/` 不入库 | 否（但建议关掉） |
| A6 | 测试卫生两处：① 注释"初版 draft-0：全部未审核是当前真实状态"已过时（现 39/39 reviewed）；② `assert q.category in GoldQuestion.__dataclass_fields__ or True` 是**恒真断言**（`or True`），无校验力 | `tests/test_bank.py:51`、`:55` | 2 行 | 否 |

合计约 15 分钟，均为文档/注释与测试用例层面的收尾，不涉及生产逻辑。

## 十八、不算"问题"、但 v5 开发文档必须继承的边界

以下均为 v4 已量化的**能力边界与已知短板**（不是缺陷，是"当前环境/规则下的固有结果"），
v5 的开发说明应直接引用，而不是重新发现：

1. **能力边界**：无 LLM key（`llm_used=false`，F06 走离线摘要回答器 —— "0 correct"是该形态所致
   而非检索失败）；向量索引 `embeddings.npy` 仍为 (0,0) 占位、`text_mode=keyword`。
2. **已量化待修的质量问题**：拒答规则只覆盖"无证据/无共享词"两类（X01–X03 应拒答却作答）；
   F04 长改写下 AND 优先必然失效（召回全部由 OR 兜底承担）；`event_type` 筛选把 raw/evidence
   整批剔除（F01 文本召回 100%→0%）；`check-bank` 7 条标注词词典未命中（含 T01「前260年」、
   B03「急于求成」、「夏桀」「夏朝」「秦朝」「208年」「三国鼎立」）。
3. **审核口径边界**：题库审核与全部 39 条评分（含 6 条复评）均由 AI 代理执行，非自然人；
   审核报告 §5.2 的 6 项存疑待人工确认；"两评委背靠背 + 仲裁"流程未落地。
4. **v5 复评的重点样本**：审核报告 §4.3(4) 的 **14 条"检索到位、回答未用"**题 —— 接入真实 LLM
   后应重点看是否转为 `correct`；F08 示例题应从 `reviewed-2` 通过题中选，并剔除评分 `incorrect`
   的题（复评后仍 incorrect 的有 B02/E01/R05/T01/T03/R02/R04 等）。
5. **评测口径资产**：耗时指标只在 trace 的 `stage_ms` 里、未进报告正文；`expected_docs` 多数留空。

## 十九、开工前建议：先把 v4 提交 Git

截至本报告，**v4 的 31 项改动（19 个已跟踪文件被修改 + 12 项新增未跟踪）全部未提交**，
分支仍是 `ragv3-frontend`，最后一次提交为 RAGv3 收口。v5 开工后，v4 的代码、题库、基线、
审核证据会与 v5 改动混在同一工作区，难以回溯与回滚。建议提交时注意：
`git add data/eval` 会带上题库 `questions.jsonl`、`questions.meta.json` 与 `review/` 下的归档表
（`data/eval/*/runs/` 已被 `.gitignore` 忽略，不会入库），`tests/`、`evaluation/`、
三个 scripts 与两份 changes 文档同在待提交清单内。

## 二十、v5 开发文档的现成输入

- 任务骨架：`docs/RAG_v1/后续阶段规划.md` §三（任务与模块、验收对照、前置与边界、
  "向量库与 RAG 框架取舍"决策留痕）+ `docs/features/08-demo-mode.md`。
- v4 产物：题库 `reviewed-2`（39/39）、当前基线 `run_20260913_postaudit`（87 条 trace +
  报告 + 评分）、评分口径（`evaluation/grading.py` 与开发说明附录一）。
- 对照基线：v5 的效果对比应以 `run_20260913_postaudit` 为"无 LLM/关键词通道"参照，
  复用同一题库重跑（`llm_used=true`）即为可对比实验。

## 二十一、审核轮次累计与覆盖范围

| 轮次 | 提出 | 状态 |
| --- | --- | --- |
| 首轮审核 | T1–T8（溯源、F02 硬筛选、报告列、桶口径、陈旧状态、top_k、测试守护、文档小疵） | 全部整改验收 |
| 复审轮 | R1–R7（文本侧偏置口径、§三混写、桶截断、后缀缺"代"、旧名误用、条目级版本、三项 nit） | 全部整改验收 |
| 第三轮 | A1–A4（注释未落地、总结 5 处旧基线/旧函数名、后缀测试覆盖、"代"同义重复） | 已整改验收（见第四轮 §二十二） |
| 收口轮 | A5–A6（review 附属文件未归档到 durable 目录、test_bank 注释与恒真断言） | 已整改验收（见第四轮 §二十二） |
| 第四轮 | B1–B5（v4 计数遗漏 1 处 + v5 规划说明准确性 4 处） | 待处理（不阻塞 v5 开工） |

覆盖范围与限制：审核验证的对象是"评测链路 + 生产检索链路的 RAGv4 相关改动 + 题库/评分/
基线数据 + 文档一致性"（第四轮起含 `RAGv5-规划说明.md` 的事实性核对）；**未覆盖**真实 LLM 与
向量通道（环境无 key、索引为占位）、前端渲染、`new/` 目录（飞书 Hermes 渠道化文档）；
人工评分的取值本身按其 notes 采信（前两轮已做过引用真实性回查与归档比对）。

---

# 第四轮：A1–A6 验收 + RAGv5 规划说明审核（2026-09-13 同日）

> 对象：① 收口轮所列 A1–A6 的整改；② 新增 `docs/RAG_v1/RAGv5-规划说明.md` 与其带动的文档更新
> （`README.md`、`docs/README.md`、`features/08-demo-mode.md`、`后续阶段规划.md`、v4 两份主文档）。
> 方法：逐项读码/读文档 + **49 用例实跑** + **两次独立进程复跑对拍** + 基线完整性核对 +
> v5 文档事实性核对（文件路径 / 开关 / 数字逐条到源码与快照验证）。
> 结论：**A1–A6 全部落地，v4 侧零回归**；v5 规划说明**事实性基本准确**（8 处关键声明核对通过），
> 新发现 B1–B5：1 处 v4 计数遗漏 + 4 处 v5 文档准确性问题，其中 **B2 会影响 v5 的实际开发路径**。

## 二十二、A1–A6 逐项验收

| 项 | 结论 | 复核证据 |
| --- | --- | --- |
| A1 代码注释 | ✅ | `server/text/searcher.py:77-79` 与 `server/text/__init__.py:43-44` 均补上"本函数返回顺序不是最终证据顺序 —— F05 按 `_score` 重排，文本侧偏置不影响最终排序（图谱侧在 F03 策略前生效）"，并指向 `docs/features/02-entity-linking.md` |
| A2 总结旧引用 | ✅ | `:58` 与 `:260` 改用 `extract_dynasty_mentions` 并注明旧名已删（R5）；`run_20260913_f2fixed` 仅作为"上一版基线/历史对照"出现在 `:76`、`:300`（表述正确）；`:177-178`、`:215`、`:251` 已指向 postaudit |
| A3 后缀测试 | ✅ | 新增正例 `清代的人口有多少？→['清']`、`宋代经济→['宋']`，反例 `这是什么时代的事？→[]`、`近代史→[]`，并补 A4 用例 `清朝末年发生了什么？→['清朝']`；用例总数 44 → **49** |
| A4 同义去重 | ✅ | 实现为"单字后缀命中区间与多字别名命中区间重叠则跳过"（`classifier.py` 的 `covered` 跨度判定），语义正确；实测 `清朝末年→['清朝']`（不再追加"清"），`商朝→['商']`、`秦朝→['秦']` 等无别名可覆盖的形式不受影响 |
| A5 附属文件归档 | ✅ | `data/eval/20260904_v2/review/audit/` 新增 6 文件（`_citation_audit.txt`、`_citation_topics.txt`、`_entity_coverage.txt`、`_verify_citations.py`、`_fill_sheets.py`、`bank_review_input.csv`）；与 `logs/review-archive/` 原件 **md5 逐一比对全部一致**；`review_report.md` 的 §2、辅助文件段与 §4.3(4) 指针均改为 `audit/…`，并注明已归档到 durable 目录 |
| A6 测试卫生 | ✅ | `tests/test_bank.py` 注释更新为"审核状态随 annotation_version 演进（当前 reviewed-2：39/39 通过）"；恒真断言 `… or True` 改为 `assert q.category in CATEGORIES`（真校验） |

## 二十三、v4 全量重验证（本轮从零跑，不沿用前轮结论）

| 验证项 | 结果 |
| --- | --- |
| `pytest tests -q` | **49 passed**（+5 用例，无失败） |
| `check-bank` | 结构校验通过 + **7 条**词典提示（与之前一致） |
| 两次独立进程复跑 | **87 条 0 内容差异** |
| 与归档基线 `run_20260913_postaudit` 对拍 | **87 条 0 差异** —— 证明 **A4 的跨度去重未改变任何题库题目**的检索/回答行为 |
| 基线完整性 | `traces.jsonl` 4554881 B / 15:39:30、`scores.jsonl` 191870 B / 15:39:58，未被重写；`runs/` 下仍只有 3 个 run；`latest.txt` 内容仍为 postaudit（时间戳被 16:13 的某次调用刷新，内容未变） |
| 评分溯源（复核） | 39 条已评分；答案 0/23/16、引用 28/3/8；来源 33 沿用 + 4 一轮复评 + 2 二轮复评，与文档一致 |

## 二十四、RAGv5 规划说明审核

### 24.1 核对为正确的事实性声明

| 声明 | 核对结果 |
| --- | --- |
| 前端示例题硬编码在 `frontend/src/components/chat/ChatPane.vue:11`（`EXAMPLES` 数组） | ✅ 位置正确；实际 4 条：赤壁之战的主帅是谁？/ 介绍长平之战。/ 井陉之战发生在什么时候？/ 牧野之战与武王伐纣有什么关系？ |
| 其中"牧野之战…"未纳入已审核题库 | ✅ 题库中检索"牧野"命中 0 条 |
| F02 有 `enable_llm` 兜底开关（词典未命中 → LLM） | ✅ `server/query/understand.py:38` 存在该参数，`server/runtime.py:84` 当前传 `enable_llm=False` |
| `server/text/__init__.py` 的 vector 分支当前返回空 | ✅ 属实（`elif eff_mode == "vector": results = []`） |
| `data/index/vectors.py` 已具备 encode 路径、`server/text/scoring.py` 负责模式判定 | ✅ 两文件均存在，`scoring.resolve_mode` 含 vector/hybrid→keyword 降级 |
| 地点坐标覆盖率 0 | ✅ 快照 5316 个地点、0 个有经度 |
| 1,215 组同名实体 | ✅ `governance_report.name_duplicates.groups = 1215`（涉及 3848 条记录） |
| §五 基线数字表与评分分布 | ✅ 与 postaudit 报告逐项一致（42.6/85.4/94.6/86.3/63.7/67.6 与 42.6/-/94.6/94.6/66.7/71.7；0/23/16 与 28/3/8） |
| 14 条"检索到位、回答未用"清单 | ✅ 与 `review/review_report.md` §4.3(4) 的 14 条完全一致 |

### 24.2 新发现（B1–B5）

| 项 | 级别 | 内容 | 位置 |
| --- | --- | --- | --- |
| **B1** | 小（v4 计数） | 根 `README.md:47` 仍写"共 **44** 个"测试用例；实际 49（`开发说明`、`阶段工作总结` 已同步为 49，仅 README 漏改） | `README.md:47` |
| **B2** | **小但影响 v5 路径** | §三 T6 写"`evaluation/`（无需改口径；用 `run --llm` 与不同 `EvalConfig` 跑对照）"，但 `evaluation/chain.py:186` 把 `mode="keyword"` **写死**、`EvalConfig` 也没有 `mode` 字段 → §2.3 验收第 4 条要求的 `vector` / `hybrid` 对照**按现状无法执行**。需给 `EvalConfig` 加 `mode`（并把 `mode` 透传给 `text.search`），或说明改用服务端路径跑对照 | `RAGv5-规划说明.md` §三 T6、§2.3-4；`evaluation/chain.py:186` |
| **B3** | 小（事实错误） | §五 的"需规避 incorrect 题"括号里把 **`F02` 列为 incorrect** —— 实际 `F02` 是 `partial`（淝水之战的交战经过）。incorrect 全集共 **16 条**：`B02、E01、F01、L01、L02、M03、M07、R02、R03、R04、R05、T01、T03、X01、X02、X03`（正文列的 7 条是子集，用"等"覆盖，但显式清单与括号自相矛盾） | `RAGv5-规划说明.md` §五 |
| **B4** | 小（命名） | §2.3 第 4 条的配置名 `text-only-and / and_or / vector / hybrid`：`and_or` 不是评测配置标签（现存标签为 `dual / text-only / text-only-and / text-only-or`），且 `vector`/`hybrid` 目前无对应 `EvalConfig`（依赖 B2）。建议写成 `text-only（and_or）/ text-only-and / vector / hybrid` 并注明需先补 `mode` | `RAGv5-规划说明.md` §2.3-4 |
| **B5** | nit（三项） | ① §三 T3 写 `scripts/build_index.py --embeddings`，实际参数是 **`--no-embeddings`**（默认即构建向量，应写"不加 `--no-embeddings`"）；② §一/§五 引用"审核报告 §4.3(4) 的 14 条" —— 该小节在**受托审核报告** `data/eval/…/review/review_report.md` §4.3(4)，不在本文档；③ 前置描述写"审核报告…三轮整改完毕"，现已四轮记录；④ §2.5-4 的 `audit_decisions.json` 目前不存在（它是 `scripts/apply_audit.py --decisions` 的输入契约），建议写成"为 1,215 组同名实体做审核回填（产出 `audit_decisions.json` 交 `apply_audit.py`）" | `RAGv5-规划说明.md` §三 T3、§一/§五、前置行、§2.5-4 |

补充（非问题，供 v5 选型参考）：前端 4 条示例题中 **`井陉之战` 也不在题库**（与 `牧野之战` 同样未审核），
文档只点了牧野；按 §2.1 增补验收第 4 条改造时，四条示例题会一并被替换，因此不影响规划，但
"未审核示例题有 2 条"这个事实值得写进动机段。

## 二十五、第四轮结论与建议

1. **v4 侧**：A1–A6 全部整改验收，49 用例、基线零回归、基线文件未被重写 → v4 **可以收口**；
   唯一残留是 B1（根 README 的用例数 44 → 49，一行）。
2. **v5 侧**：规划说明结构完整（目标/需求与验收/任务分解/前置与待确认/资产继承/风险/里程碑/边界/产物），
   事实性基本可靠（9 项关键声明核对通过）。**开工前建议先把 B2 写进任务分解**（`EvalConfig` 加 `mode`
   并透传），否则 §2.3 的向量对照评测会在执行时卡住；B3/B4/B5 为文字与命名修正，可与开工同批处理。
3. **流程**：Git 仍未提交，当前 33 项（**M 20 + ?? 13**，本轮新增 `features/08-demo-mode.md` 的修改与
   `RAGv5-规划说明.md` 新文件）；v5 开工前先固化 v4 的建议依然成立（见 §十九）。
4. 本轮审核**未改动任何代码、数据**；审核报告本身为本轮唯一写入对象。

---

# 第五轮：v4 功能复核 + RAGv5 规划说明深度分析（2026-09-13 同日）

> 对象：① B1–B5 整改（含 `EvalConfig.mode` 与新增用例）；② `RAGv5-规划说明.md` 的完备性、
> 可执行性与技术风险分析（逐项核对到代码）。
> 结论：**B1–B5 全部落地、v4 零回归**（50 passed、87 条与归档基线 0 差异、基线文件未被重写）；
> v5 规划完备性良好，但发现 **3 处任务分解缺口（C1–C3）与 2 处技术风险（R1–R2）**，
> 建议在 v5 开工前补入对应任务。
> 本轮只审不改，审核报告为唯一写入对象。

## 二十六、B1–B5 验收（v4 侧）

| 项 | 结论 | 复核证据 |
| --- | --- | --- |
| B1 用例数 | ✅ | 根 `README.md:47` 改为"共 **50** 个"（B2 新增用例后实际计数为 50）；`开发说明:71/104`、`阶段工作总结:50/168/194/259/294` 均已同步为 50 |
| B2 评测 mode | ✅ | `evaluation/chain.py:33` 增 `mode: str = "keyword"`，`:189` 透传 `mode=cfg.mode` 给 `text.search`，`describe` 体现非关键词模式；新增 `test_text_mode_passthrough_and_autodowngrade`（用 `dataclasses.replace(CONFIG_DEFAULT, mode="vector")` 断言无向量时自动降级 keyword，精准守护本项） |
| B3 incorrect 清单 | ✅ | v5 规划说明 §五 改为"`incorrect` 全集 16 条"并显式注明"`F02` 是 `partial` 而非 incorrect，勿误列" |
| B4 配置命名 | ✅ | 改为 `text-only（关键词，and_or）/ text-only-and / vector / hybrid`，并注明 `EvalConfig` 已支持 `mode`、vector/hybrid 生效依赖向量落地 |
| B5 四项文字 | ✅ | `build_index.py` 描述改为"默认即构建向量，仅无密钥时才加 `--no-embeddings`"；14 条出处改指"受托审核报告 `review/review_report.md` §4.3(4)"；前置行改"四轮（T1–T8 / R1–R7 / A1–A6 / B1–B5）"；`audit_decisions.json` 说明改为"产出…交 `scripts/apply_audit.py --decisions`"；并补上"井陉之战亦不在题库" |

## 二十七、v4 功能复核（第五轮，从零跑）

| 验证项 | 结果 |
| --- | --- |
| `pytest tests -q` | **50 passed**（+1 用例，无失败） |
| `check-bank` | 结构校验通过 + **7 条**词典提示 |
| 两次独立进程复跑 | **87 条 0 内容差异** |
| 与归档基线 `run_20260913_postaudit` 对拍 | **87 条 0 差异** —— 证明新增 `EvalConfig.mode` 字段未改变默认（keyword）路径行为 |
| 基线完整性 | `traces.jsonl` 4554881 B / 15:39:30、`scores.jsonl` 191870 B / 15:39:58，未被重写；`runs/` 仍 3 个 run、`latest.txt` 内容仍为 postaudit |

## 二十八、RAGv5 规划说明分析

### 28.1 完备性（对照需求源）

- F08 三条验收标准全部纳入 §2.1，并新增 3 条增补验收（示例题来自已审核题库、无 key 可运行、
  能力分类标签）；F08 的两个待确认项已登记在 §四-4 ✓。
- F06（真实 LLM）、F04/F11（向量）、部署形态、v4 遗留质量优化各有专节；v4 报告 §十八 的
  边界清单（拒答缺口、AND 失效、筛选元数据、同名实体回填、坐标）逐条落到 §2.5 ✓。
- §五"数据与评测口径继承"把题库/基线/14 条复评样本/选型规则/评分口径写全，可作为 v5 对照实验的
  直接依据 ✓。

### 28.2 可执行性核查（文档声明 → 代码实况）

| 文档中的依赖 | 代码实况 | 影响 |
| --- | --- | --- |
| `/api/health` 可用（§2.1-1 验收入口） | ✅ 已存在（`server/api.py:141`） | 可直接用作冒烟入口 |
| 限流按 `RATE_LIMIT_PER_MINUTE` 复核（§2.4-3） | ✅ 已实现（`api.py:115-131` 进程内滑动窗口，默认 30/min） | 属"复核"而非新建；建议把"压测 60 次/分钟"改为"按默认 30/min 构造超限请求"更准确 |
| `demo_mode` 接行为（T1） | ⚠️ 仅 `config/settings.py:65/130` 有定义，`server/` 与 `frontend/` 均未使用 | T1 的"接行为"实为**新建**，工作量高于"接线" |
| `llm_client` token 级回调已预留（T2） | ✅ `server/generate/llm_client.py:52-56` 有 `stream_chat(messages, on_delta)`；但 `server/sse.py:219` 现在传 `on_delta=lambda _: None` 并用 `_chunk_answer_stream` 假流式 | 方向正确；需把 `on_delta` 接到 SSE 帧 |
| `vectors.py` encode 路径、`scoring.resolve_mode`（T3） | ✅ 均存在；`text/__init__.py` 的 vector 分支返回空 | 待实现向量检索与 hybrid 打分 |

### 28.3 任务分解缺口（建议开工前补入）

| 编号 | 缺口 | 说明与建议 |
| --- | --- | --- |
| **C1** | F02 的 LLM 兜底**尚未实现**（T2 未列） | `server/query/understand.py:42` 只把 `enable_llm`/`llm_client` 存为属性，`understand()` 主流程**没有任何 LLM 分支**；T2 现在写的是"把 llm_client 传给 F02 并开 `enable_llm`，按开关"，会让人以为翻开关即可。建议 T2 增加条目：「在 `understand.py` 实现词典未命中 → LLM 实体识别/类型判定的兜底路径 + 失败降级 + 用例」。（另见 §二十六 之外的 v4 文档缺陷 **C1'**：`understand.py:12` 的模块 docstring 声称该兜底"调用 deepseek 做实体识别/类型判定"，与 `server/query/README.md`"不含 LLM 实现"矛盾，属 v4 遗留文档不准确） |
| **C2** | 生产侧文本模式**无接线点**（T3 未列） | `contracts/request.py` 的 `QueryRequest` 无 `mode` 字段；`server/sse.py:161` 把 `mode="keyword"` 写死；`server/runtime.py:108` 的 `meta["text_mode"]` 亦为写死的 `"keyword"`；`config/.env.example` 无文本模式开关。仅实现 `text/__init__.py` 的 vector 分支**不会让线上走向量**。建议 T3 补三处：`config/settings.py`（如 `TEXT_MODE` 默认 keyword）、`server/sse.py`（按配置传 mode）、`server/runtime.py`（`meta.text_mode` 反映真实模式） |
| **C3** | 缓存键未含文本模式（T6/T3 未提） | `server/generate/cache.py:24` 的 `cache_key(rewritten, history, filters, source_version, model)` 不含 mode。若 mode 做成"按请求可切"，向量请求会命中关键词模式写入的缓存（v4 的 `dynasty_bias` 已有先例可照做）；若做成"部署级全局开关"，则需在文档里写明该前提 |

### 28.4 技术风险（建议写入 §六 风险表）

| 编号 | 风险 | 建议 |
| --- | --- | --- |
| **R1** | hybrid 的**分数量纲不一致**：关键词分是 min-max 后的 bm25 反值、向量分是余弦相似度，而 F05 又按问题类型权重做图谱/文本重排；不先定义"向量分归一化 + hybrid 融合口径"，容易出现"看起来生效但排序不可解释"，且会连带影响 citation 顺序与评分 | 在 T3 显式增加一条"hybrid 归一化与融合口径（含与 F05 权重的衔接）"，并纳入 §2.3-4 的对照验收标准 |
| **R2** | 真实 LLM 的评测随机性：`run --llm` 下回答随机，单次运行的分布不足以支撑"必须优于基线"的结论 | §六 已提"固定模型版本与温度、注明随机性"，建议再补"同一配置 2–3 次取样取分布"（或明确接受单次 + 置信边界标注） |

### 28.5 里程碑与流程

M0（Git 固化 + 待确认项）→ M1 向量线 → M2 LLM 线（可并行）→ M3 F08 → M4 部署 → M5 质量优化与收口的顺序合理，
依赖关系（M1/M2 互不阻塞、M3 依赖 M1/M2 的稳定性）正确。**唯一的流程前置仍未完成：Git 提交 33 项（M 20 + ?? 13）。**

## 二十九、第五轮结论

1. **v4 侧**：B1–B5 全部落地，50 用例、与基线 0 差异、基线文件未被重写 → v4 **功能层面可判定收口**；
   唯一残留是 v4 文档不准确一处（**C1'**：`understand.py:12` docstring 声称 LLM 兜底已实现）。
2. **v5 侧**：规划文档结构完备、可执行性总体良好（§28.2 逐项核查）；开工前建议把 **C1/C2/C3**
   补进任务分解、**R1/R2** 补进风险表——其中 **C2 与 C3 是"线上向量检索能否真正生效"的关键前置**，
   C1 是"F02 LLM 兜底"的真实工作量所在。
3. 本轮审核**未改动任何代码、数据**；审核报告为唯一写入对象。

