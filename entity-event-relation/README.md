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
| 事件层 | 战争事件 | 事件识别 → 补全事件要素（名称、类型、时间、地点、参战方、结果、影响等） |
| 关系层 | 四类关系网络 | 事件-地点、事件-组织、事件-人物、事件-事件 |

**没有子事件。** 事件模型是"两级结构（事件识别 → 完整要素+关系）"，子事件、生命周期阶段
这两样在代码里从不存在（`models/events.py` 写明"简化版：删除子事件"），
已定决策是**平铺为独立事件、不恢复父子结构**（整改方案 10.1 第 2 项）。
同理，`EventType` 由完整事件阶段输出，**没有单独的"事件类型判定"阶段**——
原先那个 `EVENT_TYPE_PROMPT` 与 `extract_event_type()` 全仓无调用者，已随死代码一起删除。

关系类型的标准名与别名表在 `config/relation_types.json`（如"发生地点"吸收"主战场/战场/战略要地"），
别名归一在 `war_extraction/utils/normalizer.py`；**枚举取值的权威表**在
`war_extraction/utils/vocabulary.py`（`OrgType`/`Role`/`EventType`/四类关系名），
前端下拉、RAG `field_map`、后端导入白名单都应与它一致（有 `tests/test_enum_synchronization.py` 钉着）。

## 目录结构

```text
entity-event-relation/
├── main.py                    # 抽取主入口（命令行）
├── evaluate.py                # 评估主入口
├── pyproject.toml             # 包定义（包名 war_extraction，正式包：pip install -e entity-event-relation）
├── war_extraction/            # 原 src/（包名避开顶层 src，避免撞名）
│   ├── config.py              # 分段参数、提示词/配置/schema 三种版本（都由源码哈希派生）与缓存上下文
│   ├── core/
│   │   ├── llm_client.py      # DeepSeek API 封装（密钥、重试、错误提示）
│   │   ├── text_cleaner.py    # 输入清洗（不可见字符、行内硬换行、可配置错字表）+ 章节标题定位
│   │   ├── text_splitter.py   # 长文本分段（章节标题 → 段落 → 句子边界）
│   │   ├── extraction_runner.py # **抽取编排唯一实现**（分段 + 三阶段 + 失败诊断），backend 也走它
│   │   └── cache_manager.py   # 基于文本 MD5 的调用缓存（原子写 + 孤儿 GC + 可选 TTL）
│   ├── prompts/               # 三轮递进提示词（实体 / 事件识别+完整事件 / 关系）
│   ├── extractors/            # entity_extractor / event_extractor / relation_extractor
│   ├── models/                # 抽取结果的 pydantic 模型（entities / events / relations）
│   ├── processors/
│   │   ├── result_merger.py   # 多段结果合并与去重（事件身份键四处统一）
│   │   └── json_to_excel.py   # JSON → Excel（只读 9_final_all.json）
│   ├── evaluation/            # optimal_evaluator（唯一在用的评估器），评估口径见其 docstring
│   ├── geocoding/             # 历史地名 → 现代坐标（高德 API + 人工审核），见 war_extraction/geocoding/README.md
│   └── utils/                 # normalizer、**vocabulary（枚举权威表）**、provenance（产物溯源）、
│                              # publish_rules（书本特化规则的加载）、entity_classifier、json_payload、
│                              # value_parsing（含事件身份键）、relation_rules
├── config/                    # aliases.json / dynasty_ranges.json / eval_config.json / relation_types.json
│                              # + publish_rules.json（发布过滤的本本特化规则，含「过宽概括」唯一特征表）
│                              # + text_cleaning.json（输入清洗规则与错字表）
├── data/                      # 输入原文 + data/annotations/ 参考标注（**来源与字段口径见其 README**）
├── output/                    # 抽取结果（按运行批次分目录，自动生成）
│   └── 中国历代战争简史/       # 当前采用的那一批（见下方「批次目录只有 9_final_all.json」）
│       ├── 9_final_all.json    # 聚合结果（实体+事件+关系+质量报告，唯一权威产物）
│       ├── published/          # 发布子集（过滤+裁剪后的版本，事件与 final_all 不同）
│       ├── candidate/          # 候选区（未进发布子集的记录，2026-09-26 起落盘）
│       └── excel/              # 便于查看的表格（含 全部数据.xlsx，为其余 8 张的合集）
├── cache/                     # API 调用缓存（同时是产物的可复现路径，别随手清）
├── evaluation/                # 评估结果目录（`latest/` 与 `recheck-*/` 是**平级**的加注历史基线）
│   ├── latest/                #   历史基线（加注说明过：值属旧口径且不可复现，别当回归基线）
│   ├── recheck-micro-20260925/ #  同上（recall 改 micro 口径那次的重跑）
│   ├── baseline/              #   冻结基线（产物/标注/配置/提示词的 sha256 + 标注来源说明）
│   │                          #   + 两份体检报告：health_check_before.json（加事件字段级检查**之前**）
│   │                          #     与 health_check_after.json（**当前基线**，日常 --baseline 用它）
│   ├── review/                #   分层抽样导出的人工核验表（**入库**：跨版本比对要用它当基准）
│   │                          #   + sample_<seed>_with_source.csv：判定用的那份（上下文换成原文，见 docs/抽样判定规范.md）
│   └── run_<时间戳>/           #   逐次运行默认写到这里（不入库）
├── tools/
│   ├── threshold_sensitivity.py   # 四阈值敏感性扫描
│   ├── freeze_baseline.py         # 冻结基线指纹（产物/标注/配置/提示词）
│   ├── artifact_health_check.py   # 产物体检（不依赖参考集，可当 CI 门禁）
│   ├── event_pairing_review.py    # 事件配对约束复核（导出被拆配对，人工判约束是否过严）
│   ├── sample_for_review.py       # 分层抽样导出人工核验表 / 跨版本定位复核（B 组）
│   ├── expand_review_context.py   # 给核验表补原文上下文（含前后段），判时间要用
│   ├── summarize_review.py        # B 组判定汇总（精确率 + Wilson 区间 + 错误类型）
│   ├── annotation_io.py           # 参考集的读取与身份口径（定位键 / 稳定 ID，一处实现）
│   ├── assign_annotation_ids.py   # 参考集稳定 ID 分配与冲突检测（C 组第 2 步）
│   ├── backfill_annotation_evidence.py  # 参考集的证据与原文 offset 回填（C 组第 3 步）
│   ├── diff_annotation_sets.py    # 新老参考集逐条 diff（改标注的机械留档）
│   ├── sample_gold_for_review.py  # 从参考集抽样做"旧标注判定实验"（C 组第 0 步）
│   ├── build_draft_annotation_table.py  # 按朝代子集切"草稿核验表"（C 组第 2 步）
│   ├── apply_rebuild_table.py     # 把核验结论落成参考集 JSON（C 组第 3 步）
│   ├── summarize_rebuild_table.py # 核验表汇总 + 两人一致率（C 组第 3/6 步）
│   └── split_annotation_set.py    # 成稿按朝代切 development / test（C 组第 7 步）
└── tests/                     # 常驻用例（见「快速开始 7」），已全部进 CI
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
保留当时的当前版本 196 条。删缓存只损失"重跑时省下的 API 费用"，不影响任何产出。

> **2026-09-26 更正**：那 196 条**现在也已全部失效**。`7c1f862`（2026-09-25，第 11 轮）把
> `PROMPT_VERSION` 从硬编码的 `"prompt-v2-20260420"` 改成带源码哈希的派生值
> （现为 `prompt-v2-20260420+a1fb7839`），而这 196 条记录写的是旧的不带哈希串
> （见 `cache/cache_index.json` 的 `context.prompt_version`）。
> **提示词文本本身一直没改过**（三份 `prompts/*.py` 的 blob 哈希自初始提交至今相同），
> 变的是版本串的派生方式。因此下一次整本抽取会重新调用模型 195 段、全额付费；
> 不要靠"改回版本串"来强行复用——缓存里存的是过后处理链的结果，
> 而后处理代码此后改过多次。详见
> [`docs/数据提取模块分析与整改方案.md`](docs/数据提取模块分析与整改方案.md) 第 2.5.1 节。

### 批次目录只有 `9_final_all.json`（2026-09-26 起）

`main.py` 原先会在批次目录里另写 `1_places.json` … `8_events.json` 八个**分步中间产物**，
它们与聚合文件的对应字段逐字节一致（约 20 MB/次），属纯重复。因此：

- **产出一侧已停止写这八个文件**（`10_quality_report.json` 保留，它被用例引用）；
- **消费一侧已改为只读聚合文件**：`json_to_excel.convert_all()` 从
  `9_final_all.json` 在内存里切片生成全部表格，批次目录只要有聚合文件就能出 Excel
  （改之前读分步文件，那批文件被删后就一直 `FileNotFoundError`）。

所以现在从现有 JSON 重新生成 Excel 直接跑 `main.py` 之外的这一句即可，不需要先拆回分步文件：

```python
# python 交互式运行；在 entity-event-relation/ 目录下
from pathlib import Path
from war_extraction.processors import JsonToExcelConverter
JsonToExcelConverter().convert_all(Path('output/中国历代战争简史'), '中国历代战争简史')
```

批次目录的完整结构：

```text
output/中国历代战争简史/
├── 9_final_all.json          # 全量产物（唯一权威；含 entities/events/relations/quality_report）
├── 10_quality_report.json    # 等于 9_final_all.json 的 quality_report 字段
├── published/                # 发布子集（过滤+裁剪后的版本）
│   ├── final.json
│   └── quality_report.json
├── candidate/                # 候选区（2026-09-26 起落盘）
│   ├── final.json
│   └── quality_report.json
└── excel/                    # 由 9_final_all.json 生成的表格
```

**候选区为什么要落盘。** 原先 `split_publishable_outputs` 算出的 candidate 只在内存里存在、
算完即丢，于是"哪些记录没进发布子集、为什么"完全不可查。现在它写到 `candidate/`，
`metadata.publish_split_stats` 里还能看到"因枚举不合法被挪走"的分项计数。
下游只导 `9_final_all.json` 与 `published/final.json`，这个目录对它们没有影响。

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
另可设 `API_BASE_URL`（默认 `https://api.deepseek.com/v1`）与 `DEEPSEEK_MODEL`
（默认 `deepseek-flash`）。也可写在 `config/.env` 中（该文件已被 git 忽略）：

```bash
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-flash
API_BASE_URL=https://api.deepseek.com/v1
```

> **模型名这件事有两个坑，都实测过（2026-09-26）：**
>
> 1. **`deepseek-chat` 是别名，服务端把它路由到 `deepseek-flash`**——`GET /models` 里
>    只有 `deepseek-flash` 与 `deepseek-v4-pro` 两个名字。所以填 `deepseek-chat` 能跑，
>    但产物里写的模型名与实际服务方不一致。现在 `metadata` 里**分开记两个字段**：
>    `model`（请求名）与 `model_served`（响应里的实际服务名），并给出 `thinking_mode`。
> 2. **显式写 `deepseek-flash` 会默认进入思考模式**：可见内容会是**空字符串**
>    （实测 `prompt_tokens` 从 7 涨到 33、`completion_tokens` 里全是 reasoning），
>    而且温度参数失效。所以客户端在命中这类模型名时会自动带上
>    `thinking={"type":"disabled"}`（实测与别名路径逐项一致）；设 `DEEPSEEK_THINKING=auto`
>    可关掉这个自动行为。真出现空内容时 `LLMApiError` 会直接说明"大概率是思考模式吃掉了可见内容"，
>    而不是让下游报一句莫名其妙的"JSON 解析失败"。

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
#   --no-clean       跳过输入文本清洗（默认清洗：OCR 错字、行内硬换行、不可见字符）
#   --output DIR     输出目录，默认 output
```

结果写入 `output/<文本名>/`：权威产物 `9_final_all.json`、`10_quality_report.json`、
`published/`、`candidate/` 与 Excel 文件（目录结构见上面「批次目录只有 `9_final_all.json`」）。

**输入清洗默认开启**，统计写在产物 `metadata.text_cleaning` 下（合并了多少处行内换行、
订正了多少处错字、长度变化）。错字订正表在 `config/text_cleaning.json` 的 `ocr_fixes`，
**默认是空的**——只有确知的错字才登记，猜出来的替换会静默改掉原文。

### 5. 运行评估

```bash
python evaluate.py
#   --pred  PATH       预测结果 JSON，默认 output/中国历代战争简史/9_final_all.json
#   --also-published   额外评估发布子集 published/final.json（不给值自动取同批次）
#   --output DIR       评估输出目录，默认 evaluation/run_<时间戳>
```

**指标必须连着口径一起读**，`results.json` 的 `metadata` 已经把两件事分开记了：

- `metadata.predictions`：**被评估产物的自证**（文件 sha256、产物自己写的
  `prompt_version`/`extraction_version`/`model`/`git_commit`）。
  原先这里直接写"评估时"的版本串，对旧产物重跑评估就会把它标成当前提示词版本；
- `metadata.evaluator`：**本次评估运行的代码与口径**（评估器版本、`git_commit`、
  四个阈值 + 新增的事件配对约束、`eval_config` 的 sha256、标注文件指纹）。

`summary` 里现在同时给 `entity_f1`（mention 口径）与 `entity_f1_canonical`（别名归一后
去重的口径）。两套口径差多少见下面「评估设计」。

每次运行写到**自己的** `evaluation/run_YYYYmmdd_HHMMSS/`（不入库）。`evaluation/latest/` 与
`evaluation/recheck-micro-20260925/` 是**跟踪入库的历史基线**：它们的 `metadata.snapshot_note`
说明了来历与局限（值不可复现、不要当回归基线），点开那份 JSON 就能看到原文。
要覆盖它们必须显式写 `--output evaluation/latest`，届时脚本会先打出覆盖警告。

**`evaluation/baseline/` 与 `evaluation/baseline_after/`（2026-09-26 起）**：一对冻结记录，
各自含 `baseline.json`（产物/标注/配置/提示词各自的 sha256 + 提交号 + 标注来源说明）、
`baseline.md`（人读版）、`baseline_results.json`（指标来源的评估结果副本）。

- `baseline/` = **改前**（旧评估口径：宏观 F1 44.68%）；
- `baseline_after/` = **改后**（新口径：宏观 F1 25.88%，同一份旧产物）。

两份的**产物 sha256 相同**（`885b0d9e…`），所以差异**全部来自口径**，不是产物换代——
这正是"报告指标必须同时给出口径"的落点。重跑生成：

```bash
python tools/freeze_baseline.py --eval-dir evaluation/run_<时间戳> --output evaluation/baseline_after --note "说明"
```

要人工核对一次**完整评估**的跨进程一致性（CI 里没有 `output/` 制品，跑不了这一步）：

```bash
cd entity-event-relation
for s in 0 1 2; do PYTHONHASHSEED=$s python evaluate.py --output ../.eval-check-$s; done
# 然后递归对比三份 results.json：除 metadata.evaluated_at 外应逐字段相同
```

### 6. 产物体检与抽样核验（不依赖参考集）

```bash
python tools/artifact_health_check.py                 # 打印体检报告
python tools/artifact_health_check.py --json h.json   # 存 JSON，供逐版比对（报告含产物指纹 source_sha256）
python tools/artifact_health_check.py --json h.json --note "这份基线对应哪份产物"
python tools/artifact_health_check.py --baseline h.json   # 任何门禁项变差就退出码 1（可当 CI 门禁）

python tools/sample_for_review.py --total 350         # 导出人工核验表（CSV + JSON）
python tools/sample_for_review.py --compare evaluation/review/sample_<seed>.json --pred <新产物.json>

python tools/expand_review_context.py                 # 给核验表补原文上下文（含前后段），生成可判定的表

python tools/summarize_review.py                      # 判完之后汇总：精确率 + Wilson 区间 + 错误类型
```

人工核验表分两步用：`sample_for_review.py` 导出"记录 + 模型给的证据"（冻结基准），
`expand_review_context.py` 再把上下文换成**原文里那一段**（前 900 字 / 后 200 字，
`〈 〉` 标出证据位置，并单独留一列放模型自己写的证据）——判时间时需要的年份常写在
证据段的前面，只看证据会判错。**逐条怎么判见 [`docs/抽样判定规范.md`](docs/抽样判定规范.md)**，
判完用 `summarize_review.py` 汇总（精确率的分母不含"无法判断"、区间用 Wilson、
错误类型按判据的固定前缀统计）。

**B 组第一次判定的结果（2026-09-27，350 条全判完）**：整体**抽样精确率 82.8%**
（270 对 / 270+56，Wilson 95% **[78.4%, 86.5%]**），"无法判断" 6.9%。
分类看：**关系 74.4%**（错的全是关系类型或方向，没有一条是"证据不足"）、
事件 90.1%、实体 92.3%。结论与它对阶段三的影响写在
[`docs/数据提取模块分析与整改方案.md`](docs/数据提取模块分析与整改方案.md) §0.6；
本次汇总落盘在 `evaluation/review/summary_20260926.json`。

两个工具的存在理由：**现行参考集不可信**（见 `data/annotations/README.md` 顶部的来源更正），
所以"改好了没有"不能只看 F1。体检脚本查的是**绝对数字**（悬空边、枚举外取值、重复行、
残缺年份、方向与时间矛盾、证据复用率、关系量级，以及事件字段级的占位词/攻守方非组织值/
`Result` 弱值），与参考集无关，因此能当门禁；抽样脚本用的是"分层随机抽样 + 稳定定位"，
跨版本可比、也不依赖参考集。
导出的表格（`evaluation/review/`）**要入库**：它是一次投入、长期复用的基准，
丢了就得重新抽一批、也就无法与上一版比。

门禁「枚举外关系名」已扣掉 `KNOWN_ENUM_EXCEPTIONS` 里**已登记**的例外（按键匹配），
所以现产物上是 0；若它非零，说明出现了**未登记**的枚举外取值，报告会把取值点名列出。
另一条别误读：`--baseline` 比的是两份**读同一份旧产物**的报告，
"门禁未变差"**只说明评估侧口径变更不改变体检项**，抽取侧修复要等重跑后的产物才能验证
（见指南 §3.1 与 `evaluation/baseline_after/baseline.json` 的 note）。

第三阶段 D3 补的三项事件字段级门禁（占位词 / 攻守方非组织值 / `Result` 弱值）**都是基线值，
不是"应当为 0"**（旧产物上分别是 1897 / 255 / 26）——语义是"不应比上一版更多"。
E2 又加了第四项「同一事件多条统帅」（基线 **698**，目标同样不是 0：双方各一位主帅是合理的），
它盯的是 `Commanders`（"指挥官列表"）被逐人当成"最高指挥官"这件事——规则已改（见指南 §1.27），
**数字要重跑后才看得到下降**。
**入库的当前基线是 `evaluation/baseline/health_check_after.json`，日常对照用它**
（17 项全部可比）。而 `health_check_before.json` 是加这些检查**之前**的报告，对着它跑时
「攻守方非组织值」与「`Result` 弱值或占位」会判为"**无法比较**"并被点名跳过——
这不是漏检，而是"新门禁第一次纳入时不能拿缺失当 0"（否则门禁一建就是红的，A2 那个坑）。
**"跳过"还有反向的坑**：若入库基线永远缺那几节，那几项就等于没有门禁，
所以 `test_入库基线能取到全部门禁项` 钉住这件事——以后加了门禁项却忘了重生成基线，
用例会直接变红并给出命令。两份基线都记了产物指纹 `source_sha256`（`health_check_after.json`
另带一句 `--note` 说明来历）。
其中「事件字段写占位词」还有一层特殊性：如实写"不详"是**正确**行为，E1 的改动方向正是
"不确定就写不详"，所以这一项**上升可能是对的**，届时应更新基线并写明理由，不要当成回归。

### 7. 运行测试

```bash
conda activate china-war-py311
cd entity-event-relation
python -m pytest tests -q
```

常驻用例（**24 个文件、267 例**）已全部进 CI，跑在上面说的 `legacy-backend` job 里：

| 用例 | 钉住的回归 |
| --- | --- |
| `tests/test_evaluator_deterministic.py` | 评估可复现：同一输入两次评估必须逐字段相同（开 4 个不同 `PYTHONHASHSEED` 的子进程比对完整 `evaluate_relations` 返回） |
| `tests/test_artifact_provenance.py` | 产物自证：内容哈希可自校验、metadata 记录生成环境、`events.metadata` 不再在合并/清理时丢掉、Excel 只依赖聚合产物、评估 metadata 分组 |
| `tests/test_enum_synchronization.py` | 枚举的跨模块同步：前端图谱下拉、RAG `field_map`、后端导入白名单必须与权威表一致（直接读那几份文件比对）；`指挥所` 落地为"组织侧拒收而非错配"、朝代归一表的归属（第二轮工作单 B8/C3） |
| `tests/test_relation_rules.py` | 事件-事件关系的类型仲裁、方向判定（证据→时间→不猜）、收敛去重与定序 |
| `tests/test_derive_relations.py` | 派生关系绑定同句证据与具体实体：主战场不再无条件、议和不再扩散到所有组织、君主不再按名字里的字判；阶段三 E2 的 `Commanders` 派生 `将领`、同一人物不再同时挂统帅与将领 |
| `tests/test_evaluator_capabilities.py` | 新评估能力：事件配对四条约束规则（同名放行/缺值放行/朝代按时代档位/残缺年份不参与/地点按集合）、关系按条计数、两套身份口径、字段值准确率、分维度报告；`event_year_tolerance`/`also_published`/`summary_published`（第二轮工作单 A4）、`by_category` 与整体同一次匹配（B5） |
| `tests/test_health_gate.py` | 体检门禁「枚举外关系名」扣掉已登记的例外（按键匹配、要求取值确实在报告里）、未登记的取值一条都不扣、旧版报告缺例外表也能比对照（第二轮工作单 A2）；第三阶段 D3 的三项事件字段级门禁（攻守方填人名 +1、同名朝代不算地点混入、`Result` 弱值与占位词、占位词按字段分列）、「基线缺项 → 跳过并点名」的对照语义、**入库基线必须覆盖全部门禁项且指纹与磁盘产物一致**（防止门禁建了却永远比不出来） |
| `tests/test_review_context.py` | 抽样核验表的上下文扩全：`定位键` 与冻结样本**逐行一致**（判定结果要映射回基准）、生成表三列留空且原表内容一字不动、`崤底之战` 那类"时间在上一段"的行必须带出 `公元27年`、模型删掉括号后仍能定位、标成 `记录证据` 的行证据确实在上下文里、定位不到时**不编造**上下文 |
| `tests/test_annotation_tools.py` | 参考集工具链的口径：稳定 ID 只跟「名称+朝代」绑定、整数年份不崩、冲突检测把**重复行**与**同名不同年代**分开报、diff 把记账字段排除在字段变更之外、核验表汇总把「没填」单列（不混进分母）、IAA 按定位键配对、切分让**关系跟着 head 事件走**（head 不在事件表的不猜边） |
| `tests/test_review_summary.py` | B 组汇总的口径：精确率分母**不含"无法判断"**、Wilson 区间在极端比例上不越界（分层只有 8 条）、判据前缀只认带冒号的规范前缀（正文里出现"结果""时间"不算）、错误类型只统计判错的行、分类按冒号前那级合并 |
| `tests/test_health_gate.py` | 体检门禁「枚举外关系名」扣掉已登记的例外（按键匹配、要求取值确实在报告里）、未登记的取值一条都不扣、旧版报告缺例外表也能比对照（第二轮工作单 A2）；第三阶段 D3 的三项事件字段级门禁（攻守方填人名 +1、同名朝代不算地点混入、`Result` 弱值与占位词、占位词按字段分列）、「基线缺项 → 跳过并点名」的对照语义、**入库基线必须覆盖全部门禁项且指纹与磁盘产物一致**（防止门禁建了却永远比不出来） |
| `tests/test_round2_closeout.py` | 第二轮收口：枚举外事件-事件关系保留并计数、悬空边四类计数、清洗统计在顶层 metadata、清洗开关可关、`source_offset` 是原文坐标、基线指纹与 run_id、`model_served` 自证；第二轮工作单的残缺关系计数（B3）、诊断为空时清洗统计仍落盘（B4）、`包含关系`/`条件关系` 进发布子集（C1）；第三阶段 D1（置信门槛对枚举外类型返回 False、对包含/条件仍按证据非空）与 D2（`source_offset` 仍未接线的 AST 守卫，含接线时要改哪四处的清单） |
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
2. **贪心最优匹配 + 语义约束**：按相似度降序依次匹配，保证每个标注事件只匹配一个预测事件。
   相似度只是**入场券**，配对还要过三道语义约束（两侧都有值时才判，缺值不拦）：
   朝代相容、起始年份相差不超过 `event_year_tolerance`（默认 30 年）、首个地点互相包含。
   *为什么必须加*：只有 0.35 相似度时实测出现过"红巾军起义 → 英军挑起亚罗船事件"（相似度 0.25）
   这类跨战争错配，它会同时虚增事件 TP、并把 gold 关系挂到错误的预测事件上。
3. **实体评估（两套身份口径一起报）**：
   - `mention` 口径：预测侧**逐行**计数、参考侧压成名称集合，一对一模糊匹配（阈值 70%）
     ——与历史指标可比；
   - `canonical` 口径：两侧先做别名归一（`Normalizer` + `aliases.json`），预测侧按规范名去重。
   两套差多少直接说明"身份口径不一致"的代价。实测同一份产物：
   实体 F1 从 20.42%（mention）到 26.98%（canonical）——**这 6.5 个百分点是口径差异，不是模型错误**。
4. **事件评估**：基于映射表算 TP/FP/FN；另按标注侧朝代分组报（某一朝代整体抽得差会显形）。
5. **关系评估**：只评估与已对齐事件相关的关系（`filter_relations`），**不再用 gold 提前筛一遍**
   （原先的 `check_exists()` 预过滤已删除，见「已知局限」第 2 条）。先做关系类型归一，
   再做尾实体模糊匹配，最后按三元组 (head, relation, tail) 算 P/R/F1，并按四类分别报告。
   关系类型的判定分两种：两侧都落在五个规范事件-事件关系类型里时**要求精确相等**
   （这五个类型两两 `fuzz.ratio` 都是 50、都过阈值 40，若走模糊比对则类型判错也拿满分）；
   其余自由文本关系名仍走模糊比对（阈值 40%）。
6. **事件字段准确率**：除"填没填"（填充率 / 实质取值率，后者把"不详"算未填）之外，
   还在已配对事件上逐字段与标注比对"填得对不对"（时间、朝代、地点、攻守方、人物、结果），
   输出 `comparable/correct/accuracy/coverage`。
7. **综合结果**：输出实体 F1、事件 F1、关系 F1 与三者的**宏观平均 F1**。
   `--also-published` 会对发布子集再跑一遍并给出 `summary_published`——两份粒度不同
   （1050 vs 881 事件），指标**不可混用**，所以各自带上文件哈希分开报。

阈值都在 `config/eval_config.json` 里，改阈值不需要动代码；"换阈值指标会动多少"用
`python tools/threshold_sensitivity.py` 扫（一次一因子，默认约 8 分钟，输出 markdown 表，
表头会打上预测产物的 sha256，以便区分"阈值变了"和"产物换代了"）。

### 指标的分子分母

评估是"预测 vs 人工标注"的对比，每一层的分子分母必须写清楚，否则"指标降了"到底是
模型变差、标注口径变了还是评估器变了，事后无法区分（改标注的流程见
[`data/annotations/README.md`](data/annotations/README.md)）。

| 层 | 预测侧（FP 的来源） | 参考侧（FN 的来源） | TP |
| --- | --- | --- | --- |
| 实体 | 产物 `entities.places/persons/organizations` 的**逐行**条数（mention 口径） | `sample_entities.json` 三类的**名称集合**（同名多行压成一个） | 一对一模糊匹配命中的行数 |
| 事件 | 预测事件**名称集合**（先去重） | `sample_events.json` 的 `EventName` 集合 | 事件映射表与标注名集合的交集 |
| 关系 | 全部"事件能被映射到标注事件"的三元组（**不做 gold 预过滤**） | 只保留"head 能被映射到预测事件"的三元组 | 一对一贪心匹配上的三元组数 |

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

> **来源更正（2026-09-26）**：这批"标注"的实际制作方式是**几个人用不同的模型把整本书
> 分成几份、每个模型负责两份，最后把各份结果汇总，字段没有做人工处理**。
> 也就是说它**不是人工标注**，而是多模型输出的合并结果，当前指标测的是
> "模型 A 的产出"与"模型 B/C/D 汇总产出"之间的**分歧**。下面的表格写的是"只有一名标注者"，
> 那是本文件原先的表述，与实际不符；更正措辞待标注所有人确认（详见
> [`docs/数据提取模块分析与整改方案.md`](docs/数据提取模块分析与整改方案.md) 第 2.4.1 与 10.2 节，
> 以及 [`evaluation/baseline/baseline.md`](evaluation/baseline/baseline.md) 里冻结的来源说明）。

`data/annotations/` 是唯一评估来源，但它同时被用于调事件阈值、调关系阈值、加别名、
增删过滤规则、决定关系类型归一口径，最后又用来报分——**没有 train/dev/test 切分，
等于在测试集上持续调参**。三份标注的已知问题（可用
`python tools/artifact_health_check.py` 复现，数字见其"参考标注的结构问题"一节）：

| 问题 | 具体表现 |
| --- | --- |
| 不是人工标注（见上方更正） | 多模型分片汇总、无字段处理、无 IAA 与仲裁，无法区分"模型抽错"和"另一批模型口径不同" |
| 同类型内重复/冲突 | 地点同名多行 28 行、组织 37 行、事件 3 行（按名称统计）；评估把 gold 压成名称集合、重复被吞掉，预测侧却逐行计数 |
| 关系违反自身规范 | `event-place` 479 条里 head 不在事件表 230、tail 不在地点表 194；`event-org` 136/106；`event-person` 183/180；`event-event` 56/69，另有 **21 条完全重复**——这批关系进评估前就被 `build_gold_triples` 丢掉 |
| 缺少可追溯证据 | 事件 `source` 只有书名，无页码、章节、字符 offset 或原文 evidence；关系只有 `head/relation/tail`，无法逐条回溯原文 |
| 存在可确认的事实错误 | 14 处事件日期是 OCR/录入残值（如 `西摩尔联军受阻廊坊` 的 `153`）；个别关系标注与原文不符（如把"晋惠帝"同时标成"君主"和"阵亡"） |

结论：当前标注只能当**候选清单与词表**（事件名/人名/地名是书中真实存在的内容，也是
`config/aliases.json` 的来源），**不能当准确性指标的分母**，更不能用来判断"改好没改好"——
那会把新旧口径差异误读成质量升降。判断改进要用固定评估抽样集（见「快速开始 6」）。

### 2. 评估器本身的设计缺陷（影响指标解释）

> **本节前四项已在 2026-09-26 的整改中修复**，修复后的效果见「整改后的口径与指标」。
> 保留原文是为了说明"历史指标为什么不能直接与现在比"。

- ~~**关系评估有 gold 预过滤（循环过滤）**~~（**已删**）：`filter_relations` 里的
  `check_exists()` 先去 gold 关系里找相似的 head/relation/tail，只有"已经像某条 gold"的
  预测关系才进入 `pred_triples`。结果是原始预测关系里绝大部分**没有被算作 FP**，
  precision 被显著高估。现已改为：已对齐事件范围内的**全部**预测关系都进入 TP/FP 计算。
- ~~**事件匹配阈值 0.35 过低**~~（**已加语义约束**）：大量无关事件名对也能越过它，于是
  `黄巢农民起义 → 金田起义` 这类不同事件被判为同一事件。现在相似度只是入场券，
  还要过朝代 / 起始年份 / 首个地点三道约束。
- ~~**实体评估只用字符串匹配**~~（**已加 canonical 口径**）：`evaluate_entities()` 原先
  没有调用别名归一，所以 `aliases.json` 对实体评估不生效。现在两套口径一起报。
- ~~**事件身份只看名称**~~（**已统一**）：gold 里两个"扬州之战"分属不同年代，评估用名称集合
  会把它们合并。现在事件身份是"归一名称 + 朝代 + 起始时间 + 首个地点"，
  抽取器 / 合并期 / 清理期 / 发布期四处共用 `value_parsing.event_identity_key`。
- **`evaluate.py` 的 metadata 已分组**：`metadata.predictions` 记**被评估产物的自证**
  （文件 sha256、产物自己写的版本与模型），`metadata.evaluator` 记**本次评估的代码与口径**
  （评估器版本、`git_commit`、阈值、`eval_config` 的 sha256、标注文件指纹）。
  原先只有"评估时"的版本串，对旧产物重跑评估就会把它标成当前提示词版本。

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
- 实体评估曾经不使用别名归一（**已修**：现在同时报 `mention` 与 `canonical` 两套口径）。

同一份产物上两套口径的实测差：mention 口径 F1 = 20.42%，canonical 口径（两侧别名归一 +
预测侧按规范名去重）F1 = **26.98%**。也就是说低分里有约 6.5 个百分点来自
**数据模型与评估身份口径不一致**，不全是模型抽错。

**事件 F1 低（P 很低）**：预测事件数远多于 gold，粒度不同（gold 只标主要战争，
而预测包含章节级子战役与阶段行动，其中不少在原文里确有内容）；低阈值曾让大量无关事件名对
越过门槛（**已加语义约束**）。当前高召回仍部分依赖宽松模糊匹配——阈值越高 TP 与召回越低，
所以调阈值只是换一种错法，不是质量提升。

**关系 F1 看起来高，但不能相信**：旧口径它来自被 prefilter 后的分子分母（见上一节）。
去掉预过滤后参与评估的预测关系从 888 涨到数千，precision 从约 80% 掉到约 16%
（见下面「整改后的口径与指标」的实测表）。这个数仍受 gold 缺陷与事件映射影响，
不能当最终标准，但它证明了旧的精确率是虚高的。

关系层还有几类**数据正确性**缺陷（不只是评估问题，**均已在 2026-09-26 修复**）：

- ~~**派生关系过量**~~：`Place` 第一个地点一律判"主战场"、事件文本出现"议和"就给所有地点
  加"议和地点"、`Commanders` 一律生成"统帅"。现在线索必须与实体**同句**出现，
  主战场只给第一个有交战线索的地点。实测同一份产物：平均每个有关系的事件从
  **17.5 条降到 7.7 条**、极值从 **188 条降到 50 条**、证据与整段 `source_text` 逐字符相同的
  比例从 **62.6% 降到 24.1%**。
- ~~**事件-事件关系方向被按名称字典序改写**~~：现在方向按"证据里两个事件名的出现顺序 →
  事件起始年份"判定，两者都判不出来时**不合并** A→B 与 B→A（不猜方向）。
- ~~**"因果"被无条件降级为"顺承"**~~：原条件是"有顺承词**或**证据非空"，而证据几乎恒成立。
  现在只有证据里确实出现顺承词才降级。
- ~~**仍有非规范关系类型**~~：新增 `schema 校验层`，关系类型 / `OrgType` / `Role` / `EventType`
  不在枚举权威表（`war_extraction/utils/vocabulary.py`）里的记录进**候选区**、不进发布子集；
  同时把实际在用的取值扩散进权威表并同步下游（前端下拉、RAG `field_map`、导入白名单）。

### 3.1 整改后的口径与指标（2026-09-26）

同一份**旧产物**（2026-08-18 生成，未重跑抽取）在口径修正前后的实测对照。
**这些下降是修正，不是退化**——旧数是被评估方法抬上去的：

| 层 | 修前 F1 | 修后 F1 | 修后 P / R | 口径变更 |
| --- | ---: | ---: | --- | --- |
| 实体 | 20.42% | 20.42%（canonical 26.98%） | 11.50% / 90.67% | mention 口径未动；新增 canonical 口径并列报告 |
| 事件 | 41.75% | **25.98%** | 17.02% / 54.69% | 事件映射加语义约束；身份键统一（同名不同年代不再合并） |
| 关系 | 71.87% | **31.23%** | 19.20% / 83.44% | 删除 gold 预过滤；按四类分别报告 |
| 宏观 | 44.68% | **25.88%** | — | — |

发布子集（881 事件）另评一套：实体 20.62% / 事件 25.46% / 关系 27.08% / 宏观 24.39%。
两份粒度不同，**不可混用**。评估结果存放在 `evaluation/run_20260926_after/`；
改前对照在 `evaluation/baseline/`、改后记录在 `evaluation/baseline_after/`
（两份的产物 sha256 相同，差异全来自口径）。

分类明细（修后，更能看出该先修哪一类）：

| 关系类别 | P | R | F1 |
| --- | ---: | ---: | ---: |
| 事件-地点 | 9.85% | 85.89% | 17.68% |
| 事件-人物 | 21.55% | 81.62% | 34.10% |
| 事件-组织 | 33.19% | 89.69% | 48.45% |
| 事件-事件 | 31.88% | 47.83% | 38.26% |

事件字段**值准确率**（在 176 对已配对事件上）：`StartDate` 87.7%、`DynastyName` 98.9%、
`Place` 100.0%、`Person` 88.7%，而 `Aggressor` 只有 31.2%、`Defender` 41.5%、
`Result` 14.3%——**攻守方与结果这两项是当前最该修的核心字段**，
它们直接展示给用户，按用途定的目标是 P ≥ 95%。

### 4. evidence 非空率不等于数据正确

质量报告只检查 source/evidence **是否非空**，不检查"是否为原文句子、是否包含对应实体、
是否支持该关系类型、是否来自正确事件、是否只是整段文本重复使用"。实测把空白归一后
在原文中直接查找：地点 source_text 约 82%、事件 source_text 约 81% 能找到，
组织/人物只有约 58% / 60%；关系 evidence 里 event-place 约 73%、event-person 约 70%、
event-event 只有约 46%。不能直接找到不等于一定错（引号、OCR、拼接差异都可能），
但说明 evidence **不是稳定的原文引用**。

**派生关系的证据已改为"实体名所在的那一句"**（2026-09-26）：与原实现相比，
"证据与事件 `source_text` 逐字符相同"的比例从 62.6% 降到 24.1%（同一份产物的干跑实测）。
剩下那 24% 是"实体名确实不在原文片段里"的字段派生关系（模型把"周武王率诸侯之师"
规范化成了"周军"），此时退一步用事件的 `source_text`。**因此"证据非空率 100%"仍然
不能当作质量正确率。**

### 5. 输入文本质量会传导到抽取

输入是从书籍转换出的长文本，存在换行、断词和 OCR 错字（如 `日军` 被识别成 `目军`、
年份数字被拆行、章节标题与总结段和具体战例混在同一输入里）。分段器按约 1800 字、
重叠 200 字切分，边界优先句号；但重叠段会重复抽取，同名实体又按朝代/现代名称
保留多个版本，重复被进一步放大。

**已加的两件事（2026-09-26）**：

- **输入清洗**（默认开启，`--no-clean` 关闭）：清不可见字符、合并被折断的行内换行、
  按 `config/text_cleaning.json` 的 `ocr_fixes` 订正确知的错字。统计写在
  `metadata.text_cleaning`。`ocr_fixes` **默认空表**——猜出来的替换会静默改掉原文，
  比 OCR 错字本身更难查，所以只登记确知的；
- **分段优先在章节标题 / 段落边界切**：原先只找句末标点，切点不够就按字符硬切，
  而每段是独立送进模型的，硬切会把一章的开头切到上一段末尾。现在优先级是
  章节标题 → 段落空行 → 句末标点 → 字符位置，段落在 `ChunkExtraction.paragraph_id` 上有编号
  （人工抽检回原文时的定位坐标）。

### 6. 指标评的是"哪一版代码"要说清楚

`9_final_all.json` 的 metadata 现在记录：抽取时间、`extraction_version`、`prompt_version`、
`model`、`api_base`、`temperature`（分阶段）、`seed`（未设置为 `null`）、`git_commit`、
`artifact_sha256`（内容哈希，抠掉该字段后重算即可自校验）与 `text_cleaning`。
`events.metadata` 也不再在合并/清理阶段被丢掉，所以 `10_quality_report.json` 里的
`identified_event_count` / `postprocess_filtered_count` / `relation_degraded_stages`
等诊断项不再恒等。

但缓存的键包含 `prompt_version`、`config_version`（`config/*.json` 哈希）与
`schema_version`（模型源码哈希），**换过版本的缓存条目永远不会命中**——
提示词、别名表或 pydantic 模型一改，下一次抽取必然全部重新调用模型（花费用、且产物换代）。
所以做质量结论前先核对"产物 metadata 的版本 == 当前代码的版本"，否则评的是旧产物。
`python tools/freeze_baseline.py` 会把这份对照冻进 `evaluation/baseline/`。

## 整改进展（2026-09-26，两轮）

> 完整的逐层归因、跨模块契约、静默失败清单与分阶段验收命令见
> [`docs/数据提取模块分析与整改方案.md`](docs/数据提取模块分析与整改方案.md)。
> 那一节的「执行状态」记着哪些已落地、哪些还等付费重跑。

**两轮口径的实测对照**（同一份旧产物，抽取侧改动都还没进产物）：

| 层 | 改前 | 第一轮 | **第二轮（当前）** |
| --- | ---: | ---: | ---: |
| 实体 F1（mention / canonical） | 20.42% / — | 20.42% / 26.98% | 20.42% / **26.98%** |
| 事件 F1 | 41.75% | 25.98% | **34.15%** |
| 关系 F1 | 71.87% | 31.23% | **25.51%** |
| 宏观平均 F1 | 44.68% | 25.88% | **26.69%** |

第二轮的边界：**事件 F1 回升、关系 F1 继续下降都不是「模型变好/变差」**——事件回升是修掉了
配对约束的四处实现缺陷（配对 176→231），关系下降是配对放宽后进入评估的预测关系从 3228 条
涨到 5363 条（含 747 条归一后重复）。逐条证据见
[`docs/第一轮核验与遗留项.md`](docs/第一轮核验与遗留项.md) 的 §5.1。

原定的四个阶段里，**阶段 0~3 的代码部分已落地**（结果见上表），**阶段 4 未执行**：
它要真金白银调用模型跑完整本（按当前分段器 **211 段**，缓存全失效），而 `阶段 1` 的标注重建
（人工核验、IAA、train/test 切分）与 `阶段 1 附二` 的固定评估抽样集需要人工投入，
两者都不在这次代码整改范围内。所以**当前仍不建议**据此重跑整本——先按
`tools/sample_for_review.py` 导出的表格把抽样精确率做出来，再决定要不要付费。

仍未做、且代码改不动的事项：

1. **参考标注的重建**：人工逐条回原文核验、补 evidence 与字符 offset、双人复核算 IAA、
   切 development / test。这是"指标能不能信"的唯一出路，也是`candidate/` 与
   体检报告都在为之铺垫的事。
2. **`DynastyName` 的映射表：RAG 侧还没接**。位置已定（`war_extraction/utils/vocabulary.py`
   的 `DYNASTY_ALIASES`，backend 已改为引用它并删除抄写的那份），但 RAG 的运行环境没有
   `war_extraction` 依赖，接入方式（表随快照发布 / 加依赖）待重建快照时定——
   在此之前 RAG 的朝代取值仍是产物原文写法，与 backend 的归一结果不一致。
3. **`EventType` 取值域与 RAG 字典的对齐**：权威表是 29 值（提示词枚举 21 ∪ 实际在用的 8），
   而 RAG 侧治理后的标准词典是 27 项；重跑后如果出现 `党争军事化` / `军事同盟`，
   RAG 的 `data-contract.md`"27 类"口径要同步。**对齐方向已定：以权威表为准改词典**
   （反向收窄权威表会让模型产出的合法取值被挪出发布子集）。
4. **`AliasNames` 是否进 SQLite/Neo4j**：**已结案：不进**。理由与代价写在
   `war_extraction/models/events.py` 的字段注释里——没有任何下游在读它，
   进库要新增列（含存量库迁移）+ `to_dict` + Neo4j 属性 + 前端一起改；
   改主意时按 `backend/node_property_mapping.py` 的 `API_TO_COLUMN` 加一行即可。
5. **发布过滤的 `包含关系`/`条件关系` 修复已写但未上线**：代码与用例已完成
   （指南 §1.24），但它等于改整个知识库内容，必须与产物换代、SQLite 重导、
   Neo4j 重同步、RAG 快照重建**打包成一次发布**。在此之前两类关系仍不进 `published`。
6. **`source_offset` 暂未接线**：字段已具备，但 chunk 级信息不序列化，产物里拿不到；
   等参考集重建（需要逐条回原文定位）时再接进
   `events.metadata.extraction_diagnostics.chunks[]`。这条状态**现在有机械守卫**：
   `tests/test_round2_closeout.py::test_source_offset仍未接线` 用 AST 断言"除
   `extraction_runner.py` 外，生产代码不许读 `source_offset`"——一旦有人接线，用例变红并
   列出"接线时要一起改哪四处"（用例、指南 §1.16、README 本条、产物形状同步清单）。

## 与旧后端的关系

- 旧后端通过正式包 `war_extraction` 引用本目录（不再走 `sys.path` 注入），提供
  `/api/extract/entities-events`（文本实体/事件识别页）；**因此本目录必须与 `backend/` 同级存在**。
- 抽取产物经 `backend/import_json_to_sqlite.py --source <9_final_all.json>` 导入 SQLite，
  再经 `sync_sqlite_to_neo4j.py` 同步到 Neo4j。
- 旧问答用的实体抽取器是另一套（`backend/entity_extract/extractor.py`，为低延迟的规则+Ollama 方案），
  与本目录的 DeepSeek 三轮抽取**互不共享规则与归一逻辑**。已知差异：词表（人物称号、政权关键词、
  停止词）与事件名规范化各自实现一份，同一事件可能出现两种名字。改动其中一套时请注意另一套不会自动跟随。

## 相关文档

- 模块内：**先读这两份**——
  [`docs/整改落地说明与后续修改指南.md`](docs/整改落地说明与后续修改指南.md)
  （**接手先读**：上一轮改了哪些行为、每项改动何时生效、**下次要改同类东西动哪几处 + 跑什么**、
  未完成清单与踩过的坑）、
  [`docs/数据提取模块分析与整改方案.md`](docs/数据提取模块分析与整改方案.md)
  （为什么指标低、逐层归因、跨模块契约与静默失败清单、分阶段方案；其 §0.1 是执行状态）；
  另 `data/annotations/README.md`（参考标注的来源更正、字段口径与**事件粒度口径**）、
  [`docs/抽样判定规范.md`](docs/抽样判定规范.md)（**做 B 组人工核验前必读**：
  用哪份表、每列什么意思、三档怎么判、逐字段规则与真实判例）、
  [`docs/参考集重建规范.md`](docs/参考集重建规范.md)（**做 C 组参考集重建前必读**：
  为什么重建、要产出什么、开工前必须定的口径、七步流程、工具现状与工作量折算）、
  [`docs/第三阶段收尾执行单.md`](docs/第三阶段收尾执行单.md)（**接着往下做的人先看这份**：
  剩下的活按"花不花钱"分四批、**待重跑生效清单**、每批的验收命令）、
  `war_extraction/geocoding/README.md`（地理编码子系统）。
- 项目级（只引用这四个）：[`docs/README.md`](../docs/README.md)、
  [`docs/项目现状与后续计划.md`](../docs/项目现状与后续计划.md)、
  [`docs/集成与入口约定.md`](../docs/集成与入口约定.md)、
  [`docs/项目审查与修复历史.md`](../docs/项目审查与修复历史.md)。
