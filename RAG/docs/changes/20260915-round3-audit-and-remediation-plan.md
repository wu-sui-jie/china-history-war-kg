# 20260915 第三轮审核报告与整改方案

> **归档说明（2026-09-20 文档整理）**：本文档是**第三轮审核的过程记录**（带解决方向的整改工作单），
> 所列问题已闭环（复核见 [第四轮复核分析与优化建议](20260915-round4-full-review-analysis.md)）。
> 归档于 `docs/changes/`。**现行事实以 [docs/current-status.md](../current-status.md) 为准**；
> 日常开发不必读本文档。


> 本报告是第三轮全项目审核的产物，定位是**带解决方向的整改工作单**，交给执行方按方向落地。
> 审核时间：2026-09-15。审核方式：三路并行（后端运行时深度审查、前端+数据一致性审查、
> 文档全量扫描），全部结论附 文件:行号 证据。
> 范围说明：`new/` 目录为未启动的新功能暂存区，**不在本报告范围内**。
> 前两轮的 22 条问题已闭环（本轮复核确认第二轮回填声明全部属实：156 个测试实跑通过、
> 降级日志与两条新用例存在、冒烟报告在 `logs/smoke_20260915_*.json` 可查、
> 提交 e64d490/d0bccd4 与 git log 一致），本报告只列**新发现的未解决问题**。
> 各问题只给解决方向，不含完整代码；执行方按方向自行实现。

## 一、问题总览

| 级别 | 数量 | 摘要 |
| --- | --- | --- |
| 高 | 3 | 1 个后端并发阻塞（多用户必现）+ 2 个前端"永久卡住"状态机缺口 |
| 中 | 13 | 后端运维加固 5 项、前端体验 3 项、数据版本链 1 项、文档欠账 4 项 |
| 低 | 14 | 错误码语义、依赖声明、过时注释等瑕疵 |
| 清理 | — | 约 190MB 可删除的实验/调试遗留产物（注意 20260904_v2 快照不能删） |
| 流程 | 2 | 远端推送与 main 合并决策 |

## 二、高优先级问题与解决方向（3 条）

### H1. hybrid/vector 检索的 embed 调用阻塞整个事件循环

- 位置：`server/text/searcher.py:82-94`（search_vector 内同步调 embed）→
  `data/index/embeddings.py:44-68`（同步 OpenAI 客户端 + 3 次重试 + time.sleep 退避 +
  60s 超时）→ 编排点 `server/sse.py:172-177`
- 问题：默认部署即 hybrid 模式，每次提问的向量检索是一次同步 HTTP，直接跑在 asyncio
  事件循环上；最坏情况（重试+退避+超时）把循环卡住数分钟，期间所有并发 SSE 流
  （包括正在打字的 LLM 流）全部停摆。多用户并发必现互相卡顿。
- 解决方向：
  - **方案 A（推荐，最小改动）**：不改检索函数本身，在编排层把 hybrid/vector 检索段
    包进线程池——`server/sse.py` 调用 `runtime.search`（或 text 包的 search）处用
    `asyncio.to_thread(...)`（Python 3.11 可用）。检索函数保持同步，评测链路
    `evaluation/chain.py`（纯同步）完全不受影响。
  - 方案 B：`data/index/embeddings.py` 改 AsyncOpenAI 异步客户端——改动更大，
    且评测链路是同步的，需要维护两套客户端，不建议本轮做。
  - 注意事项：TextSearcher 被全部请求共享，`embed_fn` 需确认线程安全
    （openai 客户端线程安全，属实即可）；FTS5/图谱检索为本地轻量操作，无需包裹；
    补一条并发回归测试（两个并发 hybrid 请求互不阻塞）作为守护。

### H2. 前端 SSE 流"正常关闭但未发 done"时消息永久卡"进行中"

- 位置：`frontend/src/stores/session.ts:296-302`（launch 兜底只处理有 error 的情况）
- 问题：代理断流、服务重启等"连接正常关闭但 done 没送达"的场景下，
  `streamQuery` 正常 resolve、`onError` 不触发，`assistant.streaming` 恒为 true——
  气泡转圈永不消失，且该轮因未 finished 被静默排除出后续多轮上下文（session.ts:136）。
- 解决方向：扩展 `launch()` 的兜底——`await streamQuery` 结束后，无论是否收到 done，
  只要 `assistant.streaming` 仍为 true 就强制收尾（置 streaming=false、finished=true，
  可顺带给该气泡补一句"连接中断"提示）。后端契约保证错误路径 error+done 配对
  （`server/sse.py:344-353`、`api.py:222-234`），此兜底只针对网络层异常断流，
  不会与正常路径冲突。改动单点；用"手动杀掉服务进程"做一次手工验证即可。

### H3. 生成中途刷新页面后，消息永久转圈且无停止按钮

- 位置：`frontend/src/stores/session.ts:93-108`（readPersisted 无归一化）+
  `session.ts:250-253`（streaming:true 被持久化）+ `ChatInput.vue:52`
- 问题：发送时把 `streaming:true` 写入 localStorage；刷新恢复后 active 为空、
  streaming 为 true，气泡永远转圈，输入框只有"发送"没有"停止"，boot() 无矫正逻辑。
- 解决方向：在 `readPersisted()`（或 boot()）加载时做状态归一化——把所有消息的
  streaming 强制置 false；对未 finished 的消息补 `finished=true` 并附加一条提示
  （如"页面刷新导致本轮回答中断"）。可与低优先级 L-9（持久化 schema 版本号）一并做：
  给持久化数据加 `schemaVersion` 字段，读取时按版本迁移/矫正，顺便消除未来字段变更的隐患。

## 三、中优先级问题与解决方向（13 条）

### 后端运维加固（本地演示无感，公网/长期运行出问题）

#### M4. 限流器内存无界增长 + XFF 头可伪造绕过

- 位置：`server/api.py:148-170`
- 问题：RateLimiter 只清理单 key 的命中列表，key 本身永不删除；`_client_key` 无条件信任
  `x-forwarded-for` 首段。公网下可伪造 XFF 无限制造 key 消耗内存并绕过限流。
- 解决方向：① key 集合加清理机制——简单做法是当 key 数超过阈值（如 10,000）时全表
  清扫已过期 key，或改用固定容量 LRU；② XFF 信任加开关：新增环境变量
  `TRUST_PROXY_HEADERS`（默认 false，直连场景用 `request.client.host`），
  部署在反向代理后时才打开；`docs/deploy.md` 环境变量表同步新增条目。

#### M5. CORS 全开

- 位置：`server/api.py:32-37`（`allow_origins=["*"]`）
- 解决方向：当前形态是同源托管（前端由后端自己提供，`api.py:237-247`），dev 模式走
  vite 代理，理论上不需要跨域。方案：CORS 中间件改为**可配置**——从环境变量读白名单
  （默认空 = 不启用跨域），或干脆移除 CORS 中间件、在 deploy.md 注明"如需跨域接入再配"。
  保留全开会带来公网滥用面，建议至少改为配置驱动。

#### M6. 客户端断连后生成任务继续跑（孤儿任务白烧 token）

- 位置：`server/sse.py:272-276`（create_task 后无 try/finally 取消）
- 问题：客户端断开 → StreamingResponse 关闭生成器 → `await task`（sse.py:303）永不执行，
  LLM 生成继续跑完，token 白烧、结果不入缓存，任务异常还会产生
  "exception was never retrieved" 日志噪音。
- 解决方向：在事件生成器加 `finally` 块——生成器被关闭（GeneratorExit/取消）时
  `task.cancel()` 并 suppress CancelledError。注意：被取消的生成不计入缓存
  （与 degraded/cancelled 不入缓存的既有口径一致，sse.py:326-330 天然满足）；
  验证 openai 异步流在 task cancel 下的中断行为（await 点会抛 CancelledError，符合预期）。

#### M7. 回答缓存无容量上限，过期条目只增不减

- 位置：`server/generate/cache.py:44-60`
- 问题：TTL 只在 get 命中时检查并弹出；从未再访问的过期条目永久滞留，`_store` 无上限。
  配合 M4 的伪造 IP 绕过限流，可加速内存增长。
- 解决方向：`put()` 时做两步——先全表清扫过期条目，清扫后仍超上限（新增环境变量
  `CACHE_MAX_ENTRIES`，建议默认 500）则按插入顺序淘汰最旧。锁内完成，
  单测覆盖"过期清扫 + 淘汰最旧"两个行为；deploy.md 环境变量表同步。

#### M8. F02 LLM 实体兜底为同步调用，开启后阻塞事件循环

- 位置：`server/query/llm_fallback.py:31-36、51-56`（同步客户端 8s 超时），
  编排点 `understand.py:225`
- 问题：默认关闭（`ENABLE_LLM_ENTITY_FALLBACK=false`），一旦开启，词典未命中的问题
  会把事件循环阻塞最长 8 秒。
- 解决方向：与 H1 同思路——在编排层把兜底调用包 `asyncio.to_thread`。
  该功能默认关闭，优先级可放低；若短期不打算开启，最低成本是在
  `config/defaults.py` 该开关的注释里明确标注"同步实现，开启将阻塞事件循环，
  仅建议离线评测使用"。

### 前端体验

#### M9. fetch 与流读取均无超时控制

- 位置：`frontend/src/api/sse.ts:74-84`
- 问题：后端僵死或 TCP 静默挂起（不发 FIN）时，前端永远停在"生成中"，只能手动点停止。
- 解决方向：读循环加**空闲看门狗**——自上一个事件起 N 秒未收到任何字节则 abort 并走
  `onError("连接超时")`；fetch 本身可加连接级超时（AbortSignal.timeout 兜底）。
  阈值注意与 LLM 首正文时延兼容（冒烟实测 2-14s），建议"自上一事件起算 60s"而不是
  固定总时长。与 H2 的兜底配合后，前端不再存在"永久卡住"路径。

#### M10. localStorage 持久化无裁剪，超配额后静默失效

- 位置：`frontend/src/stores/session.ts:145-157`
- 问题：全量序列化所有消息（含每条 assistant 的完整 panel），长会话可触顶 5MB；
  catch 只注释不提示，此后刷新恢复悄悄失效。
- 解决方向：persist() 时裁剪——只保留最近 N 条消息（如 20 条）或剥离已结束消息的
  panel 大对象（panel 刷新后并不回放，价值低）；写入失败（catch）时用裁剪后的数据
  重试一次并 console.warn。与 H3 的 schema 版本号一并处理最顺。

#### M11. echarts 全量引入 + 地图 JSON 内嵌，首屏约 1.84MB 且服务端无压缩

- 位置：`MapView.vue:2`、`SubGraphView.vue:2`（`import * as echarts`）；
  `dist/assets/echarts-*.js` 1.0MB；index 621KB（含 582KB china-map.json）；
  `server/api.py` 无 GZip 中间件
- 解决方向：① echarts 改按需引入——`echarts/core` + 只注册本项目用到的
  GraphChart/ScatterChart/GeoComponent/CanvasRenderer 等，预计 1.0MB 降至 300-400KB；
  ② 地图 JSON 可改放 `public/` 运行时按需 fetch（首次进入地图 tab 时加载），
  或先保持内嵌接受体积；③ 服务端给静态资源加压缩——fastapi 的 GZipMiddleware
  对 SSE 流的兼容性需要验证（event-stream 不应被缓冲压缩），稳妥做法是只对静态文件
  启用压缩或构建时预压缩（vite 插件产 .gz）。每步改完 `npm run build` + 冒烟验证。

### 数据版本链

#### M12. 生效版本 demo_examples.json / questions.jsonl 是旧版本的逐字节拷贝，元数据失真

- 位置：`data/eval/20260915_v1/`（生效版本）内两文件内容与 `20260904_v2` 完全相同；
  文件内嵌 `version: 20260904_v2`、`generated_at: 2026-09-13`、
  `source_run: run_20260913_postaudit`；`/api/demo/examples` 原样返回不覆写
  （`server/api.py:130-132`）；根因 `scripts/gen_demo_examples.py:180` 的
  `--version` 默认值硬编码 `20260904_v2`
- 解决方向（三选一，成本递增）：
  1. **最低成本**：`server/api.py` 返回 demo examples 时覆写 `version=rt.version`
     （前端只读 capability/question 等字段，不受影响）；
  2. 修根因：`gen_demo_examples.py` 的 `--version` 默认值改为自动取最新数据版本
     （复用 `lib/versions.py` 的解析），下次生成时不再错；
  3. 最彻底：对 20260915_v1 重跑一次生成（基于 v5_coords_v1 的实测时延数据），
     产物自带正确元数据——需要跑在线链路，建议与人工评测回填一起安排。
  推荐先做 1 + 2（纯代码），3 留给数据版本统一收口时做。

### 文档欠账（前两轮整改的漏网同步）

#### M13. 根 README 用例数 154 未更新为 156

- 位置：`README.md:47`
- 解决方向：改为 156 并加时点注（"2026-09-15 第二轮整改后"）；顺手全文 grep "154"
  确认当前口径文档无其他残留（历史阶段文档豁免）。

#### M14. 5 处「人工评分」未加 AI 代理口径注，与 10 文档口径冲突

- 位置：`docs/README.md:52、115、130`、`docs/RAG_v1/README.md:17-18`
- 问题：第一轮 P2-22 只在 `features/10-evaluation.md` 内声明了"审核与评分主体是 AI 代理"，
  主索引 5 处未同步，两处口径直接冲突。
- 解决方向：5 处统一补短注「（AI 代理口径，待人工复核）」或加指向 10-evaluation.md
  「评分与审核口径」节的脚注；不要再展开，一处注记即可。

#### M15. RAG_v1 两份索引文档的阶段状态与实际相反

- 位置：`docs/RAG_v1/后续阶段规划.md:10`（RAGv5「仍待开工」）、`:21/:34-36`（RAGv3
  「Git 未固化」）、`:23`（RAGv5 行「未开工」）；`docs/RAG_v1/README.md:23-24`
  （「RAGv5 实施中」「提交待确认」）、`:19`（尾注已被 :20 取代）
- 解决方向：后续阶段规划.md 的状态字段统一改为「已完成」并加一行「2026-09-15 更新：
  RAGv5 已收口」；RAG_v1/README.md 的「实施中/提交待确认」改为「已完成/已入库」，
  :19 尾注删除。历史文档只改状态字段与加注，不重写正文。

#### M16. deploy.md 称前端显示「正在思考」，实际显示「正在生成回答」

- 位置：`docs/deploy.md:121`
- 问题：前端无任何「思考」文案，不消费 thinking 事件（显示逻辑在
  `MessageBubble.vue:270-273` 的 `正在{{ stage }}`）；部署演示手册会误导演示者。
- 解决方向：改为「正在生成回答」，并加注：thinking 事件为后端推理增量输出，
  当前前端默认忽略、不展示思考过程（与 data-contract.md 的"对接方可忽略"口径一致）。

## 四、低优先级问题与解决方向（14 条）

| # | 位置 | 问题 | 解决方向 |
| --- | --- | --- | --- |
| L-1 | `contracts/sse.py:47-52`、`api.py:195-213` | 限流/参数错误返回 HTTP 200 + SSE error 帧，无 RATE_LIMITED 错误码，finish_reason 误用 cancelled | ErrorCode 枚举增 `RATE_LIMITED`，限流路径改用之；同步 `frontend/src/types/contract.ts` 与 data-contract.md（三处联动改） |
| L-2 | `server/sse.py` | 无整体生成 deadline、无 heartbeat | 低优后置；上 nginx 前处理（proxy_read_timeout + 可选 SSE keepalive 注释帧） |
| L-3 | `server/generate/__init__.py:40-42、60-61` | last_usage/last_truncated 为实例属性，并发互相覆盖 | 改为 generate() 返回值的一部分或调用级上下文；仅评测消费，可暂不动 |
| L-4 | `server/generate/cache.py:33` | 缓存键对历史签名取 JSON 尾部 400 字符，理论可碰撞 | 改为对完整 JSON 串做 hashlib 哈希，一行改动 |
| L-5 | `.env.example` | 缺 `QUERY_FUSION_LIMIT` 条目（settings 可配、defaults 有默认 18） | 补一行含注释，与 defaults.py 值对齐 |
| L-6 | `requirements.txt` | sse-starlette 声明但全仓未使用；审计脚本用 pandas 未声明 | 删 sse-starlette；pandas 按实际使用决定（audit 一次性脚本可不加，改在脚本头注明依赖） |
| L-7 | `server/api.py:29`、`api.py:10` | FastAPI 元数据 version="ragv2" 过时；docstring 含本机解释器绝对路径 | 改 "ragv5"；docstring 去绝对路径改泛化表述 |
| L-8 | `frontend/src/types/contract.ts:159` | thinking 事件注释「后端当前不发射」过时（RAGv5 起已发射） | 注释改为「RAGv5 起后端发射；前端默认忽略（default 分支）」 |
| L-9 | `frontend/src/stores/session.ts:30` | 持久化无 schema 版本，未来字段变更无迁移 | 与 H3 一并做：persist 加 `schemaVersion`，读取时按版本矫正/迁移 |
| L-10 | `frontend/src/stores/session.ts:282` | 用魔法字符串「已取消」判断取消，依赖 sse.ts 文案 | 改为 sse.ts 暴露结构化取消标志（如 onError 附 reason），文案解耦 |
| L-11 | `docs/features/04-text-retrieval.md:65` | 待确认项 1（检索模式定档）已被评测定档 hybrid/rrf，未关闭 | 关闭该待确认项并引用评测结论，消除文内自相矛盾 |
| L-12 | `docs/changes/20260915-full-audit-fix-summary.md:143-144` | 「未提交 Git」与已提交现实矛盾 | 加回填注「已于 2026-09-15 分两笔提交（e64d490/d0bccd4）」 |
| L-13 | `evaluation/cli.py:292` | meta.json 的 command 字段对字符串逐字符 join，记录失真（实际执行正常） | join 前对 args.suites 做类型归一（list 或 split），仅影响元数据 |
| L-14 | `scripts/smoke_deploy.py` | 报告 JSON 不含退出码字段，只能从 failures 间接推断 | 主流程结束前把 exit_code 写入报告 JSON |

## 五、可清理的遗留产物（约 190MB，非代码问题）

| 对象 | 体积 | 定性 | 处置建议 |
| --- | --- | --- | --- |
| `logs/chrome-tmp*` 四个 Chrome 用户数据目录 + `cdp-*.mjs` + 7 个 fix-*.txt SSE 抓包 + chrome-err.log | ~59MB | 2026-09-04 渲染调试遗留，零引用且已 gitignore | 删除；保留 `amap_batch1-3.log`、`chunkexp_run.log`、`demo_examples_measure.log` 与 `smoke_20260915_*.json` 冒烟报告 |
| `data/index/20260904_v2_c500o100`、`_c1200o120` | ~113MB | 分块实验变体索引，运行时永不自动选中（实验已定档 800/80，见 chunk_exp_report.md） | 确认无需复跑实验后删除，实验报告与 chunkexp runs 保留作记录 |
| 根目录与 `frontend/` 下 `ragv3-render-check.json` 两份 | ~26KB | 渲染冒烟一次性产物，零引用 | 删除 |
| `data/index/20260904_v2/vectors/_parts/` 下 14 个 `batch_*.npy.npy` | ~0.55MB | 旧落盘逻辑的双扩展名残留（现行代码已修复） | 删除 |

**不能删**：`data/snapshot/20260904_v2`——它是两个变体索引的 source_snapshot 回指目标、
历史评测基线 run_20260913_postaudit 的数据版本、gen_demo_examples 默认版本；
在 M12 彻底收口并确认变体索引处置后再评估。

## 六、流程待办（非代码）

1. **推送远端**：`ragv5-demo-deploy` 领先 `origin/ragv5-demo-deploy` 3 个提交
   （第二轮整改的代码/文档两笔 + 复查报告回填一笔）未推送。
2. **main 合并决策**：main 落后当前分支 13 个提交（RAGv5 全部工作都在
   ragv5-demo-deploy 上）。按项目「统一提交到 GitHub 公开仓库」的约定，
   建议 main 快进合并后一并推送；合并前先跑一次全量 pytest 确认干净。

## 七、建议整改顺序

1. **第一批（高危 + 低成本文档，收益最高）**：H1（事件循环阻塞）、H2、H3
   （前端两个卡死路径）、M13–M16（纯文档同步）。
2. **第二批（运维加固）**：M4–M7（限流/CORS/孤儿任务/缓存上限）、M9、M10
   （前端超时与持久化裁剪）。
3. **第三批（数据与包体）**：M12（demo 元数据，先做方案 1+2）、M11（echarts 按需
   引入 + 压缩）、第五节清理项。
4. **第四批（顺手项）**：低优先级 L-1 至 L-14，其中 L-4/L-5/L-7/L-8/L-11/L-12
   成本极低可随批次捎带。
5. **每批完成后验证**：`E:/anaconda/envs/AI_Agent/python.exe -m pytest tests/ -q`
   （当前 156，新增行为须补守护测试）；前端改动后 `cd frontend && npm run build`
   并确认 dist 更新；运维类改动（M4-M7、M11）完成后按 `docs/deploy.md` 跑一次
   完整冒烟；文档改动后跑一遍 docs 相对链接检查。

## 八、执行约定

1. 项目红线不变：不修改 `backend/`、项目根旧 `frontend/`、`entity-event-relation/`
   中的旧代码；本报告所有路径均在 `RAG/` 子项目内。
2. `new/` 目录不在本轮范围，不读取、不引用、不调整其内容。
3. 文档改动保持既有约定：功能文档统一章节结构、状态只保留一种；同一事实多文档出现时
   全局 grep 同步（本报告 M13–M16 正是此前漏同步的教训）。
4. 行为有变化的代码改动（H1、M4-M7、M11、M12）必须配守护测试；M6/M7 需确认与
   既有缓存口径（degraded/cancelled 不入缓存）不冲突。
5. 完成后在对应条目标注处置结果，并在文末「整改记录」追加日期、改动文件清单、测试结果。

## 九、整改记录

（执行方填写）
