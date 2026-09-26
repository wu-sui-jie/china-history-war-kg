"""F11 向量索引构建（占位 + 可选云端调用）。

已确认使用云端中文文本向量模型，接口经 EMBEDDING_* 环境变量配置。
本层：
- 无密钥 / INDEX_BUILD_EMBEDDINGS=false → 生成空占位（ids.json + embeddings.npy 为空），
  目录结构不变，F04 可检测到"无向量"自动只用 FTS5。
- 有密钥 → 分批调用 embed 写 npy（接口适配函数 embed_texts 待在线阶段按实际服务补全）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from lib.json_io import write_json


def build_vector_placeholder(index_dir: Path, chunks: list[dict]) -> dict:
    """无向量模型时生成空占位，返回统计。"""
    vec_dir = index_dir / "vectors"
    vec_dir.mkdir(parents=True, exist_ok=True)
    ids = [c["chunk_id"] for c in chunks]
    write_json(vec_dir / "ids.json", ids)
    # 空矩阵占位：numpy 保存 0 行便于加载方探测
    np.save(vec_dir / "embeddings.npy", np.zeros((0, 0), dtype=np.float32))
    return {"mode": "placeholder", "chunks": len(ids), "dim": 0}
