# 数据提取模块变更日志

## 2026-04-21 12:42:17 +08:00

- 修改类型：修复
- 修改文件：`src/extractors/event_extractor.py`、`src/models/events.py`
- 修改原因：测试中出现“识别到 14/17 个战争事件，但最终事件为 0”的问题，根因是完整事件抽取阶段一次处理事件过多、JSON 外层结构不稳定、以及单条关系或单条事件字段异常会拖垮整批结果。
- 影响范围：完整事件抽取现在支持 list/root-wrapper JSON 兼容、按 3 个事件分批抽取、单条事件解析失败跳过、批次失败时退回到最小事件对象；事件内部关系的 `evidence` 允许缺省，避免整条事件因关系字段不完整而失效。
- 验证方式：运行 Python 编译检查，并用列表根节点、包装节点、空关系字段和大事件批次的样例验证不会再出现“识别成功但整批事件归零”。

## 2026-04-21 12:48:22 +08:00

- 修改类型：升级
- 修改文件：`src/extractors/entity_extractor.py`、`src/extractors/event_extractor.py`、`src/processors/result_merger.py`、`main.py`
- 修改原因：继续针对“实体只出地点、人物组织严重缺失、事件字段别名导致信息丢失、事件抽取结果没有反哺实体库存”的问题做整体升级，目标是把实体链路和事件链路连成闭环。
- 影响范围：实体抽取现在兼容 list/root-wrapper JSON、类别别名、字段别名和空壳行过滤；事件抽取支持更多字段别名并在识别阶段去重；主流程会把事件中的地点、军事势力、指挥官和关键人物回填到实体列表；结果合并时会补全缺失字段而不是只保留第一条。
- 验证方式：运行 Python 编译检查，并用本地样例验证实体 JSON 兼容、事件字段归一化、事件反哺实体和跨片段字段补全逻辑均可执行。

## 2026-04-21 12:55:15 +08:00

- 修改类型：提示词增强
- 修改文件：`src/prompts/entity_prompts.py`、`src/prompts/event_prompts.py`
- 修改原因：按用户要求在原有提示词结构基础上增量修改，补充人物/组织召回规则、战争事件边界约束和完整事件字段稳定性规则，而不重写原模板。
- 影响范围：实体提示词会更明确要求抽取君主、主将、军队、方国、部族等战争核心对象，并减少把交战势力只识别成地点；事件提示词会减少把背景说明、泛泛边患、政治动乱误识别为战争事件，同时强调即使部分字段缺失也要保留核心事件对象。
- 验证方式：运行 Python 编译检查，确认模板文件可正常导入；后续用同一测试文本对比人物、组织和事件数量变化。

## 2026-04-21 13:46:29 +08:00

- 修改类型：整体修复
- 修改文件：`src/models/relations.py`、`src/extractors/relation_extractor.py`、`src/extractors/entity_extractor.py`、`src/extractors/event_extractor.py`、`src/utils/normalizer.py`、`src/processors/result_merger.py`、`main.py`
- 修改原因：针对最新测试结果中暴露出的 `modern_name` 空值校验失败、实体人物/组织混淆、事件重复、以及部分非战争事件混入的问题，统一增强实体判断、事件判断、关系容错和最终合并逻辑。
- 影响范围：地点关系允许缺少 `modern_name` 且会跳过不完整关系行；实体抽取新增人物/组织覆写名单与后处理去混淆；事件识别与输出新增非战争候选过滤和后处理去重；事件名称标准化与事件合并键放宽，减少跨片段重复；主流程在事件回填实体后会再次清洗人物/组织冲突。
- 验证方式：运行 Python 编译检查，并用本地样例验证关系空值不再触发整批失败、实体冲突得到清理、事件候选过滤生效，以及最终实体/事件输出仍可正常序列化。

## 2026-04-21 13:57:14 +08:00

- 修改类型：重构
- 修改文件：`src/utils/entity_classifier.py`、`src/utils/__init__.py`、`src/extractors/entity_extractor.py`、`main.py`、`src/extractors/relation_extractor.py`、`src/processors/result_merger.py`
- 修改原因：继续执行提取模块重构清单第一批任务，把人物/组织/地点判断收口到共享分类器，修复关系合并中的 `len(None)` 风险，并为关系抽取失败保留轻量错误样本，避免规则分散和静默失败。
- 影响范围：实体首轮抽取、事件反哺实体、最终实体清洗现在共享同一套分类规则；关系抽取失败会在 `logs/relation_errors/` 留下响应片段；关系合并在 `evidence` 为空时不再抛异常。
- 验证方式：运行 Python 编译检查，并用本地样例验证共享分类器、地点关系回退字段和关系合并空证据场景可正常运行。

## 2026-04-21 14:45:21 +08:00

- 修改类型：修复
- 修改文件：`src/extractors/event_extractor.py`、`src/extractors/relation_extractor.py`、`src/prompts/relation_prompts.py`、`main.py`
- 修改原因：最新第三版测试中 `events[].relations` 已经能看到事件间关系，但最终 `7_event_event_relations.json` 仍为 0，根因是事件内部关系没有被可靠投影到正式关系表，且部分目标仍以 `E2/E3` 事件ID形式存在。
- 影响范围：事件抽取阶段会把关系目标从事件ID解析为真实事件名；关系抽取阶段会把 `events[].relations` 反投影为正式 `event_event_relations`；关系提示词进一步明确 `EventName_A/B` 必须输出事件名称而不是事件ID；质量报告会显示嵌入式事件关系和正式事件关系数量。
- 验证方式：运行 Python 编译检查，并用本地样例验证 `events[].relations` 可以生成 `EventEventRelation`，且目标事件名不再保留 `E1/E2` 形式。

## 2026-04-20 16:33:36 +08:00

- 修改类型：新增
- 修改文件：`src/config.py`
- 修改原因：建立统一抽取版本、提示词版本、配置版本和分段参数，避免模型调用缓存只按文本 MD5 命中旧结果。
- 影响范围：缓存、输出 metadata、后续评估结果均可追踪具体提示词和抽取版本。
- 验证方式：运行编译检查，确认配置模块可导入。

## 2026-04-20 16:33:36 +08:00

- 修改类型：新增
- 修改文件：`docs/CHANGELOG_EXTRACTION.md`
- 修改原因：按用户要求记录每次代码修改，包含时间戳、修改类型、修改原因、影响范围和验证方式。
- 影响范围：后续所有数据提取模块修改都以该文件作为主记录。
- 验证方式：人工检查文件内容和时间戳格式。

## 2026-04-20 16:33:36 +08:00

- 修改类型：新增
- 修改文件：`config/aliases.json`、`config/relation_types.json`、`config/dynasty_ranges.json`、`config/eval_config.json`、`src/utils/normalizer.py`
- 修改原因：建立统一别名、关系类型、朝代范围和评估参数配置，为重新抽取后的归一化和评估打基础。
- 影响范围：抽取结果合并、关系字段规范、评估匹配和后续导入后端时可使用同一套标准。
- 验证方式：运行编译检查，确认 `Normalizer` 可导入且配置文件为合法 JSON。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/models/relations.py`、`src/models/events.py`、`src/extractors/relation_extractor.py`
- 修改原因：关系提示词已要求 `evidence`，但原模型和抽取器未保存该字段；事件细节字段过多且 LLM 可能返回 null，强制字符串会导致整条事件失败。
- 影响范围：重新抽取后的关系 JSON/Excel 可保留原文证据，事件抽取对空值更稳健。
- 验证方式：运行编译检查，并用测试文本确认关系输出包含 `evidence` 字段。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/prompts/event_prompts.py`、`src/extractors/event_extractor.py`
- 修改原因：旧事件识别依赖 `事件ID：` 等固定中文前缀，LLM 输出稍有变化就会解析失败；改为 JSON 优先解析，并保留旧格式兜底。
- 影响范围：事件识别阶段更稳定，后续事件完整要素抽取输入更可靠。
- 验证方式：运行编译检查，并确认 `identify_events` 可解析 JSON 与旧前缀格式。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`main.py`
- 修改原因：长文本缓存未带提示词版本，且长文本路径未尊重 `--no-cache`；同时抽取器在每个片段内重复创建。
- 影响范围：同文本、同模型、同提示词版本、同分段参数可复用缓存避免重复收费；提示词升级会重新抽取；输出总表增加 metadata。
- 验证方式：运行编译检查，确认 `--no-cache` 在长文本路径生效。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/core/text_splitter.py`
- 修改原因：分段参数需要与缓存上下文和抽取配置保持一致，避免不同位置硬编码。
- 影响范围：默认分段长度和重叠窗口由统一配置控制。
- 验证方式：运行编译检查。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/core/llm_client.py`
- 修改原因：原 JSON 提取使用贪婪正则，遇到多个 JSON 块或嵌套结构时容易截取错误；新增 JSON mode 参数和 decoder 扫描兜底。
- 影响范围：实体、事件、关系抽取的 JSON 解析稳定性提升。
- 验证方式：运行编译检查，并用含 JSON 包裹文本的响应样例测试 `_extract_json`。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/processors/json_to_excel.py`
- 修改原因：关系字段命名应统一为 `relation`，且四类关系都需要导出 `evidence`，便于审核和前端溯源。
- 影响范围：导出的事件-地点、事件-人物、事件-组织、事件-事件 Excel 表包含原文证据列。
- 验证方式：运行编译检查，后续用测试抽取结果验证 Excel 可生成。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/prompts/entity_prompts.py`
- 修改原因：强化实体抽取 `source_text` 质量要求，避免模型把单独实体名、背景评价或纯地理介绍当作证据。
- 影响范围：重新抽取时实体结果更适合审核、详情页展示和问答溯源。
- 验证方式：运行编译检查，并在测试抽取中人工检查 `source_text` 是否包含实体名和战争动作。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`src/processors/result_merger.py`
- 修改原因：原合并逻辑只按实体名或事件名去重，容易误合并不同朝代/不同地点的同名对象；关系合并未考虑证据字段。
- 影响范围：长文本重新抽取后的实体、事件、关系合并更稳，保留证据差异。
- 验证方式：运行编译检查，后续用长文本抽取验证合并结果。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修复
- 修改文件：`src/evaluation/optimal_evaluator.py`
- 修改原因：组织关系类别 `event-org` 与 `event-organization` 不一致会漏算；实体包含匹配过宽；实体评估返回缺少 `filtered_pred` 但汇总会读取。
- 影响范围：评估结果更可靠，组织关系不再系统性漏算，短字符串误匹配减少。
- 验证方式：运行编译检查，并运行 `evaluate.py` 验证结果正常输出。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修复
- 修改文件：`src/evaluation/evaluator.py`
- 修改原因：基础评估器在关系部分使用 `fuzz.ratio` 但未导入 `fuzz`，存在运行时错误。
- 影响范围：基础评估器可作为严格对照模式继续运行。
- 验证方式：运行编译检查。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`requirements.txt`
- 修改原因：评估和 Excel 导出依赖 `fuzzywuzzy`、`python-Levenshtein`、`pandas`、`openpyxl`，原依赖文件未声明。
- 影响范围：新环境安装依赖后可运行评估和导出。
- 验证方式：检查依赖声明；本次未联网安装依赖。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修改
- 修改文件：`evaluate.py`
- 修改原因：评估阈值原来硬编码在脚本中，不利于提示词版本和评估配置对比。
- 影响范围：评估结果保存到 `evaluation/latest/results.json`，并包含提示词版本、抽取版本和评估配置 metadata。
- 验证方式：运行编译检查；后续在存在预测结果时运行 `py evaluate.py`。

## 2026-04-20 16:33:36 +08:00

- 修改类型：新增
- 修改文件：`src/evaluation/optimal_evaluator.py`
- 修改原因：评估只输出 P/R/F1 不利于提示词迭代，需要给出未匹配预测和未匹配标注样例。
- 影响范围：评估结果中增加 `error_samples`，可用于后续“错误分析 -> 提示词优化 -> 重新抽取”的闭环。
- 验证方式：运行编译检查，后续运行 `py evaluate.py` 查看结果 JSON。

## 2026-04-20 16:33:36 +08:00

- 修改类型：修复
- 修改文件：`src/evaluation/optimal_evaluator.py`
- 修改原因：实体评估允许多个预测匹配同一个标注实体，导致召回率可能超过 100%，评估数值失真。
- 影响范围：实体 TP 采用一对一匹配，P/R/F1 更可信。
- 验证方式：运行 `py evaluate.py`，确认实体召回率不超过 100%。

## 2026-04-20 21:46:02 +08:00

- 修改类型：修改
- 修改文件：`src/core/llm_client.py`、`src/extractors/entity_extractor.py`、`src/extractors/event_extractor.py`、`src/extractors/relation_extractor.py`
- 修改原因：提示词已经要求严格 JSON，抽取器应优先请求 JSON 输出；如果 DeepSeek 接口不支持 JSON mode，客户端会自动降级为普通调用。
- 影响范围：实体、事件、关系抽取 JSON 稳定性提升；不改变缓存命中规则。
- 验证方式：运行编译检查。

## 2026-04-20 21:46:02 +08:00

- 修改类型：新增
- 修改文件：`main.py`
- 修改原因：原 CLI 只有 `--no-cache`，无法表达“强制重新抽取但保存新缓存”；输出目录也不可配置，且缺少抽取质量概览。
- 影响范围：新增 `--refresh-cache`、`--output`，并输出 `10_quality_report.json`，最终总表也包含 `quality_report`。
- 验证方式：运行编译检查，并用现有评估结果确认 JSON 结构可读。

## 2026-04-20 21:46:02 +08:00

- 修改类型：新增
- 修改文件：`evaluate.py`
- 修改原因：评估脚本路径硬编码，不便于重新抽取后的新结果对比；错误样例和字段报告混在完整结果中不便于提示词迭代。
- 影响范围：新增 `--pred`、`--config`、`--output` 参数，并额外输出 `error_analysis.json` 和 `field_report.json`。
- 验证方式：运行编译检查，并运行 `py evaluate.py`。

## 2026-04-20 22:03:05 +08:00

- 修改类型：修改
- 修改文件：`src/config.py`、`src/prompts/entity_prompts.py`、`src/prompts/event_prompts.py`、`src/prompts/relation_prompts.py`、`src/extractors/entity_extractor.py`、`src/extractors/event_extractor.py`、`src/extractors/relation_extractor.py`
- 修改原因：提示词版本之前主要存在于缓存和 metadata 中，不够直观；现在每个提示词模板和实际发送给 DeepSeek 的 prompt 都包含明确版本号。
- 影响范围：重新抽取时可直接从 prompt 内容、缓存上下文和输出 metadata 三处追踪提示词版本。
- 验证方式：运行编译检查，并确认渲染后的提示词包含 `## 提示词版本`。
## 2026-04-20 23:06:12 +08:00

- 修改类型：修复
- 修改文件：`src/extractors/entity_extractor.py`
- 修改原因：DeepSeek 在实体抽取阶段偶尔会返回最外层为列表、`entities` 包装列表或嵌套列表的 JSON，原解析逻辑直接调用 `data.get(...)`，导致 `'list' object has no attribute 'get'` 并使该片段实体结果为空。
- 影响范围：实体抽取器现在会先把不同 JSON 形态规范化为 `places`、`organizations`、`persons` 三类列表；无法识别的非字典项会跳过，不影响后续事件和关系抽取继续执行。
- 验证方式：运行 Python 编译检查，并用列表形、`entities` 包装形和标准字典形的样例验证 `_normalize_response_data` 可正常归类。
