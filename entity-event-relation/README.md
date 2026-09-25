# entity-event-relation — 知识抽取与评估

基于大语言模型的历史战争文本知识抽取与评估流水线：从非结构化战争史文献中抽取实体、事件与关系，
构建结构化知识图谱数据，并提供与人工标注对比的评估体系。

**这一部分不参与 Web 运行**——它是离线流水线，产物（`output/` 下的 JSON 与 Excel）再由
`backend/import_json_to_sqlite.py` 导入 SQLite，进而同步到 Neo4j。
被旧后端的「文本实体识别」页调用时走 `backend/blueprints/llm.py` 的 `/api/extract/entities-events`
（第 7 轮路由蓝图拆分后从 `app.py` 迁入）。

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
├── pyproject.toml             # 包定义（包名 war_extraction，P2-4 起为正式包）
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
│   └── threshold_sensitivity.py # 四阈值敏感性扫描（EER-9）
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

## 快速开始

### 1. 安装依赖

依赖声明在 `pyproject.toml`（P2-4 打包后本模块是正式包 `war_extraction`）。两种装法：

```bash
# A) 只跑本模块的抽取/评估（装依赖 + 把包本身装上）
pip install -e entity-event-relation

# B) 作为旧后端的依赖一起装（仓库根执行；backend 以 war_extraction 引用本模块）
pip install -r requirements.txt && pip install -e entity-event-relation
```

> `-e`（可编辑安装）改了本模块代码立即生效，不必重装。**不装会出现
> `ModuleNotFoundError: No module named 'war_extraction'`**——旧后端启动即失败。

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
写明了"生成于第 9 轮定序化之前、值不可复现、不要当回归基线"。要覆盖它们必须显式写
`--output evaluation/latest`，届时脚本会先打出覆盖警告。

要人工核对一次**完整评估**的跨进程一致性（CI 里没有 `output/` 制品，跑不了这一步）：

```bash
cd entity-event-relation
for s in 0 1 2; do PYTHONHASHSEED=$s python evaluate.py --output ../.eval-check-$s; done
# 然后递归对比三份 results.json：除 metadata.evaluated_at 外应逐字段相同
```

### 6. 运行测试

```bash
cd entity-event-relation
python -m pytest tests -q
```

常驻用例（**12 个文件、109 例**，都已进 CI 的 `legacy-backend` job = `.github/workflows/ci.yml`）：

| 用例 | 钉住的回归 |
| --- | --- |
| `tests/test_evaluator_deterministic.py` | EER-15：同一输入两次评估必须逐字段相同（开 4 个不同 `PYTHONHASHSEED` 的子进程比对完整 `evaluate_relations` 返回） |
| `tests/test_normalizer_noise.py` | EER-4：「等 N 方国 / 等 N 国 / 等 N 部落」这类噪声地名必须被判为噪声 |
| `tests/test_cache_manager.py` | EER-11 原子写：写一半崩溃后旧索引仍完整、悬挂与损坏条目被摘除 |
| `tests/test_paths_and_cache_gc.py` | C-2 路径锚定（默认缓存/配置目录不随工作目录变）、配置缺失不再静默、孤儿条目 GC 与 TTL |
| `tests/test_orchestration_single_entry.py` | C-1：任何一侧都不许自建阶段循环（AST 断言）、共享编排的钩子位置/失败策略/缓存命中 |
| `tests/test_relation_types.py` | C-6：五个规范事件-事件关系类型必须精确相等（修前「因果关系 vs 顺承关系」算匹配） |
| `tests/test_prompt_version.py` | C-4：提示词版本由源码哈希派生，改一个字符就换版本、缓存键随之失效 |
| `tests/test_geocode_amap.py`、`tests/test_geocoding_pipeline.py`、`tests/test_geocoding_db_path.py` | EER-12 重试/配额/逐条落盘、A-2/A-3 一批一进度文件与配额截断提示、EER-7 路径口径 |
| `tests/test_shared_helpers.py` | EER-6：公共 JSON/多值/年份/仲裁实现，含**刻意保留**的差异（两个 JSON 策略不可互换） |
| `tests/test_decision_filters.py` | 决策项收口：人名过滤名单已删除（`秦始皇`/`吴起` 是合法人名）、占位词排除集统一为宽口径、`utils/alignment.py` 已移除 |

每条用例都是先确认「修前失败」才入库的（清单见 `docs/修复实施记录-第11轮-20260925.md` 的验证表）。
`tests/fixtures/relation_slice_repro.json` 是 EER-15 的最小复现夹具（从 `9_final_all.json`
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
   最后按三元组 (head, relation, tail) 算 P/R/F1。关系类型的判定分两种（第 11 轮 C-6 修正）：
   两侧都落在五个规范事件-事件关系类型里时**要求精确相等**（修前它们两两 `fuzz.ratio` 都是 50、
   都过阈值 40，于是类型判错也拿满分）；其余自由文本关系名仍走模糊比对（阈值 40%）。
6. **综合结果**：输出实体 F1、事件 F1、关系 F1 与三者的**宏观平均 F1**。

阈值都在 `config/eval_config.json` 里，改阈值不需要动代码；"换阈值指标会动多少"用
`python tools/threshold_sensitivity.py` 扫（一次一因子，默认约 8 分钟，输出 markdown 表）。

**可复现性（EER-15）**：模糊匹配的贪心过程必须与集合迭代顺序无关。`filter_relations` 与
`build_gold_triples` 返回的是**集合**，而 Python 的字符串哈希每进程随机化——直接迭代的话，
同一份 pred / gold / config 换个进程就会得到不同的 tp/fp/fn。实测（第 9 轮，全量数据）：

| PYTHONHASHSEED | filtered_pred | tp | fp | fn | 关系 F1 |
| --- | --- | --- | --- | --- | --- |
| 0 | 888 | 739 | 149 | 360 | 0.7438 |
| 1 | 888 | 743 | 145 | 356 | 0.7479 |
| 2 | 888 | 741 | 147 | 358 | 0.7458 |

因此匹配前先把两个集合按内容定序（`match_relation_triples`），错误样例也定序后再截前 20 条。
**不要**改成只按相似度排序（并列时又会依赖输入顺序），也**不要**用固定 `PYTHONHASHSEED` 掩盖——
那只是把症状藏起来。改动这块请跑 `python -m pytest tests -q`（见下节）。

## 与旧后端的关系

- 旧后端通过正式包 `war_extraction` 引用本目录（P2-4 打包后不再走 `sys.path`），提供
  `/api/extract/entities-events`（文本实体/事件识别页）；**因此本目录必须与 `backend/` 同级存在**。
- 抽取产物经 `backend/import_json_to_sqlite.py --source <9_final_all.json>` 导入 SQLite，
  再经 `sync_sqlite_to_neo4j.py` 同步到 Neo4j。
- 旧问答用的实体抽取器是另一套（`backend/entity_extract/extractor.py`，为低延迟的规则+Ollama 方案），
  与本目录的 DeepSeek 三轮抽取**互不共享规则与归一逻辑**。已知差异：词表（人物称号、政权关键词、
  停止词）与事件名规范化各自实现一份，同一事件可能出现两种名字。改动其中一套时请注意另一套不会自动跟随。
