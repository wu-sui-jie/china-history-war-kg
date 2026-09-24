"""entity-event-relation 源码路径注入（全仓库唯一入口）。

EER 目前没有正式打包，`src.*` 包只在仓库里以同级目录 `entity-event-relation/` 的形式存在，
运行时必须先把它加进 sys.path。原先这段 hack 只写在 app.py 里；LLM 流水线拆出后两个模块
都要用同一个路径，抽到这里避免各写一份、各写错一个相对层级。

P2-4 的收口方式是给 EER 加 pyproject 并把包名正式化，届时本模块连同调用点一起删掉。
"""

import os
import sys

EER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "entity-event-relation")
)


def ensure_eer_on_path() -> str:
    """把 EER 目录插到 sys.path 首位（幂等），返回该目录。"""
    if EER_DIR not in sys.path:
        sys.path.insert(0, EER_DIR)
    return EER_DIR
