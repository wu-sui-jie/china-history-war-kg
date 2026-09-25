# 当前状态（唯一事实源）

> 本文件是**当前**运行事实的唯一入口：版本、测试数、demo 状态、依赖锁定状态、发布状态。
> 其他文档（README、部署手册、阶段总结、整改记录）只做引用，不再各自维护这些数字。
> 最近更新：2026-09-25（**问答记录按账号隔离**：主应用以 iframe 嵌入时通过 `cw-user`
> postMessage 传账号 id，RAG 前端按 `ragv5-session-v3:u{uid}` 分桶存储；独立访问 :8000
> 行为不变。契约与边界见 `frontend/src/utils/userScope.ts` 与旧知识库系统
> `docs/集成与入口约定.md`）。
> 更早：2026-09-20（借鉴旧问答系统：P0 事件卡叙事字段 + P1 会话级导出与会话管理；
> P2 规则推理移植：离线固化 11,833 条推理边并接入 F03 检索、引用与提示词）。
> 数据计数由 `scripts/check_docs.py --strict`
> 与快照/索引清单机械核对，避免唯一事实源自身写错数字。

## 一、版本与运行

| 项 | 值 | 说明 |
| --- | --- | --- |
| 活跃数据版本 | `RAG_ACTIVE_VERSION`（未配置时按目录扫描最新一致版本） | 生产要求显式固定；`version_selection` 在 health 中区分 `cli_explicit` / `env_pinned` / `latest_scan` |
| 默认启动命令 | `python scripts/run_server.py --port 8000 --version <版本>` | `--version` 写入 `RAG_ACTIVE_VERSION` 并声明来源为 `cli_explicit`（第五轮 P0-4） |
| 数据集 | 9925 实体 / 17700 关系 / 9544 向量条 | 以 `data/snapshot/20260915_v1/manifest.json` 的 `counts` 与 `/api/health` 的 `meta` 为准 |
| 运行环境 | Python 3.11（锁文件按 3.11 生成） | Chroma 依赖树要求 ≥3.10；3.9 仅保留"语法下限"检查（`syntax-floor` job），不再声明为受支持运行版本 |
| 对外接口 | `GET /api/health`、`GET /api/dicts`、`GET /api/demo/examples`、`POST /api/query`（SSE）、`POST /api/query/json`（非流式，可选 `X-Bot-Key`）、`GET /` | 契约见 [data-contract.md](data-contract.md)；非流式响应见 `contracts/query_json.py` |
| 会话存储与账号隔离 | 主应用嵌入时 `ragv5-session-v3:u{uid}`；独立访问 :8000 时仍是 `ragv5-session-v3` | 账号 id 由主应用的 iframe 通过 `cw-user` postMessage 下发（同源校验、uid 形状受限）。**uid 非空时只读账号桶**，不回落旧全局键——旧记录不属于任何账号，不能被先登录的人收编（第 6 轮审核 H2）；换桶顺序（先写回旧桶再换）见 `frontend/src/stores/session.ts` 的 `applyUserScope` |

## 二、测试与门禁（本地实测，2026-09-16）

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `python -m pytest tests -q` | **349 passed**（2026-09-25 第 6 轮审核实测，Python 3.11；本轮改动未触及 RAG 后端）。历史口径（345 / 298 / 298+23 / 等价 runner 76）已废弃：那几次是在没有 pytest 的环境里分批跑的，不能与全量数字并列。以后只记本行这一条 |
| 前端单元（Vitest） | `cd frontend && npm run test:unit` | **67 passed**（SSE 解析/超时分类、状态机、持久化与迁移、多会话、会话导出、**按账号隔离与换桶顺序**）。原写 47 是 2026-09-20 的口径，加入隔离用例后未回写（第 6 轮审核 M6 已修正） |
| 前端组件（Vue Test Utils） | `npm run test:component` | **31 passed**（重试入口、同名候选 payload、面板空状态、tabs ARIA、引用定位、chunk 降级、事件卡叙事字段、会话列表）；合计 `npm test` = **98 passed**（2026-09-25 实测） |
| 契约端到端（需已启动服务） | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项检查全过（含 SSE 事件序、缓存命中、400 错误、同源托管） |
| 浏览器端到端（Playwright） | `npm run test:e2e`（真实服务）或 `npm run test:e2e:offline`（桩后端） | **15 passed / 5 skipped**（2026-09-25 实测，15.3s）。desktop + mobile 两个 project、10 个用例 × 2 = 20 次执行，其中 5 次按各 spec 内的 `test.skip(project === 'mobile')` 跳过。覆盖 Tab/Shift+Tab 焦点陷阱、Escape 回焦、tabs 方向键、live region、reduced-motion 双态断言、多会话切换与刷新保持、导出 .md 下载、**问答记录按账号隔离**。**原写 14/4 是 2026-09-20 的口径**（那时还没有 `user-scope-isolation.spec.ts`：9 个用例 × 2 − 4 skip = 14/4），第 11 轮整理文档时已更正。**跑之前注意**：桩后端把 `frontend/dist` 托管在 `/` 下，而 `dist/` 默认是并入模式（base=`/rag/`）产物会全 404——离线跑请先 `npx vite build --outDir dist-plain` 再用 `E2E_DIST=dist-plain`（见 `scripts/e2e-run.mjs` 注释） |
| 首屏体积门禁 | `npm run check:bundle` | 通过（入口 gzip 25.1 kB、vendor 75.9 kB、首屏合计 101.0 kB，上限 190 kB） |
| 文档与配置一致性 | `python scripts/check_docs.py --strict` | 通过（相对链接、current 口径、`.env.example`、**数据计数与清单一致**） |
| 密钥扫描 | `python scripts/check_secrets.py` | 未发现明文密钥 |
| 制品清单 | `python scripts/build_artifact_manifest.py verify` | 通过（37/37 文件；Chroma 元数据库按逻辑哈希校验） |
| 标准校验和（**标准工具**） | `sha256sum -c data/release/SHA256SUMS` | 通过（38 项全部 OK；证据文件写入固定 LF，跨平台字节一致） |
| Chroma 段审计 | `python scripts/audit_chroma_segments.py --version 20260915_v1` | 四方计数一致（ids/embeddings/collection/manifest 均 9544），退出码 0 |
| 数据血缘 | `python scripts/build_lineage.py --check` | 通过（2026-09-17 demo 链重建后 run → demo → runtime 全部一致） |
| 依赖锁 | `python scripts/lock_hashes.py --check` + `pip install --dry-run --require-hashes -r requirements-dev.lock` | **逐条**需求带 `--hash`；dry-run 通过；抽样（chromadb/numpy/openai）实际下载校验哈希一致；锁内不含 `--index-url`（镜像由本机/CI 各自指定） |

## 三、数据与发布制品

| 制品 | 状态 |
| --- | --- |
| `data/release/artifact-manifest.json` + `SHA256SUMS` + `LOGICAL_HASHES.json` | ✅ 已生成（物理哈希与逻辑哈希分离；行尾固定 LF，`sha256sum -c` 38/38 通过） |
| `data/release/lineage.json` | ✅ 已生成（source → snapshot → index → eval → demo → release 全链路哈希 + demo 父 run 解析 + 跨层一致性 `checks`，含 index_version 与 measurement_mode 判定） |
| `data/release/chroma-segment-audit.json` | ✅ 已生成；结论：1 个 collection、2 个 segment（VECTOR + METADATA）、无孤儿目录 |
| `data/release/sbom.json` | ✅ 已生成（SPDX 2.3，Python + Node 共 306 个包，命名空间可重现，`gen_sbom.py validate` 通过） |
| 前端 `dist` | ✅ 已构建，**当前为并入模式产物**（`npm run build:integration`，base=/rag/、接口前缀=/rag/api，供旧系统 3001 → `/rag` 反代）；独立部署与 release 包需用 `npm run build` 覆盖，两者共用同一目录、互相覆盖（口径见旧知识库系统 `docs/集成与入口约定.md` 第三节） |
| 规则推理产物（P2） | ✅ 对活跃快照 `20260915_v1` 生成：**11,833 条推理边**（反向 11,559 + 因果链 3 + 顺承链 271 + 战争阶段 0），`inferred_relations.json` 6.9 MB + `inference_report.json`；`war_020`（3 步包含链）在当前数据无命中，报告已注明；重复构建字节一致（SHA256 `8ad76f0e71f1e15c…`） |
| Python 锁文件 | ✅ `requirements.lock`（94 需求）/ `requirements-dev.lock`（100 需求；含 2026-09-16 为修 CI 补的 `uvloop==0.22.1`）：版本与开发环境实测一致，**每条需求均带 `--hash`**，不含 `--index-url` |
| `frontend/package-lock.json` | ✅ 已存在（npm 侧可 `npm ci`） |
| release 包 | ✅ 组装脚本就绪（`scripts/build_release_bundle.py`：源码 + 数据 + dist + 证据 + SBOM + 依赖声明）；smoke 报告缺失/失败时硬拒绝；**本机未在干净 commit 上执行完整发布** |

## 四、F08 演示示例（已恢复，2026-09-17 重建）

`data/eval/20260915_v1/demo_examples.json` 已于 2026-09-17 用**真实模型**评测重建：
`version=20260915_v1`、`measurement_mode=real_llm`、来源 run
`run_20260917_203558`（llm_used=true，模型 deepseek-v4.1-flash，双通道+纯文本共 56 条）。
候选 25 条（28 题 main 套件剔除 3 条评分为 incorrect 的拒答型检索失败题），实测后入选
12 条，首正文时延 2.4s~6.3s（阈值 9000 ms）。`/api/demo/examples` 返回 200。

评分环节按 P2-22 口径由 AI 代理完成（correct 23 / partial 2 / incorrect 3，
reviewer 字段标注"未人工复核"）；正式交付前应人工复核评分（见第五节）。

历史背景：此前 demo 文件为旧版本产物（`version=20260904_v2`），接口按版本一致性
校验返回 503，宁可让示例区显示"暂不可用"也不展示旧版本的实测时延。重建流程：

```bash
# 1) 真实模型评测（必须加 --llm，否则默认强制离线回答器）
python scripts/run_evaluation.py run --bank data/eval/20260915_v1/questions.jsonl --suites main --llm
# 2) 评分：将 scoring_template.jsonl 填分为 scores.jsonl（人工或 AI 代理，标注 reviewer）
# 3) 用该 run 重建 demo（--measure 会实测首字时延）
python scripts/gen_demo_examples.py --version 20260915_v1 --run <真实模型 run> --measure
# 4) 重新生成血缘并确认一致性（demo 父 run 必须是 latest_run）
python scripts/build_lineage.py --version 20260915_v1 && python scripts/build_lineage.py --version 20260915_v1 --check
```

## 五、未闭环事项（诚实清单）

1. ~~**真实模型 demo 制品**~~：✅ 已闭环（2026-09-17 重建，见第四节）。遗留其中的人工
   复核部分：评分由 AI 代理完成（P2-22 口径），正式交付前应人工复核 28 条评分。
2. **GitHub Actions 绿色流水线**：✅ 已达成（2026-09-17，run 35214127469 及合并 PR #1
   后的 main 分支 CI 全绿）；release workflow 仍待手动触发演练（需数据制品）。
3. **release 只在干净 commit 上有效**：`build_artifact_manifest.py build --require-clean`
   与 release workflow 都要求工作区干净；本机当前工作区含本轮改动，故发布证据里
   `git_dirty=true`。发布时应先提交，再重跑证据生成链。
4. **数据制品下载**：release workflow 需要 `data_artifact_url` + `data_artifact_sha256`
   （可再加 gpg 签名）；数据约 210 MB 不入 Git。可用
   `python scripts/fetch_data_artifact.py --pack` 在本机生成 zip 与其哈希。
5. **tested commit 与 evidence commit**：证据绑定的是产出证据时的代码 commit；
   把证据文档提交本身会产生新 commit。发布记录里应同时写 `tested_commit`
   （跑测试时）与 `evidence_commit`（生成制品时），两者不要求相等，但都必须可追溯。
6. **问答隔离不防冒充（方案 A 的边界）**：账号 id 是主应用通过 postMessage 给的，
   RAG 只校验来源与形状，不做验签。同源之下懂控制台的账号可以改 uid 去读别人的会话——
   本方案解决的是"串记录"。要防冒充需走方案 B（主应用传 JWT + RAG 服务端验签），
   触发时机是公网部署（见旧知识库系统 `docs/方案分析-账户提权与问答隔离-20260925.md`）。

## 六、历史记录入口

阶段交付与六轮审核整改的归纳记录见 **[CHANGELOG.md](CHANGELOG.md)**（2026-09-21 由原
`docs/RAG_v1/` 与 `docs/changes/` 共 38 份过程文档压缩而成）。逐条的改动清单、
复现命令与原始审核记录保留在 Git 历史中：

```bash
git log --diff-filter=D --oneline -- docs/changes/ docs/RAG_v1/   # 找到整理前的提交
git show <整理前提交>:docs/changes/20260916-round6-review-of-round5-remediation.md
```
