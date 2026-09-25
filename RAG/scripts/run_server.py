"""RAGv5 在线服务启动脚本（F02–F06 SSE 问答接口）。

用法：
    python scripts/run_server.py [--host 127.0.0.1] [--port 8000] [--version 20260915_v1]

说明：脚本保持薄，只做 uvicorn 启动；业务在 server/。
需要 fastapi/uvicorn（见 RAG requirements.txt）；无 LLM 密钥也可启动，
F06 自动使用离线摘要回答器；配好 LLM_BASE_URL/LLM_API_KEY 后接真实模型。

数据版本（2026-09-15 审核 P0-7）：`--version` 会写入环境变量 RAG_ACTIVE_VERSION，
由 app 的 lifespan 读取并显式加载——旧实现只把这个参数用于启动自检打印，
uvicorn 重新导入模块后 API 仍按"最新目录"选版本，两者可能不是同一份数据。

版本来源（2026-09-16 第五轮审核 P0-4）：传了 `--version` 时同时写 RAG_VERSION_SOURCE
=cli_explicit，否则 health 只能看到"环境变量被固定"（env_pinned），无法区分
"命令行显式指定"与".env 固定"。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保 RAG/ 根目录在 sys.path（与离线脚本约定一致：从 RAG/ 根执行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from config.settings import get_settings  # noqa: E402


def pin_version(version: str | None) -> str:
    """把 `--version` 固定进环境变量，并声明来源；返回来源标识（空=未固定）。

    必须在 `get_settings()` / 导入 `server.api` **之前**调用：uvicorn 在同一个进程里
    按字符串导入模块，晚一步就会出现"启动日志说 A、API 实际用 B"（2026-09-15 审核 P0-7）；
    来源声明（RAG_VERSION_SOURCE）则让 health 能区分 cli_explicit 与 env_pinned
    （2026-09-16 第五轮审核 P0-4）。

    不传 `--version` 时必须**清掉**本进程可能残留的 `RAG_VERSION_SOURCE=cli_explicit`
    （第五轮整改复核 B4）：否则"没传 CLI 参数、实际按环境/扫描选版"的运行会被
    health 错标成"CLI 显式固定"，而这种错标恰好出现在最需要准确诊断的场合。
    """
    if not version:
        if os.environ.get("RAG_VERSION_SOURCE") == "cli_explicit":
            os.environ.pop("RAG_VERSION_SOURCE", None)
        return ""
    os.environ["RAG_ACTIVE_VERSION"] = version
    os.environ["RAG_VERSION_SOURCE"] = "cli_explicit"
    return "cli_explicit"


def _is_loopback(host: str) -> bool:
    """监听地址是否只有本机能连（回环）。

    逐条判断而不是只比字符串：`localhost`、`::1`、`[::1]`、`127.0.0.2` 都是回环，
    而它们写的字面量完全不同。判断错的方向是"把公开监听当成回环"——那正是这个门禁
    要防的事，所以遇到不认识的形式一律按**非回环**处理（fail-closed）。
    """
    candidate = (host or "").strip().strip("[]").lower()
    if candidate in ("localhost", "::1"):
        return True
    parts = candidate.split(".")
    if len(parts) == 4 and parts[0] == "127":
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return False


def _reject_nginx_mode_on_public_bind(settings, host: str) -> None:
    """`RAG_AUTH_MODE=nginx` 时拒绝监听非回环地址。

    这一档把"身份由谁把关"整个交给了 nginx：服务自身不验签（`require_auth=False`），
    安全性完全来自"外部只能经 nginx 进来"这个前提。若服务同时监听 0.0.0.0，
    同网段的人可以直接连后端端口绕过 nginx 的 auth_basic，鉴权形同不存在——
    而配置看起来是"配了 nginx 认证"的，最容易被误判成安全。

    因此在**只有这里才知道真实监听地址**的地方拦掉，而不是在 config 里猜。
    """
    mode = (getattr(settings, "auth_mode", "") or "").strip().lower()
    if mode != "nginx" or _is_loopback(host):
        return
    raise SystemExit(
        f"[run_server] 拒绝启动：RAG_AUTH_MODE=nginx 但监听地址是 {host!r}（非回环）。\n"
        f"  nginx 档只在「外部只能经 nginx 进来」时成立；直接监听 {host} 会让人绕过 nginx\n"
        "  直连本服务，鉴权等于没有。请改用 --host 127.0.0.1（推荐，由 nginx 反代），\n"
        "  或改用 RAG_AUTH_MODE=jwt 让本服务自己验签。"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="启动 RAGv5 SSE 问答服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--version", default=None,
                   help="快照/索引版本（如 20260915_v1）；缺省用 RAG_ACTIVE_VERSION，"
                        "再缺省才取最新一致版本")
    p.add_argument("--reload", action="store_true", help="开发热重载")
    args = p.parse_args()

    # 版本必须在导入 server.api 之前固定下来（uvicorn 在同一进程导入字符串模块）
    pin_version(args.version)

    s = get_settings()
    _reject_nginx_mode_on_public_bind(s, args.host)
    effective = args.version or s.active_version or "latest"
    print(f"[run_server] 服务启动 host={args.host} port={args.port} "
          f"version={effective}{'' if (args.version or s.active_version) else '（未固定，按目录扫描）'}")
    if not (s.llm_base_url and s.llm_api_key):
        print("[run_server] 未配置 LLM_BASE_URL/LLM_API_KEY → F06 使用离线摘要回答器（可正常演示检索链）")
    else:
        print(f"[run_server] LLM: {s.llm_model} @ {s.llm_base_url}"
              f"（备用 {s.fallback_llm_model or '未配'}，max_tokens={s.llm_max_tokens}）")
    print(f"[run_server] 文本模式: {s.text_mode}"
          f" | reasoning 外发: {'开' if s.expose_thinking else '关'}"
          f" | 同源托管目录: {s.frontend_dist if s.frontend_dist.is_dir() else '未发现（先 npm run build）'}")

    # 启动自检：版本 / 向量产物 / 前端产物一次看清（失败不阻塞启动，由 /api/health 暴露）
    try:
        from server.runtime import resolve_version

        version, _snap_dir, index_dir = resolve_version(s, args.version)
        print(f"[run_server] 自检: 快照 {version} | 索引 {index_dir.name} | 前端产物 "
              f"{'有' if (s.frontend_dist / 'index.html').exists() else '无'}")
    except Exception as e:  # noqa: BLE001
        print(f"[run_server] 自检失败（服务仍会启动，可在 /api/health 查看 load_error）: {e}")

    uvicorn.run(
        "server.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
    )


if __name__ == "__main__":
    main()
