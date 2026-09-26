"""跨模块复用的小工具。

这里只放「无业务语义」的纯工具函数：app.py / import_json_to_sqlite.py /
entity_extract/extractor.py / inference/rule_llm_integration.py 都可能用到，
各自复制一份会让修一处漏三处，因此收敛到本模块。
"""

import re
import threading

# Cypher 不支持参数化标签 / 关系类型（`MATCH (n:$label)` 不是合法语法），
# 这类标识符只能校验后内联；其余一切用户输入必须走查询参数。
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff]{1,64}$")

# LRU 读写锁：OrderedDict 的 pop/赋值与淘汰循环都不是原子的，
# 而缓存由 Flask 的多个请求线程与问答模块的线程池共享。
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


def brief_error(error, limit=200):
    """把异常整理成"可以回给客户端"的一句话。

    原样回显异常字符串会把内部细节（多行栈信息、绝对路径、连接串）送出去；
    但完全不给原因，又让"模型没起"这类本来可处理的故障变得无从判断。
    折中：只取第一行并截断，完整内容仍由调用方写日志。
    """
    text = "" if error is None else str(error)
    stripped = text.strip()
    first_line = stripped.splitlines()[0].strip() if stripped else ""
    if len(first_line) > limit:
        first_line = first_line[:limit] + "…"
    return first_line


# ================== 编码守卫 ==================
# 数据里出现过 "鎴樹簤浜嬩欢"（= 战争事件）这类值：UTF-8 字节被按 GBK 读出来。
# 来源是没写 encoding 的 open()/read_text()——在 Windows 上默认走 cp936。
# 在前端 typeAliasMap 里加乱码 key 兼容等于把问题藏在展示层：
# 数据是坏的、只是看起来对，任何新入口（导入、同步、接口）都会再犯。
#
# 修在源头：导入与同步写库前统一过一遍 repair_mojibake，前端不再维护乱码映射。
# 判断依据是"能否 GBK 编码后再按 UTF-8 解码"——真实的中文文本在这步通常直接失败
# （GBK 里没有对应的字节序列），所以不会误改正常数据。


def repair_mojibake(value, source=""):
    """把「UTF-8 字节被当 GBK 读」造成的乱码还原；不适用时原样返回。

    只有往返成功且结果确实不同才替换，因此对正常文本是恒等操作。
    source 只用于日志（例如 "places.geo_name"），便于定位是哪张表/哪个字段。
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        repaired = value.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        return value
    if repaired == value or "\ufffd" in repaired:
        return value
    logger = _get_logger()
    if logger is not None:
        logger.warning("检测到编码乱码并已还原%s：%r -> %r",
                       f"（{source}）" if source else "", value, repaired)
    return repaired


def repair_mojibake_props(props):
    """对属性字典里的字符串值逐个复原（不改非字符串值，返回新字典）。"""
    return {key: repair_mojibake(value, source=str(key)) if isinstance(value, str) else value
            for key, value in (props or {}).items()}


def _get_logger():
    """延迟取 logger：common_utils 被大量模块导入，避免在这里引入日志模块的初始化顺序问题。"""
    try:
        from logging_util import get_logger
        return get_logger(__name__)
    except Exception:  # noqa: BLE001
        return None


def lru_get(cache, key):
    """按 LRU 语义取值：命中则把该键移到队尾再返回；未命中返回 None。

    列表类型做浅拷贝返回，避免调用方原地修改（sort/append 等）污染缓存。
    读写都在锁内：pop + 重新插入期间另一个线程可能正在淘汰条目。
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
