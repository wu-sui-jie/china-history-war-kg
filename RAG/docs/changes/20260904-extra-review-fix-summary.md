# 外部复核修复：总结分析文档（2026-09-04 第二轮）

- 日期：2026-09-04
- 项目：RAG 智能问答（`RAG/` 子项目）
- 触发来源：第三方模型对 docs 与全部代码模块的对照检测（13 项结论）
- 处理方式：逐条对码核实 → 修复确认项 → 回归验证（本文件记录判定与修改）

## 一、逐项判定（核实结果）

| # | 结论（来源审查） | 判定 | 说明 |
| --- | --- | --- | --- |
| 1 | map_points.events 把本轮全部事件挂在每个地点上 | ✅ 属实（潜伏） | `panel_builder.py` 原为 `events=[全部命中事件卡]`，与 data-contract 的 per-place 语义不符；当前坐标覆盖率为 0 故 map_points 恒空，无线上影响 |
| 2 | 证据 ID 用 hash() 回退，跨进程不稳定 | ⚠️ 逻辑属实、当前数据不触发 | 实测 relations.json 17,700 行全部带 source_row_id，走不到回退分支；已顺手改为确定性哈希 |
| 3 | 同一关系行从两端遍历得到同 ID、反向内容 | ✅ 属实 | 双端命中（如双事件问题）会产出同 ID 反向重复；另有 in-edge 反向命中时 content 方向翻转的语义问题，需一并处理（见修复 2） |
| 4 | understand.py 对同一字符串二次 match | ✅ 属实 | 死代码，行为不变删除 |
| 5 | _DYNASTY_TERMS 模块变量写后未读 | ✅ 属实 | 死代码删除（实际用 self._dynasty_terms） |
| 6 | classify() 的 entity_names 参数未使用 | ✅ 属实 | 参数与两处调用点清理 |
| 7 | fusion.py 连续两次同 key 排序 | ✅ 属实 | 删除重复排序 |
| 8 | 主模型未配置时空转重试才落备用 | ✅ 属实 | 改为直接走备用 |
| 9 | 缓存键用未裁剪历史 | ✅ 属实（效率项） | cache key 改用与 F02 同口径裁剪后的历史 |
| 10 | tests/ 空目录与 README 不符 | ◐ 部分属实 | README 行是“用途规划”非断言已有用例；措辞已改为“预留”，测试补写在 RAGv4 范围 |
| 11 | thinking 事件契约未标“保留” | ✅ 属实 | data-contract 两处与前端类型注释补标注 |
| 12 | event_type 筛选整批剔除 raw/evidence | ✅ 属实（已记录边界） | 保留代码行为（边界已有注释），在 RAGv4 规划中列为筛选召回专项 |

## 二、修改内容

### 1. 地图面板地点↔事件挂接（#1）

`server/fusion/panel_builder.py`：

- `_load()` 时读 relations.json（排除 pending_review），构建“地点名 → 相关事件 id”映射
  （事件—地点两端的行都会归入该地点名下）；
- `_build_map_points()` 的 `events` 改取该地点的真实关联事件（排序稳定），不再把本轮
  命中事件整体挂到每个地点。

### 2. 图谱证据行方向规范化 + 稳定 ID + 行级去重（#2 #3）

`server/graph/search.py`：

- 新增 `_triple_from_row(row)`：证据 content 恒按关系行原始方向
  （subject=行 source_name，type 由实体索引回查），从任一端命中内容一致；
- 证据 ID 优先 `graph_{source_row_id}`；缺失时用 `sha1(source|relation|target)` 确定性
  回退（弃用进程内 `hash()`）；
- 各策略（single_entity / relation / event_event / background）统一按
  `_row_key(row)`（source_row_id 行级）去重，双端命中只保留一份证据；
- 附带效果：in-edge 反向命中不再输出“方向翻转”的三元组（如“地点—主战场→事件”），
  引用标题、冲突比对、panel 边均按行方向规范化。

### 3. 低危清理（#4–#9）

- `server/query/understand.py`：删除对同一 resolved_question 的重复 match；新增公开
  `trim_history()`（与内部裁剪同口径）；
- `server/query/classifier.py`：`classify()` 去掉未用的 `entity_names` 参数（调用点同步）；
- `server/query/dictionary_matcher.py`：删除 `_DYNASTY_TERMS` 模块变量与
  `_init_dynasty_terms()`；
- `server/fusion/fusion.py`：删除重复的 graph/text 排序段；
- `server/generate/llm_client.py`：主模型未配置但备用已配置时直接走备用（不为 None
  主模型空转 llm_max_retries 次）；
- `server/sse.py`：缓存键历史改用 `runtime.question.trim_history(req.history)`（与 F02
  实际参与上下文一致）。

### 4. 文档标注（#10–#12）

- `RAG/README.md`：tests/ 行标注“预留目录，尚未写入用例”；
- `docs/data-contract.md`：thinking 事件在“流式事件协议”与“SSE 事件 payload”两处标注
  “保留枚举，后端当前不发射”；
- `frontend/src/types/contract.ts`：SSEEventType 的 thinking 项加同注释；
- `docs/RAG_v1/后续阶段规划.md`：RAGv4 增加“筛选召回专项（event_type 筛选的原文损耗）”。

## 三、回归验证结果

| 验证项 | 结果 |
| --- | --- |
| 全部改动模块 py_compile | 通过 ✅ |
| SSE 全量检索（赤壁之战的主帅是谁？） | 事件序列完整（graph_results/text_results/fusion/panel/done），引用 18 条无重复 ID，标题行方向正确（赤壁之战—主战场→赤壁…）✅ |
| SSE 缓存命中（同问二次） | status(cache_hit)→answer→citations→panel→done，cache_hit=True ✅ |
| SSE 双事件问题（牧野之战与武王伐纣） | 引用无重复 evidence_id、无反向重复证据 ✅ |
| 长历史缓存键（>4 轮 + 同问） | 二次请求命中缓存（键已按裁剪口径）✅ |
| panel 地点↔事件映射 | 赤壁→[赤壁之战]、长平→[长平之战]、牧野→[周武王灭商牧野之战,武王伐纣]；2240 个有事件关联地点，无空映射 ✅ |
| 前端 npm run build | 通过 ✅ |

## 四、边界与后续

1. 本次修复集中在 F03/F05 与服务端效率项；前端仅注释，无行为改动；
2. 证据 subject/object 顺序变化可能影响离线回答器/提示词的措辞顺序，已通过上述
   SSE 冒烟确认引用标题与回答正常；F10 评测将作为最终效果口径；
3. 证据 ID 若未来做跨进程持久化缓存（Redis 等），依赖本修复后的稳定 ID 即可；
4. 全部改动仍与 RAGv3 收口内容同批未提交 Git（见 ragv3-summary 的 Git 固化说明）。

## 五、复审修复补记（2026-09-04 第二轮复核）

对上述修复的再次检测发现 4 项，已逐条核实并修复：

| # | 复审结论 | 核实 | 修复 |
| --- | --- | --- | --- |
| 1 | `_event_event` 两跳路径未做行级去重，与“行级去重”声明不符 | 属实：路径循环不经 seen_rows，a↔mid 行在直接边循环已进入时会重复 append（graph_results 出现重复证据；fusion 层兜底去重故用户无感） | `search.py` 两跳路径循环并入同一 `seen_rows` 行级去重；另以 400 组随机事件对属性测试验证（25 组触发两跳分支，重复案例 0） |
| 2 | 缓存键（F02 裁剪窗口）与提示词历史（raw[-4:] 消息窗口）不等价，⑨ 只修了一半 | 属实：`prompts.py:75` 用 `history[-4:]`；两侧窗口不同仅致缓存 miss | 历史在 `sse.py` 单点裁剪一次，F02/缓存键/`generate()` 共用同一 `hist`；`prompts.py` 改为整段消费裁剪后历史（窗口已 ≤ history_max_turns 轮），键=实际生成上下文 |
| 3 | `_triple_from_row` 类型回退用 `source_type`（恒为 "kg_relation"）语义错误 | 属实：2000 行抽样 `source_type` 全为 "kg_relation"，非实体类型 | 删除该回退，实体类型只取自实体索引，端点缺失留空（调用方本就跳过缺失实体行，防御性修正） |
| 4 | map_points 按地点名建键，同名地点（实测 1078 组重名，如涿鹿×2、阪泉×3）会串事件 | 属实：name 键 + name 去重会把一地事件挂到所有同名点 | `panel_builder` 映射键与去重键全部改 `entity_id`，同名地点各自成点挂各自事件 |

补充说明（第 1 项附带）：两跳路径本身是“连通路径”语义，可经非事件实体中介，关系类型
不过滤——与旧版行为一致（旧代码如此，复审亦注明未触及），本轮保留并加注释说明；
是否收紧为“仅事件中介 + 事件-事件关系”留待 F10 以评测口径决定。

回归验证（本轮追加）：py_compile 通过；SSE 全量路径/cache 命中/长历史二次命中均正常；
`graph_results` 唯一性抽查（Q2 6/6 唯一）；类型污染抽查（5 类 qtype × 6 实体，0 例
"kg_relation" 污染、0 重复 ID）；地点映射按 id 键校验（2240 个地点实体，同名组不再串事件）。
