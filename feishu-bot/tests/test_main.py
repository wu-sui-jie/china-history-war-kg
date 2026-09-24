"""依赖装配的守护用例（main.build_application）。

不启动 ws、不联网：只验证"接线"正确——技能注册顺序、依赖是否齐全、
启动自检对 RAG 不可用是否只告警不阻塞（开发文档 5.4）。
装配错了在最外层才会暴露，代价是"机器人起来了但一问就崩"。
"""

from __future__ import annotations

import logging

import pytest

from bot.skills.base import SkillContext
from main import build_application, setup_logging, startup_checks


def test_build_application_wires_everything(config):
    app = build_application(config)
    try:
        assert app.session.db is app.db
        names = [skill.name for skill in app.dispatcher.skills]
        # 注册顺序即分流顺序：命令技能（/help、/new）→ knowledge_qa（兜底）→ report_error（仅按钮）
        assert names == ["help", "new_session", "knowledge_qa", "report_error"]
        knowledge_qa = app.dispatcher.skills[2]
        assert knowledge_qa.match(
            SkillContext(event=None, question="任意问题", session_key="k")) is True
        # 命令技能按"第一个词"命中，且都不走两段式（只有 knowledge_qa 声明占位卡）
        assert app.dispatcher.skills[0].match(
            SkillContext(event=None, question="/help", session_key="k")) is True
        assert app.dispatcher.skills[1].match(
            SkillContext(event=None, question="/new", session_key="k")) is True
        assert [getattr(s, "wants_placeholder", False) for s in app.dispatcher.skills] == [
            False, False, True, False]
        # 数据库已建表
        tables = {row[0] for row in app.db.query_all(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sessions", "messages", "processed_events", "feedback"} <= tables
    finally:
        app.close()


def test_renderer_none_when_disabled(config):
    config.subgraph_render_enabled = False
    app = build_application(config)
    try:
        assert app.renderer is None
        assert app.dispatcher.skills[2].renderer is None      # knowledge_qa 持有渲染器
    finally:
        app.close()


def test_startup_checks_tolerate_unreachable_rag(config, caplog):
    """RAG 不可用只打警告、不阻塞启动（机器人仍能回降级卡片）。"""
    config.rag_base_url = "http://127.0.0.1:1"
    config.rag_connect_timeout = 0.3
    app = build_application(config)
    try:
        with caplog.at_level(logging.WARNING):
            startup_checks(app)
        assert any("RAG 健康检查未通过" in r.message for r in caplog.records)
        assert any("FEISHU_OPERATORS_CHAT_ID" in r.message for r in caplog.records)
    finally:
        app.close()


def test_startup_checks_warn_when_llm_missing(config, monkeypatch, caplog):
    app = build_application(config)
    try:
        monkeypatch.setattr(app.rag, "health",
                            lambda: {"status": "ok", "version": "v", "llm_available": False})
        monkeypatch.setattr(app.rag, "demo_examples", lambda: [])
        monkeypatch.setattr(app.feishu, "get_bot_open_id", lambda: None)
        with caplog.at_level(logging.WARNING):
            startup_checks(app)
        # 上线阻塞项：没有 LLM 密钥时回答质量受限，必须显著告警
        assert any("LLM 未配置" in r.message for r in caplog.records)
    finally:
        app.close()


def test_setup_logging_is_idempotent_and_quiets_noisy_libraries():
    setup_logging("INFO")
    setup_logging("WARNING")
    assert logging.getLogger("lark").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING


def test_friendly_error_when_lark_sdk_missing(monkeypatch):
    """缺 SDK（或用了别的解释器）时给可执行的修复提示，而不是裸 ModuleNotFoundError。

    真实踩坑：Windows 的 `py main.py` 不走 conda 环境，依赖明明装了却报 No module named
    'lark_oapi'；`pip install` 之外还必须点明"解释器可能不是同一个"。
    """
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "lark_oapi" or name.startswith("lark_oapi."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    sys.modules.pop("bot.feishu_client", None)
    try:
        with pytest.raises(ImportError) as err:
            importlib.import_module("bot.feishu_client")
    finally:
        sys.modules.pop("bot.feishu_client", None)
        monkeypatch.undo()
        importlib.import_module("bot.feishu_client")     # 恢复，别影响后续用例

    message = str(err.value)
    assert "pip install -r" in message          # 怎么装
    assert "python main.py" in message          # 怎么排除解释器不一致
    assert "py main.py" in message


# ---- 启动装配（真构造 SDK 客户端，但不联网）----


def test_feishu_client_builds_ws_client(config):
    """长连接客户端的组装路径必须真跑一次。

    这条用例是补出来的：实现期 FeishuClient 忘了保存 app_secret，而 build_ws_client
    要用它，直到**真机第一次启动**才炸出 AttributeError —— 所有单测都用鸭子类型替身，
    覆盖不到"构造真实 SDK 客户端"这条路，只有把组装本身纳入测试才能拦住这类错误。
    """
    from lark_oapi.ws.client import Client as WsClient

    from bot.feishu_client import FeishuClient

    client = FeishuClient(app_id="cli_test", app_secret="secret_test", log_level="WARNING")
    ws = client.build_ws_client(on_message=lambda data: None,
                                on_card_action=lambda data: None)
    assert isinstance(ws, WsClient)
    assert client.app_secret == "secret_test"
    # 只组装不启动：start() 会真的去连飞书


def test_run_wires_everything_and_shuts_down(config, monkeypatch):
    """run() 的接线：worker 起得来、ws 客户端组得出来、退出时收尾（不真连飞书）。"""
    import main as main_mod

    app = build_application(config)
    monkeypatch.setattr(main_mod.signal, "signal", lambda *a, **k: None)  # 别抢测试进程的信号
    monkeypatch.setattr(app.feishu, "run_ws_forever", lambda: None)       # 不建立真连接

    main_mod.run(app)      # 能正常返回即说明整条接线成立

    assert app.feishu._ws is not None, "run() 里必须组装 ws 客户端"
    assert app.db._conn is None, "退出时应关闭数据库连接"


def test_run_exits_after_stop_signal_even_if_ws_blocks(config, monkeypatch):
    """长连接永久阻塞时，收到停止信号后 run() 必须仍然退出。

    这条用例是补出来的：SDK 的 `start()` 会在自己的 asyncio 循环里永久阻塞
    （`while True: await sleep(3600)`）且没有公开 stop()，早期实现把主线程停在里面，
    表现是"按 Ctrl-C、日志说正在停止、进程退不出来"（真机实测）。
    """
    import signal as signal_mod
    import threading

    import main as main_mod

    handlers: dict = {}
    monkeypatch.setattr(main_mod.signal, "signal",
                        lambda sig, fn: handlers.setdefault(sig, fn))

    app = build_application(config)
    blocked = threading.Event()
    # 模拟 SDK：永不返回，直到进程结束
    monkeypatch.setattr(app.feishu, "run_ws_forever", lambda: blocked.wait())

    done = threading.Event()
    threading.Thread(target=lambda: (main_mod.run(app), done.set()), daemon=True).start()

    # 等 worker 起来后投递"停止"信号（真机上是 Ctrl-C）
    for _ in range(100):
        if signal_mod.SIGINT in handlers:
            break
        blocked.wait(0.02)
    assert signal_mod.SIGINT in handlers, "run() 必须注册停止信号处理器"
    handlers[signal_mod.SIGINT](signal_mod.SIGINT, None)

    assert done.wait(10.0), "收到停止信号后 run() 必须在 10s 内退出（否则就是卡在 SDK 循环里）"
    assert app.db._conn is None, "退出时应关闭数据库连接"
