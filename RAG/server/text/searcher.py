"""F04 文本检索：关键词（FTS5/BM25）+ 向量（Chroma/HNSW 余弦）+ hybrid 融合。

关键词检索在本层自实现 `_query_fts`（AND/OR 召回 + BM25 排序 + 兜底），
在此基础上按需读取 chunks 元信息，并做 min-max 归一化与 mode 标记。

向量检索（RAGv5）：查询侧调云端模型得到问题向量，交给 `data/index/vectors/chroma/`
的 Chroma 集合做余弦近邻检索；**向量不可用（集合缺失/条数不一致/无密钥）时
`vector_available=False` → 由 server/text/scoring.resolve_mode 自动降级关键词**。
`embeddings.npy` 只是审计副本，不参与检索。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from data.index import chroma_store
from data.index import fts as fts_mod
from server.text.scoring import fuse_hybrid

# and_or 模式下：AND 命中少于该数量时并入 OR 结果。"仅在 AND 为空时才兜底"会丢掉
# OR 里更相关的片段（长改写问题 AND 常只命中个别片段）。
# 这些上限由 Settings（TEXT_QUERY_*）配置，下面的默认值仅在直接构造
# TextSearcher（测试）时生效。
DEFAULT_QUERY_MAX_WORDS = 8
DEFAULT_QUERY_AND_WORDS = 5
DEFAULT_QUERY_OR_WORDS = 6
DEFAULT_QUERY_AND_MIN_HITS = 3


def _norm_minmax(raw_scores: list[float]) -> list[float]:
    """min-max 归一化到 0~1；max==min 时该批全部记 0.5（data-contract 约定）。"""
    if not raw_scores:
        return []
    lo, hi = min(raw_scores), max(raw_scores)
    if hi <= lo:
        return [0.5] * len(raw_scores)
    return [(s - lo) / (hi - lo) for s in raw_scores]


class TextSearcher:
    """持有一个索引版本的检索器：关键词（FTS5/BM25）+ 向量（Chroma/余弦）+ hybrid。"""

    def __init__(self, index_dir: Path, source_version: str,
                 top_k: int = 30, collection_name: str = "chunks_v1",
                 embed_fn=None,
                 query_max_words: int = DEFAULT_QUERY_MAX_WORDS,
                 query_and_words: int = DEFAULT_QUERY_AND_WORDS,
                 query_or_words: int = DEFAULT_QUERY_OR_WORDS,
                 and_min_hits: int = DEFAULT_QUERY_AND_MIN_HITS):
        self.index_dir = Path(index_dir)
        self.source_version = source_version
        self.top_k = top_k
        self.fts_path = self.index_dir / "chunks_fts.db"
        self.collection_name = collection_name
        self.embed_fn = embed_fn
        self.collection = None
        # 用词上限（构造方从 Settings 注入；非法值兜回默认，避免 FTS 构造出空查询）
        self.query_max_words = max(1, int(query_max_words or DEFAULT_QUERY_MAX_WORDS))
        self.query_and_words = max(1, int(query_and_words or DEFAULT_QUERY_AND_WORDS))
        self.query_or_words = max(1, int(query_or_words or DEFAULT_QUERY_OR_WORDS))
        self.and_min_hits = max(1, int(and_min_hits or DEFAULT_QUERY_AND_MIN_HITS))

        # ---- 向量链路的可用性（拆成可分别观测的几段）----
        #
        # 单个 `vector_available` 回答的是"制品与客户端装好了吗"，但 health 与评测容易
        # 把它当成"向量检索真的能用"。两者不是一回事：查询侧 embedding 是**网络调用**，
        # 密钥失效 / 端点不可达时制品与客户端依然完好，查询却在运行时静默降级成关键词——
        # 于是出现"健康检查说向量可用、实际走关键词"的假阳性，还会一路掩盖进评测指标。
        # 因此拆成下面几段：
        #
        #   vector_artifact_ready        Chroma 集合能加载且条数与 ids.json 一致（制品）
        #   embedding_client_configured  查询侧 embed_fn 已装配（客户端）
        #   embedding_probe_ok           最近一次真实向量化是否成功（None = 尚未探测过）
        #   last_vector_error            最近一次向量检索失败的原文（排查用）
        #   effective_text_mode          最近一次 search() 实际执行的模式（由其回写）
        self.vector_artifact_ready = False
        self.embedding_client_configured = embed_fn is not None
        self.embedding_probe_ok: Optional[bool] = None
        self.last_vector_error = ""
        # 向量化/近邻查询的累计错误次数（单调递增）。调用方用它算"本次调用里有没有报错"，
        # 从而把"向量通道故障"与"向量通道返回空结果"这两种情况分开。
        self.vector_error_count = 0
        self.effective_text_mode = ""
        # 启动探测向量后端（失败仅影响 mode，不影响关键词检索）
        self.vector_available = False
        self._load_vector_backend()

    # ---- 启动探测 ----
    def _load_vector_backend(self) -> None:
        """向量可用性 = Chroma 集合可加载 且 条数与 ids.json 一致 且 查询侧能嵌入。

        RAGv5 D2：检索只走 Chroma 一条路径；`embeddings.npy` 仅作审计副本（不参与检索）。

        注意这里**刻意不做 embedding 网络探测**：启动时打一次外部调用会把服务可用性
        绑到外部端点的超时上（启动慢、离线环境起不来），而这一步失败本来就能被
        查询期降级兜住。运行期是否真的能用由 `embedding_probe_ok` 如实记录，
        见 vector_status()。
        """
        if self.embed_fn is None:
            return
        ids_path = self.index_dir / "vectors" / "ids.json"
        try:
            import json

            if not ids_path.exists():
                return
            ids = json.loads(ids_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        collection = chroma_store.load_collection(self.index_dir, self.collection_name)
        if collection is None:
            return
        try:
            if int(collection.count()) != len(ids):
                return      # 条数不一致 → 视为不可用（避免错位检索）
        except Exception:  # noqa: BLE001
            return
        self.collection = collection
        self.vector_artifact_ready = True
        self.vector_available = True

    def _note_vector_ok(self) -> None:
        """一次真实的向量化 + 检索成功：这是"向量确实可用"唯一的正面证据。"""
        self.embedding_probe_ok = True
        self.last_vector_error = ""

    def _note_vector_error(self, message: str, exc: Optional[BaseException] = None) -> None:
        """记录向量检索失败：探针置 False，错误原文留给 health 与日志。"""
        detail = f"{message}: {exc}" if exc is not None else message
        self.embedding_probe_ok = False
        self.last_vector_error = detail
        self.vector_error_count += 1

    def vector_status(self) -> dict:
        """向量链路的可观测状态（health 与排查共用，避免各处各拼一份）。

        `declared_available` 是启动时的能力声明；`last_error` 非空表示**声明可用但查询期失败**，
        也就是本次审查要消掉的那种假阳性——health 会据此给出告警。
        """
        return {
            "artifact_ready": bool(self.vector_artifact_ready),
            "embedding_client_configured": bool(self.embedding_client_configured),
            "embedding_probe_ok": self.embedding_probe_ok,
            "last_vector_error": self.last_vector_error,
            "effective_text_mode": self.effective_text_mode,
            "vector_error_count": self.vector_error_count,
            "declared_available": bool(self.vector_available),
        }

    def search_vector(self, query: str, limit: Optional[int] = None,
                      metadata_filter: Optional[dict] = None,
                      dynasty_bias: Optional[list] = None) -> list[dict]:
        """Chroma 向量检索（HNSW + 余弦）。

        注意 Chroma 返回的是 **distance = 1 − 余弦相似度**（越小越相似），
        这里先换算成相似度再按 data-contract 做 min-max 归一化。
        """
        if not (self.collection and self.embed_fn):
            return []
        limit = limit or self.top_k
        try:
            qv = self.embed_fn([query])[0]
        except Exception as exc:  # noqa: BLE001
            # 查询向量化失败 → 返回空，由 resolve_mode/上层决定降级。
            # 但必须把原因留下来：这一条就是"向量声明可用、实际不可用"的现场。
            self._note_vector_error("查询向量化失败（embedding 端点或密钥不可用）", exc)
            return []
        kwargs = {"query_embeddings": [list(map(float, qv))],
                  "n_results": limit, "include": ["distances"]}
        where = self._where_of(metadata_filter)
        if where:
            kwargs["where"] = where
        try:
            res = self.collection.query(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self._note_vector_error("向量近邻查询失败", exc)
            return []
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        if not ids:
            # 空结果不代表链路故障（可能真的没有近邻），但向量化与查询都成功了，
            # 所以探针仍记成功——判据是"能不能用"，不是"有没有命中"。
            self._note_vector_ok()
            return []
        self._note_vector_ok()
        sims = [1.0 - float(d) for d in dists]
        norm = _norm_minmax(sims)
        rows = self._fetch_chunks(list(ids))
        by_id = {r["chunk_id"]: r for r in rows}
        kept, kept_scores = [], []
        for cid, s in zip(ids, norm):
            row = by_id.get(cid)
            if not row:
                continue
            kept.append(row)
            kept_scores.append(s)
        for r, sc in zip(kept, kept_scores):
            r["_score"] = sc
            r["_match"] = "vector"
        if dynasty_bias:
            bias = {b for b in dynasty_bias if b}
            kept.sort(key=lambda r: 0 if r.get("dynasty") in bias else 1)
        return kept

    def search_hybrid(self, query: str, limit: Optional[int] = None,
                      metadata_filter: Optional[dict] = None,
                      keyword_mode: str = "and_or",
                      dynasty_bias: Optional[list] = None,
                      strategy: str = "weighted",
                      keyword_weight: float = 0.5) -> list[dict]:
        """关键词 + 向量融合（策略见 scoring.fuse_hybrid；fallback = 关键词为空才用向量）。"""
        limit = limit or self.top_k
        kw_rows = self.search_keyword(query, limit=limit, metadata_filter=metadata_filter,
                                      keyword_mode=keyword_mode, dynasty_bias=None)
        vec_rows = self.search_vector(query, limit=limit, metadata_filter=metadata_filter,
                                      dynasty_bias=None)
        if strategy == "fallback":
            if kw_rows:
                for r in kw_rows:
                    r["_match"] = "keyword"
                chosen = kw_rows
            else:
                for r in vec_rows:
                    r["_match"] = "vector"
                chosen = vec_rows
        else:
            kw_pairs = [(r["chunk_id"], float(r.get("_score", 0.0))) for r in kw_rows]
            vec_pairs = [(r["chunk_id"], float(r.get("_score", 0.0))) for r in vec_rows]
            fused = fuse_hybrid(kw_pairs, vec_pairs, strategy, keyword_weight)
            kw_ids = {c for c, _ in kw_pairs}
            vec_ids = {c for c, _ in vec_pairs}
            by_id: dict = {}
            for r in kw_rows + vec_rows:
                by_id.setdefault(r["chunk_id"], r)
            chosen = []
            for cid, sc in sorted(fused.items(), key=lambda kv: -kv[1]):
                row = by_id.get(cid)
                if row is None:
                    continue
                row["_score"] = sc
                row["_match"] = ("both" if (cid in kw_ids and cid in vec_ids)
                                 else "keyword" if cid in kw_ids else "vector")
                chosen.append(row)
            chosen = chosen[:limit]
        if dynasty_bias:
            bias = {b for b in dynasty_bias if b}
            chosen.sort(key=lambda r: 0 if r.get("dynasty") in bias else 1)
        return chosen

    @staticmethod
    def _where_of(mf: Optional[dict]) -> Optional[dict]:
        """filters → Chroma where 子句。

        缺失元数据的片段不会命中该子句——与 `_pass_meta` 的"无该元数据视为不匹配"语义一致。
        """
        if not mf:
            return None
        clauses = []
        for key in ("dynasty", "event_type", "chunk_type"):
            vals = [str(v) for v in (mf.get(key) or []) if v]
            if vals:
                clauses.append({key: {"$in": vals}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def search_keyword(self, query: str, limit: Optional[int] = None,
                       metadata_filter: Optional[dict] = None,
                       keyword_mode: str = "and_or",
                       dynasty_bias: Optional[list] = None) -> list[dict]:
        """FTS5 关键词检索，返回已归一化的 chunk dict 列表（保留元信息供 evidence 装配）。

        检索策略（keyword_mode）：
        - and_or（生产口径，默认）：AND 优先、OR 兜底（RAGv2 第 2 步落地版）。
          改写后问题通常含实体全名，AND 能显著压掉 OR 召回的噪音；AND 无结果时
          回退 OR 保证召回（F10 评测用 and/or 拆分对比该策略差异）；
        - and：仅 AND（AND 命中 0 即返回空，便于评测量化 AND 失效面）；
        - or：仅 OR（供评测观察 OR 兜底噪音排序）。

        dynasty_bias：问句自动识别的朝代，只把命中朝代的片段排到前面（稳定排序，
        不剔除任何结果）；显式筛选仍走 metadata_filter 硬过滤。
        注意：本函数返回的顺序不是最终证据顺序——F05 融合会按 `_score`（各条不同）
        重排文本证据，因此文本侧偏置**不影响最终排序**（图谱侧偏置在 F03 策略前
        重排节点、可经 top_k 影响证据集合）。详见 docs/features.md 第二节。
        """
        limit = limit or self.top_k
        words = fts_mod.tokenize(query)[:self.query_max_words]
        if not words:
            return []
        if keyword_mode == "and":
            hits = self._query_fts(words, mode="and", limit=limit)
        elif keyword_mode == "or":
            hits = self._query_fts(words, mode="or", limit=limit)
        else:
            hits = self._query_fts(words, mode="and", limit=limit)
            # AND 优先；命中不足时**并入** OR 结果（而不是只在 AND 为空时才兜底）：
            # 长改写问题的 AND 往往只命中 1–2 个片段，只看 AND 会把 OR 里更相关的片段整批丢掉。
            if len(hits) < min(limit, self.and_min_hits):
                seen = {cid for cid, _ in hits}
                for cid, s in self._query_fts(words, mode="or", limit=limit):
                    if cid not in seen:
                        hits.append((cid, s))
                        seen.add(cid)
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
        # dynasty_bias：命中朝代的片段前置（稳定排序，仅调整顺序不裁剪）
        if dynasty_bias:
            bias = {b for b in dynasty_bias if b}
            kept.sort(key=lambda r: 0 if r.get("dynasty") in bias else 1)
        return kept

    def _query_fts(self, words: list[str], mode: str, limit: int) -> list[tuple]:
        """构造 FTS MATCH：and → 全部词 AND；or → OR。

        OR 兜底剔除单字词（"是/谁/的"等高频虚词会导致整库召回），
        只对长度 ≥2 的实词做 OR，控制噪音。
        """
        if mode == "and":
            q = " AND ".join(f'"{w}"' for w in words[:self.query_and_words])
        else:
            real = [w for w in words if len(w) >= 2]
            if not real:
                return []
            q = " OR ".join(f'"{w}"' for w in real[:self.query_or_words])
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
        # event_type 的过滤范围：
        # - 事件卡片与关系证据有该元数据 → 严格按筛选值过滤；
        # - 原文片段（raw）没有该元数据（书页文本无事件/类型归属）→ **放行**，
        #   否则按战争类型筛选时原文证据会被整批剔除（v4 实测 F01 文本召回 100% → 0%）。
        if mf.get("event_type"):
            row_type = row.get("event_type")
            if row_type and row_type not in mf["event_type"]:
                return False
        if mf.get("chunk_type") and row.get("chunk_type") not in mf["chunk_type"]:
            return False
        return True
