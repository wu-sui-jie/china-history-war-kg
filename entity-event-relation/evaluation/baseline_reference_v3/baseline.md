# 基线记录（自动生成，勿手改）

- 冻结时间：2026-09-27 19:23:47.842373 +08:00（run_id `c9b88a2c5ad9`）
- 代码提交：`79419e7ed4d7956aa161385d3ec023db72509474`
- 提示词版本（生成侧派生值）：`prompt-v2-20260420+1228b39b`
- 抽取版本：`extraction-v2-20260420`
- 说明：v3 dev 基线（唐+秦汉合并，事件 333）：宏观 F1 67.12%（实体 71.03 / 事件 64.52 / 关系 65.81）；相对上一份 67.07% 基本持平，因为本轮是纯缺陷修复（去重 + 关系头对账），不是提示词改动

## 被评估的产物

- 路径：`output\dev_唐秦汉\9_final_all.json`
- 文件存在：True
- 文件 sha256：`f02464a723608ff92a3d5a95b2551c1a59b1e22e21a905923eb745f06d9f2c2f`
- 条数：事件 333 / 地点 1280 / 人物 688 / 组织 289 / 关系 4191

产物 metadata（产物自证，缺项即当时的产物没有记录这一项）：

| 键 | 值 |
| --- | --- |
| `extracted_at` | `None` |
| `prompt_version` | `prompt-v2-20260420+1228b39b` |
| `extraction_version` | `extraction-v2-20260420` |
| `model` | `deepseek-flash` |
| `api_base` | `https://api.deepseek.com/v1` |
| `git_commit` | `79419e7` |
| `artifact_sha256` | `None` |
| `text_length` | `None` |

## 基线指标

- 来源：`evaluation\baseline_reference_v3\baseline_results.json`（sha256 `fd88e4f9ab1b62df7daa0d484864b0fc68478a26a586ae47c65cd677891a351e`）

| 指标 | 值 |
| --- | ---: |
| entity_f1 | 0.7102894672828995 |
| entity_f1_canonical | 0.7918256130790191 |
| event_f1 | 0.6451612903225806 |
| relation_f1 | 0.6581419624217119 |
| macro_avg_f1 | 0.6712 |

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

下表是**冻结这一时刻磁盘上**的文件。它与「那次评估真正生效」的版本可能不同（见上文 `effective_at_evaluation`），这也是这个键叫 `files_on_disk_at_freeze` 的原因。

哈希有**两列**：`sha256` 是冻结当时磁盘口径（Windows 下这些 JSON/CSV 是 CRLF，项目所有历史记录用的也是它，保留以免新旧不可比）；`sha256_lf` 是行尾归一（CRLF→LF）后的口径。**在别的机器上核对「这份文件是不是当初冻的那份」要用 `sha256_lf`**——`.gitattributes` 是 `* text=auto eol=lf`，新克隆拿到的就是 LF，拿 `sha256` 对会得出假的「文件变了」。

| 目录 | 文件 | sha256（当时磁盘口径） | sha256_lf（跨机器核对用） |
| --- | --- | --- | --- |
| annotations | `sample_entities.json` | `9d98dbe54e5f0f58e7ffc13de650e5ceb08ea3d5b36dc31851e999c62df618bf` | `9d98dbe54e5f0f58e7ffc13de650e5ceb08ea3d5b36dc31851e999c62df618bf` |
| annotations | `sample_events.json` | `b9c85431e7b9208bb5f01e1f2f9b312a1459a98de852856e3f6a857451487859` | `78db34b6077331ab0485bc72ba62369622758ebb792b600ba621f2872f84641d` |
| annotations | `sample_relations.json` | `780b120ae5fb561798050ab03e3ba99c7b0d4b955fd4a6ef4124b768475a0994` | `780b120ae5fb561798050ab03e3ba99c7b0d4b955fd4a6ef4124b768475a0994` |
| annotations | `v2_conflicts.json` | `8219851233fb9fa8bb30ffb13c28f5743a4f525f7ac4f641702c34cfecbd6203` | `fd3f573930bfae478f895658d66dcf3d78b2257e185245814043f10336507fde` |
| config | `aliases.json` | `a76aa38f39eae624c3f0a7a3632a8e661da57b983c70558454b27d898128f1a4` | `a76aa38f39eae624c3f0a7a3632a8e661da57b983c70558454b27d898128f1a4` |
| config | `dynasty_ranges.json` | `d81b9e4608eae8c5d097973c2ff559f6fb9df90de1c1aeb95fa268cfc9a20e87` | `d81b9e4608eae8c5d097973c2ff559f6fb9df90de1c1aeb95fa268cfc9a20e87` |
| config | `eval_config.json` | `1512a828595290a1a0b9ee177c12dbd3989955e536544fd0cde0e47b3ca177cd` | `8d8f969aa04f4e4354930505987729faf190b8f168381cae073baecc4fc7b155` |
| config | `publish_rules.json` | `9122a31a4b9ee0ec19f8b25507bad124433568875e4aea6f97bc97d12262e8c8` | `9122a31a4b9ee0ec19f8b25507bad124433568875e4aea6f97bc97d12262e8c8` |
| config | `relation_types.json` | `2d10b3548ffbea2a8858eaec3ed3bac305f1da9e0e2c481139c8215508d0f4ce` | `2d10b3548ffbea2a8858eaec3ed3bac305f1da9e0e2c481139c8215508d0f4ce` |
| config | `text_cleaning.json` | `8d9247fab91e4d86b921862fad196925fc3379c6f13c72c4971c6003cbde2310` | `8d9247fab91e4d86b921862fad196925fc3379c6f13c72c4971c6003cbde2310` |
| prompts | `__init__.py` | `9628f6fa99cc28fad7064be1a4b080708e6686ce1db126bbd012c5bc2890d5ab` | `91311fe8a4f82b29b3990ae973b713efcbfd6e63cf490b649a67a22653f1ccb0` |
| prompts | `entity_prompts.py` | `bfb10fda72a1a0384a07c3805402b14203ca48bbb5a451eff78b17b142cf7765` | `f5b3df4de46f51ec166a97687a64f6be73bafdc85161bde15a7c5e9fed1c8335` |
| prompts | `event_prompts.py` | `ca5f581760635a648a942fbbce320297128754e19b687b9b20a98e910046e41a` | `a9997a86e40a4ce17858bd04600562624080f5792e910b559c92eca09d7e5a09` |
| prompts | `relation_prompts.py` | `e26ec8f1c2fab5a4fe6ca931f1c92a49e482044d831bbb3a63ebfb88e956534a` | `e26ec8f1c2fab5a4fe6ca931f1c92a49e482044d831bbb3a63ebfb88e956534a` |
