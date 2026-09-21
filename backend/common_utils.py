"""跨模块复用的小工具。

这里只放「无业务语义」的纯工具函数：原先在 app.py / import_json_to_sqlite.py /
entity_extract/extractor.py / inference/rule_llm_integration.py 里各写一份，
现收敛到本模块。
"""


def safe_text(value):
    """把任意值转成去空白字符串，None 与空白统一成空串。"""
    if value is None:
        return ""
    return str(value).strip()


def safe_float(value):
    """能转 float 就转，否则返回 None（空串也返回 None）。"""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def lru_get(cache, key):
    """按 LRU 语义取值：命中则把该键移到队尾再返回；未命中返回 None。

    列表类型做浅拷贝返回，避免调用方原地修改（sort/append 等）污染缓存。
    """
    if key not in cache:
        return None
    value = cache.pop(key)
    cache[key] = value
    return list(value) if isinstance(value, list) else value


def lru_set(cache, key, value, max_size=128):
    """写入缓存并淘汰最久未使用的条目（调用方需传入 OrderedDict）。"""
    cache[key] = value
    while len(cache) > max_size:
        cache.popitem(last=False)
