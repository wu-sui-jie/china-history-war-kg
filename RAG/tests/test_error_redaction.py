"""对外错误文案的脱敏。

背景：`server/api.py` 里早就有 `_public_text()` 把服务器绝对路径换成占位符，但它
只被"文件不存在"那几条分支用到，而**同一个文件**里另外三处（`读取词典失败` /
`示例题清单解析失败` / `读取示例题失败`）以及 SSE 的内部错误帧都把 `str(e)` 原样
回给客户端——工具在，只是没统一用上。三处触发条件都很普通（制品损坏、JSON 解析失败）。

这里钉两件事：脱敏函数本身的行为，以及两类对外文案确实走过了它。
"""

from __future__ import annotations

from lib.redact import public_text


def test_仓库内绝对路径被收敛为占位符():
    text = public_text(f"读取失败: {__file__}")

    assert "redact" not in text.split("读取失败: ")[1] or "RAG_ROOT" in text
    assert "<RAG_ROOT>" in text or "<path>" in text


def test_unix_与_windows_绝对路径都被收敛():
    assert "<path>" in public_text("打不开 /srv/rag/data/snapshot/x.json")
    assert "<path>" in public_text(r"打不开 C:\Users\someone\rag\data\x.json")


def test_普通文案不被改动():
    assert public_text("请求体必须是一个 JSON 对象") == "请求体必须是一个 JSON 对象"


def test_空值原样返回():
    assert public_text(None) is None
    assert public_text("") == ""


def test_异常里带路径时会被收敛成占位符(tmp_path):
    """这条才是脱敏真正起作用的地方：异常文本里**带**服务器路径。

    用「dicts.json 其实是个目录」制造真实的 `IsADirectoryError`（文本里带绝对路径），
    而 /api/dicts 是匿名接口，不该把部署结构回出去。
    """
    from types import SimpleNamespace

    from server.api import _load_dicts_payload

    snapshot_dir = tmp_path / "snapshot" / "20260915_v1"
    (snapshot_dir / "dicts.json").mkdir(parents=True)   # 是目录 → read_text 抛 OSError
    rt = SimpleNamespace(snapshot_dir=snapshot_dir, version="20260915_v1")

    payload = _load_dicts_payload(rt)

    assert payload["status"] == "error"
    message = payload["message"]
    assert str(tmp_path) not in message, f"回传了服务器绝对路径：{message}"
    assert "<path>" in message or "<RAG_ROOT>" in message


def test_异常里没有路径时文案保持可读(tmp_path):
    """脱敏只动路径，不该把「制品损坏」这类可判断的信息一起抹掉。"""
    from types import SimpleNamespace

    from server.api import _load_dicts_payload

    snapshot_dir = tmp_path / "snapshot" / "20260915_v1"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "dicts.json").write_text("{ 这不是 JSON", encoding="utf-8")
    rt = SimpleNamespace(snapshot_dir=snapshot_dir, version="20260915_v1")

    payload = _load_dicts_payload(rt)

    assert payload["status"] == "error"
    assert "读取词典失败" in payload["message"]
    assert str(tmp_path) not in payload["message"]


def test_词典缺失时的文案同样脱敏(tmp_path):
    """同一个函数的另一条分支（文件不存在）也要脱敏，这里把它一起钉住。"""
    from types import SimpleNamespace

    from server.api import _load_dicts_payload

    rt = SimpleNamespace(snapshot_dir=tmp_path / "nope", version="v")

    payload = _load_dicts_payload(rt)

    assert payload["status"] == "error"
    assert str(tmp_path) not in payload["message"]


def test_sse_内部错误帧的文案经过脱敏():
    """SSE 的内部错误帧也会被前端显示出来，同样不该带服务器路径。"""
    import inspect

    import server.sse as sse_mod

    source = inspect.getsource(sse_mod)
    # 这一条防的是"以后有人把脱敏去掉"：帧文案必须经过 public_text
    assert 'f"内部错误: {public_text(e)}"' in source
    assert 'f"内部错误: {e}"' not in source
