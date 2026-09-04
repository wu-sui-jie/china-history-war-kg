"""F04 文本检索：关键词（FTS5）检索实现。

直接复用 RAGv1 `data/index/fts.query_fts`（OR 召回 + BM25 排序，冒烟基线），
在此基础上按需读取 chunks 元信息，并做 min-max 归一化与 mode 标记。

向量检索目前不可用（F11 占位态 embeddings 为空），加载侧校验见 vectors 模块：
`len(ids) == embeddings.shape[0]` 不满足 → vector_available=False → 自动关键词。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np

from data.index import fts as fts_mod


def _norm_minmax(raw_scores: list[float]) -> list[float]:
    """min-max 归一化到 0~1；max==min 时该批全部记 0.5（data-contract 约定）。"""
    if not raw_scores:
        return []
    lo, hi = min(raw_scores), max(raw_scores)
    if hi <= lo:
        return [0.5] * len(raw_scores)
    return [(s - lo) / (hi - lo) for s in raw_scores]


class TextSearcher:
    """持有一个索引版本的关键词检索器。"""

    def __init__(self, index_dir: Path, source_version: str,
                 top_k: int = 30):
        self.index_dir = Path(index_dir)
        self.source_version = source_version
        self.top_k = top_k
        self.fts_path = self.index_dir / "chunks_fts.db"
        # 延迟加载向量（探测可用性），失败仅影响 mode
        self.vector_available = False
        self._load_vector_flag()

    # ---- 启动探测 ----
    def _load_vector_flag(self) -> None:
        vec_dir = self.index_dir / "vectors"
        ids_path = vec_dir / "ids.json"
        npy_path = vec_dir / "embeddings.npy"
        try:
            if not ids_path.exists() or not npy_path.exists():
                return
            import json
            ids = json.loads(ids_path.read_text(encoding="utf-8"))
            mat = np.load(npy_path, allow_pickle=False)
            if mat.ndim != 2 or mat.shape[0] == 0:
                return
            if len(ids) == mat.shape[0]:
                self.vector_available = True
        except Exception:
            self.vector_available = False

    def search_keyword(self, query: str, limit: Optional[int] = None,
                       metadata_filter: Optional[dict] = None) -> list[dict]:
        """FTS5 关键词检索，返回已归一化的 chunk dict 列表（保留元信息供 evidence 装配）。

        检索策略：AND 优先、OR 兜底（RAGv2 第 2 步落地版，作为 F04 初检基线）。
        改写后问题通常含实体全名，AND 能显著压掉 OR 召回的噪音；AND 无结果时
        回退 OR 保证召回（F10 评测可再比较 OR/BM25 vs AND 差异）。
        """
        limit = limit or self.top_k
        words = fts_mod.tokenize(query)[:8]
        if not words:
            return []
        hits = self._query_fts(words, mode="and", limit=limit)
        if not hits:
            hits = self._query_fts(words, mode="or", limit=limit)
        if not hits:
            return []
        rows = self._fetch_chunks([cid for cid, _ in hits])
        by_id = {r["chunk_id"]: r for r in rows}
        kept = []
        raw_ordered = []
        for cid, s in hits:
            row = by_id.get(cid)
            if not row:
                continue
            if not self._pass_meta(row, metadata_filter):
                continue
            kept.append(row)
            raw_ordered.append(s)
            if len(kept) >= limit:
                break
        if not kept:
            return []
        # FTS bm25 值越小越相关 → 取反后 min-max
        norm = _norm_minmax([-x for x in raw_ordered]) if raw_ordered else []
        for r, sc in zip(kept, norm):
            r["_score"] = sc
        return kept

    def _query_fts(self, words: list[str], mode: str, limit: int) -> list[tuple]:
        """构造 FTS MATCH：and → 全部词 AND；or → OR。

        OR 兜底剔除单字词（"是/谁/的"等高频虚词会导致整库召回），
        只对长度 ≥2 的实词做 OR，控制噪音。
        """
        if mode == "and":
            q = " AND ".join(f'"{w}"' for w in words[:5])
        else:
            real = [w for w in words if len(w) >= 2]
            if not real:
                return []
            q = " OR ".join(f'"{w}"' for w in real[:6])
        con = sqlite3.connect(str(self.fts_path))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS s FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?", (q, limit)
            ).fetchall()
            return [(r["chunk_id"], r["s"]) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()

    def _fetch_chunks(self, chunk_ids: list[str]) -> list[dict]:
        con = sqlite3.connect(str(self.fts_path))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT chunk_id, chunk_type, doc_id, source_version, event_id, "
                "event_name, event_type, dynasty, text FROM chunks WHERE chunk_id IN (%s)"
                % ",".join("?" * len(chunk_ids)),
                chunk_ids,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    @staticmethod
    def _pass_meta(row: dict, mf: Optional[dict]) -> bool:
        if not mf:
            return True
        if mf.get("dynasty") and row.get("dynasty") not in mf["dynasty"]:
            return False
        # event_type 仅事件卡片片段在索引中有冗余列；无该元数据的片段视为不匹配筛选项。
        if mf.get("event_type") and row.get("event_type") not in mf["event_type"]:
            return False
        if mf.get("chunk_type") and row.get("chunk_type") not in mf["chunk_type"]:
            return False
        return True
