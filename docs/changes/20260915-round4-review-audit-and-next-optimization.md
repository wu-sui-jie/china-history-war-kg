# 20260915 第四轮整改复核报告与后续优化工作单

> 审核对象：`docs/changes/20260915-round4-review-fix-summary.md` 及其对应代码、测试、构建产物和文档。  
> 审核日期：2026-09-15。  
> 审核范围：`RAG/`，严格排除 `RAG/new/`。  
> 审核方式：后端、前端、数据/文档/工程三路只读交叉复核。  
> 本轮约束：不修改代码，不修改既有文档，仅新增本报告。  
> 动态验证：Python 全量测试 **188 passed**；`tests/test_online_guards.py` **32 passed**；前端 `vue-tsc --noEmit --incremental false` 通过；`git diff --check` 未发现空白错误。

---

## 一、审核结论

第四轮整改的主体代码真实存在，且解决了上一轮多个关键问题，包括：同步检索阻塞事件循环、请求边界缺失、原始 reasoning 默认外发、前端永久转圈、纠正回答不进入历史、SSE 分帧兼容、移动端引用事件丢失和首屏大包预加载。

但是，当前不能认定为“第一批至第四批全部完成”或“可发布”。复核发现：

1. **4 个发布阻断问题**：生产强制版本检查不可达、SSE deadline 可失效、异步客户端未真正关闭、工作区和验证证据不能代表可发布版本；
2. **多项正确性问题**：缓存键遗漏纠正实体、纠正请求语义校验不足、同名实体仍无法正确消歧、限流淘汰可重置配额；
3. **前端状态与工程闭环未完成**：连接超时未接线、`error → done(normal)` 可污染历史、重试可启动第二条流、流式持久化不完整、没有前端自动化测试；
4. **数据与产品未收口**：`20260915_v1` 示例接口当前返回 503，题库元数据仍指向旧版本，制品哈希覆盖不足；
5. **文档声明仍有偏差**：测试总数写错，部分“已完成”声明早于实际实现，current/history 口径尚未完全统一。

### 当前风险统计

| 级别 | 数量 | 说明 |
| --- | ---: | --- |
| P0 / 发布阻断 | 4 | 不修复不应发布 |
| P1 / 高优先级 | 13 | 影响正确性、稳定性和核心交互 |
| P2 / 收口项 | 12 | 测试、可访问性、文档、制品与工程治理 |

---

## 二、第四轮整改中已确认有效的内容

### 2.1 后端

1. **同步工作已下沉线程池**
   - `server/sse.py:45-58、124-132、183-202`
   - F02 理解、F03 图谱检索、F04 文本/向量检索不再直接阻塞事件循环。
   - `test_slow_sync_search_does_not_block_event_loop` 能证明同步等待期间事件循环继续推进。

2. **请求体和主要字段已有边界**
   - `server/api.py:287-354`
   - `contracts/request.py:74-267`
   - 同时检查 `Content-Length` 和实际流累计字节；问题、session、历史、筛选和纠正项已有基础限制。

3. **原始 thinking 默认不外发**
   - `server/sse.py:283-307`
   - `config/settings.py:131-135`
   - 默认只输出首 thinking 时延和帧数统计；显式开启 `EXPOSE_THINKING` 才发送内容。

4. **流式正文输出后不再透明重试**
   - `server/generate/llm_client.py:79-157`
   - `server/generate/__init__.py:105-123`
   - 避免把失败尝试和重试尝试拼接成重复答案。

5. **生成任务有取消回收路径**
   - `server/sse.py:401-409`
   - `server/api.py:419-425`
   - `run_query` 和心跳包装器关闭时会取消或关闭内层生成。

6. **限流和缓存已完成基础加固**
   - `server/api.py:185-265`
   - `server/generate/cache.py:38-99`
   - 限流器已加锁、默认不信任 XFF；缓存已有容量、过期清理和淘汰统计。

7. **SSE 心跳和反缓冲头已存在**
   - `server/api.py:360-425`
   - 已包含 `: ping`、`Cache-Control: no-transform`、`X-Accel-Buffering: no`。

### 2.2 前端

1. **状态已收敛为单一 `turnStatus`**
   - `frontend/src/types/contract.ts:183-195`
   - `frontend/src/stores/session.ts:410-422`
   - connecting、streaming 和各终态不再完全依赖互相矛盾的多个布尔值。

2. **历史和 supersede 主体正确**
   - `frontend/src/stores/session.ts:275-293、672-685、717-733`
   - 只有 completed/refused/degraded 进入历史；被纠正或重试取代的轮次会排除；新回答携带原问题，可进入后续上下文。

3. **EOF 无 done 不再永久转圈**
   - `frontend/src/stores/session.ts:500-517`
   - 流自然结束后会收敛为 interrupted 或 failed。

4. **SSE 解析器主体整改有效**
   - `frontend/src/api/sse.ts:68-118`
   - 支持 LF/CRLF、多行 data、注释心跳、任意网络分块和 EOF 残帧。

5. **引用导航不再依赖易丢失的 window 事件**
   - `frontend/src/stores/session.ts:742-748`
   - `frontend/src/components/panel/EvidenceView.vue:65-74`
   - 移动面板未挂载时，引用定位请求仍可通过 store 保留。

6. **响应式对象问题已修复**
   - `frontend/src/stores/session.ts:197-206、424-445、487-491`
   - assistant 入队前使用 `reactive()`；事件守卫按 ID 比较，不再比较 raw 对象与 Vue proxy 引用。

7. **Markdown 后台标签页问题已修复**
   - `frontend/src/components/chat/MarkdownContent.vue:124-153`
   - DOM 装饰改为 `nextTick` 并增加合并更新；不再依赖后台标签页可能暂停的 `requestAnimationFrame`。

8. **ECharts 与地图已经按需加载**
   - `frontend/src/components/panel/SubGraphView.vue:18-29`
   - `frontend/src/components/panel/MapView.vue:17-39`
   - 当前 `dist/index.html` 没有预加载 ECharts 和地图 chunk，首屏拆包方向正确。

### 2.3 验证数字

当前实际结果为：

```text
Python 全量测试：188 passed
在线守护文件：32 passed
前端类型检查：通过
```

第四轮整改记录中的“184 passed”是算术和实跑结果均不成立的旧数字，应改为 **188 passed**。

---

## 三、P0：发布前必须修复的问题

## P0-1. `RAG_REQUIRE_ACTIVE_VERSION` 强制版本检查不可达

### 证据

- `server/runtime.py:97-114`

当显式版本为空时，代码先扫描并返回最新一致版本，后面的：

```python
if settings.require_active_version and not explicit:
```

无法执行。

### 影响

生产环境即使设置 `RAG_REQUIRE_ACTIVE_VERSION=true`，如果遗漏 `RAG_ACTIVE_VERSION`，仍会静默加载目录名最大的版本。灰度、回滚和复现实验均不可靠。

### 修改方案

1. 在扫描目录之前检查强制版本；
2. 只有开发模式允许自动扫描；
3. 生产模板同时配置：

```env
RAG_ACTIVE_VERSION=20260915_v1
RAG_REQUIRE_ACTIVE_VERSION=true
```

4. 部署命令显式传入：

```bash
python scripts/run_server.py --version 20260915_v1
```

### 必须新增的测试

- require=true、active version 为空：启动失败；
- require=true、版本不存在：启动失败；
- require=true、版本存在：成功；
- require=false、版本为空：允许自动扫描。

### 验收标准

生产模式下不存在任何自动选择“最新目录”的路径。

---

## P0-2. SSE 整体 deadline 不是严格截止时间

### 证据

- `server/api.py:381-418`

当前等待超时使用 heartbeat，而不是 deadline 剩余时间。heartbeat 为 0 时可能永久等待；heartbeat 大于 deadline 时可能延迟一个完整心跳周期。

### 修改方案

使用单调时钟，每次等待前计算：

```python
remaining = deadline_at - time.monotonic()
timeout = min(heartbeat, remaining) if heartbeat > 0 else remaining
```

当 `remaining <= 0` 时立即结束。

包装器还应记录是否已经发出正文：

- 已发正文后超时：`finish_reason=interrupted`；
- 尚未发正文：`finish_reason=failed`；
- 使用专门的 timeout 错误码，不使用笼统 `internal`。

### 必须新增的测试

- heartbeat=0，deadline 仍生效；
- heartbeat>deadline，按 deadline 结束；
- 上游永远不输出；
- 已有部分正文后超时返回 interrupted；
- 无正文时返回 failed。

### 验收标准

服务端 deadline 独立于 heartbeat，实际误差只允许为调度级毫秒误差。

---

## P0-3. 异步客户端生命周期关闭实际失效

### 证据

- `server/runtime.py:42-59`
- `server/api.py:61-66`

同步 `Runtime.shutdown()` 在 FastAPI 正在运行的事件循环中对 coroutine 调用 `asyncio.run()`，会触发 `RuntimeError`；异常被吞掉后，AsyncOpenAI 客户端没有真正关闭。

### 修改方案

1. 将 `Runtime.shutdown()` 改为 `async def shutdown()`；
2. lifespan 中执行 `await runtime.shutdown()`；
3. 同步客户端直接 `close()`；
4. 异步客户端统一 `await aclose()`；
5. 关闭异常记录日志，不应无条件吞掉。

### 必须新增的测试

使用同步 closer 和异步 closer 替身，断言：

- 每个 closer 只调用一次；
- 异步 closer 确实被 await；
- 某个资源关闭失败时其他资源仍会继续关闭；
- lifespan 退出没有未 await coroutine 警告。

### 验收标准

热重载、测试重复启动和正常停机均不遗留 HTTP 连接池或 coroutine 警告。

---

## P0-4. 当前工作区和验证证据不能代表发布版本

### 现状

RAG 内层仓库存在大量未提交修改和未跟踪核心文件。`/api/health` 只报告 HEAD，不报告 dirty 状态。因此显示的 commit 不能唯一标识当前运行源码。

第四轮文档中的浏览器和接口验证也没有形成与同一 clean commit 绑定的完整证据包。

### 修改方案

1. 完成 P0/P1 修改和测试后统一提交；
2. health 增加：
   - `source_dirty`；
   - `release_id`；
   - `artifact_manifest_sha256`；
   - `config_fingerprint`；
   - `demo_ready`；
3. release 构建时若 `git status --porcelain` 非空则失败；
4. 在同一 clean commit 下重跑测试、构建、服务冒烟和浏览器验证。

### 验收标准

任何一份验收证据都可反向定位到唯一源码 commit、配置指纹和制品清单。

---

## 四、P1：正确性与稳定性问题

## P1-1. 缓存键遗漏实体纠正语义，历史签名仍可能碰撞

### 证据

- `server/sse.py:144-151`
- `server/generate/cache.py:24-35`

某些 `add` 纠正不会改变 rewritten question，但会改变实体、图谱和证据。当前缓存键可能仍与未纠正请求相同。

历史签名仍只取 JSON 尾部 400 字符，两份不同历史只要尾部相同就会碰撞。

### 修改方案

缓存键应由规范化结构构成：

```text
question
text_mode
filters
dynasty_bias
normalized_history_sha256
normalized_entities_sha256
```

实体签名至少包含：

- `entity_id`；
- `standard_name`；
- `entity_type`；
- dynasty 或其他消歧字段。

历史应先按允许轮数裁剪并规范化，再对完整 JSON 计算 SHA-256，不得先截尾。

### 必须新增的测试

- 同题无纠正与 add 的键不同；
- add/remove/replace 三种纠正键均不同；
- 历史尾部相同、前文不同的键不同；
- 字段顺序不同但语义相同的规范化结构键相同。

---

## P1-2. corrected entities 只有外形限制，没有动作语义校验

### 证据

- `contracts/request.py:235-259`

以下请求可能通过校验并成为静默 no-op：

```json
{"action": "add"}
{"action": "replace"}
{"action": "remove"}
```

当 action 是数组或对象时，`CorrectionAction(...)` 还可能抛出未转换的 `TypeError`，最终形成 500。

### 修改方案

- add：要求 `name` 和 `entity_type`；
- replace：要求 `original` 和 `replacement`；
- remove：要求 `original`；
- action 必须先验证为字符串；
- 所有嵌套类型异常统一转换为 `RequestValidationError`；
- 对无关字段选择拒绝或规范化移除，并在契约中固定规则。

### 验收标准

所有非法纠正请求稳定返回 400，不产生 500，也不允许静默 no-op。

---

## P1-3. 同名、不同朝代候选无法正确选择

### 证据

- `frontend/src/components/chat/MessageBubble.vue:94-99、266-281`
- `contracts/request.py:124-130`
- `server/query/understand.py:347-360`

前端使用 `standard_name` 作为 key/value，同标准名、不同 `entity_id` 或朝代的候选会发生重复 key，并总是选回第一项。后端纠正契约也没有稳定传递 `entity_id`。

### 修改方案

1. 前后端 correction 契约增加 `entity_id`；
2. 候选 option key/value 优先使用 `entity_id`；
3. 旧数据无 ID 时使用 `standard_name|dynasty|entity_type`；
4. 后端优先按 ID 应用纠正，名称仅作为兼容降级；
5. UI 展示标准名、朝代和类型，避免用户看见两个完全相同的选项。

### 必须新增的测试

使用同名不同朝代的真实实体，验证前端选择、请求 payload、后端解析和最终检索全部落到指定 ID。

---

## P1-4. 限流 key 淘汰可能重置有效配额

### 证据

- `server/api.py:201-218`

当前 key 表满后淘汰最久未活跃项，即使该 key 的命中仍处于限流窗口。重新使用被淘汰 key 时配额会从零开始。

### 修改方案

- 先清理窗口已过期、hits 为空的 key；
- 不淘汰窗口内仍有命中的 key；
- 表满且没有安全可清理项时拒绝创建新 key，或使用全局保护；
- 公网和多实例部署使用 Redis token bucket 或滑动窗口。

### 必须新增的测试

填满 key 表后重新访问旧 key，确认其窗口内配额不会被重置。

---

## P1-5. 线程池任务无法随请求取消，缺少容量隔离

### 证据

- `server/sse.py:45-58、124-132、188-202`

`run_in_executor` 能避免阻塞事件循环，但取消 Future 不会停止已经运行的同步函数。断连后 embedding、Chroma 或 SQLite 工作仍可能运行到自身超时。

### 修改方案

- 为 RAG 同步工作建立独立、有界 `ThreadPoolExecutor`；
- 增加 semaphore 和排队上限；
- 外部网络超时必须小于 SSE 总 deadline；
- 能改为原生异步的 HTTP 调用优先异步化；
- shutdown 时显式关闭 executor；
- health 暴露 active、queued、rejected 数量。

### 验收标准

大量断连请求不能无限占满默认 executor，也不能拖累服务中的其他线程池任务。

---

## P1-6. demo 清单缺少 version 时会绕过版本校验

### 证据

- `server/api.py:157-170`

当前逻辑只有 `file_version` 为真且不等于 runtime 时才拒绝。version 缺失或为空会绕过检查。

### 修改方案

要求：

```python
file_version == runtime.version
```

缺失、空值或类型错误都返回 503。同时校验：

- examples 为列表；
- source_run 存在；
- measurement_mode；
- 每条示例的必要字段和测量字段。

---

## P1-7. 配置仍存在静默修正，不完全符合 fail-fast

### 证据

- `config/settings.py:311-315、341-343`

例如 `EMBEDDING_BATCH_SIZE=0` 被改为 1，`QUERY_FUSION_LIMIT=1` 被改为 4。

### 修改方案

推荐统一 fail-fast：配置解析保留原值，`validate()` 明确检查范围并给出变量名和合法区间。若必须 clamp，则启动日志和 health 必须同时展示 requested/effective 值。

---

## 五、P1：前端状态与交互问题

## P1-8. `connectTimeoutMs` 已声明但没有接入 fetch 阶段

### 证据

- `frontend/src/api/sse.ts:18-23、132-169`

代码读取了 connect timeout，但等待响应头时没有使用独立连接计时器。当前实际有效的是 idle 和 total 两档。

### 修改方案

1. 请求发出后启动 connect timer；
2. 收到响应头立即清除 connect timer；
3. 开始读取 body 后启用 idle watchdog；
4. total timer 从请求开始始终独立存在；
5. 结构化区分：
   - `connect_timeout`；
   - `idle_timeout`；
   - `total_timeout`。

### 验收标准

后端不返回响应头时，连接在配置的 connect timeout 内结束，而不是等待 idle 或 total timeout。

---

## P1-9. `error → done(normal)` 可形成带错误的 completed

### 证据

- `frontend/src/stores/session.ts:123-140、564-580`

error 事件只设置错误文本；后续 normal done 会把状态改为 completed。最终可能同时显示错误和完成，并把该轮加入历史。

### 修改方案

建立显式状态转移表：

- 一旦收到 error，normal done 不得覆盖为 completed；
- 已有正文时转 interrupted；
- 无正文时转 failed；
- 只有明确的 refused/degraded 可进入对应可用终态；
- done 后收到的业务帧应忽略或记录协议错误；
- 重复 done 保持幂等。

### 必须新增的测试

- error → done(normal)；
- error → done(cancelled)；
- answer → error → done；
- done → answer；
- 重复 done；
- 乱序事件。

---

## P1-10. `retryTurn()` 可在已有请求运行时启动第二条流

### 证据

- `frontend/src/stores/session.ts:717-733`
- `frontend/src/components/chat/MessageBubble.vue:351-357`

重试入口未先取消当前流，新请求会覆盖全局 active 和 AbortController，旧请求继续占用后端资源，但事件被 ID 守卫丢弃。

### 修改方案

推荐保持产品层“严格单流”：

- 所有 send/retry/correct 统一经过 `startTurn()`；
- `startTurn()` 先 cancel 并 await 前一流；
- 活动请求期间禁用其他重试和纠正按钮；
- stop 始终作用于唯一活动请求。

### 验收标准

任何时刻最多存在一个前端活动流和一个可被 stop 取消的 AbortController。

---

## P1-11. 普通提问在流式阶段没有及时持久化

### 证据

- `frontend/src/stores/session.ts:447-465、519-586`

普通提问添加消息后直接启动请求，launch 前没有 persist；流式正文通常只在 done 或 launch 返回后保存。刷新时可能丢失当前 user 消息和半截回答。

### 修改方案

- user/assistant 入队后立即持久化；
- answer 增量按 500～1000ms 节流保存；
- done/error/abort 立即保存；
- 恢复后保留半截正文并转 interrupted；
- 真正读取并校验 `schemaVersion`；
- 未识别版本执行显式迁移或隔离，不能盲目断言类型。

### 验收标准

任意生成阶段刷新，用户问题和已收到正文均不丢失，并以 interrupted 恢复且不进入历史。

---

## P1-12. 前端关键状态机没有自动化测试

第四轮新增的 32 条守护用例均为 Python 测试，不能覆盖前端 SSE parser、Pinia 状态、Vue 响应性和移动交互。

### 建议测试体系

#### Vitest

- SSE 的 CRLF、多行 data、UTF-8 分块、EOF 残帧；
- connect/idle/total timeout；
- error/done 状态转移；
- supersede/history；
- 重试单流约束；
- 持久化迁移、裁剪和配额失败。

#### Vue Test Utils

- 重试按钮；
- 同名候选；
- 引用导航；
- 面板空状态；
- 动态 chunk 加载失败。

#### Playwright

- 服务断流；
- 刷新恢复；
- 移动端抽屉；
- 后台标签页 Markdown；
- 键盘和焦点操作。

### 验收标准

前端 `package.json` 至少提供 `test` 和 `test:e2e`，并纳入 CI。

---

## P1-13. 移动端抽屉和核心交互仍不满足基本可访问性

### 主要位置

- `frontend/src/App.vue:60-83`
- `frontend/src/components/panel/EvidenceView.vue:78-99`
- `frontend/src/components/panel/PanelPane.vue:87-107`
- `frontend/src/components/ui/ToastView.vue:6-11`
- `frontend/src/components/chat/ChatInput.vue:40-49`

### 修改方案

- 抽屉增加 `role=dialog`、`aria-modal`、标题关联；
- 提供抽屉内部关闭按钮、Escape、焦点进入、焦点陷阱和关闭后焦点回退；
- evidence 展开项使用 button 或补齐键盘与 `aria-expanded`；
- tabs 使用标准 `tablist/tab/tabpanel`；
- toast、错误和生成状态使用适当的 `aria-live`；
- 输入框增加 label；
- 实体 chip 支持 Space；
- 支持 `prefers-reduced-motion`。

---

## 六、P2：数据、发布与文档收口

## P2-1. `20260915_v1` 示例功能当前不可用

当前 `demo_examples.json` 仍属于 `20260904_v2`，接口按新规则返回 503。该行为避免展示假数据，但意味着 F08 欢迎页示例按钮当前不可用。

### 修改步骤

1. 在 clean commit 下固定版本和模型配置；
2. 运行真实 LLM 评测，run meta 记录：
   - version；
   - model；
   - endpoint 类型；
   - 参数；
   - config fingerprint；
   - `generation_backend=real_llm`；
3. 执行：

```bash
python scripts/gen_demo_examples.py \
  --version 20260915_v1 \
  --run <真实模型run> \
  --measure
```

4. `--measure` 在 LLM 不可用时默认失败；若允许离线测量，必须显式 `--allow-offline-measure`；
5. 产物写入 `measurement_mode=real_llm|offline`；
6. 启动服务并确认 `/api/demo/examples` 返回 200。

### 验收标准

- demo version 等于 runtime version；
- source run 版本一致；
- 九条或重新筛选后的 N 条示例全部可用；
- 冒烟不再因 demo 503 失败。

---

## P2-2. `questions.meta.json` 仍为旧版本

若题库内容复用旧版本，不应假装是新生成，也不应继续保留错误 version。建议记录：

```json
{
  "version": "20260915_v1",
  "derived_from_version": "20260904_v2",
  "source_questions_sha256": "...",
  "generated_at": "..."
}
```

验收时检查目录版本、meta version、source hash 和评测 run version 一致。

---

## P2-3. release hash 不能证明完整制品内容

现有哈希只覆盖 snapshot manifest、index manifest 和 vector ids，不能证明实际服务读取的 JSON、FTS、chunks、embeddings、Chroma segment 和前端 dist 未变化。

### 修改方案

生成规范化 `artifact-manifest.json`，递归记录所有发布必需文件：

```text
relative_path
size
sha256
artifact_type
source_version
```

至少覆盖：

- snapshot 全部运行时 JSON；
- index manifest、chunks、FTS；
- ids、embeddings、Chroma 数据；
- demo/questions；
- frontend dist；
- Python 和前端锁文件。

health 返回 `artifact_manifest_sha256`，启动时可根据部署模式选择快速校验或完整校验。

---

## P2-4. 数据血缘需要闭合

统一 lineage 应记录：

- 上游数据库具体文件、大小和 SHA-256；
- 原始文本 hash 和授权状态；
- snapshot 构建命令及源码 commit；
- index 参数和工具版本；
- 向量复用来源及旧 embeddings hash；
- eval run 与 demo/questions 的来源；
- Python/Node/lockfile hash。

验收目标是形成：

```text
source → snapshot → chunks/index → vectors → eval → demo → frontend/release
```

任意一层可反向追溯。

---

## P2-5. Chroma segment 必须先映射再清理

两个 UUID 目录不等于一定存在孤儿。一个 collection 可能合法对应多个不同 scope 的 segment。

### 核验步骤

- 使用当前 Chroma API 或元数据库列出 collection；
- 建立 collection ID → segment ID → scope/type 映射；
- 对比 collection count 与 `ids.json` 的 9544；
- 随机 ID 执行 get/query；
- 只把无任何引用的 segment 定义为孤儿；
- 删除前生成备份和审计 JSON；
- 删除后重跑 checksum、查询抽样和 smoke。

---

## P2-6. Python 依赖没有锁定

建议使用 `pip-tools`：

```bash
pip-compile --generate-hashes requirements.txt -o requirements.lock
pip-compile --generate-hashes requirements-dev.txt -o requirements-dev.lock
pip-sync requirements-dev.lock
```

前端发布统一使用：

```bash
npm ci
npm run typecheck
npm run test
npm run build
```

不要在文档中称 `chromadb>=1.3` 为“已锁定版本”。

---

## P2-7. CI 与 release artifact 尚未建立

### CI 最低要求

1. Python 支持版本矩阵；
2. `pytest tests -q`，并记录 collected cases；
3. 前端 `npm ci/typecheck/test/build`；
4. Playwright 核心路径；
5. Markdown 链接和 current 口径检查；
6. demo/version/manifest lint；
7. bundle budget；
8. secret scan；
9. dirty worktree 禁止 release。

### Release 产物

- 源码 tag/commit；
- Python lock；
- package-lock；
- frontend dist；
- snapshot/index；
- eval/demo；
- artifact manifest；
- SHA256SUMS；
- lineage；
- smoke 报告；
- SBOM。

---

## P2-8. 动态 chunk 加载失败没有 UI 降级

### 位置

- `frontend/src/components/panel/MapView.vue:48-52、102-110`
- `frontend/src/components/panel/SubGraphView.vue:42-48、94-100`

动态 import 或 ECharts 初始化失败可能形成未处理 Promise rejection。

### 修改方案

为图谱和地图组件增加：

- loading；
- load error；
- retry；
- 卸载后的异步结果忽略；
- 网络离线文案。

---

## P2-9. 面板 cancelled 空状态分支不可达

### 位置

- `frontend/src/components/panel/PanelPane.vue:23-29、44-57`

cancelled 被归入 ready，但“本轮已取消”判断位于 failed 分支中，因此实际不会显示。

建议让 phase 明确包含 cancelled，或由统一状态到空状态文案的映射表生成。

---

## P2-10. `parse_error` 返回语义与实际行为不一致

### 位置

- `frontend/src/api/sse.ts:180-215`

malformed JSON 只触发 onError，outcome 仍可能保留 eof；部分网络读取错误反而被归入 parse_error。

建议拆分：

```text
protocol_error
parse_error
network_error
connect_timeout
idle_timeout
total_timeout
aborted
eof
done
```

并对每种结果建立测试。

---

## P2-11. 持久化写了 schemaVersion，但读取时未校验

### 位置

- `frontend/src/stores/session.ts:223-241、315-322`

当前主要依靠 storage key 中的 v2 隔离，不是真正的 schema 迁移。

建议读取时明确：

- 当前版本：正常解析；
- 旧版本：按迁移函数升级；
- 新于当前版本：隔离并提示；
- 数据结构非法：备份原值后安全清空。

---

## P2-12. 拆包只有告警，没有体积门禁

### 位置

- `frontend/vite.config.ts:23-49`

`chunkSizeWarningLimit` 只输出警告，不能阻止入口重新静态引入 ECharts。

建议 CI 额外检查：

- index.html 不预加载 ECharts/地图；
- entry gzip/brotli 上限；
- vendor 上限；
- 首屏总 JS 上限；
- 超限时 CI 失败。

---

## 七、第四轮整改记录需要修正的内容

审核对象：`docs/changes/20260915-round4-review-fix-summary.md`。

建议同步修正：

1. `184 passed` 改为 **188 passed**；
2. “第一批～第四批已实施”改为“主体已实施，复核发现发布阻断与收口项”；
3. “连接/空闲/整体三档超时”在 connect timeout 接线前改为“空闲/整体已实现，连接超时待接线”；
4. “生命周期已释放”在 async shutdown 修复前标为部分完成；
5. “版本固定”在强制版本不可达问题修复前标为部分完成；
6. F08 明确写为“代码完成，但当前活跃版本示例制品未就绪，接口返回 503”；
7. 构建数字应引用同一次构建的原始 stdout 和产物 hash，不手工转录近似值；
8. 浏览器/服务验证应绑定 clean commit、启动命令、环境摘要、health JSON、SSE 抓包和 smoke JSON；
9. “新增 32 条守护用例”注明是 **Python pytest cases**，不包含前端测试；
10. 未完成项补充 `questions.meta.json`、前端自动化测试、完整 artifact manifest 和 current 文档冲突。

---

## 八、仍需同步的 current/history 文档

本轮抽查仍发现：

- `docs/data-contract.md:374-376、406-407`：thinking 实际发射口径未完整更新；
- `docs/features/06-grounded-answer.md:18、23、33-34、121-123`：仍称页面展示或保留思考过程；
- `docs/features/08-demo-mode.md:44-46`：仍称当前九条实测示例可用；
- `docs/deploy.md:135`：仍称 XFF 优先，但代码默认不信任；
- `docs/deploy.md:136`：称 Chroma 已锁定，实际仅有下界；
- `docs/RAG_v1/README.md`：仍有“RAGv5 实施中、提交待确认”；
- `docs/RAG_v1/后续阶段规划.md`：仍有“待开工、Git 固化未做”；
- `frontend/README.md:56`：仍写旧 localStorage key `ragv3-session-v1`，当前实现为 `ragv5-session-v2`；
- 根 README 和第四轮总结中的测试数需要统一为当前实际 collected cases。

### 修订原则

- current 文档只描述当前可运行事实；
- history 文档顶部标注适用日期和 commit；
- 历史正文可保留，但索引和状态字段不得继续以现在时误导；
- 测试数、版本、示例数和依赖状态只保留一个当前事实源。

---

## 九、推荐实施顺序

## 阶段 1：发布阻断修复

1. 修复强制版本检查；
2. 修复严格 deadline；
3. 将 Runtime shutdown 改为异步并正确 await；
4. 修复 corrected entities 类型和动作语义；
5. 修正测试数字；
6. 补齐对应后端测试。

### 阶段 1 验收

```text
- require=true 且未指定版本时启动失败
- heartbeat=0 时 deadline 仍生效
- 异步客户端关闭测试通过
- 所有非法 correction 返回 400
- Python 全量测试通过且报告数字一致
```

## 阶段 2：正确性收口

1. 缓存键加入完整历史和实体签名；
2. 限流 key churn 修复；
3. 前端 error/done 状态转移；
4. 严格单流重试；
5. 同名实体端到端携带 entity_id；
6. demo 缺少 version 时拒绝。

### 阶段 2 验收

```text
- 纠正请求不会命中旧答案缓存
- 不同历史不会因尾部相同碰撞
- error 轮永不进入历史
- 任意时刻最多一个前端活动流
- 同名不同朝代实体可选择指定 ID
```

## 阶段 3：前端测试与体验

1. 真正接入 connect timeout；
2. 流式节流持久化和 schema 迁移；
3. 建立 Vitest、Vue Test Utils、Playwright；
4. 动态 chunk 错误降级；
5. 修复 cancelled 空状态和解析错误分类；
6. 完成移动抽屉与核心操作可访问性；
7. 增加 bundle budget。

### 阶段 3 验收

```text
- 前端单元、组件和端到端测试全部通过
- 刷新后保留部分正文并恢复为 interrupted
- 三类超时均有独立测试
- 键盘可完成提问、证据查看和关闭抽屉
- 首屏不预加载 ECharts/地图
```

## 阶段 4：数据与发布

1. 在真实模型下生成 `20260915_v1` run；
2. 重建 demo，修复 questions meta；
3. 生成完整 artifact manifest 和 lineage；
4. 核验 Chroma segment；
5. 生成 Python 锁文件；
6. 建立 CI 和 release artifact；
7. clean commit 下重跑完整验收。

### 阶段 4 验收

```text
- /api/demo/examples 返回 200
- demo、questions、run 与 runtime 版本一致
- 任一运行时制品变化都能被 hash 校验发现
- 干净 clone 可用锁文件完成测试和构建
- release artifact 可在另一台机器校验和启动
```

## 阶段 5：文档和流程收口

1. 修正第四轮整改记录；
2. 清理 current 文档冲突；
3. 为 history 文档增加历史标记；
4. 统一测试数、版本、示例和依赖口径；
5. 将最终验证结果绑定 release commit。

---

## 十、建议新增测试清单

### 后端

- [ ] require active version 为空；
- [ ] heartbeat=0 的 deadline；
- [ ] heartbeat>deadline；
- [ ] 部分正文后 timeout；
- [ ] async closer 被 await；
- [ ] correction action 非字符串；
- [ ] add/replace/remove 必填字段；
- [ ] correction 进入缓存键；
- [ ] 完整历史进入缓存 hash；
- [ ] 限流 key churn；
- [ ] demo version 缺失；
- [ ] 有界线程池排队与拒绝。

### 前端

- [ ] CRLF、多行 data、UTF-8 分块、EOF 残帧；
- [ ] connect/idle/total timeout；
- [ ] error → done(normal)；
- [ ] 重复/乱序 done；
- [ ] 单流重试；
- [ ] supersede/history；
- [ ] 流式持久化与刷新恢复；
- [ ] schema migration；
- [ ] 同名不同朝代候选；
- [ ] 移动端引用与抽屉；
- [ ] 动态 import 失败；
- [ ] 键盘与读屏语义。

### 数据与发布

- [ ] 目录版本、manifest、questions、demo、run 一致；
- [ ] artifact manifest 完整性；
- [ ] dirty worktree 禁止 release；
- [ ] 前端入口不预加载 ECharts/地图；
- [ ] release artifact 解包与 checksum；
- [ ] clean clone 安装、测试、构建和启动。

---

## 十一、最终发布门禁

以下条件全部满足后，才建议标记 `release-ready`：

- [ ] Python 测试数与文档一致，当前基准为 188 collected/passed；
- [ ] 前端自动化测试建立并通过；
- [ ] 工作区 clean，所有核心新增文件已入库；
- [ ] 生产模式必须显式指定版本；
- [ ] heartbeat=0 时服务端 deadline 仍严格生效；
- [ ] AsyncOpenAI 等异步资源确实被 await 关闭；
- [ ] 缓存键覆盖纠正实体和完整历史；
- [ ] 同名实体可通过 entity_id 正确纠正；
- [ ] 限流 key 淘汰不能重置窗口配额；
- [ ] `20260915_v1` demo 接口返回 200；
- [ ] questions、demo、run 和 runtime 版本链一致；
- [ ] 完整制品哈希和数据血缘可验证；
- [ ] Python 锁文件、CI 和 release artifact 流程建立；
- [ ] current 文档与当前接口行为一致；
- [ ] 同一 clean commit 下归档测试、构建、服务、浏览器和冒烟证据。

---

## 十二、最终评价

第四轮整改不是失败整改。相反，它已经实质性解决了上一轮最突出的一批问题，特别是事件循环阻塞、前端永久卡死、SSE 兼容和首屏拆包。但整改范围很大，当前出现的主要问题是“完成声明领先于实际验收”和“新状态模型缺少前端自动化守护”。

后续不建议继续扩大新功能范围。应按本报告顺序：

1. 先修生产版本、deadline、异步关闭等发布阻断；
2. 再修缓存、纠正、状态转移和单流约束等正确性问题；
3. 随后建立前端测试；
4. 最后完成真实 demo、完整制品哈希、依赖锁定、CI 和文档收口。

完成这些工作后，项目才可以从“第四轮主体整改已落地”提升为“整改已验证、能够稳定发布和复现”。
