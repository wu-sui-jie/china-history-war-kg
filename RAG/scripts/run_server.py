"""RAGv5 在线服务启动脚本（F02–F06 SSE 问答接口）。

用法：
    python scripts/run_server.py [--host 127.0.0.1] [--port 8000] [--version 20260915_v1]

说明：脚本保持薄，只做 uvicorn 启动；业务在 server/。
需要 fastapi/uvicorn（见 RAG requirements.txt）；无 LLM 密钥也可启动，
F06 自动使用离线摘要回答器；配好 LLM_BASE_URL/LLM_API_KEY 后接真实模型。

数据版本（2026-09-15 审核 P0-7）：`--version` 会写入环境变量 RAG_ACTIVE_VERSION，
由 app 的 lifespan 读取并显式加载——旧实现只把这个参数用于启动自检打印，
uvicorn 重新导入模块后 API 仍按"最新目录"选版本，两者可能不是同一份数据。
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
    if args.version:
        os.environ["RAG_ACTIVE_VERSION"] = args.version

    s = get_settings()
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
