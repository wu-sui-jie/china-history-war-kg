# 20260915 全项目审核整改总结

- 文档类型：审核整改总结（对应 [20260915-全项目审核报告](../20260915-全项目审核报告.md)）
- 状态：整改完成（22/22 条处置完毕；其中 P1-9 以文档口径收口、未执行真实数据回填，理由见第四节）
- 整改日期：2026-09-15
- 验证结果：`pytest tests/ -q` → **154 passed**（整改前 140）；`frontend npm run build` 通过

## 一、总览

| 优先级 | 条目 | 处置方式 |
| --- | --- | --- |
| P0（4 条） | P0-1、P0-4 补代码；P0-2、P0-3 改文档 | 4/4 完成 |
| P1（10 条） | P1-10、P1-11、P1-14 补代码；P1-5/6/7/8/9/12/13 改文档 | 10/10 完成（P1-9 未执行数据回填） |
| P2（8 条） | 全部改文档 | 8/8 完成 |

- **代码改动 5 项**：P0-1（模型自拒识别）、P0-4（冒烟证据数断言）、P1-10（timeline 排序）、
  P1-11（图谱节点点击）、P1-14（默认值对齐 hybrid/rrf）。
- **文档改动 17 项**：P0-2、P0-3、P1-5 至 P1-9、P1-12、P1-13、P2-15 至 P2-22，
  另含 4 处跨文档一致性同步（模型名、TEXT_MODE 默认值、fusion 字段、测试数字）。

选择「改文档」而非「改代码」的共同原则：**当文档描述的是更早的设计意图、而现有代码行为
已经过评测验证或已冻结为数据产物时，以代码现状为准回写文档**，避免为对齐文档而改动
已验证行为或重建数据产物，造成新的版本错位。

## 二、代码改动明细（5 项）

### 1. P0-1 模型自拒识别（方案 A）

- 文件：`server/generate/refusal.py`、`server/generate/__init__.py`、
  `tests/test_model_refusal.py`（新增 8 条用例）。
- 行为：LLM 返回的正文若满足「前 200 字内命中固定拒答句式」且「全文不含引用编号 `[n]`」，
  `finish_reason` 置 `refused`（优先于 `degraded`）；前端已有的
  `MessageBubble.vue` 会据此显示"依据不足"标签。
- 判定口径的保守性说明：只收录语义完整的自拒短语（如"依据现有资料无法"、"史料中没有相关记载"），
  **不收录**"无法回答"这类单独出现的短句；带引用的「局部不确定」（如"其出生年份无法确认，
  但据 [1] 他参与了……"）不判拒答——宁可漏判保持 `normal`，不误判可用回答。
- 缓存：`refused` 是确定性结果，按 `server/sse.py` 既有策略入缓存（无需改动）。

### 2. P0-4 冒烟脚本证据数断言（方案 A）

- 文件：`scripts/smoke_deploy.py`。
- 行为：`_sse_query` 新增统计 `graph_results` / `text_results` 事件的证据条数；
  逐条示例题断言「图谱证据数 ≥ `expect.graph_min`、文本证据数 ≥ `expect.text_min`」，
  低于下限计入 failures；成功输出附两条通道的证据数。示例题 `expect` 值来自题库实测
  （9 条均为 `graph_min=1 / text_min=1`），无需调整。

### 3. P1-10 timeline 组内排序（方案 A）

- 文件：`server/fusion/panel_builder.py`、`tests/test_panel_timeline_order.py`（新增 6 条用例）。
- 行为：朝代分组内按可解析的 `start_date` 升序；无法可靠解析的日期文本保持数据原序、
  置于组内末尾；"时间不详/仅知朝代"分组仍整体置尾。
- 日期解析只处理两种可靠格式（`前 N 年` → `-N`；`前 N 世纪` → 世纪中点），
  中文数字与模糊表述（"约四五千年前"、"夏朝末年"）不猜年份。

### 4. P1-11 图谱节点点击追问（方案 A，部分）

- 文件：`frontend/src/components/panel/SubGraphView.vue`（已重新构建 `frontend/dist`）。
- 行为：echarts 画布内节点点击（按 `dataType === 'node'` 过滤边的点击）→ 生成
  "介绍一下 XX"追问，与下方实体按钮行为一致；提示文案同步更新。
- 未做：'查看实体详情'操作菜单、按问题类型自动切换标签页（报告亦建议可先不做），
  已在 F07 文档标注为未实现。

### 5. P1-14 默认值对齐 hybrid/rrf（方案 A）

- 文件：`config/defaults.py`、`config/settings.py`；同步文档 3 处
  （`server/text/README.md`、`docs/RAG_v1/RAGv5-规划说明.md` 两处）。
- 行为：代码级兜底从 `keyword/weighted` 对齐为 `hybrid/rrf`，与 `.env` 部署默认一致；
  换环境丢失 `.env` 时不再静默回退关键词模式。向量/Chroma 不可用时由检索层自动降级
  keyword 并在 `text_results.mode` 上报（既有机制，未被改动）。

## 三、文档改动明细（17 项）

| 编号 | 文件 | 改动 |
| --- | --- | --- |
| P0-2 | `docs/features/06-grounded-answer.md`、`docs/data-contract.md` | 拒答规则第 3 条改为"score 不单独作阈值；向量/hybrid 下与无共享词叠加生效"；data-contract 的 score 段落同步 |
| P0-3 | `docs/data-contract.md` | fusion 事件 payload 改为 `evidence_count`，注明完整证据由 citations/panel 承载 |
| P1-5 | `docs/features/07-knowledge-panel.md` | 模块清单第 3 项与用户场景 2 降级为"由图谱子图的关系连边承载" |
| P1-6 | `docs/features/09-data-governance.md` | 实现思路 5–6、孤立节点策略 2/5 改为"全部保留标记、本层不补边" |
| P1-7 | `docs/features/09-data-governance.md` | 注明 `event_type_map` 当前为恒等映射、3 类 review_needed 待人工增补 |
| P1-8 | `docs/features/09-data-governance.md` | 验收第 2 条改为"治理统计快照 + data_issues 逐条 before/after"口径 |
| P1-9 | `docs/features/09-data-governance.md` | 人工审核要求改为"工具就绪、当前为待审清单、未执行回填" |
| P1-12 | `docs/features/03-graph-retrieval.md` | 两跳建议标注"未采纳，除 event_event 外均 1 跳定档"；unknown 策略改主实体口径；展示方式补当前实现 |
| P1-13 | `docs/features/06-grounded-answer.md` | 补"推理增量（thinking 事件）"与"降级路径"两节；领域外谓词并入拒答规则第 5 条 |
| P2-15 | `docs/architecture.md` | 目录树补齐 contracts/config/evaluation/lib 与职责 |
| P2-16 | `docs/architecture.md`、`docs/features/06-grounded-answer.md` | 统一为 `deepseek/deepseek-v4.1-flash`（中转 id） |
| P2-17 | `docs/features/08-demo-mode.md`、`docs/README.md` | 删"暂缓编写"残留；输入项改"无需开关、欢迎页常驻"；待确认项收敛为已确认事项 |
| P2-18 | `docs/RAG_v1/RAGv5-遗留项整改方案.md` | 用例数加注"2026-09-15 时点 140 全过" |
| P2-19 | `docs/features/04-text-retrieval.md` | 补 and_or 实际口径（AND 不足 3 条即并入 OR） |
| P2-20 | `docs/features/05-fusion-rerank.md`、`docs/data-contract.md` | 补 different_object 仅对单值关系生效 |
| P2-21 | `docs/data-contract.md` | entities / graph_results / done 事件补齐超集字段 |
| P2-22 | `docs/features/10-evaluation.md` | 文首新增"评分与审核口径"节；实现思路/用户场景/依赖/已确认需求同步补注 |

另同步：`RAG/README.md`（用例数 140 → 154）、`docs/deploy.md`（冒烟断言的
`expect.graph_min/text_min` 口径）、`docs/features/05-fusion-rerank.md`（timeline 排序口径）。

## 四、未采纳的建议与原因（重点）

### 1. P1-9 方案 A「执行 apply_audit 真实回填 + 重建索引」——不执行

审核报告推荐方案 A，但本次**明确不执行**，以方案 B（文档口径降到实际状态）收口。理由：

1. **主体性冲突**：F09 的审核闭环定义是"人工审核"，而本次整改的执行主体是 AI。
   由 AI 从 1,215 组重名里挑若干组执行别名补充/合并，等于 AI 自己审自己，
   违背该功能的设计前提，产出的 `audit_applied` 也不构成"人工审核记录"。
2. **动作影响面超出审核整改范围**：回填产出新快照后必须重建 9,544 条向量索引、
   更新 `.env` 数据版本并回归测试——这是数据版本切换与部署决策，不是文档一致性整改。
3. **成本与不可逆性**：全量重建索引消耗百炼 API 配额（约 25 分钟串行）且产生新的
   数据版本；一旦有误需再次回滚。建议由人工确认审核清单后单独安排。

### 2. 选择方案 B 而非方案 A 的条目与判断依据

| 条目 | 报告推荐 | 本次选择 | 依据 |
| --- | --- | --- | --- |
| P0-3 | 方案 A（改文档） | 同报告 | fusion 事件本就定位为"融合结果摘要"，前端与评测均不消费该事件的证据数组；改为携带完整证据会让事件体积翻数倍 |
| P1-5 | 方案 B（改文档） | 同报告 | `contracts/panel.py` 的 PanelData 无路径字段；补代码需动契约 + F05 + 前端三层，而图谱子图已能表达关系连边，收益低于回归风险 |
| P1-8 | 方案 A 或 B | 方案 B（改文档） | 治理报告是历史快照产物（`20260904_v2`/`20260915_v1` 均已冻结）；只改代码不重跑治理会造成"代码与产物不一致"，重跑治理超出本工作单范围 |

### 3. 「其他备忘」项

F02 指代消解简化实现、验收第 3 条靠下游打分隐式满足等备忘项，报告注明"不强制整改"，
本次未处理；如需处理，建议在 F02 文档补注"简化实现"字样。

## 五、验证与回归

| 项 | 结果 |
| --- | --- |
| 后端测试 `E:/anaconda/envs/AI_Agent/python.exe -m pytest tests/ -q` | **154 passed**（整改前 140；新增 timeline 排序 6 条 + 模型自拒 8 条） |
| 前端类型检查与构建 `cd frontend && npm run build` | 通过（vue-tsc + vite build），`frontend/dist` 已更新 |
| 端到端冒烟 `scripts/smoke_deploy.py` | 未执行（需先启动服务）；语法与断言逻辑已静态核对，建议部署前按 `docs/deploy.md` 跑一次完整冒烟 |

## 六、风险与注意事项

1. **P0-1 判定为启发式**：词表命中 + 无引用编号的组合判定是保守策略，存在两类边界——
   模型用词表外句式自拒（漏判，保持 normal）与极少数"整段自拒但引用了无关编号"
   （漏判）。若 F10 复跑发现漏判案例，扩充 `MODEL_REFUSAL_PATTERNS` 即可，无需改调用方。
2. **P0-1 影响评测口径**：评测链路（`evaluation/chain.py`）与在线链路共用
   `generate()`，复跑评测时 trace 的 `answer.finish_reason` 会新增 `refused` 取值；
   历史 run 产物不变，但新旧 run 对比时需注意该口径差异。
3. **P1-14 默认值变化**：无 `.env` 的新环境将以 hybrid 模式启动，首次检索会尝试加载
   Chroma；若索引/密钥缺失会自动降级 keyword（有测试守护，`test_vector_degradation.py`）。
4. **P1-10 日期解析边界**："约四五千年前"等模糊表述不参与排序（保持组内原序置后），
   时间线在同一朝代组内可能出现"可解析年份在前、模糊表述在后"的观感，属预期行为。
5. **未提交 Git**：本次改动全部在工作区，未执行 `git add/commit`（RAG 为嵌套仓库，
   提交策略见 `RAGv5-遗留项整改方案.md` §二）。
