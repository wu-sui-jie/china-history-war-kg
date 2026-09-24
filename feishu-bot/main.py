"""入口：组装依赖、自检、启动 ws 客户端与 worker（开发文档第十一节）。

启动自检顺序（任一"可降级项"失败只打警告，不阻塞启动）：
    config 校验（fail-fast）→ SQLite 建表 → RAG health 探测 →
    子图渲染可用性探测 → 示例题预取 → 机器人 open_id → ws 客户端启动

为什么 health 失败不阻塞启动：机器人仍能收消息并回复"知识服务暂不可用"降级卡片，
直接退出反而让用户以为机器人坏了（开发文档 5.4）。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

# 允许 `python feishu-bot/main.py` 与 `python -m main` 两种启动方式
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bot.db import Database                                   # noqa: E402
from bot.dispatcher import Dispatcher                         # noqa: E402
from bot.feishu_client import FeishuClient                    # noqa: E402
from bot.rag_client import DemoExamplesCache, RagClient       # noqa: E402
from bot.render.subgraph import SubgraphRenderer               # noqa: E402
from bot.session import SessionStore                          # noqa: E402
from bot.skills.help import HelpSkill                          # noqa: E402
from bot.skills.knowledge_qa import KnowledgeQaSkill           # noqa: E402
from bot.skills.new_session import NewSessionSkill             # noqa: E402
from bot.skills.report_error import ReportErrorSkill          # noqa: E402
from config import Config, ConfigError, load_config           # noqa: E402

log = logging.getLogger("bot.main")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


@dataclass
class Application:
    """组装好的依赖容器（测试可直接用假依赖拼一个）。"""

    config: Config
    db: Database
    session: SessionStore
    rag: RagClient
    feishu: FeishuClient
    renderer: SubgraphRenderer | None
    examples: DemoExamplesCache
    dispatcher: Dispatcher

    def close(self) -> None:
        try:
            # 停机等待放宽到 RAG 预算：worker 可能正阻塞在途查询（最长
            # RAG_QUERY_TIMEOUT + 连接超时），默认 5s 等不到它，而紧接着的
            # rag/db close 会让在途任务抛异常（进程即将退出、无实害，但日志有
            # 吓人堆栈）。等太久时连按两次 Ctrl-C 走 os._exit(0) 逃生。
            self.dispatcher.stop(
                timeout=self.config.rag_query_timeout + self.config.rag_connect_timeout + 5.0)
        finally:
            self.rag.close()
            self.db.close()
            self.feishu.close()


def setup_logging(level: str = "INFO") -> None:
    """stdout 结构化文本日志（不落文件，交给进程守护重定向）。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # SDK 自带的 logger 很啰嗦（每帧都要打日志），非 DEBUG 档降一档
    logging.getLogger("lark").setLevel(logging.WARNING)
    # httpx 每个请求一行 INFO，降噪
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_application(config: Config) -> Application:
    """组装全部依赖（不启动 ws；便于测试与单进程自检）。"""
    db = Database(config.database_path)
    db.connect()

    session = SessionStore(
        db, ttl_hours=config.session_ttl_hours,
        history_max_bytes=config.history_max_bytes,
        history_max_items=config.history_max_items,
        history_content_max_chars=config.history_content_max_chars,
        history_assistant_max_chars=config.history_assistant_max_chars,
    )
    rag = RagClient(config.rag_base_url, bot_api_key=config.rag_bot_api_key,
                    query_timeout=config.rag_query_timeout,
                    connect_timeout=config.rag_connect_timeout)
    feishu = FeishuClient(app_id=config.feishu_app_id, app_secret=config.feishu_app_secret,
                          log_level=config.log_level)

    renderer = SubgraphRenderer(
        feishu, enabled=config.subgraph_render_enabled,
        timeout=config.subgraph_render_timeout,
    ) if config.subgraph_render_enabled else None

    examples = DemoExamplesCache(
        rag, count=config.demo_examples_count,
        refresh_seconds=config.demo_examples_refresh_seconds,
        failure_retry_seconds=config.demo_examples_failure_retry_seconds,
        enabled=config.demo_examples_enabled,
    )

    # 注册顺序即分流顺序：命令技能（/help、/new）→ knowledge_qa（兜底，恒真）
    # report_error 不在文本分流里（仅由卡片按钮触发，开发文档 5.3）
    skills = [
        HelpSkill(),
        NewSessionSkill(session=session),
        KnowledgeQaSkill(rag=rag, session=session, renderer=renderer, examples=examples,
                         config=config),
        ReportErrorSkill(session=session, feishu=feishu, config=config),
    ]
    dispatcher = Dispatcher(db=db, session=session, skills=skills, feishu=feishu,
                            config=config)
    return Application(config=config, db=db, session=session, rag=rag, feishu=feishu,
                       renderer=renderer, examples=examples, dispatcher=dispatcher)


def startup_checks(app: Application) -> None:
    """启动自检：全部失败都只告警，不阻塞启动。"""
    health = app.rag.health()
    if health.get("status") != "ok":
        log.warning("RAG 健康检查未通过（机器人仍可收消息，回复将走降级卡片）：%s",
                    health.get("message") or health.get("load_error") or health)
    else:
        log.info("RAG 就绪：版本=%s 索引=%s 向量=%s LLM=%s",
                 health.get("version"), health.get("index_version"),
                 health.get("vector_available"), health.get("llm_available"))
        if not health.get("llm_available"):
            # 需求文档第六节的"上线阻塞项"：没有 LLM 密钥回答质量受限
            log.warning("RAG 侧 LLM 未配置：回答会走离线摘要模式，质量受限"
                        "（上线前必须配好 LLM 密钥）")
        # 版本不匹配比"服务挂了"更隐蔽：health 正常但问答接口不存在，
        # 用户每次提问都收到"服务暂不可用"而降级卡片
        if not app.rag.supports_json_query():
            log.error("RAG 未提供 /api/query/json —— 该地址上的服务是改动前的旧版本，"
                      "所有提问都会降级。请用当前代码重启 RAG 服务"
                      "（python RAG/scripts/run_server.py --version <版本>）")

    if app.renderer is not None:
        reason = app.renderer.unavailable_reason()
        if reason:
            log.warning("子图出图不可用（将始终走文字降级）：%s", reason)
        else:
            log.info("子图出图可用：Node SSR + resvg 就绪")

    questions = app.examples.refresh(force=True)
    if questions:
        log.info("示例问题按钮已就绪：%d 条", len(questions))
    elif app.config.demo_examples_enabled:
        log.warning("示例题未取到：卡片按钮区将不渲染（不影响问答）")

    if not app.config.feishu_operators_chat_id:
        log.warning("未配置 FEISHU_OPERATORS_CHAT_ID：纠错反馈只落 SQLite，不投递运营群")

    bot_open_id = app.feishu.get_bot_open_id()
    if bot_open_id:
        app.dispatcher.command_bot_open_id = bot_open_id
    else:
        log.warning("未取到机器人 open_id：群聊 @ 判断将退化为按 mention 文本判断")


def run(app: Application) -> None:
    """启动 worker 与 ws 长连接，主线程等待停机信号。

    **长连接跑在后台线程**，不是主线程——这是有意的：
    SDK 的 `ws.Client.start()` 会在自己的 asyncio 循环里永久阻塞
    （`run_until_complete(_select())`，而 `_select()` 是 `while True: await sleep(3600)`），
    且**没有公开的 stop()/close()**，`auto_reconnect=True` 时断线还会无限重连。
    若把主线程停在里面，就会出现"按了 Ctrl-C、日志说正在停止、进程却退不出来"
    （实测：信号处理器执行了、worker 也退了，但主线程卡在 SDK 循环里）。
    放后台线程后，主线程只等一个 Event；退出时该守护线程随进程结束。
    """
    app.dispatcher.start()
    # 事件处理器在此注册到 ws 客户端（内部持有）；run_ws_forever 再启动它
    app.feishu.build_ws_client(on_message=app.dispatcher.on_message,
                              on_card_action=app.dispatcher.on_card_action)

    stopping = threading.Event()

    def _shutdown(signum, _frame):
        if stopping.is_set():
            # 第二次 Ctrl-C：给一个逃生口（正常路径只需几百毫秒，走到这里说明卡住了）
            log.warning("再次收到信号 %s，强制退出", signum)
            os._exit(0)
        log.info("收到信号 %s，正在停止…", signum)
        stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):   # 非主线程 / 平台不支持
            pass

    ws_thread = threading.Thread(target=app.feishu.run_ws_forever, name="lark-ws",
                                daemon=True)
    ws_thread.start()
    log.info("机器人服务已启动：等待飞书事件（长连接模式，无需公网回调地址）"
             "；Ctrl-C 停止（连按两次强制退出）")

    try:
        while not stopping.wait(timeout=1.0):
            if not ws_thread.is_alive():
                # start() 返回或抛异常都意味着连接已不在：继续守着没有意义
                log.warning("长连接线程已退出，服务将停止（查看上面的 lark 日志确认原因）")
                break
    except KeyboardInterrupt:           # pragma: no cover - 信号未走 handler 的兜底
        log.info("收到 Ctrl-C，正在停止…")
    finally:
        app.close()
        log.info("机器人服务已停止（长连接线程随进程结束）")


def main() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"[feishu-bot] 启动失败：{e}", file=sys.stderr)
        return 2
    setup_logging(config.log_level)
    log.info("配置已加载：RAG=%s 数据库=%s 会话TTL=%.1fh 子图出图=%s",
             config.rag_base_url, config.database_path, config.session_ttl_hours,
             config.subgraph_render_enabled)
    app = build_application(config)
    startup_checks(app)
    run(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
