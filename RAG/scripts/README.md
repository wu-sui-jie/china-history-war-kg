# scripts

**归属功能：F09 / F11 离线链路命令行入口 + RAGv2 在线服务启动/人工审核回填 + RAGv4 评测 + RAGv5 部署冒烟与向量校验。**

把数据治理（snapshot 层）、索引构建（index 层）、在线服务、治理增强与部署运维封装成可一键运行的命令，
统一从 `RAG/` 根目录执行。

## 脚本

| 脚本 | 功能 | 对应功能 |
| --- | --- | --- |
| `export_snapshot.py` | 治理并导出快照（实体/关系/词典/报告） | F09 |
| `build_index.py` | 基于快照构建 FTS5 + 向量索引（含 `--vectors-only` / `--rebuild-chroma` / 索引变体） | F11 |
| `run_pipeline.py` | 串联 F09 → F11 全流程 | F09+F11 |
| `run_server.py` | 启动 SSE 问答服务（F02–F06，含启动自检与同源静态托管） | F02–F06 |
| `apply_audit.py` | 人工审核决定回填（audit_decisions.json → 新版本快照） | F09 增强 |
| `run_evaluation.py` | F10 评测入口（check-bank/run/report，业务在 `evaluation/` 包） | F10（RAGv4） |
| `gen_draft_bank.py` | 从快照数据生成黄金问答集草稿（之后以 questions.jsonl 人工维护） | F10（RAGv4） |
| `review_bank.py` | 题库人工审核 / 人工评分的 Excel(CSV) 工作表导出与回填 | F10（RAGv4） |
| `gen_demo_examples.py` | 从已审核题库生成 F08 演示示例清单（能力标注 + 按实测时延/截断筛题） | F08（RAGv5） |
| `smoke_deploy.py` | 部署冒烟六步：health / 页面 / 示例题接口 / 逐条示例题 / 缓存命中 / 限流；兼作演示预热 | F08/F06（RAGv5） |
| `check_vector_consistency.py` | 向量一致性：条数校验 + Chroma top-k 与暴力余弦 top-k 抽样重合率（下限 0.9） | F04/F11（RAGv5） |
| `compare_chunking.py` | 分块参数对比实验：建索引变体 → 跑评测 → 汇总报告 | F11（RAGv5） |
| `fetch_place_coords.py` | 批量获取高德坐标（复用旧项目编码器 + 断点续跑 + 配额保护 + 按（地名+省）去重） | F09/F07（RAGv5） |
| `build_place_coord_dict.py` | 把历次高德产物合并成**按名称索引**的坐标词典（换代后 `place_id` 会错位，必须翻成名称键） | 发布 |
| `apply_place_coords.py` | 按（名称+省/市/朝代）分层把词典回填进 `places` 表：同名歧义保护 + 未命中清单 | 发布 |
| `build_lineage.py` | 数据血缘（`data/release/lineage.json`）+ `--check` 校验 schema 与 run→demo→runtime 一致性 | 发布 |
| `audit_chroma_segments.py` | Chroma collection/segment 映射、孤儿目录审计、四方计数一致性（不一致即退出非零） | 发布 |
| `build_artifact_manifest.py` | 制品清单 + `verify`（含逻辑哈希）/ `verify-sums`（标准物理 SHA256SUMS） | 发布 |
| `gen_sbom.py` | 生成/校验 SPDX 2.3 SBOM（Python + Node 依赖） | 发布 |
| `lock_hashes.py` | 给 Python 锁文件补 `--hash`（走 PyPI JSON API，不下载制品）；`--check` 供 CI 门禁 | 发布 |
| `fetch_data_artifact.py` | 数据制品打包（`--pack`）/ 下载 / 预期哈希校验 / 可选 gpg 验签 / 安全解包（zip-slip 与范围限制） | 发布 |
| `build_release_bundle.py` | 组装完整 release 包（源码 + 数据 + dist + 证据 + SBOM + 依赖声明） | 发布 |
| `check_docs.py` | 文档一致性：相对链接、current 口径、`.env.example` 键、**数据计数与清单一致** | 文档 |
| `check_secrets.py` | 明文密钥扫描（提交与发布前门禁） | 安全 |

## 用法

```bash
# 从 RAG/ 根执行（依赖相对 import 项目包）
# 版本号示例用当前实际存在的版本；<v> 也可写成当天日期 _v1（export 未指定时自动生成）
python scripts/export_snapshot.py --version 20260915_v1   # 可省 --version（自动取当天 vN）
python scripts/build_index.py --version 20260915_v1        # 默认取最新快照；有向量密钥则同时嵌入并写 Chroma
python scripts/build_index.py --no-embeddings              # 无向量密钥时只建 FTS5（向量写空占位）
python scripts/build_index.py --vectors-only --version 20260915_v1   # 只补/重建向量（断点续跑，不重切分）
python scripts/build_index.py --rebuild-chroma --version 20260915_v1 # 从 npy 审计副本重建 Chroma（不调云端）
python scripts/run_pipeline.py                             # 一键全流程

# 地点坐标三步（取 → 并词典 → 回填）；发布流程里是 ②b/②c，见 scripts/publish.py
python scripts/build_place_coord_dict.py                  # 合并历次高德产物为名称键词典（自动挑带坐标的备份库）
python scripts/apply_place_coords.py --dry-run            # 先看命中率（分层匹配 + 歧义保护）
python scripts/apply_place_coords.py --yes                # 写回 places 表
python scripts/fetch_place_coords.py --dry-run             # 只补"未命中清单"里的新地点（调 API，注意额度）
python scripts/fetch_place_coords.py --limit 1000          # 抓完再跑上面两条，坐标就补上了

# 在线服务（RAGv2，需 fastapi/uvicorn，见 RAG/requirements.txt）
python scripts/run_server.py --port 8000 --version 20260915_v1   # SSE 问答服务（同源托管前端 dist）
# 人工审核回填（RAGv2 F09 增强；decisions 结构见 data/snapshot/apply_audit.py）
python scripts/apply_audit.py --decisions audit_decisions.json

# 部署与运维（RAGv5）
python scripts/smoke_deploy.py --base http://127.0.0.1:8000   # 冒烟（演示前跑一次即预热缓存）
python scripts/check_vector_consistency.py                    # 向量一致性抽检（抽样比对暴力余弦）
python scripts/gen_demo_examples.py                           # 生成 F08 示例清单（含实测时延）
python scripts/compare_chunking.py                            # 分块参数对比实验

# 发布链路（详见 docs/deploy.md 第九节）
python scripts/lock_hashes.py --check                     # 锁文件是否每条需求都带 --hash
python scripts/build_lineage.py --version 20260915_v1 && python scripts/build_lineage.py --check
python scripts/audit_chroma_segments.py --version 20260915_v1
python scripts/gen_sbom.py generate --version 20260915_v1 && python scripts/gen_sbom.py validate
python scripts/build_artifact_manifest.py build --version 20260915_v1 --require-clean
python scripts/build_artifact_manifest.py verify && python scripts/build_artifact_manifest.py verify-sums
python scripts/build_release_bundle.py --version 20260915_v1 --smoke-report logs/smoke_release.json
```

运行环境：**Python 3.11**（本地 conda 环境 `china-war-py311`，即 `conda activate china-war-py311`；
口径见 [../README.md](../README.md) 第五节），需已装 fastapi/uvicorn/openai/chromadb。

## 约定

- 脚本只做**编排**，业务逻辑在 `data/snapshot` / `data/index` 层，脚本保持薄。
- 日志输出到控制台并追加 `logs/rag.log`。
- 参数不足时打印帮助并退出非 0。
- **地点坐标不在"导入"里，换代必须走上面那三步。** `backend/import_json_to_sqlite.py` 会先
  `DELETE FROM places` 再按产物插入，而产物里**没有经纬度字段**（地点只有名称+行政区），
  所以坐标只能在导入**之后**回填。旧链路是导入后手工跑一次旧项目的 `geocoding/import`，
  它按 `place_id` 写库——而 `place_id` 是**旧库**主键，换代重导后全部错位（实测旧 id 268 是
  "洛水"、新 id 268 是"大梁"），按 id 回写会写到错的地点上，所以本仓库改成"名称键词典 +
  分层匹配"。2026-09-27 换代就是因为这一步不在流程里又没人发现：5527 个地点 **0 条坐标**，
  4819 条高德坐标躺在磁盘上，地图页只剩后端内置的省/市中心点兜底（"可定位事件 130、
  低置信坐标 55"就是这么来的）。现在它是 `scripts/publish.py` 的 ②b/②c 两步，
  核对阶段还有 `MIN_PLACES_WITH_COORD` 兜底。
