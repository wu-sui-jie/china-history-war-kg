# RAGv5 遗留项整改方案

- 文档类型：v5 收口遗留的整改与提交方案
- 状态：**收口项已完成（2026-09-14）**，`.gitignore` 已修（离线链路源码可入库）；提交与推送待确认后执行
- 范围纪律：只动第五阶段自己的代码与文档。前一阶段文档（`RAGv2/RAGv3/RAGv4` 的开发说明、
  阶段审核报告、`docs/changes/` 历史总结）与 `new/` 目录一律不改。
- 依据：2026-09-14 按 `RAGv5-开发说明.md` §三（文件级改动清单）与 §十（文档同步清单）
  逐条复核代码与产物

## 一、收口项：已完成（2026-09-14）

| # | 项 | 内容 | 证据 |
| --- | --- | --- | --- |
| 1 | `requirements.txt` 缺 `chromadb` | 补 `chromadb>=1.3`（按旧清单装环境会静默降级成关键词模式） | `requirements.txt` 末尾新增一节 |
| 2 | `data/index/build.py` 向量接线（T3 计划项） | 新增 `_build_vectors()`：有密钥且 `INDEX_BUILD_EMBEDDINGS` 为真 → 调 `vector_pipeline.build_vector_store`（嵌入 + Chroma）；否则写空占位（在线侧自动降级 keyword，不中断构建） | `tests/test_vector_wiring.py`（3 条分支用例） |
| 3 | `--rebuild-chroma`（T3 计划项） | 从审计副本（`vectors/ids.json` + `embeddings.npy`）重建 Chroma，不调云端；行数/维度/占位态不一致即报错；与 `--vectors-only` 互斥 | 真索引实测 **9,544 条 / 10.6 s**；重建后 `check_vector_consistency.py` 重合率 **0.932**（下限 0.9）；5 条用例 |
| 4 | `demo_mode` 死配置（T1 遗留） | 按开发说明 §4.7 结论②**删除**（示例区恒显示），涉及 `config/defaults.py`、`config/settings.py`、`.env.example` | 全仓 grep 无残留引用；`pytest` 全过 |
| 5 | 文档同步（开发说明 §十 未打勾项） | ① `server/query/README.md`（F02 兜底已实现）、`server/text/README.md`（Chroma 向量与 hybrid）、`scripts/README.md`（4 个新脚本）、根 `README.md`（用例数、启动与部署入口、依赖、密钥来源）；② v5 两份文档收口：规划说明状态行/里程碑/§八-7 `thinking` 边界条目/§4.2 待确认结论，开发说明 M 步骤标记、§十一 快照、§十 清单；③ `docs/RAG_v1/README.md` 与 `docs/README.md` 的 v5 状态行 | 见各文件；`docs/deploy.md` 补 `--rebuild-chroma` 与一步构建说明 |
| 6 | 回归 | 用例 **105 → 134 全过**（含 2026-09-14 的同名朝代消歧 21 条）；真索引一致性抽检通过 | `pytest tests -q` |

> `data/index/vectors.py` 的计划改造**不再执行**（设计变更，已在开发说明 §三 记录）：审计副本写入与
> 断点续跑已由 `vector_pipeline.py` 承担，该文件保持占位时代的空实现、全仓无调用点。

## 二、提交与推送（待确认后执行）

前提：v5 功能与文档已收口（上表），代码只在本地工作区，未提交。

### A1 ✅ `.gitignore` 已修（2026-09-14，离线侧源码不再丢失）

问题：`.gitignore` 原第 7–8 行整目录忽略 `data/snapshot/` 与 `data/index/`，而这两个目录放的是**源码**：

- `git log --all -- data/index data/snapshot` 为空（从未提交）；RAGv1 初始化提交 `ce68a62` 中
  `data/` 只有 `data/__init__.py`；
- 被忽略的 16 个 `.py` 里有本次 v5 新增的 `embeddings.py`、`vector_pipeline.py`、`chroma_store.py`
  与修改过的 `chunking.py`。

改动（已落地）：

```gitignore
# ---- 离线数据产物（不进 Git；路径相对本 .gitignore 所在 RAG/ 目录）----
# snapshot/ 与 index/ 里同时放着 F09/F11 的源码（含 RAGv5 的向量构建），
# 故不整目录忽略，改为"忽略产物、保留源码与说明"（避免源码从未入库）。
data/snapshot/*
!data/snapshot/*.py
!data/snapshot/README.md
data/index/*
!data/index/*.py
!data/index/README.md
data/raw/
data/cache/
logs/
```

**验证结果**：`git status --untracked-files=all` 现列出 **18 个文件**（`data/snapshot/` 8 个 `.py` +
`README.md`，`data/index/` 8 个 `.py` + `README.md`）；`git add -n` 预演纳入的正是这 18 个；
版本目录（`20260904_v2/`、`20260904_v2_c500o100/`）、`vectors/chroma/`、`embeddings.npy`、
`__pycache__/` 仍被忽略（`git check-ignore` 逐项确认）。

附带核查：全仓被忽略的 `.py` 除 `__pycache__` 外仅剩 `logs/` 下 4 个标注"临时脚本"的调试文件
与 `logs/review-archive/` 的审核脚本副本（其正式版已归档在已跟踪的
`data/eval/20260904_v2/review/audit/`）——均非交付源码，保持忽略。

### A2 分支与提交切分

新建 `ragv5-demo-deploy` 分支，分 6 次提交：

| # | 提交内容 |
| --- | --- |
| ① | 固化修正：`.gitignore` + 离线链路 18 个文件（首次入库） |
| ② | 后端与配置：`config/`、`contracts/request.py`、`server/`（含新增 `server/query/prompts.py`、`llm_fallback.py`） |
| ③ | 数据侧：`scripts/`（4 个新脚本 + `build_index.py`）、`data/eval/20260904_v2/demo_examples.json`、`chunk_exp_report.md` |
| ④ | 前端：`frontend/src/api/demo.ts`、`types/contract.ts`、`components/chat/ChatPane.vue`、`styles.css` |
| ⑤ | 测试：`tests/`（8 个新用例文件 + `test_chain_smoke.py` + `test_vector_wiring.py`） |
| ⑥ | 文档：`docs/**`（含阶段总结、部署手册、本方案） |

### A3 提交前安全检查

1. `.env` 未入库（`.gitignore:2` 已忽略）；`git diff --cached | grep -iE "sk-|api[_-]?key"` 应为空；
2. 数据制品（`data/index/<v>/vectors/`、`data/snapshot/<v>/`）保持忽略——公开仓库只带代码、
   脚本与不含密钥的配置；部署时按 `docs/deploy.md` 的数据制品清单拷贝；
3. `logs/`、`data/eval/*/runs/` 忽略生效。

### A4 判据

`git clone . ../RAG-verify` → 新目录内 `python -m pytest tests -q` 全过（134），且
`data/index/embeddings.py`、`vector_pipeline.py`、`chroma_store.py`、`chunking.py` 存在。

## 三、可选治理项（不影响 v5 收口，单独排期）

| # | 项 | 成本 | 说明 |
| --- | --- | --- | --- |
| D1 | ~~28 条 LLM 评分人工抽查复核~~ | — | ✅ **不做**（2026-09-14 用户确认）：由 AI 代理按 `evaluation/grading.py` 口径评估即为对外口径；评分表 `runs/v5_llm_3/scores.jsonl` 与失败清单随时可回看 |
| D2 | 1,215 组同名实体人工回填 | 人工，分批 | 先按"39 条题库命中频次"排序做 Top 50–100，再 `apply_audit.py` → 重建快照与索引 → 复测 F02 歧义降级次数 |
| D3 | 地点坐标补录 | 需外部素材 | 现状：`graph_triple` 证据不带 `entity_id`，1,078 组同名地点只能按名粗筛；无坐标素材时维持列表降级并记为已知边界 |
| D4 | R1 方案①（朝代偏置折进文本分数） | 小改动 | 在 `server/text/scoring.py` 加可配偏置项（默认 0 = 关闭）；判据：`main` 套件文本召回与回答覆盖不低于 `v5_vec_fusion10`，`long_rewrite`/`filter_loss` 无回归 |
