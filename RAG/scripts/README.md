# scripts

**归属功能：F09 / F11 离线链路命令行入口。**

把数据治理（snapshot 层）与索引构建（index 层）封装成可一键运行、可参数化的命令，
统一从 `RAG/` 根目录执行。

## 脚本

| 脚本 | 功能 | 对应功能 |
| --- | --- | --- |
| `export_snapshot.py` | 治理并导出快照（实体/关系/词典/报告） | F09 |
| `build_index.py` | 基于快照构建 FTS5 + 向量索引 | F11 |
| `run_pipeline.py` | 串联 F09 → F11 全流程 | F09+F11 |

## 用法

```bash
# 从 RAG/ 根执行（依赖相对 import 项目包）
python scripts/export_snapshot.py --version 20260904_v3   # 可省 --version（自动取当天 vN）
python scripts/build_index.py --version 20260904_v3        # 默认取最新快照
python scripts/build_index.py --no-embeddings              # 无向量密钥时只建 FTS5
python scripts/run_pipeline.py                             # 一键全流程
```

## 约定

- 脚本只做**编排**，业务逻辑在 `data/snapshot` / `data/index` 层，脚本保持薄。
- 日志输出到控制台并追加 `logs/rag.log`。
- 参数不足时打印帮助并退出非 0。
