# 20260916 第五轮复核整改总结

> 执行依据：[20260916-round5-remediation-review-and-full-project-audit.md](20260916-round5-remediation-review-and-full-project-audit.md)
> （第五轮独立复核：13 项重裁定 + 7 项新增问题 R5-1…R5-7）。
> 范围：仅 `RAG/`，严格排除 `RAG/new/`（本轮未修改、未提交该目录）。
> 完成日期：2026-09-16。当前数字统一维护在 [../current-status.md](../current-status.md)。
>
> 配套文档：[20260916-round5-remediation-change-note.md](20260916-round5-remediation-change-note.md)
> —— 那篇是**修改说明**（按文件说明改了什么、依据哪条审核要求、怎么验证）；
> 本篇是**结论对照**（每条审核项如何重裁定、完成到哪一步）。

## 一、结论

| 判定 | 项 |
| --- | --- |
| ✅ 本轮已修复（代码 + 自动化测试 + 本地门禁复现） | P0-3、P0-4、P1-5、P1-12、P1-13、P2-3、P2-4、P2-5、P2-6、P2-7、R5-1…R5-7 |
| ⚠️ 代码就绪、证据待外部条件 | 远端 CI/release 绿色 run（需推送）、干净 commit 上的发布包 |
| ⛔ 仍需付费模型调用 | P2-1 真实 demo 制品（连带 P2-4 的 demo 父子链闭合） |

本轮不宣称"全部完成"：**P2-1 未完成**（需要一次真实模型评测，由维护者决定何时执行），
因此 lineage 的跨层一致性检查在本地仍以退出码 1 报出 demo 链断裂——这是**正确行为**
（宁可让门禁红着，也不让"demo 来自哪次评测"变成无人核对的字符串）。

**本地门禁实测（2026-09-16）**：

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `pytest tests -q` | 278 passed（原 250，本轮 +26 条守护用例） |
| 前端 | `cd frontend && npm run verify` | 43 passed + build + 体积门禁 |
| 契约端到端 | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项全过（真实服务） |
| 浏览器验收 | `npm run test:e2e`（真实服务）/ `npm run test:e2e:offline`（桩后端） | 12 passed / 2 skipped，两种后端各跑通一次 |
| 制品清单 | `build_artifact_manifest.py verify` / `verify-sums` | 37/37 一致 / 38 个物理哈希全过 |
| 依赖锁 | `pip install --dry-run --require-hashes -r requirements-dev.lock` | 通过（锁文件含完整 `--hash`） |
| 文档 | `check_docs.py --strict` | 通过（含数据计数与清单一致） |
| 编译 | `python -m compileall`（3.11）与 3.9 语法下限 | 通过 |

## 二、第五轮 13 项重裁定的逐项处置

| 编号 | 第五轮裁定 | 本轮修复 | 关键证据 |
| --- | --- | --- | --- |
| P0-3 客户端生命周期 | ⚠️ 部分完成（停机顺序反了） | `Runtime.shutdown` 改为「同步池停止收单 → 撤销排队 → 有上限 drain 在途 → 再关外部客户端」；`SHUTDOWN_DRAIN_SECONDS` 可配；收尾结果写入 `meta.shutdown_sync_pool` | `test_shutdown_drains_active_before_closing_clients`（断言"任务结束"早于"客户端关闭"）、`test_shutdown_cancels_queued_tasks_without_running_them` |
| P0-4 clean commit 与证据绑定 | ⚠️ 部分完成 | `run_server.py --version` 同时写 `RAG_VERSION_SOURCE=cli_explicit`；settings 新增 `version_source_hint`；health 实测 `version_selection=cli_explicit` | `test_version_source_cli_explicit_via_env_hint`、`test_version_source_three_states`；真实服务 health 输出 |
| P1-5 同步池观测与预算 | ⚠️ 部分完成 | 见 P0-3；另修 `SyncWorkPool` 在停机时的自锁死（`Future.cancel()` 同步触发回调 + 非重入锁） | 全量测试不再卡死；`test_sync_pool_separates_active_and_queued` 等 |
| P1-12 前端测试体系 | ⚠️ 部分完成（浏览器从未成功运行） | 新增桩后端 `frontend/scripts/e2e-server.mjs` + 运行器 `e2e-run.mjs`（`npm run test:e2e:offline`），Chromium 安装成功；`.gitignore` 忽略 Playwright 产物；CI 新增 `browser` job | 本地真实服务 12 passed / 桩服务 12 passed；`git check-ignore` 确认产物被忽略 |
| P1-13 可访问性验收 | ⚠️ 部分完成 | 新增 Tab/Shift+Tab 焦点陷阱用例；**用例首次运行即发现真实缺陷**（tablist roving tabindex 让"最后一个可聚焦元素"算错 → 第 2 次 Tab 就逃逸），修 `App.vue` 的 `tabbableIn()` | `[desktop]/[mobile] 焦点陷阱` 用例；修复前后实测焦点轨迹 |
| P2-1 真实 demo 制品 | ❌ 未完成 | 未执行（需付费模型调用）；重建命令与校验链路已就绪并在 current-status 中写明 | `build_lineage.py --check` 如实报出 demo 链断裂 |
| P2-3 制品清单稳定性 | ⚠️ 部分完成 | `SHA256SUMS` 只写**物理**哈希（`sha256sum -c` 语义成立），逻辑哈希另出 `LOGICAL_HASHES.json`；新增 `verify-sums` 子命令；`_git()` 统一 strip | `test_sha256sums_uses_physical_hash_and_logical_stays_separate`、`test_manifest_git_commit_is_stripped`；本地 38 文件物理校验通过 |
| P2-4 完整数据血缘 | ⚠️ 部分完成 | lineage 新增 `demo.parent`（按 `source_run` 真实解析 run 目录 + meta 哈希）、`checks`（run/demo/runtime 版本一致性）、`--check` 模式与 `LINEAGE_SCHEMA`；CI 加校验步骤 | `test_lineage_*` 3 条；本地 `--check` 因 demo 未重建而**按预期失败** |
| P2-5 Chroma 段审计 | ⚠️ 部分完成 | 修正 manifest 计数读取位置（`vectors.count`）；补 `collection count`（按 collection 全 segment 汇总，实测 embeddings 挂在 METADATA 段）；四方计数任一缺失/不一致即退出非零；`--apply` 后重审计 | `test_chroma_audit_counts_four_sources`、`test_chroma_audit_detects_manifest_mismatch_and_missing_ids`、`test_chroma_audit_main_returns_nonzero_on_mismatch`；实跑四方均 9544 |
| P2-6 Python 锁文件 | ❌ 未完成（CI 直接失败） | 只支持 Python 3.11（3.9 降为语法下限 job）；用 `pip freeze` 约束把锁钉到**实测版本**；新增 `scripts/lock_hashes.py`（走 PyPI JSON API 取哈希，不需要下载制品） | `pip install --dry-run --require-hashes` 通过；抽样实际下载 chromadb/numpy/openai 校验哈希一致；`lock_hashes.py --check` 进入 CI |
| P2-7 CI 与 release | ❌ 未完成 | `release.yml` 重写：heredoc 全部移入 `scripts/`（YAML 现可解析）、smoke 去掉 `|| true`、数据制品必须带预期 sha256 + zip-slip/范围检查（`fetch_data_artifact.py`）、SBOM 用 `gen_sbom.py` 生成有效 SPDX 2.3、release 包用 `build_release_bundle.py` 补齐源码/数据/dist/证据 | `yaml.safe_load` 解析通过；`test_fetch_data_artifact_*`、`test_sbom_*` |

## 三、R5-1…R5-7 的处置

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| R5-1 | Windows GBK 控制台 `UnicodeEncodeError` | `smoke_deploy.py` 探测 `sys.stdout.encoding`，编码不支持时用 `[OK]/[FAIL]`，并提供 `_safe_print` 兜底；**实测** `PYTHONIOENCODING=gbk` 下正常输出（修复前会崩） |
| R5-2 | 唯一事实源数据集数字错误 | `current-status.md` 改为 9925/17700/9544；`check_docs.py` 新增"数据计数与快照/索引清单一致"检查，写错即 CI 失败 |
| R5-3 | 整改总结状态统计与表格不符 | 第四轮总结加口径修正说明（8/4/1 → 复核重裁 2/8/3），并注明统计必须由表格机械汇总 |
| R5-4 | Playwright 产物未忽略 | `.gitignore` 增加 `frontend/test-results/`、`playwright-report/`、`blob-report/`、`.playwright/`；release 增加"跑完浏览器用例后工作区仍干净"的显式检查 |
| R5-5 | 生产 CORS 默认通配符 | 显式生产档（`RAG_REQUIRE_ACTIVE_VERSION=true`）+ 通配 + 未确认 → **启动失败**；隐式生产只告警；新增 `ALLOW_PUBLIC_CORS` 确认项；health 的 `warnings` 同步透出 |
| R5-6 | health 的 sync_pool schema 不稳定 | 池未创建时返回与 `stats()` **字段完全一致**的零值对象（`initialized=false`）；health 首次请求即为完整 schema |
| R5-7 | release 元数据 commit 未规范化 | `_git()` 统一 `.strip()`；`reference` 字段不再带换行 |

## 四、本轮修出来的额外缺陷（不在审核清单里，但属于同类问题）

1. **同步池停机路径自锁死**（我引入并被全量测试抓到）：`Future.cancel()` 会**同步**触发
   done 回调 `_forget`，而它需要同一把非重入锁 → `note_cancel`/`begin_drain` 死锁。
   改为 `threading.RLock()` 并写明原因。若只跑子集测试或不看退出码，这个问题会被漏掉。
2. **抽屉焦点陷阱并未真正生效**（P1-13 的核心结论被证实）：tablist 用 roving tabindex，
   旧选择器把 `tabindex="-1"` 的按钮算作可聚焦，`active === last` 永不成立 → 第 2 次
   Tab 焦点即逃出抽屉。修复后才满足"Tab/Shift+Tab 都不逃逸"。
3. **reduced-motion 断言依赖浏览器字符串形态**：Chrome 把 `0.001ms` 规范化为 `1e-06s`，
   原用例从未跑过因此没暴露；改为按秒解析数值比较。

## 五、未完成 / 未验证的部分（不夸大）

1. **P2-1 真实 demo**：需要付费模型调用，本轮未执行；demo 接口仍诚实返回 503，
   `build_lineage.py --check` 因此仍失败。
2. **远端 CI/release 绿色**：本轮**未推送**，所有结论仅来自本地复现；GitHub Actions
   的绿色结论必须由推送后的实际 run 证明（这正是第四轮"文件存在≠流水线可用"的教训）。
3. **干净 commit 上的完整发布**：发布包组装脚本已就绪，但未在提交后重跑一遍
   `--require-clean` 链路；当前证据里 `git_dirty=true`。
4. **真实代理/生产环境验收**：CORS、限流可信代理、SSE 缓冲等仍需在真实反代下复核。

## 六、复现命令

```bash
# 后端
python -m pytest tests -q
# 前端（类型 + 单测 + 组件 + 构建 + 体积）
cd frontend && npm run verify
# 浏览器（桩后端，无需 data/）与（真实服务）
npm run test:e2e:offline
RAG_BASE_URL=http://127.0.0.1:8125 npm run test:e2e
# 依赖锁
python scripts/lock_hashes.py --check
pip install --dry-run --require-hashes -r requirements-dev.lock
# 发布证据链
python scripts/build_lineage.py --version 20260915_v1
python scripts/build_lineage.py --check
python scripts/audit_chroma_segments.py --version 20260915_v1
python scripts/gen_sbom.py generate --version 20260915_v1 && python scripts/gen_sbom.py validate
python scripts/build_artifact_manifest.py build --version 20260915_v1 --require-clean
python scripts/build_artifact_manifest.py verify
python scripts/build_artifact_manifest.py verify-sums
python scripts/build_release_bundle.py --version 20260915_v1
# 文档与密钥
python scripts/check_docs.py --strict
python scripts/check_secrets.py
```
