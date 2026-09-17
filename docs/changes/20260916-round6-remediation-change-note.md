# 20260916 第六轮复核整改修改说明（改动清单 + 参考依据）

## 〇、本次修改参考的文档（唯一依据）

**本次修改全部依据下面这一份复核文档执行，未引入该文档之外的整改范围：**

| 项 | 内容 |
| --- | --- |
| 被参考文档 | [`docs/changes/20260916-round6-review-of-round5-remediation.md`](20260916-round6-review-of-round5-remediation.md) |
| 完整路径 | `F:\python\python_space\china-war\RAG\docs\changes\20260916-round6-review-of-round5-remediation.md` |
| 文档性质 | 第六轮独立复核（审核方产出）：对第五轮**修改说明**的核验 + 全项目增量清查 |
| 审核日期 / 被审核状态 | 2026-09-16 / 分支 HEAD `2e06aea` + 工作区未提交改动 |
| 文档给出什么 | ① 对第五轮声称的复现结论（第二节）；② 2 条"没讲透的硬事实"（第三节）；③ **B1–B8 修复自身缺陷清单**（第四节，含每条的"下次改法"）；④ **Z1–Z5 / D1–D7 / G1 增量发现**（第五节）；⑤ 下一轮整改建议顺序（第六节） |
| 上游追溯链 | `20260916-round4-review-remediation-work-order.md` → 第五轮审核报告 → 第五轮修改说明（上轮被审核对象）→ 第六轮复核（本文依据）→ 本修改说明 |

本次改动逐条对应该文档的：

1. **第四节 B 类 8 项**（B1 SHA256SUMS 行尾、B2 drain 阻塞事件循环与 submit 竞态、
   B3 血缘判定缺口、B4 版本来源误标、B5 空转用例、B6 e2e 假绿、B7 打包放行口子、
   B8 空串通配与错误态 schema）——全部按文档给出的"下次改法"实施；
2. **第五节 G1**（锁文件写死阿里云镜像）——按"CI 改用官方 PyPI，镜像只留给本机"处理；
3. **第五节 Z2/Z3/Z5 与 D1–D6** 中可在本轮闭合的条目（补测试、构建失败清理、桩事件序、
   帧类型判定、文档版本、commit 口径、测试卫生、release 补密钥扫描与 https 强制）；
4. **第八节追溯方式第 4 条**：按"修改说明 + 结论对照 + current-status"三文档分工出本轮文档。

**未在本次范围内**（见第八节说明）：Z1（前端 ESLint）、Z4（SSE 同步 CPU 调用下沉）、
D7（本机临时文件清理），以及 A 类外部条件项（P2-1 付费评测、远端 CI 绿色、干净 commit 发布）。

**修改范围**：仅 `RAG/`，严格排除 `RAG/new/`。
**改动规模**：本轮修改 32 个文件、新增 3 个文件（另 4 个未跟踪文件为审核方文档与第五轮产物）；
后端用例 278 → **298**。

---

## 一、B 类 8 项：修复自身缺陷

### B1（P2-3 只改对一半）SHA256SUMS 是 CRLF —— 已修，并用标准工具验证

- **根因**：`write_text(...)` 在 Windows 文本模式下把 `\n` 翻成 `\r\n`；而自建 `verify-sums`
  用 `splitlines()` 解析，能容忍 `\r`，于是"38/38 通过"掩盖了标准工具下必然失败的事实。
- **改法**：`lib/json_io.py` 新增 `write_text_lf()` 并让 `write_json()` 也固定 `newline="\n"`；
  `build_artifact_manifest`（清单 / SHA256SUMS / LOGICAL_HASHES）、`build_lineage`、
  `audit_chroma_segments`、`gen_sbom` 全部改走 LF 写入。
- **证据**：字节级测试 `test_sha256sums_is_lf_only`（断言文件不含 `\r`）；
  重建后实测 `sha256sum -c data/release/SHA256SUMS` → **38 项全部 OK**（无 FAILED），
  六份证据文件 `CR=0`。

### B2（P0-3 引入的并发缺陷）drain 阻塞事件循环 + submit 窗口竞态 —— 已修

- **改法**：
  1. `SyncWorkPool` 增加"彻底空闲"事件 `_idle`（active 与 queued 同时为 0 才置位），
     `submit` / `_wrapped` / `note_cancel` / `begin_drain` 在持锁路径上重算；
  2. `wait_idle(timeout)` 改为阻塞在事件上（不再 `time.sleep` 轮询）且**同时看 queued**；
  3. 新增 `wait_idle_async()`（`asyncio.sleep` 让出事件循环）与 `shutdown_sync_pool_async()`，
     `Runtime.shutdown()` 改用它——停机期间事件循环仍能服务正在收尾的 SSE 流与健康检查；
  4. `submit` 把"提交执行器"与"登记 future"放进**同一个临界区**，`begin_drain` 的快照
     不可能落在两步之间。
- **证据**：`test_async_shutdown_does_not_block_event_loop`（drain 期间心跳协程必须推进，
  并断言心跳时间戳早于 drain 结束）、`test_submit_registers_future_before_returning`、
  `test_wait_idle_waits_for_queued_not_only_active`。

### B3（P2-4 仍断一个维度）血缘索引版本"只记录不判定" + measurement_mode 形同虚设 —— 已修

- **改法**：
  1. `scripts/build_lineage.py`：`parent_run_index_version_matches` 为假时产出 problem；
     `validate_lineage()` 增加 measurement_mode 的**条件 schema 校验**（有 demo 制品就必须声明，
     且发布要求 `real_llm`）；`_consistency()` 对 `measurement_mode != real_llm` 判 problem；
  2. `scripts/gen_demo_examples.py`：`--measure` 时按运行时 LLM 可用性写入
     `measurement_mode = real_llm | offline`，并在 `measured` 里记录 `model_used`
     （done 事件），使"demo 是否真的来自模型"可直接判定。
- **证据**：`test_lineage_flags_parent_run_index_version_mismatch`、
  `test_lineage_requires_real_llm_measurement_mode`、
  `test_gen_demo_examples_writes_measurement_mode`（跑通 main 的 payload 组装）。

### B4（P0-4 残留误标路径）版本来源在"不传 --version"时沿用遗留值 —— 已修

- **改法**：`run_server.pin_version(None)` 清掉本进程可能残留的 `RAG_VERSION_SOURCE=cli_explicit`；
  `version_source()` 与 `RAG_ACTIVE_VERSION` **交叉校验**（声明 cli_explicit/env_pinned 但没有
  固定版本 → 以实际行为返回 `latest_scan` 并告警）；`Settings.validate()` 对非法取值报错。
- **证据**：`test_version_source_hint_is_cross_checked`、`test_pin_version_clears_stale_cli_marker`、
  `test_version_source_rejects_illegal_value`。

### B5（P1-13 用例空转）reduced-motion 断言测了没有过渡的元素 —— 已修并反向验证

- **改法**：`styles.css` 给 `.qa-panel-drawer` / `.panel-drawer-mask` 补**真实过渡**
  （transform/opacity 0.18s）；用例改为**双态断言**——正常态时长必须 > 0.05 s（证明元素真有过渡），
  reduce 态必须 ≤ 0.001 s 且小于正常态。
- **反向验证**：临时注释掉 `@media (prefers-reduced-motion: reduce)` 里的
  `transition-duration` 后重建，用例**如实失败**（`Expected: <= 0.001，Received: 0.18`），
  恢复后通过 —— 证明它现在能抓到回归，而不是空转。

### B6（P1-12 假绿风险）e2e 运行器对端口占用无防护 —— 已修（并当场捕获真实冲突）

- **改法**：桩后端接受 `--marker`（health.version / demo.version 用它），运行器传入标记并
  在探活时**校验回来**；同时监听子进程 `exit` 事件，桩提前退出即失败。
- **证据**：实跑时端口 8125 上恰好残留着上一轮的真实服务，运行器立即报
  "端口 8125 上的服务不是本次启动的桩（health.version=20260915_v1）" 并拒绝执行——
  按旧实现，这次会对着真实服务跑绿或超时报错。

### B7（P2-7 两处放行口子）打包 smoke 豁免 + 验签未闭环 —— 已修

- **改法**：
  1. `build_release_bundle.py`：smoke 报告**缺失 / 无法解析 / exit_code ≠ 0** 一律硬拒绝
     （删除原来的 warning 放行路径）；
  2. `release.yml`：给了签名就必须同时给公钥（`data_artifact_pubkey_url` 或
     `RELEASE_DATA_PUBKEY`），并显式传 `--pubkey`，不再依赖 runner keyring；
     数据制品 URL 强制 https。
- **证据**：`test_release_bundle_refuses_missing_or_failed_smoke`（缺失/失败/损坏三类 + 0 退出码对照）。

### B8（R5-5 / R5-6 收尾缺口）空串通配 + 错误态 schema —— 已修

- **改法**：
  1. `get_settings()` 不再 `or ["*"]`；`Settings.validate()` 对显式空 `CORS_ALLOW_ORIGINS` 报错；
  2. health 不再在 `rt is None` 时提前返回：错误态同样返回
     `cache` / `sync_pool` / `rate_limit` / `release_id` / `version_selection` 等键
     （新增 `server/generate/cache.py:empty_cache_stats()`，字段与 `AnswerCache.stats()` 对齐）。
- **证据**：`test_explicit_empty_cors_origins_is_rejected`、
  `test_loading_settings_with_empty_cors_env_is_rejected`、
  `test_health_schema_is_stable_when_runtime_missing`（断言键集合与零值）。

---

## 二、G1 与可低成本闭合的 Z / D 项

| 编号 | 处理 |
| --- | --- |
| G1（锁写死阿里云镜像） | 两份锁文件**删除 `--index-url`**，改为注释说明：锁固定的是版本与哈希，镜像属于本机/网络选择；CI 走官方 PyPI，本机可命令行或 pip.conf 指定镜像（`docs/deploy.md` 同步） |
| Z2（测试覆盖缺口） | 新增：`build_release_bundle` 三条拒绝分支 + 放行对照、`lock_hashes` 解析与逐条判定、`/api/demo/examples` 版本不一致 503、Chroma 审计 `--apply` 清理与清理后重审计 |
| Z3（build_runtime 半途失败泄漏客户端） | `build_runtime` 拆出 `_load_layers`，失败时关闭已登记资源再抛出；`test_build_runtime_closes_clients_when_later_layer_fails` 断言 embedding 客户端被关闭 |
| Z5（桩事件序与注释不符） | 桩的 SSE 序列改为与 `server/sse.py` 逐帧一致（两个检索 status 都先于两个 results 帧），注释写明真实顺序 |
| D1（`'"type": "answer"' in frame` 判答案帧） | 新增 `_frame_type()` 解析帧取 `type`，不再用子串匹配（用户/模型可控内容含该字面量时不再误判超时终态） |
| D3（README 引用不存在的版本） | `scripts/README.md` 示例版本改为实际存在的 `20260915_v1`，并补发布链路命令段 |
| D4（两份证据 commit 口径不一） | manifest 的 `git_commit` 统一为 `<sha12>@<branch>`（与 lineage 一致），另存 `git_commit_full` |
| D5（测试卫生） | 删除恒真断言；`check_dataset_counts()` 支持传入文档路径，测试改用临时副本（不再改写仓库里的 current-status.md） |
| D6（release 不复跑密钥扫描 / URL 未强制 https） | release workflow 增加 `check_secrets.py` + `check_docs.py --strict` 步骤；数据制品 URL 必须是 https |

**未处理（明确留在下一轮）**：

- **Z1 前端 ESLint**：需要引入 eslint + 插件依赖并对存量代码洗一遍，属独立工程；
  本轮不半做（当前前端门禁为 `vue-tsc` 类型检查 + 43 条单测/组件测试 + Playwright）。
- **Z4 SSE 同步 CPU 调用下沉**：涉及 F05 融合装配、引用构建、缓存写入的热路径改造，
  审核方也只列为"静态确认、未量化"；在没有负载测量前改并发结构风险大于收益，留待专项。
- **D7 本机临时文件清理**：`logs/` 下调试脚本与根目录 `ragv3-render-check.json` 均已被
  `.gitignore` 覆盖（无入库风险），属维护者本机清理事项；只修了 `server/api.py` docstring
  里硬编码的本机解释器路径。

---

## 三、文件级修改清单

### 3.1 修改的文件（32 个）

| 文件 | 改了什么 | 对应条目 |
| --- | --- | --- |
| `lib/json_io.py` | 新增 `write_text_lf()`；`write_json()` 固定 LF | B1 |
| `scripts/build_artifact_manifest.py` | LF 写入、`git_commit` 口径统一 + `git_commit_full` | B1、D4 |
| `scripts/build_lineage.py` | index_version 判定、measurement_mode 校验 | B3 |
| `scripts/gen_demo_examples.py` | 写 `measurement_mode` 与 `measured.model_used` | B3 |
| `scripts/audit_chroma_segments.py` | LF 写入 | B1 |
| `scripts/gen_sbom.py` | LF 写入、可重现命名空间、scoped purl、两份锁都收 | B1、D2 |
| `scripts/lock_hashes.py` | `check()` 改为**逐条**判定（原计数式会漏判） | Z2（并修出真实缺陷） |
| `scripts/build_release_bundle.py` | smoke 报告缺失/失败/损坏 → 硬拒绝 | B7 |
| `scripts/run_server.py` | `pin_version(None)` 清理残留来源声明 | B4 |
| `scripts/check_docs.py` | `check_dataset_counts(doc_path=None)` 可传路径 | D5 |
| `scripts/README.md` | 示例版本修正 + 发布链路命令段 | D3 |
| `server/sse.py` | `_idle` 事件、`wait_idle` 看 queued、`wait_idle_async`、`shutdown_sync_pool_async`、submit 锁内登记 | B2 |
| `server/runtime.py` | `shutdown` 用异步 drain；`version_source` 交叉校验；`build_runtime` 失败清理 | B2、B4、Z3 |
| `server/api.py` | health 错误态完整 schema；`_frame_type()` 替代子串判定；CORS 不再 `or ["*"]` | B8、D1、B8 |
| `server/generate/cache.py` | 新增 `empty_cache_stats()` | B8 |
| `config/settings.py` | CORS 空值报错；`RAG_VERSION_SOURCE` 取值校验 | B8、B4 |
| `frontend/src/styles.css` | 抽屉/蒙层补真实过渡 | B5 |
| `frontend/tests/e2e/accessibility.spec.ts` | reduced-motion 双态断言（真实过渡元素） | B5 |
| `frontend/scripts/e2e-run.mjs` | 桩 marker 校验 + 子进程退出处理 | B6 |
| `frontend/scripts/e2e-server.mjs` | 支持 `--marker`；SSE 序列与后端逐帧对齐 | B6、Z5 |
| `requirements.lock` / `requirements-dev.lock` | 去掉 `--index-url`（镜像归本机） | G1 |
| `.github/workflows/release.yml` | 验签公钥闭环、https 强制、密钥扫描与文档检查步骤 | B7、D6 |
| `.github/workflows/ci.yml` | （沿用第五轮）锁哈希门禁、browser job | — |
| `.env.example`、`docs/deploy.md`、`docs/current-status.md`、`docs/README.md`、`README.md` | 口径与入口同步（含镜像安装说明、LF/标准校验说明） | G1、B1 |
| `tests/test_release_guards.py` | +18 条（B2/B4/B8/Z3 及各门禁） | B2、B4、B8、Z3 |
| `tests/test_release_pipeline.py` | +8 条（B1/B3/B7/D2/D5/Z2） | B1、B3、B7、D2、D5、Z2 |

### 3.2 本轮新增的文件（3 个）

| 文件 | 用途 |
| --- | --- |
| `docs/changes/20260916-round6-remediation-change-note.md` | 本文档：修改说明与参考依据 |
| `docs/changes/20260916-round6-review-remediation-summary.md` | 结论对照（每条审核项改到哪一步） |
| （`server/generate/cache.py` 的 `empty_cache_stats` 为修改非新增） | — |

> 其余未跟踪文件（第五轮审核/修改说明/结论对照、第六轮复核报告）由审核方或上一轮产出，非本轮改动。

---

## 四、验证方法与实测结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `python -m pytest tests -q` | **298 passed**（上轮 278，+20） |
| 标准哈希校验（B1 的验收工具） | `sha256sum -c data/release/SHA256SUMS` | **38 项全部 OK**（无 FAILED） |
| 证据文件行尾 | 逐文件字节统计 | `SHA256SUMS`/`LOGICAL_HASHES`/`manifest`/`lineage`/`chroma 审计`/`sbom` 均 `CR=0` |
| 制品清单 | `build_artifact_manifest.py verify` / `verify-sums` | 37/37 一致；38 个物理哈希通过 |
| Chroma 四方计数 | `audit_chroma_segments.py --version 20260915_v1` | 9544 四方一致，退出码 0 |
| 血缘 | `build_lineage.py --check` | 仍**按预期失败**（demo 未重建，B3 新增两条判定同时报出） |
| SBOM | `gen_sbom.py generate` + `validate` | SPDX 2.3，306 包（Python 99 / Node 207），命名空间可重现，scoped purl 规范 |
| 依赖锁 | `lock_hashes.py --check` | 93/99 条需求**逐条**带 `--hash`；锁内已无 `--index-url` |
| 前端 | `cd frontend && npm run verify` | 43 passed + build + 体积门禁 |
| 浏览器（桩后端） | `npm run test:e2e:offline` | **12 passed / 2 skipped**；端口占用时按 B6 拒绝执行（实测捕获） |
| 浏览器（真实服务） | `RAG_BASE_URL=http://127.0.0.1:8125 npm run test:e2e` | **12 passed / 2 skipped** |
| 契约端到端 | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项全过 |
| health schema（正常态） | `curl /api/health` | 20 个键齐全，`version_selection=cli_explicit`，`cache` 完整 |
| health schema（错误态） | `test_health_schema_is_stable_when_runtime_missing` | 键集合与零值对象断言通过 |
| reduced-motion 非空转 | 临时禁用规则 → 重建 → 跑用例 | **如实失败**（Received 0.18）；恢复后通过 |
| workflow 语法 | `yaml.safe_load` + 逐 `run:` 块 `bash -n` | 两个 workflow 解析通过，全部 shell 块通过 |
| 文档 / 密钥 / 语法下限 | `check_docs.py --strict`、`check_secrets.py`、3.9 语法 parse | 全部通过 |

---

## 五、第六节"下一轮整改建议顺序"的对账

| 建议步骤 | 本轮状态 |
| --- | --- |
| 1. 修 B1 + 字节级测试 + 重建清单 | ✅ 完成，并用 `sha256sum -c` 复核 |
| 2. 修 B2 / B4 + 集成测试 | ✅ 完成（含真实停机场景与 env 残留场景） |
| 3. 修 B3（index_version problem、measurement_mode） | ✅ 代码完成；demo 数据待 P2-1 付费评测 |
| 4. 修 B5 / B6 / B7 / B8 | ✅ 完成（B5 含反向验证） |
| 5. 处理 G1（CI 用官方 PyPI） | ✅ 完成（锁文件去掉镜像） |
| 6. 提交流程（核对未跟踪文件 → P2-1 评测 → 链路级演练 → 推送） | ⏳ 未做：需维护者决定提交与付费评测；本轮未提交、未推送 |

---

## 六、未完成与未验证（不夸大）

1. **A 类外部条件项**：P2-1 真实 demo（需付费模型评测）、远端 CI/release 绿色（未推送）、
   干净 commit 上的完整发布 —— 与前一轮口径一致，本轮未推进。
2. **硬事实 1（未跟踪文件与 workflow 引用脱节）**：本轮仍未提交；提交时务必按第六轮报告
   第三节表格核对 `scripts/lock_hashes.py`、`frontend/scripts/e2e-run.mjs`、`e2e-server.mjs`、
   `scripts/fetch_data_artifact.py`、`gen_sbom.py`、`build_release_bundle.py`、
   `tests/test_release_pipeline.py`（以及本轮的 3 个新文档）一个不漏。
3. **链路级发布演练**：本轮做了逐命令复现与打包脚本的分支测试，但**没有**把 release-gate
   的等价命令序列在本地完整跑一遍（仍会被 demo 门禁挡在打包之前，见硬事实 2）。
4. **Z1 / Z4 / D7**：见第二节"未处理"，留给下一轮或专项。
5. **真实反代/生产环境**：CORS 实际行为、`X-Forwarded-For` 可信代理、SSE 缓冲仍需真实环境复核。

## 七、追溯方式（从复核条目反查改动）

1. 打开被参考文档，取条目编号（如 `B2`、`G1`、`D4`）；
2. 在本文第二节或第三节表格按编号检索，得到"改法 + 对应文件 + 证据"；
3. 代码内所有本轮改动都写了"第六轮复核 Bx / Zx / Dx"注释与原因；
4. 运行第四节对应行的命令复现结论；
5. 未闭合条目见第六节的原因与前置条件。
