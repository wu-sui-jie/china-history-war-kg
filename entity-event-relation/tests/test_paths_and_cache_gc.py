"""路径锚定、配置缺失不再静默、缓存 GC/TTL。

三条口径都必须钉住：

1. `CacheManager(cache_dir="cache")` 与 `Normalizer(config_dir="config")` 若用**相对当前工作
   目录**的路径，就和同包 `llm_client` 的 `__file__` 锚定口径不一致。
   后果是"从仓库根跑"和"从模块目录跑"落到不同的缓存/配置上，读不到别名表时还完全无声。
2. `Normalizer._load_json` 找不到文件时必须出声，否则别名表没加载这件事在日志里毫无痕迹。
3. 原子写之后，写结果文件成功、写索引前崩掉会留下永不失效的**孤儿条目文件**，`cache/`
   只增不减（已 22MB），必须靠 GC 回收。

用例都不写仓库目录：缓存与配置一律指到 `tmp_path`。
"""

import json
import time
from pathlib import Path

from war_extraction.core.cache_manager import (
    DEFAULT_CACHE_DIR,
    ORPHAN_GRACE_SECONDS,
    CacheManager,
)
from war_extraction.utils.normalizer import DEFAULT_CONFIG_DIR, Normalizer

MODULE_ROOT = Path(__file__).resolve().parents[1]


def test_默认缓存目录锚定模块根_与工作目录无关():
    """默认缓存目录必须是模块根下的 cache/，不是 CWD 下的 cache/。"""
    assert Path(DEFAULT_CACHE_DIR).resolve() == (MODULE_ROOT / "cache").resolve()
    assert Path(DEFAULT_CACHE_DIR).is_absolute()


def test_默认配置目录锚定模块根():
    assert Path(DEFAULT_CONFIG_DIR).resolve() == (MODULE_ROOT / "config").resolve()


def test_默认配置目录下_别名与关系映射确实加载到了():
    """锚定之后从任何工作目录启动都能真正读到别名表（而不是空表）。"""
    normalizer = Normalizer()

    assert normalizer.aliases, "aliases.json 没加载到内容"
    assert normalizer.relation_map, "relation_types.json 没加载到内容"
    assert normalizer.normalize_relation("导致") == "因果关系"


def test_配置缺失时不再静默(tmp_path, capsys):
    """指到不存在的配置目录时必须出提示——静默空表会让归一化悄悄失效。"""
    normalizer = Normalizer(config_dir=str(tmp_path / "不存在的配置目录"))

    out = capsys.readouterr().out
    assert "配置缺失" in out
    assert "aliases.json" in out
    assert normalizer.aliases == {}


def test_孤儿条目文件在索引重写时被回收(tmp_path):
    """索引里没有、也不是刚写的东西 → 回收。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("正常文本", {"v": 1})

    orphan = tmp_path / "00112233445566778899aabbccddeeff.json"
    orphan.write_text('{"孤儿": true}', encoding="utf-8")
    # 把 mtime 拨到宽限期之外（刚写的文件按设计先留着，可能是并发进程还没记账）
    old = time.time() - ORPHAN_GRACE_SECONDS - 60
    import os
    os.utime(orphan, (old, old))

    removed = cache.collect_garbage()

    assert [Path(p).name for p in removed] == [orphan.name]
    assert not orphan.exists()
    assert cache.get("正常文本", None) == {"v": 1}, "在索引里的条目不能被误删"


def test_刚写下的孤儿文件先留着(tmp_path):
    """宽限期内不删：可能是另一个进程刚写完、还没写索引。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("正常文本", {"v": 1})

    fresh_orphan = tmp_path / "ffeeddccbbaa99887766554433221100.json"
    fresh_orphan.write_text('{"刚写的": true}', encoding="utf-8")

    cache.collect_garbage()

    assert fresh_orphan.exists()


def test_索引重写会自动触发孤儿回收(tmp_path):
    """不用人工记得跑 GC：写一次缓存（必然重写索引）就把过期孤儿收掉。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("第一次", {"v": 1})

    orphan = tmp_path / "abcdefabcdefabcdefabcdefabcdefab.json"
    orphan.write_text("{}", encoding="utf-8")
    import os
    old = time.time() - ORPHAN_GRACE_SECONDS - 60
    os.utime(orphan, (old, old))

    cache.set("第二次", {"v": 2})

    assert not orphan.exists()


def test_TTL_默认只报告不删除(tmp_path):
    """prune_expired 默认 dry_run：把"要不要丢缓存"这个决定留给人。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("老条目", {"v": 1})
    # 把 cached_at 改成很久以前
    key = next(iter(cache.index))
    cache.index[key]["cached_at"] = time.time() - 400 * 86400
    cache._save_index()

    expired = cache.prune_expired(ttl_days=180, dry_run=True)

    assert expired == [key]
    assert (tmp_path / f"{key}.json").exists(), "dry_run 不该真删"
    assert key in cache.index


def test_TTL_显式确认后才删除(tmp_path):
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("老条目", {"v": 1})
    key = next(iter(cache.index))
    cache.index[key]["cached_at"] = time.time() - 400 * 86400
    cache._save_index()

    expired = cache.prune_expired(ttl_days=180, dry_run=False)

    assert expired == [key]
    assert not (tmp_path / f"{key}.json").exists()
    assert key not in cache.index
    assert json.loads(cache.index_path.read_text(encoding="utf-8")) == {}


def test_新条目不会被TTL误伤(tmp_path):
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("新条目", {"v": 1})

    assert cache.prune_expired(ttl_days=180, dry_run=False) == []
    assert cache.get("新条目") == {"v": 1}
