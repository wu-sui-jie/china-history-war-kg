"""跨模块复用的小工具。

这里只放「无业务语义」的纯工具函数：原先在 app.py / import_json_to_sqlite.py /
entity_extract/extractor.py / inference/rule_llm_integration.py 里各写一份，
现收敛到本模块。
"""

import re
import threading

# Cypher 不支持参数化标签 / 关系类型（`MATCH (n:$label)` 不是合法语法），
# 这类标识符只能校验后内联；其余一切用户输入必须走查询参数。
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff]{1,64}$")

# LRU 读写锁：OrderedDict 的 pop/赋值与淘汰循环都不是原子的，
# 而缓存由 Flask 的多个请求线程与问答模块的线程池共享（BE-6）。
# 用 RLock：lru_get/lru_set 内部不重入，但调用方可能在已持有锁的路径上继续调用。
_LRU_LOCK = threading.RLock()


def safe_identifier(value, kind="标签"):
    """校验要内联进 Cypher 的标签 / 关系类型等标识符。

    只允许字母、数字、下划线与汉字，长度不超过 64；不合法直接拒绝，
    避免把用户输入当成 Cypher 片段执行。
    """
    text = "" if value is None else str(value).strip()
    if not _SAFE_IDENTIFIER_RE.match(text):
        raise ValueError(f"非法的{kind}: {value!r}")
    return text


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
    读写都在锁内：pop + 重新插入期间另一个线程可能正在淘汰条目（BE-6）。
    """
    with _LRU_LOCK:
        if key not in cache:
            return None
        value = cache.pop(key)
        cache[key] = value
        return list(value) if isinstance(value, list) else value


def lru_set(cache, key, value, max_size=128):
    """写入缓存并淘汰最久未使用的条目（调用方需传入 OrderedDict）。"""
    with _LRU_LOCK:
        cache[key] = value
        while len(cache) > max_size:
            cache.popitem(last=False)
