# 基线记录（自动生成，勿手改）

- 冻结时间：2026-09-26 23:23:32.311716 +08:00（run_id `9a54fe5ea31f`）
- 代码提交：`acffb465bc6dddb3c022bad14eb6dab00d2b809e`
- 提示词版本（生成侧派生值）：`prompt-v2-20260420+250f2471`
- 抽取版本：`extraction-v2-20260420`
- 说明：改前冻结基线（旧评估口径）：产物由 133a9aa 于 2026-08-18 生成。此记录在改后重新生成过，所以 config/ 下的文件哈希是**当前**文件；当次评估真正生效的 eval_config 见 baseline_metrics.effective_at_evaluation。

## 被评估的产物

- 路径：`F:\python\python_space\china-war\entity-event-relation\output\中国历代战争简史\9_final_all.json`
- 文件存在：True
- 文件 sha256：`885b0d9ec340a50b151a42ba16a1b883cf2a7fff5147bac49259cbd9fcc4e559`
- 条数：事件 1050 / 地点 5316 / 人物 2492 / 组织 1067 / 关系 18146

产物 metadata（产物自证，缺项即当时的产物没有记录这一项）：

| 键 | 值 |
| --- | --- |
| `extracted_at` | `2026-08-18 03:37:13 +08:00` |
| `prompt_version` | `prompt-v2-20260420` |
| `extraction_version` | `extraction-v2-20260420` |
| `model` | `None` |
| `api_base` | `None` |
| `git_commit` | `None` |
| `artifact_sha256` | `None` |
| `text_length` | `306730` |

## 基线指标

- 来源：`evaluation\baseline\baseline_results.json`（sha256 `b7db0e838797d3981058c3dd86d78ff87223471cbe50cf3183662d457bc001d0`）

| 指标 | 值 |
| --- | ---: |
| entity_f1 | 0.20417958204179582 |
| event_f1 | 0.4175334323922734 |
| relation_f1 | 0.7186713638651233 |
| macro_avg_f1 | 0.4468 |

## 参考标注的来源（引用指标前必读）

- 类型：`multi_model_merged`，**是否人工标注：False**
- 制作方式：几个人用不同的模型把整本书分成几份、每个模型负责两份，最后把各份结果汇总，字段没有做人工处理，直接作为标注数据使用。

后果：
- 当前指标测的是'模型 A 的产出'与'模型 B/C/D 汇总产出'之间的分歧，不是抽取正确率
- 结构性缺陷（head 大量不在事件表、完全重复关系、同名多行、命名风格不统一）源于汇总环节缺失实体与事件对齐，不是个别标注者的疏漏
- 在人工逐条核验之前，任何基于它的指标都不能作为抽取质量或模型能力的证据

仍可用作：
- 候选清单 / 词表（事件名、人名、地名是书中真实存在的内容）
- 别名表（config/aliases.json）的来源
- 回归 / 可复现性检测的参照

不可用作：
- 准确性指标的分母
- 跨版本'改好没改好'的判断依据（会把口径差异误读成质量升降）

> data/annotations/README.md 现写的'只有一名标注者、没有 IAA'与上述实际制作方式不符，该文件的更正措辞待标注所有人确认后另行处理（见整改方案 10.2 第 1 条）。

## 文件指纹

| 目录 | 文件 | sha256 |
| --- | --- | --- |
| annotations | `sample_entities.json` | `9d98dbe54e5f0f58e7ffc13de650e5ceb08ea3d5b36dc31851e999c62df618bf` |
| annotations | `sample_events.json` | `b9c85431e7b9208bb5f01e1f2f9b312a1459a98de852856e3f6a857451487859` |
| annotations | `sample_relations.json` | `780b120ae5fb561798050ab03e3ba99c7b0d4b955fd4a6ef4124b768475a0994` |
| config | `aliases.json` | `a76aa38f39eae624c3f0a7a3632a8e661da57b983c70558454b27d898128f1a4` |
| config | `dynasty_ranges.json` | `d81b9e4608eae8c5d097973c2ff559f6fb9df90de1c1aeb95fa268cfc9a20e87` |
| config | `eval_config.json` | `1512a828595290a1a0b9ee177c12dbd3989955e536544fd0cde0e47b3ca177cd` |
| config | `publish_rules.json` | `ccfe91065429cbd36bfeee848f2749c0278a9025357b8f34d2279082a47a31b6` |
| config | `relation_types.json` | `2d10b3548ffbea2a8858eaec3ed3bac305f1da9e0e2c481139c8215508d0f4ce` |
| config | `text_cleaning.json` | `8d9247fab91e4d86b921862fad196925fc3379c6f13c72c4971c6003cbde2310` |
| prompts | `__init__.py` | `9628f6fa99cc28fad7064be1a4b080708e6686ce1db126bbd012c5bc2890d5ab` |
| prompts | `entity_prompts.py` | `ff9c2ac7fa5e0d9467a907d89529657114f8c19ab2e83bab6083311f7b3cc65c` |
| prompts | `event_prompts.py` | `7684ac8e51617480223e80ff4136bac88fe3c3bd6d5a2377b9877d1fe43d7e7c` |
| prompts | `relation_prompts.py` | `e8d3c1e0cecc6d41ad206f2845a3dc889591e94c26ecce4df391b45e3c4603ce` |
