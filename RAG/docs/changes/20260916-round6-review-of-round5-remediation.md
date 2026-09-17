# 20260916 第六轮独立复核：第五轮整改修改说明核验与全项目增量清查报告

> 文档类型：独立整改复核 + 全项目增量清查（审核方产出，本轮不修改业务代码，只新增本报告）。
> 审核日期：2026-09-16。
> 项目根目录：`F:\python\python_space\china-war\RAG`。
> 审核分支：`round4-review-remediation`（第五轮整改的全部改动停留在工作区，未提交）。
> 审核范围：整个 `RAG/`，严格排除 `RAG/new/`。
> 上游追溯链：`20260916-round4-review-remediation-work-order.md` → `20260916-round5-remediation-review-and-full-project-audit.md`（第五轮审核）→ `20260916-round5-remediation-change-note.md`（第五轮修改说明，**本报告的被审核对象**）→ `20260916-round5-review-remediation-summary.md`（第五轮结论对照）→ 本报告。

---

## 〇、本报告根据什么文档进行审核分析

**被审核文档（唯一核验对象）：**

| 项 | 内容 |
| --- | --- |
| 被审核文档 | [`20260916-round5-remediation-change-note.md`](20260916-round5-remediation-change-note.md) |
| 文档性质 | 第五轮复核整改的**修改说明**（修改方产出）：改了哪些文件、依据哪条审核要求、如何验证 |
| 被审核 HEAD / 工作区 | 分支 HEAD `2e06aea`，其上叠加 28 个已修改文件 + 10 个未跟踪文件（共 38 处未提交变更） |

**验收标准（判断"改没改好"的依据）：**

| 项 | 内容 |
| --- | --- |
| 验收依据 | [`20260916-round5-remediation-review-and-full-project-audit.md`](20260916-round5-remediation-review-and-full-project-audit.md) |
| 具体取用 | 第一节 7 条发布阻断、第三节 13 项重裁定（P0-3/P0-4/P1-5/P1-12/P1-13/P2-1/P2-3/P2-4/P2-5/P2-6/P2-7 及已完成项）、第四节 R5-1…R5-7、第七节 17 条发布门禁 |
| 配套对照 | [`20260916-round5-review-remediation-summary.md`](20260916-round5-review-remediation-summary.md)（第五轮整改方的结论对照）、[`../current-status.md`](../current-status.md)（当前状态唯一事实源） |

**审核方法（三步）：**

1. **文档比对**：把修改说明的每条声称与其宣称依据的审核验收标准逐条对齐，找"声称大于实际"的表述；
2. **代码核验**：四路并行清查——① 服务端（runtime/sse/api/settings/run_server，对应 P0-3、P0-4、P1-5、R5-5、R5-6）；② 发布链路（两个 workflow、五份锁文件、fetch_data_artifact/gen_sbom/build_release_bundle/lock_hashes/build_artifact_manifest/build_lineage/audit_chroma_segments/smoke_deploy、test_release_pipeline）；③ 前端（e2e 桩与运行器、playwright 配置、App.vue 焦点陷阱、accessibility 用例）；④ 全项目增量清查（文档口径、数据制品、脚本目录、测试目录、散落文件）；
3. **本机复现**：对可机械验证的结论亲自重跑（见 2.1），不采信文档单方陈述。

---

## 一、总体结论

**第五轮修改说明基本诚实，修复主体真实可用，质量明显高于前几轮的"纸面修复"；但按当前状态推送必失败、发布链路结构性必红，且部分修复自身带进了新缺陷。**

判定拆开说：

- 核心代码（问答链路、契约、同步池、前端组件）：**可信**，与前几轮审核结论一致；
- 发布工程代码（workflow、脚本、锁文件）：**基本可信**，本报告核验未发现方向性造假，但存在"验证工具与验收标准不一致"类缺陷（见 B1、B3、B5）与新引入的并发问题（见 B2）；
- 发布流程本身：**不可用**——改动未提交（且关键文件未进 git 索引）、demo 门禁在 P2-1 完成前必然失败、修复未经过一次端到端发布演练。

因此本报告的发布判定与第五轮审核一致：**当前不具备发布条件**，且新增一条：**当前状态连"推送后让远端验证"都做不到**（见第三节硬事实 1）。

---

## 二、对修改说明声称内容的核验结果

### 2.1 本机复现通过的声称（不采信文档，亲自重跑）

| 声称 | 复现方式 | 结果 |
| --- | --- | --- |
| 后端全量 278 passed | `python -m pytest tests -q`（Python 3.11.15） | **278 passed in 14.72s**，属实 |
| 两个 workflow YAML 合法 | `yaml.safe_load` 逐文件解析 | 均通过；ci.yml jobs = backend/syntax-floor/frontend/browser/lint，release.yml jobs = release-gate，与声称一致 |
| 锁文件补齐 `--hash` | 脚本化统计两份 lock | requirements.lock 93 条、requirements-dev.lock 99 条需求**全部**带 `--hash=sha256:`；`aiohappyeyeballs` 无命中；锁头注明 Python 3.11 生成 |
| SHA256SUMS 存在且为物理哈希 | `file` + 抽查 | 文件存在、38 条目、哈希值本身正确；**但发现 CRLF 行尾缺陷，见 B1** |
| 整改未提交 | `git status --porcelain -- ':!new'` | 38 处变更，与修改说明第八节自述一致 |

### 2.2 代码核验确认真实的修复（抽查实现与声称一致）

| 项 | 核验结论 |
| --- | --- |
| P0-3 停机顺序 | `server/runtime.py:80-109` 顺序为「停收 → 撤销排队 → 有上限 drain → 再关外部客户端」，与声称一致；`SHUTDOWN_DRAIN_SECONDS` 默认 10 s、范围校验 0–120 均落实。**但 drain 实现有并发缺陷，见 B2** |
| R5-1 smoke 编码 | `smoke_deploy.py:39-63` 编码探测 + `[OK]/[FAIL]` 回退 + `_safe_print` 兜底，且有直接测试（test_release_guards.py:1176-1196） |
| R5-5 CORS 门禁 | `server/api.py:79-87` 模块级评估，导入即抛错；有子进程端到端测试；通配符判断与 CORSMiddleware 语义一致。**残留收口缺口见 B8** |
| R5-6 sync_pool schema | `stats()` 与 `empty_sync_pool_stats()` 逐字段对比**完全一致**（16 字段），测试显式断言 `set(live) == set(empty)`。**错误态（rt 为 None）仍有缺口，见 B8** |
| R5-7 commit strip | `_git()` 统一 `.strip()`，唯一调用点生效，有测试 |
| P2-5 四方计数 | `audit_chroma_segments.py` 正确读取 `manifest["vectors"]["count"]`，collection 计数按 segment_id 汇总不会重复计数，四方任一缺失/不一致退出非零，测试覆盖 |
| P2-3（逻辑层） | `SHA256SUMS` 写物理哈希、`LOGICAL_HASHES.json` 带 `hash_mode` 说明、`verify-sums` 子命令存在——逻辑正确，**行尾缺陷见 B1** |
| P2-4（框架层） | `demo.parent`、`checks`、`LINEAGE_SCHEMA`、`--check` 均存在且 fail-closed。**判定缺口见 B3** |
| P1-12 离线浏览器链路 | 桩后端四端点齐全、SSE 契约满足前端消费、运行器正确处理 Windows 下 Playwright 调用与子进程收尾；`channel: 'chromium'` 属实。**假绿风险见 B6** |
| P1-13 焦点陷阱 | `App.vue:55-67` 过滤 `tabIndex<0`/disabled/aria-hidden/不可见元素，主根因（roving tabindex 误算）已修；用例逐次断言焦点轨迹，非"按键即过"。**reduced-motion 用例空转，见 B5** |
| P2-6 | 只声明 3.11（matrix 单值），3.9 降级为仅 compileall 的 syntax-floor；`lock_hashes.py --check` 进入 lint job |
| P2-7（主体） | release.yml 无内联 heredoc、门禁顺序自洽（数据制品 → 依赖 → 测试 → Playwright → smoke → 契约 → demo → 证据 → clean 检查 → 打包）、`|| true` 已移除；SBOM 为字段完整的 SPDX 2.3 且 validate 非走过场；fetch 四道关（哈希必需/验签失败即拒/zip-slip/体积限制）实现属实。**放行口子见 B7** |
| test_release_pipeline.py | 16 条与声称数目精确一致，真跑被测逻辑、强断言，无 mock 掉被测逻辑的假测试 |

### 2.3 记账出入（小）

- 修改说明第五节 5.1 列出"修改 27 个文件"，git 实际为 **28 个**：`docs/README.md` 也被修改（新增第五轮文档索引段落）但未列入清单。无实质影响，属登记遗漏。

---

## 三、修改说明没有讲透的两个硬事实

### 硬事实 1：六个被 workflow 直接引用的文件未进 git 索引——现在推送必然失败

`git status` 显示以下文件为**未跟踪（??）**状态，而它们被 workflow 逐字引用：

| 未跟踪文件 | 引用位置 |
| --- | --- |
| `scripts/lock_hashes.py` | `ci.yml:129`（lint job 的 `lock_hashes.py --check` 门禁） |
| `frontend/scripts/e2e-run.mjs`、`e2e-server.mjs` | `frontend/package.json:16`（`test:e2e:offline`）+ `ci.yml:111`（browser job） |
| `scripts/fetch_data_artifact.py` | `release.yml:95`（数据制品下载） |
| `scripts/gen_sbom.py` | `release.yml:170`（SBOM 生成/校验） |
| `scripts/build_release_bundle.py` | `release.yml:186`（发布打包） |
| `tests/test_release_pipeline.py` | `ci.yml:42`（测试收集；缺失时 CI 静默少跑 16 条守护用例，"278"对不上） |

后果分两种姿势：**现在直接 push**，GitHub 执行 HEAD 上的旧 workflow（第五轮审核判定的不可用版本）；**只提交 workflow、漏提交脚本**，runner checkout 后报"文件不存在"，照样失败——第四轮"文件存在 ≠ 流水线可用"的教训会换个姿势复发。修改说明第八节只写了"改动尚未提交"，没有点出引用与文件会脱节这个具体死法。

### 硬事实 2：P2-1 完成前，release 门禁结构性必红

`release.yml:156-163` 硬性要求 `/api/demo/examples` 返回 200；而 `data/eval/20260915_v1/demo_examples.json` 的 `version` 仍为 `20260904_v2`、`source_run` 指向 `run_20260913_postaudit`（该 run 目录不存在，runs/ 下只有 `v5_coords_v1`），服务端按版本一致性校验返回 503。这意味着：

- 修改说明第七节对账表中所有依赖 demo 的门禁（demo 200、smoke 退出码 0、lineage 跨层一致）在付费评测执行前**全部无法变绿**，不是"差一项"而是"发布验收整体被阻塞"；
- 修复方没有做过一次端到端发布演练——哪怕本地把 release-gate 的等价命令序列完整走一遍，也会立刻发现 demo 链断裂挡在打包之前。修改说明第六节的验证表是**逐命令分散复现**，不是链路复现。

---

## 四、上一轮问题仍未闭合清单（原因 + 下次改法）

分两类：A 类 = 外部条件未满足（代码就绪、等前置动作）；B 类 = 修复自身有缺陷（代码要再改）。

### A 类：外部条件未满足（3 项）

| 项 | 现状 | 原因 | 下次怎么改 |
| --- | --- | --- | --- |
| P2-1 真实 demo 制品 | `/api/demo/examples` 仍 503，demo_examples.json 为旧版本 | 需一次付费模型评测，第五轮明确未代跑（处理方式诚实） | 维护者执行 `run_evaluation.py run` + `gen_demo_examples.py --measure`（真实 LLM），重建后重跑血缘、清单、打包，并做一次 release-gate 全链路演练 |
| 远端 CI/release 绿色 | 从未推送 | 改动未提交（见硬事实 1） | 按第六节顺序提交**全部**文件后推送，以远端实际 run 为最终证据 |
| 干净 commit 上的发布 | 证据 `git_dirty=true` | 同上 | 提交后按 deploy.md 第九节重跑 `--require-clean` 链路 |

### B 类：修复自身有缺陷（8 项，均为本报告新发现）

#### B1.（P2-3 只改对一半）SHA256SUMS 是 CRLF 行尾，标准 `sha256sum -c` 在 Linux 上 38 行全失败

- **证据**：`file data/release/SHA256SUMS` 实测输出 "ASCII text, with CRLF line terminators"；根因是 `scripts/build_artifact_manifest.py:390` 用 `write_text(...)` 写文件，Windows 文本模式把 `\n` 翻译成 `\r\n`。
- **为什么本地"实测通过"**：脚本自带 `verify-sums` 用 `splitlines()` 解析，能容忍 `\r`，于是 38/38 一致的实测结果掩盖了问题；而修改说明与 current-status 声称的"`sha256sum -c` 语义成立"在 Linux 标准工具下不成立。
- **根因模式**：验证工具与验收标准不是同一个。
- **下次改法**：写入时指定 `newline="\n"`（或二进制写入）；重建 `data/release/SHA256SUMS`；补一条字节级测试断言文件不含 `\r`。

#### B2.（P0-3 修复引入的并发缺陷）drain 阻塞事件循环 + submit 窗口竞态

- **证据 1**：`server/sse.py:174-181` 的 `wait_idle` 用 `time.sleep(0.02)` 同步轮询，而它从 async 的 `Runtime.shutdown()`（`server/runtime.py:85`）直接调用——停机期间事件循环最长被卡死 `SHUTDOWN_DRAIN_SECONDS`（默认 10 s，可配至 120 s），所有在收尾的 SSE 流全部冻结。
- **证据 2**：`submit` 在 `executor.submit` **之后**才把 future 登记进 `_futures`（`sse.py:120-128`，两段各自持锁）；若 `begin_drain` 的快照落在两步之间，该任务不会被撤销，而 `wait_idle` 只看 `_active` 不看 `_queued`，可能提前判空。兜底 `pool.shutdown(cancel_futures=True)` 覆盖大多数情形，但存在任务恰在 wait_idle 返回后、shutdown 执行前被 worker 捞起的窗口——与原 P0-3 同性质，且无告警。
- **根因模式**：修复引入的新缺陷未被集成测试覆盖（没有"真实停机时有在途任务"的并发测试路径覆盖事件循环侧）。
- **下次改法**：drain 改为可等待（async 路径用 `asyncio.sleep` 轮询，或 `asyncio.to_thread` 下沉）；`submit` 先在锁内登记 future 再提交执行器（拒绝路径再移除）；`wait_idle` 同时校验 `_queued == 0`；补"存在 active/queued 任务时 async shutdown"的集成测试。

#### B3.（P2-4 仍断一个维度）血缘索引版本检查"只记录不判定"，且 measurement_mode 形同虚设

- **证据 1**：`scripts/build_lineage.py:215-216` 计算了 `parent_run_index_version_matches`，但其下的 problems 分支（219-233 行）没有任何一处对它判定——demo 父 run 的索引版本与 runtime 版本不一致时不产生 problem，"run→demo→runtime 一致"在索引版本维度是断的。
- **证据 2**：`gen_demo_examples.py` 的 payload **不写** `measurement_mode` 字段，导致 lineage 的 `demo_is_real_llm` 恒为 False，"demo 来自真实模型 run"在血缘里无法判定（当前恰好因 P2-1 未做而无矛盾，但机制是空的）。
- **下次改法**：`index_version` 不一致时产出 problem；`gen_demo_examples.py --measure` 写入 `measurement_mode=real_llm`；补 lineage schema 对应字段的校验。

#### B4.（P0-4 残留误标路径）版本来源在"不传 --version"时会沿用遗留值

- **证据**：`scripts/run_server.py` 的 `pin_version()` 在 version 为空时直接 return，不清理环境里遗留的 `RAG_VERSION_SOURCE=cli_explicit`；此时实际是 env/scan 选版，health 却可能错标 `cli_explicit`。另外 `version_source()`（runtime.py:185-187）盲信 hint、不与 `RAG_ACTIVE_VERSION` 交叉校验；非法取值静默回落。
- **下次改法**：不传 `--version` 时显式清掉或改写该变量；hint 与实际选版不一致时以实际行为为准并告警；非法取值在 settings.validate 报错。

#### B5.（P1-13 用例空转）reduced-motion 断言测了一个没有过渡的元素

- **证据**：用例解析逻辑正确（按秒数值比较），但断言对象 `.qa-topbar` 在整个前端**没有任何 transition 声明**（styles.css 中仅有的过渡在 toast 进出场），因此即使删掉 reduced-motion 块该用例照样通过——它测不出回归。
- **根因模式**：同 B1，"测试存在"被当成了"验收有效"。
- **下次改法**：断言改到真实有过渡的元素（如 toast 容器），或给抽屉/蒙层补一条真实过渡后再测。

#### B6.（P1-12 假绿风险）e2e 运行器对端口占用无防护

- **证据**：`frontend/scripts/e2e-run.mjs` 起桩后只探测"8125 端口上有没有 /api/health"，不监听子进程的 `error`/`exit` 事件；若端口被上次残留的桩或其他服务占用，会静默对着错误的服务跑绿（或超时报错误导）。
- **下次改法**：health 校验桩的特征字段（桩已带 `version: 'e2e-fixture'`）；监听子进程退出事件并及时失败。

#### B7.（P2-7 两处放行口子）打包脚本的 smoke 豁免与验签未闭环

- **证据 1**：`build_release_bundle.py` 在 smoke 报告缺失或 `exit_code=None`（旧格式）时仅 warning 放行——手动跑 bundle 可绕过 smoke 门禁（release 链路内因 smoke 步骤在前不受影响）。
- **证据 2**：`release.yml:89-96` 下载签名后调用 `fetch_data_artifact.py` 时**未传 `--pubkey`**，验签依赖 runner keyring 里恰好有公钥，信任链未闭合。
- **下次改法**：报告缺失/无法解析时硬拒绝；workflow 显式传公钥，或不声称支持验签。

#### B8.（R5-5 / R5-6 收尾缺口）空串通配与错误态 schema

- **证据 1**：`CORS_ALLOW_ORIGINS=""`（显式空串）经 `or ["*"]` 兜底**静默变成通配符**，错误配置不会被发现；隐式生产档只告警是有意收窄，但意味着真实生产漏配 `RAG_REQUIRE_ACTIVE_VERSION` 时门禁不生效。
- **证据 2**：health 在 runtime 加载失败（`rt is None`）分支提前 return（`server/api.py:383-392`），返回体没有 `sync_pool`/`rate_limit`/`cache` 键——错误态下 schema 仍不稳定。
- **下次改法**：空串视为配置错误并报错；错误态 health 也返回与正常态字段一致的零值对象。

### 未闭合原因的模式归纳

上一轮"没改好"的原因可归为三种模式，下一轮整改应针对模式而非单点：

1. **外部条件类**（A 类）：付费模型、远端流水线确实无法本地满足——处理方式是诚实的，改法是补动作而非补代码；
2. **验证工具 ≠ 验收标准**（B1、B3、B5）：本地"全绿"由自建工具或空转用例产生，与审核方采用的标准工具/语义不一致——改法是每条门禁至少有一个"用标准语义验证"的测试（字节级文件断言、真实浏览器过渡、problem 级判定）；
3. **修复引入新缺陷且无集成测试**（B2、B4、B6）：并发、环境残留、端口竞争这类场景没有自动化覆盖——改法是补"真实场景"集成测试（真实停机、环境变量残留、端口占用）。

---

## 五、清单之外的增量发现（全项目清查，均不属前五轮问题清单）

### 高

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| G1 | `requirements.lock:4` 把阿里云镜像写成全局 `--index-url`，GitHub 海外 runner 执行 `--require-hashes` 安装时全部流量走该镜像，慢且可能限流，给远端 CI 引入仓库外可用性依赖 | `requirements.lock:4`、`ci.yml:33`、`release.yml:102`；deploy.md 安装说明未提示差异 |

### 中

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| Z1 | 前端完全没有 ESLint（无配置、无依赖、无 script），CI lint 只覆盖 Python | frontend/ 目录与 package.json |
| Z2 | 测试覆盖缺口：`build_release_bundle.py`（含 3 处拒绝分支）与 `lock_hashes.py` 零测试；`/api/demo/examples` 的 503 路径无测试走到；audit 的 `--apply` 重审计路径无测试 | tests/ 目录 |
| Z3 | `build_runtime` 半途失败泄漏 embedding 客户端：`runtime.py:219` 先建客户端，后续任一加载步骤抛错时局部 `rt` 被丢弃，连接池永不释放 | server/runtime.py:219-230、server/api.py:46-49 |
| Z4 | SSE 真实链路仍有多处同步 CPU 调用未下沉线程（F05 融合装配、`_text_shares_any` 全量分词、引用构建、缓存写入），属 P0-1 同类遗留 | server/sse.py:353-361、412-417、485-486、517、624-637 |
| Z5 | e2e 桩的 SSE 事件顺序与真实后端不一致（graph_results 插在 status(text_search) 之前），注释却声称"与 server/sse.py 产出顺序一致"——前端按类型消费故无害，但注释会误导下一个审核者 | frontend/scripts/e2e-server.mjs:170、176-181 对照 server/sse.py:453-494 |

### 低（择要）

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| D1 | `_stream_with_heartbeat` 用子串 `'"type": "answer"' in frame` 判答案帧，用户可控字段含该字面量时超时终态误判；应改用解析后的 type 字段 | server/api.py:566 |
| D2 | SBOM 三处口径：`documentNamespace` 每次生成随机 uuid4（构建不可重现）；npm scoped 包 purl 未按规范编码（`@vue/test-utils` 应为 `%40vue`）；读 dev-lock 而部署实际用 lock | scripts/gen_sbom.py:60、105、150 |
| D3 | `scripts/README.md:39-43` 示例引用从未存在的版本 `20260904_v3`（实际只有 20260904_v2 与 20260915_v1） | scripts/README.md:39-43 |
| D4 | manifest 的 `git_commit` 为全哈希、lineage 为 `sha12@branch`，两份发布证据口径不一致，交叉核对需换算 | build_artifact_manifest.py / lib/release_info.py:44-71 |
| D5 | `test_release_pipeline.py:346` 有恒真断言（`sha256_file(evil) == sha256_file(evil)`）；同文件另一测试直接改写真实 `docs/current-status.md`（try/finally 恢复），中途失败会留脏文件污染 clean 门禁 | tests/test_release_pipeline.py:346、386-401 |
| D6 | release 流程不复跑 `check_secrets.py`（仅 ci 跑）；数据制品 URL 未强制 https（有 sha256 兜底，机密性无保障） | release.yml |
| D7 | `api.py:11` docstring 硬编码本机 anaconda 解释器路径；`logs/` 下临时调试脚本与根目录 `ragv3-render-check.json` 建议清理（均已被 gitignore，无入库风险） | server/api.py:11、logs/、根目录 |

### 明确未发现问题的区域

清查过且可放心的区域，后续轮次不必重复怀疑：current-status 数字与 manifest 逐字一致、无跨文档矛盾；tests 无假 skip、数据依赖全部有 skipif 守护、CI 不会因缺 `data/` 假失败；server/config/contracts/lib 无 TODO/FIXME、无裸 except（3 处 `except Exception: pass` 均为清理路径且带 noqa）；.gitignore 对全部测试产物生效；22 个脚本在 scripts/README 全有记载、无未记载脚本；`node_modules`/`dist`/测试产物均未入库。

---

## 六、下一轮整改建议顺序

1. **修 B1（SHA256SUMS 行尾）**：一行写入修复 + 一条字节级测试，重建清单——这是"验证工具 ≠ 验收标准"教训最直接的纠正；
2. **修 B2 / B4**（drain 异步化 + submit 登记顺序；版本来源清理与交叉校验），各补一条集成测试（真实停机场景、环境变量残留场景）；
3. **修 B3（血缘两处判定补齐）**：problem 化 index_version、写入 measurement_mode——等 P2-1 完成后血缘才能整体闭合；
4. **修 B5 / B6 / B7 / B8**：空转用例换目标元素、运行器防假绿、打包硬拒绝、CORS 空串报错——均为小改动；
5. **处理 G1**：CI 改用官方 PyPI，镜像只留给本机；
6. **提交流程**：提交前用 `git status` 核对第三节表格中 6 个未跟踪脚本一个不少 → 执行 P2-1 付费评测并重建 demo → 本地完整走一遍 release-gate 等价命令序列（链路级，非逐命令）→ 推送 → 以远端实际 run 为最终证据。

---

## 七、本报告的局限

1. 远端 CI/release 从未推送，所有"修复真实"的结论基于本地代码核验与本地复现，不含 GitHub Actions 实际 run 证据；
2. P2-1 付费评测未执行，demo 链路只能验证到"fail-closed 方向正确"为止；
3. Z4（同步 CPU 调用）为静态确认，未做真实负载下的量化测量；
4. 真实反代/生产环境（CORS 实际行为、`X-Forwarded-For` 可信代理、SSE 缓冲）不在本轮范围，与前几轮审核口径一致。

## 八、追溯方式

1. 取第五轮审核条目编号（如 `P2-3`、`R5-5`），在本报告第四节查对应 B 类条目，得"现状—原因—下次改法"；
2. 每条结论均带 `文件:行号`，可直接定位代码；
3. 第二节表格标注了每条声称的核验方式（本机复现 / 代码核验）；
4. 对本报告判定为"未闭合"的条目，修复后应在本报告基础上出第六轮修改说明，并沿用"修改说明（怎么改）+ 结论对照（改到哪）+ current-status（数字唯一事实源）"三文档分工。
