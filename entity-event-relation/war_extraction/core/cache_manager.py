"""
缓存管理模块
避免重复处理相同文本，节省 API 费用
"""
from __future__ import annotations

import json
import glob
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

#: 以本文件位置锚定项目根（entity-event-relation/）——与 llm_client 的做法一致。
#: Changed 2026-09-25（第 11 轮 C-2）：默认 cache_dir 原先是相对当前工作目录的 "cache"，
#: 于是"从仓库根跑"和"从模块目录跑"会落到两个不同的缓存上（一个读不到另一个的结果）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 默认缓存目录（项目根下的 cache/）
DEFAULT_CACHE_DIR = _PROJECT_ROOT / "cache"

#: 孤儿条目的宽限期（秒）。刚写进缓存、索引还没记账的结果文件不能被当成孤儿删掉，
#: 并发的另一个进程也可能正写到一半；比这个更旧的、索引里没有的条目才回收。
ORPHAN_GRACE_SECONDS = 3600

#: 默认的 TTL 口径（天）——**只在显式调用 prune_expired 时**生效，见该方法的说明。
DEFAULT_TTL_DAYS = 180

#: 缓存目录里允许存在的非条目文件
_INDEX_FILENAME = "cache_index.json"


class CacheManager:
    """
    缓存管理器
    基于文本、模型、提示词版本、阶段和分段参数实现缓存
    """

    def __init__(self, cache_dir: str = None, ttl_days: int = None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(exist_ok=True)
        #: TTL（天）。None = 读取时不按时间失效（默认）；见 prune_expired 的说明。
        self.ttl_days = ttl_days

        # 加载缓存索引
        self.index_path = self.cache_dir / _INDEX_FILENAME
        self.index = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """加载缓存索引"""
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                # 改原子写之前留下的半截文件会让整个抽取任务启动即崩。
                # 索引只是"省钱用的加速表"，损坏时按空索引继续（不静默删文件，便于人工排查）。
                print(f"  [缓存索引损坏] {self.index_path} 解析失败（{e}），本次按空索引继续")
        return {}

    def _write_json_atomic(self, path: Path, payload: Any):
        """
        写 JSON 到临时文件再 os.replace 原子替换。

        Added 2026-09-25（EER-11）：原实现直接 open(path, "w") 全量重写，
        进程在中途被杀（Ctrl-C、OOM、断电）就会留下截断的 JSON；索引尤其致命——
        下次启动 _load_index 解析失败，整个缓存连同任务一起不可用。
        os.replace 在同一文件系统内是原子的，因此任何时刻读到的都是完整的旧版或新版。
        """
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            # 失败时清掉临时文件，别在 cache/ 里留垃圾
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _save_index(self):
        """保存缓存索引（原子替换）"""
        self._write_json_atomic(self.index_path, self.index)

    def _drop_entry(self, text_hash: str, reason: str):
        """
        摘除失效条目（索引里有、结果文件缺失或损坏），并立刻落盘。

        Added 2026-09-25（EER-11）：悬挂条目此前会永远留在索引里，
        每次读都白跑一次文件系统、每次都判"未命中"，索引只增不减。
        """
        self.index.pop(text_hash, None)
        self._save_index()
        print(f"  [缓存条目已摘除] {text_hash[:8]}…（{reason}）")

    def _compute_hash(self, text: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Changed 2026-04-20 16:33:36 +08:00: Cache keys include extraction
        context so optimized prompts do not reuse stale DeepSeek responses.
        """
        payload = {
            "text": text,
            "context": context or {}
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
        """
        获取缓存结果

        Args:
            text: 原始文本

        Returns:
            缓存的结果字典，或 None（未命中）
        """
        text_hash = self._compute_hash(text, context)

        if text_hash in self.index:
            cache_file = self.cache_dir / f"{text_hash}.json"
            if not cache_file.exists():
                self._drop_entry(text_hash, "结果文件不存在")
                return None
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    result = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self._drop_entry(text_hash, f"结果文件不可读：{e}")
                return None
            print(f"  [缓存命中] 使用已缓存结果 ({context.get('stage') if context else 'legacy'})")
            return result

        return None

    def set(self, text: str, result: Dict, context: Optional[Dict[str, Any]] = None):
        """
        保存结果到缓存

        Args:
            text: 原始文本
            result: 抽取结果
        """
        text_hash = self._compute_hash(text, context)

        # 保存结果文件（同样原子写：半截的结果文件比没有缓存更糟）
        cache_file = self.cache_dir / f"{text_hash}.json"
        self._write_json_atomic(cache_file, result)

        # 更新索引
        self.index[text_hash] = {
            "text_preview": text[:100] + "..." if len(text) > 100 else text,
            "cache_file": str(cache_file),
            "context": context or {},
            "cached_at": time.time(),
            "size_bytes": cache_file.stat().st_size if cache_file.exists() else None,
        }
        self._save_index()

        print(f"  [缓存已保存] {cache_file}")

    def _save_index(self):
        """保存缓存索引（原子替换），顺手回收孤儿条目文件。"""
        self._write_json_atomic(self.index_path, self.index)
        self.collect_garbage()

    def collect_garbage(self, grace_seconds: int = ORPHAN_GRACE_SECONDS) -> List[str]:
        """
        GC（EER-11 的另一半）：删掉**索引里没有**的条目文件。

        Added 2026-09-25（第 11 轮 C-2）：改原子写之后，写结果文件成功、写索引前崩掉
        会留下永不失效的孤儿文件（索引没记账，永远读不到它，也永远没人删它）。
        现在索引每重写一次就把这类文件回收掉。

        Args:
            grace_seconds: 宽限期。比它更新的孤儿文件先留着——可能是另一个进程
                刚写完还没记账，或者我们自己正处在"写完文件、还没写索引"的瞬间。

        Returns:
            被删除的文件路径列表（便于测试与人工核对）
        """
        keep = {entry_hash + ".json" for entry_hash in self.index}
        keep.add(_INDEX_FILENAME)
        now = time.time()
        removed = []
        for path in sorted(glob.glob(str(self.cache_dir / "*.json"))):
            name = os.path.basename(path)
            if name in keep:
                continue
            try:
                if now - os.path.getmtime(path) < grace_seconds:
                    continue
                os.remove(path)
                removed.append(path)
            except OSError:
                # 被别的进程占着（Windows 上常见）就跳过，下次再收
                continue
        if removed:
            print(f"  [缓存 GC] 回收 {len(removed)} 个孤儿条目文件")
        return removed

    def prune_expired(self, ttl_days: int = None, dry_run: bool = True) -> List[str]:
        """
        TTL（EER-11 的另一半）：挑出超过 ``ttl_days`` 天没被更新的条目并（可选）删掉。

        **刻意不自动调用**，也没接进 `get()`。理由：这份缓存同时是当前抽取产物的
        **可复现路径**（同模型 + 同提示词版本 + 同分段参数才算命中，命中即可零成本重跑），
        按时间自动失效会让人在毫无察觉的情况下把 22MB、195 段的缓存放掉，
        下次抽取变成整本重跑（真花钱、且产物换代）。所以 TTL 留成显式动作：

            python -c "from war_extraction.core.cache_manager import CacheManager; \\
                       print(CacheManager().prune_expired(ttl_days=180, dry_run=True))"

        体积控制主要由 `collect_garbage`（回写索引时自动跑）负责。

        Args:
            ttl_days: 保留天数，默认 `DEFAULT_TTL_DAYS`
            dry_run: True（默认）只报告不删；确认后传 False 才真删

        Returns:
            过期条目的 text_hash 列表（dry_run 时为"将被删除"的）
        """
        ttl_days = DEFAULT_TTL_DAYS if ttl_days is None else int(ttl_days)
        deadline = time.time() - ttl_days * 86400
        expired = []
        for text_hash, entry in list(self.index.items()):
            cached_at = entry.get("cached_at")
            if cached_at is None:
                # 老条目没有 cached_at（改这个字段之前写的），退化成看文件时间
                cache_file = self.cache_dir / f"{text_hash}.json"
                try:
                    cached_at = cache_file.stat().st_mtime
                except OSError:
                    cached_at = None
            if cached_at is None or cached_at < deadline:
                expired.append(text_hash)
        if not dry_run:
            for text_hash in expired:
                cache_file = self.cache_dir / f"{text_hash}.json"
                try:
                    os.remove(cache_file)
                except OSError as e:
                    # 删不掉（被占用）就留着文件，但索引条目照摘——下次 GC 会再收
                    print(f"  [缓存 TTL] 条目文件删除失败（{cache_file}）: {e}")
                self.index.pop(text_hash, None)
                print(f"  [缓存 TTL] 已淘汰 {text_hash[:8]}…（超过 {ttl_days} 天未更新）")
            self._save_index()
        return expired
