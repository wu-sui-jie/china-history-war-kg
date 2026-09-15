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
| `compare_chunking.py` | 分块参数对比实验：建索引变体 → 跑评测 → 汇总报告 | F11（RAGv5 T8） |
| `fetch_place_coords.py` | 批量获取高德坐标（复用旧项目编码器 + 断点续跑 + 配额保护 + 按（地名+省）去重） | F09/F07（RAGv5） |

## 用法

```bash
# 从 RAG/ 根执行（依赖相对 import 项目包）
python scripts/export_snapshot.py --version 20260904_v3   # 可省 --version（自动取当天 vN）
python scripts/build_index.py --version 20260904_v3        # 默认取最新快照；有向量密钥则同时嵌入并写 Chroma
python scripts/build_index.py --no-embeddings              # 无向量密钥时只建 FTS5（向量写空占位）
python scripts/build_index.py --vectors-only --version 20260904_v2   # 只补/重建向量（断点续跑，不重切分）
python scripts/build_index.py --rebuild-chroma --version 20260904_v2 # 从 npy 审计副本重建 Chroma（不调云端）
python scripts/run_pipeline.py                             # 一键全流程

# 在线服务（RAGv2，需 fastapi/uvicorn，见 RAG/requirements.txt）
python scripts/run_server.py --port 8000                   # SSE 问答服务（同源托管前端 dist）
# 人工审核回填（RAGv2 F09 增强；decisions 结构见 data/snapshot/apply_audit.py）
python scripts/apply_audit.py --decisions audit_decisions.json

# 部署与运维（RAGv5）
python scripts/smoke_deploy.py --base http://127.0.0.1:8000   # 冒烟（演示前跑一次即预热缓存）
python scripts/check_vector_consistency.py                    # 向量一致性抽检（抽样比对暴力余弦）
python scripts/gen_demo_examples.py                           # 生成 F08 示例清单（含实测时延）
python scripts/compare_chunking.py                            # 分块参数对比实验（T8）
```

说明：`run_server.py` / `apply_audit.py` 属 RAGv2 新增；运行环境建议用
已装 fastapi/uvicorn/openai/chromadb 的 Python 3.11 环境（本机 `E:/anaconda/envs/AI_Agent`）。

## 约定

- 脚本只做**编排**，业务逻辑在 `data/snapshot` / `data/index` 层，脚本保持薄。
- 日志输出到控制台并追加 `logs/rag.log`。
- 参数不足时打印帮助并退出非 0。
