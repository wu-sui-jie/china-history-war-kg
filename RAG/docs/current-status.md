# 当前状态（唯一事实源）

> 本文件是**当前**运行事实的唯一入口：版本、测试数、demo 状态、依赖锁定状态、发布状态。
> 其他文档（README、部署手册、阶段总结、整改记录）只做引用，不再各自维护这些数字。
> 最近更新：2026-09-25（**凭证撤销查询**：验签只能证明"这张 token 是旧后端签的"，
> 证明不了"它还该被承认"——账号被停用或改密码之后，旧后端已经拒绝该凭证，而本服务此前会
> 一直放行到 token 自然过期（默认 7 天）。现在配上 `RAG_INTROSPECT_URL` +
> `RAG_INTERNAL_SERVICE_KEY` 之后，两条问答通道都会向旧后端的
> `POST /api/internal/token/introspect` 确认凭证状态，结论按
> `RAG_INTROSPECT_TTL_SECONDS`（默认 30 秒）缓存 —— **该 TTL 就是"撤销生效延迟"的上界**。
> 后端不可用时按 `RAG_INTROSPECT_FAIL_MODE` 处理：`closed`（默认，拒绝；"把后端打挂"不该
> 成为绕过撤销的手段）或 `open`（放行）。
> **撤销策略必须显式选择**：生产档 + jwt 档下要么把查询配齐
> （或设 `RAG_REQUIRE_REVOCATION_CHECK=true` 表示"必须有"），要么设
> `RAG_ALLOW_DELAYED_REVOCATION=true` 明确接受延迟——两个都不做即**拒绝启动**，
> 那条边界不能靠"两个值都不填"隐式接受。
> `/api/health` 的 `auth.revocation` 报三态 `policy`（`enforced` / `delayed` /
> `not-applicable`）、`max_delay_seconds`（启用时=缓存 TTL；未启用时为 null，因为上界
> 由签发端的 `JWT_TTL_SECONDS` 决定，本服务不猜）、以及 `last_ok_at` /
> `last_failure_at` / `last_failure_reason`——backend 重启期间"它恢复了吗"是运维最想知道的。
> 实现见 `server/introspection.py`。
> 同时**服务端身份校验改为模式分档**：`RAG_AUTH_MODE=jwt` 时
> `/api/query` 与 `/api/query/json` 都要求请求头带旧后端签发的 JWT，由 `server/auth.py`
> 用共享密钥验签，并校验 `iss` / `aud`（挡"同一把密钥的别的服务签的 token"）
> ——**这补上了"知道 `/rag/` 地址即可调用"的口子**。同一份 `cw-user` postMessage 现在还会
> 带 token（`frontend/src/api/authToken.ts` 保管并拼进请求头）。鉴权用**三档**
> `jwt` / `nginx` / `disabled` 而不是单一布尔开关，因为"nginx 在把关"与"根本没人在把关"
> 在配置里长得一样；**显式生产档下 `disabled` 会拒绝启动**（只打一条 WARNING 会被忽略），
> `nginx` 档若监听非回环地址也拒绝启动。**生产模板默认是 `jwt`**（`deploy/env/rag.env`）：
> 只写 `nginx` 而 nginx 侧的 `auth_basic` 是注释状态，照着模板部署会得到
> "两边都以为对方在把关"的组合——两边都能正常启动、日志无异常，唯一后果是公网没有访问控制；
> 现在这个组合由 `deploy/scripts/check_rag_auth.sh` 在安装期阻断（见该脚本头部说明）。
> 开启校验后**直接打开 `/rag/` 将无法问答**，这是预期效果。
> 同时**问答记录按账号隔离**依旧有效：主应用以 iframe 嵌入时通过 `cw-user` postMessage
> 传账号 id，RAG 前端按 `ragv5-session-v3:u{uid}` 分桶存储；独立访问 :8000 行为不变。
> 契约与边界见 `frontend/src/utils/userScope.ts` 与旧知识库系统 `docs/集成与入口约定.md`）。
> 更早：2026-09-20（借鉴旧问答系统：事件卡叙事字段、会话级导出与会话管理；
> 规则推理移植：离线固化 11,833 条推理边并接入 F03 检索、引用与提示词）。
> 数据计数由 `scripts/check_docs.py --strict`
> 与快照/索引清单机械核对，避免唯一事实源自身写错数字。

## 一、版本与运行

| 项 | 值 | 说明 |
| --- | --- | --- |
| 活跃数据版本 | `RAG_ACTIVE_VERSION`（未配置时按目录扫描最新一致版本） | 生产要求显式固定；`version_selection` 在 health 中区分 `cli_explicit` / `env_pinned` / `latest_scan` |
| 默认启动命令 | `cd RAG && python scripts/run_server.py --port 8000 --version <版本>` | `--version` 写入 `RAG_ACTIVE_VERSION` 并声明来源为 `cli_explicit` |
| 数据集 | 9925 实体 / 17700 关系 / 9544 向量条 | 以 `data/snapshot/20260915_v1/manifest.json` 的 `counts` 与 `/api/health` 的 `meta` 为准 |
| 运行环境 | **Python 3.11**（全项目四个模块统一；本地 conda 环境 `china-war-py311`，锁文件按 3.11 生成） | Chroma 依赖树要求 ≥3.10；3.9 仅保留"语法下限"检查（`syntax-floor` job），不再声明为受支持运行版本。CI 的 RAG job 用 3.11，旧后端与抽取链 job 仍是 3.8/3.11 双跑（过渡档） |
| 对外接口 | `GET /api/health`、`GET /api/dicts`、`GET /api/demo/examples`、`POST /api/query`（SSE）、`POST /api/query/json`（非流式）、`GET /` | 契约见 [data-contract.md](data-contract.md)；非流式响应见 `contracts/query_json.py` |
| 接口身份校验 | `RAG_AUTH_MODE`（`jwt` / `nginx` / `disabled`，默认 `disabled`）+ `RAG_JWT_SECRET`（与 backend/.env 的 `JWT_SECRET` 同值）+ `RAG_JWT_ISSUER` / `RAG_JWT_AUDIENCE` | `jwt` 档两条问答通道都要求请求头 `Token`（也接受 `Authorization: Bearer`）为旧后端签发的有效 JWT，验不过 401。`server/auth.py` 只认 HS256、强制校验 `exp`、**并校验 `iss` / `aud`**（挡"同一把密钥的别的服务签的 token"）、签名用 `hmac.compare_digest`；用标准库实现是为了不引入未审计的依赖（RAG 用带哈希锁文件安装）。非流式接口另接受 `X-Bot-Key`——飞书机器人没有用户身份，要求 JWT 会把这条调用方堵死。启动门禁：`jwt` 档不给密钥**拒绝启动**；**显式生产档**（`RAG_REQUIRE_ACTIVE_VERSION=true`）下 `disabled` 也拒绝启动；`nginx` 档若监听非回环地址由 `scripts/run_server.py` 拒绝。旧开关 `RAG_REQUIRE_AUTH` 仍被接受（`true` ≡ `jwt`）。`/api/health` 的 `auth.mode` 直接给出当前档位。**生产模板默认 `jwt`**（`deploy/env/rag.env`），并以 `deploy/scripts/check_rag_auth.sh` 在安装期校验两侧密钥同值 |
| 凭证撤销查询 | `RAG_INTROSPECT_URL` + `RAG_INTERNAL_SERVICE_KEY`（与 backend/.env 的 `INTERNAL_SERVICE_KEY` 同值）+ `RAG_INTROSPECT_TTL_SECONDS`（默认 30）+ `RAG_INTROSPECT_FAIL_MODE`（`closed` 默认 / `open`）+ **策略开关**：`RAG_REQUIRE_REVOCATION_CHECK`（要求必须配齐）或 `RAG_ALLOW_DELAYED_REVOCATION`（显式接受延迟） | 验签通过之后，两条问答通道再向旧后端的 `POST /api/internal/token/introspect` 确认"这张凭证现在还作不作数"（该接口用服务间密钥保护，**未配密钥时返回 503 而不是放行**；响应里不回失败原因）。结论按 token 摘要缓存 TTL 秒——**TTL 即撤销生效延迟的上界**。**两项都不配 = 不查询**，此时 `/api/health` 的 `auth.revocation.enabled=false` 且 `warnings` 持续给出"停用/改密码后在 token 到期前仍可用"的边界告警。失败**不写缓存**（一次抖动不该变成一段时间内人人被拒/放行）。`redis` 共享方案（文档方案 C）留给多实例部署 |
| 会话存储与账号隔离 | 主应用嵌入时 `ragv5-session-v3:u{uid}`；独立访问 :8000 时仍是 `ragv5-session-v3` | 账号 id 由主应用的 iframe 通过 `cw-user` postMessage 下发（同源校验、uid 形状受限）。**uid 非空时只读账号桶**，不回落旧全局键——旧记录不属于任何账号，不能被先登录的人收编；换桶顺序（先写回旧桶再换）见 `frontend/src/stores/session.ts` 的 `applyUserScope` |

## 二、测试与门禁（本地实测，2026-09-26）

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `cd RAG && python -m pytest tests -q` | **464 passed**（2026-09-26 实测，Python 3.11 / conda `china-war-py311`）。其中凭证撤销查询与通道准入相关：`tests/test_rag_revocation.py` 31 例 + `tests/test_rag_auth.py` 67 例。`test_chain_smoke` 的模式透传用例已用**桩向量客户端**离线化（不依赖外部 embedding 端点） |
| 前端单元（Vitest） | `cd frontend && npm run test:unit` | **79 passed**（SSE 解析/超时分类、状态机、持久化与迁移、多会话、会话导出、**按账号隔离与换桶顺序**、**体积门禁两种构建模式**、**身份头接线**） |
| 前端组件（Vue Test Utils） | `npm run test:component` | **31 passed**（重试入口、同名候选 payload、面板空状态、tabs ARIA、引用定位、chunk 降级、事件卡叙事字段、会话列表）；合计 `npm test` = **110 passed**（2026-09-25 实测） |
| 契约端到端（需已启动服务） | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项检查全过（含 SSE 事件序、缓存命中、400 错误、同源托管） |
| 浏览器端到端（Playwright） | `npm run test:e2e`（真实服务）或 `npm run test:e2e:offline`（桩后端） | **15 passed / 5 skipped**（2026-09-25 实测，15.3s）。desktop + mobile 两个 project、10 个用例 × 2 = 20 次执行，其中 5 次按各 spec 内的 `test.skip(project === 'mobile')` 跳过。覆盖 Tab/Shift+Tab 焦点陷阱、Escape 回焦、tabs 方向键、live region、reduced-motion 双态断言、多会话切换与刷新保持、导出 .md 下载、**问答记录按账号隔离**。**跑之前注意**：桩后端把 `frontend/dist` 托管在 `/` 下，而 `dist/` 默认是并入模式（base=`/rag/`）产物会全 404——离线跑请先 `npx vite build --outDir dist-plain` 再用 `E2E_DIST=dist-plain`（见 `scripts/e2e-run.mjs` 注释） |
| 首屏体积门禁 | `npm run check:bundle` | 通过（入口 gzip 25.5 kB、vendor 75.9 kB、首屏合计 101.4 kB，上限 190 kB）。门禁按产物自解析资源前缀，**找不到入口或 vendor 直接失败**，standalone 与 integration 两种产物各有用例 |
| 文档与配置一致性 | `python scripts/check_docs.py --strict` | 通过（相对链接、current 口径、`.env.example`、**数据计数与清单一致**） |
| 密钥扫描 | `python scripts/check_secrets.py`（**扫全仓库**，CI 由 `secret-scan` job 执行 `--root .`） | 未发现明文密钥。范围必须覆盖仓库的每个角落——只扫 `RAG/` 或只 grep `backend`、`entity-event-relation` 时，`deploy/`、`feishu-bot/`、工作流与根级脚本全在门禁之外。占位符样本不误报 |
| 静态检查（RAG） | `python -m ruff check server config contracts lib data scripts evaluation tests` | All checks passed（ruff 版本在 CI 与本地都钉死 `0.16.8`，避免升级后判定变化导致门禁口径漂移） |
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
| 规则推理产物 | ✅ 对活跃快照 `20260915_v1` 生成：**11,833 条推理边**（反向 11,559 + 因果链 3 + 顺承链 271 + 战争阶段 0），`inferred_relations.json` 6.9 MB + `inference_report.json`；`war_020`（3 步包含链）在当前数据无命中，报告已注明；重复构建字节一致（SHA256 `8ad76f0e71f1e15c…`） |
| Python 锁文件 | ✅ `requirements.lock`（94 需求）/ `requirements-dev.lock`（100 需求；含为修 CI 补的 `uvloop==0.22.1`）：版本与开发环境实测一致，**每条需求均带 `--hash`**，不含 `--index-url` |
| `frontend/package-lock.json` | ✅ 已存在（npm 侧可 `npm ci`） |
| release 包 | ✅ 组装脚本就绪（`scripts/build_release_bundle.py`：源码 + 数据 + dist + 证据 + SBOM + 依赖声明）；smoke 报告缺失/失败时硬拒绝；**本机未在干净 commit 上执行完整发布** |

## 四、F08 演示示例（2026-09-17 重建）

`data/eval/20260915_v1/demo_examples.json` 已于 2026-09-17 用**真实模型**评测重建：
`version=20260915_v1`、`measurement_mode=real_llm`、来源 run
`run_20260917_203558`（llm_used=true，模型 deepseek-v4.1-flash，双通道+纯文本共 56 条）。
候选 25 条（28 题 main 套件剔除 3 条评分为 incorrect 的拒答型检索失败题），实测后入选
12 条，首正文时延 2.4s~6.3s（阈值 9000 ms）。`/api/demo/examples` 返回 200。

评分环节由 AI 代理完成（correct 23 / partial 2 / incorrect 3，reviewer 字段标注
"未人工复核"）；正式交付前应人工复核评分（见第五节）。

清单版本与运行时版本不一致时（例如清单还停在 `version=20260904_v2`），接口按版本一致性
校验返回 503——宁可不出示例，也不展示旧版本的实测时延。重建流程：

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
   复核部分：评分由 AI 代理完成，正式交付前应人工复核 28 条评分。
2. **GitHub Actions 绿色流水线**：✅ 已达成（2026-09-17，run 35214127469 及合并 PR #1
   后的 main 分支 CI 全绿）；release workflow 仍待手动触发演练（需数据制品）。
3. **release 只在干净 commit 上有效**：`build_artifact_manifest.py build --require-clean`
   与 release workflow 都要求工作区干净；工作区含未提交改动时发布证据里
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
   触发时机是公网部署（见项目级 `docs/项目审查与修复历史.md`）。

## 六、历史记录入口

阶段交付与历次整改的归纳记录见 **[CHANGELOG.md](CHANGELOG.md)**（2026-09-21 由原
`docs/RAG_v1/` 与 `docs/changes/` 共 38 份过程文档压缩而成）。逐条的改动清单、
复现命令与原始审核记录保留在 Git 历史中：

```bash
git log --diff-filter=D --oneline -- docs/changes/ docs/RAG_v1/   # 找到整理前的提交
git show <整理前提交>:docs/changes/20260916-round6-review-of-round5-remediation.md
```
