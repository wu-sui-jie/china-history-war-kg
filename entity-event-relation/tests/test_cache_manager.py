"""缓存索引必须原子写，悬挂条目必须被摘除。

`CacheManager._save_index` 若用 `open(index_path, "w")` 全量重写，这个调用一执行
就把索引文件截断成 0 字节，随后才写入内容；进程在中间被杀（Ctrl-C、OOM、断电）就留下半截
JSON。下一次 `_load_index` 解析失败 → `CacheManager.__init__` 抛异常 → 整个抽取任务还没
开始就崩，而且 cache/ 里那 22MB 缓存在人工删掉索引前完全不可用。

所以索引与结果文件都必须"写临时文件再 `os.replace` 原子替换"，并在读到悬挂/损坏条目时
顺手摘掉。用例用"写一半抛异常"模拟被中途杀掉的进程——比真去 kill 进程稳定，
且同样能证明"任何时刻磁盘上的索引都是完整的旧版或新版"。
"""

import json

import pytest

from war_extraction.core.cache_manager import CacheManager


def _temp_files(cache_dir):
    return sorted(p.name for p in cache_dir.iterdir() if p.name.endswith(".tmp"))


def _crash_dump_at(monkeypatch, fail_from_call: int):
    """
    把 json.dump 换成"第 fail_from_call 次起先写半截再抛异常"。

    为什么按调用次数区分：set() 会写两次文件（先结果文件、后索引），而索引那次才是
    致命的那次。用次数而不是文件名，是因为文件名在原子写实现里是随机临时名。
    """
    real_dump = json.dump
    calls = {"n": 0}

    def flaky_dump(obj, fp, **kwargs):
        calls["n"] += 1
        if calls["n"] < fail_from_call:
            return real_dump(obj, fp, **kwargs)
        fp.write('{"半截的": ')
        raise KeyboardInterrupt("模拟写入中途 Ctrl-C")

    monkeypatch.setattr("war_extraction.core.cache_manager.json.dump", flaky_dump)


def test_set_and_get_roundtrip(tmp_path):
    """基本功能不受影响：写进去能读回来。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("赤壁之战", {"events": ["赤壁之战"]}, {"stage": "events"})

    assert cache.get("赤壁之战", {"stage": "events"}) == {"events": ["赤壁之战"]}
    assert cache.get("别的文本", {"stage": "events"}) is None


def test_index_survives_crash_mid_write(tmp_path, monkeypatch):
    """索引写到一半崩溃后，磁盘上的索引仍是上次的完整内容。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("第一次", {"v": 1})
    before = cache.index_path.read_bytes()
    assert json.loads(before)

    with monkeypatch.context() as m:
        _crash_dump_at(m, fail_from_call=2)  # 第 1 次（结果文件）正常，第 2 次（索引）崩
        with pytest.raises(KeyboardInterrupt):
            cache.set("第二次", {"v": 2})

    # 旧索引纹丝不动——崩在 os.replace 之前，磁盘上还是旧版
    assert cache.index_path.read_bytes() == before
    assert json.loads(cache.index_path.read_text(encoding="utf-8"))

    # 崩溃写入留下的临时文件不许堆在 cache/ 里
    assert _temp_files(tmp_path) == []

    # 重启后索引仍可用，且不含那次失败的条目
    reopened = CacheManager(cache_dir=str(tmp_path))
    assert reopened.get("第一次", None) == {"v": 1}
    assert reopened.get("第二次", None) is None
    # 说明：失败那次的**结果文件**会作为孤儿留在 cache/ 里（索引没记账、读不到它）。
    # 清理过期孤儿由 collect_garbage / prune_expired 负责，见 test_paths_and_cache_gc.py。


def test_crash_while_writing_result_file_leaves_no_garbage(tmp_path, monkeypatch):
    """结果文件同样是原子写：写一半崩掉不留不可解析的缓存文件，索引也不记这条。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("已有条目", {"v": 1})
    before = cache.index_path.read_bytes()

    with monkeypatch.context() as m:
        _crash_dump_at(m, fail_from_call=1)  # 第 1 次（结果文件）就崩
        with pytest.raises(KeyboardInterrupt):
            cache.set("会失败的新文本", {"v": 2})

    assert _temp_files(tmp_path) == []
    assert cache.index_path.read_bytes() == before

    reopened = CacheManager(cache_dir=str(tmp_path))
    assert reopened.get("已有条目", None) == {"v": 1}
    assert reopened.get("会失败的新文本", None) is None


def test_dangling_index_entry_is_pruned(tmp_path):
    """索引里有、结果文件已不在的条目，读到时摘掉并落盘，不再无限堆积。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("会被删掉文件的文本", {"v": 1})
    text_hash = next(iter(cache.index))

    (tmp_path / f"{text_hash}.json").unlink()

    assert cache.get("会被删掉文件的文本") is None
    assert text_hash not in cache.index

    # 摘除必须落盘，否则下次启动又从磁盘读回悬挂条目
    reopened = CacheManager(cache_dir=str(tmp_path))
    assert text_hash not in reopened.index


def test_corrupt_entry_file_is_pruned(tmp_path):
    """结果文件被外力写坏时按未命中处理并摘除，而不是抛异常打断整条流水线。"""
    cache = CacheManager(cache_dir=str(tmp_path))
    cache.set("文本", {"v": 1})
    text_hash = next(iter(cache.index))
    (tmp_path / f"{text_hash}.json").write_text('{"截断的', encoding="utf-8")

    assert cache.get("文本") is None
    assert text_hash not in cache.index


def test_corrupt_index_does_not_block_startup(tmp_path):
    """半截/损坏的索引不该让任务启动即崩：按空索引继续。"""
    (tmp_path / "cache_index.json").write_text('{"截断的索引": ', encoding="utf-8")

    cache = CacheManager(cache_dir=str(tmp_path))
    assert cache.index == {}

    # 而且新的写入能把它修回正常状态
    cache.set("新文本", {"v": 1})
    assert json.loads(cache.index_path.read_text(encoding="utf-8"))
