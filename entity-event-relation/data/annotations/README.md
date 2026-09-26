# 人工标注规范（`data/annotations/`）

评估**唯一**的对照来源。三个 JSON 决定所有指标的分子分母，因此口径必须写下来——
否则"指标降了"到底是模型变差还是标注口径变了，事后无法区分。本文件同时如实记录
**已知的标注局限**。

> 现状：`sample_entities.json` / `sample_events.json` / `sample_relations.json` 三份，
> 覆盖原书先秦~清的全部主要战争。
> **没有做标注者间一致性（IAA）**——只有一名标注者，无法给出标注可靠性上界。

## 一、标注范围

- 语料：`data/中国历代战争简史.txt`（受版权约束，不入库）。
- 粒度：**以"事件"为中心**。实体标注存在的意义是作为事件要素被评估；
  关系标注的 head 必须是事件名。
- 抽样：全书事件（不是抽样片段）。因此指标是全书级别的，不是样本级抽样估计。

## 二、三份文件的口径

### 1. `sample_entities.json`

| 键 | 条数 | 字段 | 说明 |
| --- | --- | --- | --- |
| `places` | 406（唯一名称 378） | `geo_name`（必填）、`modern_name`、`DynastyName`、`Province`、`City`、`District_County`、`Specific_location` | 地名。`geo_name` 用**书中的写法**，不改写成今地名（今地名是抽取产物里的 `modern_name`） |
| `organizations` | 279（唯一名称 242） | `OrgName`（必填）、`OrgType`、`DynastyName` | 政权 / 军队 / 部族。"周军""商军"这类军队算组织，不算地点 |
| `persons` | 506（无重名） | `PersonName`（必填）、`DynastyName`、`OrgName`、`Role`、`Note` | 人物。**用书名（含朝代前缀）**，如 `周武王` 而不是 `武王` |

注意"条数"与"唯一名称数"不同：地点与组织里有同名多行（同名地点按朝代/现代位置各记一条）。
**评估用的是名称集合**，同名多行会被压成一个（见下面的局限 1）。

匹配口径见 `war_extraction/evaluation/optimal_evaluator.py::evaluate_entities`：
名称精确相等、或一方包含另一方（长度 ≥2）、或 `fuzz.ratio ≥ 70` 即算命中，
且**一对一**（同一标注实体不会被两个预测重复领走，召回率因此不会超过 100%）。
这里**不使用** `Normalizer` 的别名归一——别名表不作用于实体评估匹配。

### 2. `sample_events.json`

- `events`：323 条（唯一事件名 320）。字段 `EventName`（必填、唯一）、`EventType`、`StartDate`、
  `EndDate`、`DynastyName`、`Place`、`Aggressor`、`Defender`、`Relations`、`Result`、`Person`、
  `Action`、`Scale`、`source`、`Impact`、`Remark`。
- `sub_events`：当前为空数组（占位）。
- **事件名归一原则**：用"最能指认这场战争"的完整写法（如 `周武王灭商牧野之战`），
  不要用"牧野之战"这类简称——评估侧的事件名相似度阈值只有 0.35，
  简称也能配上，但简称会与其它事件的简称互相混淆。
  `relations` 里的 `Relations` 是**自由文本**（如"因果关系：启继位后伯益反抗，启镇压；
  顺承关系：启战胜后巩固统治"），只作参考，不参与指标计算（结构性关系在
  `sample_relations.json` 里）。
- **字段名与抽取产物不完全一致**（历史遗留，评估时按下列对应关系看）：
  标注用 `Person` / `Scale`，抽取产物用 `KeyPersons` / `TroopSize`。
  评估器目前只比对事件名（`EventName`），因此这个差异**不进指标**，
  但人工做错误分析时要记住它们不是同一字段。
- 同名不同年代的事件各记一条（如两个"扬州之战"），但**评估只用事件名集合**，
  同名会被合并（见下面的局限 1）。

### 3. `sample_relations.json`

四类，每条形如 `{"head": 事件名, "relation": 关系名, "tail": 实体名/事件名}`：

| 类别 | 条数 | relation 取值（实测分布） |
| --- | --- | --- |
| `event-place` | 479 | 主战场 275、目的地 55、战略要地 48、出发地 33、途经地 25、次要战场 22、补给地 10、指挥所 4、驻防地 4、议和地点 3 |
| `event-org` | 527 | 发起方 237、防守方 216、同盟方 32、投降方 19、支援方 12、被俘方 9、议和方 2 |
| `event-person` | 592 | 将领 181、统帅 153、君主 119、阵亡 54、谋士 29、参与者 20、俘虏 13、叛变 10、投降 7、使者 4、可汗 2 |
| `event-event` | 168 | 因果关系 80、顺承关系 59、并列关系 13、条件关系 10、包含关系 6 |

- **约束**：`head` 必须是 `sample_events.json` 里出现过的事件名（实际文件不满足，见下面的局限 1）；
  `event-event` 的 `tail` 也必须是事件名，其余类别的 `tail` 必须是对应类型的实体名
  （`event-place` 的 tail 要与 `entities.places[].geo_name` 对得上）。
- **关系名必须取自上表**（事件-事件那五类还必须在规范集合内，见
  `Normalizer.CANONICAL_EVENT_RELATION_TYPES`）。评估器对关系类型的口径是：
  **五个规范事件-事件关系类型之间要求精确相等**，其余自由文本关系名走模糊比对
  （阈值 `relation_threshold`）。之所以要单独规定：这五个类型两两之间的 `fuzz.ratio`
  都是 50，若走模糊比对则"因果关系/顺承关系/并列关系"互相顶替、类型判错也拿满分。

## 三、已知的标注局限（如实记录，别当成完美金标准）

1. **有重复行，且部分关系的 head/tail 不在被引用的名单里**（实测数字）：

   | 问题 | 实测 |
   | --- | --- |
   | 实体重复行 | 地点 406 行 / 378 个唯一名称，组织 279 行 / 242 个唯一名称，人物无重复 |
   | 关系 `head` 不在事件标注名单 | `event-place` 479 条里 230 条、`event-org` 527 条里 136 条、`event-person` 592 条里 183 条、`event-event` 168 条里 56 条 |
   | 关系 `tail` 不在对应实体/事件名单 | `event-place` 194 条、`event-org` 106 条、`event-person` 180 条、`event-event` 69 条 |

   评估时 `build_gold_triples` 只保留"head 能被映射到预测事件"的关系，因此
   **这批关系的 gold 三元组根本不进分母**——关系召回率的分母比标注文件看起来的小，
   报告里引用条数时要注明这一点。实体与事件同理：gold 被压成名称集合，
   重复行与同名不同年代的行都会被合并，而预测侧是逐行计数的，两侧身份口径不一致。
2. **原书用字不统一**：同一人/地在书里有多种写法。典型是 `孙膑/孙滨`
   （原书 `孙膑` 9 次、`孙滨` 6 次；三份标注里 `孙膑` 共 6 处、`孙滨` 0 处）。
   这类"错字变体"应当做**别名归一**（`孙滨 → 孙膑` 已加进 `Normalizer.ENTITY_ALIASES`），
   而不是把它当成"低质量人名"整条丢掉——后者会让预测压根不产生这个名字，
   连配对的可能都没有。遇到这类差异，**以标注写法为准**并把变体加进别名表。
3. **未做标注者间一致性（IAA）**：只有一名标注者，没有第二人复核，
   所以无法给出"标注本身有多少噪声"的上界；实体/事件指标里有一部分误差可能来自标注。
4. **标注是单文献的**：全部标注只覆盖《中国历代战争简史》。
   `Normalizer.ENTITY_ALIASES` / `EVENT_NAME_ALIASES`、`is_noisy_place_name` 的噪声正则
   这类规则都是**语料特定**的（把某本书里的错字变体或噪声写法硬编码进归一表与过滤器），
   换语料时要连同标注一起重新审视。人名侧按"整条丢弃"的低质量名单已经不存在了：
   现在的做法是别名归一，而不是删掉整条预测。
5. **事件级指标只看事件名**：`EventName` 之外的字段（`Place`/`Aggressor`/`StartDate`…）
   目前只报填充率（`evaluate_event_fields`），不与标注比对——
   所以"字段填错"不会体现在 F1 上。
6. **事件 `StartDate`/`EndDate` 里混有 OCR/录入残值**（如清代事件出现 `153`、`200`），
   这些字段目前不参与事件 F1，不会拉低指标，但会污染下游知识库。
7. **没有证据字段**：事件的 `source` 只有书名，没有页码、章节、字符 offset 或原文 evidence；
   关系只有 `head/relation/tail`。因此无法逐条回溯"这条标注来自原文何处"，
   标注修改也做不了逐条审计。

## 四、改动标注的流程（重要）

标注是**指标的分母**，动它等于改口径：

1. 改完先跑 `python evaluate.py --pred <固定的同一份产物> --output evaluation/run_<时间戳>`；
2. 与改前的 `results.json` 逐字段对照（除 `metadata.evaluated_at`），
   差异要有解释（哪一类指标为什么动）；
3. 在 [`../../../docs/项目审查与修复历史.md`](../../../docs/项目审查与修复历史.md)
   里记下"改了什么标注 + 前后指标 + 原因"，并把新产物留档——
   否则下一轮又会把"标注变了"误读成"模型退化了"
   （曾经吃过一次"产物换代被当成匹配抖动"的教训）。

## 五、相关文件

- 阈值与开关：`config/eval_config.json`（四个阈值 + 两个映射表路径）
- 别名与关系归一：`config/aliases.json`、`config/relation_types.json`、
  `war_extraction/utils/normalizer.py`
- 评估实现：`war_extraction/evaluation/optimal_evaluator.py`
- 阈值敏感性扫描：`tools/threshold_sensitivity.py`
- 评估口径的分子分母、评估器已知缺陷与改进方向：见 [`../../README.md`](../../README.md)
