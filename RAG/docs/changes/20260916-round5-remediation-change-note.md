# 20260916 第五轮复核整改修改说明（改动清单 + 参考依据）

## 〇、本次修改参考的文档（唯一依据）

**本次修改全部依据下面这一份审核文档执行，未引入该文档之外的整改范围：**

| 项 | 内容 |
| --- | --- |
| 被参考文档 | [`docs/changes/20260916-round5-remediation-review-and-full-project-audit.md`](20260916-round5-remediation-review-and-full-project-audit.md) |
| 完整路径 | `F:\python\python_space\china-war\RAG\docs\changes\20260916-round5-remediation-review-and-full-project-audit.md` |
| 文档性质 | 第五轮独立整改复核报告（审核方产出，非本次修改产物） |
| 审核日期 / 被审核 HEAD | 2026-09-16 / `2e06aea4736c4663945f2e782e3c7c88e0647fd8`（分支 `round4-review-remediation`） |
| 文档给出什么 | ① 13 项上轮整改的重裁定（P0-3、P0-4、P1-2、P1-3、P1-5、P1-12、P1-13、P2-1、P2-3、P2-4、P2-5、P2-6、P2-7）；② 7 项新增问题 R5-1…R5-7；③ 推荐整改顺序（阶段 1–4）；④ 最终发布门禁 17 条清单 |
| 上游追溯链 | `20260916-round4-review-remediation-work-order.md`（工作单）→ `20260916-round4-review-remediation-summary.md`（上轮整改总结）→ 本轮审核报告 → 本修改说明 |

具体地，本次改动逐条对应被参考文档的这三处要求：

1. **第一节"发布结论"列出的 7 条发布阻断**（release.yml 不是合法 YAML、lock 无 hashes、
   3.9 矩阵不可安装、demo 503、Playwright 未通过、smoke 失败被忽略/发布包不完整、
   Playwright 产物污染 clean 门禁）；
2. **第六节"推荐整改顺序"的四个阶段**（阶段 1 恢复 CI 基础 / 阶段 2 修复发布门禁 /
   阶段 3 数据与浏览器闭环 / 阶段 4 运行时与文档收口）——本文第二节即按这四个阶段组织；
3. **第七节"最终发布门禁"的 17 条 checklist**——本文第五节逐条对账。

> 与另一份文档的分工（避免重复维护同一件事）：
> - 本文档 = **修改说明**：改了什么文件、为什么改、对应哪条审核要求、怎么验证；
> - [`20260916-round5-review-remediation-summary.md`](20260916-round5-review-remediation-summary.md) = **结论对照**：
>   每条审核项的重裁定结论、完成/未完成判定；
> - [`../current-status.md`](../current-status.md) = **当前状态唯一事实源**：版本、测试数、demo 与发布状态。
> 三者互相引用，数字只在 current-status 维护。

**修改范围**：仅 `RAG/`，严格排除 `RAG/new/`（本轮未改动、未提交该目录）。
**改动规模**：修改 27 个文件、新增 9 个文件（另有 1 个由审核方提供的输入文档），
后端用例 250 → **278**（新增 28 条）。

---

## 一、阶段 1：恢复 CI 基础可用性

对应被参考文档"第六节 阶段 1"与 P2-6、P2-7 第 1 条。

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| release.yml YAML 合法化 | `.github/workflows/release.yml` | 原文件把多行 Python heredoc 直接写在 YAML 块标量里（内容未缩进），解析失败导致 GitHub run 0 秒失败。**改法**：把所有内联脚本移到 `scripts/`（`fetch_data_artifact.py`、`gen_sbom.py`、`build_release_bundle.py`），YAML 里只剩单行命令；`yaml.safe_load` 解析通过，且每个 `run:` 块另过 `bash -n` 语法检查 |
| Python 版本矩阵收敛 | `.github/workflows/ci.yml` | 原矩阵 3.9 + 3.11 共用一份由 3.11 生成的 lock（`aiohappyeyeballs>=3.10` 在 3.9 上必然失败）。**改法**：正式支持版本声明为 3.11；原 3.9 的意图（"不依赖新语法"）保留为 `syntax-floor` job（只跑 `compileall`，不装依赖）；`docs/deploy.md`、`docs/current-status.md` 同步口径 |
| 锁文件补齐 `--hash` | `requirements.lock`、`requirements-dev.lock`、`scripts/lock_hashes.py`（新增） | `pip-compile --generate-hashes` 要为所有平台下载 wheel（实测两次因镜像传输中断失败，这正是上一轮放弃的原因）。**改法**：新增 `scripts/lock_hashes.py`，走 PyPI JSON API 取每个文件的 `digests.sha256`，不下载制品即可补齐 93/99 条需求的哈希；同时用 `pip freeze` 约束把锁钉到**开发环境实测版本**（原锁解析出 chromadb 1.5.9，而实测为 1.3.4） |
| CI 按锁安装 | `.github/workflows/ci.yml` | 装依赖时先判断锁文件是否含 `--hash=`：有则 `--require-hashes`（供应链强制），无则退化为普通安装并打 warning；`lint` job 增加 `lock_hashes.py --check` 门禁，防止哈希被回退 |

---

## 二、阶段 2：修复发布门禁

对应被参考文档"第六节 阶段 2"与 P2-7 第 3–7 条、P2-3。

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| smoke 失败不再被吞 | `.github/workflows/release.yml` | 去掉 `python scripts/smoke_deploy.py ... \|\| true`，smoke 退出码非 0 即阻断发布 |
| 数据制品可信下载 | `scripts/fetch_data_artifact.py`（新增） | 四道关：预期 sha256 必需（没有就不下载也不解包）、可选 gpg 验签（有 `--signature` 但无 gpg 时**失败**而非跳过）、zip-slip 防护（绝对路径/盘符/`..`/符号链接一律拒绝）、解包范围与总体积限制。另带 `--pack` 模式供维护者本机生成数据 zip |
| SBOM 成为有效 SPDX | `scripts/gen_sbom.py`（新增） | 原实现只有 `spdxVersion/name/packages`，缺文档必需字段、无 Node 依赖、也没打进包里。**改法**：生成结构完整的 SPDX 2.3（`dataLicense`、`SPDXID`、`documentNamespace`、`creationInfo`、每包 `downloadLocation`/`filesAnalyzed=false`/许可证字段/purl、`DESCRIBES` 关系），Python 依赖取锁文件、Node 取 `package-lock.json`（实测 306 个包），并提供不依赖第三方库的 `validate` 自检 |
| SHA256SUMS 语义修正 | `scripts/build_artifact_manifest.py` | 原实现把 Chroma 元数据库的**逻辑哈希**写进标准校验和文件，`sha256sum -c` 必然报错。**改法**：`SHA256SUMS` 只写物理哈希；逻辑哈希另出 `LOGICAL_HASHES.json`（带 `hash_mode` 说明）；新增 `verify-sums` 子命令做等价物理校验；`_git()` 统一 `.strip()`（R5-7） |
| release 包补全 | `scripts/build_release_bundle.py`（新增） | 原打包只复制证据与依赖声明。**改法**：`git archive HEAD` 导出源码树 + snapshot/index/eval + 前端 dist + 发布证据 + SBOM + 依赖声明 + `README-RELEASE.md`/`MANIFEST.txt`，并生成 tar.gz 与 `.sha256`；smoke 报告退出码非 0 时**拒绝打包** |
| Playwright 产物治理 | `.gitignore`、`.github/workflows/release.yml` | 忽略 `frontend/test-results/`、`playwright-report/`、`blob-report/`、`.playwright/`（R5-4）；release 增加显式步骤"跑完浏览器用例后 `git status --porcelain -- ':!new'` 必须为空" |

---

## 三、阶段 3：完成数据与浏览器闭环

对应被参考文档"第六节 阶段 3"、P1-12、P1-13、P2-4、P2-5。

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| 浏览器用例可真正运行 | `frontend/scripts/e2e-server.mjs`、`frontend/scripts/e2e-run.mjs`（新增）、`frontend/package.json`、`frontend/playwright.config.ts`、`.github/workflows/ci.yml` | CI 没有 `data/`（约 210 MB 不入 Git），浏览器用例因此从未跑起来。**改法**：加一个桩后端（`/api/health`、`/api/dicts`、`/api/demo/examples`、`/api/query` 的完整 SSE 事件序列）+ 运行器 `npm run test:e2e:offline`，让普通 CI 也能跑 desktop + mobile；`playwright.config.ts` 改用完整 Chromium 的新 headless 模式（`channel: 'chromium'`，避开本机两次中断的 headless-shell 下载）；`ci.yml` 新增 `browser` job 并在失败时上传报告 |
| 焦点陷阱真实生效 | `frontend/src/App.vue`、`frontend/tests/e2e/accessibility.spec.ts` | 新增 Tab/Shift+Tab 循环用例后**测出真实缺陷**：tablist 的 roving tabindex 让"最后一个可聚焦元素"算错，第 2 次 Tab 焦点即逃出抽屉。**改法**：`tabbableIn()` 过滤 `tabIndex < 0`、`disabled`、`aria-hidden`、不可见元素；焦点不在抽屉内时按方向拉回；用例同时断言"焦点确实在动"，避免被吞键造成的假通过 |
| reduced-motion 断言稳健化 | `frontend/tests/e2e/accessibility.spec.ts` | Chrome 把 `0.001ms` 规范化为 `1e-06s`，原断言匹配固定字符串必然失败；改为按秒解析数值比较 |
| Chroma 四方计数 | `scripts/audit_chroma_segments.py` | 修正 manifest 计数读取位置（`manifest["vectors"]["count"]`，原写法永远读到 null 却判"一致"）；补 collection 计数（按该 collection 全部 segment 汇总——实测 embedding 行挂在 METADATA 段，只数 VECTOR 段会得 0）；ids / embeddings / collection / manifest 四者任一缺失或不一致即**退出非零**；`--apply` 后重新审计，保证结论反映清理后的磁盘状态 |
| 血缘父子链与 schema | `scripts/build_lineage.py` | 新增 `demo.parent`（按 `source_run` 真实解析 run 目录 + meta 哈希 + 版本 + `llm_used`）、`checks`（run→demo→runtime 版本一致性、measurement_mode、父 run 是否 latest_run）、`LINEAGE_SCHEMA` 与 `--check` 模式（字段类型/取值正则 + 跨层一致性），供 CI 门禁使用 |

> P2-1（真实 demo 制品）需要一次**付费模型评测**，本次未代跑；因此 `build_lineage.py --check`
> 当前按预期报出 demo 链断裂并以退出码 1 结束——这是设计行为，不是遗漏。

---

## 四、阶段 4：运行时与文档收口

对应被参考文档"第六节 阶段 4"、P0-3、P0-4、P1-5、R5-1、R5-2、R5-5、R5-6、R5-7。

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| 停机顺序与在途任务 | `server/runtime.py`、`server/sse.py`、`server/api.py`、`config/defaults.py`、`config/settings.py`、`.env.example` | 原顺序是"先关外部客户端、再关同步池"，池里在跑的 embedding/LLM 任务会用到已关闭的客户端。**改法**：`SyncWorkPool` 增加 `begin_drain()`（停收新任务 + 撤销全部排队任务）与 `wait_idle(timeout)`（有上限等待在途），`Runtime.shutdown()` 按"停收 → 撤销 → drain → 关客户端"执行，新增 `SHUTDOWN_DRAIN_SECONDS`（默认 10 s）控制上限，收尾结果写入 `meta.shutdown_sync_pool` 并打日志 |
| 同步池自锁死修复 | `server/sse.py`、`tests/test_release_guards.py` | 实现停机撤销时引入的缺陷（新测试抓出）：`Future.cancel()` 会**同步**触发 done 回调，回调再去抢同一把非重入锁 → `note_cancel`/`begin_drain` 死锁，整份测试套件卡住。**改法**：改用 `threading.RLock()` 并写明原因 |
| health 的同步池 schema 稳定 | `server/sse.py`、`server/api.py` | 池未创建时原返回 `{}`，字段随"是否已跑过第一次查询"变化，监控无法写告警规则。**改法**：`empty_sync_pool_stats()` 返回与 `stats()` **完全一致的字段集合**（零值 + `initialized=false` + `draining` + `drained_out`） |
| CLI 版本来源 | `scripts/run_server.py`、`config/settings.py`、`server/runtime.py` | 原实现把 `--version` 写进 `RAG_ACTIVE_VERSION` 就丢了来源，health 显示成 `env_pinned`。**改法**：抽出 `pin_version()`，同时写 `RAG_VERSION_SOURCE=cli_explicit`；settings 增加 `version_source_hint`；`version_source()` 三态可区分（CLI 显式 / 环境固定 / 扫描最新） |
| 生产 CORS 门禁 | `server/api.py`、`config/settings.py`、`config/defaults.py`、`.env.example` | 生产漏配 `CORS_ALLOW_ORIGINS` 时任意站点可跨域调用公开接口。**改法**：**显式生产档**（自己写了 `RAG_REQUIRE_ACTIVE_VERSION=true`）下仍为 `*` 且未确认 → 导入 `server.api` 即抛错（fail-fast）；隐式生产（`run_server --version`）只告警，避免把文档推荐的本地启动命令一起挡掉；新增 `ALLOW_PUBLIC_CORS=true` 作为"确实要公开"的显式确认项；health 的 `warnings` 同步透出 |
| smoke 跨平台输出 | `scripts/smoke_deploy.py` | Windows GBK 控制台下 `print("✓")` 抛 `UnicodeEncodeError`，脚本死在健康检查阶段。**改法**：探测 `sys.stdout.encoding`，不能编码则退化 `[OK]/[FAIL]`，并提供 `_safe_print` 兜底；实测 `PYTHONIOENCODING=gbk` 下不再崩溃 |
| 数据计数单一事实源 | `scripts/check_docs.py`、`docs/current-status.md` | 原 current-status 写"10925 实体 / 17730 关系"，实际为 9925 / 17700。**改法**：修正数字；`check_docs.py` 新增"文档计数与快照/索引清单一致"检查（无 `data/` 时跳过并说明），写错即 CI 失败 |
| 上轮总结口径 | `docs/changes/20260916-round4-review-remediation-summary.md` | 原结论"11 完成、2 部分"与自身表格（8 完成/4 部分/1 未完成）矛盾。**改法**：加口径修正说明并写明复核重裁定（2 完成/8 部分/3 未完成），保留原表以便对照（R5-3） |
| 文档同步 | `docs/current-status.md`、`docs/deploy.md`、`README.md`、`scripts/README.md` | current-status 更新数字、门禁实测表与未闭环清单；deploy 补新环境变量、health 字段解读、依赖安装、发布打包与门禁章节；README 不再硬编码用例数（指向唯一事实源）；scripts/README 补 7 个新脚本 |

---

## 五、文件级修改清单

### 5.1 修改的文件（27 个）

| 文件 | 改了什么 | 对应审核项 |
| --- | --- | --- |
| `.env.example` | 新增 `ALLOW_PUBLIC_CORS`、`SHUTDOWN_DRAIN_SECONDS`、`RAG_VERSION_SOURCE` 说明 | R5-5、P0-3、P0-4 |
| `.github/workflows/ci.yml` | 3.9 测试矩阵 → `syntax-floor`；新增 `browser` job；lint 加锁哈希门禁；按锁安装（有 hashes 才 `--require-hashes`）；补 `compileall` | P2-6、P1-12、P2-7 |
| `.github/workflows/release.yml` | 整体重写：版本解析、数据制品哈希+安全解包、锁哈希强制、smoke 硬门禁、demo 硬门禁、证据链生成与校验、clean 检查、release 包、退出日志 | P2-7、P2-1、R5-4 |
| `.gitignore` | 忽略 Playwright 产物、`release/`、`release.tar.gz*`、`.tmp/` | R5-4、P2-7 |
| `README.md` | 用例数不再硬编码，指向 current-status；补第五轮内容 | R5-2、R5-3 |
| `config/defaults.py` | 新增 `ALLOW_PUBLIC_CORS`、`SHUTDOWN_DRAIN_SECONDS` | R5-5、P0-3 |
| `config/settings.py` | 新增 `version_source_hint`、`require_active_version_explicit`、`allow_public_cors`、`shutdown_drain_seconds` 与 `cors_startup_problem()`/`cors_warning()`；补范围校验 | P0-4、R5-5、P0-3 |
| `docs/current-status.md` | 数字修正（9925/17700/9544）；门禁实测表；未闭环诚实清单；文档入口 | R5-2、P2-1 |
| `docs/deploy.md` | 环境变量、health 字段、依赖安装、发布打包与门禁、故障表两条 | R5-5、P0-3、P2-6、P2-7 |
| `docs/changes/20260916-round4-review-remediation-summary.md` | 口径修正与复核重裁定说明 | R5-3 |
| `frontend/package.json` | 新增 `test:e2e:offline`、`e2e:server` 脚本 | P1-12 |
| `frontend/playwright.config.ts` | `channel: 'chromium'`（不依赖 headless-shell 下载） | P1-12 |
| `frontend/src/App.vue` | `tabbableIn()` 焦点陷阱修复 | P1-13 |
| `frontend/tests/e2e/accessibility.spec.ts` | 新增 Tab/Shift+Tab 焦点陷阱用例；reduced-motion 断言按数值 | P1-13 |
| `requirements.lock` | 重新锁定（与实测版本一致）+ 全量 `--hash` | P2-6 |
| `requirements-dev.lock` | 同上 | P2-6 |
| `scripts/README.md` | 补 7 个新脚本条目 | P2-3…P2-7、R5-2 |
| `scripts/audit_chroma_segments.py` | manifest 计数读取修正、collection 计数、四方一致判定、非零退出、`--apply` 后重审计 | P2-5 |
| `scripts/build_artifact_manifest.py` | 物理 `SHA256SUMS` + `LOGICAL_HASHES.json`、`verify-sums`、`_git()` strip、排除 `smoke_release.json` 自登记 | P2-3、R5-7 |
| `scripts/build_lineage.py` | `demo.parent`、`checks`、`LINEAGE_SCHEMA`、`validate_lineage()`、`--check` | P2-4 |
| `scripts/check_docs.py` | 新增数据计数一致性检查 | R5-2 |
| `scripts/run_server.py` | 抽出 `pin_version()`，写 `RAG_VERSION_SOURCE` | P0-4 |
| `scripts/smoke_deploy.py` | 编码安全输出（`[OK]/[FAIL]` + `_safe_print`） | R5-1 |
| `server/api.py` | CORS 门禁与启动告警、`warnings` 列表化、lifespan 收尾分支 | R5-5、P0-3 |
| `server/runtime.py` | shutdown 顺序（先收池再关客户端）、`drain_seconds`、`version_source` 支持来源声明 | P0-3、P0-4 |
| `server/sse.py` | `RLock`、`draining` 停收、`begin_drain()`/`wait_idle()`/`_forget()`、零值 stats、`shutdown_sync_pool()` 返回观测快照 | P0-3、P1-5、R5-6 |
| `tests/test_release_guards.py` | 追加 12 条（停机顺序、排队撤销、零值 schema、版本来源、CORS 门禁 5 组参数+告警+导入失败、smoke 符号） | 对应各项 |

### 5.2 新增的文件（9 个）

| 文件 | 用途 | 对应审核项 |
| --- | --- | --- |
| `scripts/fetch_data_artifact.py` | 数据制品打包 / 下载 / 预期哈希校验 / gpg 验签 / zip-slip 与范围检查的安全解包 | P2-7 第 7 条 |
| `scripts/gen_sbom.py` | 生成并自检 SPDX 2.3 SBOM（Python + Node） | P2-7 第 6 条 |
| `scripts/build_release_bundle.py` | 组装完整 release 包（源码 + 数据 + dist + 证据 + SBOM + 依赖） | P2-7 第 5 条 |
| `scripts/lock_hashes.py` | 给锁文件补 `--hash`（PyPI JSON API，不下载制品）；`--check` 供 CI | P2-6 |
| `frontend/scripts/e2e-server.mjs` | 浏览器验收用的桩后端（无 `data/` 也能跑） | P1-12 |
| `frontend/scripts/e2e-run.mjs` | 离线浏览器验收运行器（起桩服务 → 跑 Playwright → 收尾） | P1-12 |
| `tests/test_release_pipeline.py` | 发布链路脚本守护用例 16 条（SHA256SUMS/四方计数/lineage/SBOM/安全解包/文档计数） | P2-3、P2-4、P2-5、P2-7、R5-2、R5-7 |
| `docs/changes/20260916-round5-review-remediation-summary.md` | 本轮结论对照（本文档的配套） | — |
| `docs/changes/20260916-round5-remediation-change-note.md` | 本文档：修改说明与参考依据 | — |

> 另有一份 `docs/changes/20260916-round5-remediation-review-and-full-project-audit.md`
> 是**审核方提供的输入文档**（本次修改的依据），不是本次修改的产物。

---

## 六、验证方法与实测结果

全部命令在 `RAG/` 根执行；括号内为 2026-09-16 本机实测结果。

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `python -m pytest tests -q` | **278 passed**（原 250，+28） |
| 发布链路脚本用例 | `python -m pytest tests/test_release_pipeline.py -q` | 16 passed |
| 编译 | `python -m compileall server config contracts lib data scripts evaluation tests` | 退出码 0；另用 `ast.parse(feature_version=(3,9))` 复核语法下限通过 |
| 前端 | `cd frontend && npm run verify` | 43 passed（27 unit + 16 component）+ build + 体积门禁通过 |
| 浏览器（桩后端） | `cd frontend && npm run test:e2e:offline` | **12 passed / 2 skipped**（desktop + mobile） |
| 浏览器（真实服务） | `RAG_BASE_URL=http://127.0.0.1:8125 npm run test:e2e` | **12 passed / 2 skipped**（真实数据 + 真实模型） |
| 契约端到端 | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项全过 |
| 版本来源（P0-4） | 启动服务后 `curl /api/health` | `version_selection=cli_explicit` |
| 同步池 schema（R5-6） | 首次 `curl /api/health` | `sync_pool` 字段齐全（`initialized=false`，无空对象） |
| smoke 跨平台（R5-1） | `PYTHONIOENCODING=gbk python scripts/smoke_deploy.py ...` | 输出 `[OK]/[FAIL]`，不再崩溃（demo 未重建故退出码 1，属预期） |
| 制品清单 | `build_artifact_manifest.py verify` | **37/37 一致** |
| 物理校验和 | `build_artifact_manifest.py verify-sums` | **38 个文件全部一致** |
| Chroma 四方计数 | `audit_chroma_segments.py --version 20260915_v1` | ids=embeddings=collection=manifest=**9544**，退出码 0 |
| 血缘 | `build_lineage.py --version ... --check` | 各层哈希齐全；demo 链**按预期**报不一致（P2-1 未做） |
| SBOM | `gen_sbom.py generate` + `validate` | SPDX 2.3，306 个包，自检通过 |
| 依赖锁 | `lock_hashes.py --check` + `pip install --dry-run --require-hashes -r requirements-dev.lock` | 全部需求带 hash；dry-run 通过；抽样（chromadb/numpy/openai）实际下载校验哈希一致 |
| 文档 | `check_docs.py --strict` | 通过（链接 / current 口径 / `.env.example` / 数据计数） |
| 密钥 | `check_secrets.py` | 未发现明文密钥 |
| workflow 语法 | `yaml.safe_load` + 对每个 `run:` 块执行 `bash -n` | 两个 workflow 解析通过，全部 shell 块语法通过 |
| Playwright 产物（R5-4） | `git check-ignore -v frontend/test-results/...` | 命中 `.gitignore:46`，`git status` 无测试痕迹 |

---

## 七、最终发布门禁对账（对应被参考文档第七节 17 条）

| 门禁条目 | 本次状态 |
| --- | --- |
| `ci` 所有 jobs 绿色 | ⚠️ 本地等价命令全绿；远端 run 需推送后确认 |
| `release.yml` YAML 合法并成功创建 jobs | ✅ 本地解析 + shell 语法校验通过；远端 job 创建需推送后确认 |
| Python lock 可在所有声明版本安装 | ✅ 只声明 3.11（唯一声明版本），dry-run 安装通过 |
| 后端全量测试通过 | ✅ 278 passed |
| 前端 unit/component/build/bundle 通过 | ✅ `npm run verify` |
| Playwright desktop/mobile 通过 | ✅ 12 passed / 2 skipped（桩后端与真实服务各一次） |
| demo API 返回 200 | ❌ 未完成（需真实模型 run 重建 demo，P2-1） |
| smoke 退出码 0 且失败不能被忽略 | ⚠️ 失败不再被忽略（`\|\| true` 已移除）；退出码 0 依赖 demo 重建 |
| manifest 与标准 SHA256SUMS 均通过 | ✅ 37/37 + 38 文件物理校验 |
| Chroma 四方计数一致 | ✅ 9544 四方一致 |
| lineage 中 run→demo→runtime 一致 | ❌ 依赖 demo 重建（检查逻辑已就绪并会如实报错） |
| release artifact 含源码/dist/数据/eval/SBOM 等 | ✅ 组装脚本就绪并本地演练通过（含对失败 smoke 报告的拒绝） |
| 下载数据制品有哈希/签名验证与安全解包 | ✅ `fetch_data_artifact.py` |
| clean commit / tested commit / evidence commit 明确 | ⚠️ 机制已写入文档；本次改动未提交，故本地证据为 `git_dirty=true` |
| current-status 与实际 health/CI 一致 | ✅ 数字与清单机械核对（`check_docs.py --strict`） |
| 未修改或提交 `RAG/new/` | ✅ `git status` 无 `new/` 变更 |
| 无密钥进入仓库或发布包 | ✅ `check_secrets.py`；`git archive` 天然排除未跟踪/忽略文件 |

---

## 八、未完成与未验证（不夸大）

1. **P2-1 真实 demo**：需要一次付费模型评测（`run_evaluation.py run` +
   `gen_demo_examples.py --measure`），本次未执行；`/api/demo/examples` 仍诚实返回 503，
   lineage 的跨层一致性检查因此仍失败（设计如此）。
2. **远端 CI/release 绿色**：本次改动尚未推送，所有结论来自本地复现；
   第四轮的教训正是"文件存在 ≠ 流水线可用"，所以不在本地宣称 CI 已绿。
3. **干净 commit 上的完整发布**：发布证据生成链与打包脚本已本地演练，
   但最新一轮证据是在含未提交改动的工作区上生成的（`git_dirty=true`）；
   正式发布应先提交，再按 `docs/deploy.md` 第九节的顺序重跑。
4. **真实反代/生产环境**：CORS、`X-Forwarded-For` 可信代理、SSE 缓冲等仍需在真实反代下复核。

## 九、追溯方式（从审核条目反查改动）

1. 打开被参考文档，取条目编号（如 `P2-5`、`R5-1`）；
2. 在本文第五节"文件级修改清单"的"对应审核项"列检索该编号，得到文件清单；
3. 以文件为线索看代码内注释——所有本次改动都写明了"第五轮审核 Px-x / Rx-x"及原因；
4. 运行第六节对应行的命令复现结论；
5. 若该条目被判"未完成/未验证"，见第八节的原因与前置条件。
