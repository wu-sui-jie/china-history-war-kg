"""pytest 根配置：把 RAG/ 锚进 sys.path，并提供"离线向量客户端"工具。

测试不依赖 cwd：这里显式锚定到 RAG 根，换 rootdir（例如在仓库根跑
`pytest RAG/tests`）也不会 import 失败。
这里显式锚定到 RAG 根，与 llm_client 等模块的 `__file__` 锚定做法保持一致。
"""

import hashlib
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))


def offline_embed_fn(dim: int = 1024):
    """确定性的桩向量客户端：同样的文本永远得到同一个向量，不碰网络。

    为什么需要它：查询侧 embedding 是**网络调用**，测试跑到真端点上
    有两种坏结果——没网/没密钥时静默降级，让"应当走向量"的断言失败（审查当次就有 1 条
    用例这样挂掉）；有网时又要花钱、还受端点波动影响，用例时绿时红。
    这些用例考的是模式透传与结果非空，不是检索质量，所以用桩把"向量链路能不能走通"
    与"外部端点好不好"彻底解耦。

    向量按文本哈希派生（同问题同结果），并归一化到单位长度——零向量会让余弦距离
    失去意义，反而可能返回不出近邻。
    """

    def _embed(texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = [(digest[i % len(digest)] / 127.5) - 1.0 for i in range(dim)]
            norm = sum(value * value for value in raw) ** 0.5 or 1.0
            vectors.append([value / norm for value in raw])
        return vectors

    return _embed
