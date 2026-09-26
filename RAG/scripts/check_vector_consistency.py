"""向量索引一致性抽检（RAGv5）。

两件事：
1. **条数一致性**：`chroma.count() == len(ids.json) == embeddings.npy 行数`；
   （npy 是审计副本，不参与检索，但它是"换机器免重嵌入"的依据，必须与库一致）
2. **近似召回抽检**：用题库问题做查询，比较"Chroma HNSW top-k"与"暴力余弦 top-k"的重合率
   —— HNSW 是近似检索，可能静默漏掉本应召回的片段；重合率低于阈值要记录并评估。

用法：
  python scripts/check_vector_consistency.py --sample 20 --top-k 20
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings          # noqa: E402
from data.index import chroma_store                # noqa: E402
from data.index.embeddings import build_embed_fn   # noqa: E402
from server.runtime import resolve_version         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="向量索引一致性抽检")
    ap.add_argument("--version", default="", help="索引版本（默认最新一致版本）")
    ap.add_argument("--sample", type=int, default=20, help="抽样问题数（默认 20）")
    ap.add_argument("--top-k", type=int, default=20, help="重合率比较的 top-k（默认 20）")
    ap.add_argument("--min-overlap", type=float, default=0.9,
                    help="重合率下限（低于则退出码非 0，默认 0.9）")
    args = ap.parse_args()

    settings = get_settings()
    version, _snap, index_dir = resolve_version(settings, args.version or None)
    vec_dir = index_dir / "vectors"
    ids = json.loads((vec_dir / "ids.json").read_text(encoding="utf-8"))
    mat = np.load(vec_dir / "embeddings.npy", allow_pickle=False)

    print(f"索引: {index_dir.name}（快照 {version}）")
    print(f"ids.json: {len(ids)} 条 | embeddings.npy: {mat.shape}")

    collection = chroma_store.load_collection(index_dir, settings.chroma_collection)
    if collection is None:
        print("✗ Chroma 集合不可加载 → 向量检索会降级关键词")
        return 2
    count = int(collection.count())
    print(f"Chroma 集合 {settings.chroma_collection}: count={count}")
    if not (count == len(ids) == mat.shape[0]):
        print(f"✗ 条数不一致：chroma={count} ids={len(ids)} npy={mat.shape[0]}")
        return 2
    print("✓ 条数一致")

    embed_fn = build_embed_fn(settings)
    if embed_fn is None:
        print("✗ 无法构造 embed_fn（缺密钥/配置），跳过近似召回抽检")
        return 2

    bank_path = Path(f"data/eval/{version}/questions.jsonl")
    questions = []
    if bank_path.exists():
        with open(bank_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    questions.append(json.loads(line)["question"])
    if not questions:
        print(f"（未找到题库 {bank_path}，改用随机片段文本作查询）")
        questions = [f"片段 {i}" for i in range(args.sample)]
    random.seed(42)
    sample = random.sample(questions, min(args.sample, len(questions)))

    # 暴力余弦基准：npy 未做 L2 归一化，这里显式归一化后再点积
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat_norm = mat / norms

    overlaps = []
    for q in sample:
        qv = np.asarray(embed_fn([q])[0], dtype=np.float32)
        qn = qv / (np.linalg.norm(qv) or 1.0)
        # 暴力 top-k
        sims = mat_norm @ qn
        brute_idx = np.argsort(-sims)[: args.top_k]
        brute = {ids[i] for i in brute_idx}
        # Chroma top-k
        res = collection.query(query_embeddings=[list(map(float, qv))],
                               n_results=args.top_k, include=["distances"])
        chroma = set((res.get("ids") or [[]])[0])
        if not brute:
            continue
        overlaps.append(len(brute & chroma) / len(brute))

    if not overlaps:
        print("✗ 未能完成抽检")
        return 2
    mean_ov = statistics.mean(overlaps)
    print(f"\n近似召回抽检：{len(overlaps)} 条问题 × top-{args.top_k}")
    print(f"  Chroma top-k 与暴力余弦 top-k 的重合率：均值 {mean_ov:.3f}"
          f"（最低 {min(overlaps):.3f}，最高 {max(overlaps):.3f}）")
    ok = mean_ov >= args.min_overlap
    print(f"  判定：{'✓ 通过' if ok else '✗ 低于下限'}（下限 {args.min_overlap}）")
    if not ok:
        print("  → 建议：调 HNSW 参数（M / construction_ef / search_ef，参数名以安装版本为准）"
              "或把该限制记录到 RAGv5-开发说明 §六.1")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
