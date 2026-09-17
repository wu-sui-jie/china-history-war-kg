# 20260915 第四轮复核整改实施记录（发布阻断与正确性收口）

- 文档类型：整改实施记录（对应
  [20260915-round4-review-audit-and-next-optimization.md](20260915-round4-review-audit-and-next-optimization.md)）
- 状态（2026-09-16 复核后修订，逐项口径见文末表格）：**主体完成、发布收口中**。
  代码与测试已落地；`20260915_v1` 的真实 demo 制品、Python 锁文件与 clean commit 绑定
  当时未完成，已在后续工作单
  [20260916-round4-review-remediation-work-order.md](20260916-round4-review-remediation-work-order.md)
  中继续处理（除真实模型 run 外均已补齐）
- 整改日期：2026-09-15
- 验证结果（同一次运行）：
  - `pytest tests -q` → **225 passed**（整改前 188；新增 37 条守护用例）
  - `cd frontend && npm test` → **24 passed**（Node 内置测试运行器）
  - `npm run typecheck` / `npm run build` / `npm run check:bundle` → 通过
    （入口 gzip 19.9 kB、vendor 75.9 kB、首屏合计 95.8 kB，门禁上限 190 kB）

## 一、发布阻断（P0）

| 项 | 问题 | 修复 |
| --- | --- | --- |
| P0-1 | `RAG_REQUIRE_ACTIVE_VERSION` 检查写在扫描分支之后，未指定版本时不可达 | 强制检查前移到扫描之前（`server/runtime.py`）：生产模式下不存在"自动选最新目录"的路径 |
| P0-2 | 整体 deadline 用心跳当等待上限，心跳为 0 时永久等待、心跳大于剩余时间时晚一个周期 | 改用 `time.monotonic()` 计算剩余时间，每次等待取 `min(心跳, 剩余)`；`remaining <= 0` 立即收流；超时按"是否已送出正文"区分 `interrupted` / `failed`，错误码改为专门的 `timeout` |
| P0-3 | `Runtime.shutdown()` 是同步方法，在运行中的事件循环里 `asyncio.run()` 必然抛错且被吞，异步客户端从未真正关闭 | 改为 `async def shutdown()` 并由 lifespan `await`；单个资源关闭失败只记 warning，不中断其余资源 |
| P0-4 | health 只报告 HEAD，脏工作区下无法定位源码 | health 新增 `source_dirty` / `release_id` / `config_fingerprint` / `artifact_manifest_sha256` / `version_selection` / `demo_ready`；新增 `scripts/build_artifact_manifest.py`（含 `verify` 与 `--require-clean` 发布门禁） |

## 二、正确性收口（P1）

1. **缓存键覆盖纠正语义与完整历史**（P1-1）：`server/generate/cache.py` 改为规范化结构
   （问题 / 文本模式 / 筛选 / 朝代偏置 / 实体签名 / 纠正指令 / 版本 / 模型）后整体 SHA-256，
   历史不再截尾；实体签名含 `entity_id` / 标准名 / 类型 / 朝代。`server/sse.py` 把纠正后实体、
   纠正指令与朝代偏置一并传入。
2. **纠正指令动作语义校验**（P1-2）：`add` 必须有 `name` + `entity_type`，`replace` 必须有
   `original` + `replacement`，`remove` 必须有 `original`；`action` 非字符串时给 400 而不是
   未捕获的 TypeError（500）；与动作无关的字段被规范化移除，避免静默 no-op。
3. **同名实体按 `entity_id` 纠正**（P1-3）：纠正契约新增 `entity_id`；后端优先按 ID 匹配、
   名称仅作兼容降级；前端候选下拉的 key/value 改为 `entity_id`（无 ID 时用
   "标准名\|朝代\|类型"），展示文案带朝代与类型，同名不同朝代的选项不再重复。
4. **限流 key 淘汰不再重置配额**（P1-4）：表满时只清理"窗口内已无命中"的 key，
   仍满则拒绝新来源（`rejected_new_keys` 计数），不再淘汰活跃 key。
5. **同步工作池有界**（P1-5）：新增 `SyncWorkPool`（独立 `ThreadPoolExecutor` + 排队上限 +
   in_flight/rejected 统计），排队满时以 `server_busy` 收尾；lifespan 退出时关闭；
   `/api/health` 暴露 `sync_pool`；配置校验要求外部调用超时预算小于 SSE 总上限。
6. **demo 清单严格校验**（P1-6）：`version` 必须存在且严格等于运行时版本，
   另校验 `source_run`、`measurement_mode`、`examples` 结构与每条示例的必填/实测字段。
7. **配置 fail-fast**（P1-7）：去掉 `EMBEDDING_BATCH_SIZE` 与 `QUERY_FUSION_LIMIT` 的静默
   clamp，改为范围校验并报出变量名与合法区间。

## 三、前端状态与交互（P1-8 ~ P1-13）

- **连接超时接线**（P1-8）：三档独立计时（connect / idle / total），结果细分为
  `connect_timeout`、`idle_timeout`、`total_timeout`、`aborted`、`protocol_error`、
  `parse_error`、`network_error`、`http_error`、`eof`；判定发生在定时器真正触发时，
  手动取消不会被误判成超时。
- **error → done(normal) 不再变成 completed**（P1-9）：收到过 error 时 `normal` 终态降级为
  `interrupted`（有正文）或 `failed`（无正文）；`done` 之后的业务帧只计数不改状态；
  重复 done 幂等。
- **严格单流**（P1-10）：所有入口统一走 `beginTurn`；同步建消息、同步取消活动流，
  排队中的旧请求在启动前被判定为"已被取代"并收敛为 `cancelled`，任何时刻只有一条活动流。
- **流式持久化与 schema 迁移**（P1-11 / P2-11）：入队与终态立即落盘，流式正文按约 800ms
  节流保存；读取时校验 `schemaVersion`，更高版本或损坏数据隔离到
  `ragv5-session-quarantine` 并提示，不再盲目断言类型。
- **前端自动化测试**（P1-12）：新增 `frontend/tests/*.test.ts`（SSE 解析、超时分类、
  状态转移、单流、历史、持久化与迁移共 24 条），运行器 `scripts/run-tests.mjs`；
  另有 `test:e2e`（契约级端到端）与 `check:bundle`（首屏体积门禁）。
- **可访问性**（P1-13）：移动抽屉补 `role=dialog` / `aria-modal` / Escape / 焦点进入与陷阱 /
  关闭后焦点回退；面板 tabs 用标准 `tablist/tab/tabpanel` 并支持左右键；证据项改为原生
  button + `aria-expanded`；toast 用常驻 live region；输入框补 label；实体 chip 支持空格键；
  新增 `prefers-reduced-motion` 处理。

## 四、数据、发布与工程（P2）

- `questions.meta.json` 记录 `derived_from_version` 与题库内容 SHA-256（P2-2）。
- `scripts/build_artifact_manifest.py` 生成/校验完整制品清单（37 个文件、204.6 MB，
  覆盖 snapshot JSON、索引 manifest/FTS/chunks/ids/embeddings、Chroma 段、题库与示例、
  前端 dist、依赖声明）；health 返回清单哈希（P2-3）。
- 前端 chunk 加载失败给出可见降级与重试（P2-8）；面板 `cancelled` 空状态可达（P2-9）。
- 首屏体积门禁：`npm run check:bundle` 检查"index.html 不预加载 ECharts/地图"与
  entry/vendor/首屏 gzip 预算（P2-12）。
- CI：`.github/workflows/ci.yml`（Python 3.9/3.11 矩阵、前端 typecheck/test/build/bundle、
  手动触发的 release-gate 要求工作区干净）（P2-7）。
- 文档口径：`data-contract.md`、`features/06-grounded-answer.md`、`features/08-demo-mode.md`、
  `deploy.md`、`RAG_v1/README.md`、`RAG_v1/后续阶段规划.md`、`frontend/README.md` 与根 README
  的旧口径已按当前实现改写（P2 与第八节清单）。

## 五、验证证据

- 后端：`pytest tests -q` → 225 passed（新增 `tests/test_release_guards.py` 37 条，
  不依赖 data/ 快照）。
- 前端：`npm test` → 24 passed；`npm run verify`（typecheck + test + build + bundle）通过。
- 制品：`build_artifact_manifest.py build` → 37 条、`verify` → 与清单一致。
- 服务：见下一节"环境限制"说明的复现命令。

## 六、环境限制与未完成项

1. **真实模型 run 与 demo 重建**：`data/eval/20260915_v1/demo_examples.json`
   **文件存在但属于旧版本产物**（内部 `version=20260904_v2`、无 `measurement_mode`），
   接口按版本一致性校验返回 503；本环境不执行付费模型调用，无法产出同版本实测数据。
   重建命令见 `docs/features/08-demo-mode.md`。
2. **前端依赖无法安装**：本机访问 npm 源会挂起（`npm install` 超时），因此
   Vitest/Vue Test Utils/Playwright 未安装。为让测试真的能跑，改用 Node 内置测试运行器 +
   仓库已有的 esbuild（`frontend/scripts/run-tests.mjs`）；浏览器级用例（抽屉、焦点、
   mock 断流）待依赖可安装后按 `docs/deploy.md` 的清单补齐。
3. **依赖锁定**：本轮只分层了 `requirements.txt` 与 `requirements-dev.txt`；
   `frontend/package-lock.json` 已存在，**Python 侧锁文件当时未生成**（需 `pip-compile` 与网络），
   已在后续工作单中补齐。
4. **Chroma 段核验**：当时只做到"清单里逐文件记录哈希"；collection→segment 映射与
   孤儿判定已在后续工作单中完成（审计脚本 + 备份清理，结论见
   `data/release/chroma-segment-audit.json`）。
