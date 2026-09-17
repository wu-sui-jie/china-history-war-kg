# 20260915 第四轮复核整改实施记录（代码）

- 文档类型：整改实施记录（对应 [20260915-RAG全项目复核分析与优化建议](../20260915-RAG全项目复核分析与优化建议.md)）
- 状态：**主体已实施；经第四轮复核发现 4 项发布阻断与若干收口项，已在后续整改中修复**
  （见 [20260915-round4-review-audit-and-next-optimization.md](20260915-round4-review-audit-and-next-optimization.md)
  与其修复记录 [20260915-round4-review-fix-summary-2.md](20260915-round4-review-fix-summary-2.md)）
- 整改日期：2026-09-15
- 验证结果（当时）：`pytest tests -q` → **188 passed**（整改前 156；新增 32 条 **Python pytest** 用例，
  不含前端测试）。写作时误记为 184，此处更正。
  `npm run build` 通过；另做了真实服务的接口级与浏览器级端到端验证（见第四节）。
  当前基准见最新整改记录：后端 **225 passed**、前端 `npm test` **24 passed**。

## 一、总览

| 批次 | 内容 | 落地情况 |
| --- | --- | --- |
| 第一批 | P0 正确性与安全边界（并发阻塞、请求上限、reasoning 外发、会话状态机、纠正历史、版本固定） | 完成 |
| 第二批 | 在线稳定性与性能（取消回收、限流器、缓存容量、配置校验、心跳/超时、SSE 解析、懒加载） | 完成 |
| 第三批 | 数据与可复现交付（版本固定、元数据一致性守卫、制品指纹、依赖分层） | 部分完成（重新生成活跃版本示例清单需要一次真实模型实测，见第五节） |
| 第四批 | 页面体验与文档收口 | 代码完成；文档按 current/history 口径修订 |

另外在浏览器端验证过程中发现了 **1 个审计报告未列出的前端 P0 缺陷**（详情见第三节），
已一并修复——它正是"前端永久卡死"的真实根因。

## 二、代码改动明细

### 1. 并发与阻塞（P0-1、P1-2）

- `server/sse.py` 新增 `run_in_thread()`，把握不到事件循环的同步调用全部下沉到线程池：
  F02 理解（含词典匹配与 LLM 兜底）、F03 图谱检索、F04 文本检索（含查询侧 embedding）。
- 不用 `asyncio.to_thread`（3.9+），改用 `run_in_executor`，保持 3.8 兼容口径。
- 守护用例：`test_slow_sync_search_does_not_block_event_loop`（0.3 s 同步阻塞期间事件循环仍推进）。

### 2. 请求边界（P0-2）

- `contracts/request.py`：`QueryRequest.from_dict(payload, settings=None)` 做类型 + 长度 + 数量校验，
  失败抛 `RequestValidationError`；新增 `_Limits` 从 Settings 取上限。
- `server/api.py`：先按 `Content-Length` 快速拒绝、再按流累计校验（两道都要，单靠前者可被分块传输绕过）；
  4xx 返回 JSON（`payload_too_large` 413 / `invalid_request` 400 / `rate_limited` 429），
  在调用检索与模型之前就拒绝。
- 默认上限：请求体 64 KiB、问题 500 字、session_id 128 字、历史 40 条（单条 4000 字）、
  纠正 20 条、筛选每维 20 项。全部可用环境变量覆盖。

### 3. 原始推理不外发（P0-3）

- `server/sse.py`：默认不推送 `thinking` 事件，只统计首思考时延与增量条数，经 `done` 的
  `first_thinking_ms` / `thinking_frames` 对外可见（不含内容）；`EXPOSE_THINKING=true` 才外发。
- 与 `docs/features/06-grounded-answer.md` 的过滤要求对齐（该文档同步改写）。

### 4. 前端会话状态机（P0-4 / P0-5 / P0-6）

- `frontend/src/stores/session.ts` 重写：
  - 每轮回答只有一个终态 `turnStatus`（connecting/streaming/completed/refused/degraded/cancelled/failed/interrupted），
    `streaming/finished/cancelled` 由它派生，不再互相矛盾；
  - 只有 completed/refused/degraded 进入多轮历史；被纠正或重试取代的轮次（`supersededBy`）排除；
  - 纠正/重试产生的新回答**进入历史**（旧实现因缺少配对 user 消息而被忽略，P0-4）；
  - 流自然结束但无 `done` → interrupted（半截回答不再写进历史，也不再永久转圈）；
  - 刷新恢复时把瞬态状态迁移为 interrupted，并把持久化改为带 `schemaVersion` + 裁剪 + 配额降级重试。
- `frontend/src/api/sse.ts` 重写解析器：按行累积、空行分发，兼容 CRLF、多行 data、注释心跳行与
  EOF 残帧；4xx 时读取后端 JSON 错误信息。
  **超时说明**：本次只落地了空闲与整体两档；连接超时（connectTimeoutMs）当时未接入 fetch 阶段，
  已在后续整改中补齐（三档独立计时 + 结果细分）。
- 新增"重试本轮"入口（沿用原问题、原筛选、原纠正项）。

### 5. 数据版本与元数据（P0-7、P0-9）

- `RAG_ACTIVE_VERSION` + `RAG_REQUIRE_ACTIVE_VERSION`：显式固定的版本缺失即启动失败。
  **当时为部分完成**：强制检查写在扫描分支之后，未指定版本时不可达；已在后续整改中前移到扫描之前。
- `scripts/run_server.py --version` 现在真正生效（写入环境变量后再由 lifespan 读取）。
- `lib/release_info.py`：快照/索引 manifest 与向量 ids 的 SHA-256、Git commit；
  `/api/health` 返回 `version`、`index_version`、`git_commit`、制品哈希、缓存与限流统计。
- `/api/demo/examples` 校验清单版本与运行时版本一致，不一致返回 503 + 可执行的重新生成命令
  （当前 20260915_v1 的清单仍是 20260904_v2 的产物，接口因此诚实地报错而不是展示旧时延）。
- `scripts/gen_demo_examples.py`：取消硬编码版本与 run，缺省读 `runs/latest.txt`，
  校验 run 的 `meta.version`，无候选时不写文件。

### 6. 在线稳定性（P1-1、P1-3、P1-4、P1-5、P1-6、P1-8、P1-9、P1-12、P1-13）

- 流取消：`run_query` 的 `finally` 取消并 await 生成任务；API 层心跳包装器关闭时回收内层生成。
- 限流器：加锁保证原子、key 表有界（默认 4096，超出淘汰最久未活跃）、XFF 默认不信任（可配可信代理）。
- 回答缓存：容量上限 + 过期清扫 + 最旧淘汰，`/api/health` 暴露 entries/evictions。
- 流式重试：正文一旦流出就不再重试、不切备用模型，以 `interrupted` 收敛并保留部分正文。
- 配置校验：`Settings.validate()` 对非法数值/未知枚举/越界长度 fail fast。
- SSE：`: ping` 心跳（默认 15 s）、整体上限（默认 300 s，超时以 error + done(failed) 收流）、
  反缓冲响应头（`no-transform` + `X-Accel-Buffering: no`）。
- 生命周期：lifespan 关闭时释放 AsyncOpenAI / 同步 OpenAI 客户端。
  **当时为部分完成**：`Runtime.shutdown()` 还是同步方法，在运行中的事件循环里 `asyncio.run()`
  必然抛 RuntimeError 且被吞掉，客户端实际没关；已在后续整改中改为 `async def shutdown()` 并 await。

### 7. 前端性能与交互（P1-10、P1-11、P1-15、P1-16、P1-17、P1-19、P1-20）

- 引用导航走 store（`requestCitation`），移动端抽屉未挂载时不再丢事件；面板 tab 也由 store 驱动。
- 纠正菜单只列同一 mention 的候选，不再跨 mention/跨类型混入。
- ECharts 与中国地图 JSON 改为动态 import + `echarts/core` 按需注册；图谱/地点 tab 打开时才下载。
- Markdown 渲染节流 80 ms；`[n]` 只有真实存在于 citations 时才渲染为引用按钮。
- 面板空状态区分"未提问/进行中/失败/完成但无数据/筛选后为空"；失败轮提供重试。

## 三、浏览器验证中新发现并修复的前端 P0

**现象**：浏览器里提问后界面停在"正在会话开始"，服务端 200 且已正常返回事件流。

**根因（两个，都会独立导致界面卡死）**：

1. `messages.value.push(assistant)` 存进数组的是**原始对象**，而事件守卫用
   `assistant !== active.value`（后者是 Vue 响应式代理）比较对象引用 → 两者恒不相等 →
   **所有 SSE 事件被静默丢弃**；即便放行，直接修改原始对象的属性也不会触发任何重渲染。
   实测：通过代理写入同一字段界面立刻更新，用原始对象写入则毫无反应。
   `git show HEAD:frontend/src/stores/session.ts` 确认该写法在整改前就存在。
2. `MarkdownContent.vue` 的正文装饰（引用按钮、实体高亮）挂在 `requestAnimationFrame` 上，
   后台标签页里 rAF 被浏览器暂停 → 引用按钮永不出现。

**修复**：消息对象统一用 `reactive()` 包装后再入数组（新增 `asReactiveMessage`），
事件守卫改为按 `id` 比较；装饰改用 Vue 的 `nextTick`（微任务，不依赖页面可见性）并加序列号防重入。

**验证**：浏览器实测回答逐字出现、9 个正文引用按钮与 6 处实体高亮渲染正常、
点击引用自动切到"引用证据"并展开对应行、失败轮"重试本轮"复用原问题成功重答、
多轮历史排除了中断轮并保留了重试后的回答。

## 四、验证证据

- `pytest tests -q`：**184 passed**（新增 `tests/test_online_guards.py` 32 条，不依赖 data/ 快照）。
- `npm run build`：入口 chunk 48.2 kB（原 608 kB）、vendor 171.9 kB、CSS 16.7 kB；
  echarts（1015 kB）与地图（581 kB）改为按需 chunk，不再被 index.html 预加载。
- 真实服务（离线模型模式，端口 8124）：
  - `/api/health` 返回版本固定、制品哈希、Git commit、缓存与限流统计；
  - 完整 SSE 链路 session_start → … → citations → panel → done，`finish_reason=normal`，
    无 thinking 事件；同题第二次请求命中缓存（`cache_hit=true`）；
  - 70000 字节请求体 → 413、非法 JSON → 400、501 字问题 → 400、缺 session_id → 400；
  - 连续请求超过配额 → 429，60 秒窗口过后恢复 200；
  - 同源托管返回新构建产物。
- 说明：echarts chunk 仍是 1 MB 级——`echarts/charts` 桶文件在包内被标记为有副作用，
  公开 API 下无法进一步 tree-shaking；首屏收益来自"不再预加载"。

## 五、未完成项（需要真实模型实测或人工决策）

1. **F08 示例清单未就绪（代码完成、制品未就绪）**：活跃版本 `20260915_v1` 目录下没有带评分的
   run（`v5_coords_v1` 是 `llm_used=false` 的离线 run），无法在不实测的前提下产出可信时延与评分，
   因此 `/api/demo/examples` 按版本一致性校验返回 **503**（宁可不显示，也不展示旧版本时延）。
   补齐需要执行
   `python scripts/gen_demo_examples.py --version 20260915_v1 --run <真实模型 run> --measure`。
2. **`questions.meta.json` 血缘**：原文件仍写 `version=20260904_v2`；后续整改已改为记录
   `derived_from_version` 与题库内容 SHA-256。
3. **完整制品清单**：本轮只有 snapshot/index manifest 与向量 ids 的哈希；后续整改已补
   `scripts/build_artifact_manifest.py`（递归记录 snapshot/index/Chroma/dist/题库/依赖文件的
   path+size+sha256，并支持 `verify` 与 `--require-clean` 发布门禁）。
4. **前端自动化测试**：本轮只加了 Python 用例；后续整改已补前端测试
   （`npm test` / `npm run test:e2e` / `npm run check:bundle`）。
2. **Chroma 孤儿 segment 核查**：清单只声明一个集合，但两个活跃索引目录下都存在两个约 38 MB 的
   UUID 数据段；删除前需用 Chroma API 确认，本轮未做删除。
3. **发布制品与 CI**：快照/索引/前端产物仍被 .gitignore 忽略，尚未建立带校验和的 release artifact
   与 CI 流水线（本轮只补齐了"版本可固定、指纹可查询"这两个前提）。
4. **requests/httpx 等依赖锁定**：`requirements.txt` 只声明下界，未生成锁文件。
