# 20260915 RAG 全项目复核分析与优化建议

> **归档说明（2026-09-20 文档整理）**：本文档是**第四轮全项目复核的过程记录**（含三路只读复核结论），
> 所列问题已整改（改动与证据见 [round4 整改记录](20260915-round4-review-fix-summary.md)、
> [补充整改](20260915-round4-review-fix-summary-2.md)）。归档于 `docs/changes/`。
> **现行事实以 [docs/current-status.md](../current-status.md) 为准**；日常开发不必读本文档。


> 审核日期：2026-09-15  
> 审核范围：`RAG/` 全项目，严格排除 `RAG/new/`。  
> 审核方式：后端、前端、数据与工程、文档四路只读复核。  
> 约束：本轮不修改代码、不修改既有文档，仅新增本报告。  
> 基准：RAG 内层仓库分支 `ragv5-demo-deploy`，HEAD `481df24`；第三轮报告为未跟踪文件。  
> 动态验证：使用禁用字节码和 pytest 缓存的方式运行测试，结果为 **156 passed，19.77s**；未运行会生成 `dist` 或 `*.tsbuildinfo` 的前端构建。

---

## 一、执行摘要

当前 RAG 项目已经具备完整的历史战争问答演示链路：查询理解、图谱与文本混合检索、流式生成、引用、实体纠正、子图、地图和评测工具均已形成。现有 156 个 Python 测试在本机完整数据环境中全部通过，说明核心离线能力具备较好的回归基础。

但项目距离“可稳定公开部署、可从干净仓库复现、可长期维护”仍有明显差距。问题主要集中在五个方面：

1. **在线并发链路仍有事件循环阻塞和孤儿任务问题**，单个慢 embedding 请求可能拖停同一 worker 的全部连接。
2. **前端会话状态模型不足以表达纠正、失败、中断和恢复**，不仅存在永久转圈，还会把错误回答写入历史，或在纠正后继续沿用旧答案。
3. **部署版本和发布制品不可复现**，服务按目录名自动选择数据版本，`--version` 没有真正控制 API，必需的数据、索引和前端产物又全部被 Git 忽略。
4. **数据血缘与评测证据不足**，活跃版本仍携带旧版本元数据，向量复用没有内容哈希证明，最新 run 也不是生产 LLM 质量评测。
5. **文档数量充足但缺少分层和单一事实源**，当前规范、历史规划、阶段总结和审计快照相互混杂，多个入口仍停留在 RAGv2/RAGv3 或“待开工”状态。

### 当前风险总览

| 级别 | 数量 | 主要内容 |
| --- | ---: | --- |
| P0 / 高 | 10 | 事件循环阻塞、请求无尺寸限制、推理内容暴露、会话历史错误、前端永久卡死、部署版本漂移、发布不可复现 |
| P1 / 中 | 22 | 任务取消、限流与缓存、流式重试、SSE 解析、移动端证据链、数据哈希、测试分层、首屏性能、文档规范冲突 |
| P2 / 低 | 16 | HTTP 语义、配置和依赖声明、可访问性细节、过时注释、评测元数据、仓库清理与流程欠账 |

> 数量按本报告合并后的独立主题计算，不与第三轮报告的编号一一相加，避免同一根因重复计数。

---

## 二、第三轮审核报告的价值分析

审核对象：`docs/changes/20260915-round3-audit-and-remediation-plan.md`。

### 2.1 结论

该报告作为一次性整改工作单的价值较高，可评为 **8/10**。它不是无效或过时文档：其中 H1–H3、M4–M16 和多数低优先级问题，在当前工作树中仍然存在；问题定位、优先级分组和整改顺序总体合理。

它最有价值的部分包括：

- 将并发阻塞、前端卡死、运维加固、数据版本和文档欠账放在同一工作单中；
- 多数条目包含准确的源码位置和可执行的最小修复方向；
- 明确指出不可删除的 `data/snapshot/20260904_v2`，降低误清理风险；
- 记录了前两轮整改和测试结果，形成连续审计链；
- 给出了分批顺序和验证命令，适合转化为 issue 或迭代计划。

### 2.2 当前仍成立的核心结论

以下问题经本轮复核确认仍未整改：

- H1：hybrid/vector 的同步 embedding 调用阻塞事件循环；
- H2：SSE 正常结束但无 `done` 时前端永久处于 streaming；
- H3：刷新后恢复出无法停止的幽灵流式消息；
- M4：限流 key 无界增长、无条件信任 XFF；
- M5：CORS 全开；
- M6：客户端断连后生成任务未可靠取消；
- M7：回答缓存无容量上限、冷过期项不清理；
- M8：开启 F02 LLM fallback 后同步调用阻塞事件循环；
- M9：HTTP/SSE 无连接与空闲超时；
- M10：localStorage 无裁剪、失败静默；
- M11：ECharts 全量加载、地图内嵌、首屏包过大；
- M12：`20260915_v1` 示例与题库元数据沿用旧版本；
- M13–M16：测试数、人工评分口径、阶段状态、thinking 展示说明仍不一致；
- L1–L14：多数低优先级项仍可复现。

### 2.3 需要修正的建议或表述

#### 1. H2 不能简单把中断消息设为 `finished=true`

第三轮报告建议 EOF 无 `done` 时设置 `streaming=false、finished=true`。但历史构建逻辑会把 `finished && !cancelled` 的回答加入下一轮上下文（`frontend/src/stores/session.ts:128-139`）。如果直接照做，网络中断留下的半截回答会被误当成完整答案。

正确方向应是：转为结构化 `interrupted` 状态，停止转圈但排除出历史；若继续使用多布尔字段，至少设置 `cancelled=true` 或同步增加历史过滤条件。

#### 2. M8 的 `asyncio.to_thread` 落点描述不够准确

F02 fallback 位于同步 `understand()` 链内部（`server/query/understand.py:219-226`），不能在该同步函数内直接 `await asyncio.to_thread(...)`。最小改法是在 `server/sse.py:105-110` 将包含 fallback 的整个理解调用放入线程；若要只异步 fallback，则需要重构理解链并提供同步评测适配层。

#### 3. M12 混淆了两个文件的元数据问题

`demo_examples.json` 确实内嵌错误的 `version/generated_at/source_run`；`questions.jsonl` 本身没有这三个顶层字段。题库的问题是逐字节复用旧版本且来源表达不清，相关错误版本位于 `questions.meta.json`。两者应拆开描述。

#### 4. H1 的触发条件应写得更严谨

阻塞缺陷真实且应保持高优先级，但只有在向量索引、embedding 配置和密钥均可用、实际走 vector/hybrid 路径时触发。向量不可用时会降级 keyword，因此“默认部署、多用户必现”表述过于绝对。

#### 5. “全部结论附文件行号”不完全成立

Git ahead/behind、目录体积、测试结果和文件哈希属于运行时证据，不能用源码行号证明。报告应记录核验命令、时间、分支和 commit，而不是把所有证据统一描述为“文件:行号”。

### 2.4 第三轮报告的主要遗漏

- 公共 SSE 原样输出模型 `reasoning_content/reasoning`；
- 请求体、问题、历史、筛选和纠正项均无尺寸上限；
- 流式回答已输出部分正文后透明重试，会拼接多个尝试的文本；
- 实体纠正后的新答案不进入后续多轮历史；
- `error + done(cancelled)` 的异常回答反而会进入历史；
- 移动端点击引用无法打开未挂载的知识面板；
- 服务部署版本未固定，`run_server.py --version` 没有真正控制 API；
- 数据、索引和前端构建产物均被忽略，仓库没有发布制品和 CI/CD；
- 数据 manifest 没有内容哈希，无法证明向量复用正确；
- `RAGv5-开发说明.md` 顶部仍写“待开工”；
- `server/README.md` 和 `evaluation/README.md` 大面积停留在旧实现口径。

### 2.5 作为长期项目资产的局限

- 报告自身未纳入 Git，无法保证随仓库传播；
- 缺少 `status/owner/fixed_in/verification` 等结构化字段；
- 源码证据、动态测试、Git 瞬时状态和体积统计混在一起；
- 修复建议没有进行“修复后语义反查”，H2 就是反例；
- 行号随代码变更漂移，缺少 commit 和符号名双重定位；
- 当前规范与历史记录未分层，旧状态容易再次被引用为现状。

---

## 三、项目当前问题清单

## 3.1 P0：应优先处理的正确性、稳定性与交付问题

### P0-1. 同步 embedding 阻塞整个异步服务

- 证据：`server/sse.py:170-177`、`server/text/searcher.py:82-105`、`data/index/embeddings.py:44-68`。
- 场景：hybrid/vector 可用时，在 async SSE 链中同步调用 OpenAI embedding，含 60 秒超时、三次尝试和 `time.sleep`。
- 影响：一个慢请求会冻结同一 worker 的全部 SSE、健康检查和其他异步请求。
- 优化：在线编排层使用 `asyncio.to_thread` 隔离同步检索，或提供异步 embedding；增加双并发请求响应性测试。

### P0-2. API 请求和字段无尺寸上限

- 证据：`server/api.py:185-219`、`contracts/request.py:84-96`、`server/query/understand.py:142-162`。
- 场景：超大 JSON、超长问题、超长 session id、巨量 history/filters/corrections。
- 影响：高内存和 CPU 消耗、embedding/LLM 成本放大，现有按 IP 限流不能替代请求大小限制。
- 优化：代理和 ASGI 限制 body；契约层限制字符数、历史轮数、单条内容长度和嵌套数组数量；超限在调用外部服务前返回 4xx。

### P0-3. 原始模型推理内容直接暴露到公共 SSE

- 证据：`server/generate/llm_client.py:100-115,152-153`、`server/sse.py:269-284`；与 `docs/features/06-grounded-answer.md:92-98` 的过滤要求冲突。
- 影响：API 调用方可读取完整模型推理增量，可能包含中间判断、上下文复述或不适合展示的内容。
- 优化：默认不发送原始 reasoning，只保留内部时延；若产品需要展示，生成独立、受控的高层状态或摘要。

### P0-4. 实体纠正后的有效答案不进入后续历史

- 证据：`frontend/src/stores/session.ts:128-142,395-417,474-483`。
- 场景：原回答 A 经实体替换/移除后生成 B，B 前没有新的 user 消息，历史配对逻辑忽略 B。
- 影响：后续追问继续使用纠正前答案 A，与界面展示的 B 矛盾。
- 优化：将消息模型升级为显式 turn，支持 `turnId/parentTurnId/supersedesTurnId/effectiveAnswer`；最低成本方案是让纠正结果显式替代原 turn。

### P0-5. 异常回答会污染后续对话历史

- 证据：`frontend/src/stores/session.ts:128-139,340-358`；后端 `server/sse.py:344-353` 发送 `error + done(cancelled)`。
- 场景：前端收到 error 后，done 又设置 `finished=true`，但未设置 `cancelled=true`。
- 影响：空回答或异常前的半截回答被加入下一轮历史。
- 优化：以 `finish_reason` 和 error 统一派生终态；只有允许的完成状态进入历史。

### P0-6. SSE 无 done 与页面刷新均产生永久卡死状态

- 证据：`frontend/src/stores/session.ts:92-107,231-253,295-301`、`frontend/src/api/sse.ts:48-58`。
- 影响：消息永久转圈；刷新后的幽灵任务没有 AbortController，却无法由用户停止。
- 优化：建立判别联合状态机 `connecting/streaming/completed/refused/degraded/cancelled/failed/interrupted`；持久化只保存稳定状态，恢复时将瞬态状态迁移为 interrupted。

### P0-7. 服务没有固定生效数据版本

- 证据：`server/runtime.py:74-92` 按目录名选择最新一致版本；`server/api.py:39-46` 未接收版本；`scripts/run_server.py:53-59` 使用模块字符串启动，`--version` 没有传给 API runtime。
- 影响：只要目录中出现更大的版本号，重启就可能静默切换；无法可靠灰度或回滚。
- 优化：生产环境要求显式 `RAG_ACTIVE_VERSION`，或使用 app factory 传参；健康接口返回 snapshot/index/manifest hash/Git commit。

### P0-8. 干净仓库无法重建可部署系统

- 证据：`.gitignore:9-19,29-35` 忽略 snapshot/index/dist/runs；`server/api.py:239-247` 依赖本地 dist；项目无 Dockerfile、CI 工作流或发布清单。
- 影响：公开仓库检出后无法加载完整 runtime、同源网页和报告中的 9544 向量环境。
- 优化：建立带 SHA-256 的 release artifact；提供下载、校验、解包和前端构建流程；CI 分层执行单元、夹具集成、前端测试与构建。

### P0-9. 活跃版本仍传播旧版本示例与题库元数据

- 证据：`data/eval/20260915_v1/demo_examples.json:2-4`、`questions.meta.json:2-5`、`scripts/gen_demo_examples.py:178-195`、`server/api.py:127-132`。
- 影响：runtime 声称 `20260915_v1`，示例接口却返回 `20260904_v2`，示例时延和质量也来自旧 run。
- 优化：取消生成脚本硬编码；重新生成活跃版本资产；目录版本、文件版本和 source run 版本不一致时启动或接口直接报错，不建议静默覆写。

### P0-10. RAG 在父仓库中的版本管理关系不明确

- 证据：RAG 自身是内层 Git 仓库，分支 `ragv5-demo-deploy` 领先远端 3 个提交；父仓库把整个 `RAG/` 显示为未跟踪目录。
- 影响：父仓库无法固定 RAG 版本，协作者可能得到完全不同的本地目录；第三轮报告的“合并 main”只适用于内层仓库。
- 优化：明确选择独立仓库、Git submodule 或并入父仓库之一；不要维持“父仓库未跟踪的嵌套仓库”状态。

## 3.2 P1：应在稳定性迭代中处理的问题

### 后端与接口

1. **断连后生成任务不可靠取消**：`server/sse.py:272-303` 缺少 `try/finally`、cancel 和 await 回收，可能继续消耗 token并产生未检索异常。
2. **F02 fallback 阻塞事件循环**：`server/sse.py:105-110`、`server/query/llm_fallback.py:31-62`；默认关闭但开启后最长阻塞约 8 秒。
3. **限流器不安全且非并发原子**：`server/api.py:147-170`；XFF 可伪造、key 无界增长、检查与追加没有锁。
4. **回答缓存无容量上限**：`server/generate/cache.py:39-64`；只在命中同 key 时清理过期项。
5. **流中途失败后的透明重试会拼接重复答案**：`server/generate/llm_client.py:77-96,138-156`；一旦正文已发送，重试不能撤回前一段文本。
6. **配置缺少集中校验**：`config/settings.py:142-214`；非法数字、负值和未知枚举可能导致启动崩溃、全量拒绝或静默降级。
7. **请求契约只做 dataclass 展开**：`contracts/request.py:26-95`；字段类型、枚举、长度和数量没有可靠验证，OpenAPI 也不准确。
8. **SSE 无整体 deadline、heartbeat 和反缓冲头**：`server/sse.py:87-353`、`server/api.py:215-219`。
9. **运行时资源没有生命周期清理**：`server/runtime.py:41-43` 的 shutdown 为空，AsyncOpenAI 等客户端未显式关闭。

### 前端正确性与页面

10. **移动端引用导航失效**：`frontend/src/App.vue:54-76`、`ChatPane.vue:79-85`；知识面板关闭时接收者未挂载，点击引用事件丢失。
11. **实体替换候选跨 mention、跨类型混入**：`MessageBubble.vue:64-87,193-205`；需要稳定的 mention/candidate 关联字段。
12. **SSE 解析不兼容 CRLF 和 EOF 残帧**：`frontend/src/api/sse.ts:27-56`；代理改变换行时可能收不到 done。
13. **HTTP 和 SSE 均无超时**：`frontend/src/api/sse.ts:73-83`、`api/http.ts:4-11`、`api/demo.ts:4-9`。
14. **localStorage 无裁剪且失败静默**：`session.ts:144-156`；长会话超配额后恢复能力悄然失效。
15. **首屏包体过大**：`SubGraphView.vue:1`、`MapView.vue:1-4`、`PanelPane.vue:3-8`；现有 ECharts chunk 约 1.0MB，主 chunk 约 608KB，地图源约 572KB。
16. **流式 Markdown 全量重渲染并堆积双 RAF**：`MarkdownContent.vue:21-23,27-110`；长回答可能趋向二次增长。
17. **任意 `[数字]` 都被渲染为引用**：`MarkdownContent.vue:41-49,79-87`；未与真实 citations 交叉校验。
18. **接口边界依赖 `any` 和类型断言**：`types/contract.ts:165-170`、`session.ts:92-103,305-379`；合法 JSON 也可能在渲染期崩溃。
19. **生成中面板错误显示“暂无数据”**：`PanelPane.vue:15-27,76-99`；没有区分加载、完成为空、筛选为空和异常。
20. **失败轮没有可执行恢复入口**：界面只显示错误文本，缺少沿用原问题、原筛选和原纠正项的“重试本轮”。

### 数据、评测与工程

21. **数据 manifest 没有内容哈希**：`data/snapshot/governance.py:270-289`、`data/index/build.py:87-107`；无法证明复用向量与新 chunks 文本逐条一致。
22. **最新活跃版本评测不是生产 LLM 评测**：`data/eval/20260915_v1/runs/v5_coords_v1/meta.json:3-15` 中 `llm_used=false`，只能证明离线回归。
23. **156 个测试依赖被忽略的本机数据**：多个测试缺旧 snapshot/index 时直接 skip；干净 clone 的绿色结果可能没有覆盖核心集成链路。
24. **没有前端自动化测试**：`frontend/package.json:6-10` 只有开发、构建、类型检查和预览。
25. **Python 依赖没有锁定和分层**：`requirements.txt:4-17` 只有宽松下界，未声明 pytest；审计脚本需要 pandas，服务却声明未使用的 sse-starlette。
26. **manifest 和环境示例泄露本机绝对路径**：`data/snapshot/20260915_v1/manifest.json:4-8`、`.env.example:13-15`。
27. **Chroma 目录疑似存在孤儿 segment**：两个活跃索引均有两个约 38MB UUID 数据段，但 manifest 只声明一个集合；删除前必须用 Chroma API 确认。

### 文档体系

28. **契约对断连取消作出尚未实现的保证**：`docs/data-contract.md:414-418` 声称断开后立即终止生成，代码并未做到。
29. **thinking 口径跨多文档冲突**：不仅是 deploy，`features/06-grounded-answer.md:61-65` 和 `RAGv5-开发说明.md:629` 也声称前端展示 thinking。
30. **server README 仍把向量写成待接入**：`server/README.md:49-61` 与当前 vector/hybrid/rrf 实现冲突。
31. **evaluation README 缓存键说明过时**：`evaluation/README.md:52-56` 称不区分检索通道，但 `server/generate/cache.py:24-35` 已包含 text mode。
32. **RAGv5 开发说明顶部仍写待开工**：`docs/RAG_v1/RAGv5-开发说明.md:2-6` 与后文实施结果矛盾。
33. **架构和契约仍标“初稿”**：`docs/architecture.md:2-4`、`docs/data-contract.md:2-4`，但已被其他文档作为正式规范引用。
34. **当前入口与历史规划未分层**：`后续阶段规划.md`、`RAG_v1/README.md`、根入口和阶段总结存在状态冲突。

## 3.3 P2：低优先级但应纳入收口的问题

- 错误和限流建立 SSE 后仍返回 HTTP 200，缺少 `RATE_LIMITED`，结束原因语义失真：`server/api.py:194-234`、`contracts/sse.py:47-52`。
- 共享生成器的 `last_usage/last_truncated` 在并发下相互覆盖：`server/generate/__init__.py:39-42,60-61`。
- 缓存历史签名只取 JSON 尾部 400 字符：`server/generate/cache.py:25-36`。
- CORS 对全部来源、方法和头开放：`server/api.py:31-37`。当前主要风险是资源滥用，不是典型 cookie 窃取。
- `.env.example` 缺少 `QUERY_FUSION_LIMIT`：`.env.example:65-72`。
- FastAPI 版本仍为 `ragv2`，docstring 和启动脚本含本机绝对路径：`server/api.py:8-10,29`、`scripts/run_server.py:1,26,34`。
- 评测 meta 把 suites 字符串逐字符 join：`evaluation/cli.py:289-293`，实际产物出现 `m,a,i,n`。
- 冒烟 JSON 不含显式 exit_code：`scripts/smoke_deploy.py:254-267`。
- 前端持久化无 schemaVersion；取消状态依赖中文字符串“已取消”：`session.ts:29,280-291`。
- 输入框、证据条目、tabs、筛选弹层、移动抽屉、toast 和状态提示缺少基本无障碍语义。
- 筛选弹层可同时打开，点击外部和 Escape 不关闭：`FiltersBar.vue:5-7,28-83`。
- 根 README 仍写 154 个测试，当前实测为 156：`README.md:46-47`。
- `docs/README.md` 同一文件内既写 hybrid/rrf，又保留“向量待接入”。
- F04 文档既写已定档 hybrid/rrf，又保留“待确认模式”：`docs/features/04-text-retrieval.md:3-4,39-44,62-65`。
- 清理工作未执行，旧报告对双扩展文件的数量和体积不准确；应重新生成 inventory 后再删。
- RAG 分支领先远端 3 个提交，第三轮报告自身和本报告均未跟踪，审计结果尚未进入协作链。

---

## 四、整个项目的修改与优化方案

## 4.1 功能与架构优化

### 方案 A：统一会话 turn 模型

把当前“user/assistant 消息相邻配对”改为显式业务轮次：

```text
Turn
- turnId
- parentTurnId
- supersedesTurnId
- question
- filtersSnapshot
- correctedEntitiesSnapshot
- answer
- status
- finishReason
- evidence/panel
```

收益：

- 纠正结果可替代原回答并被后续追问继承；
- 失败、取消、中断轮可自然排除出历史；
- 重试能够复用原轮问题和筛选，而不是读取当前全局状态；
- 页面可明确展示“该回答替代了哪一轮”。

### 方案 B：统一前后端流状态机

服务端和前端共同约定结构化终态：

```text
completed | refused | degraded | cancelled | failed | interrupted | timeout
```

`done` 必须携带可判定状态；EOF 无 done 由前端映射为 interrupted；用户 abort、网络错误、协议错误和超时不得再依赖展示文案判断。

### 方案 C：建立在线任务生命周期

- 同步网络操作放入线程或改为异步；
- 每个请求拥有独立 deadline、取消域和资源回收；
- SSE 生成器 `finally` 取消并 await 子任务；
- 首个正文 token 发出后禁止透明重试；
- 增加 heartbeat、代理反缓冲头和空闲超时。

### 方案 D：把数据版本视为不可变发布单元

每个发布版本至少包含：

- snapshot manifest 与内容哈希；
- index manifest、chunks/ids/embeddings 哈希；
- 前端 dist 哈希；
- 题库、示例和 source run 版本；
- Git commit、Python/Node/依赖锁哈希；
- 下载、校验和解包脚本。

生产启动必须显式选择版本，不再扫描“最新目录”。

### 方案 E：评测分成两条基线

1. **确定性离线回归**：检索、契约、排序、引用、状态机；可在 CI 稳定运行。
2. **固定模型在线评测**：记录模型、参数、endpoint、成本、延迟、LLM 使用标志和复核主体；用于回答质量判断。

不得用 `llm_used=false` 的 run 代表生产回答质量。

## 4.2 页面与交互优化

### P0：打通“回答—引用—证据—返回回答”闭环

- 桌面端点击引用后定位并展开证据；
- 移动端自动打开抽屉、切换证据 tab、展开并聚焦目标；
- 证据项提供复制和返回正文引用位置；
- 只把 citations 中真实存在的编号装饰为引用。

### P0：失败和中断可恢复

回答气泡应区分：用户取消、网络中断、连接超时、服务异常、拒答和降级。失败气泡提供：

- 重试本轮；
- 编辑原问题；
- 复制错误详情；
- 保留部分文本时标记“回答可能不完整”；
- 重试默认沿用原轮筛选和实体纠正。

### P1：知识面板使用完整生命周期空状态

至少区分：

1. 尚未提问；
2. 正在识别实体；
3. 正在检索；
4. 正在生成、证据尚未到达；
5. 已完成但该 tab 无数据；
6. 筛选后无结果；
7. 网络中断或失败。

### P1：重构筛选交互

- 筛选靠近输入区，表达“本次查询条件”；
- 发送后将筛选快照展示在 user turn；
- 纠正重查默认沿用源问题筛选；
- 移动端收拢为“筛选（N）”按钮和底部面板；
- 弹层互斥，支持外部点击、Escape 和焦点回退。

### P1：首屏性能优化

- 图谱与地图 tab 使用动态 import；
- ECharts 使用 `echarts/core` 按需注册；
- 中国地图 JSON 首次进入地图时加载并缓存；
- 流式 Markdown 每帧或 50–100ms 节流，取消旧 RAF；
- CI 设置 gzip/brotli 体积预算，不再仅提高 chunk 告警阈值。

### P2：长会话与无障碍

- 用户上滚后暂停自动滚动，并显示“有新内容”；
- 增加导出、回到最新回答和历史裁剪提示；
- 大型持久化迁移 IndexedDB，或只保留最近 N 个有效 turn；
- 使用原生 button、标准 tabs、dialog、焦点陷阱、Escape、`aria-live`、`role=status/alert`；
- 移动抽屉使用 `100dvh`，锁定背景滚动并在关闭后恢复焦点。

## 4.3 文档体系优化

### 建立四层文档结构

- **current**：当前架构、契约、部署、功能状态；必须与代码一致；
- **adr**：关键决策，如 hybrid/rrf、thinking 暴露策略、同源部署；
- **history**：阶段规划、开发记录、审核报告；只描述特定 commit 和日期；
- **generated**：评测 run、冒烟、指标和资产清单；由工具生成。

### 给规范文档增加元数据

```text
status: active | draft | deprecated | archived
applies_to_commit:
last_verified:
owner:
supersedes:
superseded_by:
```

优先覆盖 `architecture.md`、`data-contract.md`、`deploy.md` 和 `features/*.md`。

### 建立单一事实源

- 默认配置只以 `config/defaults.py` 为源；
- SSE 枚举只以 `contracts/sse.py` 为源；
- 活跃数据版本只以部署 manifest 或环境配置为源；
- 测试数由 CI 产生；
- 阶段状态只在一个 roadmap 维护；
- 评测数字必须引用 run id，不复制无上下文裸数字。

### 审计报告使用结构化发现模板

```text
ID / severity / category / status
commit / evidence / reproduction
impact / recommended_fix
owner / fixed_in / verification
```

Git 状态、体积和测试结果应附命令和时间，不应伪装成源码行号证据。

## 4.4 测试、CI 与质量门禁

### 后端新增测试

- 两个并发 hybrid 请求不阻塞事件循环；
- 请求体与字段长度/数量限制；
- 断连和 `aclose()` 后子任务取消；
- 流已输出正文后失败不得透明拼接重试；
- 限流 XFF 策略、容量清扫和并发原子性；
- 缓存过期清扫与容量淘汰；
- 非法配置 fail fast；
- lifespan 关闭外部客户端。

### 前端新增测试

- EOF 无 done、error+done、abort、timeout；
- CRLF、UTF-8 分块和 EOF 残帧；
- 刷新恢复流状态；
- 原回答—实体纠正—后续追问的历史正确性；
- 移动端点击引用打开并定位证据；
- 引用编号与 citations 交叉校验；
- localStorage 迁移、裁剪和配额失败；
- 无障碍键盘操作。

### CI 建议

- Python 纯单元测试；
- 仓库内小型 fixture 集成测试；
- 完整数据/在线模型测试作为显式阶段，禁止意外 skip；
- 前端 typecheck、lint、Vitest、Playwright、build；
- Markdown 链接与状态一致性；
- `.env.example` 与 Settings 差异；
- 后端枚举、前端联合类型和数据契约差异；
- 活跃版本、manifest、demo、题库和 run 元数据一致性；
- 构建包体预算和发布 artifact 哈希。

---

## 五、建议整改路线

### 第一批：P0 正确性与安全边界

1. 决定并关闭原始 reasoning 的公共输出；
2. 隔离同步 embedding 与 F02 fallback；
3. 统一前端状态机，修复 EOF、刷新、error+done；
4. 修复纠正答案历史和移动端引用导航；
5. 增加请求体、字段长度和数量限制；
6. 为以上行为补守护测试。

**验收标准**：两个并发问答互不冻结；任何流终止路径都进入明确终态；失败/中断/旧回答不进入历史；移动端可从引用到达证据。

### 第二批：在线稳定性与性能

1. 取消并回收断连任务；
2. 修复限流器、缓存容量和配置校验；
3. 增加 deadline、heartbeat、反缓冲头和前端超时；
4. 修复 SSE 标准解析和流式重试语义；
5. 图谱、地图与 ECharts 懒加载，Markdown 渲染节流。

**验收标准**：断连后模型任务停止；内存结构有明确上限；代理环境下 SSE 可持续工作；首屏产物显著下降且有体积门禁。

### 第三批：数据与可复现交付

1. 固定 `RAG_ACTIVE_VERSION`；
2. 重新生成 `20260915_v1` demo、题库 meta 和对应 run；
3. 建立 snapshot/index/eval 全链路哈希；
4. 构建 release artifact 和干净 clone 部署流程；
5. 锁定依赖并分离 runtime/test/audit 依赖。

**验收标准**：在另一台机器上仅凭仓库和发布制品可校验、启动、测试和回滚；健康接口能报告可追溯版本。

### 第四批：页面体验、文档与工程收口

1. 重构筛选、空状态、失败重试、长会话和无障碍；
2. 建立前端测试金字塔；
3. 按 current/ADR/history/generated 分层文档；
4. 修正所有当前入口的旧版本状态；
5. 核查 Chroma segment 后执行分类清理；
6. 明确内层 RAG 仓库与父仓库的集成方式并推送远端。

**验收标准**：当前文档不再出现互相冲突的阶段状态；干净 CI 覆盖前后端；历史文档明确标注适用 commit；清理有机器可读 inventory 和回滚依据。

---

## 六、项目优势与应保留资产

整改不应掩盖项目已有价值，以下能力应保留并继续强化：

- 图谱、关键词、向量和 hybrid/rrf 已形成可组合检索链；
- SSE 事件类型覆盖状态、实体、证据、子图、地图、回答和结束信息；
- 39 条题库、多个评测 suite 和历史 run 为回归提供了基础；
- 第二轮整改后 156 个 Python 测试在完整数据环境中全部通过；
- 文档覆盖架构、契约、功能、部署、评测和阶段历史，缺的是治理而不是数量；
- 前端已具备引用、实体纠正、图谱和地图的产品雏形，优化重点应是正确性闭环、移动端和状态一致性，而非推倒重写。

---

## 七、本轮验证与限制

### 已完成

- 严格排除 `RAG/new/`，未读取、搜索或引用其内容；
- 复核第三轮报告的大部分源码与文档证据；
- 审查后端、前端、数据、评测、测试、依赖、部署和 Git 状态；
- 无缓存运行 Python 测试：`156 passed，19.77s`；
- 核对内层 RAG 仓库分支：领先 `origin/ragv5-demo-deploy` 3 个提交；
- 仅新增本报告，未修改任何代码和既有文档。

### 未完成或不应过度解读

- 未运行在线 LLM/embedding 冒烟，因此不对外部服务当前可用性作结论；
- 未运行前端 build，避免产生 `dist` 和 `*.tsbuildinfo`；包体依据现有构建产物核验；
- 未删除任何日志、索引、segment 或实验产物；
- 未修改 Git 分支、提交、远端或父仓库集成方式；
- “156 passed”仅代表当前本机完整数据目录，不能证明干净 clone 也会执行同等覆盖范围。

---

## 八、最终结论

第三轮报告仍然是一份有价值的整改工作单，但不能直接作为最终实施规范：其大多数发现仍未关闭，部分修复建议需要纠正，而且本轮又发现了会话历史正确性、原始推理暴露、请求大小、流式重试、部署版本、发布制品和数据哈希等更基础的问题。

项目下一阶段不宜继续优先堆叠新功能。最优路线是先完成三项系统性收口：

1. **用统一 turn 模型和流状态机修复多轮问答正确性与永久卡死；**
2. **用显式版本、哈希 manifest、发布制品和 CI 建立可复现交付；**
3. **用文档分层和单一事实源消除“实现已完成、入口仍写待开工”的长期漂移。**

完成这三项后，现有图谱、混合检索、引用、实体纠正和地图能力才能从“本机可演示”稳定提升为“可部署、可验证、可协作维护”的完整 RAG 产品。
