"""Chroma 持久化向量库（RAGv5 D2）。

口径（docs/CHANGELOG.md 的 RAGv5 阶段条目）：
- 落盘位置：`data/index/<索引版本>/vectors/chroma/`（`PersistentClient`，进程内嵌入，无需独立服务）
- 相似度空间：**建集合时锁定 `hnsw:space=cosine`**（事后改无效，只能重建集合）
- `embedding_function=None`：显式禁用默认嵌入模型，避免混入与我们不同的向量空间
- 元数据只接受标量（str/int/float/bool）：列表字段要连接成字符串，缺失字段**省略 key**
  （Chroma 拒绝 None 值）
- **查询返回的是 distance = 1 − 余弦相似度（越小越相似）**，换算在读取侧完成
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


# 需要写入向量库的元数据列（与 server/text/searcher.py 的过滤项一致）
META_FIELDS = ("chunk_type", "doc_id", "event_id", "event_name", "event_type", "dynasty")


def chroma_dir(index_dir: Path) -> Path:
    return Path(index_dir) / "vectors" / "chroma"


def to_metadatas(chunks: Iterable[dict]) -> List[dict]:
    """片段 → Chroma metadata（只保留标量；缺失/空值省略 key）。"""
    metas: List[dict] = []
    for c in chunks:
        m: dict = {}
        for f in META_FIELDS:
            v = c.get(f)
            if v is None or v == "":
                continue          # 不写 None：Chroma 会直接拒绝
            m[f] = str(v)
        metas.append(m)
    return metas


def build_collection(index_dir: Path, ids: List[str], embeddings,
                     metadatas: List[dict], collection_name: str,
                     rebuild: bool = True, logger=None) -> dict:
    """写入（或重建）Chroma 集合，返回统计信息。"""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    path = chroma_dir(index_dir)
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
    if rebuild:
        try:
            client.delete_collection(collection_name)
        except Exception:  # noqa: BLE001
            pass
    col = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},   # 建库时锁定，事后改无效
        embedding_function=None,             # 禁用默认嵌入模型（避免向量空间混用）
    )
    batch = 512
    for i in range(0, len(ids), batch):
        col.add(
            ids=ids[i:i + batch],
            embeddings=[list(map(float, v)) for v in embeddings[i:i + batch]],
            metadatas=metadatas[i:i + batch],
            documents=None,
        )
    count = col.count()
    if logger:
        logger.info(f"Chroma 集合已写入: {collection_name} @ {path}（count={count}）")
    return {"collection": collection_name, "count": int(count), "path": str(path),
            "space": "cosine"}


def load_collection(index_dir: Path, collection_name: str):
    """加载集合（不可用时返回 None，由调用方降级关键词）。

    **客户端会被登记下来**（第 14 轮审计 P2-13）：原实现只把 collection 返回出去、
    把 `PersistentClient` 丢掉，而 chromadb 内部按路径缓存客户端——那个对象连同
    `chroma.sqlite3` 的连接与文件锁会一直留到进程退出，同进程的热重载或重复构建
    就会撞上锁冲突。登记之后由 `release_clients()` 在停机统一释放。
    """
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        path = chroma_dir(index_dir)
        if not path.exists():
            return None
        client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
        _CLIENTS[str(path)] = client
        return client.get_collection(name=collection_name, embedding_function=None)
    except Exception:  # noqa: BLE001
        return None


# 本进程创建的 chromadb 客户端（按路径去重；第 14 轮审计 P2-13）
_CLIENTS: dict = {}


def client_count() -> int:
    """当前登记的客户端数（health 用：只是个整数，不含路径）。"""
    return len(_CLIENTS)


def release_clients() -> int:
    """释放本进程创建的 chromadb 客户端，返回释放个数。

    **这一版 chromadb 没有 per-client 的 `close()`**（1.3.4 实测：PersistentClient
    上只有 get/create/delete 那一套）。能做的两件事都做掉：

    1. `Client.clear_system_cache()`——chromadb 把客户端按路径缓存在一个进程级
       "system" 里，这个调用清掉那份缓存，是这一版提供的唯一释放手段；
    2. 丢掉我们自己的引用，让对象可被回收（只清缓存而不丢引用的话，
       我们手里那个仍持有连接）。

    将来升级 chromadb 时请重新确认有没有真正的 `close()`：有就优先用它，
    并把这里换掉——这句话是留给下一个升级的人看的。
    """
    released = len(_CLIENTS)
    _CLIENTS.clear()
    if not released:
        return 0
    try:
        import chromadb

        clear = getattr(getattr(chromadb, "api", None), "client", None)
        clear = getattr(getattr(clear, "Client", None), "clear_system_cache", None)
        if callable(clear):
            clear()
    except Exception:  # noqa: BLE001 - 释放失败不该影响停机
        pass
    return released


class ChromaClients:
    """把"释放 chromadb 客户端"包装成 `Runtime.resources()` 认得的形态。

    Runtime 的收尾协议是"有 `aclose` 或 `close` 就调用它"，所以这里只提供一个
    `close()`。放在本模块而不是 Runtime 里，是为了让"谁创建客户端、谁负责登记"
    留在创建处附近。
    """

    @staticmethod
    def close() -> int:
        return release_clients()
