# 20260916 第四轮复核遗留问题整改总结

> 执行依据：[20260916-round4-review-remediation-work-order.md](20260916-round4-review-remediation-work-order.md)
> （13 项遗留问题）。范围：仅 `RAG/`，严格排除 `RAG/new/`。
> 完成日期：2026-09-16。分支 `round4-review-remediation`，验证时 commit `cbedc91`（已推送到 origin）。当前状态数字统一维护在 [../current-status.md](../current-status.md)。

## 一、结论

> **口径修正（2026-09-16 第五轮独立复核）**：本节原写"13 项中 11 项已完成、2 项部分完成"，
> 与下面的逐项表格不符——按表格实际统计为 **8 项完成、4 项部分完成、1 项未完成**；
> 第五轮复核结合动态验证、GitHub Actions 实际状态与全项目静态复核后，进一步裁定为
> **2 项完成、8 项部分完成、3 项未完成**
> （依据：[20260916-round5-remediation-review-and-full-project-audit.md](20260916-round5-remediation-review-and-full-project-audit.md)）。
> 下表保留整改方的**自评**原样以便对照，不代表复核结论；第五轮已按裁定逐项修复，
> 结果见 [20260916-round5-review-remediation-summary.md](20260916-round5-review-remediation-summary.md)。
> 教训：状态统计必须由表格机械汇总而不能手写——两者不一致时，读者会拿到与事实相反的结论（R5-3）。

**回归门禁（本轮执行时的实测值）**：后端 `pytest tests -q` → **250 passed**；前端 `npm test` → **43 passed**
（unit 27 + component 16）；`npm run verify`（typecheck + test + build + bundle 门禁）通过。
（当前数字见 [../current-status.md](../current-status.md)，本段只记录当轮证据。）

## 二、逐项状态

| 编号 | 状态 | 修改文件 | 新增测试 | 验证命令与结果 | 剩余风险 |
| --- | --- | --- | --- | --- | --- |
| P0-3 客户端生命周期 | ✅ 已完成 | `data/index/embeddings.py`、`server/runtime.py`、`server/api.py` | `test_shutdown_closes_embedding_client_once`、`test_shutdown_is_idempotent`、`test_resources_enumerates_all_clients`、`test_embedding_client_close_is_idempotent_and_marks_unavailable` | `pytest tests/test_release_guards.py -q` → 61 passed | 无 |
| P0-4 clean commit 绑定 | ◐ 部分完成 | `server/runtime.py`、`lib/release_info.py`、`.github/workflows/release.yml` | `test_version_source_three_states` | 见第四节"证据绑定"与第五节第 5 条 | 证据文档提交本身产生新 commit，绑定的是产出证据时的代码 commit |
| P1-2 correction 严格契约 | ✅ 已完成 | `contracts/request.py`、`frontend/src/types/contract.ts` | `test_correction_rejects_action_mismatched_fields`（9 组参数化）、`test_correction_entity_id_alias_only_on_add`、`test_correction_replace_and_remove_use_source_and_replacement_ids` | `pytest tests/test_release_guards.py -q` → 通过 | 旧客户端若发送 `replacement` 顶替 `name` 将收到 400（这是本次的显式设计） |
| P1-3 同名实体全链路 | ✅ 已完成 | `contracts/request.py`、`server/query/understand.py`、`server/query/dictionary_matcher.py`、`frontend/src/stores/session.ts`、`frontend/src/types/contract.ts`、`server/generate/cache.py` | `test_apply_corrections_remove_targets_source_id_not_list_order`、`test_apply_corrections_replace_lands_on_replacement_id`、`test_apply_corrections_add_uses_replacement_id`、`test_name_lookup_refuses_to_guess_between_same_name_entities`、前端 `replace 纠正携带源 ID 与用户选中的目标 ID` 等 3 条 | 后端/前端测试均通过 | 词典中同名同类型实体仍需前端回传 ID，纯名称请求按"拒绝猜测"处理 |
| P1-5 同步池观测与预算 | ✅ 已完成 | `server/sse.py`、`config/settings.py`、`config/defaults.py`、`server/api.py` | `test_sync_pool_separates_active_and_queued`、`test_sync_pool_cancel_before_start_frees_queue_slot`、`test_sync_pool_budget_equal_to_deadline_is_rejected` | 同上 | 运行中的同步函数仍无法中断（Python 限制），已用 `running_after_disconnect` 观测 |
| P1-12 前端测试体系 | ◐ 部分完成 | `frontend/vitest.config.ts`、`frontend/playwright.config.ts`、`frontend/tests/unit/*`、`frontend/tests/component/*`、`frontend/tests/e2e/accessibility.spec.ts`、`frontend/package.json`、`.github/workflows/ci.yml` | 43 条前端用例 | `npm run test:unit` → 27；`npm run test:component` → 16；`npm run test:e2e` → 见第五节第 2 条 | Playwright 浏览器二进制未下载完成，浏览器用例未在本机执行；CI 会执行 |
| P1-13 可访问性验收 | ◐ 部分完成 | `frontend/src/components/panel/PanelPane.vue`、`frontend/tests/component/panel.test.ts` | tabs ARIA、抽屉焦点、live region、reduced-motion 用例 | 组件层已自动验证；浏览器层见 P1-12 | 同 P1-12 |
| P2-1 demo 制品 | ⛔ 未完成（需付费模型） | `scripts/gen_demo_examples.py`（上一轮已支持 `--measure`） | — | `/api/demo/examples` 当前 503（预期行为） | 需要一次真实模型 run，见第五节第 3 条 |
| P2-3 制品清单稳定性 | ✅ 已完成 | `scripts/build_artifact_manifest.py`、`data/release/artifact-manifest.json`、`SHA256SUMS` | `test_manifest_sqlite_logical_hash_ignores_runtime_writes`、`test_manifest_verify_detects_changed_and_unregistered_files`、`test_manifest_build_refuses_dirty_worktree` | 全量测试**前后**各执行一次 `verify` → 均"制品与清单一致" | Chroma 元数据库改为逻辑哈希，极端情况下（表结构变化）需升级哈希规则 |
| P2-4 数据血缘 | ✅ 已完成 | `scripts/build_lineage.py`、`data/release/lineage.json` | 由制品清单测试间接覆盖（lineage 进入清单） | `python scripts/build_lineage.py --version 20260915_v1` → 各层哈希齐全 | 上游原始文本仅本机可用，已标 `available` 与授权说明 |
| P2-5 Chroma 审计 | ✅ 已完成 | `scripts/audit_chroma_segments.py`、`data/release/chroma-segment-audit.json` | 审计脚本自带 count/随机 get/query 校验 | 见第三节 | 备份目录保留在 `data/index/_orphan_backup_*`，确认无误后可手动删除 |
| P2-6 Python 锁文件 | ◐ 部分完成 | `requirements.lock`、`requirements-dev.lock`、`.github/workflows/ci.yml` | — | 两个锁文件已生成（88/93 包，版本锁定） | 尚未带 hash（`--generate-hashes` 未跑完），见第五节第 1 条 |
| P2-7 CI 与 release | ✅ 已完成 | `.github/workflows/ci.yml`、`.github/workflows/release.yml`、`scripts/check_docs.py`、`scripts/check_secrets.py` | 文档检查 3 项 + 密钥扫描 | `python scripts/check_docs.py --strict` → 通过；`check_secrets.py` → 未发现明文密钥 | release workflow 需要数据制品下载地址（200MB 不入 Git） |

> 状态口径：✅ 实现+测试+回归+文档齐备；◐ 主体完成但有关键外部限制；⛔ 需要外部条件（付费模型/网络）。

## 三、关键问题与修复要点

### 1. 一个 `entity_id` 承担两种语义（P1-3 根因）

旧契约里 replace 的 `entity_id` 是**被替换实体**，后端再按 replacement 名称去词典取 ID，
同名多候选时取"列表第一项"。修复：

- 契约拆成 `source_entity_id`（被替换/移除）与 `replacement_entity_id`（用户选中的新实体）；
- `add` 只接受 `name` + `entity_type`，`entity_id` 仅作为 add 的目标 ID 别名；
- 词典新增 `by_id()` 索引；`_resolve_by_name()` 在**同名多候选时返回 None 而不是第一项**；
- replace 后按 `entity_id` 去重（把 A 换成已存在的 B 等价于删除 A）；
- 缓存键纳入两个 ID，纠正请求不再命中旧答案。

### 2. Chroma 元数据库导致制品校验不可重复（P2-3）

服务一旦启动，Chroma 会写 WAL 与运行态表，物理哈希必然变化。修复：对
`chroma.sqlite3` 采用**逻辑哈希**（collection 定义 + segment 映射 + 每段向量条数的规范化
dump），运行态写入不影响，增删向量必然被发现；同时 verify 增加"未登记新文件"双向比对。

### 3. Chroma 孤儿目录（P2-5）

审计（只读打开元数据库）得到权威映射：

```text
collections: chunks_v1 (dim=1024)
segments:    cd0d52ef…(VECTOR)  a96e8458…(METADATA)
磁盘目录:    cd0d52ef…（被引用）  e5d4b2c3…（无任何 segment 引用 → 孤儿）
embeddings=9544 = ids.json=9544，随机 get/query 均成功
```

按工作单要求先备份（含逐文件 sha256 与移动后校验）再移出孤儿目录；清理后重跑审计：
磁盘段目录 1 个、孤儿 0、计数与随机查询仍正常。

## 四、验证证据（同一次工作树）

```text
（工作区 clean；commit cbedc91；分支 round4-review-remediation）
pytest tests -q                      → 250 passed
cd frontend && npm run test:unit     → 27 passed
cd frontend && npm run test:component→ 16 passed
cd frontend && npm run verify        → typecheck / test / build / bundle 门禁全通过
python scripts/check_docs.py --strict→ 通过（相对链接、current 口径、.env.example）
python scripts/check_secrets.py      → 未发现明文密钥
python scripts/build_artifact_manifest.py verify
  测试前 → 36/36 一致；全量测试后 → 36/36 一致
python scripts/build_artifact_manifest.py build --require-clean
  → 成功（git_dirty=false，36 条目 / 166.9 MB；被忽略的约定外目录：new/）
git status --porcelain -- ':!new'    → 空（工作区 clean）
```

## 五、未完成事项与环境限制（诚实清单）

1. **锁文件哈希模式**：`requirements.lock` / `requirements-dev.lock` 已锁定版本（88/93 包），
   但 `--generate-hashes` 需要下载 chromadb 依赖树的全部 wheel，本机多轮尝试均未在
   会话预算内完成。升级命令：
   `python -m piptools compile --generate-hashes requirements.txt -o requirements.lock`。
   CI 已兼容两种形态（有 `requirements-dev.lock` 时用 `--require-hashes`，否则退回
   `requirements-dev.txt`）。
2. **Playwright 浏览器用例**：`frontend/tests/e2e/accessibility.spec.ts`（桌面 + 移动两套
   project：键盘提问→引用展开、tabs 方向键、抽屉焦点陷阱与 Escape 回焦、移动端引用自动开抽屉、
   live region、reduced-motion）与 `playwright.config.ts` 均已就绪，`@playwright/test` 已装，
   但 Chromium 二进制约 196 MB 在本机下载停滞，**未在本机执行**；CI 的 release job 会安装并运行。
3. **P2-1 demo 重建**：需要一次真实模型评测（付费调用），repository 维护者决定何时执行：
   `python scripts/gen_demo_examples.py --version 20260915_v1 --run <真实模型 run> --measure`。
   当前接口返回 503 属预期——不拿旧版本时延冒充新版本。
4. **release workflow 的数据来源**：`data/` 约 167 MB 不入 Git，workflow 需要
   `data_artifact_url` 输入或 `RELEASE_DATA_URL` Secret。
5. **clean commit 绑定**：验证在干净工作树上的代码 commit 上完成；由于证据文档本身也要
   提交，"证据 commit"与"代码 commit"相差一个文档提交——这是流程固有限制，本总结明确标注，
   不做掩盖。

## 六、对工作单的两处意见（供后续参考）

1. **P0-4 的"证据 commit 完全一致"在单仓库里不可严格满足**：任何"把证据写进仓库"的动作都会
   产生新 commit。建议改为"证据记录所测 commit + 证据提交自身 commit"两个字段，或把证据
   放到 CI artifact 而不是仓库内。
2. **P1-12 的 Playwright 在受限网络下应先做能力探测**：建议 CI 里对浏览器安装失败给出
   "跳过并标记 skipped"而不是直接失败，否则内网/镜像受限环境永远无法变绿；
   本次实现保留了在 runner 上安装的路径，并把本机未执行的原因写进了 `current-status.md`。
