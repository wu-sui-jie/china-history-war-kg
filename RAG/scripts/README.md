# scripts

**归属功能：F09 / F11 离线链路命令行入口 + RAGv2 在线服务启动/人工审核回填。**

把数据治理（snapshot 层）、索引构建（index 层）、在线服务与治理增强封装成可一键运行的命令，
统一从 `RAG/` 根目录执行。

## 脚本

| 脚本 | 功能 | 对应功能 |
| --- | --- | --- |
| `export_snapshot.py` | 治理并导出快照（实体/关系/词典/报告） | F09 |
| `build_index.py` | 基于快照构建 FTS5 + 向量索引 | F11 |
| `run_pipeline.py` | 串联 F09 → F11 全流程 | F09+F11 |
| `run_server.py` | 启动 RAGv2 SSE 问答服务（F02–F06） | F02–F06 |
| `apply_audit.py` | 人工审核决定回填（audit_decisions.json → 新版本快照） | F09 增强 |

## 用法

```bash
# 从 RAG/ 根执行（依赖相对 import 项目包）
python scripts/export_snapshot.py --version 20260904_v3   # 可省 --version（自动取当天 vN）
python scripts/build_index.py --version 20260904_v3        # 默认取最新快照
python scripts/build_index.py --no-embeddings              # 无向量密钥时只建 FTS5
python scripts/run_pipeline.py                             # 一键全流程

# 在线服务（RAGv2，需 fastapi/uvicorn，见 RAG/requirements.txt）
python scripts/run_server.py --port 8000                   # SSE 问答服务
# 人工审核回填（RAGv2 F09 增强；decisions 结构见 data/snapshot/apply_audit.py）
python scripts/apply_audit.py --decisions audit_decisions.json
```

说明：`run_server.py` / `apply_audit.py` 属 RAGv2 新增；运行环境建议用
已装 fastapi/uvicorn/openai 的 Python 3.11 环境（本机 `E:/anaconda/envs/AI_Agent`）。

## 约定

- 脚本只做**编排**，业务逻辑在 `data/snapshot` / `data/index` 层，脚本保持薄。
- 日志输出到控制台并追加 `logs/rag.log`。
- 参数不足时打印帮助并退出非 0。
