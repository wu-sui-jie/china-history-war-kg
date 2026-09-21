# config

实现：RAG 子项目全局配置加载（F09/F11 离线链路 + RAGv2 在线链路共用）。

## 职责

- 定义默认路径与开关（`defaults.py`），从 `.env` / 环境变量覆盖（`settings.py`）。
- 统一提供 `data_dir / snapshot_dir / index_dir / log_dir` 等路径，供 scripts 与各数据层使用。
- 密钥不硬编码，一律经环境变量注入。
- RAGv2 追加在线配置：LLM（base_url/api_key/model/超时/重试）、备用模型（fallback_*）、
  限流（RATE_LIMIT_PER_MINUTE）、回答缓存 TTL、历史轮数、图谱/文本 top-k。

## 输入 / 输出

- 输入：仓库根 `.env`（可选）、环境变量。
- 输出：`Settings` 对象（含各目录路径与治理/索引/在线配置）。

## 文件

| 文件 | 说明 |
| --- | --- |
| `defaults.py` | 默认路径与开关（相对 `RAG/` 根，含在线链路默认值）。 |
| `settings.py` | `get_settings()`：合并 `.env` 后返回 `Settings`。 |

## 使用

```python
from config.settings import get_settings
s = get_settings()
s.snapshot_dir   # RAG/data/snapshot
s.llm_model      # deepseek-v4-flash
s.rate_limit_per_minute
```

## 约定 / 边界

- 本层不依赖 contracts、不产生业务数据。
- 新增可配置项时同步更新 `RAG/.env.example` 与本文档。
