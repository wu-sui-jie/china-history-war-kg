"""
缓存管理模块
避免重复处理相同文本，节省 API 费用
"""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any


class CacheManager:
    """
    缓存管理器
    基于文本、模型、提示词版本、阶段和分段参数实现缓存
    """

    def __init__(self, cache_dir: str = "cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

        # 加载缓存索引
        self.index_path = self.cache_dir / "cache_index.json"
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
            "context": context or {}
        }
        self._save_index()

        print(f"  [缓存已保存] {cache_file}")
