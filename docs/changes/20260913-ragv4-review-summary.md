# RAGv4 评审轮总结：题库审核 + 答案评分 + 可复现性修复（2026-09-13）

- 日期：2026-09-13
- 阶段：RAGv4（F10 问答效果评测）评审轮
- 前置：[RAGv4-开发说明](../RAG_v1/RAGv4-开发说明.md)、[20260904-ragv4-summary.md](20260904-ragv4-summary.md)
- 委托审核任务书：[data/eval/20260904_v2/review/REVIEW_GUIDE.md](../../data/eval/20260904_v2/review/REVIEW_GUIDE.md)
- 审核报告（受审方产出）：`data/eval/20260904_v2/review/review_report.md`

## 一、本轮做了什么

1. **题库首轮审核（委托 AI 代理执行）**
   - `reviewed` 结果：**35 条通过、4 条留空**；留空的是 `B01`、`E01`、`R05`、`T03`
     ——四题题目与数据均成立，拒答源于 F02 朝代别名误判（系统缺陷），按任务书建议
     **保留原问句作缺陷复现样本**，待修复后原地复测；
   - `E02` 改写后的要点（去掉数据不支持的"两战皆以少胜多"，改为"曹操均为一方 +
     官渡以少胜多奠定统一北方 + 赤壁奠定三国鼎立"）经核对确认成立；
   - B 类 13 行（M11–M14、R06、T02、L01–L04、F01–F04）逐行核对要点与关系行锚点，
     全部通过；其余 20 行按自动核对结论通过；
   - 审核过程未改动题目内容（`question/expected_entities/gold_notes` 全部零改动），
     只填了 `reviewed`；引用真实性由受审方回查快照（17,700 关系行、1,050 事件卡、
     evidence/原文）逐条核对，未发现编造引用。
   - 回填：`bank-apply`（reviewer=`AI代理审核-ZCode(deepseek-v4-flash)`），
     题库 `annotation_version` 由 `draft-0` 升为 **`reviewed-1`**。
2. **答案/引用人工评分（39/39 条）**
   - 答案正确性：`correct` 0、`partial` 21、`incorrect` 18；
     引用正确性：`supported` 25、`unrelated` 3、`unsupported` 11；
   - 结论：**没有任何一条达到 correct**，根因是本次 run 用离线摘要回答器
     （`llm_used=false`），输出为"关系清单 + 固定长度截断"，不是完整作答；
     证据层面并不差：25/39 引用被判定可支持结论，14 条题的关键证据已进入引用、
     只是回答没有组织成结论（详见审核报告 §4.3）。
   - 三类系统问题被评分确认：4 条应可答却拒答（F02 误判）、3 条应拒答却作答、
     1 条（`F01`）在 filters-on 下引用仅 5 条且无关。
3. **可复现性缺陷修复（本轮新发现，影响 F10 验收第 1 条）**
   - 现象：同一题库连续两次 `run`，`R02`/`E02` 的证据顺序与回答顺序不一致；
   - 根因：`server/query/dictionary_matcher.py` 用
     `sorted(set(...), key=len, reverse=True)` 排列实体名，**等长词并列时顺序取决于
     set 迭代顺序**（受 `PYTHONHASHSEED` 影响），跨进程不稳定；同类写法还存在于
     `server/query/classifier.py::extract_dynasty_filter`（影响 filters 顺序/缓存键）；
   - 修复：并列长度改为按词本身排序（最长优先不变，并列确定）；
   - 验证：两次独立进程完整 run（87 条 trace）**内容级逐字段一致**（回答、证据顺序、
     引用顺序、实体顺序；仅耗时 `stage_ms` 不同）；新增回归测试
     `tests/test_query_determinism.py`（等长并列顺序、最长优先、filter 顺序稳定），
     全量 `python -m pytest tests -q` → **24 passed**；
   - 附带保护：`scripts/gen_draft_bank.py` 增加"题库已存在则拒绝覆盖"（需 `--force`），
     避免草稿重建冲掉人工审核字段。
4. **题库指标改进（采纳审核建议）**：`T01` 标注词补 `前260年`、`B03` 补 `急于求成`
   （此前自动指标无法识别这两题的正确作答）。
5. **正式基线 run**：`run_20260913_reviewed`（修复确定性后重跑 + 人工评分合并）；
   评分迁移已做内容级对拍（与受审 run 的差异仅 `R02`/`E02` 的证据顺序，不影响判分）。

## 二、结果数据（`run_20260913_reviewed`）

主套件 28 题客观指标：

| 配置 | 实体命中 | 图谱命中 | 文本top-k召回 | 融合文本携带 | 引用携带(文本) | 回答覆盖 | ok/n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dual | 42.6% | 75.3% | 83.3% | 78.6% | 57.1% | 59.2% | 24/28 |
| text-only | 42.6% | - | 83.3% | 83.3% | 58.3% | 63.4% | 24/28 |

人工评分分布（39 条）：答案 0 correct / 21 partial / 18 incorrect；
引用 25 supported / 3 unrelated / 11 unsupported。

## 三、边界与说明

- **本次审核为 AI 代理执行**（`reviewer` 已如实标注），不是自然人审核；F10 要求
  "人工审核"，如需正式口径，建议对存疑清单（审核报告 §5.2 共 6 项）由人复核签字。
- 评分基于**离线摘要回答器**的作答形态，不能作为 RAG 最终质量结论；语义质量应在
  接入真实 LLM 后重跑同一题库复评。
- `filter_loss` 评分表只含 filters-on 主形态（报告内的自动指标已含 filters-on/off
  两种变体对比，可量化损耗）。

## 四、后续动作（RAGv5 或优化轮）

1. 修复 F02 朝代别名子串误判（影响 B01/E01/R05/T03 可答性）；
2. 接入真实 LLM 后重跑本题库（`llm_used=true`），重点复测审核报告 §4.3(4) 的
   14 条"检索到位、回答未用"题；
3. 拒答规则补强（实体命中但问题属性不存在的越界问题）；
4. F03 证据 ID 跨 legacy 表重复问题（`graph_{source_row_id}` → 建议带表名）；
5. 若采纳方案②（改写避开触发词），审核报告附录 A 已给出四题改写备选与标注词核对结论。

## 五、跟随动作（评审后修复与复评，同日）

评审结论落地后，按"先修复再复测"的顺序完成两项系统修复（原划归 RAGv5，因直接影响
题库可用性提前到本轮）：

1. **F02 朝代别名子串误判修复**
   - `classifier.extract_dynasty_filter`：只接受**长度 ≥ 2** 且**不与已识别实体
     mention 重叠**的朝代术语；词典里 18 个单字键（秦/楚/代/商/汉/唐…）不再自动
     识别（F01 下拉显式 filters 不受影响，调用方直接合并）；
   - 结果：B01/E01/R05/T03 四题不再被误加 dynasty 筛选，图谱/文本检索恢复
     （4 题由拒答变为正常作答，`reviewed` 补为通过，题库升到 `reviewed-2`，
     39/39 通过）。
2. **F03 证据 ID 跨表重复修复**
   - `graph.search._triple_from_row`：证据 ID 由 `graph_{行号}` 改为
     **`graph_{legacy表名}_{行号}`**（缺行号回退哈希也带表名），保证 17,700 行关系
     全局唯一；`data-contract` 与本文档已同步格式说明。
3. **测试与验证**
   - 新增 `tests/test_dynasty_filter.py`（单字不误判/多字仍识别/mention 重叠跳过）、
     `tests/test_evidence_ids.py`（全量行 ID 唯一 + 运行期检索 ID 唯一且带表名）；
   - 全量 `python -m pytest tests -q` → **44 passed**（含 `test_f02_regression.py`：
     四题不拒答 + 补词典别名后仍不拒答）；
   - 两次独立进程 run 内容级一致（可复现性保持）。
4. **复评与正式基线**
   - 新基线 run：`run_20260913_f2fixed`（修复后重跑 + 评分合并）；
   - 只有 4 题（×2 配置）的回答发生变化；其余 35 题沿用原评分；
   - 4 题按既有评分口径复评：`B01` partial/supported（引用含白起攻韩上党之战卡与
     上党郡关系，回答未组织成因果结论）、`E01` incorrect/supported（引用含"夏亡、
     商汤建商"原文但回答未用）、`R05` incorrect/supported（引用含"巨鹿之战—统帅→
     项羽"但回答未给出主帅）、`T03` incorrect/unsupported（引用中无朝代证据）；
   - 最新评分分布：答案 partial 22 / incorrect 17；引用 supported 28 / unsupported 8 /
     unrelated 3（较修复前：unsupported 由 11 降至 8，4 题里 3 题的关键证据已在引用中）。
   - 复评执行者与口径：`ZCode 复评（F02 修复后）`，沿用委托审核报告 §4.1 的评分口径。

## 六、第三方功能审核整改（2026-09-13 审核报告 T1–T8）

审核报告：`docs/RAG_v1/RAGv4-阶段审核报告.md`（第三方模型，含 17 项复现验证 + 8 项待改）。
本轮逐条整改：

| 项 | 内容 | 处理 |
| --- | --- | --- |
| T1 | 人工评分溯源链补全 | `run_20260913_reviewed` 误标"评分原始依据"已改；`bank_review_filled.csv`/`scores_review_filled.csv`/`scores_review_20260913_143352.csv` 已归档进 `review/`（不受 .gitignore 影响）；开发说明 §四之二 补 4 题复评出处；阶段总结 §七 补 R02/E02 差异性质说明（R02 是**证据集合**变化、E02 仅换序，均不影响判分） |
| T2 | 朝代"硬筛选"语义风险 | **自动识别朝代改为软偏置**（`F02Output.dynasty_bias` → F03/F04 排序前置，不剔除结果）；显式筛选仍硬过滤；恢复 `X朝/X国` 识别（单字朝代 + 朝/国 后缀）；新增 `tests/test_f02_regression.py`（四题不拒答 + **补词典别名后仍不拒答**）；未改治理词典（避免污染 F01 下拉） |
| T3 | 报告"类别"列恒空 | `cli.py` 写入 `category`；`report.py` 对旧 traces 用题库回填 |
| T4 | 失败样例桶标签与来源不一致 | 有评分时按**人工评分**分桶、无评分时按**自动候选**分桶，标题标明来源（`_HUMAN_CATEGORY`/`_AUTO_CATEGORY`） |
| T5 | 陈旧状态行 | 开发说明/README/后续阶段规划等 12 处 `draft-0`/`reviewed-1`/待评分表述统一为终态 |
| T6 | 评测链路图谱 `top_k` 硬编码 | `EvalConfig.graph_top_k`（0 = 取 `settings.query_top_k_graph`，与生产同源） |
| T7 | 证据 ID 测试复制被测规则 | 抽出 `server/graph/search.evidence_id_of()`，被测代码与测试共用 |
| T8 | 文档小疵 | data-contract conflicts 示例改新格式；`cli.py` 注释修正；features/04 补 `keyword_mode`；evaluation/README 差异说明补 `graph_top_k`；阶段总结 git 计数待最终核对 |

整改后重跑基线（软偏置改变了 B03/L03 的证据集合与回答）：
新基线 run = `run_20260913_postaudit`（87 条 trace，内容级可复现 0 差异）；
B03 复评 incorrect→partial、L03 复评说明更新，最终分布 0 correct / 23 partial /
16 incorrect（引用 28 supported / 3 unrelated / 8 unsupported）。
`pytest` 用例数由 34 增至 44；主套件"引用携带(文本)"由 61.9%/64.9% 升至
63.7%/66.7%（dual/text-only）。

审核整改（T1–T8）逐条对照见本节表格；审核报告原文：
`docs/RAG_v1/RAGv4-阶段审核报告.md`。

## 七、复审轮整改（R1–R7，2026-09-13）

复审报告（同一文档文末"复审轮：整改验收"）确认 T1–T8 全部落地，另提 R1–R7。处理结果：

| 项 | 内容 | 处理 |
| --- | --- | --- |
| R1 | 文本侧 `dynasty_bias` 实际不生效（F04 返回序被 F05 按 `_score` 重排覆盖） | 采纳审核推荐**方案②**：文档如实写明"图谱侧生效、文本侧仅为返回序"（features/02、data-contract、evaluation/README）；代码处补注释。方案①（偏置折进 `_score`）会改变融合排序、需重跑基线，未采用，列为后续可选优化 |
| R2 | 开发说明 §三 历史与现状混写 | §三 改标题为"**历史存档**：run_20260913_reviewed，annotation_version=reviewed-1"，加指引到 §四之二/阶段总结 §5 的当前基线 |
| R3 | 报告失败桶静默截断（桶内 39/11 条只显示 10 条） | 超过 10 条时补"（共 N 条，此处仅列前 10）" |
| R4 | 单字朝代后缀注释与正则不符（缺"代"） | 正则改为 `(王朝|朝|国|代)`，清代/宋代/唐代等可识别；误命中风险已复核（"朝代/时代/近代/民国/六国/战国/中国"均不命中） |
| R5 | 旧名 `extract_dynasty_filter` 有误用风险 | 删除旧名，两处测试改用 `extract_dynasty_mentions` |
| R6 | 题库条目级 `annotation_version` 残留 draft-0 | 39 条同步为 reviewed-2；`bank-apply` 以后同步元数据与条目级版本 |
| R7 | 小疵三项 | 哈希回退断言加强（前缀/确定性/与有行号版本不同）；阶段总结 §5.1 补"差异来自两轮修复 + 引用携带提升几乎全部来自 B03 一题、小样本不过度解读" |

R1–R7 均为文档/一行代码级改动，**不改变评测行为**；改后 `pytest` 与基线复跑对拍将在
执行记录中体现（见阶段工作总结 §6 的验证表）。

## 八、收口轮残余项整改（A1–A6，2026-09-13 同日）

审核报告"收口轮"列出 v4 剩余 6 项（均不阻塞 v5），已全部落地：

| 项 | 内容 | 处理 |
| --- | --- | --- |
| A1 | changes 文档称"代码处补注释"未落地 | `server/text/searcher.py`、`server/text/__init__.py` docstring 补"返回序会被 F05 按 `_score` 重排覆盖，文本侧偏置不影响最终排序" |
| A2 | 阶段总结 5 处旧基线/旧函数名 | §2.1 函数名改 `extract_dynasty_mentions`（旧名已删）；§6 复现命令、§七迁移说明、审核清单第 6/7 条改 `run_20260913_postaudit`、复评 6 条 |
| A3 | "代"后缀无测试守护 | `test_dynasty_filter.py` 补 5 例（清代/宋代/时代/近代/清朝末年），用例数 44→49 |
| A4 | `dynasty_bias` 同义重复（"清朝末年"→`['清朝','清']`） | 跨度重叠去重：①多字别名已覆盖的跨度不再走②单字后缀，返回 `['清朝']`；docstring 注明 |
| A5 | 审核附属 5 文件只在被忽略的 `logs/` | 复制到 durable `data/eval/20260904_v2/review/audit/`（含 `bank_review_input.csv` 共 6 文件），`review_report.md` 指针改为 `audit/_citation_audit.txt` 等 |
| A6 | `test_bank.py` 过时注释 + 恒真断言 | 注释改为"当前 reviewed-2"；`assert q.category in CATEGORIES`（从恒真改为真校验） |

验证：`pytest tests -q` → **49 passed**（本轮为 49；第四轮 B2 新增用例后为 **50**）；两次独立进程复跑仍 0 内容差异；临时 run 与当前基线
`run_20260913_postaudit` **87 条 0 差异**（A1–A6 不扰动评测行为）；`review/` 目录现为
5 个顶层文件 + `audit/` 6 文件，全部在 Git 可见路径内。

## 九、第四轮审核整改（B1–B5，2026-09-13 同日）

第四轮验收确认 A1–A6 全部落地、v4 零回归；对 v5 规划说明提 B1–B5，已逐项处理：

| 项 | 内容 | 处理 |
| --- | --- | --- |
| B1 | 根 `README.md` 测试用例数仍写 44 | 改为 50（含 B2 新增用例） |
| B2 | **影响 v5 路径**：`evaluation/chain.py` 写死 `mode="keyword"`、`EvalConfig` 无 `mode` → v5 的 vector/hybrid 对照无法执行 | `EvalConfig` 增 `mode: str = "keyword"` 并透传 `text.search`；`describe` 体现非关键词模式；新增 `test_text_mode_passthrough_and_autodowngrade`（透传 + 无向量自动降级） |
| B3 | v5 文档把 `F02` 误列为 incorrect | 改为 incorrect 全集 16 条：`B02、E01、F01、L01、L02、M03、M07、R02、R03、R04、R05、T01、T03、X01、X02、X03`（注明 F02 为 partial） |
| B4 | 对照配置名不准确（`and_or` 非配置标签） | 改为 `text-only（关键词，and_or）/ text-only-and / vector / hybrid`，注明 `EvalConfig.mode` 已支持 |
| B5 | 四项 nit：`--embeddings` 参数名、14 条出处指向受托审核报告、三轮→四轮、`audit_decisions.json` 契约措辞 | 逐项修正；另补"前端 4 条示例题中 2 条（牧野/井陉）不在题库"的动机说明 |

验证：`pytest tests -q` → **50 passed**；临时 run 与基线 `run_20260913_postaudit`
**87 条 0 差异**（`mode` 默认 keyword，链路行为不变）。

## 十、第五轮审核处理（C1–C3 / R1–R2 / C1'，2026-09-13 同日）

第五轮确认 B1–B5 全部落地（50 用例、与基线 0 差异），并就 v5 规划提"3 处任务缺口 + 2 处技术风险"，
另指出 v4 唯一残留 **C1'**。处理如下：

| 项 | 内容 | 处理 |
| --- | --- | --- |
| **C1'**（v4 残留） | `server/query/understand.py` 模块 docstring 声称 LLM 兜底"调用 deepseek 做实体识别/类型判定"，但 `understand()` 主流程无 LLM 分支（`llm_client/enable_llm` 仅保存）；与 `server/query/README.md`"不含 LLM 实现"矛盾 | 已改为"**预留，尚未实现**"，并指向 v5 T2；`server/query/README.md` 同步为"接口占位、当前无 LLM 分支" |
| C1 | v5 的 T2 把 F02 LLM 兜底写成"打开开关"，实际需**新实现** | v5 规划 §2.2-3 与 §三 T2 改为"实现兜底分支 + 失败降级 + 用例"，并显式列 `understand.py` 为改动点 |
| C2 | 生产侧文本模式**无接线点**（`QueryRequest` 无 mode、`sse.py` 写死 keyword、`runtime.meta.text_mode` 写死、`.env.example` 无开关）→ 只实现 vector 分支线上不会生效 | v5 规划 §三 T3 补三处接线（settings/`.env.example`、`sse.py`、`runtime.meta`） |
| C3 | 缓存键未含 mode，向量请求可能命中关键词缓存 | v5 规划 §三 T3 补"`cache_key` 纳入 mode"，并注明"按请求可切必须加、部署级全局开关需写明前提" |
| R1 | hybrid 分数量纲不一致（bm25 反值 vs 余弦），与 F05 权重重排叠加后排序不可解释 | v5 风险表新增该条，要求先在 T3 定义归一化与融合口径并纳入对照验收 |
| R2 | 真实 LLM 评测随机性，单次运行不足以支撑"必须优于基线" | v5 风险表补"2–3 次取样取分布（或明确接受单次并标注置信边界）" |

附：审核 §28.2 指出的两处措辞也已修正（限流按默认 30/min 构造超限请求；`demo_mode` 改称"新建行为"）。
本次 v4 侧只改 docstring 与 README 文字，无逻辑变更；`pytest tests -q` → **50 passed**。
