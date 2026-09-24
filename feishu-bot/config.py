"""配置：环境变量 + .env，启动即校验（fail-fast）。

对应 feishu-bot/docs/开发文档.md 第八节。原则：
- 必填项缺失时打印**缺失清单**并退出，而不是启动后才在第一条消息上报错；
- 所有可调参数都在这一个文件里定义默认值，其他模块只读 Config，不直接读环境变量
  （便于测试构造 Config 而不污染 os.environ）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 本文件所在目录 = feishu-bot/（删除本目录即完全下线）
BOT_ROOT = Path(__file__).resolve().parent

DEFAULT_DB_PATH = BOT_ROOT / "data" / "bot.db"
# 开发文档 5.2：RAG 的 REQUEST_MAX_BYTES=65536 在参数校验**之前**生效，
# 所以机器人侧真正的硬边界是"整份 history 序列化后的字节数"，取 40 KB 留足余量。
DEFAULT_HISTORY_MAX_BYTES = 40 * 1024
# 与 RAG 契约一致的次级保护（HISTORY_MAX_ITEMS / HISTORY_CONTENT_MAX_CHARS）
DEFAULT_HISTORY_MAX_ITEMS = 40
DEFAULT_HISTORY_CONTENT_MAX_CHARS = 4000


class ConfigError(RuntimeError):
    """配置不合法（必填缺失/取值越界）。启动时直接抛出，不做"带病运行"。"""


def _load_dotenv() -> None:
    """加载 feishu-bot/.env（若安装了 python-dotenv）。

    override=False：真实环境变量优先于 .env，便于临时用环境变量覆盖而不改文件。
    python-dotenv 是可选项（开发文档第二节），未安装时静默跳过——
    此时配置全部来自真实环境变量，行为仍然正确。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - 取决于部署环境是否装了可选项
        return
    load_dotenv(BOT_ROOT / ".env", override=False)


def _env(name: str, default: str = "", source: dict | None = None) -> str:
    raw = (source if source is not None else os.environ).get(name)
    return default if raw is None else str(raw).strip()


def _float_env(name: str, default: float, problems: list[str],
               source: dict | None = None) -> float:
    raw = _env(name, "", source)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        problems.append(f"{name} 必须是数字，当前 {raw!r}")
        return default


def _int_env(name: str, default: int, problems: list[str],
             source: dict | None = None) -> int:
    raw = _env(name, "", source)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        problems.append(f"{name} 必须是整数，当前 {raw!r}")
        return default


def _bool_env(name: str, default: bool, source: dict | None = None) -> bool:
    raw = _env(name, "", source)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """机器人服务的全部配置。"""

    # ---- 必填（飞书开放平台企业自建应用）----
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # ---- RAG 调用 ----
    rag_base_url: str = "http://127.0.0.1:8000"
    # 与 RAG 侧 RAG_BOT_API_KEY 同值；留空 = 不加 X-Bot-Key 头（内网默认）
    rag_bot_api_key: str = ""
    # 机器人等待 RAG 的读超时。必须小于 RAG 侧 QUERY_JSON_TIMEOUT_SECONDS
    # （默认 30，即下面的 rag_json_budget），留出错误可见余量：本端先超时能拿到
    # 明确的 timeout，而不是等服务端先截断。
    #
    # 这两个值要**一起调**：介绍类长回答（"介绍一下某某之战"）实测 16–25s
    # （truncated=True 的那类），25s 预算在慢一点的时候会被顶穿，表现为降级卡片。
    # 想等更久就把两边同时放大，例如 RAG 侧 QUERY_JSON_TIMEOUT_SECONDS=45
    # 配本侧 RAG_QUERY_TIMEOUT=40 + RAG_JSON_BUDGET=45。
    rag_query_timeout: float = 25.0
    rag_connect_timeout: float = 5.0
    # 机器人**认为的** RAG 侧非流式预算（对应 RAG 的 QUERY_JSON_TIMEOUT_SECONDS）。
    # 本端读不到 RAG 的配置，只能由运维保证两边一致；它的唯一用途是把
    # "机器人超时必须小于服务端预算"这条口径写成可校验的检查，而不是写死 30。
    rag_json_budget: float = 30.0

    # ---- 纠错反馈投递（P2）----
    feishu_operators_chat_id: str = ""

    # ---- 状态存储 ----
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    # 历史组装的时间窗（软口径）；物理清理见 scripts/cleanup_db.py（硬口径）
    session_ttl_hours: float = 24.0

    # ---- 历史组装边界（开发文档 5.2）----
    history_max_bytes: int = DEFAULT_HISTORY_MAX_BYTES
    history_max_items: int = DEFAULT_HISTORY_MAX_ITEMS
    history_content_max_chars: int = DEFAULT_HISTORY_CONTENT_MAX_CHARS
    # 回答进入历史时的额外上限（0 = 不额外截断）。RAG 的指代消解只读历史里的提问，
    # 所以截回答不影响追问；而不截的话长回答会滚进后续每一轮请求，直接拖慢生成。
    history_assistant_max_chars: int = 800

    # ---- 卡片与交互（P1/P2）----
    # 示例问题按钮（P1-3）：从 RAG /api/demo/examples 取题，失败则按钮区不渲染
    demo_examples_enabled: bool = True
    demo_examples_count: int = 3
    demo_examples_refresh_seconds: float = 3600.0
    # 取题失败后的重试间隔（负缓存窗口）。必须为正：没有窗口就意味着每张卡片
    # 都同步重打一次 HTTP（接口挂掉时最坏吃满客户端超时，叠加在用户等待时间上）。
    demo_examples_failure_retry_seconds: float = 300.0
    # 子图服务端出图（P2-1）：需要部署机装好 Node ≥ 18 与 render/node_modules
    subgraph_render_enabled: bool = True
    subgraph_render_timeout: float = 10.0

    # ---- 运维 ----
    log_level: str = "INFO"
    # 去重记录保留时长（开发文档第四节：飞书重试通常集中在 1 分钟内）
    processed_events_ttl_hours: float = 24.0
    # 卡片按钮回调缺少 event_id 时的退化去重窗口（秒）。
    # 只用于压制"同一次点击的重投"，必须远小于人工二次点击的间隔——
    # 连点两次同一按钮必须两条都处理（P0-4 验收），所以窗口取个位数秒。
    card_dedupe_window_seconds: float = 2.0

    @property
    def database_path(self) -> Path:
        return Path(self.db_path)

    def validate(self) -> None:
        missing = [name for name, value in (
            ("FEISHU_APP_ID", self.feishu_app_id),
            ("FEISHU_APP_SECRET", self.feishu_app_secret),
        ) if not value]
        if missing:
            raise ConfigError(
                "缺少必填配置：" + "、".join(missing)
                + "。请在 feishu-bot/.env 中填写（模板见 .env.example），"
                  "或在环境变量里导出。飞书开放平台 → 企业自建应用 → 凭证与基础信息。"
            )

        problems: list[str] = []
        if self.rag_query_timeout <= 0:
            problems.append(f"RAG_QUERY_TIMEOUT 必须为正数，当前 {self.rag_query_timeout!r}")
        if self.rag_connect_timeout <= 0:
            problems.append(f"RAG_CONNECT_TIMEOUT 必须为正数，当前 {self.rag_connect_timeout!r}")
        # 层层截断口径（开发文档 5.4）：机器人超时必须**小于**非流式接口的预算，
        # 否则两端同时到点，机器人只能看到连接被掐断，拿不到 RAG 给出的 timeout 说明。
        if self.rag_query_timeout >= self.rag_json_budget:
            problems.append(
                f"RAG_QUERY_TIMEOUT={self.rag_query_timeout} 必须小于 RAG 侧非流式预算 "
                f"RAG_JSON_BUDGET={self.rag_json_budget}（应等于 RAG 的 "
                f"QUERY_JSON_TIMEOUT_SECONDS，默认 30）：机器人先超时才能拿到明确的超时原因。"
                f"想等更久请两边一起调大，例如 RAG_JSON_BUDGET=45 + RAG_QUERY_TIMEOUT=40"
            )
        if self.rag_json_budget <= 0:
            problems.append(f"RAG_JSON_BUDGET 必须为正数，当前 {self.rag_json_budget!r}")
        if self.session_ttl_hours <= 0:
            problems.append(f"SESSION_TTL_HOURS 必须为正数，当前 {self.session_ttl_hours!r}")
        if self.history_max_bytes <= 0:
            problems.append(f"HISTORY_MAX_BYTES 必须为正数，当前 {self.history_max_bytes!r}")
        if self.history_max_items <= 0:
            problems.append(f"HISTORY_MAX_ITEMS 必须为正数，当前 {self.history_max_items!r}")
        if self.history_assistant_max_chars < 0:
            problems.append(
                f"HISTORY_ASSISTANT_MAX_CHARS 不能为负（0 表示不额外截断），"
                f"当前 {self.history_assistant_max_chars!r}")
        if self.subgraph_render_timeout <= 0:
            problems.append(
                f"SUBGRAPH_RENDER_TIMEOUT 必须为正数，当前 {self.subgraph_render_timeout!r}"
            )
        if self.card_dedupe_window_seconds < 0:
            problems.append(
                f"CARD_DEDUPE_WINDOW_SECONDS 不能为负，"
                f"当前 {self.card_dedupe_window_seconds!r}"
            )
        if self.demo_examples_failure_retry_seconds <= 0:
            problems.append(
                f"DEMO_EXAMPLES_FAILURE_RETRY_SECONDS 必须为正数（失败后的重试窗口），"
                f"当前 {self.demo_examples_failure_retry_seconds!r}"
            )
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            problems.append(f"BOT_LOG_LEVEL 取值非法：{self.log_level!r}")
        if problems:
            raise ConfigError("配置校验失败：\n- " + "\n- ".join(problems))


def load_config(env: dict | None = None, *, skip_dotenv: bool = False) -> Config:
    """从环境变量（+ .env）构造并校验配置。

    `env` 参数供测试直接喂一份字典（不读 .env、不改动 os.environ）；
    生产路径传 None，走 os.environ + .env。
    """
    source: dict | None = env
    if env is None and not skip_dotenv:
        _load_dotenv()

    problems: list[str] = []
    cfg = Config(
        feishu_app_id=_env("FEISHU_APP_ID", source=source),
        feishu_app_secret=_env("FEISHU_APP_SECRET", source=source),
        rag_base_url=(_env("RAG_BASE_URL", "http://127.0.0.1:8000", source) or "").rstrip("/"),
        rag_bot_api_key=_env("RAG_BOT_API_KEY", source=source),
        rag_query_timeout=_float_env("RAG_QUERY_TIMEOUT", 25.0, problems, source),
        rag_connect_timeout=_float_env("RAG_CONNECT_TIMEOUT", 5.0, problems, source),
        rag_json_budget=_float_env("RAG_JSON_BUDGET", 30.0, problems, source),
        feishu_operators_chat_id=_env("FEISHU_OPERATORS_CHAT_ID", source=source),
        db_path=Path(_env("BOT_DB_PATH", source=source) or DEFAULT_DB_PATH).expanduser(),
        session_ttl_hours=_float_env("SESSION_TTL_HOURS", 24.0, problems, source),
        history_max_bytes=_int_env("HISTORY_MAX_BYTES", DEFAULT_HISTORY_MAX_BYTES,
                                   problems, source),
        history_max_items=_int_env("HISTORY_MAX_ITEMS", DEFAULT_HISTORY_MAX_ITEMS,
                                   problems, source),
        history_content_max_chars=_int_env(
            "HISTORY_CONTENT_MAX_CHARS", DEFAULT_HISTORY_CONTENT_MAX_CHARS, problems, source),
        history_assistant_max_chars=_int_env("HISTORY_ASSISTANT_MAX_CHARS", 800,
                                             problems, source),
        demo_examples_enabled=_bool_env("DEMO_EXAMPLES_ENABLED", True, source),
        demo_examples_count=_int_env("DEMO_EXAMPLES_COUNT", 3, problems, source),
        demo_examples_refresh_seconds=_float_env(
            "DEMO_EXAMPLES_REFRESH_SECONDS", 3600.0, problems, source),
        demo_examples_failure_retry_seconds=_float_env(
            "DEMO_EXAMPLES_FAILURE_RETRY_SECONDS", 300.0, problems, source),
        subgraph_render_enabled=_bool_env("SUBGRAPH_RENDER_ENABLED", True, source),
        subgraph_render_timeout=_float_env("SUBGRAPH_RENDER_TIMEOUT", 10.0, problems, source),
        log_level=_env("BOT_LOG_LEVEL", "INFO", source).upper() or "INFO",
        processed_events_ttl_hours=_float_env("PROCESSED_EVENTS_TTL_HOURS", 24.0,
                                              problems, source),
        card_dedupe_window_seconds=_float_env("CARD_DEDUPE_WINDOW_SECONDS", 2.0,
                                              problems, source),
    )

    if problems:
        raise ConfigError("配置校验失败：\n- " + "\n- ".join(problems))
    cfg.validate()
    return cfg
