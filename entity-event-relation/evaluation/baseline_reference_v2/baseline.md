# 基线记录（自动生成，勿手改）

- 冻结时间：2026-09-27 13:11:24.174676 +08:00（run_id `eb8956fdb97b`）
- 代码提交：`9c59e0e8c4b1472cfbef92a64b0bb957df4837bb`
- 提示词版本（生成侧派生值）：`prompt-v2-20260420+1228b39b`
- 抽取版本：`extraction-v2-20260420`
- 说明：C 组参考集重建：v2 成稿（三个子集逐条核验的合并，经仲裁落地、人工判定补录与去重，带原文证据坐标）。dev/test 切分 = --dev 唐 秦汉。本记录在 2026-09-27 的整本重跑与其后的缓存重放（追加两条后处理丢弃规则）之后冻结，artifact 指向最新产物（事件 1313 / 关系 16666）；参考集三份文件本身未变。

## 被评估的产物

- 路径：`F:\python\python_space\china-war\entity-event-relation\output\中国历代战争简史\9_final_all.json`
- 文件存在：True
- 文件 sha256：`16a6879b21088690f96e57ce59938487ed1f0e843d86bb7e401d3904e988bc45`
- 条数：事件 1313 / 地点 5527 / 人物 2562 / 组织 1021 / 关系 16666

产物 metadata（产物自证，缺项即当时的产物没有记录这一项）：

| 键 | 值 |
| --- | --- |
| `extracted_at` | `2026-09-27 13:08:16 +08:00` |
| `prompt_version` | `prompt-v2-20260420+1228b39b` |
| `extraction_version` | `extraction-v2-20260420` |
| `model` | `deepseek-flash` |
| `api_base` | `https://api.deepseek.com/v1` |
| `git_commit` | `9c59e0e` |
| `artifact_sha256` | `248020cd14cd240c2fb67312a80e3231da498eeede4abbcd682583f2c3c688c0` |
| `text_length` | `306730` |

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
| annotations | `sample_entities.json` | `bf0832398444730eed800c7bf827391e24d7ea75d4a4617867b75dcaf585f4d3` | `6b3897fe09e7c3515b69f68c475368c84482b27c3324ec3699d0055d9bb77694` |
| annotations | `sample_events.json` | `0e31f76c488f6dfcabe99888bf60ac686b46c9efe993e83f4b8d169cc9fc5bdd` | `9e167116c2ff0506dbb9c843ec2fb728c29603b824ae515dd471cd2997f29020` |
| annotations | `sample_relations.json` | `326340dd3f40b9cdbc10e83d803a165b3616e50bf51358d2078828eb019db11f` | `d47a69a83ac554fa9d7dd2b07d8f006a2fead5b609e8b29502bc25229e48af0b` |
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
