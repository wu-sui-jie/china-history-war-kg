"""配置加载：读取 .env 并合并环境变量，返回 Settings。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from config import defaults
from lib.versions import is_valid_version

# 从仓库根 / RAG 根加载 .env（若存在）。keys=True 表示不覆盖已有环境变量。
load_dotenv(defaults.RAG_ROOT / ".env", override=False)

# 云端向量模型单请求上限（百炼 text-embedding-v4：最多 10 条文本；配大只会报错）
MAX_EMBEDDING_BATCH = 10

# 合法的鉴权模式（取值含义见 Settings.auth_mode 的注释）。顺序即文档里推荐的顺序。
AUTH_MODES = ("jwt", "nginx", "disabled")

# 凭证撤销查询的失败策略取值（第 13 轮复核）。与 server/introspection.py 的常量同值，
# 这里重复一份是为了让配置校验不依赖 server 包（config 被离线脚本共享）。
FAIL_MODES = ("closed", "open")


def _path_env(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v).expanduser() if v else default


def _bool_env(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float_env(key: str, default, fallback: float) -> float:
    """读一个浮点配置；缺失或非法时回落到 `fallback`。

    `default` 是 defaults.py 里的字符串形态（配置文件与代码里的默认值保持同一处声明），
    非法值不抛异常而回落到安全默认——这类参数（超时/缓存时长）写错时，"用默认值继续跑"
    比"服务起不来"更合适；真正需要报错的是取值范围，那由 revocation_startup_problem 负责。
    """
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        raw = default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def _first_env(*keys: str, default: str = "") -> str:
    """按优先级返回第一个非空环境变量（密钥别名链用）。

    为什么需要别名链：同一把密钥在不同机器上可能落在不同的变量名里（中转服务发的是
    `RAG-command` 这类连字符名，而连字符在 Linux 的 shell 里无法 export）。见开发说明 §四.11。

    别名顺序约定：**下划线名在前、历史连字符名在后**——Linux 上只能用前者，
    Windows 上两者都可能存在，前者优先可保证跨平台行为一致。
    """
    for key in keys:
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return default


@dataclass
class Settings:
    data_dir: Path
    raw_dir: Path
    snapshot_dir: Path
    index_dir: Path
    cache_dir: Path
    log_dir: Path
    # 同源托管（D8）：后端挂载的前端构建产物目录；不存在时跳过挂载
    frontend_dist: Path

    legacy_sqlite_path: Path
    legacy_raw_texts: List[Path]

    governance_enable_relation_extraction: bool
    index_build_embeddings: bool

    chunk_max_chars: int
    chunk_overlap_chars: int

    # ---- 在线链路（RAGv2，F02–F06）----
    # 大模型（OpenAI 兼容；RAGv5 起默认中转 endpoint，地址与模型在 .env 配）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek/deepseek-v4.1-flash"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # 输出上限：推理模型会先消耗 reasoning token，设小会导致正文为空（v5 实测 1024/2048 均触顶）
    llm_max_tokens: int = 3072
    # F02 LLM 兜底（词典完全未命中 → 模型抽实体）：默认关闭，每问会多一次串行调用
    enable_llm_entity_fallback: bool = False
    llm_entity_timeout_seconds: int = 8
    # 备用生成模型
    fallback_llm_base_url: str = ""
    fallback_llm_api_key: str = ""
    fallback_llm_model: str = ""
    # 云端文本向量模型（F11 构建 / F04 向量检索；无密钥则自动降级关键词）
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    # 百炼硬上限：单请求最多 10 条文本（配大不会生效，只会报错）
    embedding_batch_size: int = 10
    embedding_timeout_seconds: int = 60
    # 向量库（D2）：Chroma 持久化集合名
    chroma_collection: str = "chunks_v1"
    # 文本检索模式（T3）：keyword / vector / hybrid —— 部署级全局开关（D3 推荐形态）
    # 兜底值与 defaults 对齐 hybrid/rrf（2026-09-15 审核整改，消除静默回退）
    text_mode: str = "hybrid"
    # hybrid 融合策略与权重（§四.2）：weighted / rrf / fallback
    text_hybrid_strategy: str = "rrf"
    text_hybrid_keyword_weight: float = 0.5
    # 关键词检索用词上限（原先硬编码在 server/text/searcher.py，见 RAG-10）
    text_query_max_words: int = 8
    text_query_and_words: int = 5
    text_query_or_words: int = 6
    text_query_and_min_hits: int = 3
    # 向量/hybrid 下"无共享词"拒答的分数阈值
    vector_refusal_min_score: float = 0.25
    # 演示 / 限流 / 缓存
    rate_limit_per_minute: int = 30
    cache_ttl_seconds: int = 3600
    history_max_turns: int = 4
    query_top_k_graph: int = 40
    query_top_k_text: int = 30
    # 送入 F06 的融合证据条数上限（18 = v4 口径；调小可压制推理长度/截断与首延迟）
    query_fusion_limit: int = 18

    # ---- 数据版本固定（2026-09-15 审核 P0-7）----
    # 显式活跃版本；空字符串 = 开发态"最新一致版本"。生产必须显式设置，
    # 否则目录里出现更大版本号时重启会静默切换数据，无法灰度/回滚。
    active_version: str = ""
    require_active_version: bool = False
    # 上面这项是不是**被显式配置**过（.env 或环境变量里写了 RAG_REQUIRE_ACTIVE_VERSION）。
    # 只设了 RAG_ACTIVE_VERSION（或 run_server --version）时它会被推导为 True，
    # 那种"隐式生产"只告警；显式写了才当作生产档，触发 CORS 等启动门禁（R5-5）。
    require_active_version_explicit: bool = False
    # 版本来源声明（第五轮审核 P0-4）：由启动方写入，让 health 能区分
    # "CLI 显式指定"与"环境变量固定"——两者都会被 run_server 写进 RAG_ACTIVE_VERSION，
    # 只看环境变量会丢掉来源。取值：cli_explicit / env_pinned / latest_scan（空=未声明）。
    version_source_hint: str = ""

    # ---- 请求尺寸/长度边界（2026-09-15 审核 P0-2）----
    request_max_bytes: int = 65536
    question_max_chars: int = 500
    session_id_max_chars: int = 128
    history_content_max_chars: int = 4000
    history_max_items: int = 40
    corrections_max_items: int = 20
    filters_max_items: int = 20
    filter_value_max_chars: int = 64

    # ---- 限流与缓存容量（2026-09-15 审核 P1-3 / P1-4）----
    rate_limit_max_keys: int = 4096
    rate_limit_trust_forwarded_for: bool = False
    rate_limit_trusted_proxies: List[str] = field(default_factory=list)
    cache_max_entries: int = 2048

    # ---- 流式安全与保活（2026-09-15 审核 P0-3 / P1-8）----
    expose_thinking: bool = False
    sse_heartbeat_seconds: float = 15.0
    sse_max_duration_seconds: float = 300.0
    # 非流式问答（POST /api/query/json）的聚合超时预算与可选共享密钥。
    # 预算独立于 SSE：SSE 的心跳/总时长只约束流式通道（开发文档 6.4）。
    query_json_timeout_seconds: float = 30.0
    bot_api_key: str = ""
    # 上面这项是不是**被显式配置**过（环境变量/.env 里出现了 RAG_BOT_API_KEY 键）。
    # 用于区分"没配（=不校验，正常）"与"配了个空白值（=漏配，必须报错）"，
    # 与 require_active_version_explicit 同一思路。
    bot_api_key_explicit: bool = False

    # ---- 服务端身份校验（第 12 轮审查 P1-1）----
    # 与旧后端共享的 JWT 密钥（HS256）。两处必须同值，否则验签必然失败。
    # 留空且未开启 require_auth = 不校验（内网/开发现状）；此时对外接口只能靠
    # nginx 认证保护，health 会就此给出告警。
    jwt_secret: str = ""
    jwt_secret_explicit: bool = False
    # 是否强制要求可信身份：为真时 /api/query（SSE）与非流式接口都要求携带旧后端
    # 签发的 JWT；验不过一律 401，不再存在"知道地址就能调"的通道。
    require_auth: bool = False
    require_auth_explicit: bool = False
    # 鉴权模式（第 13 轮整改）。把"谁来把关身份"这件事显式化——原先只有一个布尔
    # `RAG_REQUIRE_AUTH`，于是"由 nginx 的 auth_basic 把关"和"根本没人在把关"
    # 在配置里长得一模一样，运维看不出自己属于哪一种：
    #   jwt      —— 本服务用共享密钥验签（require_auth=True）；
    #   nginx    —— 身份由 nginx/前置网关把关，本服务只监听回环（require_auth=False）；
    #   disabled —— 无人校验身份（require_auth=False），**只允许开发环境**。
    # 显式生产档下出现 disabled 会拒绝启动，见 auth_startup_problem()。
    auth_mode: str = "disabled"
    auth_mode_explicit: bool = False
    # token 的来源与受众（第 13 轮整改）。与签发端 backend/jwt_util.py 的取值必须一致，
    # 不一致时表现为"验签通过但请求 401"——所以这两个值只在两端同时改。
    # 收窄到具体取值而不是"不校验"：同一把密钥可能被多个服务共用，把发给别人的 token
    # 拿来调 RAG（或反过来）本来就不该成立。
    jwt_issuer: str = "china-war-backend"
    jwt_audience: str = "china-war-rag"
    # ---- 凭证撤销查询（第 13 轮复核，文档第四节方案 B：Token Introspection）----
    # 验签只证明"这张 token 是旧后端签的"，证明不了"它现在还作不作数"：管理员停用账号
    # 或用户改密码之后，旧 token 仍能调本服务直到自然过期（默认 7 天）。配上这一组
    # 之后，本服务会向旧后端问一次"这张凭证还有效吗"，答案在 ttl 秒内复用。
    #
    # 启用条件 = **URL 与密钥都非空**。两个都留空即不查询（默认，保持内网部署行为），
    # 此时 /api/health 会明确给出"撤销延迟到 token 到期"的告警——这条边界必须可见，
    # 而不是靠读代码才知道。
    introspect_url: str = ""
    introspect_service_key: str = ""
    # 判定结果的缓存时长（秒）。它同时是"撤销生效延迟"的上界：改密码/封号后，
    # 最迟这么多秒之后本服务开始拒绝旧凭证。
    introspect_ttl_seconds: float = 30.0
    # 单次查询的超时（秒）。后端是同一台机器上的本地调用，正常在毫秒级；
    # 给到秒级是为了让"后端正在重启"表现为一次快速失败而不是把请求挂住。
    introspect_timeout_seconds: float = 3.0
    # 后端不可用（连不上 / 超时 / 返回非预期内容）时的取舍：
    #   closed —— 拒绝请求（默认）。理由：这是一条**安全**查询，无法确认凭证状态时
    #             放行等于让"把后端打挂"成为一种绕过撤销的手段；
    #   open   —— 放行。适合"问答可用性优先于即时撤销"的场合，风险是把后端故障
    #             变成一段所有人都不受校验的窗口。
    # 两种取值都要显式选：默认 closed 会让后端故障期间问答不可用，这是有意的取舍，
    # 因此 health 会把它与当前取值一起报出来。
    introspect_fail_mode: str = "closed"
    # 生产档下**必须显式选择撤销策略**（第 13 轮复核整改 §2.7）。
    #
    # 问题：原先"两个值都不填"就等于接受"停用账号 / 改密码后旧 token 在自然过期前
    # （默认 7 天）仍能调用 RAG"。而这条边界的代价只有 health 告警能看见——
    # 而告警是会被忽略的，正如当初那个布尔鉴权开关一样。
    #
    # 所以生产档（RAG_REQUIRE_ACTIVE_VERSION=true）下二选一：
    #   RAG_REQUIRE_REVOCATION_CHECK=true   把撤销查询配齐（url + 服务间密钥）；
    #   RAG_ALLOW_DELAYED_REVOCATION=true   显式接受延迟撤销（写明"我知道后果"）。
    # 都不做则**拒绝启动**。
    allow_delayed_revocation: bool = False
    allow_delayed_revocation_explicit: bool = False
    # `RAG_REQUIRE_REVOCATION_CHECK=true`：**要求**撤销查询必须配齐（与生产档无关）。
    # 它给"我知道必须有它，别的环境随手起也必须有"这种诉求一个明确写法；
    # 而"生产档下不配就得显式接受延迟"是上面那条默认规则。两者可以同时用。
    require_revocation_check: bool = False
    cors_allow_origins: List[str] = field(default_factory=lambda: ["*"])
    # 显式确认"就是要公开 API"（第五轮审核 R5-5）：生产 + wildcard CORS 时
    # 必须为真，否则 server.api 启动即失败，避免漏配把公开接口暴露给任意站点。
    allow_public_cors: bool = False

    # ---- 同步工作线程池（2026-09-15 第四轮复核 P1-5）----
    sync_pool_max_workers: int = 8
    sync_pool_max_queue: int = 32
    # 收尾余量：外部调用预算 + 余量 必须严格小于 SSE 总上限，
    # 否则会出现"外部调用还没返回、服务端已经按 deadline 收流"的现象（2026-09-16 工作单 P1-5）
    shutdown_margin_seconds: int = 15
    # 停机时有上限地等待在途同步任务（第五轮审核 P0-3）
    shutdown_drain_seconds: float = 10.0

    @property
    def cors_allows_any_origin(self) -> bool:
        """CORS 是否为通配（任意站点可跨域调用）。"""
        return "*" in [o.strip() for o in (self.cors_allow_origins or [])]

    def cors_startup_problem(self) -> Optional[str]:
        """**显式生产档** + 通配 CORS 且未显式确认时返回错误说明，否则 None（R5-5）。

        触发条件刻意收窄到"显式配置了 RAG_REQUIRE_ACTIVE_VERSION=true"：
        `run_server.py --version` 也会让 require_active_version 推导为真，但那是
        本地/演示启动方式（README 的推荐命令），没理由因此拒绝启动——
        隐式生产只告警（见 `cors_warning()`），显式生产才 fail-fast。

        放在这里而不是 `validate()`：config 校验被所有离线脚本共享，
        而 CORS 只对**对外服务**有意义，没理由让离线构建脚本因此起不来。
        """
        if not self.cors_allows_any_origin or self.allow_public_cors:
            return None
        if not (self.require_active_version and self.require_active_version_explicit):
            return None
        return (
            "生产模式（显式设置 RAG_REQUIRE_ACTIVE_VERSION=true）下 CORS_ALLOW_ORIGINS 仍为 *："
            "任意站点都能从浏览器直接调用公开问答接口，消耗限流配额与模型成本。"
            "请设置 CORS_ALLOW_ORIGINS=<站点域名[,域名]>；"
            "若确实要公开 API，显式设置 ALLOW_PUBLIC_CORS=true 确认这一决定。"
        )

    def cors_warning(self) -> Optional[str]:
        """通配 CORS 的告警文案（不阻断启动）；非通配返回 None。"""
        if not self.cors_allows_any_origin:
            return None
        if self.allow_public_cors:
            return "CORS_ALLOW_ORIGINS=*：已用 ALLOW_PUBLIC_CORS=true 显式确认公开跨域访问"
        return ("CORS_ALLOW_ORIGINS=*（任意站点可跨域调用）：本地开发可接受，"
                "生产必须限定站点域名；显式生产档（RAG_REQUIRE_ACTIVE_VERSION=true）"
                "下服务会因此拒绝启动")

    # ---- 身份校验的启动判定（第 12 轮审查 P1-1 / 第 13 轮整改）----
    @property
    def is_explicit_production(self) -> bool:
        """是否处于**显式生产档**（显式设置了 RAG_REQUIRE_ACTIVE_VERSION=true）。

        本模块里"生产"的口径统一由这一条定义（CORS 门禁、鉴权门禁都用它）：
        只看 `require_active_version` 会把 `run_server.py --version` 这种本地演示启动
        也算成生产，那会让开发机因为没配鉴权而启动失败。
        """
        return bool(self.require_active_version and self.require_active_version_explicit)

    @property
    def auth_protected(self) -> bool:
        """身份是否在本服务侧校验（= jwt 档）。"""
        return bool(self.require_auth)

    def auth_startup_problem(self) -> Optional[str]:
        """配置自相矛盾或生产档不安全时返回错误说明，否则 None。

        拦三件事：

        1. **说了要校验却没给密钥**：`RAG_REQUIRE_AUTH=true` 而没有密钥，等于服务起来后
           每个请求都 401——与其等用户反馈"问答全挂"，不如启动即失败。
        2. **生产档 + disabled**（第 13 轮整改）：显式生产档下 `RAG_AUTH_MODE=disabled`
           意味着"知道 /rag/ 地址的人都能调用问答接口"，既不受旧系统角色控制，也消耗
           模型配额与限流额度。原先这种部署只打一条 WARNING——而 WARNING 会被忽略，
           没配的人根本不会去看日志。现在改为启动即失败，强制部署方**显式做一次选择**：
           `jwt` 或 `nginx`。
        3. **鉴权模式取值非法**：拼错（如 `jwt `、`Jwt`、`none`）若静默回落成 disabled，
           运维会以为自己在验签而实际没有。这里直接拒绝。

        刻意**不**在"显式生产档 + nginx 档"时拒绝：那种部署由 nginx 的 auth_basic 或
        前置网关把关，服务只监听回环，是合法且已被文档记录的做法
        （见 deploy/nginx/china-war.conf 第 3 节）。bind 地址的对应约束在
        `scripts/run_server.py` 里检查——只有那里才知道真实监听地址。

        与 cors_startup_problem 一致，放在这里而不是 validate()：config 校验被所有
        离线脚本共享，而鉴权只对**对外服务**有意义。
        """
        mode = (self.auth_mode or "").strip().lower()
        if mode not in AUTH_MODES:
            return (
                f"RAG_AUTH_MODE 必须是 {'/'.join(AUTH_MODES)} 之一，当前 {self.auth_mode!r}。"
                "留空即关闭服务端校验；生产部署请显式设置 RAG_AUTH_MODE=jwt（本服务验签）"
                "或 RAG_AUTH_MODE=nginx（由 nginx 把关且只监听回环）"
            )
        if mode == "jwt" and not self.jwt_secret:
            return (
                "RAG_AUTH_MODE=jwt 但未配置 JWT 密钥（RAG_JWT_SECRET / JWT_SECRET）："
                "服务启动后所有问答请求都会因为无法验签而被拒（401）。"
                "请把与旧后端 backend/.env 相同的 JWT_SECRET 值配到本服务，"
                "或改用 RAG_AUTH_MODE=nginx 由 nginx 把关。"
            )
        # P2-7：新老开关**语义冲突**时拒绝启动。
        # `RAG_REQUIRE_AUTH=true` 说的是"本服务自己验签"，而 `RAG_AUTH_MODE=nginx`
        # 说的是"验签交给网关"——原实现让新模式静默覆盖旧开关（require_auth=False），
        # 于是"两边都不拦"：nginx 那边若没配 auth_basic，就没有任何人在把关，
        # 而配置看起来是写了的（这正是本项目反复出现的那类缺陷）。
        require_auth_env = (os.environ.get("RAG_REQUIRE_AUTH") or "").strip().lower()
        if mode != "jwt" and require_auth_env in ("1", "true", "yes", "on"):
            return (
                f"配置自相矛盾：RAG_REQUIRE_AUTH=true（要求本服务验签）"
                f"与 RAG_AUTH_MODE={mode}（不验签）同时存在。"
                "RAG_AUTH_MODE 是新的分档开关，它会覆盖旧开关——两个都留着会让"
                "「到底谁在把关」变成一件只能靠读代码才知道的事。"
                "请二选一：删掉 RAG_REQUIRE_AUTH，或把 RAG_AUTH_MODE 改成 jwt"
            )
        if mode == "disabled" and self.is_explicit_production:
            return (
                "生产模式（显式设置 RAG_REQUIRE_ACTIVE_VERSION=true）下 RAG_AUTH_MODE 仍是 "
                "disabled：知道 /rag/ 地址的人都能直接调用问答接口，不受旧系统角色控制，"
                "并消耗模型配额与限流额度。请显式设置 RAG_AUTH_MODE=jwt（本服务验签，"
                "需同时配好 RAG_JWT_SECRET）或 RAG_AUTH_MODE=nginx（由 nginx 把关，"
                "服务只监听回环地址）。"
            )
        return None

    def bot_channel_warning(self) -> Optional[str]:
        """jwt 档下未配 `RAG_BOT_API_KEY` 时的告警（第 14 轮审计 P2-20）。

        jwt 档推出 `require_auth=True`，而非流式接口的准入是"有效 JWT **或**正确的
        X-Bot-Key，二者其一"。飞书机器人没有用户身份、只会发 `X-Bot-Key`，所以
        "jwt 档 + 空 Bot Key"下**机器人每问必被 401**——而机器人侧的降级文案只说
        "RAG 不可用"，运维会朝"RAG 挂了"的方向修，方向完全是反的。

        为什么放在 health 而不是启动门禁：**不是每个部署都接机器人**（它是可选的 IM 入口），
        把"必须配 Bot Key"做成一票否决会挡住那些根本不用机器人的部署。
        安装脚本按"仓库里有没有 feishu-bot/.env"来判断——那份配置在就说明要部署机器人。
        """
        mode = (self.auth_mode or "").strip().lower()
        if mode != "jwt":
            return None
        if (getattr(self, "bot_api_key", "") or "").strip():
            return None
        return ("RAG_AUTH_MODE=jwt 但未配置 RAG_BOT_API_KEY：飞书机器人（只发 X-Bot-Key，"
                "没有用户身份）调用 /api/query/json 会被 401 拒绝，而机器人侧只会显示"
                "「RAG 不可用」。要接机器人请把两侧的 RAG_BOT_API_KEY 填成同一个值；"
                "不接机器人可以忽略本条。")

    def auth_warning(self) -> Optional[str]:
        """未启用服务端身份校验时的告警文案（不阻断启动）。"""
        mode = (self.auth_mode or "").strip().lower()
        if mode == "jwt":
            return None
        if self.jwt_secret:
            # 配了密钥却没走 jwt 档：多半是想开但漏了配置，值得提醒
            return (f"已配置 JWT 密钥但 RAG_AUTH_MODE 是 {mode or '空'}（非 jwt）："
                    "问答接口目前不校验身份，请显式设置 RAG_AUTH_MODE=jwt")
        if mode == "nginx":
            return ("RAG_AUTH_MODE=nginx：本服务不校验身份，安全性完全依赖 nginx/前置网关的"
                    "认证，且必须只监听回环地址（见 deploy/nginx/china-war.conf）")
        return ("未启用服务端身份校验（RAG_AUTH_MODE 未设置或为 disabled）："
                "知道 /rag/ 地址即可调用问答接口，不受旧系统角色控制，并消耗模型配额。"
                "正式部署请二者之一：RAG_AUTH_MODE=jwt（+ RAG_JWT_SECRET）"
                "或 RAG_AUTH_MODE=nginx（+ 只监听回环地址）")

    # ---- 凭证撤销查询（第 13 轮复核，文档第四节方案 B）----
    @property
    def revocation_check_enabled(self) -> bool:
        """是否向旧后端查询凭证状态（URL 与密钥都配好才算启用）。"""
        return bool((self.introspect_url or "").strip()
                    and (self.introspect_service_key or "").strip())

    def revocation_startup_problem(self) -> Optional[str]:
        """撤销查询相关配置明显错误时返回说明，否则 None（与 auth_startup_problem 同思路）。

        只拦"配了但一定不工作"的情形：取值非法的 fail_mode、非正的缓存时长/超时。
        半套配置（只配 URL 或只配密钥）不在这里拦——那种情况按"未启用"处理，
        由 `revocation_warning()` 报出来，因为服务本身仍可正常工作。
        """
        if self.introspect_fail_mode not in FAIL_MODES:
            return (
                f"RAG_INTROSPECT_FAIL_MODE 必须是 {' / '.join(FAIL_MODES)} 之一，"
                f"当前 {self.introspect_fail_mode!r}。closed=后端不可用时拒绝请求（默认），"
                "open=放行"
            )
        if self.introspect_ttl_seconds <= 0:
            return (f"RAG_INTROSPECT_TTL_SECONDS 必须为正数，"
                    f"当前 {self.introspect_ttl_seconds!r}")
        if self.introspect_timeout_seconds <= 0:
            return (f"RAG_INTROSPECT_TIMEOUT_SECONDS 必须为正数，"
                    f"当前 {self.introspect_timeout_seconds!r}")
        # 显式要求了撤销查询却没配齐：这是配置自相矛盾（与"说了要校验却没给密钥"同一类），
        # 直接拒绝，而不是让"要求"变成一句没人执行的注释
        if self.require_revocation_check and not self.revocation_check_enabled:
            return (
                "RAG_REQUIRE_REVOCATION_CHECK=true，但撤销查询未配齐：需要同时设置 "
                "RAG_INTROSPECT_URL 与 RAG_INTERNAL_SERVICE_KEY（后者与后端 "
                "INTERNAL_SERVICE_KEY 同值）"
            )
        # §2.7：生产档 + jwt 档下，"撤销延迟"不能是被默认接受的既成事实
        if (self.is_explicit_production and self.auth_protected
                and not self.revocation_check_enabled
                and not self.allow_delayed_revocation):
            missing = ("RAG_INTROSPECT_URL 与 RAG_INTERNAL_SERVICE_KEY"
                       if not (self.introspect_url or "").strip()
                       and not (self.introspect_service_key or "").strip()
                       else "凭证撤销查询的配置（只配了一半）")
            return (
                f"生产档 + RAG_AUTH_MODE=jwt，但未启用凭证撤销查询（{missing} 未配齐）："
                "账号被停用或改密码后，旧 token 在自然过期前（后端 JWT_TTL_SECONDS，"
                "默认 7 天）仍能调用问答接口——旧后端已经拒绝该凭证，两侧口径不同。"
                "二选一：把 RAG_INTROSPECT_URL 与 RAG_INTERNAL_SERVICE_KEY 配齐并设 "
                "RAG_REQUIRE_REVOCATION_CHECK=true；或显式接受该延迟并设 "
                "RAG_ALLOW_DELAYED_REVOCATION=true（后果见 docs/deploy.md 与 "
                "deploy/scripts/check_rag_auth.sh 的提示）"
            )
        return None

    @property
    def revocation_policy(self) -> str:
        """撤销策略的当前口径（health 用）：enforced / delayed / not-applicable。

        三态而不是布尔：`enabled=false` 既可能是"显式接受了延迟"，也可能是"根本没配"——
        而这两者对一个部署的意义完全不同（前者是决定，后者是遗漏）。
        """
        if not self.auth_protected:
            return "not-applicable"
        if self.revocation_check_enabled:
            return "enforced"
        return "delayed"

    @property
    def revocation_max_delay_seconds(self) -> Optional[float]:
        """撤销生效延迟的上界（秒）；无法给出确定值时返回 None。

        启用查询时就是缓存 TTL（判定结论在 TTL 内复用）；未启用时上界是"token 的自然
        过期时间"，而那个数值由签发端（旧后端的 JWT_TTL_SECONDS）决定，本服务不猜——
        返回 None，由调用方配上"直到 token 过期"的说明。
        """
        if self.revocation_check_enabled:
            return self.introspect_ttl_seconds
        return None

    def revocation_warning(self) -> Optional[str]:
        """未启用（或只配了一半）撤销查询时的告警文案，不阻断启动。

        这段文案就是文档要求写明的**撤销边界**：把它放进 health 的 warnings，
        是因为"停用账号后 RAG 还能用到 token 到期"这件事必须在运行中的服务上可见，
        而不是只存在于一份部署文档里。
        """
        mode = (self.auth_mode or "").strip().lower()
        if mode != "jwt":
            # 不验签的档位没有"该不该承认这张 token"这个问题（nginx 档由网关把关）
            return None
        if self.revocation_check_enabled:
            return None
        # 显式接受延迟就**不再告警**：决定已经写在配置里（health 的 policy=delayed /
        # delayed_revocation_accepted=true 就是它的记录），反复告警只会训练人忽略告警。
        # 与"漏配"的区别必须留在 health 字段里，而不是靠"有没有这条文案"。
        if self.allow_delayed_revocation:
            return None
        # 只配了一半时说得更具体：漏配的一半才是排障要用的信息
        if bool((self.introspect_url or "").strip()) != bool(
                (self.introspect_service_key or "").strip()):
            missing = "RAG_INTERNAL_SERVICE_KEY" if self.introspect_url else "RAG_INTROSPECT_URL"
            return (f"凭证撤销查询只配了一半（缺少 {missing}）：本服务不会向后端确认凭证状态，"
                    "被停用或改过密码的账号在 token 自然过期前仍可调用问答接口")
        return ("未启用凭证撤销查询（RAG_INTROSPECT_URL + RAG_INTERNAL_SERVICE_KEY）："
                "账号被停用或改密码后，旧 token 在自然过期前仍可调用问答接口"
                "（旧后端已立刻拒绝，两边口径不同）")

    def validate(self) -> None:
        """启动期配置校验：非法值直接抛错，避免"启动成功但行为异常"。

        覆盖三类历史坑（2026-09-15 审核 P1-6）：
        - 负值/零值（限流 0 会把所有请求判为超限、top_k 0 会静默返回空证据）；
        - 未知枚举（TEXT_MODE 拼错会静默走 keyword，用户以为在用 hybrid）；
        - 尺寸边界越界（过小会误伤正常请求，过大等于没有保护）。
        """
        problems: list[str] = []

        def _positive(name: str, value) -> None:
            if value is None or value <= 0:
                problems.append(f"{name} 必须为正数，当前 {value!r}")

        text_mode = (self.text_mode or "").strip().lower()
        if text_mode not in ("keyword", "vector", "hybrid"):
            problems.append(f"TEXT_MODE 必须是 keyword/vector/hybrid，当前 {self.text_mode!r}")
        strategy = (self.text_hybrid_strategy or "").strip().lower()
        if strategy not in ("weighted", "rrf", "fallback"):
            problems.append(
                f"TEXT_HYBRID_STRATEGY 必须是 weighted/rrf/fallback，当前 {self.text_hybrid_strategy!r}"
            )
        if not 0.0 <= float(self.text_hybrid_keyword_weight) <= 1.0:
            problems.append(
                f"TEXT_HYBRID_KEYWORD_WEIGHT 必须在 0~1，当前 {self.text_hybrid_keyword_weight!r}"
            )
        if not 0.0 <= float(self.vector_refusal_min_score) <= 1.0:
            problems.append(
                f"VECTOR_REFUSAL_MIN_SCORE 必须在 0~1，当前 {self.vector_refusal_min_score!r}"
            )
        # 关键词检索用词上限：必须为正（0/负数会让 FTS 查询为空或行为不可预期）
        for name, value in (
            ("TEXT_QUERY_MAX_WORDS", self.text_query_max_words),
            ("TEXT_QUERY_AND_WORDS", self.text_query_and_words),
            ("TEXT_QUERY_OR_WORDS", self.text_query_or_words),
            ("TEXT_QUERY_AND_MIN_HITS", self.text_query_and_min_hits),
        ):
            if not isinstance(value, int) or value <= 0:
                problems.append(f"{name} 必须为正整数，当前 {value!r}")

        for name, value in (
            ("RATE_LIMIT_PER_MINUTE", self.rate_limit_per_minute),
            ("RATE_LIMIT_MAX_KEYS", self.rate_limit_max_keys),
            ("CACHE_TTL_SECONDS", self.cache_ttl_seconds),
            ("CACHE_MAX_ENTRIES", self.cache_max_entries),
            ("HISTORY_MAX_TURNS", self.history_max_turns),
            ("QUERY_TOP_K_GRAPH", self.query_top_k_graph),
            ("QUERY_TOP_K_TEXT", self.query_top_k_text),
            ("QUERY_FUSION_LIMIT", self.query_fusion_limit),
            ("REQUEST_MAX_BYTES", self.request_max_bytes),
            ("QUESTION_MAX_CHARS", self.question_max_chars),
            ("SESSION_ID_MAX_CHARS", self.session_id_max_chars),
            ("HISTORY_CONTENT_MAX_CHARS", self.history_content_max_chars),
            ("HISTORY_MAX_ITEMS", self.history_max_items),
            ("CORRECTIONS_MAX_ITEMS", self.corrections_max_items),
            ("FILTERS_MAX_ITEMS", self.filters_max_items),
            ("FILTER_VALUE_MAX_CHARS", self.filter_value_max_chars),
            ("LLM_TIMEOUT_SECONDS", self.llm_timeout_seconds),
            ("LLM_MAX_TOKENS", self.llm_max_tokens),
        ):
            _positive(name, value)

        if self.llm_max_retries < 0:
            problems.append(f"LLM_MAX_RETRIES 不能为负，当前 {self.llm_max_retries!r}")
        if self.llm_entity_timeout_seconds <= 0:
            problems.append(
                f"LLM_ENTITY_TIMEOUT_SECONDS 必须为正数，当前 {self.llm_entity_timeout_seconds!r}"
            )
        if self.embedding_dim <= 0:
            problems.append(f"EMBEDDING_DIM 必须为正数，当前 {self.embedding_dim!r}")
        if self.embedding_timeout_seconds <= 0:
            problems.append(
                f"EMBEDDING_TIMEOUT_SECONDS 必须为正数，当前 {self.embedding_timeout_seconds!r}"
            )
        if self.sse_heartbeat_seconds < 0:
            problems.append(
                f"SSE_HEARTBEAT_SECONDS 不能为负，当前 {self.sse_heartbeat_seconds!r}"
            )
        if self.sse_max_duration_seconds <= 0:
            problems.append(
                f"SSE_MAX_DURATION_SECONDS 必须为正数，当前 {self.sse_max_duration_seconds!r}"
            )
        if self.query_json_timeout_seconds <= 0:
            problems.append(
                f"QUERY_JSON_TIMEOUT_SECONDS 必须为正数，"
                f"当前 {self.query_json_timeout_seconds!r}"
            )
        if self.bot_api_key_explicit and not self.bot_api_key:
            # 显式写空（RAG_BOT_API_KEY=）在"空即关闭"的读法下会静默关闭鉴权，
            # 与 CORS_ALLOW_ORIGINS 的坑同源（第五轮整改复核 B8）：漏配必须报错，
            # 而不是静默变成"看起来配了密钥其实谁都能调"。
            problems.append(
                "RAG_BOT_API_KEY 被设置为空白：这会静默关闭 /api/query/json 的共享密钥校验。"
                "请填入实际密钥，或删除该配置项（不设置即为不校验）"
            )
        if self.chunk_max_chars <= 0 or self.chunk_overlap_chars < 0:
            problems.append(
                f"分块参数非法：CHUNK_MAX_CHARS={self.chunk_max_chars!r} "
                f"CHUNK_OVERLAP_CHARS={self.chunk_overlap_chars!r}"
            )
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            problems.append(
                f"CHUNK_OVERLAP_CHARS({self.chunk_overlap_chars}) 必须小于 "
                f"CHUNK_MAX_CHARS({self.chunk_max_chars})"
            )
        if self.active_version and not is_valid_version(self.active_version):
            problems.append(
                f"RAG_ACTIVE_VERSION 必须形如 YYYYMMDD_vN，当前 {self.active_version!r}"
            )
        hint = (self.version_source_hint or "").strip()
        if hint not in ("", "cli_explicit", "env_pinned", "latest_scan"):
            # 非法取值若静默回落，health 的来源字段会与真实行为不符（第五轮整改复核 B4）
            problems.append(
                "RAG_VERSION_SOURCE 必须是 cli_explicit / env_pinned / latest_scan（或留空），"
                f"当前 {self.version_source_hint!r}"
            )

        # 取值范围（第四轮复核 P1-7）：越界不再静默修正，直接给出变量名与合法区间
        if not 1 <= self.embedding_batch_size <= MAX_EMBEDDING_BATCH:
            problems.append(
                f"EMBEDDING_BATCH_SIZE 必须在 1~{MAX_EMBEDDING_BATCH}"
                f"（云端接口硬上限），当前 {self.embedding_batch_size!r}"
            )
        if not 1 <= self.query_fusion_limit <= 100:
            problems.append(
                f"QUERY_FUSION_LIMIT 必须在 1~100，当前 {self.query_fusion_limit!r}"
            )
        if not 0.0 <= self.text_hybrid_keyword_weight <= 1.0:  # noqa: SIM108
            problems.append(
                f"TEXT_HYBRID_KEYWORD_WEIGHT 必须在 0~1，当前 {self.text_hybrid_keyword_weight!r}"
            )
        if not 1 <= self.sync_pool_max_workers <= 64:
            problems.append(
                f"SYNC_POOL_MAX_WORKERS 必须在 1~64，当前 {self.sync_pool_max_workers!r}"
            )
        if not 0 <= self.sync_pool_max_queue <= 4096:
            problems.append(
                f"SYNC_POOL_MAX_QUEUE 必须在 0~4096，当前 {self.sync_pool_max_queue!r}"
            )
        if not 0.0 <= float(self.shutdown_drain_seconds) <= 120.0:
            problems.append(
                f"SHUTDOWN_DRAIN_SECONDS 必须在 0~120，当前 {self.shutdown_drain_seconds!r}"
            )
        if not self.cors_allow_origins:
            # 显式写空（CORS_ALLOW_ORIGINS=）在旧实现里被 `or ["*"]` 静默变成通配符，
            # 等于"漏配保护"反过来成了"放开保护"（第五轮整改复核 B8）
            problems.append(
                "CORS_ALLOW_ORIGINS 不能为空：留空会失去跨域白名单语义。"
                "开发用 `*`，生产填站点域名（逗号分隔）；两者都要显式写出"
            )

        # 外部调用超时预算必须**严格小于** SSE 总上限，并留出收尾余量（P1-5）：
        # 单次 llm 超时 × (重试次数+1) 与 embedding 超时 × 3 次尝试，加上
        # SHUTDOWN_MARGIN_SECONDS 之后仍要有富余，否则会出现"外部调用还没返回、
        # 服务端已经按 deadline 收流"的现象，日志与用户看到的现象对不上。
        # 注意用的是 >=：预算正好等于 deadline 也不允许（调度与序列化都要时间）。
        margin = self.shutdown_margin_seconds
        if margin < 0:
            problems.append(f"SHUTDOWN_MARGIN_SECONDS 不能为负，当前 {margin!r}")
        llm_budget = self.llm_timeout_seconds * (max(1, self.llm_max_retries + 1))
        if llm_budget + margin >= self.sse_max_duration_seconds:
            problems.append(
                f"LLM 超时预算 {llm_budget}s（LLM_TIMEOUT_SECONDS={self.llm_timeout_seconds} × "
                f"(LLM_MAX_RETRIES={self.llm_max_retries}+1)）+ 收尾余量 {margin}s "
                f"必须严格小于 SSE_MAX_DURATION_SECONDS={self.sse_max_duration_seconds}s"
            )
        embedding_budget = self.embedding_timeout_seconds * 3
        if embedding_budget + margin >= self.sse_max_duration_seconds:
            problems.append(
                f"embedding 超时预算 {embedding_budget}s（EMBEDDING_TIMEOUT_SECONDS="
                f"{self.embedding_timeout_seconds} × 3 次尝试）+ 收尾余量 {margin}s "
                f"必须严格小于 SSE_MAX_DURATION_SECONDS={self.sse_max_duration_seconds}s"
            )

        if problems:
            raise ValueError("配置校验失败：\n- " + "\n- ".join(problems))


def _active_version() -> str:
    return (os.environ.get("RAG_ACTIVE_VERSION", defaults.ACTIVE_VERSION) or "").strip()


def _csv_env(key: str, default: str = "") -> List[str]:
    """逗号分隔环境变量 → 去除空白项后的列表。"""
    raw = os.environ.get(key, default)
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def get_settings() -> Settings:
    # 鉴权模式的解析（第 13 轮整改）：新配置 RAG_AUTH_MODE 优先，旧开关
    # RAG_REQUIRE_AUTH 作为兼容回退。取值合法性交给 auth_startup_problem()——
    # 这里不抛异常，否则连 `python -c "from config.settings import get_settings"` 这类
    # 只想看一眼配置的诊断命令都会失败，而真正的门禁属于"对外服务"这一层。
    _mode_env = (os.environ.get("RAG_AUTH_MODE") or "").strip()
    if _mode_env:
        _auth_mode = _mode_env
    else:
        _auth_mode = "jwt" if _bool_env("RAG_REQUIRE_AUTH", defaults.REQUIRE_AUTH) \
            else defaults.AUTH_MODE

    settings = Settings(
        data_dir=_path_env("RAG_DATA_DIR", defaults.DATA_DIR),
        raw_dir=_path_env("RAG_RAW_DIR", defaults.RAW_DIR),
        snapshot_dir=_path_env("RAG_SNAPSHOT_DIR", defaults.SNAPSHOT_DIR),
        index_dir=_path_env("RAG_INDEX_DIR", defaults.INDEX_DIR),
        cache_dir=_path_env("RAG_CACHE_DIR", defaults.CACHE_DIR),
        log_dir=_path_env("RAG_LOG_DIR", defaults.LOG_DIR),
        frontend_dist=_path_env("FRONTEND_DIST", defaults.FRONTEND_DIST),
        legacy_sqlite_path=_path_env("LEGACY_SQLITE_PATH", defaults.LEGACY_SQLITE_PATH),
        legacy_raw_texts=[
            Path(p.strip()).expanduser()
            for p in os.environ.get(
                "LEGACY_RAW_TEXTS",
                ";".join(str(p) for p in defaults.LEGACY_RAW_TEXTS),
            ).split(";")
            if p.strip()
        ],
        governance_enable_relation_extraction=_bool_env(
            "GOVERNANCE_ENABLE_RELATION_EXTRACTION",
            defaults.GOVERNANCE_ENABLE_RELATION_EXTRACTION,
        ),
        index_build_embeddings=_bool_env(
            "INDEX_BUILD_EMBEDDINGS", defaults.INDEX_BUILD_EMBEDDINGS
        ),
        chunk_max_chars=int(os.environ.get("CHUNK_MAX_CHARS", defaults.CHUNK_MAX_CHARS)),
        chunk_overlap_chars=int(
            os.environ.get("CHUNK_OVERLAP_CHARS", defaults.CHUNK_OVERLAP_CHARS)
        ),
        llm_base_url=os.environ.get("LLM_BASE_URL", defaults.LLM_BASE_URL),
        # 密钥别名链（按优先级）：LLM_API_KEY → DEEPSEEK_API_KEY
        #   → RAG-command（中转，项目期优先）→ RAG-deepseek-v4（官方，项目结束后启用）
        # 切回官方 = 从系统环境变量删掉 RAG-command，零代码切换
        llm_api_key=_first_env("LLM_API_KEY", "DEEPSEEK_API_KEY",
                               "RAG_COMMAND", "RAG_DEEPSEEK_V4",
                               # 历史别名（连字符名，Linux 下无法 export；仅为兼容存量机器保留）
                               "RAG-command", "RAG-deepseek-v4",
                               default=defaults.LLM_API_KEY),
        llm_model=os.environ.get("LLM_MODEL", defaults.LLM_MODEL),
        llm_timeout_seconds=int(
            os.environ.get("LLM_TIMEOUT_SECONDS", defaults.LLM_TIMEOUT_SECONDS)
        ),
        llm_max_retries=int(
            os.environ.get("LLM_MAX_RETRIES", defaults.LLM_MAX_RETRIES)
        ),
        llm_max_tokens=int(
            os.environ.get("LLM_MAX_TOKENS", defaults.LLM_MAX_TOKENS)
        ),
        enable_llm_entity_fallback=_bool_env(
            "ENABLE_LLM_ENTITY_FALLBACK", defaults.ENABLE_LLM_ENTITY_FALLBACK
        ),
        llm_entity_timeout_seconds=int(os.environ.get(
            "LLM_ENTITY_TIMEOUT_SECONDS", defaults.LLM_ENTITY_TIMEOUT_SECONDS)),
        fallback_llm_base_url=os.environ.get(
            "FALLBACK_LLM_BASE_URL", defaults.FALLBACK_LLM_BASE_URL
        ),
        # 备用模型默认可用官方密钥（FALLBACK_LLM_BASE_URL 配好即生效，未配则不降级）
        fallback_llm_api_key=_first_env(
            "FALLBACK_LLM_API_KEY", "RAG_DEEPSEEK_V4",
            # 历史别名（同上）
            "RAG-deepseek-v4",
            default=defaults.FALLBACK_LLM_API_KEY,
        ),
        fallback_llm_model=os.environ.get(
            "FALLBACK_LLM_MODEL", defaults.FALLBACK_LLM_MODEL
        ),
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", defaults.EMBEDDING_BASE_URL),
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", defaults.EMBEDDING_API_KEY),
        embedding_model=os.environ.get("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", defaults.EMBEDDING_DIM) or 0),
        # 不再静默 clamp：原值进配置，由 validate() 给出变量名与合法区间（第四轮复核 P1-7）
        embedding_batch_size=int(os.environ.get(
            "EMBEDDING_BATCH_SIZE", defaults.EMBEDDING_BATCH_SIZE)),
        embedding_timeout_seconds=int(os.environ.get(
            "EMBEDDING_TIMEOUT_SECONDS", defaults.EMBEDDING_TIMEOUT_SECONDS)),
        chroma_collection=os.environ.get("CHROMA_COLLECTION", defaults.CHROMA_COLLECTION),
        text_mode=(os.environ.get("TEXT_MODE", defaults.TEXT_MODE) or "hybrid").strip().lower(),
        text_hybrid_strategy=(os.environ.get(
            "TEXT_HYBRID_STRATEGY", defaults.TEXT_HYBRID_STRATEGY) or "rrf").strip().lower(),
        text_hybrid_keyword_weight=float(os.environ.get(
            "TEXT_HYBRID_KEYWORD_WEIGHT", defaults.TEXT_HYBRID_KEYWORD_WEIGHT)),
        text_query_max_words=int(os.environ.get(
            "TEXT_QUERY_MAX_WORDS", defaults.TEXT_QUERY_MAX_WORDS)),
        text_query_and_words=int(os.environ.get(
            "TEXT_QUERY_AND_WORDS", defaults.TEXT_QUERY_AND_WORDS)),
        text_query_or_words=int(os.environ.get(
            "TEXT_QUERY_OR_WORDS", defaults.TEXT_QUERY_OR_WORDS)),
        text_query_and_min_hits=int(os.environ.get(
            "TEXT_QUERY_AND_MIN_HITS", defaults.TEXT_QUERY_AND_MIN_HITS)),
        vector_refusal_min_score=float(os.environ.get(
            "VECTOR_REFUSAL_MIN_SCORE", defaults.VECTOR_REFUSAL_MIN_SCORE)),
        rate_limit_per_minute=int(
            os.environ.get("RATE_LIMIT_PER_MINUTE", defaults.RATE_LIMIT_PER_MINUTE)
        ),
        cache_ttl_seconds=int(
            os.environ.get("CACHE_TTL_SECONDS", defaults.CACHE_TTL_SECONDS)
        ),
        history_max_turns=int(
            os.environ.get("HISTORY_MAX_TURNS", defaults.HISTORY_MAX_TURNS)
        ),
        query_top_k_graph=int(
            os.environ.get("QUERY_TOP_K_GRAPH", defaults.QUERY_TOP_K_GRAPH)
        ),
        query_top_k_text=int(
            os.environ.get("QUERY_TOP_K_TEXT", defaults.QUERY_TOP_K_TEXT)
        ),
        query_fusion_limit=int(os.environ.get(
            "QUERY_FUSION_LIMIT", defaults.QUERY_FUSION_LIMIT)),
        active_version=_active_version(),
        # 显式配了版本就默认要求它可用（写错版本名必须启动失败，而不是静默回退最新）；
        # 想临时忽略用 RAG_REQUIRE_ACTIVE_VERSION=false。
        require_active_version=_bool_env(
            "RAG_REQUIRE_ACTIVE_VERSION", bool(_active_version())
        ),
        # 「显式配置过」= 该键出现在环境变量或 .env 里（load_dotenv 写进 os.environ）
        require_active_version_explicit=os.environ.get("RAG_REQUIRE_ACTIVE_VERSION") is not None,
        version_source_hint=(os.environ.get("RAG_VERSION_SOURCE", "") or "").strip(),
        request_max_bytes=int(os.environ.get(
            "REQUEST_MAX_BYTES", defaults.REQUEST_MAX_BYTES)),
        question_max_chars=int(os.environ.get(
            "QUESTION_MAX_CHARS", defaults.QUESTION_MAX_CHARS)),
        session_id_max_chars=int(os.environ.get(
            "SESSION_ID_MAX_CHARS", defaults.SESSION_ID_MAX_CHARS)),
        history_content_max_chars=int(os.environ.get(
            "HISTORY_CONTENT_MAX_CHARS", defaults.HISTORY_CONTENT_MAX_CHARS)),
        history_max_items=int(os.environ.get(
            "HISTORY_MAX_ITEMS", defaults.HISTORY_MAX_ITEMS)),
        corrections_max_items=int(os.environ.get(
            "CORRECTIONS_MAX_ITEMS", defaults.CORRECTIONS_MAX_ITEMS)),
        filters_max_items=int(os.environ.get(
            "FILTERS_MAX_ITEMS", defaults.FILTERS_MAX_ITEMS)),
        filter_value_max_chars=int(os.environ.get(
            "FILTER_VALUE_MAX_CHARS", defaults.FILTER_VALUE_MAX_CHARS)),
        rate_limit_max_keys=int(os.environ.get(
            "RATE_LIMIT_MAX_KEYS", defaults.RATE_LIMIT_MAX_KEYS)),
        rate_limit_trust_forwarded_for=_bool_env(
            "RATE_LIMIT_TRUST_FORWARDED_FOR", defaults.RATE_LIMIT_TRUST_FORWARDED_FOR
        ),
        rate_limit_trusted_proxies=_csv_env(
            "RATE_LIMIT_TRUSTED_PROXIES", defaults.RATE_LIMIT_TRUSTED_PROXIES
        ),
        cache_max_entries=int(os.environ.get(
            "CACHE_MAX_ENTRIES", defaults.CACHE_MAX_ENTRIES)),
        expose_thinking=_bool_env("EXPOSE_THINKING", defaults.EXPOSE_THINKING),
        sse_heartbeat_seconds=float(os.environ.get(
            "SSE_HEARTBEAT_SECONDS", defaults.SSE_HEARTBEAT_SECONDS)),
        sse_max_duration_seconds=float(os.environ.get(
            "SSE_MAX_DURATION_SECONDS", defaults.SSE_MAX_DURATION_SECONDS)),
        query_json_timeout_seconds=float(os.environ.get(
            "QUERY_JSON_TIMEOUT_SECONDS", defaults.QUERY_JSON_TIMEOUT_SECONDS)),
        # 密钥两侧空白一律去掉：带空白的密钥在 HTTP 头里传不过去，等于配了也校验失败
        bot_api_key=(os.environ.get("RAG_BOT_API_KEY", defaults.BOT_API_KEY) or "").strip(),
        bot_api_key_explicit=os.environ.get("RAG_BOT_API_KEY") is not None,
        # ---- 服务端身份校验（第 12 轮审查 P1-1）----
        # 密钥名接受两个：RAG_JWT_SECRET 是本服务自己的名字；JWT_SECRET 是旧后端
        # backend/.env 里的名字。两处在同一台机器上时只需配一次 JWT_SECRET，
        # 而用 systemd EnvironmentFile 分开注入时又能各写各的名字。
        jwt_secret=_first_env("RAG_JWT_SECRET", "JWT_SECRET",
                              default=defaults.JWT_SECRET).strip(),
        jwt_secret_explicit=(
            os.environ.get("RAG_JWT_SECRET") is not None
            or os.environ.get("JWT_SECRET") is not None
        ),
        # 鉴权模式（第 13 轮整改）。解析顺序是"新配置优先、旧开关兼容"：
        # 1. 显式写了 RAG_AUTH_MODE → 以它为准（取值合法性由 auth_startup_problem 兜底）；
        # 2. 否则看旧的 RAG_REQUIRE_AUTH：true 视为 jwt 档，false 视为 disabled。
        #    这样"没升级配置的老部署"行为一字不变，而新部署有一处能一眼看懂
        #    "身份到底谁在把关"的配置项。
        # require_auth 仍保留为字段（api.py 与既有用例直接读它），由模式推导而来。
        auth_mode=_auth_mode,
        auth_mode_explicit=os.environ.get("RAG_AUTH_MODE") is not None,
        require_auth=(_auth_mode == "jwt"),
        require_auth_explicit=(
            os.environ.get("RAG_REQUIRE_AUTH") is not None
            or os.environ.get("RAG_AUTH_MODE") is not None
        ),
        jwt_issuer=os.environ.get("RAG_JWT_ISSUER", defaults.JWT_ISSUER).strip(),
        jwt_audience=os.environ.get("RAG_JWT_AUDIENCE", defaults.JWT_AUDIENCE).strip(),
        # 凭证撤销查询（第 13 轮复核）。密钥名接受两个：RAG_INTERNAL_SERVICE_KEY 是本服务
        # 自己的名字，INTERNAL_SERVICE_KEY 是旧后端 backend/.env 里的名字——单机部署时
        # "两边配同一个值"是最容易出错的一步，少一次改名就少一次踩坑机会。
        introspect_url=_first_env("RAG_INTROSPECT_URL",
                                  default=defaults.INTROSPECT_URL),
        introspect_service_key=_first_env("RAG_INTERNAL_SERVICE_KEY", "INTERNAL_SERVICE_KEY",
                                          default=defaults.INTROSPECT_SERVICE_KEY),
        introspect_ttl_seconds=_float_env("RAG_INTROSPECT_TTL_SECONDS",
                                          defaults.INTROSPECT_TTL_SECONDS, 30.0),
        introspect_timeout_seconds=_float_env("RAG_INTROSPECT_TIMEOUT_SECONDS",
                                              defaults.INTROSPECT_TIMEOUT_SECONDS, 3.0),
        introspect_fail_mode=_first_env("RAG_INTROSPECT_FAIL_MODE",
                                        default=defaults.INTROSPECT_FAIL_MODE).lower(),
        # §2.7：两个开关都接受；`RAG_REQUIRE_REVOCATION_CHECK` 只是"要撤销查询"的
        # 另一种写法（等价于把 url + 密钥配齐），因此它不单独存字段——
        # 真正决定行为的是"撤销查询能不能用"，而不是运维写了哪个开关名。
        allow_delayed_revocation=_bool_env("RAG_ALLOW_DELAYED_REVOCATION",
                                            defaults.ALLOW_DELAYED_REVOCATION),
        require_revocation_check=_bool_env("RAG_REQUIRE_REVOCATION_CHECK",
                                          defaults.REQUIRE_REVOCATION_CHECK),
        allow_delayed_revocation_explicit=(
            os.environ.get("RAG_ALLOW_DELAYED_REVOCATION") is not None
        ),
        # 不再 `or ["*"]`：显式空值必须报错，不能静默变成通配符（第五轮整改复核 B8）
        cors_allow_origins=_csv_env("CORS_ALLOW_ORIGINS", defaults.CORS_ALLOW_ORIGINS),
        allow_public_cors=_bool_env("ALLOW_PUBLIC_CORS", defaults.ALLOW_PUBLIC_CORS),
        sync_pool_max_workers=int(os.environ.get(
            "SYNC_POOL_MAX_WORKERS", defaults.SYNC_POOL_MAX_WORKERS)),
        sync_pool_max_queue=int(os.environ.get(
            "SYNC_POOL_MAX_QUEUE", defaults.SYNC_POOL_MAX_QUEUE)),
        shutdown_margin_seconds=int(os.environ.get(
            "SHUTDOWN_MARGIN_SECONDS", defaults.SHUTDOWN_MARGIN_SECONDS)),
        shutdown_drain_seconds=float(os.environ.get(
            "SHUTDOWN_DRAIN_SECONDS", defaults.SHUTDOWN_DRAIN_SECONDS)),
    )
    settings.validate()
    return settings
