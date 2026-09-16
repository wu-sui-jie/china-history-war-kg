# 当前状态（唯一事实源）

> 本文件是**当前**运行事实的唯一入口：版本、测试数、demo 状态、依赖锁定状态、发布状态。
> 其他文档（README、部署手册、阶段总结、整改记录）只做引用，不再各自维护这些数字。
> 最近更新：2026-09-16（第四轮复核整改工作单实施后）。

## 一、版本与运行

| 项 | 值 | 说明 |
| --- | --- | --- |
| 活跃数据版本 | `RAG_ACTIVE_VERSION`（未配置时按目录扫描最新一致版本） | 生产要求显式固定；`version_selection` 在 health 中区分 `cli_explicit` / `env_pinned` / `latest_scan` |
| 默认启动命令 | `python scripts/run_server.py --port 8000 --version <版本>` | `--version` 会写入 `RAG_ACTIVE_VERSION` |
| 数据集 | 10925 实体 / 17730 关系 / 9544 向量条 | 以 `/api/health` 的 `meta` 为准 |
| 对外接口 | `GET /api/health`、`GET /api/dicts`、`GET /api/demo/examples`、`POST /api/query`、`GET /` | 契约见 [data-contract.md](data-contract.md) |

## 二、测试与门禁（本地实测，2026-09-16）

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `python -m pytest tests -q` | **250 passed** |
| 前端单元（Vitest） | `cd frontend && npm run test:unit` | **27 passed**（SSE 解析/超时分类、状态机、持久化与迁移） |
| 前端组件（Vue Test Utils） | `npm run test:component` | **16 passed**（重试入口、同名候选 payload、面板空状态、tabs ARIA、引用定位、chunk 降级）；合计 `npm test` = **43 passed** |
| 契约端到端（需已启动服务） | `npm run test:contract -- --base http://127.0.0.1:8125` | 19 项检查全过 |
| 浏览器端到端（Playwright） | `npm run test:e2e` | 见"未闭环"一节：用例已写，浏览器依赖在本机未能下载完成 |
| 首屏体积门禁 | `npm run check:bundle` | 通过（入口 gzip ≈20 kB、首屏合计 ≈96 kB，上限 190 kB） |
| 文档与配置一致性 | `python scripts/check_docs.py --strict` | 通过（相对链接、current 口径、`.env.example`） |
| 密钥扫描 | `python scripts/check_secrets.py` | 未发现明文密钥 |
| 制品清单 | `python scripts/build_artifact_manifest.py verify` | 通过（Chroma 元数据库按逻辑哈希校验，服务运行时改写不再误报） |

## 三、数据与发布制品

| 制品 | 状态 |
| --- | --- |
| `data/release/artifact-manifest.json` + `SHA256SUMS` | ✅ 已生成（覆盖 snapshot/index/FTS/Chroma/题库/demo/前端 dist/依赖声明/血缘） |
| `data/release/lineage.json` | ✅ 已生成（source → snapshot → index → eval → demo → release 全链路哈希） |
| `data/release/chroma-segment-audit.json` | ✅ 已生成；结论：1 个 collection、2 个 segment（VECTOR + METADATA），**磁盘上 1 个未引用目录已备份并清理** |
| 前端 `dist` | ✅ 已构建（`npm run build`） |
| Python 锁文件 | ✅ `requirements.lock` / `requirements-dev.lock`（**版本已锁定**，88/93 个包；带哈希的变体见"未闭环"第 1 条） |
| `frontend/package-lock.json` | ✅ 已存在（npm 侧可 `npm ci`） |

## 四、F08 演示示例（当前**不可用**，接口返回 503）

`data/eval/20260915_v1/demo_examples.json` **文件存在但属于旧版本产物**（内部
`version=20260904_v2`、无 `measurement_mode`）。`/api/demo/examples` 按版本一致性校验
返回 503 并给出重建命令——宁可让示例区显示"暂不可用"，也不展示旧版本的实测时延。

重建需要一次**真实模型**评测：

```bash
python scripts/gen_demo_examples.py --version 20260915_v1 --run <真实模型 run> --measure
```

## 五、未闭环事项（诚实清单）

1. **Python 锁的哈希模式**：`requirements.lock` / `requirements-dev.lock` 已生成并锁定版本，
   但 `--generate-hashes`（`pip install --require-hashes` 需要）要把 chromadb 依赖树的每个
   wheel 下载后哈希，本机单次会话内未跑完；命令保留如下，可在网络充裕时升级为带哈希的锁：
   ```bash
   pip install pip-tools
   pip-compile --generate-hashes requirements.txt -o requirements.lock
   pip-compile --generate-hashes requirements-dev.txt -o requirements-dev.lock
   pip install --require-hashes -r requirements-dev.lock
   ```
2. **Playwright 浏览器用例**：`frontend/tests/e2e/accessibility.spec.ts` 与
   `playwright.config.ts` 已就绪（桌面 + 移动两套 project，覆盖键盘提问、tabs 方向键、
   抽屉焦点陷阱与 Escape 回焦、移动端引用自动开抽屉、live region、reduced-motion）；
   Chromium 二进制约 200 MB，本机未下载完成，故**未在本机执行**。CI（ci.yml）会在
   runner 上安装并执行。
3. **真实模型 demo 制品**：见第四节，需要付费模型调用，由仓库维护者决定何时执行。
4. **release 工作流**：`.github/workflows/release.yml` 需要数据制品下载地址
   （`data_artifact_url` 输入或 `RELEASE_DATA_URL` Secret），因为 200 MB 级数据不入 Git。
5. **clean commit 与证据绑定**：见 `docs/changes/20260916-round4-review-remediation-summary.md`
   的验证章节——证据绑定到具体 commit，但"把证据文档提交"本身会产生新 commit，
   因此绑定的是**产出证据时的代码 commit**，两者差一个文档提交。

## 六、历史文档入口

- 第三轮审核：`docs/20260915-第三轮审核报告与整改方案.md`
- 第四轮复核：`docs/20260915-RAG全项目复核分析与优化建议.md`
- 第四轮复核的后续工作单：`docs/changes/20260916-round4-review-remediation-work-order.md`
- 整改记录：`docs/changes/20260915-round4-review-fix-summary.md`、
  `docs/changes/20260915-round4-review-fix-summary-2.md`、
  `docs/changes/20260916-round4-review-remediation-summary.md`
