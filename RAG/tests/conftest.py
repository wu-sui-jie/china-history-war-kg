"""pytest 根配置：把 RAG/ 锚进 sys.path。

测试原先依赖 cwd——只能在 RAG/ 目录下 `python -m pytest tests` 运行，
换 rootdir（例如在仓库根跑 `pytest RAG/tests`）就会 import 失败。
这里显式锚定到 RAG 根，与 llm_client 等模块的 `__file__` 锚定做法保持一致。
"""

import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))
