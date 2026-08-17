"""
缓存管理模块
避免重复处理相同文本，节省 API 费用
"""

import json
import hashlib
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
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self):
        """保存缓存索引"""
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

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
            if cache_file.exists():
                print(f"  [缓存命中] 使用已缓存结果 ({context.get('stage') if context else 'legacy'})")
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)

        return None

    def set(self, text: str, result: Dict, context: Optional[Dict[str, Any]] = None):
        """
        保存结果到缓存

        Args:
            text: 原始文本
            result: 抽取结果
        """
        text_hash = self._compute_hash(text, context)

        # 保存结果文件
        cache_file = self.cache_dir / f"{text_hash}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 更新索引
        self.index[text_hash] = {
            "text_preview": text[:100] + "..." if len(text) > 100 else text,
            "cache_file": str(cache_file),
            "context": context or {}
        }
        self._save_index()

        print(f"  [缓存已保存] {cache_file}")
