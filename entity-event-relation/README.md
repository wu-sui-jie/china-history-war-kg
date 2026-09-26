# entity-event-relation — 知识抽取与评估

基于大语言模型的历史战争文本知识抽取与评估流水线：从非结构化战争史文献中抽取实体、事件与关系，
构建结构化知识图谱数据，并提供与人工标注对比的评估体系。

**这一部分不参与 Web 运行**——它是离线流水线，产物（`output/` 下的 JSON 与 Excel）再由
`backend/import_json_to_sqlite.py` 导入 SQLite，进而同步到 Neo4j。
被旧后端的「文本实体识别」页调用时走 `backend/blueprints/llm.py` 的 `/api/extract/entities-events`。

## 抽取内容

| 层次 | 产出 | 说明 |
| --- | --- | --- |
| 实体层 | 地点、人物、组织 | 地点含古今地名映射；人物含官职/角色/归属；组织区分国家/军队/联盟 |
| 事件层 | 战争事件 | 事件类型判定 → 子事件（时间 + 生命周期阶段）→ 补全事件要素（名称、时间、地点、参战方、结果、影响等） |
| 关系层 | 四类关系网络 | 事件-地点、事件-组织、事件-人物、事件-事件 |

关系类型的标准名与别名表在 `config/relation_types.json`（如"发生地点"吸收"主战场/战场/战略要地"），
别名归一在 `war_extraction/utils/normalizer.py`。

## 目录结构

```text
entity-event-relation/
├── main.py                    # 抽取主入口（命令行）
├── evaluate.py                # 评估主入口
├── pyproject.toml             # 包定义（包名 war_extraction，正式包：pip install -e entity-event-relation）
├── war_extraction/            # 原 src/（包名避开顶层 src，避免撞名）
│   ├── config.py              # 分段参数、提示词版本（源码哈希派生）与缓存上下文
│   ├── core/
│   │   ├── llm_client.py      # DeepSeek API 封装（密钥、重试、错误提示）
│   │   ├── text_splitter.py   # 长文本分段（优先句子边界）
│   │   ├── extraction_runner.py # **抽取编排唯一实现**（分段 + 三阶段 + 失败诊断），backend 也走它
│   │   └── cache_manager.py   # 基于文本 MD5 的调用缓存（原子写 + 孤儿 GC + 可选 TTL）
│   ├── prompts/               # 三轮递进提示词（实体 / 事件 / 关系）
│   ├── extractors/            # entity_extractor / event_extractor / relation_extractor
│   ├── models/                # 抽取结果的 pydantic 模型（entities / events / relations）
│   ├── processors/
│   │   ├── result_merger.py   # 多段结果合并与去重
│   │   └── json_to_excel.py   # JSON → Excel
│   ├── evaluation/            # optimal_evaluator（唯一在用的评估器），评估口径见其 docstring
│   ├── geocoding/             # 历史地名 → 现代坐标（高德 API + 人工审核），见 war_extraction/geocoding/README.md
│   └── utils/                 # normalizer（名称/关系归一，别名表唯一来源）、entity_classifier、json_payload、value_parsing、relation_rules
├── config/                    # aliases.json / dynasty_ranges.json / eval_config.json / relation_types.json
├── data/                      # 输入原文 + data/annotations/ 人工标注（**字段口径见其 README**）
├── output/                    # 抽取结果（按运行批次分目录，自动生成）
│   └── 中国历代战争简史/       # 当前采用的那一批（见下方「批次取舍」）
│       ├── 9_final_all.json    # 聚合结果（实体+事件+关系+质量报告，唯一权威产物）
│       ├── published/          # 发布子集（过滤+清洗后的版本，事件与 final_all 不同）
│       └── excel/              # 便于查看的表格（含 全部数据.xlsx，为其余 8 张的合集）
├── cache/                     # API 调用缓存（同时是产物的可复现路径，别随手清）
├── evaluation/                # 评估结果目录（`latest/` 与 `recheck-*/` 是**平级**的加注历史基线）
│   ├── latest/                #   历史基线（加注说明过：值属旧口径且不可复现，别当回归基线）
│   ├── recheck-micro-20260925/ #  同上（recall 改 micro 口径那次的重跑）
│   └── run_<时间戳>/           #   逐次运行默认写到这里（不入库）
├── tools/
│   └── threshold_sensitivity.py # 四阈值敏感性扫描
└── tests/                     # 常驻用例（见「快速开始 6」），已全部进 CI
```

## 批次取舍（`output/` 与 `cache/` 的清理口径）

`main.py --output` 每跑一次就在 `output/` 下生成一个批次目录。历史迭代（`full_phase*`、`test_*`）
都是同一条流水线的中间产物，只有**事件数最多、且与库内数据一致**的那一批才是当前采用的数据：

```bash
# 判断哪一批是当前采用的：事件数与 SQLite 一致（当前 1050）
python -c "import json;d=json.load(open('output/中国历代战争简史/9_final_all.json',encoding='utf-8'));print(len(d['events']['events']))"
```

2026-09-21 整理时，已删除 10 个被取代的批次（5 个中间迭代 + 5 个小样本文本冒烟批次，共约 39 MB），
只保留当前批次 `output/中国历代战争简史/`。判断依据是事件数：被删批次为 861–932 条（少于当前的 1050），
或跑在 8.8 KB 的 `中国历代战争简史_测试数据.txt` 上。

`cache/` 同理：缓存键包含 `prompt_version`（`war_extraction/config.py` 的 `PROMPT_VERSION`），
换过提示词版本的条目**永远不会命中**，属纯占位。整理时按版本筛掉了 392 条旧条目（40 MB），
保留当前版本的 196 条。删缓存只损失"重跑时省下的 API 费用"，不影响任何产出。

### 分步 JSON 已删除（2026-09-21）

`main.py` 会在批次目录里另写 `1_places.json` … `10_quality_report.json` 共 9 个**中间产物**，
它们与聚合文件的关系已经核对过：**9 个文件的内容与 `9_final_all.json` 内的对应字段逐字节一致**
（`10_quality_report.json` 等同于 `9_final_all.json` 的 `quality_report` 字段）。
为消除这份重复，已删除这 9 个中间文件（约 20.5 MB），批次目录只保留 `9_final_all.json`。

**影响与恢复**：`war_extraction/processors/json_to_excel.py` 的 `convert_all()` 读的是这 9 个分步文件，
所以**从现有 JSON 重新生成 Excel 之前要先拆回来**（内容无损，一步即可）：

```python
# python 交互式运行；在 entity-event-relation/ 目录下
import json, pathlib
out = pathlib.Path('output/中国历代战争简史')
d = json.load(open(out / '9_final_all.json', encoding='utf-8'))
dump = lambda name, obj: json.dump(obj, open(out / name, 'w', encoding='utf-8'),
                                  ensure_ascii=False, indent=2)
dump('1_places.json', d['entities']['places'])
dump('2_persons.json', d['entities']['persons'])
dump('3_organizations.json', d['entities']['organizations'])
dump('8_events.json', d['events']['events'])
dump('4_event_place_relations.json', d['relations']['event_place_relations'])
dump('5_event_person_relations.json', d['relations']['event_person_relations'])
dump('6_event_organization_relations.json', d['relations']['event_organization_relations'])
dump('7_event_event_relations.json', d['relations']['event_event_relations'])
dump('10_quality_report.json', d['quality_report'])
```

重新跑 `python main.py data/中国历代战争简史.txt` 也会重新写出这些文件（会走 API，但缓存命中则近乎免费）。

## 运行环境（四个模块统一）

四个 Python 模块（backend / RAG / feishu-bot / entity-event-relation）**统一 Python 3.11**，
本地统一 conda 环境名 `china-war-py311`（Python 3.11.16）：

```bash
conda activate china-war-py311
```

仓库根的 `requirements.lock`（119 个包、每条带 sha256）覆盖四个模块的依赖，是全仓依赖的权威来源；
本模块自己的依赖声明在 `pyproject.toml`（包名 `war_extraction`）。

CI 里抽取链与旧后端**在同一个 job**（`.github/workflows/ci.yml` 的 `legacy-backend`）：
`compileall` 与 ruff F821 门禁按 3.8 语法目标跑，抽取链用例在该 job 里执行；
过渡期该 job 仍按 3.8 / 3.11 双档跑，两档都绿并稳定后再删掉 3.8 那一档。

## 快速开始

### 1. 安装依赖

依赖声明在 `pyproject.toml`（本模块是正式包 `war_extraction`）。两种装法：

```bash
# A) 只跑本模块的抽取/评估（装依赖 + 把包本身装上）
pip install -e entity-event-relation

# B) 作为旧后端的依赖一起装（仓库根执行；backend 以 war_extraction 引用本模块）
pip install -r requirements.txt && pip install -e entity-event-relation
```

> `-e`（可编辑安装）改了本模块代码立即生效，不必重装。**不装会出现
> `ModuleNotFoundError: No module named 'war_extraction'`**——旧后端启动即失败。
> backend 以正常依赖方式引用本模块，不再往 `sys.path` 里塞目录。

### 2. 配置 API 密钥

密钥走环境变量（**不要硬编码**）：`DEEPSEEK_API_KEY` 为主（兼容历史变量名 `Chinese_txt`），
另可设 `API_BASE_URL`（默认 `https://api.deepseek.com/v1`）与 `DEEPSEEK_MODEL`（默认 `deepseek-chat`）。
也可写在 `config/.env` 中（该文件已被 git 忽略）：

```bash
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat
API_BASE_URL=https://api.deepseek.com/v1
```

### 3. 准备数据

待抽取文本放 `data/`（如 `data/中国历代战争简史.txt`）。如需评估，在 `data/annotations/` 下准备
对应的实体、事件、关系 JSON 标注——**字段口径、关系名取值表与已知局限见
[`data/annotations/README.md`](data/annotations/README.md)**（评估的分子分母全由它决定，
改标注等于改口径）。

### 4. 运行抽取

```bash
python main.py data/中国历代战争简史.txt

# 可选参数
#   --no-split       禁用长文本分段
#   --no-cache       完全禁用缓存（不读也不写）
#   --refresh-cache  跳过读取缓存，但保存本次新结果
#   --output DIR     输出目录，默认 output
```

结果写入 `output/<文本名>/`，包含分步骤 JSON（`1_places.json` … `9_final_all.json`）、
`10_quality_report.json`、`published/final.json` 与 Excel 文件。

### 5. 运行评估

```bash
python evaluate.py
#   --pred  PATH     预测结果 JSON，默认 output/中国历代战争简史/9_final_all.json
#   --output DIR     评估输出目录，默认 evaluation/run_<时间戳>
```

每次运行写到**自己的** `evaluation/run_YYYYmmdd_HHMMSS/`（不入库）。`evaluation/latest/` 与
`evaluation/recheck-micro-20260925/` 是**跟踪入库的历史基线**：它们的 `metadata.snapshot_note`
说明了来历与局限（值不可复现、不要当回归基线），点开那份 JSON 就能看到原文。
要覆盖它们必须显式写 `--output evaluation/latest`，届时脚本会先打出覆盖警告。

要人工核对一次**完整评估**的跨进程一致性（CI 里没有 `output/` 制品，跑不了这一步）：

```bash
cd entity-event-relation
for s in 0 1 2; do PYTHONHASHSEED=$s python evaluate.py --output ../.eval-check-$s; done
# 然后递归对比三份 results.json：除 metadata.evaluated_at 外应逐字段相同
```

### 6. 运行测试

```bash
conda activate china-war-py311
cd entity-event-relation
python -m pytest tests -q
```

常驻用例（**12 个文件、109 例**）已全部进 CI，跑在上面说的 `legacy-backend` job 里：

| 用例 | 钉住的回归 |
| --- | --- |
| `tests/test_evaluator_deterministic.py` | 评估可复现：同一输入两次评估必须逐字段相同（开 4 个不同 `PYTHONHASHSEED` 的子进程比对完整 `evaluate_relations` 返回） |
| `tests/test_normalizer_noise.py` | 「等 N 方国 / 等 N 国 / 等 N 部落」这类噪声地名必须被判为噪声 |
| `tests/test_cache_manager.py` | 缓存索引原子写：写一半崩溃后旧索引仍完整、悬挂与损坏条目被摘除 |
| `tests/test_paths_and_cache_gc.py` | 路径锚定（默认缓存/配置目录不随工作目录变）、配置缺失不再静默、孤儿条目 GC 与 TTL |
| `tests/test_orchestration_single_entry.py` | 抽取编排只有一份实现：任何一侧都不许自建阶段循环（AST 断言）、共享编排的钩子位置/失败策略/缓存命中 |
| `tests/test_relation_types.py` | 五个规范事件-事件关系类型必须精确相等（它们两两 `fuzz.ratio` 都是 50，走模糊比对则类型判错也满分） |
| `tests/test_prompt_version.py` | 提示词版本由源码哈希派生，改一个字符就换版本、缓存键随之失效 |
| `tests/test_geocode_amap.py`、`tests/test_geocoding_pipeline.py`、`tests/test_geocoding_db_path.py` | 高德地理编码的重试退避/配额区分/逐条落盘、一批一进度文件与配额截断提示、主库路径解析口径 |
| `tests/test_shared_helpers.py` | 公共 JSON/多值/年份/仲裁实现，含**刻意保留**的差异（两个 JSON 策略不可互换） |
| `tests/test_decision_filters.py` | `LOW_QUALITY_PERSON_NAMES` 名单不存在（`秦始皇`/`吴起` 是合法人名）、占位词排除集为宽口径、`utils/alignment.py` 已移除 |

每条用例都是先确认「改动前失败」才入库的（验证表见
[`../docs/项目审查与修复历史.md`](../docs/项目审查与修复历史.md)）。
`tests/fixtures/relation_slice_repro.json` 是评估可复现性的最小复现夹具（从 `9_final_all.json`
delta-debugging 缩到 2 条关系），文件头的 `_note` 记了来源与缩减方法。

## 评估设计

在信息抽取领域用三个标准指标衡量质量：**P**（精确率，预测对了多少）、
**R**（召回率，标注被找回多少）、**F1**（二者的调和平均）。

本项目的评估策略是"**最优模糊匹配**"，而不是严格字符串相等——因为同一事件的写法天然不唯一：

1. **事件名称相似度**：完全匹配 → 1.0；标准化后（去"之战/战争/起义"等）匹配 → 0.95；
   包含关系 → 0.9；字符交集 + fuzzywuzzy（ratio / partial_ratio / token_set_ratio）→ [0,1]；
   阈值 ≥0.35（`config/eval_config.json` 的 `event_sim_threshold`）视为候选。
2. **贪心最优匹配**：按相似度降序依次匹配，保证每个标注事件只匹配一个预测事件，产出事件映射表。
3. **实体评估（事件中心过滤）**：只保留与匹配事件要素或标注实体模糊匹配（阈值 70%）的预测实体，
   再按严格名称匹配算 P/R/F1——宁可少评价，也不要因事件没对齐就判实体错。
4. **事件评估**：基于映射表算 TP/FP/FN。
5. **关系评估**：只评估与匹配事件相关的关系；先做关系类型归一，再做尾实体模糊匹配，
   最后按三元组 (head, relation, tail) 算 P/R/F1。关系类型的判定分两种：
   两侧都落在五个规范事件-事件关系类型里时**要求精确相等**（这五个类型两两 `fuzz.ratio` 都是 50、
   都过阈值 40，若走模糊比对则类型判错也拿满分）；其余自由文本关系名仍走模糊比对（阈值 40%）。
6. **综合结果**：输出实体 F1、事件 F1、关系 F1 与三者的**宏观平均 F1**。

阈值都在 `config/eval_config.json` 里，改阈值不需要动代码；"换阈值指标会动多少"用
`python tools/threshold_sensitivity.py` 扫（一次一因子，默认约 8 分钟，输出 markdown 表，
表头会打上预测产物的 sha256，以便区分"阈值变了"和"产物换代了"）。

### 指标的分子分母

评估是"预测 vs 人工标注"的对比，每一层的分子分母必须写清楚，否则"指标降了"到底是
模型变差、标注口径变了还是评估器变了，事后无法区分（改标注的流程见
[`data/annotations/README.md`](data/annotations/README.md)）。

| 层 | 预测侧（FP 的来源） | 参考侧（FN 的来源） | TP |
| --- | --- | --- | --- |
| 实体 | 产物 `entities.places/persons/organizations` 的**逐行**条数 | `sample_entities.json` 三类的**名称集合**（同名多行压成一个） | 一对一模糊匹配命中的行数 |
| 事件 | 预测事件**名称集合**（先去重） | `sample_events.json` 的 `EventName` 集合 | 事件映射表与标注名集合的交集 |
| 关系 | 只保留"事件能被映射到标注事件"的三元组（**注意**：这一层还被 gold 预过滤，见「已知局限」第 2 条） | 只保留"head 能被映射到预测事件"的三元组 | 一对一贪心匹配上的三元组数 |

三点必须记住：

1. **实体召回率封顶 100%**：匹配是一对一，同一标注实体不会被两个预测重复领走。
2. **`entities` 的行数远多于唯一名称数**：产物保留"同名不同朝代/现代地名"的多行表示，
   而评估把 gold 压成名称集合——两侧的身份口径本来就不一致。
3. **关系的分母不是标注文件总量**：head 映射不到预测事件的关系直接不进 gold 三元组，
   所以关系召回率的分母会随事件匹配结果浮动。

**可复现性**：模糊匹配的贪心过程必须与集合迭代顺序无关。`filter_relations` 与
`build_gold_triples` 返回的是**集合**，而 Python 的字符串哈希每进程随机化——直接迭代的话，
同一份 pred / gold / config 换个进程就会得到不同的 tp/fp/fn。实测（全量数据）：

| PYTHONHASHSEED | filtered_pred | tp | fp | fn | 关系 F1 |
| --- | --- | --- | --- | --- | --- |
| 0 | 888 | 739 | 149 | 360 | 0.7438 |
| 1 | 888 | 743 | 145 | 356 | 0.7479 |
| 2 | 888 | 741 | 147 | 358 | 0.7458 |

因此匹配前先把两个集合按内容定序（`match_relation_triples`），错误样例也定序后再截前 20 条。
**不要**改成只按相似度排序（并列时又会依赖输入顺序），也**不要**用固定 `PYTHONHASHSEED` 掩盖——
那只是把症状藏起来。改动这块请跑 `python -m pytest tests -q`（见上节）。

## 已知局限（数据与评估）

下面这些不是"以后有空再修"的清单，而是**读当前指标时必须一并知道的前提**。
不读这一节直接引用 F1，会得出错误结论。

### 1. 参考标注不是可靠金标准

`data/annotations/` 是唯一评估来源，但它同时被用于调事件阈值、调关系阈值、加别名、
增删过滤规则、决定关系类型归一口径，最后又用来报分——**没有 train/dev/test 切分，
等于在测试集上持续调参**。三份标注的已知问题：

| 问题 | 具体表现 |
| --- | --- |
| 只有一名标注者 | 没有第二标注者、没有 IAA 与仲裁，无法区分"模型抽错"和"标注漏标" |
| 同类型内重复/冲突 | 地点有同名多行（按朝代、现代位置各记一条）、组织有重复且字段互相冲突、事件有同名不同年代各一条；评估把 gold 压成名称集合、重复被吞掉，预测侧却逐行计数 |
| 关系违反自身规范 | 大量关系的 `head` 不在事件标注名单里、`tail` 不在对应实体/事件名单里——这批关系进评估前就被 `build_gold_triples` 丢掉 |
| 缺少可追溯证据 | 事件 `source` 只有书名，无页码、章节、字符 offset 或原文 evidence；关系只有 `head/relation/tail`，无法逐条回溯原文 |
| 存在可确认的事实错误 | 部分事件日期字段是 OCR/录入残值（如清代事件出现 `153`、`200`）；个别关系标注与原文不符（如把"晋惠帝"同时标成"君主"和"阵亡"） |

结论：当前标注只能当**待审核参考集**，不能称为可靠 ground truth。

### 2. 评估器本身的设计缺陷（影响指标解释）

- **关系评估有 gold 预过滤（循环过滤）**：`filter_relations` 里的 `check_exists()` 先去 gold
  关系里找相似的 head/relation/tail，只有"已经像某条 gold"的预测关系才进入 `pred_triples`。
  结果是原始预测关系里绝大部分**没有被算作 FP**，precision 被显著高估。标准做法是：
  已对齐事件范围内的**全部**预测关系都要进入 TP/FP 计算，不能先用 gold 判断"像不像"。
- **事件匹配阈值 0.35 过低**：大量无关事件名对也能越过它，于是 `黄巢农民起义 → 金田起义`
  这类不同事件被判为同一事件——虚增事件 TP 的同时，还把 gold 关系挂到错误的预测事件上。
- **实体评估只用字符串匹配**：`evaluate_entities()` 走"原字符串相等 / 双向包含 /
  `fuzz.ratio ≥ 70`"，**没有调用 `normalize_entity()`**，所以 `aliases.json` 与
  别名归一并不作用于实体评估。
- **事件身份只看名称**：gold 里两个"扬州之战"、两个"平壤之战"分属不同年代，
  评估用名称集合会把它们合并。事件身份至少要包含名称、朝代、时间、地点。
- **`evaluate.py` 写的 metadata 是"评估时"的版本**：`prompt_version` / `extraction_version`
  取自当前代码，而不是读预测产物自身的 metadata。直接对旧产物跑当前 `evaluate.py`，
  报告会把旧产物标成新提示词版本。要能自证来源，metadata 里应同时记录
  `artifact_prompt_version`、`artifact_extraction_version`、`evaluator_version`、
  `git_commit` 与预测文件 SHA-256。

### 3. 三个 F1 各自不能直接读

下面的数字来自**一份固定产物 + 当时那版评估器**的诊断实测；评估口径（如 recall 的
micro/macro 口径、定序化）一改，具体数值就会变。引用前先自己跑一次
`python evaluate.py`，以本次 `evaluation/run_*/results.json` 为准。

**实体 F1 低（P 很低、R 很高）**，原因分四类，不能都算模型错：

- gold 覆盖范围窄：提示词要求抽"与战争/军事相关的所有实体"，产物里大量真实出现在原文中的
  合法实体（地点、人物、组织）不在 gold 里，全部算 FP；
- 预测侧重复表示：合并键含名称 + 朝代 + 现代地名，而评估只按名称，同名多行被当成重复 FP；
- 预测侧确实有噪声：地名被截断或括号拆裂（`东夷（山东`、`江苏一带）`）、组织里混进地点或
  事件概念（`零陵`、`黄巾起义`）、人物有 OCR 截断名，以及相当数量的一字名称待逐类判定；
- 实体评估不使用别名归一（见上一节）。

仅把预测地点按名称去重（不改任何模型结果）就能让实体 F1 从 0.2042 升到 0.2698——
说明低分有相当一部分来自**数据模型与评估身份口径不一致**，不全是模型抽错。

**事件 F1 低（P 很低）**：预测事件数远多于 gold，粒度不同（gold 只标主要战争，`sub_events`
至今是空数组，而预测包含章节级子战役与阶段行动，其中不少在原文里确有内容）；叠加 0.35 的
低阈值与同名不同事件被合并。低阈值不是"模型质量提高"，只是评估器更愿意把名字相似的事件
视作同一事件（阈值越高、事件 TP 与召回越低，说明当前高召回依赖宽松模糊匹配）。

**关系 F1 看起来高，但不能相信**：它来自被预过滤后的分子分母。保留当前事件映射与模糊匹配、
但**不做 gold 预过滤**的诊断复算显示：参与评估的预测关系从 888 涨到 5,291，
precision 从约 80% 掉到约 16%，关系 F1 从约 0.72 掉到约 0.27。
这个复算仍受 gold 缺陷与事件错误映射影响，不能当最终标准，但它证明了当前的精确率是虚高的。

关系层还有几类**数据正确性**缺陷（不只是评估问题）：

- **派生关系过量**：除 LLM 输出外，系统还从事件字段反向派生关系（`Place` 第一个地点一律
  判"主战场"、事件文本出现"议和"就给所有地点加"议和地点"、`Commanders` 一律生成"统帅"…）。
  平均每个有关系的事件约 17.5 条关系，极端值上百条（平定三藩之乱 188 条）。
- **事件-事件关系方向被按名称字典序改写**：`result_merger.py` 对"顺承/因果"用排序后的无向
  pair 去重，`EventName_A > EventName_B` 时直接交换两端，方向可能由汉字字典序决定；
  后续清理只有在 evidence 同时包含两个完整事件名时才换得回来，而系统概括的事件名常常
  不在 evidence 里，错误方向可能保留到最终产物。
- **类型分布偏斜**：事件-事件关系里"顺承关系"占绝大多数，因为 `arbitrate_event_event_relation`
  会把"模型说是因果、但证据里没有强因果词"的关系降级成顺承关系。
- **仍有非规范关系类型**：输出里存在不在标注枚举里的关系名（`退守地`、`登陆地`、
  `防守方统帅`、`向导`、`监督` 等），抽取输出 schema 与评估 schema 不统一。

### 4. evidence 非空率不等于数据正确

质量报告只检查 source/evidence **是否非空**，不检查"是否为原文句子、是否包含对应实体、
是否支持该关系类型、是否来自正确事件、是否只是整段文本重复使用"。实测把空白归一后
在原文中直接查找：地点 source_text 约 82%、事件 source_text 约 81% 能找到，
组织/人物只有约 58% / 60%；关系 evidence 里 event-place 约 73%、event-person 约 70%、
event-event 只有约 46%。不能直接找到不等于一定错（引号、OCR、拼接差异都可能），
但说明 evidence **不是稳定的原文引用**。关系 evidence 还大量整段复用（同一条 evidence 被
几十条关系共用）。因此"evidence 非空率 100%"不能当作质量正确率。

### 5. 输入文本质量会传导到抽取

输入是从书籍转换出的长文本，存在换行、断词和 OCR 错字（如 `日军` 被识别成 `目军`、
年份数字被拆行、章节标题与总结段和具体战例混在同一输入里）。分段器按约 1800 字、
重叠 200 字切分，边界优先句号，基本可用；但重叠段会重复抽取，同名实体又按朝代/现代名称
保留多个版本，重复被进一步放大。模型抽取前缺少 OCR 清洗与章节结构解析。

### 6. 指标评的是"哪一版代码"要说清楚

`9_final_all.json` 的 metadata 记录抽取时间、`extraction_version`、`prompt_version`
与 `model` / `api_base`。但缓存的键包含 `prompt_version`，**换过提示词版本的缓存条目
永远不会命中**——提示词一改，下一次抽取必然全部重新调用模型（花费用、且产物换代）。
所以做质量结论前先核对"产物 metadata 的版本 == 当前代码的版本"，否则评的是旧产物。

## 后续改进方向

顺序很重要：**先重建评估口径，再修评估器，再修抽取与后处理，最后才付费整本重跑**。
先重跑、后修评估，钱花了也得不到可信结论。

### 第一阶段：重建评估口径

1. 给事件、实体分配稳定 ID；事件身份至少包含名称、朝代、时间、地点。
2. 清理 gold 重复、错误日期与关系 head/tail 违规。
3. 每条事件与关系标注补原文 evidence、章节、字符 offset。
4. 至少两名标注者独立标注一部分数据，计算 IAA 并仲裁。
5. 数据拆成 development / test；调提示词与阈值只看 development，test 最后一次使用。
6. 明确事件粒度口径：战役、战争、阶段行动、政治事件分别是否计数。

### 第二阶段：修评估器

7. 实体评估统一身份口径并真正使用别名归一；分别报告 mention 与 canonical entity 两种指标。
8. 事件映射禁止 0.35 这种无语义约束的宽松全局配对，增加朝代、时间、地点约束。
9. 关系评估删除 `check_exists(gold)` 预过滤，已对齐事件上的全部预测关系都进入 FP 计算。
10. 事件字段从"填充率"升级为值准确率：时间、地点、攻守方、人物、结果分别评估。
11. 按朝代、实体类型、关系类型分别报告 P/R/F1，不只给一个宏观平均数。
12. 报告 raw 与 published 两套指标，并固定预测文件哈希、代码 commit 与模型参数。

### 第三阶段：修抽取与后处理

13. 修复事件-事件关系按名称字典序换向的问题。
14. 派生关系必须绑定具体证据与具体实体，不能因整段出现"投降/议和"就给所有实体加关系。
15. 减少从事件字段反向生成实体与关系的无条件扩张。
16. 输出关系类型统一到 schema 枚举，非枚举项进 candidate 区。
17. 增加 OCR 清洗与章节结构解析。

### 第四阶段：再做付费整本重跑

18. 固定模型、temperature、seed（若 API 支持）、prompt hash 与代码 commit。
19. 在 development 上调试，不查看 test 结果。
20. 完成后只在 test 上跑一次，附人工抽样正确率与置信区间。
21. 通过后再导入 SQLite/Neo4j 并重建 RAG 数据。

## 与旧后端的关系

- 旧后端通过正式包 `war_extraction` 引用本目录（不再走 `sys.path` 注入），提供
  `/api/extract/entities-events`（文本实体/事件识别页）；**因此本目录必须与 `backend/` 同级存在**。
- 抽取产物经 `backend/import_json_to_sqlite.py --source <9_final_all.json>` 导入 SQLite，
  再经 `sync_sqlite_to_neo4j.py` 同步到 Neo4j。
- 旧问答用的实体抽取器是另一套（`backend/entity_extract/extractor.py`，为低延迟的规则+Ollama 方案），
  与本目录的 DeepSeek 三轮抽取**互不共享规则与归一逻辑**。已知差异：词表（人物称号、政权关键词、
  停止词）与事件名规范化各自实现一份，同一事件可能出现两种名字。改动其中一套时请注意另一套不会自动跟随。

## 相关文档

- 模块内：`data/annotations/README.md`（人工标注字段规范，评估分子分母的定义）、
  `war_extraction/geocoding/README.md`（地理编码子系统）。
- 项目级（只引用这四个）：[`docs/README.md`](../docs/README.md)、
  [`docs/项目现状与后续计划.md`](../docs/项目现状与后续计划.md)、
  [`docs/集成与入口约定.md`](../docs/集成与入口约定.md)、
  [`docs/项目审查与修复历史.md`](../docs/项目审查与修复历史.md)。
