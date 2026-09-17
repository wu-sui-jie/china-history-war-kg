# 20260916 第六轮复核整改结论对照

> 执行依据：[20260916-round6-review-of-round5-remediation.md](20260916-round6-review-of-round5-remediation.md)
> （第六轮独立复核：B1–B8 修复缺陷 + G1 高优先增量 + Z/D 级清查项）。
> 配套文档：[20260916-round6-remediation-change-note.md](20260916-round6-remediation-change-note.md)
> （**修改说明**：依据哪条要求改了哪些文件、如何验证）；本篇是**结论对照**（每条改到哪一步）。
> 当前数字统一维护在 [../current-status.md](../current-status.md)。

## 一、结论

| 判定 | 项 |
| --- | --- |
| ✅ 本轮已修（代码 + 自动化测试 + 本地复现） | B1、B2、B3、B4、B5、B6、B7、B8、G1、Z2、Z3、Z5、D1–D6 |
| ⏳ 代码就绪、待外部动作 | P2-1 真实 demo（付费评测）、远端 CI/release 绿色（需提交并推送）、干净 commit 发布、链路级发布演练 |
| 🚫 明确不本轮处理 | Z1（前端 ESLint）、Z4（SSE 同步 CPU 调用下沉）、D7（本机临时文件清理） |

**本地门禁实测（2026-09-16）**：

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `pytest tests -q` | **298 passed**（上轮 278，+20） |
| 标准哈希校验 | `sha256sum -c data/release/SHA256SUMS` | **38/38 OK**（B1 的验收工具，非自建解析器） |
| 制品清单 | `verify` / `verify-sums` | 37/37 一致 / 38 个物理哈希通过 |
| Chroma 四方计数 | `audit_chroma_segments.py` | 9544 四方一致，退出码 0 |
| 血缘 | `build_lineage.py --check` | 按预期失败（demo 未重建）；B3 新增的两条判定同样报出 |
| SBOM | `gen_sbom.py validate` | SPDX 2.3、306 包、命名空间可重现 |
| 依赖锁 | `lock_hashes.py --check` | 93/99 条逐条带 `--hash`，锁内无 `--index-url` |
| 前端 | `npm run verify` | 43 passed + build + 体积门禁 |
| 浏览器（桩 / 真实服务） | `npm run test:e2e:offline` / `RAG_BASE_URL=... npm run test:e2e` | 各 **12 passed / 2 skipped** |
| 契约端到端 | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项全过 |
| 文档 / 密钥 / 语法下限 / workflow | `check_docs.py --strict`、`check_secrets.py`、3.9 语法 parse、`bash -n` | 全部通过 |

## 二、B 类 8 项逐条对照

| 编号 | 第六轮"下次改法" | 本轮实施 | 证据 |
| --- | --- | --- | --- |
| B1 SHA256SUMS 行尾 | 写入指定 `newline="\n"`；重建；补字节级测试 | `lib/json_io.write_text_lf()` + 证据写入全部改 LF | `test_sha256sums_is_lf_only`；`sha256sum -c` 38/38 OK |
| B2 drain 阻塞 + submit 竞态 | drain 可等待（async）；submit 锁内登记；wait_idle 看 queued；补集成测试 | `_idle` 事件 + `wait_idle_async` + `shutdown_sync_pool_async`；submit 单临界区登记 | 3 条新用例（心跳推进、future 登记、queued 等待） |
| B3 血缘两处判定 | index_version 产出 problem；写 measurement_mode；schema 校验 | `_consistency` 判 index_version 与 measurement_mode；生成器写 `real_llm/offline` + `model_used` | 3 条新用例；`--check` 实跑报出新增两条 |
| B4 版本来源误标 | 不传 `--version` 时清理变量；hint 与实际交叉校验；非法取值报错 | `pin_version(None)` 清理；`version_source()` 交叉校验并告警；`validate()` 校验取值 | 3 条新用例 |
| B5 空转用例 | 断言改到真实有过渡的元素 | 抽屉/蒙层补过渡；用例双态断言（正常 > 0.05s、reduce ≤ 0.001s 且更小） | **反向验证**：禁用规则后如实失败（0.18） |
| B6 e2e 假绿 | 校验桩特征字段；监听子进程退出 | `--marker` 端到端标记 + exit 事件 | 实测捕获端口残留真实服务并拒绝执行 |
| B7 打包放行口子 | 报告缺失/无法解析硬拒绝；workflow 显式传公钥 | `build_release_bundle` 三条硬拒绝；release 要求签名+公钥成对、URL 强制 https | `test_release_bundle_refuses_missing_or_failed_smoke` |
| B8 空串通配 + 错误态 schema | 空串视为配置错误；错误态也返回零值对象 | `validate()` 报错 + 去掉 `or ["*"]`；health 错误态含 `cache`/`sync_pool` 等 | 3 条新用例（含 TestClient 错误态断言） |

## 三、增量发现（G / Z / D）逐条对照

| 编号 | 本轮处理 |
| --- | --- |
| G1 锁写死阿里云镜像 | 两份锁去掉 `--index-url`，注释说明"版本与哈希属锁、镜像属环境"；`docs/deploy.md` 补本机镜像用法 |
| Z2 覆盖缺口 | 补 `build_release_bundle`（3 拒绝 + 1 放行）、`lock_hashes`（解析/逐条判定/渲染）、demo 503、Chroma `--apply` 清理与重审计 |
| Z3 构建失败泄漏客户端 | `_load_layers` + 失败回收；用例断言 embedding 客户端被关闭 |
| Z5 桩事件序注释不符 | 桩序列与 `server/sse.py` 逐帧对齐并写明真实顺序 |
| D1 帧类型子串判定 | `_frame_type()` 解析帧取 `type` |
| D2 SBOM 三处口径 | 命名空间改为内容摘要（可重现）；npm scoped purl 按规范编码；两份锁都收并标注来源 |
| D3 README 失效版本 | 示例改为实际存在的 `20260915_v1`，补发布链路命令段 |
| D4 证据 commit 口径 | manifest 统一 `<sha12>@<branch>` + 新增 `git_commit_full` |
| D5 测试卫生 | 去恒真断言；`check_dataset_counts(doc_path)` 支持临时副本，不再改写仓库文件 |
| D6 release 缺密钥扫描 / URL 未强制 https | release workflow 增加 `check_secrets.py` 与 `check_docs.py --strict`；URL 必须 https |
| Z1 前端 ESLint | **未做**：需引入依赖并清洗存量代码，属独立工程；当前前端门禁为 vue-tsc + 43 单测/组件 + Playwright |
| Z4 SSE 同步 CPU 调用下沉 | **未做**：热路径并发改造，需先有负载量化；留专项 |
| D7 本机临时文件清理 | **未做**（已被 .gitignore 覆盖，无入库风险）；仅修了 `server/api.py` docstring 里的本机路径 |

## 四、第六轮指出的两条硬事实

| 硬事实 | 本轮状态 |
| --- | --- |
| 1. 未跟踪文件与 workflow 引用脱节，现在推送必然失败 | **仍未提交**（本轮改动同样停留在工作区）。提交前的核对清单已写入修改说明第六节第 2 条：7 个被 workflow 引用的脚本 + 本轮 3 个文档一个不漏 |
| 2. P2-1 完成前 release 门禁结构性必红 | **仍然成立**（demo 未重建）。本轮把 B3 的两条判定补上后，`--check` 的失败原因从 2 条增到 3 条，全部指向同一件事：需要一次真实模型评测 |

## 五、未完成与未验证

1. **P2-1 真实 demo 制品**：需付费模型评测；重建命令与校验链路已就绪
   （`run_evaluation.py run` → `gen_demo_examples.py --measure` → `build_lineage.py --check`）。
2. **远端 CI/release 绿色**：本轮未提交、未推送；所有结论来自本地复现。
3. **链路级发布演练**：本轮做了逐命令复现与打包分支测试，但没有把 release-gate 的等价
   命令序列完整跑通（会被 demo 门禁挡在打包前，见硬事实 2）。
4. **干净 commit 发布**：当前证据仍是 `git_dirty=true`。
5. **Z1 / Z4 / D7**：见第三节表格。

## 六、三文档分工（沿用第六轮报告第八节的约定）

| 文档 | 职责 |
| --- | --- |
| [20260916-round6-remediation-change-note.md](20260916-round6-remediation-change-note.md) | 修改说明：依据哪条要求、改了哪些文件、怎么验证 |
| 本文档 | 结论对照：每条改到哪一步、哪些未闭合 |
| [../current-status.md](../current-status.md) | 当前状态唯一事实源：版本、测试数、demo 与发布状态 |
